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

import datetime
import gzip
import os
import signal
import sys
import time


POLL_SECONDS = 0.2


def now():
    return datetime.datetime.now().isoformat(sep=" ", timespec="microseconds")


def main():
    if len(sys.argv) != 3:
        print("usage: logwatch.py LOG_PATH OUT.gz", file=sys.stderr)
        return 2
    path = sys.argv[1]
    out = sys.argv[2]
    state = {"stop": False}

    def request_stop(signum, frame):
        state["stop"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    current = None
    inode = None
    opened = 0
    with gzip.open(out, "wb", compresslevel=1) as raw_sink:
        tail = {"last": b"\n"}

        class Sink:
            @staticmethod
            def write(data):
                raw_sink.write(data)
                tail["last"] = data[-1:]

            @staticmethod
            def mark(text):
                prefix = b"" if tail["last"] == b"\n" else b"\n"
                raw_sink.write(prefix + "#LOGWATCH {} {}\n".format(now(), text).encode())
                tail["last"] = b"\n"

        sink = Sink()
        sink.mark("start {}".format(path))
        while True:
            stopping = state["stop"]
            if current is None:
                try:
                    current = open(path, "rb")
                    inode = os.fstat(current.fileno()).st_ino
                    opened += 1
                    sink.mark("open {} inode {}".format(opened, inode))
                except FileNotFoundError:
                    current = None
            if current is not None:
                data = current.read()
                if data:
                    sink.write(data)
                try:
                    changed = os.stat(path).st_ino != inode
                except FileNotFoundError:
                    changed = True
                if changed:
                    data = current.read()
                    if data:
                        sink.write(data)
                    current.close()
                    current = None
                    sink.mark("close {} inode {}".format(opened, inode))
                    continue
            if stopping:
                if current is not None:
                    data = current.read()
                    if data:
                        sink.write(data)
                    current.close()
                sink.mark("stop")
                break
            time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
