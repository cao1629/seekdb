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
from collections import namedtuple
import hashlib
import importlib.util
from pathlib import Path
import re
import sys


HELPER_PATH = Path(__file__).resolve()
REPO_ROOT = HELPER_PATH.parents[4]
LIVE_RUNNER = REPO_ROOT / ".github" / "script" / "seekdb" / "mysqltest_for_seekdb.py"
CANDIDATES = (
    REPO_ROOT / "migration" / "judge" / "lists" / "hash-order-select-candidates.txt"
)
DESCRIPTION = (
    "Turn migration/judge/lists/hash-order-select-candidates.txt into a draft of "
    "the list the runner's row-order mask reads (compare --mask row-order): each "
    "candidate the helper can place in the reference output becomes one line with "
    "its echo occurrence, its row count and a digest of the line after its rows, "
    "and an empty hash_operators field that the confirmation step fills in from "
    "the reference's EXPLAIN (the runner refuses the field empty); the others are "
    "written as '# unresolved' lines with the reason."
)
STATEMENT_WORDS = ("eval", "send", "query")
BLOCK_START = re.compile(r"^(if|while)\s*\(.*\)\s*\{?\s*(#.*)?$", re.IGNORECASE)
BLOCK_OTHER = re.compile(r"^\}?\s*(else\s*)?\{?\s*$", re.IGNORECASE)
SILENT_COMMANDS = frozenset(
    (
        "let",
        "inc",
        "dec",
        "sleep",
        "real_sleep",
        "connection",
        "disconnect",
        "disable_warnings",
        "enable_warnings",
        "disable_info",
        "enable_info",
        "sorted_result",
        "replace_column",
        "replace_numeric_round",
        "error",
        "disable_abort_on_error",
        "enable_abort_on_error",
        "disable_ps_protocol",
        "enable_ps_protocol",
    )
)
STATE_COMMANDS = frozenset(
    (
        "enable_result_log",
        "disable_result_log",
        "enable_query_log",
        "disable_query_log",
        "horizontal_results",
        "vertical_results",
        "enable_metadata",
        "disable_metadata",
        "enable_column_names",
        "disable_column_names",
        "enable_sorted_result",
        "disable_sorted_result",
        "delimiter",
        "connect",
    )
)
ECHO_REWRITING_COMMANDS = frozenset(("replace_result", "replace_regex"))
COMMANDS = SILENT_COMMANDS | STATE_COMMANDS | ECHO_REWRITING_COMMANDS | frozenset(
    (
        "echo",
        "source",
        "exec",
        "execw",
        "exec_in_background",
        "system",
        "reap",
        "send_eval",
        "query_vertical",
        "query_horizontal",
        "query_get_value",
        "result_format",
        "explain_protocol",
        "while",
        "if",
        "end",
        "die",
        "exit",
        "skip",
        "perl",
        "ping",
        "require",
        "result",
        "write_file",
        "append_file",
        "cat_file",
        "remove_file",
        "copy_file",
        "move_file",
        "file_exists",
        "mkdir",
        "rmdir",
        "list_files",
        "diff_files",
        "chmod",
        "change_user",
        "send_quit",
        "send_shutdown",
        "shutdown_server",
        "disable_parsing",
        "enable_parsing",
        "disable_reconnect",
        "enable_reconnect",
        "character_set",
        "start_timer",
        "end_timer",
        "lowercase_result",
        "output",
        "dirty_close",
    )
)
WARNING_LINE = re.compile(rb"(Note|Warning|Error)\t[0-9]+\t")
INFO_PREFIXES = (b"affected rows: ", b"info: ")

Item = namedtuple("Item", ("kind", "word", "line", "text", "delimiter", "state"))
Candidate = namedtuple("Candidate", ("case", "line", "text"))


class Unresolved(Exception):
    pass


def load_runner(path):
    spec = importlib.util.spec_from_file_location("mysqltest_for_seekdb", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    missing = [
        name
        for name in (
            "collapse_whitespace",
            "find_statement_echoes",
            "statement_echo_end",
            "result_header_size",
            "split_content_lines",
            "short_digest",
            "parse_hash_order_line",
            "HASH_ORDER_COLUMNS",
            "HASH_OPERATOR_WORD",
        )
        if not hasattr(module, name)
    ]
    if missing:
        raise SystemExit(
            "{} has no {}: apply migration/judge/harness/second-set/"
            "runner-second-set.patch or pass --runner".format(path, ", ".join(missing))
        )
    return module


def read_candidates(path):
    candidates = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) != 3 or not re.match(r"[0-9]+\Z", fields[1]):
            raise SystemExit(
                "{}:{}: expected case, line and statement".format(path, number)
            )
        candidates.append(Candidate(fields[0], int(fields[1]), fields[2]))
    return candidates


def snapshot(state):
    return tuple(sorted(state.items()))


def state_value(item, key):
    return dict(item.state)[key]


def split_at_delimiter(text, delimiter):
    parts = []
    start = 0
    index = 0
    quote = None
    comment = None
    while index < len(text):
        char = text[index]
        if comment == "line":
            if char == "\n":
                comment = None
        elif comment == "block":
            if text.startswith("*/", index):
                comment = None
                index += 1
        elif quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == "#" or text.startswith("-- ", index):
            comment = "line"
        elif text.startswith("/*", index):
            comment = "block"
            index += 1
        elif text.startswith(delimiter, index):
            parts.append((start, index))
            start = index + len(delimiter)
            index = start
            continue
        index += 1
    return parts, start


def strip_leading_comments(text):
    while True:
        stripped = text.lstrip()
        if stripped.startswith("#") or stripped.startswith("-- "):
            newline = stripped.find("\n")
            text = "" if newline < 0 else stripped[newline + 1 :]
        else:
            return stripped


def apply_command(state, word, rest):
    if word == "enable_result_log":
        state["result_log"] = True
    elif word == "disable_result_log":
        state["result_log"] = False
    elif word == "enable_query_log":
        state["query_log"] = True
    elif word == "disable_query_log":
        state["query_log"] = False
    elif word == "vertical_results":
        state["vertical"] = True
    elif word == "horizontal_results":
        state["vertical"] = False
    elif word == "enable_metadata":
        state["metadata"] = True
    elif word == "disable_metadata":
        state["metadata"] = False
    elif word == "disable_column_names":
        state["column_names"] = False
    elif word == "enable_column_names":
        state["column_names"] = True
    elif word == "enable_sorted_result":
        state["sorted"] = True
    elif word == "disable_sorted_result":
        state["sorted"] = False
    elif word == "result_format":
        state["result_format"] = rest.strip().rstrip(";").strip()
    elif word == "explain_protocol":
        state["explain_protocol"] = rest.strip().rstrip(";").strip()
    elif word == "delimiter" and rest.strip():
        state["delimiter"] = rest.strip()


def read_test_items(path):
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Unresolved("cannot read {}: {}".format(path, exc))
    state = {
        "result_log": True,
        "query_log": True,
        "vertical": False,
        "metadata": False,
        "column_names": True,
        "sorted": False,
        "result_format": "1",
        "explain_protocol": "0",
        "delimiter": ";",
        "depth": 0,
    }
    items = []
    buffer = ""
    buffer_line = 0
    for number, raw in enumerate(text.split("\n"), 1):
        if not buffer.strip():
            buffer = ""
            line = raw.strip()
            if not line:
                items.append(Item("blank", "", number, "", state["delimiter"], snapshot(state)))
                continue
            if line.startswith("#"):
                items.append(
                    Item("comment", "", number, line, state["delimiter"], snapshot(state))
                )
                continue
            if line.startswith("--"):
                parts = line[2:].strip().split(None, 1)
                word = parts[0].lower() if parts else ""
                rest = parts[1] if len(parts) > 1 else ""
                if word in STATEMENT_WORDS and rest:
                    kind = "sql" if word == "query" else word
                    items.append(
                        Item(kind, word, number, rest.rstrip().rstrip(";").rstrip(),
                             state["delimiter"], snapshot(state))
                    )
                else:
                    items.append(
                        Item("command", word, number, rest, state["delimiter"], snapshot(state))
                    )
                    apply_command(state, word, rest)
                continue
            if BLOCK_START.match(line) or BLOCK_OTHER.match(line):
                items.append(Item("block", "", number, line, state["delimiter"], snapshot(state)))
                state["depth"] += line.count("{") - line.count("}")
                continue
            buffer_line = number
        buffer += raw + "\n"
        while True:
            delimiter = state["delimiter"]
            parts, rest_start = split_at_delimiter(buffer, delimiter)
            changed = False
            consumed = 0
            for start, end in parts:
                piece = strip_leading_comments(buffer[start:end])
                consumed = end + len(delimiter)
                if not piece.strip():
                    continue
                item_line = buffer_line + buffer[: end - len(piece)].count("\n")
                words = piece.split(None, 1)
                word = words[0].lower()
                rest = words[1] if len(words) > 1 else ""
                if word in STATEMENT_WORDS and rest:
                    kind, item_text = ("sql" if word == "query" else word), rest
                elif word in COMMANDS or word.startswith("$") or word in ("{", "}"):
                    kind, item_text = "command", rest.strip()
                else:
                    kind, item_text = "sql", piece
                items.append(Item(kind, word, item_line, item_text, delimiter, snapshot(state)))
                if kind == "command":
                    apply_command(state, word, rest)
                    if state["delimiter"] != delimiter:
                        changed = True
                        break
            if changed:
                buffer_line += buffer[:consumed].count("\n")
                buffer = buffer[consumed:]
                continue
            buffer_line += buffer[:rest_start].count("\n")
            buffer = buffer[rest_start:]
            break
    return items


def statement_echo(runner, item):
    return runner.collapse_whitespace((item.text + item.delimiter).encode("utf-8"))


def is_statement(item):
    return item.kind in ("sql", "eval", "send")


def predict_next_output(runner, items, index):
    pending_error = False
    for item in items[index + 1 :]:
        result_format = state_value(item, "result_format")
        if item.kind == "blank":
            if result_format not in ("1", "3"):
                raise Unresolved(
                    "a blank line follows under --result_format {}".format(result_format)
                )
            continue
        if item.kind == "comment":
            if result_format != "1" and item.text.startswith("##"):
                raise Unresolved(
                    "a ## comment follows under --result_format {}".format(result_format)
                )
            continue
        if item.kind == "block":
            raise Unresolved("an if or while block follows (test line {})".format(item.line))
        if is_statement(item):
            if not state_value(item, "query_log"):
                raise Unresolved(
                    "a statement run without the query log follows (test line {})".format(
                        item.line
                    )
                )
            if state_value(item, "explain_protocol") != "0":
                raise Unresolved("the next statement runs under --explain_protocol")
            if item.kind == "eval" and ("$" in item.text or "\\" in item.text):
                raise Unresolved(
                    "an eval with variables follows (test line {})".format(item.line)
                )
            return "echo", statement_echo(runner, item)
        word = item.word
        if word == "echo":
            if "$" in item.text or "\\" in item.text:
                raise Unresolved(
                    "an echo with variables follows (test line {})".format(item.line)
                )
            return "line", runner.collapse_whitespace(item.text.encode("utf-8"))
        if word in ("result_format", "explain_protocol"):
            return "line", "{}: {}".format(word, item.text.rstrip(";").strip()).encode(
                "utf-8"
            )
        if word == "connect" and pending_error:
            raise Unresolved(
                "a connect under --error follows (test line {})".format(item.line)
            )
        if word == "error":
            pending_error = True
        if word in ECHO_REWRITING_COMMANDS:
            raise Unresolved(
                "a {} follows, which can rewrite the next statement's echo (test "
                "line {})".format(word, item.line)
            )
        if word in SILENT_COMMANDS or word in STATE_COMMANDS:
            continue
        raise Unresolved(
            "the mysqltest command {} follows (test line {})".format(word, item.line)
        )
    return "eof", None


def output_line_starts(runner, items):
    starts = set()
    for item in items:
        if is_statement(item):
            text = (item.text + item.delimiter).split("\n", 1)[0]
        elif item.kind == "command" and item.word == "echo":
            text = item.text
        else:
            continue
        line = runner.collapse_whitespace(text.encode("utf-8"))
        if line:
            starts.add(line)
    return starts


def terminator_at(runner, lines, line_count, position, prediction):
    kind, value = prediction
    if kind == "eof":
        return position == line_count
    if position >= line_count:
        return False
    if kind == "line":
        return runner.collapse_whitespace(lines[position]) == value
    return runner.statement_echo_end(lines, line_count, position, value) is not None


def plain_rows(runner, lines, line_count, start, prediction):
    for position in range(start, line_count + 1):
        if terminator_at(runner, lines, line_count, position, prediction):
            return position - start
        if position == line_count:
            break
        line = lines[position]
        if line == b"Warnings:" or line.startswith(INFO_PREFIXES):
            after = position + 1 if line == b"Warnings:" else position
            while after < line_count and WARNING_LINE.match(lines[after]):
                after += 1
            while after < line_count and lines[after].startswith(INFO_PREFIXES):
                after += 1
            if not terminator_at(runner, lines, line_count, after, prediction):
                raise Unresolved(
                    "the warnings or info after the rows are not followed by the next "
                    "output the .test predicts"
                )
            return position - start
    raise Unresolved("the next output the .test predicts is not in the result")


def resolve(runner, candidate, items, content):
    matches = [
        index
        for index, item in enumerate(items)
        if is_statement(item)
        and item.line == candidate.line
        and " ".join(item.text.split()) == candidate.text
    ]
    if len(matches) != 1:
        raise Unresolved("this reader of the .test does not find the statement at its line")
    index = matches[0]
    item = items[index]
    if item.kind == "send":
        raise Unresolved("sent with send, so its result comes at reap")
    if item.kind == "eval" and ("$" in item.text or "\\" in item.text):
        raise Unresolved("an eval with variables, whose echo holds their values")
    if item.delimiter != ";" and split_at_delimiter(item.text, ";")[0]:
        raise Unresolved("more than one statement under delimiter {}".format(item.delimiter))
    for key, bad, reason in (
        ("result_log", False, "run with --disable_result_log"),
        ("query_log", False, "run without the query log"),
        ("vertical", True, "printed with --vertical_results"),
        ("metadata", True, "printed with --enable_metadata"),
        ("column_names", False, "printed with --disable_column_names"),
        ("sorted", True, "sorted by --enable_sorted_result"),
    ):
        if state_value(item, key) == bad:
            raise Unresolved(reason)
    if state_value(item, "depth") != 0:
        raise Unresolved("inside an if or while block")
    echo = statement_echo(runner, item)
    twins = [
        other
        for other in items
        if is_statement(other)
        and state_value(other, "query_log")
        and statement_echo(runner, other) == echo
    ]
    if any(state_value(other, "depth") != 0 for other in twins):
        raise Unresolved("the same statement also runs inside an if or while block")
    occurrence = twins.index(item) + 1
    lines, line_count = runner.split_content_lines(content)
    echoes = runner.find_statement_echoes(lines, line_count, echo)
    if len(echoes) != len(twins):
        raise Unresolved(
            "the .test runs this statement {} times with the query log on, the result "
            "echoes it {} times".format(len(twins), len(echoes))
        )
    header = echoes[occurrence - 1][1] + 1
    if header >= line_count:
        raise Unresolved("nothing follows the echo in the result")
    if lines[header].startswith(b"ERROR "):
        raise Unresolved("the reference returned an error")
    size = runner.result_header_size(lines, line_count, header)
    start = header + size
    if size == 3:
        rows = 0
        while start + rows < line_count and lines[start + rows].startswith(b"|"):
            rows += 1
        if start + rows >= line_count or lines[start + rows] != lines[header]:
            raise Unresolved("the boxed result has no closing border")
    else:
        if runner.BOX_BORDER_PATTERN.match(lines[header]):
            raise Unresolved("a border line follows the echo but the box is incomplete")
        rows = plain_rows(runner, lines, line_count, start, predict_next_output(runner, items, index))
        columns = lines[header].count(b"\t")
        if any(line.count(b"\t") != columns for line in lines[start : start + rows]):
            raise Unresolved("a row does not have the header's number of columns")
        starts = output_line_starts(runner, items)
        if any(runner.collapse_whitespace(line) in starts for line in lines[start : start + rows]):
            raise Unresolved(
                "a row reads like the first line of a statement's echo or an --echo "
                "line, so the rows may run past the result"
            )
    end = start + rows
    next_digest = (
        runner.HASH_ORDER_END_OF_FILE if end == line_count else runner.short_digest(lines[end])
    )
    return {
        "case": candidate.case,
        "occurrence": str(occurrence),
        "statement_sha256": runner.short_digest(echo),
        "rows": str(rows),
        "next_sha256": next_digest,
        "test_line": str(candidate.line),
        "hash_operators": "",
        "statement": echo.decode("utf-8"),
    }


def list_line(runner, row):
    return "\t".join(row[column] for column in runner.HASH_ORDER_COLUMNS)


def reference_contents(cases, result_dir, case_name):
    if result_dir is None:
        path = cases[case_name].result_file
    else:
        path = result_dir / (case_name + ".result")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Unresolved("no reference output: {}".format(exc))


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--candidates", default=str(CANDIDATES), help="candidate list")
    parser.add_argument(
        "--result-dir",
        help="reference output as DIR/<case>.result (a recording); defaults to the "
        "checked-in .result files",
    )
    parser.add_argument(
        "--runner",
        default=str(LIVE_RUNNER),
        help="the runner whose compare applies the mask; defaults to the live runner",
    )
    parser.add_argument("--out", help="write the list here instead of to stdout")
    args = parser.parse_args(argv)
    runner = load_runner(Path(args.runner).resolve())
    candidates_path = Path(args.candidates).resolve()
    result_dir = Path(args.result_dir).resolve() if args.result_dir else None
    cases = dict((case.name, case) for case in runner.discover_cases(REPO_ROOT))
    candidates = read_candidates(candidates_path)
    rows = []
    unresolved = []
    items_by_case = {}
    for candidate in candidates:
        try:
            if candidate.case not in cases:
                raise Unresolved("not a configured case")
            if candidate.case not in items_by_case:
                items_by_case[candidate.case] = read_test_items(cases[candidate.case].test_file)
            content = reference_contents(cases, result_dir, candidate.case)
            row = resolve(runner, candidate, items_by_case[candidate.case], content)
            confirmed = dict(row, hash_operators=runner.HASH_OPERATOR_WORD)
            runner.parse_hash_order_line(
                "helper output", len(rows) + 1, list_line(runner, confirmed)
            )
            rows.append(row)
        except Unresolved as exc:
            unresolved.append((candidate, str(exc)))
    source = (
        "the checked-in .result files"
        if result_dir is None
        else "{} (<case>.result)".format(result_dir)
    )
    lines = [
        "# Row-order mask list, draft: every candidate that {} could resolve.".format(
            HELPER_PATH.relative_to(REPO_ROOT)
        ),
        "#   The confirmation step keeps only the statements whose order comes from hash output and whose",
        "#   select count(*) on the reference equals rows, fills in hash_operators (left empty here, which",
        "#   compare refuses) from the reference's plan, writes the result to",
        "#   migration/judge/lists/hash-order-selects.txt and pins its sha256 in the runner",
        "#   (HASH_ORDER_LIST_SHA256).",
        "# Candidates: {} (sha256 {}).".format(
            candidates_path.relative_to(REPO_ROOT)
            if REPO_ROOT in candidates_path.parents
            else candidates_path,
            file_sha256(candidates_path),
        ),
        "# Reference output: {}.".format(source),
        "# Columns (tab-separated): {}.".format(", ".join(runner.HASH_ORDER_COLUMNS)),
        "# Resolved {} of {} candidates; the {} others are listed as '# unresolved' lines.".format(
            len(rows), len(candidates), len(unresolved)
        ),
    ]
    lines.extend(
        "# unresolved\t{}\t{}\t{}".format(candidate.case, candidate.line, reason)
        for candidate, reason in unresolved
    )
    lines.extend(list_line(runner, row) for row in rows)
    output = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    print(
        "resolved {} of {} candidates, {} unresolved".format(
            len(rows), len(candidates), len(unresolved)
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
