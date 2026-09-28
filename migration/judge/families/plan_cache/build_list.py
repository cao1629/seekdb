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
import importlib.util
from pathlib import Path
import sys

import differences


DESCRIPTION = (
    "Write family 7's list for compare --plan-cache-not-comparable from C++ recordings "
    "of the reference: hits are compared for no case; misses are compared only for the "
    "cases that every recording reached in under the bound between the two reads and "
    "on which all of them agree; every other case is listed as both. Counts from "
    "instances alive between 22:00 and 23:01 are left out."
)
RUNNER_PATH = differences.REPO_ROOT / ".github" / "script" / "seekdb" / "mysqltest_for_seekdb.py"
MIN_RECORDINGS = 3


def configured_cases():
    spec = importlib.util.spec_from_file_location("mysqltest_for_seekdb", str(RUNNER_PATH))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return [case.name for case in runner.discover_cases(differences.REPO_ROOT)]


def decide(entries, bound):
    if len(entries) < MIN_RECORDINGS:
        return "both", "reached by {} recording{}".format(len(entries), "" if len(entries) == 1 else "s")
    seconds = [entry["seconds"] for entry in entries]
    if any(value is None for value in seconds):
        return "both", "a recording has no time between the reads"
    if max(seconds) >= bound:
        return "both", "reads {:.3f} s apart or more in some recording".format(bound)
    if len(set(entry["misses"] for entry in entries)) != 1:
        return "both", "misses differ although every read gap is under {} s".format(bound)
    return "hits", "misses agree in every recording, every read gap under {} s".format(bound)


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument(
        "--recording", action="append", required=True, type=differences.parse_run,
        metavar="NAME=DIR", help="a record.sh run directory; repeat for every recording",
    )
    parser.add_argument("--bound", type=float, required=True, help="seconds between the reads")
    parser.add_argument("--out-list", required=True)
    parser.add_argument("--out-tsv", required=True)
    args = parser.parse_args(argv)
    names = [name for name, _ in args.recording]
    if len(set(names)) != len(names):
        parser.error("recording names must differ")
    paths = dict(args.recording)
    data = {}
    skipped = {}
    for name, path in args.recording:
        data[name] = differences.read_recording(path)
        skipped[name] = differences.skip_window_hour(path, data[name])
    cases = configured_cases()
    rows = []
    for case in cases:
        entries = [data[name]["counts"][case] for name in names if case in data[name]["counts"]]
        failed = [name for name in names if case in data[name]["failures"]]
        word, reason = decide(entries, args.bound)
        if failed:
            word, reason = "both", "failed in " + ", ".join(failed)
        rows.append((case, word, reason))
    compared = [case for case, word, _ in rows if word == "hits"]
    lines = [
        "# Family 7: the plan cache counts compare --plan-cache-not-comparable does not compare, for",
        "#   C++ recordings made with families/plan_cache/record.sh. Hits are listed for every case: the",
        "#   server's own timers add hits at about 5 a second even with no statement running. Misses are",
        "#   compared only for the {} cases below listed as hits: at least {} recordings reached them, every".format(len(compared), MIN_RECORDINGS),
        "#   one with the two reads under {} s apart, and all of them agree on their misses; every other".format(args.bound),
        "#   case is listed as both. README.md, \"Which counts family 7 compares\", gives the evidence.",
        "# Written by: python3 migration/judge/families/plan_cache/build_list.py --bound {} \\".format(args.bound),
    ]
    for name in names:
        lines.append("#   --recording {}={} \\".format(name, paths[name].resolve()))
    lines.append("#   --out-list <this file> --out-tsv <its evidence>")
    for name in names:
        lines.append("# {}: {} cases counted, {}".format(name, len(data[name]["counts"]), skipped[name]["reason"]))
    for case, word, reason in rows:
        lines.append("{} {}  # {}".format(case, word, reason))
    Path(args.out_list).write_text("\n".join(lines) + "\n")
    header = ["case", "listed", "reason"]
    for name in names:
        header += [name + "_hits", name + "_misses", name + "_seconds"]
    tsv = ["\t".join(header)]
    for case, word, reason in rows:
        fields = [case, word, reason]
        for name in names:
            entry = data[name]["counts"].get(case)
            if entry is None:
                fields += ["", "", ""]
            else:
                fields += [
                    str(entry["hits"]),
                    str(entry["misses"]),
                    "" if entry["seconds"] is None else "{:.3f}".format(entry["seconds"]),
                ]
        tsv.append("\t".join(fields))
    Path(args.out_tsv).write_text("\n".join(tsv) + "\n")
    print("{} cases: misses compared for {}, nothing compared for {}".format(
        len(rows), len(compared), len(rows) - len(compared)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
