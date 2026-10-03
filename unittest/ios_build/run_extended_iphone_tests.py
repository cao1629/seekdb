#!/usr/bin/env python3
"""Execute extended physical-device phases and audit the final evidence matrix."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import run_device_suite as device

PRIOR_PHASES = ('inventory', 'registry-smoke', 'cpp-device-equivalents',
                'rust-device-runtime', 'mysqltest', 'vector', 'lifecycle-memory')
STATUS_FIELDS = ('state', 'result', 'data_name', 'build_id', 'hook_mode',
                 'sql_verified', 'sql_result', 'suite_result', 'previous_runs',
                 'cleanup_status', 'cleanup_error', 'working_directory_restored',
                 'run_id', 'lifecycle_events', 'timestamp')


def digest(path):
    """Hash retained evidence bytes to bind the audit to exact files."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    """Persist structured evidence without raw command output or device identifiers."""
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def status(options, run_id, predicate, timeout=120):
    """Wait for current device status satisfying a bounded lifecycle predicate."""
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'status.json'
        while time.monotonic() < deadline:
            if device.copy_probe_status(options.device, options.bundle_id, path):
                try:
                    value = json.loads(path.read_text())
                    if (value.get('run_id') == run_id
                            and value.get('build_id') == options.build_id
                            and value.get('data_name') == options.data_name
                            and predicate(value)):
                        return {key: value.get(key) for key in STATUS_FIELDS}
                except json.JSONDecodeError:
                    pass
            time.sleep(1)
    raise RuntimeError('current device lifecycle status did not reach its required state')


def launch(options, run_id, auto_stop):
    """Launch a fresh owned probe process with explicit persistence and control settings."""
    device.launch_device_process([
        'device', 'process', 'launch', '--device', options.device,
        '--terminate-existing', '--environment-variables', json.dumps({
            'SEEKDB_IOS_TEST_RUN_ID': run_id,
            'SEEKDB_PROBE_DATA_NAME': options.data_name,
            'SEEKDB_PROBE_AUTO_STOP': str(auto_stop),
            'SEEKDB_PROBE_CONTROL': '1',
        }), '--timeout', '60', options.bundle_id], time.monotonic() + 120)


def command(options, arguments):
    """Require successful device control while suppressing raw local metadata."""
    result = device.devicectl(arguments[:3] + ['--device', options.device] + arguments[3:])
    if result.returncode:
        raise RuntimeError('device lifecycle control command failed')
    return result


def probe_pid(options):
    """Find only the exact probe executable and reject ambiguous process ownership."""
    result = command(options, ['device', 'info', 'processes', '--json-output', '-'])
    processes = json.loads(result.stdout)['result']['runningProcesses']
    pids = [row['processIdentifier'] for row in processes
            if row.get('executable', '').endswith('/SeekDBProbe')]
    if len(pids) > 1:
        raise RuntimeError('ambiguous probe process ownership')
    return pids[0] if pids else None


def is_running(value):
    """Require completed ordinary SQL in a live test-hook engine."""
    return (value.get('state') == 'Running' and value.get('sql_verified') is True
            and value.get('sql_result') == 0 and value.get('hook_mode') == 'enabled')


def validate_transition(value):
    """Require ordered device callbacks with a continuously running engine."""
    events = value.get('lifecycle_events', [])
    background = next((index for index, event in enumerate(events)
                       if event.get('name') == 'background' and event.get('engine_state') == 2), None)
    if background is None or not any(event.get('name') == 'foreground'
                                    and event.get('engine_state') == 2
                                    and event.get('timestamp', 0) >= events[background].get('timestamp', 0)
                                    for event in events[background + 1:]):
        raise ValueError('device background/foreground callback evidence is incomplete')


def sql_evidence(options, run_id, previous_runs, label):
    """Copy complete ordinary SQL and require clean shutdown with the expected history."""
    value = status(options, run_id, lambda item: item.get('state') == 'Stopped')
    device.validate_sql_terminal_status(value, run_id, options.build_id,
                                       options.data_name, previous_runs, 'enabled')
    path = options.output_dir / f'evidence-lifecycle-{label}.jsonl'
    if not device.copy_sql_evidence(options.device, options.bundle_id, path):
        raise RuntimeError('lifecycle SQL evidence is missing')
    device.validate_sql_records(device.read_jsonl(path))
    return value, path.name


def vector(options):
    """Run index creation and recovery in separate processes sharing one owned directory."""
    rounds = []
    for label in ('seed', 'restore'):
        case_id = f'ios.vector.{label}'
        result = subprocess.run([
            sys.executable, str(Path(device.__file__)), '--device', options.device,
            '--bundle-id', options.bundle_id, '--suite', 'vector', '--filter', case_id,
            '--expected-case', case_id, '--data-name', options.data_name,
            '--timeout', '600', '--output-dir', str(options.output_dir)],
            capture_output=True, text=True, timeout=720)
        if result.returncode:
            raise RuntimeError(f'vector {label} device case failed')
        summary = json.loads(result.stdout)
        path = options.output_dir / f"device-test-{summary['run_id']}.jsonl"
        device.validate_records(device.read_jsonl(path), summary['run_id'], options.build_id,
                                [case_id], 'vector', case_id)
        value = status(options, summary['run_id'], lambda item: item.get('state') == 'Stopped')
        device.validate_terminal_status(value, summary['run_id'], options.build_id)
        destination = options.output_dir / f'evidence-vector-{label}.jsonl'
        path.replace(destination)
        rounds.append({'run_id': summary['run_id'], 'case_id': case_id,
                       'status': value, 'evidence': destination.name})
    return {'rounds': rounds}


def lifecycle(options):
    """Exercise real scene transitions, owned-process termination, and SQL recovery."""
    report_root = Path.home() / 'Library/Logs/CrashReporter/MobileDevice'
    before_crashes = device.crash_snapshot(report_root)
    first = uuid.uuid4().hex
    launch(options, first, 0)
    running = status(options, first, is_running)
    if running['previous_runs'] != 0:
        raise RuntimeError('lifecycle fixture directory is not fresh')
    pid = probe_pid(options)
    if pid is None:
        raise RuntimeError('owned probe process is absent')
    command(options, ['device', 'process', 'launch', 'com.apple.Preferences'])
    background = status(options, first, lambda value: any(
        event.get('name') == 'background' and event.get('engine_state') == 2
        for event in value.get('lifecycle_events', [])))
    command(options, ['device', 'process', 'launch', options.bundle_id])
    foreground = status(options, first, lambda value: is_running(value) and any(
        event.get('name') == 'foreground' for event in value.get('lifecycle_events', [])))
    validate_transition(foreground)
    if probe_pid(options) != pid:
        raise RuntimeError('probe process changed during foreground recovery')
    command(options, ['device', 'process', 'launch', '--payload-url',
                      f'seekdb-probe://stop/{first}', options.bundle_id])
    stopped, first_file = sql_evidence(options, first, 0, 'clean')
    second = uuid.uuid4().hex
    launch(options, second, 0)
    before_termination = status(options, second, is_running)
    if before_termination['previous_runs'] != 1:
        raise RuntimeError('clean-stop restart history is incorrect')
    second_pid = probe_pid(options)
    if second_pid is None:
        raise RuntimeError('termination target is absent')
    command(options, ['device', 'process', 'signal', '--pid', str(second_pid), '--signal', 'SIGTERM'])
    deadline = time.monotonic() + 30
    while probe_pid(options) is not None and time.monotonic() < deadline:
        time.sleep(1)
    if probe_pid(options) is not None:
        raise RuntimeError('owned process termination was not observed')
    third = uuid.uuid4().hex
    launch(options, third, 1)
    recovered, recovery_file = sql_evidence(options, third, 2, 'recovered')
    device.reject_new_crash_reports(report_root, before_crashes, options.bundle_id)
    return {'background': background, 'foreground': foreground, 'clean_stop': stopped,
            'before_termination': before_termination, 'termination_observed': True,
            'same_process_foreground': True, 'recovered': recovered,
            'sql_files': [first_file, recovery_file],
            'limitations': ['lock/unlock requires manual device interaction',
                            'bounded allocations do not certify OOM or Jetsam limits']}


def matrix(directory, run_id, source_commit):
    """Reject missing phases or evidence and bind every passed case to retained bytes."""
    checkpoint = json.loads((directory / 'checkpoint.json').read_text())
    if checkpoint.get('run_id') != run_id or checkpoint.get('source_commit') != source_commit:
        raise ValueError('final matrix checkpoint identity mismatch')
    phases = {phase['id']: phase for phase in checkpoint['phases']}
    evidence = {}
    for phase_id in PRIOR_PHASES:
        phase = phases.get(phase_id)
        if phase is None or phase.get('status') != 'passed' or not phase.get('cases'):
            raise ValueError(f'final matrix requires passed phase: {phase_id}')
        for case in phase['cases']:
            if (case.get('status') != 'passed' or case.get('exit_status') != 0
                    or case.get('clean_state') is not True or not case.get('evidence_paths')):
                raise ValueError('final matrix case evidence is incomplete')
            for name in case['evidence_paths']:
                if Path(name).name != name or not name.startswith('evidence-'):
                    raise ValueError('final matrix evidence path is unsafe')
                path = directory / name
                if not path.is_file() or path.stat().st_size == 0:
                    raise ValueError('final matrix evidence is missing or empty')
                evidence[name] = digest(path)
    return {'run_id': run_id, 'source_commit': source_commit,
            'required_phases': list(PRIOR_PHASES), 'evidence_sha256': evidence,
            'limitations': ['host mysqltest coverage is not physical-device execution',
                            'lock/unlock remains a manual acceptance step',
                            'bounded pressure does not certify OOM or Jetsam limits']}


def validate_evidence(directory, mode, run_id, source_commit):
    """Independently revalidate saved extended evidence before accepting a phase."""
    path = directory / f'evidence-{mode}.json'
    value = json.loads(path.read_text())
    if value.get('run_id') != run_id or value.get('source_commit') != source_commit:
        raise ValueError('extended phase evidence identity mismatch')
    if mode == 'final-matrix':
        if value != matrix(directory, run_id, source_commit):
            raise ValueError('final matrix retained evidence changed')
        return (path.name,)
    names = [path.name]
    for name, expected in value.get('evidence_sha256', {}).items():
        if Path(name).name != name or digest(directory / name) != expected:
            raise ValueError('extended retained evidence changed')
        names.append(name)
    if mode == 'vector':
        rounds = value['rounds']
        if [item['case_id'] for item in rounds] != ['ios.vector.seed', 'ios.vector.restore']:
            raise ValueError('vector rounds are incomplete')
        if rounds[0]['run_id'] == rounds[1]['run_id']:
            raise ValueError('vector recovery requires a fresh process run')
        for item in rounds:
            device.validate_records(device.read_jsonl(directory / item['evidence']),
                                    item['run_id'], source_commit[:12], [item['case_id']],
                                    'vector', item['case_id'])
            device.validate_terminal_status(item['status'], item['run_id'], source_commit[:12])
            if item['status']['data_name'] != value['data_name']:
                raise ValueError('vector recovery did not share its persisted directory')
    else:
        validate_transition(value['foreground'])
        first_id = value['clean_stop']['run_id']
        if (value['background']['run_id'] != first_id or value['foreground']['run_id'] != first_id
                or value['before_termination']['run_id'] == first_id
                or value['recovered']['run_id'] in (first_id, value['before_termination']['run_id'])):
            raise ValueError('lifecycle runs are not isolated or scene identity changed')
        for label in ('background', 'foreground', 'before_termination'):
            item = value[label]
            if (item['build_id'] != source_commit[:12] or item['data_name'] != value['data_name']
                    or not is_running(item)):
                raise ValueError('lifecycle state identity is invalid')
        if value['before_termination']['previous_runs'] != 1:
            raise ValueError('lifecycle termination history is invalid')
        if value['termination_observed'] is not True or value['same_process_foreground'] is not True:
            raise ValueError('lifecycle control evidence is incomplete')
        for label, previous in (('clean_stop', 0), ('recovered', 2)):
            status_value = value[label]
            device.validate_sql_terminal_status(status_value, status_value['run_id'],
                                               source_commit[:12], value['data_name'], previous, 'enabled')
        for name in value['sql_files']:
            device.validate_sql_records(device.read_jsonl(directory / name))
    return tuple(names)


def main():
    """Dispatch a bounded extended phase and print only a safe success summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('vector', 'lifecycle-memory', 'final-matrix'), required=True)
    parser.add_argument('--device', default='')
    parser.add_argument('--bundle-id', default='')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--source-commit', required=True)
    options = parser.parse_args()
    options.build_id = options.source_commit[:12]
    scope = hashlib.sha256((options.run_id + options.mode + uuid.uuid4().hex).encode()).hexdigest()[:20]
    options.data_name = f'extended-{options.mode}-{scope}'
    if options.mode == 'final-matrix':
        value = matrix(options.output_dir, options.run_id, options.source_commit)
    else:
        value = vector(options) if options.mode == 'vector' else lifecycle(options)
        names = [item['evidence'] for item in value['rounds']] if options.mode == 'vector' else value['sql_files']
        value.update(run_id=options.run_id, source_commit=options.source_commit,
                     data_name=options.data_name,
                     device_hash=hashlib.sha256(options.device.encode()).hexdigest(),
                     evidence_sha256={name: digest(options.output_dir / name) for name in names})
    save(options.output_dir / f'evidence-{options.mode}.json', value)
    validate_evidence(options.output_dir, options.mode, options.run_id, options.source_commit)
    print(json.dumps({'run_result': 0}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
