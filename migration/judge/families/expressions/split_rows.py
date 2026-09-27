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
from pathlib import Path
import re
import subprocess
import sys

import generate


DESCRIPTION = (
    "Find the sweeps over tm, and over tm a, tm b, that fail on the C++ reference although some "
    "rows other than the all-NULL row succeed, and write split-rows.tsv, from which "
    "'generate.py cases' splits those sweeps. Runs the corpus without splits once, with every "
    "failed sweep followed by one statement per row or row pair, against a running reference "
    "instance under the reduced init."
)
DEFAULT_MYSQLTEST = "/Users/colin/seekdb-dev/ref-archive-834bbee1e/client/mysqltest"
MARKER = re.compile(r"^## S (\d+) (\S+)$")
CROSS_SCOPE = re.compile(r"^a\.id IN \((\d+(?:, \d+)*)\) AND b\.id IN \((\d+(?:, \d+)*)\)$")
END_MARKER = "## E"
CLIENT_ERRNO_FIRST = 2000
CLIENT_ERRNO_LAST = 2999


def row_keys(statement):
    match = generate.MATRIX_SWEEP.match(statement)
    if match:
        excluded = set(match.group("excluded").split(", ")) if match.group("excluded") else set()
        items = match.group("items")
        return [
            (str(row), "SELECT id, {} FROM tm WHERE id = {}".format(items, row))
            for row in range(1, 9)
            if str(row) not in excluded
        ]
    match = generate.CROSS_SWEEP.match(statement)
    if match:
        prefix = "SELECT a.id, b.id, {} FROM tm a, tm b WHERE ".format(match.group("items"))
        a_rows = b_rows = range(1, 9)
        condition = match.group("condition")
        if condition:
            scope = CROSS_SCOPE.match(condition)
            if scope is None:
                raise generate.GeneratorError("cannot tell which row pairs this condition selects: {}".format(condition))
            a_rows = [int(r) for r in scope.group(1).split(", ")]
            b_rows = [int(r) for r in scope.group(2).split(", ")]
            prefix += "({}) AND ".format(condition)
        return [
            ("{}:{}".format(a, b), prefix + "a.id = {} AND b.id = {}".format(a, b))
            for a in a_rows
            for b in b_rows
        ]
    return None


def is_null_row(key):
    return "1" in key.split(":")


def probe_text(case):
    lines = case.text.split("\n")
    out = []
    index = -1
    probes = {}
    pending = None
    for line in lines:
        out.append(line)
        if line == "--echo errno $mysql_errno":
            if pending is not None:
                out.append("if ($mysql_errno)")
                out.append("{")
                for key, query in probes[pending]:
                    out.append("  --echo ## S {} {}".format(pending, key))
                    out.append("  " + query + ";")
                    out.append("  --echo errno $mysql_errno")
                    out.append("  --echo " + END_MARKER)
                out.append("}")
            pending = None
            continue
        if index + 1 < len(case.statements) and line == case.statements[index + 1] + ";":
            index += 1
            if case.roles[index] == "probe":
                keys = row_keys(case.statements[index])
                if keys is not None:
                    probes[index] = keys
                    pending = index
    if index + 1 != len(case.statements):
        raise generate.GeneratorError("could not follow the statements of a corpus file")
    return "\n".join(out), probes


def parse_result(text, statements):
    errnos = generate.recorded_errnos(text, statements)
    per_row = {}
    current = None
    for line in text.split("\n"):
        match = MARKER.match(line)
        if match:
            current = (int(match.group(1)), match.group(2))
            continue
        if line == END_MARKER:
            current = None
            continue
        match = generate.ERRNO_LINE.match(line)
        if match and current is not None:
            per_row.setdefault(current[0], {})[current[1]] = int(match.group(1))
    return errnos, per_row


def run_file(args, test_path, result_path):
    command = [
        args.mysqltest,
        "--host={}".format(args.host),
        "--port={}".format(args.port),
        "--user={}".format(args.user),
        "--password={}".format(args.password),
        "--database=test",
        "--tmpdir={}".format(args.work_dir),
        "--logdir={}".format(args.work_dir),
        "--silent",
        "--test-file={}".format(test_path),
        "--result-file={}".format(result_path),
        "--record",
    ]
    result = subprocess.run(
        command,
        cwd=str(generate.REPO_ROOT / "tools" / "deploy"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        check=False,
    )
    if result.returncode != 0:
        raise generate.GeneratorError("mysqltest failed on {}: {}".format(test_path, result.stdout[-2000:]))


def find_splits(args):
    index, entries = generate.load_registry()
    measured = generate.read_measured(entries, generate.TSV_PATH)
    files, rows, heads = generate.build_corpus(entries, measured, splits={})
    splits = []
    swept = 0
    failed = 0
    for name, case in files.items():
        if name == generate.KNOWN_ANSWERS_CASE:
            continue
        stem = name[: -len(".test")]
        text, probes = probe_text(case)
        if not probes:
            continue
        test_path = args.work_dir / (stem + ".test")
        result_path = args.work_dir / (stem + ".result")
        test_path.write_bytes(text.encode("utf-8"))
        run_file(args, test_path, result_path)
        errnos, per_row = parse_result(result_path.read_text(encoding="utf-8", errors="replace"), case.statements)
        for number, (errno, _) in enumerate(errnos):
            if errno is None:
                raise generate.GeneratorError("{}: statement {} or its errno line not found".format(stem, number + 1))
            if CLIENT_ERRNO_FIRST <= errno <= CLIENT_ERRNO_LAST:
                raise generate.GeneratorError("{}: the connection was lost at statement {}".format(stem, number + 1))
        for number, keys in probes.items():
            swept += 1
            if not errnos[number][0]:
                if number in per_row:
                    raise generate.GeneratorError("{}: row statements ran after a sweep that succeeded".format(stem))
                continue
            failed += 1
            outcome = per_row.get(number, {})
            if sorted(outcome) != sorted(k for k, _ in keys):
                raise generate.GeneratorError("{}: row statements of statement {} missing".format(stem, number + 1))
            for key, errno in outcome.items():
                if CLIENT_ERRNO_FIRST <= errno <= CLIENT_ERRNO_LAST:
                    raise generate.GeneratorError("{}: the connection was lost at row {} of statement {}".format(stem, key, number + 1))
            bad = [k for k, _ in keys if outcome[k] != 0]
            good = [k for k, _ in keys if outcome[k] == 0 and not is_null_row(k)]
            if bad and good:
                splits.append((case.statements[number], bad))
        print("{}: {} sweeps, {} split so far".format(stem, len(probes), len(splits)), flush=True)
    unique = []
    seen = set()
    for statement, bad in splits:
        if statement not in seen:
            seen.add(statement)
            unique.append((statement, bad))
    lines = ["statement\trows"] + ["{}\t{}".format(s, ",".join(b)) for s, b in unique]
    Path(args.out).write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    print("{} sweeps, {} failed, {} split, written to {}".format(swept, failed, len(unique), args.out))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--port", required=True, type=int, help="port of a running reference instance")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--mysqltest", default=DEFAULT_MYSQLTEST)
    parser.add_argument("--work-dir", required=True, help="new directory for the probe files and their results")
    parser.add_argument("--out", default=str(generate.SPLIT_ROWS_PATH), help="split-rows.tsv to write")
    args = parser.parse_args(argv)
    args.work_dir = Path(args.work_dir).resolve()
    try:
        args.work_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print("error: {} already exists".format(args.work_dir), file=sys.stderr)
        return 1
    try:
        return find_splits(args)
    except generate.GeneratorError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
