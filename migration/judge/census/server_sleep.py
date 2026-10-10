#!/usr/bin/env python3
"""Replace mysqltest's built-in sleeps with `--exec sleep N` in judge-owned files.

Environment fix, not an assertion change. obclient 2.2.12's mysqltest on macOS
sleeps short and erratically (--real_sleep 1 measured 0.42 s, --sleep 1 between
0.23 and 1.00 s on 2026-10-10). Two earlier replacements were wrong too: a
server-side `select sleep(N)` hits the 10 s statement timeout (error 4012) and
fails on a connection that has a `send` still waiting for `reap`. The shell's
sleep(1) is exact, uses no database connection, has no timeout, and prints
nothing, so result files are unaffected.

Idempotent. Rewrites, in place, any of:
  --sleep N / sleep N; / --real_sleep N / real_sleep N;     (N literal or $var)
  let $judge_sleep = `select sleep(N)`;                      (earlier fix)
  let $judge_sleep_left = N; while (...) { ... } (6 lines)   (earlier fix)

Usage: python3 -I server_sleep.py FILE [FILE ...]   (prints counts)
"""
import re
import sys

CLIENT = re.compile(r"^(\s*)(?:--)?(?:real_)?sleep\s+([0-9]+(?:\.[0-9]+)?|\$\w+)\s*;?\s*$")
SERVER = re.compile(r"^(\s*)let \$judge_sleep = `select sleep\(([0-9]+(?:\.[0-9]+)?|\$\w+)\)`;\s*$")
LOOP_HEAD = re.compile(r"^(\s*)let \$judge_sleep_left = ([0-9]+|\$\w+);\s*$")
LOOP_BODY = ("while ($judge_sleep_left)", "{", "let $judge_sleep = `select sleep(1)`;",
             "dec $judge_sleep_left;", "}")


def exec_line(indent, amount):
    return "{}--exec sleep {}".format(indent, amount)


for path in sys.argv[1:]:
    with open(path, encoding="utf-8", newline="") as handle:
        lines = handle.read().split("\n")
    out, changed, i = [], 0, 0
    while i < len(lines):
        line = lines[i]
        crlf = "\r" if line.endswith("\r") else ""
        body = line[:-1] if crlf else line
        head = LOOP_HEAD.match(body)
        if head and [l.rstrip("\r").strip() for l in lines[i + 1:i + 6]] == list(LOOP_BODY):
            out.append(exec_line(head.group(1), head.group(2)) + crlf)
            changed += 1
            i += 6
            continue
        match = CLIENT.match(body) or SERVER.match(body)
        if match:
            new = exec_line(match.group(1), match.group(2))
            if new != body:
                changed += 1
            out.append(new + crlf)
        else:
            out.append(line)
        i += 1
    if changed:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("\n".join(out))
    print("{:3d} {}".format(changed, path))
