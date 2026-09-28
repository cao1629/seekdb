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
from collections import Counter
import datetime
import json
import os
import re
import signal
import sys
import time


DESCRIPTION = (
    "Keep the MySQL LOGIN lines of a runner instance's seekdb.log while the run goes "
    "on (watch), and check that a --compress recording's mysqltest sessions used the "
    "compressed protocol, against a plain recording of the same cases (check)."
)
POLL_SECONDS = 0.2
DRAIN_QUIET_SECONDS = 10
LOGIN_MARKER = b"MySQL LOGIN("
LOGIN_FIELDS = re.compile(
    r"user_name=(?P<user>[^,]*), .*?capability=(?P<capability>[0-9]+), "
    r'c/s protocol="(?P<protocol>[A-Z_]+)", .*?proc_ret=(?P<proc_ret>-?[0-9]+), '
    r"ret=(?P<ret>-?[0-9]+)"
)
COMPRESSED = "OB_MYSQL_COMPRESS_CS_TYPE"
PLAIN = "OB_MYSQL_CS_TYPE"
CLIENT_COMPRESS = 0x20
RUNNER_USER = "root"


def now():
    return datetime.datetime.now().isoformat(sep=" ", timespec="microseconds")


class Followed(object):
    def __init__(self, handle, inode, number):
        self.handle = handle
        self.inode = inode
        self.number = number
        self.pending = b""
        self.quiet_since = None


def command_watch(args):
    state = {"stop": False}

    def request_stop(signum, frame):
        state["stop"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    current = None
    draining = []
    opened = 0
    with open(args.out, "wb") as sink:

        def take(followed, data):
            if not data:
                return False
            followed.pending += data
            lines = followed.pending.split(b"\n")
            followed.pending = lines.pop()
            for line in lines:
                if LOGIN_MARKER in line:
                    sink.write(line + b"\n")
            sink.flush()
            return True

        def finish(followed):
            take(followed, followed.handle.read())
            if followed.pending:
                take(followed, b"\n")
            followed.handle.close()
            mark("close {} inode {}".format(followed.number, followed.inode))

        def mark(text):
            sink.write("#WATCH {} {}\n".format(now(), text).encode())
            sink.flush()

        mark("start {}".format(args.log))
        while True:
            stopping = state["stop"]
            if current is None:
                try:
                    handle = open(args.log, "rb")
                    opened += 1
                    current = Followed(handle, os.fstat(handle.fileno()).st_ino, opened)
                    mark("open {} inode {}".format(opened, current.inode))
                except FileNotFoundError:
                    current = None
            if current is not None:
                take(current, current.handle.read())
                try:
                    changed = os.stat(args.log).st_ino != current.inode
                except FileNotFoundError:
                    changed = True
                if changed:
                    take(current, current.handle.read())
                    current.quiet_since = time.monotonic()
                    draining.append(current)
                    mark("draining {} inode {}".format(current.number, current.inode))
                    current = None
                    continue
            for followed in list(draining):
                if take(followed, followed.handle.read()):
                    followed.quiet_since = time.monotonic()
                elif time.monotonic() - followed.quiet_since >= DRAIN_QUIET_SECONDS:
                    finish(followed)
                    draining.remove(followed)
            if stopping:
                for followed in draining + ([current] if current is not None else []):
                    finish(followed)
                mark("stop")
                break
            time.sleep(POLL_SECONDS)
    return 0


def read_logins(path):
    logins = []
    failed = 0
    unreadable = 0
    with open(path, "rb") as source:
        for raw in source:
            if raw.startswith(b"#WATCH "):
                continue
            match = LOGIN_FIELDS.search(raw.decode("utf-8", "replace"))
            if match is None:
                unreadable += 1
                continue
            if match.group("ret") != "0" or match.group("proc_ret") != "0":
                failed += 1
                continue
            logins.append(
                (
                    match.group("user"),
                    int(match.group("capability")),
                    match.group("protocol"),
                )
            )
    return logins, failed, unreadable


def summarize(logins):
    return {
        "logins": len(logins),
        "by_user_and_protocol": sorted(
            [user, protocol, count]
            for (user, protocol), count in Counter(
                (user, protocol) for user, _, protocol in logins
            ).items()
        ),
        "capabilities": sorted(
            [user, capability, count]
            for (user, capability), count in Counter(
                (user, capability) for user, capability, _ in logins
            ).items()
        ),
    }


def command_check(args):
    compressed, compressed_failed, compressed_unreadable = read_logins(args.compress)
    plain, plain_failed, plain_unreadable = read_logins(args.plain)
    checks = []

    def check(text, passed):
        checks.append({"check": text, "passed": bool(passed)})

    check("no unreadable MySQL LOGIN line in either file", compressed_unreadable == 0 and plain_unreadable == 0)
    compressed_users = Counter(user for user, _, _ in compressed)
    plain_users = Counter(user for user, _, _ in plain)
    count_differences = sorted(
        [user, compressed_users.get(user, 0), plain_users.get(user, 0)]
        for user in set(compressed_users) | set(plain_users)
        if compressed_users.get(user, 0) != plain_users.get(user, 0)
    )
    check(
        "in the plain run no login uses the compressed protocol",
        all(protocol == PLAIN for _, _, protocol in plain),
    )
    check(
        "in the plain run no login asks for CLIENT_COMPRESS",
        all(not capability & CLIENT_COMPRESS for _, capability, _ in plain),
    )
    check(
        "in the --compress run every login that is not the runner's {} uses the "
        "compressed protocol and asks for CLIENT_COMPRESS".format(RUNNER_USER),
        all(
            protocol == COMPRESSED and capability & CLIENT_COMPRESS
            for user, capability, protocol in compressed
            if user != RUNNER_USER
        ),
    )
    check(
        "in the --compress run every login uses the compressed protocol exactly when "
        "it asks for CLIENT_COMPRESS",
        all(
            (protocol == COMPRESSED) == bool(capability & CLIENT_COMPRESS)
            for _, capability, protocol in compressed
        ),
    )
    check(
        "in the --compress run the admin logins, mysqltest's own, are all compressed and "
        "there is at least one",
        compressed_users.get("admin", 0) > 0
        and all(
            protocol == COMPRESSED for user, _, protocol in compressed if user == "admin"
        ),
    )
    report = {
        "compress_file": os.path.abspath(args.compress),
        "plain_file": os.path.abspath(args.plain),
        "compress": dict(summarize(compressed), failed_logins=compressed_failed),
        "plain": dict(summarize(plain), failed_logins=plain_failed),
        "checks": checks,
        "login_count_differences": count_differences,
        "passed": all(item["passed"] for item in checks),
    }
    text = json.dumps(report, indent=1)
    if args.out:
        with open(args.out, "w") as sink:
            sink.write(text + "\n")
    for item in checks:
        print("{}: {}".format("yes" if item["passed"] else "no", item["check"]))
    for name in ("compress", "plain"):
        for user, protocol, count in report[name]["by_user_and_protocol"]:
            print("{} run: {} logins as {} with {}".format(name, count, user, protocol))
        print("{} run: {} failed logins".format(name, report[name]["failed_logins"]))
    for user, compressed_count, plain_count in count_differences:
        print(
            "logins as {}: {} in the --compress run, {} in the plain run (not a check: the server "
            "drops an INFO line when its log buffer is full)".format(user, compressed_count, plain_count)
        )
    print("passed" if report["passed"] else "failed")
    return 0 if report["passed"] else 1


def create_parser():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    commands = parser.add_subparsers(dest="command", required=True)
    watch = commands.add_parser("watch", help="copy the MySQL LOGIN lines of LOG to OUT until SIGTERM")
    watch.add_argument("log", help="the instance's log/seekdb.log")
    watch.add_argument("out", help="file to write")
    watch.set_defaults(handler=command_watch)
    check = commands.add_parser("check", help="compare the logins of a --compress run and a plain run")
    check.add_argument("--compress", required=True, help="watch output of the --compress run")
    check.add_argument("--plain", required=True, help="watch output of the plain run")
    check.add_argument("--out", help="write the report to this file as JSON")
    check.set_defaults(handler=command_check)
    return parser


def main(argv=None):
    args = create_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
