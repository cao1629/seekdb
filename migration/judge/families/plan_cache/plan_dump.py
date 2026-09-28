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

"""Diagnostic runs for family 7: wrap configured cases so that every plan in the
plan cache is written to $PC_DUMP_DIR with its executions and hits right before
and right after the case, then compare two runs of the wrappers plan by plan."""

import argparse
import collections
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
DEPLOY_DIR = REPO_ROOT / "tools" / "deploy"
SYS_DATABASE_ID = 201001
DUMP_DIR_VARIABLE = "PC_DUMP_DIR"
DUMP_COMMAND = (
    "--exec obclient -h$OBMYSQL_MS0 -P$OBMYSQL_PORT -uroot -A -B -N --proxy-mode "
    "\"--init-command=SET ob_enable_plan_cache = 0\" -Doceanbase -e "
    "\"select plan_id, db_id, executions, hit_count, statement "
    "from oceanbase.__all_virtual_plan_stat order by plan_id\" > ${}/{}.{}.tsv"
)


def case_source(case):
    if "." in case:
        suite, stem = case.split(".", 1)
        return "mysql_test/test_suite/{}/t/{}.test".format(suite, stem)
    return "mysql_test/t/{}.test".format(case)


def wrapper_stem(case):
    return "pcd_" + case.replace(".", "_")


def make_tests(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    names = []
    for case in args.cases:
        source = case_source(case)
        if not (DEPLOY_DIR / source).is_file():
            raise SystemExit("no such case: {}".format(case))
        stem = wrapper_stem(case)
        body = [
            "--exec mkdir -p ${}".format(DUMP_DIR_VARIABLE),
            DUMP_COMMAND.format(DUMP_DIR_VARIABLE, stem, "before"),
            "--source {}".format(source),
            DUMP_COMMAND.format(DUMP_DIR_VARIABLE, stem, "after"),
            "",
        ]
        (out / (stem + ".test")).write_text("\n".join(body))
        names.append(stem)
    (out / "cases.json").write_text(json.dumps(dict(zip(names, args.cases)), indent=1))
    print("wrote {} wrappers to {}".format(len(names), out))
    return 0


def parse_dump(path):
    plans = {}
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        fields = line.split("\t")
        if len(fields) != 5 or not fields[0].isdigit():
            raise ValueError("{}:{}: not a plan row".format(path, number))
        plans[int(fields[0])] = {
            "db_id": int(fields[1]),
            "executions": int(fields[2]),
            "hit_count": int(fields[3]),
            "stmt": fields[4],
        }
    return plans


def read_dumps(dump_dir, stem):
    return (
        parse_dump(dump_dir / (stem + ".before.tsv")),
        parse_dump(dump_dir / (stem + ".after.tsv")),
    )


def plan_deltas(before, after):
    deltas = collections.defaultdict(lambda: [0, 0, 0])
    for plan_id in set(before) | set(after):
        b = before.get(plan_id)
        a = after.get(plan_id)
        source = a or b
        key = (source["db_id"], source["stmt"])
        executions = (a["executions"] if a else 0) - (b["executions"] if b else 0)
        hits = (a["hit_count"] if a else 0) - (b["hit_count"] if b else 0)
        entry = deltas[key]
        entry[0] += executions
        entry[1] += hits
        if b is None:
            entry[2] += 1
        elif a is None:
            entry[2] -= 1
    return dict((key, value) for key, value in deltas.items() if value != [0, 0, 0])


def totals(deltas, user_only):
    executions = 0
    hits = 0
    for (db_id, _), (delta_executions, delta_hits, _) in deltas.items():
        if user_only and db_id == SYS_DATABASE_ID:
            continue
        executions += delta_executions
        hits += delta_hits
    return {"executions": executions, "hits": hits, "compiles": executions - hits}


def read_counters(run_dir):
    table = run_dir / "rec" / "plan_cache.tsv"
    counters = {}
    if table.is_file():
        for line in table.read_text().splitlines()[1:]:
            case, hits, misses = line.split("\t")
            counters[case] = (int(hits), int(misses))
    return counters


def analyze(args):
    left_dir = Path(args.left)
    right_dir = Path(args.right)
    left_counters = read_counters(left_dir)
    right_counters = read_counters(right_dir)
    names = sorted(
        path.name[: -len(".after.tsv")]
        for path in (left_dir / "dumps").glob("pcd_*.after.tsv")
        if (right_dir / "dumps" / path.name).is_file()
    )
    report = []
    for stem in names:
        left_before, left_after = read_dumps(left_dir / "dumps", stem)
        right_before, right_after = read_dumps(right_dir / "dumps", stem)
        left_deltas = plan_deltas(left_before, left_after)
        right_deltas = plan_deltas(right_before, right_after)
        differing = []
        for key in sorted(set(left_deltas) | set(right_deltas)):
            a = left_deltas.get(key, [0, 0, 0])
            b = right_deltas.get(key, [0, 0, 0])
            if a != b:
                differing.append(
                    {"db_id": key[0], "stmt": key[1][:200], "left": a, "right": b}
                )
        entry = {
            "case": stem,
            "counters": {"left": left_counters.get(stem), "right": right_counters.get(stem)},
            "all_databases": {
                "left": totals(left_deltas, False),
                "right": totals(right_deltas, False),
            },
            "user_databases": {
                "left": totals(left_deltas, True),
                "right": totals(right_deltas, True),
            },
            "differing_statements": differing,
        }
        report.append(entry)
    Path(args.out_json).write_text(json.dumps(report, indent=1))
    for entry in report:
        counters = entry["counters"]
        user = entry["user_databases"]
        print(
            "{} counters {}/{} user-db plans {} {} statements differing {} (user-db {})".format(
                entry["case"],
                counters["left"],
                counters["right"],
                "same" if user["left"] == user["right"] else "DIFF",
                user["left"],
                len(entry["differing_statements"]),
                sum(1 for s in entry["differing_statements"] if s["db_id"] != SYS_DATABASE_ID),
            )
        )
        for statement in entry["differing_statements"]:
            print(
                "    db {} exec/hit/plans {} -> {}  {}".format(
                    statement["db_id"], statement["left"], statement["right"], statement["stmt"][:150]
                )
            )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    make = commands.add_parser("make-tests", help="write one wrapper .test per case")
    make.add_argument("--out", required=True, help="new directory for the wrappers")
    make.add_argument("cases", nargs="+")
    make.set_defaults(handler=make_tests)
    compare = commands.add_parser("analyze", help="compare two runs of the wrappers")
    compare.add_argument("--left", required=True, help="run directory with rec/ and dumps/")
    compare.add_argument("--right", required=True, help="run directory with rec/ and dumps/")
    compare.add_argument("--out-json", required=True)
    compare.set_defaults(handler=analyze)
    args = parser.parse_args()
    if not hasattr(args, "handler"):
        parser.print_usage(sys.stderr)
        return 2
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
