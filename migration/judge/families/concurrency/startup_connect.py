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
import re
import sys
import threading
import time

import recording_common as common


DESCRIPTION = (
    "Start seekdb while two obclient sessions keep trying to connect and run a "
    "fixed query, then kill and restart it the same way, and record what each "
    "session saw in the recording format of mysqltest_for_seekdb.py."
)
QUERY = "select schema_name from information_schema.schemata order by schema_name;"
SESSIONS = ("a", "b")
PROBE_DATABASE = "startup_probe"
ATTEMPT_TIMEOUT = 30
RETRY_INTERVAL = 0.05
SESSION_DEADLINE = 600
POLL_INTERVAL = 0.2
JOIN_TIMEOUT = ATTEMPT_TIMEOUT + 10
ERROR_PATTERN = re.compile(r"^ERROR (\d+)(?: \(([0-9A-Za-z]{5})\))?")


def attempt_error(code, error_output):
    if code is None:
        return "no answer within {} seconds".format(ATTEMPT_TIMEOUT)
    for line in error_output.decode("utf-8", "replace").split("\n"):
        match = ERROR_PATTERN.match(line)
        if match:
            if match.group(2):
                return "{} ({})".format(match.group(1), match.group(2))
            return match.group(1)
    return "exit code {} without an ERROR line".format(code)


def error_order(error):
    number = error.split(" ", 1)[0]
    return (0, int(number), error) if number.isdigit() else (1, 0, error)


class Session(threading.Thread):
    def __init__(self, args, name, stop):
        super(Session, self).__init__(name="session-" + name)
        self.daemon = True
        self.args = args
        self.label = name
        self.stop = stop
        self.errors = set()
        self.attempts = 0
        self.first_failed = None
        self.first_attempt = threading.Event()
        self.output = None
        self.last_error_output = b""
        self.failure = None
        self.seconds = None

    def run(self):
        started = time.monotonic()
        try:
            while not self.stop.is_set():
                code, output, error_output = common.run_client(
                    self.args, QUERY, ("--table",), timeout=ATTEMPT_TIMEOUT
                )
                self.attempts += 1
                if code == 0:
                    self.output = output
                else:
                    self.errors.add(attempt_error(code, error_output))
                    self.last_error_output = error_output
                if self.attempts == 1:
                    self.first_failed = code != 0
                    self.first_attempt.set()
                if code == 0:
                    break
                if time.monotonic() - started >= SESSION_DEADLINE:
                    self.failure = "no success within {} seconds".format(
                        SESSION_DEADLINE
                    )
                    break
                self.stop.wait(RETRY_INTERVAL)
        except Exception as exc:
            self.failure = str(exc)
        finally:
            self.seconds = round(time.monotonic() - started, 3)
            self.first_attempt.set()

    def distinct_errors(self):
        return sorted(self.errors, key=error_order)


def start_sessions(args, stop):
    sessions = [Session(args, name, stop) for name in SESSIONS]
    for session in sessions:
        session.start()
    for session in sessions:
        session.first_attempt.wait(JOIN_TIMEOUT)
    return sessions


def wait_sessions(args, sessions):
    while any(session.is_alive() for session in sessions):
        if common.server_exited(args):
            return "seekdb exited before both sessions got an answer"
        time.sleep(POLL_INTERVAL)
    return None


def record_sessions(recording, sessions):
    for session in sessions:
        errors = session.distinct_errors()
        recording.line(
            "-- session {}: error codes before success: {}".format(
                session.label, ", ".join(errors) if errors else "none"
            )
        )
        if session.output is None:
            recording.line(
                "-- session {}: no success: {}".format(
                    session.label, session.failure or "stopped"
                )
            )
            recording.append(common.tail_lines(session.last_error_output))
            continue
        recording.line("-- session {}: result".format(session.label))
        recording.append(session.output)


def run_round(args, recording, number):
    prefix = "" if number == 0 else "restart {}: ".format(number)
    if number > 0:
        problem = common.kill_server(args)
        if problem is not None:
            recording.line("-- {}stop (kill) failed".format(prefix))
            raise common.CaseFailed("restart {}: {}".format(number, problem))
        recording.line("-- {}stop (kill)".format(prefix))
    common.require_free_ports([args.port])
    recording.line(
        "-- sessions {}: each runs the query below through obclient in a loop, a "
        "new connection per attempt, until it succeeds; both loops start, and fail "
        "once, before seekdb starts".format(" and ".join(SESSIONS))
    )
    recording.line(QUERY)
    stop = threading.Event()
    sessions = start_sessions(args, stop)
    problem = None
    try:
        if any(session.first_failed is None for session in sessions):
            problem = "a session made no first attempt within {} seconds".format(
                JOIN_TIMEOUT
            )
        elif any(session.first_failed is False for session in sessions):
            raise common.runner.RunnerError(
                "something already answers on port {} before seekdb starts".format(
                    args.port
                )
            )
        else:
            try:
                common.start_server(args)
            except common.runner.RunnerError as exc:
                recording.line("-- {}start failed".format(prefix))
                raise common.CaseFailed("{}start: {}".format(prefix, exc))
            recording.line("-- {}start".format(prefix))
            problem = wait_sessions(args, sessions)
    finally:
        stop.set()
        for session in sessions:
            session.join(JOIN_TIMEOUT)
    recording.details = dict(
        (
            session.label,
            {
                "attempts": session.attempts,
                "seconds": session.seconds,
                "errors": session.distinct_errors(),
                "failure": session.failure,
            },
        )
        for session in sessions
    )
    record_sessions(recording, sessions)
    if problem is not None:
        recording.line("-- {}".format(problem))
        raise common.CaseFailed(problem)
    if any(session.output is None for session in sessions):
        raise common.CaseFailed("a session never got an answer")
    recording.check(
        "both sessions got the same result",
        sessions[0].output == sessions[1].output,
    )
    if number == 0:
        common.execute(args, recording, "create database {};".format(PROBE_DATABASE))
    else:
        recording.check(
            "the result lists {}, created before the first kill".format(
                PROBE_DATABASE
            ),
            all(
                "| {} ".format(PROBE_DATABASE).encode("utf-8") in session.output
                for session in sessions
            ),
        )


def case_function(number):
    return lambda args, recording: run_round(args, recording, number)


def command_run(args):
    common.prepare_args(args, __file__, "startup-connect")
    cases = [("first_start", case_function(0))] + [
        ("restart_{}".format(number), case_function(number))
        for number in range(1, args.restarts + 1)
    ]
    recorded = (
        "<case>.result is the round's recording when both sessions got an answer "
        "and every check held; otherwise <case>.partial is what it recorded up to "
        "the failure, and outcomes gives the problems; outcomes.<case>.details "
        "holds each session's attempt count and time, which depend on timing and "
        "are not in the recording"
    )
    extra = {
        "query": QUERY,
        "sessions": list(SESSIONS),
        "restarts": args.restarts,
        "retry_interval_seconds": RETRY_INTERVAL,
        "attempt_timeout_seconds": ATTEMPT_TIMEOUT,
        "session_deadline_seconds": SESSION_DEADLINE,
    }
    return common.run_family(args, cases, recorded, extra)


def create_parser():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    common.add_server_arguments(parser)
    parser.add_argument(
        "--restarts",
        type=common.runner.non_negative_int,
        default=2,
        help="kill-and-restart rounds after the first start (default 2); each is "
        "a case named restart_<n>",
    )
    return parser


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    common.check_server_arguments(parser, args)
    return command_run(args)


if __name__ == "__main__":
    sys.exit(main())
