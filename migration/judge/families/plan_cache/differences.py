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

"""List the cases whose plan cache counts differ between two recordings, with
the time between each case's two reads and the background work that the server
log shows between them."""

import argparse
import collections
import datetime
import gzip
import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
DEPLOY_DIR = REPO_ROOT / "tools" / "deploy"
INSTANCE_START = b"[server_start 3/14] observer syslog service init success."
LOG_LINE = re.compile(
    rb"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6})\] (\w+)\s+(?:\[(\w+)\] )?(\S+) \(([^)]*)\) "
    rb"\[(\d+)\]\[([^\]]*)\]\[([^\]]*)\] \[lt=\d+\] ?(.*)$"
)
LOGIN = re.compile(rb"MySQL LOGIN\(.*user_name=([^,]*), host_name=[^,]*, sessid=(\d+),")
RUN_LINE = re.compile(r"^\[ RUN      \] (\S+)$")
OK_LINE = re.compile(r"^\[       OK \] (\S+) \(")
FAILED_LINE = re.compile(r"^\[  FAILED  \] (\S+) \(.*exit=(-?\d+)\)$")
MESSAGE_LINE = re.compile(r"^(?:\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] )?(mysqltest: .*)$")
STATS_LINE = re.compile(
    r"^\[ PC STATS \] (\S+) hits=(\d+) misses=(\d+) \(([0-9.]+)s between the reads\)$"
)
THREAD_PREFIX = re.compile(r"^(?:TxLoopWorker_|TimerWK\d*_)")
THREAD_DIGITS = re.compile(r"\d+")
MARKERS = (
    ("schema_refresh", "SerScheQueue", "ob_server_schema_updater.cpp:258"),
    ("freeze_info", "FreInfoReload", "ob_freeze_info_manager.cpp:177"),
    ("freezer", "MemstoreFreezer", "ob_memstore_freezer.cpp:117"),
    ("change_stream", "CSFetcher", "ob_change_stream_fetcher.cpp:452"),
    ("scheduler", "DBMSSched", "ob_dbms_sched_job_master.cpp:372"),
    ("lock_gc", "OBJLockGC", "ob_table_access_helper.h:333"),
    ("vector_index_tasks", "VecIdxSched", "ob_vector_index_async_task.cpp:238"),
    ("sys_package", "SystemPkgLoad", "ob_system_package_load_task.cpp:121"),
    ("package_compile", "ReqWorker", "ob_pl_package_manager.cpp:1416"),
)
CAUSE_NAMES = {
    "schema_refresh": "schema refresh rounds",
    "package_compile": "package compiles",
}
SECONDS_BUCKETS = (
    (0.1, "under 0.1 s"),
    (0.3, "0.1 to 0.3 s"),
    (1.0, "0.3 to 1 s"),
    (5.0, "1 to 5 s"),
    (20.0, "5 to 20 s"),
    (None, "20 s or more"),
)
WINDOW_HOUR = 22
WINDOW_LENGTH = datetime.timedelta(hours=1, minutes=1)
LIST_WORDS = {(False, True): "hits", (True, False): "misses", (False, False): "both"}
STAMP =re.compile(rb"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6})\]")
DDL_WAIT_START = "ob_ddl_executor_util.cpp:75"
DDL_WAIT_END = "ob_ddl_executor_util.cpp:129"
INTERESTING = re.compile(
    b"|".join(
        re.escape(needle)
        for needle in [INSTANCE_START, b"MySQL LOGIN("]
        + [location.encode() for _, _, location in MARKERS]
        + [DDL_WAIT_START.encode(), DDL_WAIT_END.encode()]
    )
)
SOURCE_LINE = re.compile(r"^\s*(?:--)?\s*source\s+([^\s;]+)", re.IGNORECASE)
WHILE_LINE = re.compile(r"^\s*(?:--)?\s*while\s*\(", re.IGNORECASE)
SLEEP_LINE = re.compile(r"^\s*(?:--)?\s*(?:real_)?sleep\s+([0-9.]+)", re.IGNORECASE)


def thread_kind(name):
    return THREAD_DIGITS.sub("", THREAD_PREFIX.sub("", name)) or name


def parse_stamp(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f")


def overlaps_window_hour(start, end):
    for day in sorted(set([start.date(), end.date()])):
        opening = datetime.datetime.combine(day, datetime.time(WINDOW_HOUR))
        if opening < end and start < opening + WINDOW_LENGTH:
            return True
    return False


def window_text():
    opening = datetime.datetime.combine(datetime.date(2000, 1, 1), datetime.time(WINDOW_HOUR))
    return "{:%H:%M}-{:%H:%M}".format(opening, opening + WINDOW_LENGTH)


def in_window_hour(started, ended):
    if started is None or ended is None:
        return None
    return overlaps_window_hour(parse_stamp(started), parse_stamp(ended))


def read_runner_log(run_dir):
    order = []
    counts = {}
    failures = {}
    current = None
    for line in (run_dir / "runner.log").read_text(errors="replace").splitlines():
        match = RUN_LINE.match(line)
        if match:
            order.append(match.group(1))
            current = match.group(1)
            continue
        match = MESSAGE_LINE.match(line)
        if match and current is not None:
            failures.setdefault(current, []).append(match.group(1))
            continue
        match = FAILED_LINE.match(line)
        if match:
            failures.setdefault(match.group(1), [])
            failures[match.group(1)].append("exit={}".format(match.group(2)))
            continue
        match = OK_LINE.match(line)
        if match:
            failures.pop(match.group(1), None)
            continue
        match = STATS_LINE.match(line)
        if match:
            counts[match.group(1)] = {
                "hits": int(match.group(2)),
                "misses": int(match.group(3)),
                "seconds": float(match.group(4)),
            }
    return order, counts, failures


def read_recording(run_dir):
    order, counts, failures = read_runner_log(run_dir)
    table = run_dir / "rec" / "plan_cache.tsv"
    manifest_path = run_dir / "rec" / "manifest.json"
    finished = False
    if table.is_file() and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        finished = manifest.get("finished") is True
        seconds = manifest.get("plan_cache_seconds") or {}
        recorded = {}
        for line in table.read_text().splitlines()[1:]:
            case, hits, misses = line.split("\t")
            recorded[case] = {
                "hits": int(hits),
                "misses": int(misses),
                "seconds": seconds.get(case),
            }
        for case, entry in recorded.items():
            logged = counts.get(case)
            if logged and (logged["hits"], logged["misses"]) != (entry["hits"], entry["misses"]):
                raise SystemExit(
                    "{}: plan_cache.tsv and runner.log disagree on {}".format(run_dir, case)
                )
        counts = recorded
        outcomes = manifest.get("outcomes") or {}
        failures = dict(
            (
                case,
                list((outcomes.get(case) or {}).get("failure_lines") or [])
                + ["exit={}".format((outcomes.get(case) or {}).get("exit_code"))],
            )
            for case in manifest.get("failed_cases") or []
        )
    return {"order": order, "counts": counts, "finished": finished, "failures": failures}


def read_times(run_dir):
    result_path = run_dir / "work" / "seekdb_result.json"
    if not result_path.is_file():
        return None
    entries = json.loads(result_path.read_text()).get("plan_cache") or {}
    return dict(
        (case, (parse_stamp(entry["before"]["started"]), parse_stamp(entry["after"]["finished"])))
        for case, entry in entries.items()
        if entry.get("before") and entry.get("after")
    )


def run_span(run_dir):
    started = None
    for line in (run_dir / "times.txt").read_text().splitlines():
        if line.startswith("started "):
            started = datetime.datetime.strptime(line.split()[1] + " " + line.split()[2], "%Y-%m-%d %H:%M:%S")
    ended = datetime.datetime.fromtimestamp((run_dir / "runner.log").stat().st_mtime)
    return started, ended


def skip_window_hour(run_dir, recording):
    times = read_times(run_dir)
    if times is not None:
        skipped = sorted(
            case for case in recording["counts"]
            if case not in times or overlaps_window_hour(*times[case])
        )
        reason = "read times from work/seekdb_result.json: {} cases whose two reads span part of {}".format(
            len(skipped), window_text()
        )
    else:
        started, ended = run_span(run_dir)
        whole = started is None or overlaps_window_hour(started, ended)
        skipped = sorted(recording["counts"]) if whole else []
        reason = "no read times: the run from {} to {} {} {}".format(
            started, ended.replace(microsecond=0), "spans part of" if whole else "lies outside",
            window_text(),
        )
    for case in skipped:
        recording["counts"].pop(case, None)
        recording["failures"].pop(case, None)
    return {"skipped": skipped, "reason": reason}


def new_segment():
    return {"markers": collections.Counter(), "waits": []}


class InstanceScan:
    def __init__(self, started):
        self.started = started
        self.logins = []
        self.segments = [new_segment()]

    def add(self, raw):
        line = LOG_LINE.match(raw)
        if line is None:
            return
        if b"MySQL LOGIN(" in raw:
            login = LOGIN.search(raw)
            if login:
                self.logins.append(
                    (login.group(1).decode(), int(login.group(2)), line.group(1).decode())
                )
                self.segments.append(new_segment())
            return
        segment = self.segments[-1]
        kind = thread_kind(line.group(7).decode("utf-8", "replace"))
        location = line.group(5).decode("utf-8", "replace")
        for name, marker_kind, marker_location in MARKERS:
            if kind == marker_kind and location == marker_location:
                segment["markers"][name] += 1
        if location in (DDL_WAIT_START, DDL_WAIT_END):
            task = re.search(rb"task_id=(\d+)", line.group(9))
            segment["waits"].append(
                (
                    line.group(1).decode(),
                    location == DDL_WAIT_START,
                    task.group(1).decode() if task else "?",
                )
            )

    def window(self):
        admin = [index for index, login in enumerate(self.logins) if login[0] == "admin"]
        if not admin:
            return None
        before = [
            index for index, login in enumerate(self.logins)
            if index < admin[0] and login[0] == "root"
        ]
        if not before:
            return None
        first = before[-1]
        last = len(self.logins) - 1
        markers = collections.Counter()
        events = []
        for segment in self.segments[first + 1:last + 1]:
            markers.update(segment["markers"])
            events.extend(segment["waits"])
        waits = []
        open_waits = {}
        for stamp, is_start, task in events:
            if is_start:
                open_waits[task] = parse_stamp(stamp)
            elif task in open_waits:
                waits.append((parse_stamp(stamp) - open_waits.pop(task)).total_seconds())
        start_stamp = self.logins[first][2]
        end_stamp = self.logins[last][2]
        return {
            "instance_start": self.started,
            "in_window_hour": in_window_hour(start_stamp, end_stamp),
            "before_login": start_stamp,
            "after_login": end_stamp,
            "log_seconds": round(
                (parse_stamp(end_stamp) - parse_stamp(start_stamp)).total_seconds(), 3
            ),
            "markers": dict((name, markers.get(name, 0)) for name, _, _ in MARKERS),
            "ddl_waits": len(waits) + len(open_waits),
            "ddl_wait_seconds": round(sum(waits), 3),
        }


def read_windows(run_dir, order, wanted):
    windows = {}
    log_path = run_dir / "seekdb-log.gz"
    if not log_path.is_file():
        return windows
    positions = [position for position, case in enumerate(order) if case in wanted]
    if not positions:
        return windows
    last_wanted = positions[-1]
    index = -1
    scan = None
    with gzip.open(log_path, "rb") as log:
        try:
            for raw in log:
                if not INTERESTING.search(raw):
                    continue
                if INSTANCE_START in raw:
                    if scan is not None and 0 <= index < len(order) and order[index] in wanted:
                        windows[order[index]] = scan.window()
                    index += 1
                    if index > last_wanted:
                        scan = None
                        break
                    stamp = STAMP.match(raw)
                    scan = (
                        InstanceScan(stamp.group(1).decode() if stamp else None)
                        if order[index] in wanted else None
                    )
                    continue
                if scan is not None:
                    scan.add(raw)
        except EOFError:
            scan = None
    if scan is not None and 0 <= index < len(order) and order[index] in wanted:
        windows[order[index]] = scan.window()
    return windows


def case_test_file(case):
    if "." in case:
        suite, stem = case.split(".", 1)
        return DEPLOY_DIR / "mysql_test" / "test_suite" / suite / "t" / (stem + ".test")
    return DEPLOY_DIR / "mysql_test" / "t" / (case + ".test")


def test_waits(case):
    seen = set()
    pending = [case_test_file(case)]
    sleeps = []
    loops = 0
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for line in path.read_text(errors="replace").splitlines():
            match = SOURCE_LINE.match(line)
            if match:
                pending.append(DEPLOY_DIR / match.group(1))
            match = SLEEP_LINE.match(line)
            if match:
                sleeps.append(float(match.group(1)))
            if WHILE_LINE.match(line):
                loops += 1
    return {"sleep_statements": len(sleeps), "sleep_seconds": round(sum(sleeps), 3), "while_loops": loops}


def describe_differences(left, right):
    parts = []
    for name, _, _ in MARKERS:
        a = left["markers"][name]
        b = right["markers"][name]
        if a != b:
            parts.append("{} {}/{}".format(name, a, b))
    if left["ddl_waits"] or right["ddl_waits"]:
        parts.append(
            "ddl_wait {}x {:.3f}s/{}x {:.3f}s".format(
                left["ddl_waits"], left["ddl_wait_seconds"],
                right["ddl_waits"], right["ddl_wait_seconds"],
            )
        )
    return parts


def classify(left_window, right_window, waits):
    causes = []
    if left_window is None or right_window is None:
        return ["no window in the log"]
    window_hour = left_window["in_window_hour"] or right_window["in_window_hour"]
    for name, _, _ in MARKERS:
        if left_window["markers"][name] != right_window["markers"][name]:
            if name == "package_compile" and window_hour:
                causes.append("statistics window job (22:00-23:00)")
            else:
                causes.append(CAUSE_NAMES.get(name, "timer rounds"))
    if left_window["ddl_waits"] or right_window["ddl_waits"]:
        if abs(left_window["ddl_wait_seconds"] - right_window["ddl_wait_seconds"]) >= 0.1:
            causes.append("ddl wait length")
    if waits["sleep_statements"] or waits["while_loops"]:
        causes.append("test sleeps or loops")
    if not causes:
        causes.append("no logged difference")
    return sorted(set(causes), key=causes.index)


def differs_in(delta_hits, delta_misses):
    if delta_hits and delta_misses:
        return "both"
    if delta_hits:
        return "hits"
    if delta_misses:
        return "misses"
    return "neither"


def parse_run(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=DIR")
    name, directory = value.split("=", 1)
    path = Path(directory)
    if not (path / "runner.log").is_file():
        raise argparse.ArgumentTypeError("{} has no runner.log".format(directory))
    return name, path


def same_counts(a, b):
    return (
        a is not None and b is not None
        and (a["hits"], a["misses"]) == (b["hits"], b["misses"])
    )


def span(values, pattern):
    low, high = pattern.format(min(values)), pattern.format(max(values))
    return low if low == high else low + "-" + high


def list_line(row):
    entries = [entry for entry in row["counts"].values() if entry is not None]
    seconds = [entry["seconds"] for entry in entries if entry["seconds"] is not None]
    parts = [
        "hits {}, misses {} in {} recordings".format(
            span([entry["hits"] for entry in entries], "{}"),
            span([entry["misses"] for entry in entries], "{}"),
            len(entries),
        )
    ]
    if seconds:
        parts.append("{} s between the reads".format(span(seconds, "{:.2f}")))
    if row.get("causes"):
        parts.append("+".join(row["causes"]))
    return "{} {}  # {}".format(row["case"], row["not_comparable"], "; ".join(parts))


def list_text(names, paths, skipped, rows):
    lines = [
        "# Family 7: configured cases whose plan cache counts differ between C++ recordings of",
        "# the reference, for compare --plan-cache-not-comparable. Each line names the counts",
        "# that differ between the recordings that reached the case: hits, misses or both.",
        "# README.md gives the causes and the evidence, not-comparable.tsv the counts.",
        "# Written by differences.py{} from:".format(" --skip-window-hour" if skipped else ""),
    ]
    for name in names:
        lines.append("#   {} {}{}".format(
            name, paths[name].resolve(),
            "" if not skipped else " ({} counts left out)".format(len(skipped[name]["skipped"])),
        ))
    lines.extend(list_line(row) for row in rows if row["not_comparable"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", required=True, type=parse_run, metavar="NAME=DIR")
    parser.add_argument("--right", required=True, type=parse_run, metavar="NAME=DIR")
    parser.add_argument(
        "--extra", action="append", default=[], type=parse_run, metavar="NAME=DIR",
        help="another recording of the same cases; a case that --left and --right "
        "agree on but this recording does not is listed too",
    )
    parser.add_argument(
        "--skip-window-hour", action="store_true",
        help="leave out every count that could hold the statistics window job: in a "
        "recording with work/seekdb_result.json, the cases whose two reads span part of "
        "22:00-23:01; a recording without it (stopped part way) whole, if its run spans "
        "part of that time",
    )
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-tsv", required=True)
    parser.add_argument(
        "--out-list",
        help="write the list for compare --plan-cache-not-comparable: every case whose "
        "hits, misses or both differ between the recordings that reached it",
    )
    args = parser.parse_args()
    runs = [args.left, args.right] + args.extra
    names = [name for name, _ in runs]
    if len(set(names)) != len(names):
        parser.error("recording names must differ")
    paths = dict(runs)
    data = dict((name, read_recording(path)) for name, path in runs)
    skipped = None
    if args.skip_window_hour:
        skipped = dict((name, skip_window_hour(path, data[name])) for name, path in runs)
    left_name, right_name = names[0], names[1]
    left = data[left_name]
    right = data[right_name]
    order = [
        case for case in left["order"]
        if case in left["counts"] and case in right["counts"]
    ]
    status = {}
    pairs = {}
    failed_in = dict(
        (case, [name for name in names if case in data[name]["failures"]]) for case in order
    )
    for case in order:
        if not same_counts(left["counts"][case], right["counts"][case]):
            status[case] = "different"
            pairs[case] = (left_name, right_name)
            continue
        status[case] = "identical"
        for extra_name in names[2:]:
            entry = data[extra_name]["counts"].get(case)
            if entry is not None and not same_counts(entry, left["counts"][case]):
                status[case] = "different in " + extra_name
                pairs[case] = (extra_name, left_name)
                break
        if case not in pairs and failed_in[case]:
            status[case] = "failed in " + ", ".join(failed_in[case])
            pairs[case] = (left_name, right_name)
    wanted = dict((name, set()) for name in names)
    for case, (a, b) in pairs.items():
        wanted[a].add(case)
        wanted[b].add(case)
    windows = dict(
        (name, read_windows(paths[name], data[name]["order"], wanted[name]))
        for name in names
    )
    rows = []
    for case in order:
        row = {
            "case": case,
            "status": status[case],
            "counts": dict((name, data[name]["counts"].get(case)) for name in names),
        }
        if case in pairs:
            a_name, b_name = pairs[case]
            a = data[a_name]["counts"][case]
            b = data[b_name]["counts"][case]
            wa = windows[a_name].get(case)
            wb = windows[b_name].get(case)
            waits = test_waits(case)
            row["evidence_pair"] = [a_name, b_name]
            row["delta_hits"] = b["hits"] - a["hits"]
            row["delta_misses"] = b["misses"] - a["misses"]
            row["differs_in"] = differs_in(row["delta_hits"], row["delta_misses"])
            row["windows"] = {a_name: wa, b_name: wb}
            row["test_waits"] = waits
            row["causes"] = [
                "case failed in " + name for name in failed_in[case]
            ] + classify(wa, wb, waits)
            row["failures"] = dict(
                (name, data[name]["failures"][case]) for name in failed_in[case]
            )
            row["log_differences"] = describe_differences(wa, wb) if wa and wb else []
        rows.append(row)
    summary = collections.Counter(row["status"] for row in rows)
    causes = collections.Counter(
        "+".join(row["causes"]) for row in rows if "causes" in row
    )
    agreement = collections.defaultdict(collections.Counter)
    for row in rows:
        entries = [entry for entry in row["counts"].values() if entry is not None]
        longest = max(entry["seconds"] or 0.0 for entry in entries)
        bucket = next(
            label for limit, label in SECONDS_BUCKETS if limit is None or longest < limit
        )
        row["hits_agree"] = len(set(entry["hits"] for entry in entries)) == 1
        row["misses_agree"] = len(set(entry["misses"] for entry in entries)) == 1
        row["not_comparable"] = LIST_WORDS.get((row["hits_agree"], row["misses_agree"]))
        agreement[bucket]["cases"] += 1
        agreement[bucket]["hits_agree"] += row["hits_agree"]
        agreement[bucket]["misses_agree"] += row["misses_agree"]
        agreement[bucket]["both_agree"] += row["hits_agree"] and row["misses_agree"]
    payload = {
        "recordings": [
            [name, str(paths[name]), data[name]["finished"]] for name in names
        ],
        "left": left_name,
        "right": right_name,
        "markers": [list(marker) for marker in MARKERS],
        "skip_window_hour": skipped,
        "not_comparable": dict(
            collections.Counter(row["not_comparable"] for row in rows if row["not_comparable"])
        ),
        "summary": dict(summary),
        "causes": dict(causes),
        "agreement_by_seconds": dict(
            (label, dict(agreement[label])) for _, label in SECONDS_BUCKETS
            if label in agreement
        ),
        "cases": rows,
    }
    Path(args.out_json).write_text(json.dumps(payload, indent=1))
    header = ["case", "status"]
    for name in names:
        header += [name + "_hits", name + "_misses", name + "_seconds"]
    header += [
        "compared", "differs in", "causes", "log (first/second of compared)", "test sleeps",
        "failure", "not comparable",
    ]
    lines = ["\t".join(header)]
    for row in rows:
        if "causes" not in row:
            continue
        fields = [row["case"], row["status"]]
        for name in names:
            entry = row["counts"][name]
            if entry is None:
                fields += ["", "", ""]
            else:
                fields += [
                    str(entry["hits"]),
                    str(entry["misses"]),
                    "" if entry["seconds"] is None else "{:.3f}".format(entry["seconds"]),
                ]
        waits = row["test_waits"]
        fields += [
            "/".join(row["evidence_pair"]),
            row["differs_in"],
            "+".join(row["causes"]),
            "; ".join(row["log_differences"]) or "-",
            "{} sleeps {:.1f}s, {} loops".format(
                waits["sleep_statements"], waits["sleep_seconds"], waits["while_loops"]
            ),
            "; ".join(
                "{}: {}".format(name, " | ".join(messages))
                for name, messages in sorted(row["failures"].items())
            ) or "-",
            row["not_comparable"] or "-",
        ]
        lines.append("\t".join(fields))
    Path(args.out_tsv).write_text("\n".join(lines) + "\n")
    if args.out_list:
        Path(args.out_list).write_text(list_text(names, paths, skipped, rows))
    print(
        "cases={} {}".format(
            len(rows),
            " ".join("{}={}".format(key.replace(" ", "_"), value)
                     for key, value in sorted(summary.items())),
        )
    )
    for cause, count in causes.most_common():
        print("  {:4d} {}".format(count, cause))
    print(
        "  not comparable: {}".format(
            " ".join(
                "{}={}".format(word, payload["not_comparable"].get(word, 0))
                for word in ("hits", "misses", "both")
            )
        )
    )
    if skipped:
        for name in names:
            print("  {}: {} counts left out; {}".format(
                name, len(skipped[name]["skipped"]), skipped[name]["reason"]
            ))
    for label, counts in payload["agreement_by_seconds"].items():
        print(
            "  longest {}: {} cases, hits agree in {}, misses in {}, both in {}".format(
                label, counts["cases"], counts["hits_agree"], counts["misses_agree"],
                counts["both_agree"],
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
