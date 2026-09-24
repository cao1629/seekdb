#!/usr/bin/env python3
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import recording_common as common


DESCRIPTION = (
    "Run mysqltest_for_seekdb.py in record mode as several slices at the same "
    "time, each with its own port, base directory and record directory, and "
    "merge the slices' recordings into one recording that the runner's compare "
    "subcommand accepts against a one-slice recording."
)
runner = common.runner
REPO_ROOT = common.REPO_ROOT
RUNNER_PATH = common.RUNNER_PATH
DEPLOY_DIR = REPO_ROOT / "tools" / "deploy"
REDUCED_INIT_DIR = REPO_ROOT / "migration" / "judge" / "reduced-init"
INIT_FILES = {
    "full": (DEPLOY_DIR / "init.sql", DEPLOY_DIR / "init_user.sql"),
    "reduced": (REDUCED_INIT_DIR / "init.sql", REDUCED_INIT_DIR / "init_user.sql"),
}
DEFAULT_PORTS = "3891,3892,3893,3894"
COPIED_KEYS = (
    "seekdb",
    "seekdb_sha256",
    "mysqltest",
    "mysqltest_sha256",
    "obclient",
    "obclient_sha256",
    "init_sql",
    "init_sql_sha256",
    "init_user_sql",
    "init_user_sql_sha256",
    "sdb_sha256",
    "runner_sha256",
    "repo_head",
    "tools_deploy_tree",
    "tools_deploy_status",
    "case_list",
    "fresh_instance_per_case",
)
PER_SLICE_KEYS = ("repo_head",)
SHARED_KEYS = tuple(
    sorted(
        (
            set(COPIED_KEYS)
            | set(runner.RECORDING_INPUT_KEYS)
            | set(runner.RECORDING_NOTE_KEYS)
            | {"slice_count"}
        )
        - set(PER_SLICE_KEYS)
    )
)
SUFFIXES = (".result", ".partial")


class MergeRefused(Exception):
    pass


def load_slice(directory):
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MergeRefused("cannot read {}: {}".format(manifest_path, exc))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise MergeRefused("{} has no case list".format(manifest_path))
    for key in ("slice_index", "slice_count"):
        if type(manifest.get(key)) is not int:
            raise MergeRefused("{} has no integer {}".format(manifest_path, key))
    return manifest, runner.file_sha256(manifest_path)


def expected_cases(case_list):
    try:
        cases = runner.discover_cases(REPO_ROOT)
        if case_list is not None:
            path = Path(case_list)
            if not path.is_file():
                raise MergeRefused(
                    "the slices ran case list {}, which does not exist now".format(
                        case_list
                    )
                )
            cases = runner.load_case_list(path, cases)
    except runner.RunnerError as exc:
        raise MergeRefused("cannot select the cases as the runner does: {}".format(exc))
    return [case.name for case in cases]


def recorded_files(directory, manifest):
    names = set(str(name) for name in manifest["cases"])
    outcomes = manifest.get("outcomes")
    outcomes = outcomes if isinstance(outcomes, dict) else {}
    present = {}
    for entry in sorted(directory.iterdir()):
        if entry.name == "manifest.json":
            continue
        suffix = next((s for s in SUFFIXES if entry.name.endswith(s)), None)
        case = entry.name[: -len(suffix)] if suffix else None
        if suffix is None or case not in names or not entry.is_file():
            raise MergeRefused(
                "{} holds a file the manifest does not account for: {}".format(
                    directory, entry.name
                )
            )
        present.setdefault(case, set()).add(suffix)
    for case in names:
        kinds = present.get(case, set())
        outcome = outcomes.get(case)
        if len(kinds) > 1:
            raise MergeRefused(
                "{} holds both {}.result and {}.partial".format(directory, case, case)
            )
        if isinstance(outcome, dict):
            wanted = set()
            if outcome.get("recorded"):
                wanted.add(".result")
            if outcome.get("partial"):
                wanted.add(".partial")
            if kinds != wanted:
                raise MergeRefused(
                    "{}: case {} has {} but its outcome says {}".format(
                        directory,
                        case,
                        ", ".join(sorted(kinds)) or "no file",
                        ", ".join(sorted(wanted)) or "no file",
                    )
                )
        elif kinds and manifest.get("finished") is True:
            raise MergeRefused(
                "{}: case {} has {} but no outcome".format(
                    directory, case, ", ".join(sorted(kinds))
                )
            )
    return present


def copy_verified(source, destination):
    shutil.copyfile(str(source), str(destination))
    if destination.read_bytes() != source.read_bytes():
        raise MergeRefused("{} differs from {} after the copy".format(destination, source))


def worst_max_retries(values):
    if all(value == values[0] for value in values):
        return values[0]
    numbers = [value for value in values if type(value) is int]
    return max(numbers) if numbers else None


def merged_outcome(slices, merged_cases):
    errors = []
    failed = set()
    retried = {}
    outcomes = {}
    for index, (_, manifest, _) in enumerate(slices):
        if manifest.get("finished") is not True:
            errors.append("slice {} did not finish".format(index))
        if manifest.get("error"):
            errors.append("slice {}: {}".format(index, manifest["error"]))
        failed.update(manifest.get("failed_cases") or [])
        if isinstance(manifest.get("retried_cases"), dict):
            retried.update(manifest["retried_cases"])
        elif manifest.get("retried_cases"):
            errors.append("slice {} has unreadable retried_cases".format(index))
        if isinstance(manifest.get("outcomes"), dict):
            outcomes.update(manifest["outcomes"])
    init_counts = [manifest.get("init_failed_statements") for _, manifest, _ in slices]
    return {
        "finished": all(manifest.get("finished") is True for _, manifest, _ in slices),
        "success": not errors
        and all(manifest.get("success") is True for _, manifest, _ in slices),
        "error": "; ".join(errors) if errors else None,
        "failed_cases": [case for case in merged_cases if case in failed]
        + sorted(case for case in failed if case not in merged_cases),
        "retried_cases": retried,
        "max_retries": worst_max_retries(
            [manifest.get("max_retries") for _, manifest, _ in slices]
        ),
        "outcomes": outcomes,
        "init_failed_statements": sum(init_counts)
        if all(type(count) is int for count in init_counts)
        else None,
    }


def merge(record_dirs, out_dir):
    slices = []
    for directory in record_dirs:
        manifest, digest = load_slice(directory)
        slices.append((directory, manifest, digest))
    count = len(slices)
    if any(manifest["slice_count"] != count for _, manifest, _ in slices):
        raise MergeRefused(
            "the slices' slice_count ({}) differs from the {} directories given".format(
                ", ".join(str(manifest["slice_count"]) for _, manifest, _ in slices),
                count,
            )
        )
    indexes = sorted(manifest["slice_index"] for _, manifest, _ in slices)
    if indexes != list(range(count)):
        raise MergeRefused(
            "the slice indexes are {}, not 0 to {}".format(indexes, count - 1)
        )
    slices.sort(key=lambda item: item[1]["slice_index"])
    first = slices[0][1]
    for key in SHARED_KEYS:
        values = [manifest.get(key) for _, manifest, _ in slices]
        if any(value != values[0] for value in values):
            raise MergeRefused(
                "{} differs between the slices: {}".format(
                    key, ", ".join(repr(value) for value in values)
                )
            )
    total = sum(len(manifest["cases"]) for _, manifest, _ in slices)
    merged_cases = [None] * total
    for index, (_, manifest, _) in enumerate(slices):
        positions = range(index, total, count)
        if len(manifest["cases"]) != len(positions):
            raise MergeRefused(
                "slice {} holds {} cases, but slice {} of {} over {} cases holds {}".format(
                    index, len(manifest["cases"]), index, count, total, len(positions)
                )
            )
        for position, name in zip(positions, manifest["cases"]):
            merged_cases[position] = str(name)
    expected = expected_cases(first.get("case_list"))
    if merged_cases != expected:
        raise MergeRefused(
            "the slices' cases, interleaved, are not the runner's own selection "
            "({} merged, {} expected; first difference at position {})".format(
                len(merged_cases),
                len(expected),
                next(
                    (
                        position
                        for position, (left, right) in enumerate(
                            zip(merged_cases, expected)
                        )
                        if left != right
                    ),
                    min(len(merged_cases), len(expected)),
                ),
            )
        )
    present = [recorded_files(directory, manifest) for directory, manifest, _ in slices]
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise MergeRefused("the merged recording's directory is not new or empty: {}".format(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.open("x").close()
    merged = dict((key, first.get(key)) for key in COPIED_KEYS)
    for key in PER_SLICE_KEYS:
        values = [manifest.get(key) for _, manifest, _ in slices]
        merged[key] = values[0] if all(value == values[0] for value in values) else None
    merged.update(
        {
            "finished": False,
            "recorder": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
            "merger_sha256": runner.file_sha256(Path(__file__).resolve()),
            "cases": merged_cases,
            "slice_index": None,
            "slice_count": count,
            "work_dir": None,
            "recorded": "{} Merged by parallel_slices.py from the slices in "
            "merged_slices; every file is the slice's own, copied byte for "
            "byte.".format(first.get("recorded") or ""),
            "merged_slices": [
                dict(
                    [
                        ("slice_index", manifest["slice_index"]),
                        ("record_dir", str(directory)),
                        ("manifest_sha256", digest),
                        ("work_dir", manifest.get("work_dir")),
                        ("finished", manifest.get("finished")),
                        ("success", manifest.get("success")),
                        ("error", manifest.get("error")),
                    ]
                    + [(key, manifest.get(key)) for key in PER_SLICE_KEYS]
                )
                for directory, manifest, digest in slices
            ],
        }
    )
    runner.write_json(manifest_path, merged)
    for (directory, _, _), files in zip(slices, present):
        for case, kinds in files.items():
            for suffix in kinds:
                copy_verified(directory / (case + suffix), out_dir / (case + suffix))
    merged.update(merged_outcome(slices, merged_cases))
    runner.write_json(manifest_path, merged)
    return merged


def print_merge(out_dir, merged):
    print(
        "merged {} cases from {} slices into {}: finished={}, success={}, "
        "failed={}, error={}".format(
            len(merged["cases"]),
            merged["slice_count"],
            out_dir,
            merged["finished"],
            merged["success"],
            len(merged["failed_cases"]),
            merged["error"],
        ),
        flush=True,
    )


def slice_command(args, index, slice_dir, init_files, save_root):
    command = [
        sys.executable,
        str(RUNNER_PATH),
        "run",
        "--seekdb",
        str(args.seekdb),
        "--obclient",
        str(args.obclient),
        "--mysqltest",
        str(args.mysqltest),
        "--base-dir",
        str(slice_dir / "base"),
        "--work-dir",
        str(slice_dir / "work"),
        "--port",
        str(args.ports[index]),
        "--slice-index",
        str(index),
        "--slice-count",
        str(len(args.ports)),
        "--max-retries",
        "0",
        "--no-ignore-trailing-whitespace",
        "--record-dir",
        str(slice_dir / "rec"),
        "--init-sql",
        str(init_files[0]),
        "--init-user-sql",
        str(init_files[1]),
    ]
    if args.case_list:
        command += ["--case-list", str(args.case_list)]
    if args.fresh_instance_per_case:
        command.append("--fresh-instance-per-case")
    if save_root is not None:
        command += ["--save-instance-dir", str(save_root / "slice_{}".format(index))]
    return command


def start_slice(entry, environment):
    print("+ {} > {}".format(runner.format_command(entry["command"]), entry["runner_log"]), flush=True)
    with open(entry["runner_log"], "wb") as log:
        entry["process"] = subprocess.Popen(
            entry["command"],
            cwd=str(REPO_ROOT),
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    entry["started"] = time.monotonic()


def finish_slice(entry):
    entry["exit_code"] = entry["process"].wait()
    entry["seconds"] = round(time.monotonic() - entry["started"], 3)
    print(
        "slice {} exited with {} after {} s".format(
            entry["slice_index"], entry["exit_code"], entry["seconds"]
        ),
        flush=True,
    )


def command_run(args):
    args.out_dir = runner.absolute_path(args.out_dir)
    args.seekdb = runner.absolute_path(args.seekdb)
    args.obclient = runner.absolute_path(args.obclient)
    args.mysqltest = runner.absolute_path(args.mysqltest)
    args.case_list = runner.absolute_path(args.case_list) if args.case_list else None
    save_root = runner.instance_save_dir(args)
    environment = dict(os.environ)
    environment.pop(runner.INSTANCE_SAVE_ENVIRONMENT, None)
    init_files = INIT_FILES[args.init]
    try:
        common.require_free_ports(args.ports)
    except runner.RunnerError as exc:
        print("[parallel-slices][ERROR] {}".format(exc), file=sys.stderr)
        return 1
    entries = []
    for index in range(len(args.ports)):
        slice_dir = args.out_dir / "slice_{}".format(index)
        slice_dir.mkdir(parents=True)
        entries.append(
            {
                "slice_index": index,
                "port": args.ports[index],
                "command": slice_command(args, index, slice_dir, init_files, save_root),
                "runner_log": str(slice_dir / "runner.log"),
                "record_dir": slice_dir / "rec",
                "exit_code": None,
                "not_started": None,
            }
        )
    started = time.monotonic()
    if args.serial:
        stopped = None
        for entry in entries:
            if stopped is None:
                try:
                    common.require_free_ports([entry["port"]])
                except runner.RunnerError as exc:
                    stopped = "slice {} was not started: {}".format(
                        entry["slice_index"], exc
                    )
                    print("[parallel-slices][ERROR] {}".format(stopped), file=sys.stderr)
            if stopped is not None:
                entry["not_started"] = stopped
                continue
            start_slice(entry, environment)
            finish_slice(entry)
    else:
        for entry in entries:
            start_slice(entry, environment)
        for entry in entries:
            finish_slice(entry)
    wall_seconds = round(time.monotonic() - started, 3)
    merged_dir = args.out_dir / "merged"
    merged = None
    refusal = None
    try:
        merged = merge([entry["record_dir"] for entry in entries], merged_dir)
        print_merge(merged_dir, merged)
    except MergeRefused as exc:
        refusal = str(exc)
        print("[parallel-slices][ERROR] merge refused: {}".format(refusal), file=sys.stderr)
    summary = {
        "mode": "serial" if args.serial else "parallel",
        "init": args.init,
        "case_list": str(args.case_list) if args.case_list else None,
        "fresh_instance_per_case": args.fresh_instance_per_case,
        "wall_seconds": wall_seconds,
        "merged": str(merged_dir) if merged is not None else None,
        "merge_refused": refusal,
        "slices": [
            dict(
                (key, str(value) if isinstance(value, Path) else value)
                for key, value in entry.items()
                if key not in ("process", "started")
            )
            for entry in entries
        ],
    }
    runner.write_json(args.out_dir / "parallel.json", summary)
    success = (
        merged is not None
        and merged["success"] is True
        and all(entry["exit_code"] == 0 for entry in entries)
    )
    print(
        "parallel slices finished: slices={}, wall={} s, success={}".format(
            len(entries), wall_seconds, success
        ),
        flush=True,
    )
    return 0 if success else 1


def command_merge(args):
    out_dir = runner.absolute_path(args.out)
    try:
        merged = merge([runner.absolute_path(value) for value in args.record_dirs], out_dir)
    except MergeRefused as exc:
        print("[parallel-slices][ERROR] merge refused: {}".format(exc), file=sys.stderr)
        return 2
    print_merge(out_dir, merged)
    return 0 if merged["success"] else 1


def port_list(value):
    ports = [runner.positive_int(item.strip()) for item in value.split(",")]
    if len(set(ports)) != len(ports):
        raise argparse.ArgumentTypeError("the ports must differ")
    return ports


def create_parser():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser(
        "run", help="run the slices in record mode and merge their recordings"
    )
    run.add_argument("--seekdb", required=True, help="seekdb executable")
    run.add_argument("--obclient", required=True, help="obclient executable")
    run.add_argument("--mysqltest", required=True, help="mysqltest executable")
    run.add_argument(
        "--out-dir",
        required=True,
        help="new or empty directory; gets slice_<i>/ (base, work, rec, "
        "runner.log), merged/ and parallel.json",
    )
    run.add_argument(
        "--init",
        required=True,
        choices=sorted(INIT_FILES),
        help="full: tools/deploy/init.sql and init_user.sql; reduced: "
        "migration/judge/reduced-init/",
    )
    run.add_argument(
        "--ports",
        type=port_list,
        default=port_list(DEFAULT_PORTS),
        help="one SQL port per slice, comma-separated; their number is the slice "
        "count (default {})".format(DEFAULT_PORTS),
    )
    run.add_argument("--case-list", help="passed to the runner's --case-list")
    run.add_argument(
        "--fresh-instance-per-case",
        action="store_true",
        help="passed to the runner; compare needs the same setting on both sides",
    )
    run.add_argument(
        "--serial",
        action="store_true",
        help="run the slices one after another instead of at the same time",
    )
    run.add_argument(
        "--save-instance-dir",
        help="each slice saves its instances' log/ to <dir>/slice_<i>; defaults to "
        "${}".format(runner.INSTANCE_SAVE_ENVIRONMENT),
    )
    run.set_defaults(handler=command_run)

    merge_parser = subparsers.add_parser(
        "merge", help="merge existing slice recordings into one recording"
    )
    merge_parser.add_argument(
        "--out", required=True, help="new or empty directory for the merged recording"
    )
    merge_parser.add_argument(
        "record_dirs", nargs="+", help="the slices' record directories, in any order"
    )
    merge_parser.set_defaults(handler=command_merge)
    return parser


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_usage(sys.stderr)
        return 2
    if args.command == "run":
        for path, what in (
            (args.seekdb, "--seekdb"),
            (args.obclient, "--obclient"),
            (args.mysqltest, "--mysqltest"),
        ):
            if not runner.absolute_path(path).is_file():
                parser.error("{} is not a file: {}".format(what, path))
        if args.case_list and not runner.absolute_path(args.case_list).is_file():
            parser.error("--case-list is not a file: {}".format(args.case_list))
        common.require_new_or_empty(
            parser, runner.absolute_path(args.out_dir), "output directory"
        )
        save_root = runner.instance_save_dir(args)
        if save_root is not None:
            common.require_new_or_empty(
                parser, save_root, "instance save directory"
            )
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
