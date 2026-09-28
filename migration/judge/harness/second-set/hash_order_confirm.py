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
import json
from pathlib import Path
import re
import sys


TOOL_PATH = Path(__file__).resolve()
REPO_ROOT = TOOL_PATH.parents[4]
HELPER_PATH = TOOL_PATH.with_name("hash_order_list.py")
LIVE_RUNNER = REPO_ROOT / ".github" / "script" / "seekdb" / "mysqltest_for_seekdb.py"
CANDIDATES = (
    REPO_ROOT / "migration" / "judge" / "lists" / "hash-order-select-candidates.txt"
)
DESCRIPTION = (
    "Confirm the row-order mask's list against the reference. prepare writes a "
    "copy of every configured case that has a candidate, with the candidate's "
    "EXPLAIN and its select count(*) printed between two --echo markers right "
    "before it and select found_rows() between two more right after it, for a "
    "runner --test-dir recording; confirm reads that recording, checks that each "
    "case's output without the markers is its reference output, and keeps the "
    "draft lines whose plan orders the rows by a hash operator."
)
MARKER = "hash_order_confirm"
BLOCK_LINE = re.compile(rb"hash_order_confirm (begin|found|end) ([0-9]+)\Z")
COUNT_ALIAS = "hash_order_confirm_rows"
FOUND_ROWS_ALIAS = "hash_order_confirm_found_rows"
ONE_SHOT_COMMANDS = frozenset(
    (
        "error",
        "replace_column",
        "replace_result",
        "replace_regex",
        "replace_numeric_round",
        "sorted_result",
        "partially_sorted_result",
        "lowercase_result",
    )
)
PASS_OVER_COMMANDS = ONE_SHOT_COMMANDS | frozenset(
    (
        "echo",
        "enable_warnings",
        "disable_warnings",
        "enable_info",
        "disable_info",
        "enable_query_log",
        "disable_query_log",
        "enable_result_log",
        "disable_result_log",
        "enable_metadata",
        "disable_metadata",
        "horizontal_results",
        "vertical_results",
        "enable_column_names",
        "disable_column_names",
        "enable_sorted_result",
        "disable_sorted_result",
        "result_format",
        "explain_protocol",
        "enable_abort_on_error",
        "disable_abort_on_error",
    )
)
STOP_OPERATORS = frozenset(
    (
        "SORT",
        "TOP-N SORT",
        "PARTITION SORT",
        "PARTITION TOP-N SORT",
        "PX COORDINATOR MERGE SORT",
        "EXCHANGE IN MERGE SORT DISTR",
        "MERGE GROUP BY",
        "SCALAR GROUP BY",
        "MERGE DISTINCT",
        "MERGE UNION DISTINCT",
        "MERGE INTERSECT DISTINCT",
        "MERGE EXCEPT DISTINCT",
        "INSERT",
        "DISTRIBUTED INSERT",
        "UPDATE",
        "DISTRIBUTED UPDATE",
        "DELETE",
        "DISTRIBUTED DELETE",
    )
)
FIRST_CHILD_OPERATORS = frozenset(("SUBPLAN FILTER",))
LAST_CHILD_OPERATORS = frozenset(("TEMP TABLE TRANSFORMATION",))
ALL_CHILDREN_OPERATORS = frozenset(
    (
        "UNION ALL",
        "LIMIT",
        "MATERIAL",
        "SUBPLAN SCAN",
        "PX COORDINATOR",
        "EXCHANGE OUT DISTR",
        "EXCHANGE OUT DISTR (PKEY)",
        "EXCHANGE OUT DISTR (HASH)",
        "EXCHANGE OUT DISTR (BROADCAST)",
        "EXCHANGE OUT DISTR (BC2HOST)",
        "EXCHANGE OUT DISTR (RANDOM)",
        "EXCHANGE IN DISTR",
        "PX PARTITION ITERATOR",
        "PX BLOCK ITERATOR",
        "WINDOW FUNCTION",
        "COUNT",
        "STATISTICS COLLECTOR",
        "FOR UPDATE",
        "DISTRIBUTED FOR UPDATE",
        "MONITORING DUMP",
        "TEMP TABLE INSERT",
    )
)
JOIN_PREFIXES = ("NESTED-LOOP ", "MERGE ")
LEAF_PREFIXES = (
    "TABLE ",
    "DISTRIBUTED TABLE ",
    "TEXT RETRIEVAL SCAN",
    "DISTRIBUTED TEXT RETRIEVAL SCAN",
    "VECTOR INDEX ",
    "EXPRESSION",
    "VALUES TABLE ACCESS",
    "FUNCTION TABLE",
    "JSON TABLE",
)
HASH_PREFIX = "HASH "
TREE_CHARACTERS = " │├└─"
DOP_PATTERN = re.compile(r"\bdop=([0-9]+)")
ERROR_LINE = re.compile(rb"ERROR [0-9A-Z]+( \([0-9A-Z]+\))?: ")
LIST_COLUMNS = (
    "case",
    "occurrence",
    "statement_sha256",
    "rows",
    "next_sha256",
    "test_line",
    "hash_operators",
    "statement",
)
DECISION_COLUMNS = (
    "case",
    "test_line",
    "draft",
    "rows",
    "count",
    "found_rows",
    "decision",
    "hash_operators",
    "order_path",
    "reason",
    "statement",
)

Block = namedtuple("Block", ("number", "case", "stem", "test_line", "kind", "statement"))
Node = namedtuple("Node", ("id", "depth", "operator", "name", "children"))


class ConfirmError(Exception):
    pass


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tools(runner_path):
    runner = load_module("mysqltest_for_seekdb", runner_path)
    helper = load_module("hash_order_list", HELPER_PATH)
    if not hasattr(runner, "find_statement_echoes"):
        raise SystemExit(
            "{} has no row-order mask; pass --runner".format(runner_path)
        )
    return runner, helper


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stem_for(case_name):
    return case_name.replace(".", "__")


def line_offsets(text):
    return [0] + [match.end() for match in re.finditer("\n", text)]


def line_bounds(text, offsets, number):
    start = offsets[number - 1]
    end = offsets[number] if number < len(offsets) else len(text)
    return start, end


def statement_bounds(text, offsets, item, statement_words):
    start, end = line_bounds(text, offsets, item.line)
    line = text[start:end]
    if line.lstrip().startswith("--"):
        return start, end
    position = text.find(item.text, start)
    if position < start or position >= end:
        raise ConfirmError(
            "test line {}: the statement text does not start on its line".format(
                item.line
            )
        )
    if text.find(item.text, position + 1, end) >= 0:
        raise ConfirmError(
            "test line {}: the statement text starts twice on its line".format(item.line)
        )
    after = position + len(item.text)
    if not text.startswith(item.delimiter, after):
        raise ConfirmError(
            "test line {}: the delimiter {!r} does not follow the statement".format(
                item.line, item.delimiter
            )
        )
    finish = after + len(item.delimiter)
    if item.word in statement_words and item.kind != "sql" or (
        item.kind == "sql" and item.word == "query"
    ):
        word_end = position
        while word_end > start and text[word_end - 1].isspace():
            word_end -= 1
        word_start = word_end - len(item.word)
        if word_start < start or text[word_start:word_end].lower() != item.word:
            raise ConfirmError(
                "test line {}: no {} before the statement".format(item.line, item.word)
            )
        return word_start, finish
    return position, finish


def whole_line_command(text, offsets, item):
    start, end = line_bounds(text, offsets, item.line)
    stripped = text[start:end].strip()
    if stripped.startswith("--"):
        return start, end
    if stripped.lower().startswith(item.word) and stripped.endswith(item.delimiter):
        return start, end
    raise ConfirmError(
        "test line {}: the {} command shares its line with something else".format(
            item.line, item.word
        )
    )


def wrapped_lines(opening, state, abort_on_error, body):
    explain_protocol = state["explain_protocol"]
    result_log = state["result_log"]
    lines = [opening]
    if explain_protocol != "0":
        lines.append("--explain_protocol 0")
    if abort_on_error:
        lines.append("--disable_abort_on_error")
    if not result_log:
        lines.append("--enable_result_log")
    lines.extend(body)
    if not result_log:
        lines.append("--disable_result_log")
    if abort_on_error:
        lines.append("--enable_abort_on_error")
    if explain_protocol != "0":
        lines.append("--explain_protocol {}".format(explain_protocol))
    return lines


def block_text(number, item, statement, state, abort_on_error, needs_newline):
    delimiter = item.delimiter
    prefix = "eval " if item.kind == "eval" else ""
    lines = wrapped_lines(
        "--echo {} begin {}".format(MARKER, number),
        state,
        abort_on_error,
        [
            "{}explain {}{}".format(prefix, statement, delimiter),
            "{}select count(*) from ({}) {}{}".format(
                prefix, statement, COUNT_ALIAS, delimiter
            ),
        ],
    )
    lines.append("--echo {} end {}".format(MARKER, number))
    return ("\n" if needs_newline else "") + "\n".join(lines) + "\n"


def found_block_text(number, item, state, abort_on_error, needs_newline):
    lines = wrapped_lines(
        "--echo {} found {}".format(MARKER, number),
        state,
        abort_on_error,
        ["select found_rows() as {}{}".format(FOUND_ROWS_ALIAS, item.delimiter)],
    )
    lines.append("--echo {} end {}".format(MARKER, number))
    return ("\n" if needs_newline else "") + "\n".join(lines) + "\n"


def instrument_case(helper, case, candidates, first_number):
    text = case.test_file.read_bytes().decode("utf-8")
    items = helper.read_test_items(case.test_file)
    offsets = line_offsets(text)
    edits = []
    blocks = []
    skipped = []
    abort_states = []
    abort_on_error = True
    for item in items:
        abort_states.append(abort_on_error)
        if item.kind == "command" and item.word == "disable_abort_on_error":
            abort_on_error = False
        elif item.kind == "command" and item.word == "enable_abort_on_error":
            abort_on_error = True
    number = first_number
    for candidate in candidates:
        matches = [
            index
            for index, item in enumerate(items)
            if helper.is_statement(item)
            and item.line == candidate.line
            and " ".join(item.text.split()) == candidate.text
        ]
        if len(matches) != 1:
            skipped.append((candidate, "the .test reader does not find it at its line"))
            continue
        index = matches[0]
        item = items[index]
        position, finish = statement_bounds(text, offsets, item, helper.STATEMENT_WORDS)
        moved = []
        for earlier in range(index - 1, -1, -1):
            other = items[earlier]
            if other.kind in ("comment", "blank"):
                continue
            if other.kind != "command" or other.word not in PASS_OVER_COMMANDS:
                break
            if other.word in ONE_SHOT_COMMANDS:
                moved.append(whole_line_command(text, offsets, other))
        moved.reverse()
        line_start = line_bounds(text, offsets, item.line)[0]
        needs_newline = bool(text[line_start:position].strip())
        statement = item.text
        state = dict(item.state)
        insertion = block_text(
            number, item, statement, state, abort_states[index], needs_newline
        ) + "".join(text[start:end] for start, end in moved)
        for start, end in moved:
            edits.append((start, end, len(edits), ""))
        edits.append((position, position, len(edits), insertion))
        if item.kind != "send":
            following = next(
                (
                    other
                    for other in items[index + 1 :]
                    if other.kind not in ("comment", "blank")
                ),
                None,
            )
            line_end = text.find("\n", finish)
            same_line = line_end >= 0 and text[finish:line_end].strip()
            if same_line or (line_end < 0 and text[finish:].strip()):
                after, after_newline = finish, True
            elif following is not None:
                after, after_newline = offsets[following.line - 1], False
            else:
                after = len(text)
                after_newline = not text.endswith("\n")
            edits.append(
                (
                    after,
                    after,
                    len(edits),
                    found_block_text(
                        number, item, state, abort_states[index], after_newline
                    ),
                )
            )
        blocks.append(
            Block(
                number,
                candidate.case,
                stem_for(candidate.case),
                candidate.line,
                item.kind,
                candidate.text,
            )
        )
        number += 1
    edits.sort(key=lambda edit: (edit[0], edit[1], edit[2]), reverse=True)
    for (start, end, _, _), (next_start, _, _, _) in zip(edits[1:], edits[:-1]):
        if end > next_start:
            raise ConfirmError("{}: overlapping edits".format(case.name))
    for start, end, _, replacement in edits:
        text = text[:start] + replacement + text[end:]
    return text, blocks, skipped


def read_candidates(helper, path):
    return helper.read_candidates(Path(path))


def command_prepare(args):
    runner, helper = load_tools(Path(args.runner).resolve())
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit("{} exists; prepare writes a new directory".format(out))
    candidates = read_candidates(helper, args.candidates)
    cases = dict((case.name, case) for case in runner.discover_cases(REPO_ROOT))
    by_case = {}
    for candidate in candidates:
        by_case.setdefault(candidate.case, []).append(candidate)
    stems = {}
    for name in by_case:
        stem = stem_for(name)
        if stem in stems:
            raise SystemExit("{} and {} map to one file name".format(stems[stem], name))
        stems[stem] = name
    out.mkdir(parents=True)
    all_blocks = []
    all_skipped = []
    number = 1
    for name in sorted(by_case, key=lambda case_name: stem_for(case_name)):
        if name not in cases:
            all_skipped.extend(
                (candidate, "not a configured case") for candidate in by_case[name]
            )
            continue
        ordered = sorted(by_case[name], key=lambda candidate: candidate.line)
        try:
            text, blocks, skipped = instrument_case(helper, cases[name], ordered, number)
        except (ConfirmError, helper.Unresolved) as exc:
            raise SystemExit("{}: {}".format(name, exc))
        number += len(blocks)
        all_blocks.extend(blocks)
        all_skipped.extend(skipped)
        (out / (stem_for(name) + ".test")).write_bytes(text.encode("utf-8"))
    lines = ["\t".join(("number", "case", "stem", "test_line", "kind", "statement"))]
    lines.extend(
        "\t".join(
            (str(block.number), block.case, block.stem, str(block.test_line), block.kind, block.statement)
        )
        for block in all_blocks
    )
    (out / "blocks.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    skipped_lines = ["\t".join(("case", "test_line", "reason"))]
    skipped_lines.extend(
        "\t".join((candidate.case, str(candidate.line), reason))
        for candidate, reason in all_skipped
    )
    (out / "skipped.tsv").write_text("\n".join(skipped_lines) + "\n", encoding="utf-8")
    (out / "prepare.json").write_text(
        json.dumps(
            {
                "argv": args.argv,
                "candidates": shown_path(args.candidates),
                "candidates_sha256": file_sha256(args.candidates),
                "helper_sha256": file_sha256(HELPER_PATH),
                "tool_sha256": file_sha256(TOOL_PATH),
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    case_list = sorted(set(block.stem for block in all_blocks))
    (out.parent / (out.name + ".cases")).write_text(
        "\n".join(case_list) + "\n", encoding="utf-8"
    )
    print(
        "wrote {} cases with {} blocks to {}; {} candidates skipped; case list {}".format(
            len(case_list),
            len(all_blocks),
            out,
            len(all_skipped),
            out.parent / (out.name + ".cases"),
        ),
        file=sys.stderr,
    )
    return 0


def read_blocks(path):
    blocks = []
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    for raw in lines[1:]:
        if not raw:
            continue
        number, case, stem, test_line, kind, statement = raw.split("\t")
        blocks.append(Block(int(number), case, stem, int(test_line), kind, statement))
    return blocks


def split_blocks(content):
    lines = content.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    kept = []
    blocks = {b"begin": {}, b"found": {}}
    current = None
    problems = []
    for line in lines:
        match = BLOCK_LINE.match(line)
        if match is not None:
            kind, number = match.group(1), int(match.group(2))
            if kind in blocks:
                if current is not None:
                    problems.append(
                        "block {} begins inside block {}".format(number, current[1])
                    )
                if number in blocks[kind]:
                    problems.append("block {} {} appears twice".format(kind.decode(), number))
                current = (kind, number)
                blocks[kind][number] = []
            else:
                if current is None or current[1] != number:
                    problems.append("block {} ends outside itself".format(number))
                current = None
            continue
        if current is None:
            kept.append(line)
        else:
            blocks[current[0]][current[1]].append(line)
    if current is not None:
        problems.append("block {} does not end".format(current[1]))
    return kept, blocks[b"begin"], blocks[b"found"], problems


def plan_cells(line):
    parts = line.split("|")
    if len(parts) < 4 or parts[0] != "" or parts[-1] != "":
        return None
    return parts[1:-1]


def parse_plan(lines):
    text_lines = [line.decode("utf-8", "replace") for line in lines]
    header = None
    for index, line in enumerate(text_lines):
        cells = plan_cells(line)
        if cells and [cell.strip() for cell in cells[:3]] == ["ID", "OPERATOR", "NAME"]:
            header = index
            break
    if header is None:
        return None, None, "no plan table"
    width = len(text_lines[header])
    if header == 0 or text_lines[header - 1] != "=" * width:
        return None, None, "the plan table has no opening frame"
    columns = len(plan_cells(text_lines[header]))
    rows = []
    index = header + 2
    while index < len(text_lines) and text_lines[index].startswith("|"):
        cells = plan_cells(text_lines[index])
        if cells is None or len(cells) < columns:
            return None, None, "a plan row does not have the header's columns"
        name_cells = cells[2 : len(cells) - (columns - 3)]
        rows.append((cells[0].strip(), cells[1], "|".join(name_cells).strip()))
        index += 1
    if index >= len(text_lines) or text_lines[index] != "=" * width:
        return None, None, "the plan table has no closing frame"
    end = index + 1
    outputs = {}
    if end < len(text_lines) and text_lines[end] == "Outputs & filters:":
        index = end + 2
        current = None
        while index < len(text_lines) and text_lines[index].startswith(" "):
            match = re.match(r"\s+([0-9]+) - ", text_lines[index])
            if match:
                current = match.group(1)
                outputs[current] = []
            if current is not None:
                outputs[current].append(text_lines[index])
            index += 1
        end = index
    nodes = []
    for row_id, operator_cell, name in rows:
        operator = operator_cell.rstrip()
        stripped = operator.lstrip(TREE_CHARACTERS)
        depth = (len(operator) - len(stripped)) // 2
        nodes.append(Node(row_id, depth, stripped, name, []))
    stack = []
    for node in nodes:
        while stack and stack[-1].depth >= node.depth:
            stack.pop()
        if stack:
            if node.depth != stack[-1].depth + 1:
                return None, None, "the plan tree skips a level at ID {}".format(node.id)
            stack[-1].children.append(node)
        elif node is not nodes[0]:
            return None, None, "the plan has more than one root"
        stack.append(node)
    return {"nodes": nodes, "outputs": outputs}, end, None


def operator_kind(operator):
    if operator.startswith(HASH_PREFIX):
        return "hash"
    if operator in STOP_OPERATORS:
        return "stop"
    if operator in FIRST_CHILD_OPERATORS:
        return "first"
    if operator in LAST_CHILD_OPERATORS:
        return "last"
    if operator in ALL_CHILDREN_OPERATORS:
        return "all"
    if operator.startswith(JOIN_PREFIXES) and "JOIN" in operator:
        return "all"
    if operator.startswith(LEAF_PREFIXES) or operator == "TEMP TABLE ACCESS":
        return "leaf"
    return None


def order_path(plan):
    nodes = plan["nodes"]
    inserts = dict(
        (node.name, node) for node in nodes if node.operator == "TEMP TABLE INSERT"
    )
    hash_nodes = []
    path = []
    unknown = []

    def visit(node):
        kind = operator_kind(node.operator)
        path.append("{}:{}".format(node.id, node.operator))
        if kind is None:
            unknown.append(node.operator)
            return
        if kind == "stop":
            return
        if kind == "hash":
            hash_nodes.append(node)
            children = node.children
        elif kind == "first":
            children = node.children[:1]
        elif kind == "last":
            children = node.children[-1:]
        elif kind == "leaf":
            children = []
            if node.operator == "TEMP TABLE ACCESS":
                source = inserts.get(node.name)
                if source is None:
                    unknown.append("TEMP TABLE ACCESS without its insert")
                else:
                    children = [source]
            elif node.children:
                unknown.append("{} with children".format(node.operator))
        else:
            children = node.children
        for child in children:
            visit(child)

    visit(nodes[0])
    names = []
    for node in sorted(hash_nodes, key=lambda node: int(node.id)):
        if node.operator not in names:
            names.append(node.operator)
    dops = [
        int(value)
        for lines in plan["outputs"].values()
        for line in lines
        for value in DOP_PATTERN.findall(line)
    ]
    return names, path, unknown, max(dops) if dops else None


def parse_value(lines, column):
    name = column.encode("utf-8")
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if line == name and index + 1 < len(lines):
            value = lines[index + 1]
            if re.match(rb"[0-9]+\Z", value):
                return int(value), None
        if (
            re.match(rb"\| " + re.escape(name) + rb" *\|\Z", line)
            and index + 2 < len(lines)
        ):
            match = re.match(rb"\| *([0-9]+) *\|\Z", lines[index + 2])
            if match:
                return int(match.group(1)), None
    return None, "no {} result".format(column)


def error_lines(lines):
    return "; ".join(
        line.decode("utf-8", "replace") for line in lines if ERROR_LINE.match(line)
    )


def parse_count(lines):
    count, problem = parse_value(lines, "count(*)")
    errors = error_lines(lines)
    if count is None and errors:
        problem = "{}: {}".format(problem, errors)
    return count, problem


def parse_found_rows(lines):
    if lines is None:
        return None, "no found_rows() block"
    found, problem = parse_value(lines, FOUND_ROWS_ALIAS)
    errors = error_lines(lines)
    if found is None and errors:
        problem = "{}: {}".format(problem, errors)
    return found, problem


def read_output(recording, stem):
    for suffix in (".result", ".partial"):
        path = recording / (stem + suffix)
        if path.is_file():
            return path.read_bytes(), suffix
    return None, None


def read_draft(path):
    rows = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").split("\n"), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) != len(LIST_COLUMNS):
            raise SystemExit("{}:{}: not a list line".format(path, number))
        rows.append(dict(zip(LIST_COLUMNS, fields)))
    return rows


def shown_path(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def read_recorded_cases(blocks, recording, cases):
    reproduced = {}
    parsed = {}
    for stem in sorted(set(block.stem for block in blocks)):
        case_name = next(block.case for block in blocks if block.stem == stem)
        content, suffix = read_output(recording, stem)
        if content is None:
            reproduced[case_name] = "no output recorded"
            continue
        kept, found, found_rows_blocks, problems = split_blocks(content)
        reference = cases[case_name].result_file.read_bytes().split(b"\n")
        if reference and reference[-1] == b"":
            reference = reference[:-1]
        if problems:
            reproduced[case_name] = "; ".join(problems)
        elif suffix != ".result":
            reproduced[case_name] = "the case failed; only its partial output was recorded"
        elif kept != reference:
            first = next(
                (
                    index
                    for index, (left, right) in enumerate(zip(kept, reference))
                    if left != right
                ),
                min(len(kept), len(reference)),
            )
            reproduced[case_name] = (
                "the output without the blocks differs from the checked-in .result at "
                "its line {}".format(first + 1)
            )
        else:
            reproduced[case_name] = None
        for number, lines in found.items():
            plan, end, problem = parse_plan(lines)
            count, count_problem = parse_count(lines[end:] if end is not None else lines)
            found_rows, found_problem = parse_found_rows(found_rows_blocks.get(number))
            parsed[number] = {
                "plan": plan,
                "plan_problem": problem,
                "count": count,
                "count_problem": count_problem,
                "found_rows": found_rows,
                "found_rows_problem": found_problem,
            }
    return reproduced, parsed


def show_number(value):
    return "" if value is None else str(value)


def decide(row, block, data, state, decision, unknown_operators):
    if block is None:
        return "not-instrumented", "the prepare step did not instrument it"
    if data is None:
        return "no-block", "no block in the recording ({})".format(state)
    if state is not None:
        return "not-reproduced", "reference output not reproduced: {}".format(state)
    if data["plan"] is None:
        return "no-plan", "no plan: {}".format(data["plan_problem"])
    names, path, unknown, dop = order_path(data["plan"])
    decision["order_path"] = " > ".join(path)
    decision["count"] = show_number(data["count"])
    decision["found_rows"] = show_number(data["found_rows"])
    if unknown:
        unknown_operators.update(unknown)
        return "unknown-operator", "operators the tool does not know: {}".format(
            ", ".join(unknown)
        )
    decision["hash_operators"] = ", ".join(names)
    rows = int(row["rows"])
    if dop is not None and dop > 1:
        return "parallel", "the plan runs with dop={}".format(dop)
    if not names:
        return "no-hash-order", "no hash operator on the path that sets the output order"
    if rows < 2:
        return "under-two-rows", "{} row(s): there is no order to mask".format(rows)
    found = data["found_rows"]
    count = data["count"]
    if found is None:
        return "count-failed", "found_rows() gave no count ({})".format(
            data["found_rows_problem"]
        )
    if found != rows:
        return "count-differs", "found_rows() gave {}; the listed rows are {} lines".format(
            found, rows
        )
    if count is not None and count != found:
        return "count-differs", "select count(*) gave {}, found_rows() {}".format(
            count, found
        )
    if count is None:
        return "keep", "found_rows() gave the row count; select count(*) failed ({})".format(
            data["count_problem"]
        )
    return "keep", "found_rows() and select count(*) gave the row count"


def command_confirm(args):
    runner_path = Path(args.runner).resolve()
    runner, helper = load_tools(runner_path)
    tests = Path(args.tests).resolve()
    recording = Path(args.recording).resolve()
    draft_path = Path(args.draft_out).resolve()
    if helper.main(["--runner", str(runner_path), "--out", str(draft_path)]) != 0:
        raise SystemExit("the helper failed")
    blocks = read_blocks(tests / "blocks.tsv")
    prepared = json.loads((tests / "prepare.json").read_text(encoding="utf-8"))
    cases = dict((case.name, case) for case in runner.discover_cases(REPO_ROOT))
    manifest = json.loads((recording / "manifest.json").read_text(encoding="utf-8"))
    if Path(manifest.get("test_dir") or "").resolve() != tests:
        raise SystemExit("{} is not a recording of {}".format(recording, tests))
    reproduced, parsed = read_recorded_cases(blocks, recording, cases)
    by_key = dict(((block.case, block.test_line), block) for block in blocks)
    draft = read_draft(draft_path)
    decisions = []
    kept_rows = []
    unknown_operators = set()
    for row in draft:
        block = by_key.get((row["case"], int(row["test_line"])))
        decision = dict(
            (column, "")
            for column in DECISION_COLUMNS
        )
        decision.update(
            case=row["case"],
            test_line=row["test_line"],
            draft="yes",
            rows=row["rows"],
            statement=row["statement"],
        )
        data = parsed.get(block.number) if block is not None else None
        code, reason = decide(
            row,
            block,
            data,
            reproduced.get(row["case"], "no output recorded"),
            decision,
            unknown_operators,
        )
        decision["decision"] = code
        decision["reason"] = reason
        decisions.append(decision)
        if code == "keep":
            kept_rows.append(dict(row, hash_operators=decision["hash_operators"]))
    for number, raw in enumerate(kept_rows, 1):
        runner.parse_hash_order_line(
            "confirmed list", number, "\t".join(raw[column] for column in LIST_COLUMNS)
        )
    draft_keys = set((row["case"], int(row["test_line"])) for row in draft)
    for block in blocks:
        if (block.case, block.test_line) in draft_keys:
            continue
        data = parsed.get(block.number)
        extra = dict((column, "") for column in DECISION_COLUMNS)
        extra.update(
            case=block.case,
            test_line=str(block.test_line),
            draft="no",
            decision="not-in-draft",
            statement=block.statement,
        )
        if data is None:
            extra["reason"] = "the helper does not place it; no block in the recording"
        elif data["plan"] is None:
            extra["reason"] = "the helper does not place it; no plan: {}".format(
                data["plan_problem"]
            )
        else:
            names, path, unknown, dop = order_path(data["plan"])
            unknown_operators.update(unknown)
            extra["hash_operators"] = ", ".join(names)
            extra["order_path"] = " > ".join(path)
            extra["count"] = show_number(data["count"])
            extra["found_rows"] = show_number(data["found_rows"])
            extra["reason"] = "the helper does not place it{}".format(
                "; dop={}".format(dop) if dop is not None and dop > 1 else ""
            )
        decisions.append(extra)
    codes = {}
    for decision in decisions:
        if decision["draft"] == "yes":
            codes[decision["decision"]] = codes.get(decision["decision"], 0) + 1
    command = ["python3", "-B", shown_path(TOOL_PATH)] + list(args.argv)
    header = [
        "# Row-order mask list (compare --mask row-order): the SELECTs whose rows the reference returns in an order that",
        "#   a hash operator decides, confirmed against the reference's plan in each statement's place in its case.",
        "# Produced by: {}".format(" ".join(command)),
        "#   which ran the helper {} (sha256 {}) over {} (sha256 {})".format(
            shown_path(HELPER_PATH),
            file_sha256(HELPER_PATH),
            shown_path(CANDIDATES),
            file_sha256(CANDIDATES),
        ),
        "#   against the checked-in .result files, writing the draft {} (sha256 {}),".format(
            shown_path(draft_path), file_sha256(draft_path)
        ),
        "#   and read the recording {} of the instrumented copies in {},".format(
            shown_path(recording), shown_path(tests)
        ),
        "#   which {} made (the tool's sha256 then {}, now {}).".format(
            " ".join(["python3", "-B", shown_path(TOOL_PATH)] + prepared["argv"]),
            prepared["tool_sha256"],
            file_sha256(TOOL_PATH),
        ),
        "# The recording: runner {} run --test-dir {} --case-list {} --record-dir {} --max-retries 0".format(
            shown_path(runner_path),
            shown_path(tests),
            manifest.get("case_list"),
            shown_path(recording),
        ),
        "#   --no-ignore-trailing-whitespace, seekdb {} (sha256 {}), init {} and {}.".format(
            manifest.get("seekdb"),
            manifest.get("seekdb_sha256"),
            shown_path(manifest.get("init_sql")),
            shown_path(manifest.get("init_user_sql")),
        ),
        "# Each case's copy runs the candidate's EXPLAIN and select count(*) from (<candidate>) right before it and",
        "#   select found_rows() right after it. A draft line is kept when: its case's recorded output without those",
        "#   blocks is the checked-in .result byte for byte; a HASH operator lies on the plan's path that sets the",
        "#   output order (from the root through each operator that passes its input's order on, into every join input,",
        "#   the first input of SUBPLAN FILTER and the last of TEMP TABLE TRANSFORMATION, and stopping at SORT, TOP-N SORT,",
        "#   the MERGE ... DISTINCT and MERGE GROUP BY operators, SCALAR GROUP BY and merge-sort exchanges); no operator",
        "#   runs with dop above 1; the statement prints two rows or more; found_rows() gives that number; and",
        "#   select count(*), where it runs (it fails on duplicate column names), gives it too.",
        "# Draft lines: {}; decisions: {}.".format(
            len(draft),
            ", ".join("{} {}".format(count, code) for code, count in sorted(codes.items())),
        ),
        "# Per-candidate decisions: {}.".format(shown_path(args.decisions)),
        "# Columns (tab-separated): {}.".format(", ".join(LIST_COLUMNS)),
    ]
    body = ["\t".join(row[column] for column in LIST_COLUMNS) for row in kept_rows]
    Path(args.out).write_text("\n".join(header + body) + "\n", encoding="utf-8")
    decision_lines = ["\t".join(DECISION_COLUMNS)]
    decision_lines.extend(
        "\t".join(decision[column] for column in DECISION_COLUMNS) for decision in decisions
    )
    Path(args.decisions).write_text("\n".join(decision_lines) + "\n", encoding="utf-8")
    summary = {
        "draft_lines": len(draft),
        "kept": len(kept_rows),
        "decisions": codes,
        "not_reproduced": dict(
            (case, state) for case, state in reproduced.items() if state is not None
        ),
        "unknown_operators": sorted(unknown_operators),
        "count_found_rows_mismatches": sorted(
            "{}:{}".format(decision["case"], decision["test_line"])
            for decision in decisions
            if decision["count"] and decision["found_rows"]
            and decision["count"] != decision["found_rows"]
        ),
    }
    print(json.dumps(summary, indent=1, sort_keys=True), file=sys.stderr)
    return 0 if not unknown_operators else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--runner", default=str(LIVE_RUNNER))
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--out", required=True, help="the test directory to write")
    prepare.add_argument("--candidates", default=str(CANDIDATES))
    confirm = commands.add_parser("confirm")
    confirm.add_argument("--tests", required=True, help="the directory prepare wrote")
    confirm.add_argument(
        "--recording", required=True, help="the runner's --record-dir of those tests"
    )
    confirm.add_argument(
        "--draft-out", required=True, help="where the helper's draft is written"
    )
    confirm.add_argument("--out", required=True, help="the confirmed list")
    confirm.add_argument(
        "--decisions", required=True, help="one line per candidate with its decision"
    )
    args = parser.parse_args(argv)
    args.argv = argv
    if args.command == "prepare":
        return command_prepare(args)
    return command_confirm(args)


if __name__ == "__main__":
    sys.exit(main())
