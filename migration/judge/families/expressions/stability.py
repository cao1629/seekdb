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
import difflib
from pathlib import Path
import re
import subprocess
import sys

import generate


DESCRIPTION = (
    "Run every corpus file against a running instance with each SELECT probe that reads no "
    "session state repeated several times, a different unrelated statement before each repeat, "
    "and report the probes whose output changes between repeats. A probe that reads memory it "
    "never wrote prints different values in one session, which two recordings may miss."
)
DEFAULT_MYSQLTEST = "/Users/colin/seekdb-dev/ref-archive-834bbee1e/client/mysqltest"
NOISE = (
    "SELECT SHA2(REPEAT(CONCAT(id, 'x'), 700), 512) AS noise FROM tm ORDER BY id",
    "SELECT CAST(c_double AS CHAR) AS noise, c_dec_65_30 * 3 AS n2 FROM tm ORDER BY id",
    "SELECT ST_AsText(ST_Buffer(POINT(id, id), 2)) AS noise FROM tm ORDER BY id",
    "SELECT JSON_ARRAYAGG(c_varchar) AS noise FROM tm",
    "SELECT GROUP_CONCAT(HEX(c_blob) ORDER BY id) AS noise FROM tm",
)
STATEFUL = re.compile(
    r"@|LAST_INSERT_ID|ROW_COUNT|FOUND_ROWS|_LOCK|LOCKS|SLEEP|BENCHMARK|NEXTVAL|CONNECTION_ID|UUID|RAND|"
    r"RANDOM|SYSDATE|CURRENT_SCN|TRACE_ID|EXECUTION_ID|TRANSACTION_ID|HOST_IP|RPC_PORT|MYSQL_PORT|"
    r"INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER|SET |CALL",
    re.I,
)
MARKER = re.compile(r"^## R (\d+) (\d+)$")
END_MARKER = "## E"


def repeated_text(case, repeats):
    out = []
    index = -1
    targets = []
    pending = None
    noise = 0
    for line in case.text.split("\n"):
        out.append(line)
        if line == "--echo errno $mysql_errno":
            if pending is not None:
                for repeat in range(repeats):
                    noise += 1
                    out.append("--disable_result_log")
                    out.append(NOISE[noise % len(NOISE)] + ";")
                    out.append("--enable_result_log")
                    out.append("--echo ## R {} {}".format(pending, repeat))
                    out.append(case.statements[pending] + ";")
                    out.append("--echo errno $mysql_errno")
                    out.append("--echo " + END_MARKER)
            pending = None
            continue
        if index + 1 < len(case.statements) and line == case.statements[index + 1] + ";":
            index += 1
            statement = case.statements[index]
            if case.roles[index] == "probe" and statement.startswith("SELECT") and not STATEFUL.search(statement):
                targets.append(index)
                pending = index
    if index + 1 != len(case.statements):
        raise generate.GeneratorError("could not follow the statements of a corpus file")
    return "\n".join(out), targets


def statement_blocks(lines, statements):
    blocks = {}
    position = 0
    for number, statement in enumerate(statements):
        echo = statement + ";"
        while position < len(lines) and lines[position] != echo:
            position += 1
        start = position
        while position < len(lines) and not generate.ERRNO_LINE.match(lines[position]):
            position += 1
        blocks[number] = lines[start : position + 1]
        position += 1
    return blocks


def repeat_blocks(lines):
    blocks = {}
    current = None
    for line in lines:
        match = MARKER.match(line)
        if match:
            current = (int(match.group(1)), int(match.group(2)))
            blocks[current] = []
            continue
        if line == END_MARKER:
            current = None
            continue
        if current is not None:
            blocks[current].append(line)
    return blocks


def check(args):
    index, entries = generate.load_registry()
    measured = generate.read_measured(entries, generate.TSV_PATH)
    files, rows, heads = generate.build_corpus(entries, measured)
    checked = 0
    unstable = 0
    for name, case in files.items():
        if name == generate.KNOWN_ANSWERS_CASE:
            continue
        stem = name[: -len(".test")]
        text, targets = repeated_text(case, args.repeats)
        test_path = args.work_dir / (stem + ".test")
        result_path = args.work_dir / (stem + ".result")
        test_path.write_bytes(text.encode("utf-8"))
        result = subprocess.run(
            [
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
            ],
            cwd=str(generate.REPO_ROOT / "tools" / "deploy"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            check=False,
        )
        if result.returncode != 0:
            raise generate.GeneratorError("mysqltest failed on {}: {}".format(test_path, result.stdout[-2000:]))
        lines = result_path.read_text(encoding="utf-8", errors="replace").split("\n")
        first = statement_blocks(lines, case.statements)
        again = repeat_blocks(lines)
        for number in targets:
            checked += 1
            variants = [again.get((number, repeat)) for repeat in range(args.repeats)]
            changed = [v for v in variants if v != first[number]]
            if changed:
                unstable += 1
                print("unstable: {} statement {}: {}".format(stem, number + 1, case.statements[number]))
                for line in list(difflib.unified_diff(first[number], changed[0] or [], lineterm=""))[2:12]:
                    print("    " + line)
    print("{} probes checked, {} repeats each, {} unstable".format(checked, args.repeats, unstable))
    return 1 if unstable else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--port", required=True, type=int, help="port of a running instance under the reduced init")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--mysqltest", default=DEFAULT_MYSQLTEST)
    parser.add_argument("--repeats", default=3, type=int)
    parser.add_argument("--work-dir", required=True, help="new directory for the repeated files and their results")
    args = parser.parse_args(argv)
    args.work_dir = Path(args.work_dir).resolve()
    try:
        args.work_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print("error: {} already exists".format(args.work_dir), file=sys.stderr)
        return 1
    try:
        return check(args)
    except generate.GeneratorError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
