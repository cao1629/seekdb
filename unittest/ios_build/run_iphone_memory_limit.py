#!/usr/bin/env python3
"""Explicitly stress an owned iPhone probe to exhaustion and verify system evidence/recovery."""
import argparse
import dataclasses
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import iphone_test_phases as phases
import run_all_iphone_tests as cli
import run_device_suite as device
import run_extended_iphone_tests as extended

REASONS = {'per-process-limit', 'highwater', 'vm-pageshortage',
           'vm-thrashing', 'vm-compressor-thrashing', 'vm-compressor-space-shortage'}


def jetsam_victim(content, pid):
    """Return allowlisted metrics only when a Jetsam report names the exact owned victim."""
    decoder = json.JSONDecoder()
    header, offset = decoder.raw_decode(content)
    body = json.loads(content[offset:].strip()) if content[offset:].strip() else header
    if str(body.get('bug_type', header.get('bug_type'))) != '298':
        raise ValueError('not a Jetsam event report')
    page_size = body['memoryStatus']['pageSize']
    if not isinstance(page_size, int) or page_size <= 0:
        raise ValueError('invalid Jetsam page size')
    victims = [p for p in body['processes']
               if p.get('pid') == pid and p.get('name') == 'SeekDBProbe'
               and p.get('reason') in REASONS]
    if len(victims) != 1:
        raise ValueError('Jetsam report does not identify this probe as a victim')
    victim = victims[0]
    pages = victim.get('rpages')
    if not isinstance(pages, int) or pages <= 0:
        raise ValueError('invalid victim resident pages')
    return {'reason': victim['reason'], 'page_size': page_size,
            'resident_bytes': pages * page_size,
            'lifetime_max_bytes': victim.get('lifetimeMax', pages) * page_size,
            'states': victim.get('states', [])}


def progress_identity(value, options, round_id):
    """Reject stale progress, invalid allocation bounds, or unavailable device metrics."""
    if (value.get('run_id') != round_id or value.get('build_id') != options.build_id
            or value.get('data_name') != options.data_name or value.get('schema_version') != 1):
        raise ValueError('memory progress identity mismatch')
    allocated, attempted = value['allocated_bytes'], value['attempted_bytes']
    chunk, ceiling = value['chunk_bytes'], value['ceiling_bytes']
    if (chunk != 64 * 1024**2 or ceiling != 8 * 1024**3
            or not isinstance(allocated, int) or allocated < 0 or allocated % chunk
            or attempted not in (allocated, allocated + chunk) or attempted > ceiling
            or allocated > ceiling or value['footprint_bytes'] <= 0):
        raise ValueError('memory progress bounds are invalid')


def copy_progress(options, path):
    """Read only this probe's memory checkpoint into a temporary host file."""
    result = device.devicectl([
        'device', 'copy', 'from', '--device', options.device,
        '--domain-type', 'appDataContainer', '--domain-identifier', options.bundle_id,
        '--source', 'Documents/memory-limit-progress.json', '--destination', str(path),
        '--timeout', '15'])
    return json.loads(path.read_text()) if result.returncode == 0 and path.is_file() else None


def crash_files(options):
    """List system Jetsam reports while keeping unrelated device metadata in memory."""
    result = extended.command(options, ['device', 'info', 'files',
        '--domain-type', 'systemCrashLogs', '--json-output', '-', '--timeout', '30'])
    rows = json.loads(result.stdout)['result']['files']
    return {row['relativePath'] for row in rows if row['name'].startswith('JetsamEvent-')
            and row['name'].endswith('.ips') and not row['resources']['isDirectory']}


def collect_jetsam(options, before, pid):
    """Require a new device Jetsam report identifying the owned PID, retaining a redacted extract."""
    deadline = time.monotonic() + 120
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'jetsam.ips'
        while time.monotonic() < deadline:
            for name in sorted(crash_files(options) - before):
                if Path(name).is_absolute() or '..' in Path(name).parts:
                    raise ValueError('unsafe crash report path')
                result = device.devicectl(['device', 'copy', 'from', '--device', options.device,
                    '--domain-type', 'systemCrashLogs', '--source', name,
                    '--destination', str(path), '--timeout', '30'])
                if result.returncode == 0:
                    try:
                        content = path.read_text()
                        victim = jetsam_victim(content, pid)
                        victim['raw_report_sha256'] = hashlib.sha256(content.encode()).hexdigest()
                        return victim
                    except (ValueError, KeyError, TypeError):
                        pass
            time.sleep(3)
    raise RuntimeError('probe disappeared without a matching new Jetsam report')


def stress(options, round_id):
    """Run one explicit pressure launch, retaining checkpoints and observing natural termination."""
    before = crash_files(options)
    device.launch_device_process(['device', 'process', 'launch', '--device', options.device,
        '--terminate-existing', '--environment-variables', json.dumps({
            'SEEKDB_IOS_TEST_RUN_ID': round_id, 'SEEKDB_PROBE_DATA_NAME': options.data_name,
            'SEEKDB_PROBE_MEMORY_LIMIT_TEST': '1', 'SEEKDB_PROBE_AUTO_STOP': '1',
            'SEEKDB_PROBE_CONTROL': '1'}), '--timeout', '60', options.bundle_id],
        time.monotonic() + 120)
    running = extended.status(options, round_id, extended.is_running)
    if running['previous_runs'] != 0:
        raise ValueError('pressure database must be a fresh owned fixture')
    pid = extended.probe_pid(options)
    if pid is None:
        raise RuntimeError('pressure process exited before ownership was established')
    samples = []
    deadline = time.monotonic() + 420
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'progress.json'
        while time.monotonic() < deadline:
            value = copy_progress(options, path)
            if value is not None:
                progress_identity(value, options, round_id)
                if not samples or value != samples[-1]:
                    samples.append(value)
                    extended.save(options.output_dir / 'evidence-memory-limit-progress.json', samples)
                if value['state'] == 'released':
                    if value['outcome'] == 'allocation-failed' and value['allocation_errno'] == errno.ENOMEM:
                        extended.status(options, round_id, lambda item: item.get('state') == 'Stopped')
                        return {'outcome': 'allocation-enomem', 'samples': samples, 'initial_status': running}
                    raise RuntimeError('pressure returned without reaching OOM/Jetsam')
            if extended.probe_pid(options) != pid:
                victim = collect_jetsam(options, before, pid)
                if not samples or samples[-1]['allocated_bytes'] == 0:
                    raise RuntimeError('Jetsam occurred without retained pressure checkpoints')
                extended.save(options.output_dir / 'evidence-memory-limit-jetsam.json', victim)
                return {'outcome': 'jetsam', 'samples': samples, 'jetsam': victim, 'initial_status': running}
            time.sleep(1)
    extended.command(options, ['device', 'process', 'launch', '--payload-url',
                              f'seekdb-probe://stop/{round_id}', options.bundle_id])
    raise RuntimeError('pressure deadline reached without OOM/Jetsam evidence')


def main():
    """Prepare the current signed test App, run pressure once, and verify SQL recovery."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    options = parser.parse_args()
    options.output_dir = options.output_dir.resolve()
    options.output_dir.relative_to(cli.REPOSITORY_ROOT.resolve())
    if options.output_dir.exists() and any(options.output_dir.iterdir()):
        raise ValueError('memory evidence directory must be new or empty')
    options.output_dir.mkdir(parents=True, exist_ok=True)
    source = cli.source_commit()
    run_id = uuid.uuid4().hex
    configuration = cli.resolve_local_configuration(cli.parse_args([]), os.environ)
    selected = cli.select_physical_device(configuration.device, cli.discover_physical_devices())
    configuration = dataclasses.replace(configuration, device=selected.identifier,
                                        profile_device=selected.profile_identifier)
    configuration = cli.infer_signing_configuration(configuration)
    prepared = phases.prepare_test_app(configuration=configuration, suites=('lifecycle-memory',),
                                       source_revision=source, run_id=run_id)
    if not isinstance(prepared, dict):
        raise RuntimeError('current test App preparation failed')
    options.device, options.bundle_id = configuration.device, configuration.bundle_id
    options.build_id = source[:12]
    options.data_name = f'memory-limit-{run_id}'
    result = stress(options, run_id)
    recovered_id = uuid.uuid4().hex
    extended.launch(options, recovered_id, 1)
    recovered, sql_file = extended.sql_evidence(options, recovered_id, 1, 'memory-limit-recovered')
    names = ['evidence-memory-limit-progress.json', sql_file]
    if result['outcome'] == 'jetsam':
        names.append('evidence-memory-limit-jetsam.json')
    result.update(run_id=run_id, source_commit=source, data_name=options.data_name,
        device_hash=hashlib.sha256(options.device.encode()).hexdigest(),
        recovered=recovered, evidence_sha256={name: extended.digest(options.output_dir / name) for name in names},
        limitations=['one foreground run on the current device/OS state; not a universal fixed limit',
                     'anonymous resident pressure with a running engine; not solely seekdb allocator usage'])
    extended.save(options.output_dir / 'evidence-memory-limit.json', result)
    print(json.dumps({'run_result': 0, 'outcome': result['outcome'],
                      'last_completed_allocation_bytes': result['samples'][-1]['allocated_bytes'],
                      'recovered_previous_runs': recovered['previous_runs']}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        print(type(error).__name__ + ': memory limit verification failed', file=sys.stderr)
        sys.exit(1)
