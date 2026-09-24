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
from collections import OrderedDict, namedtuple
from pathlib import Path
import re
import subprocess
import sys


DESCRIPTION = (
    "Generate the judge's family 5 corpus (expressions and casts) as mysqltest "
    "files. 'reach' reads the expression registry from the source and the "
    "coverage profile and writes expressions.tsv; 'cases' reads the source and "
    "expressions.tsv and writes the .test files; 'check-recording' reads a "
    "recording of the corpus and reports failed setup statements. None of them "
    "talks to a server."
)
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
TSV_PATH = HERE / "expressions.tsv"
CASES_DIR = HERE / "cases"
FACTORY_FILE = "src/sql/engine/expr/ob_expr_operator_factory.cpp"
NAME_DEF_FILE = "src/oblib/lib/ob_name_def.h"
PARSER_FILE = "src/sql/parser/sql_parser_mysql_mode.y"
DATUM_CAST_FILE = "src/sql/engine/expr/ob_datum_cast.cpp"
CONSTRUCTOR_DIRS = (
    "src/sql/engine/expr",
    "src/query/api/query/engine/expr",
    "src/sql/resolver/expr",
    "src/sql/engine",
    "src/sql",
    "src/pl",
    "src/share",
    "src/oblib/lib",
)
COVERAGE_ROOT = Path("/Users/colin/seekdb-dev/cov-076eb309b")
DEFAULT_PROFDATA = Path(
    "/Users/colin/seekdb-dev/mysqltest-runs/cov-076eb309b/analysis/AB.profdata"
)
DEFAULT_COVERAGE_BINARY = COVERAGE_ROOT / "build_release" / "src" / "observer" / "seekdb"
DEFAULT_LLVM_BIN = COVERAGE_ROOT / "deps" / "3rd" / "usr" / "local" / "oceanbase" / "devtools" / "bin"
COVERAGE_SOURCE_DIRS = ("src/sql/engine/expr", "src/query/api/query/engine/expr")
GENERIC_BASES = frozenset(
    (
        "ObExprOperator",
        "ObFuncExprOperator",
        "ObStringExprOperator",
        "ObArithExprOperator",
        "ObRelationalExprOperator",
        "ObLogicalExprOperator",
        "ObBitwiseExprOperator",
        "ObSubQueryRelationalExpr",
        "ObVectorExprOperator",
        "ObLocationExprOperator",
        "ObMinMaxExprOperator",
    )
)
COMPILE_TIME_MEMBER = re.compile(
    r"^(?:~?Ob\w+|calc_result_type\w*|cg_expr|assign|deep_copy|serialize|deserialize|"
    r"get_serialize_size|need_rt_ctx|is_\w+|get_\w+|set_\w+|init\w*|reset|destroy|"
    r"to_string|check_\w+)$"
)
EVAL_ASSIGNMENT = re.compile(
    r"(?:\.|->)\s*(eval_(?:func|batch_func|vector_func)_)\s*=\s*([^;]+);"
)
EVAL_SIGNATURES = {
    "eval_func_": "oceanbase::sql::ObExpr const&, oceanbase::sql::ObEvalCtx&, oceanbase::common::ObDatum&",
    "eval_batch_func_": (
        "oceanbase::sql::ObExpr const&, oceanbase::sql::ObEvalCtx&, "
        "oceanbase::sql::ObBitVectorImpl<unsigned long long> const&, long long"
    ),
    "eval_vector_func_": (
        "oceanbase::sql::ObExpr const&, oceanbase::sql::ObEvalCtx&, "
        "oceanbase::sql::ObBitVectorImpl<unsigned long long> const&, oceanbase::sql::EvalBound const&"
    ),
}
HELPER_CALL = "helper"
FACTORY_ALLOC = re.compile(r"ObExprOperatorFactory::alloc<(?:\w+::)*(\w+)>\(")
CLASS_COMPILE_MEMBER = re.compile(r"^(?:calc_result_type\w*|cg_expr)$")
TSV_COLUMNS = (
    "order",
    "name",
    "item_type",
    "class",
    "param_num",
    "internal",
    "registered",
    "reached",
    "reach_basis",
    "eval_functions",
    "eval_functions_run",
    "probes",
    "sql",
    "constructor",
)


class GeneratorError(Exception):
    pass


Entry = namedtuple(
    "Entry",
    "order name item_type cls param_num internal debug_only constructor",
)


def read_text(relative):
    path = REPO_ROOT / relative
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise GeneratorError("cannot read {}: {}".format(path, exc))


def blank_comments(text):
    text = re.sub(
        r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S
    )
    return re.sub(r"//[^\n]*", "", text)


def closing_paren(text, start):
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def closing_brace(text, start):
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def split_top_level(text):
    parts = []
    depth = 0
    current = []
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_parameters(text):
    parameters = []
    for raw in split_top_level(text):
        raw = " ".join(raw.split())
        if not raw or raw == "void":
            continue
        default = None
        if "=" in raw:
            raw, default = raw.split("=", 1)
            raw = raw.strip()
            default = default.strip()
        match = re.search(r"(\w+)\s*(?:\[\s*\])?$", raw)
        parameters.append((match.group(1) if match else raw, default))
    return parameters


def parse_initializers(text, close):
    position = close + 1
    while position < len(text) and text[position].isspace():
        position += 1
    if position >= len(text):
        return None, [], ""
    if text[position] == ";":
        return "decl", [], ""
    if text[position] == "{":
        end = closing_brace(text, position)
        return "def", [], text[position : end + 1] if end >= 0 else ""
    if text[position] != ":" or text[position : position + 2] == "::":
        return None, [], ""
    initializers = []
    position += 1
    item = re.compile(r"\s*((?:\w+::)*\w+)\s*(?:<[^()]*>)?\s*\(")
    while True:
        match = item.match(text, position)
        if not match:
            break
        open_at = match.end() - 1
        close_at = closing_paren(text, open_at)
        if close_at < 0:
            break
        initializers.append(
            (match.group(1).split("::")[-1], split_top_level(text[open_at + 1 : close_at]))
        )
        position = close_at + 1
        comma = re.compile(r"\s*,").match(text, position)
        if not comma:
            break
        position = comma.end()
    body = ""
    brace = re.compile(r"\s*\{").match(text, position)
    if brace:
        end = closing_brace(text, brace.end() - 1)
        if end >= 0:
            body = text[brace.end() - 1 : end + 1]
    return "def", initializers, body


class SourceIndex(object):
    def __init__(self):
        self.texts = OrderedDict()
        for directory in CONSTRUCTOR_DIRS:
            base = REPO_ROOT / directory
            for path in sorted(base.rglob("*")):
                if path.suffix in (".h", ".cpp", ".ipp") and path.is_file():
                    relative = path.relative_to(REPO_ROOT).as_posix()
                    if relative not in self.texts:
                        self.texts[relative] = blank_comments(
                            path.read_text(encoding="utf-8", errors="replace")
                        )
        self.names = {}
        name_text = read_text(NAME_DEF_FILE)
        for match in re.finditer(r'#define\s+(N_\w+)\s+"([^"]*)"', name_text):
            self.names.setdefault(match.group(1), match.group(2))
        self.constructors = {}
        self.class_bodies = {}
        self.cg_bodies = {}
        self.member_bodies = {}
        self.chains = {}
        for relative, text in self.texts.items():
            self.index_file(relative, text)

    def line_of(self, relative, offset):
        return self.texts[relative].count("\n", 0, offset) + 1

    def add_constructor(self, cls, relative, offset, where, parameters, kind, inits, body):
        self.constructors.setdefault(cls, []).append(
            {
                "file": relative,
                "line": self.line_of(relative, offset),
                "where": where,
                "parameters": parameters,
                "kind": kind,
                "inits": inits,
                "body": body,
            }
        )

    def index_file(self, relative, text):
        for match in re.finditer(r"\b(Ob\w+)::\1\s*\(", text):
            before = text[: match.start()].rstrip()
            if before.endswith(":") or before.endswith(","):
                continue
            open_at = match.end() - 1
            close_at = closing_paren(text, open_at)
            if close_at < 0:
                continue
            kind, inits, body = parse_initializers(text, close_at)
            self.add_constructor(
                match.group(1),
                relative,
                match.start(),
                "out",
                parse_parameters(text[open_at + 1 : close_at]),
                kind,
                inits,
                body,
            )
        for match in re.finditer(r"\bint\s+(Ob\w+)::(\w+)\s*\(", text):
            brace = text.find("{", match.end())
            end = closing_brace(text, brace) if brace >= 0 else -1
            if end < 0:
                continue
            key = (match.group(1), match.group(2))
            self.member_bodies.setdefault(key, []).append((relative, text[brace : end + 1]))
            if match.group(2) == "cg_expr":
                self.cg_bodies.setdefault(match.group(1), []).append(
                    (relative, text[brace : end + 1])
                )
        for match in re.finditer(
            r"\b(?:class|struct)\s+(Ob\w+)\b\s*(?:final\s*)?(:[^{;]*)?\{", text
        ):
            cls = match.group(1)
            start = match.end() - 1
            end = closing_brace(text, start)
            if end < 0:
                continue
            body = text[start + 1 : end]
            self.class_bodies.setdefault(cls, []).append((relative, body))
            pattern = re.compile(r"(?<![:~\w])(?:explicit\s+)?%s\s*\(" % cls)
            for inner in pattern.finditer(body):
                open_at = inner.end() - 1
                close_at = closing_paren(body, open_at)
                if close_at < 0:
                    continue
                kind, inits, ctor_body = parse_initializers(body, close_at)
                if kind is None:
                    continue
                self.add_constructor(
                    cls,
                    relative,
                    start + 1 + inner.start(),
                    "in",
                    parse_parameters(body[open_at + 1 : close_at]),
                    kind,
                    inits,
                    ctor_body,
                )
            cg = re.search(
                r"\bint\s+cg_expr\s*\([^)]*\)\s*(?:const)?\s*(?:override)?\s*\{", body
            )
            if cg:
                brace = cg.end() - 1
                cg_end = closing_brace(body, brace)
                if cg_end >= 0:
                    self.cg_bodies.setdefault(cls, []).append(
                        (relative, body[brace : cg_end + 1])
                    )

    def pick_constructor(self, cls, argument_count):
        declarations = [
            c for c in self.constructors.get(cls, []) if c["where"] == "in"
        ]
        candidates = []
        for ctor in self.constructors.get(cls, []):
            if ctor["kind"] != "def":
                continue
            parameters = list(ctor["parameters"])
            if ctor["where"] == "out":
                for declaration in declarations:
                    if len(declaration["parameters"]) == len(parameters) and any(
                        p[1] for p in declaration["parameters"]
                    ):
                        parameters = [
                            (name, declared[1])
                            for (name, _), declared in zip(
                                parameters, declaration["parameters"]
                            )
                        ]
                        break
            required = sum(1 for p in parameters if p[1] is None)
            if required <= argument_count <= len(parameters):
                candidates.append((len(parameters), ctor["line"], ctor, parameters))
        candidates.sort(key=lambda c: (c[0], c[1]))
        return candidates[0] if candidates else None

    def resolve(self, cls, arguments, depth=0):
        if depth > 16:
            raise GeneratorError("constructor chain too deep at {}".format(cls))
        picked = self.pick_constructor(cls, len(arguments))
        if picked is None:
            return None
        ctor, parameters = picked[2], picked[3]
        environment = {}
        for index, (name, default) in enumerate(parameters):
            environment[name] = arguments[index] if index < len(arguments) else default
        if cls == "ObExprOperator":
            return {"env": environment, "chain": [], "overrides": {}}
        for base, base_arguments in ctor["inits"]:
            if base not in self.constructors:
                continue
            substituted = [environment.get(a, a) for a in base_arguments]
            result = self.resolve(base, substituted, depth + 1)
            if result is None:
                continue
            result["chain"].insert(0, (cls, ctor["file"], ctor["line"]))
            for field, pattern in (
                ("type", r"&type_\)\)\s*=\s*(T_\w+)"),
                ("name", r"&name_\)\)\s*=\s*(N_\w+)"),
            ):
                found = re.search(pattern, ctor["body"])
                if found:
                    result["overrides"][field] = found.group(1)
            return result
        return None

    def name_value(self, token):
        if token in self.names:
            return self.names[token]
        literal = re.fullmatch(r'"([^"]*)"', token or "")
        if literal:
            return literal.group(1)
        raise GeneratorError("cannot resolve expression name {}".format(token))


def registry_lines():
    text = read_text(FACTORY_FILE)
    start = text.index("void ObExprOperatorFactory::register_expr_operators()")
    end = text.index("\n}\n", start)
    lines = []
    debug_block = False
    for raw in text[start:end].split("\n"):
        line = raw.strip()
        if line.startswith("#if"):
            debug_block = "NDEBUG" in line or "ENABLE_DEBUG_LOG" in line
            continue
        if line.startswith("#endif"):
            debug_block = False
            continue
        match = re.match(r"REG_OP\((\w+)\);", line)
        if match:
            lines.append(("op", match.group(1), debug_block))
            continue
        match = re.match(r"REG_SAME_OP\((\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,", line)
        if match:
            lines.append(("same", match.groups(), debug_block))
    if not lines:
        raise GeneratorError("no REG_OP lines found in {}".format(FACTORY_FILE))
    return lines


def load_registry(index=None):
    index = index or SourceIndex()
    entries = []
    seen = set()
    for kind, value, debug_only in registry_lines():
        if kind == "op":
            if value in seen:
                continue
            seen.add(value)
            resolved = index.resolve(value, ["alloc"])
            if resolved is None:
                raise GeneratorError("cannot resolve the constructor of {}".format(value))
            env = resolved["env"]
            item_type = resolved["overrides"].get("type", env.get("type"))
            name_token = resolved["overrides"].get("name", env.get("name"))
            if not item_type or not item_type.startswith("T_"):
                raise GeneratorError("no item type for {}: {}".format(value, item_type))
            internal = env.get("is_internal_for_mysql") in ("INTERNAL_IN_MYSQL_MODE", "true")
            head = resolved["chain"][0]
            entries.append(
                Entry(
                    len(entries) + 1,
                    index.name_value(name_token),
                    item_type,
                    value,
                    env.get("param_num"),
                    internal,
                    debug_only,
                    "{}:{}".format(head[1], head[2]),
                )
            )
            index.chains[value] = [c[0] for c in resolved["chain"]]
        else:
            original_type, new_type, new_name = value
            original = [e for e in entries if e.item_type == original_type]
            if not original:
                raise GeneratorError("REG_SAME_OP names an unregistered type {}".format(original_type))
            entries.append(
                Entry(
                    len(entries) + 1,
                    index.name_value(new_name),
                    new_type,
                    original[0].cls,
                    original[0].param_num,
                    original[0].internal,
                    debug_only,
                    original[0].constructor,
                )
            )
    keys = [(e.name, e.item_type, e.cls) for e in entries]
    if len(set(keys)) != len(keys):
        raise GeneratorError("duplicate registry entries")
    return index, entries


def strip_template_arguments(text):
    result = []
    depth = 0
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(depth - 1, 0)
        elif depth == 0:
            result.append(char)
    return "".join(result)


def qualified_parts(demangled):
    depth = 0
    cut = len(demangled)
    for index, char in enumerate(demangled):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "(" and depth == 0:
            cut = index
            break
    head = strip_template_arguments(demangled[:cut]).replace("(anonymous namespace)", "anon")
    head = head.strip().split(" ")[-1]
    return tuple(part for part in head.split("::") if part)


def parameter_list(demangled):
    depth = 0
    start = -1
    for index, char in enumerate(demangled):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "(" and depth == 0 and start < 0:
            start = index
            break
    if start < 0:
        return None
    close = closing_paren(demangled, start)
    if close < 0:
        return None
    return demangled[start + 1 : close]


def run_tool(command, stdin_text=None):
    try:
        completed = subprocess.run(
            command,
            input=stdin_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
        )
    except OSError as exc:
        raise GeneratorError("cannot run {}: {}".format(command[0], exc))
    if completed.returncode != 0:
        raise GeneratorError(
            "{} failed with exit code {}: {}".format(
                command[0], completed.returncode, completed.stderr.strip()[:2000]
            )
        )
    return completed.stdout


def export_function_counts(profdata, binary, llvm_bin, coverage_root):
    sources = [str(Path(coverage_root) / d) for d in COVERAGE_SOURCE_DIRS]
    lcov = run_tool(
        [
            str(Path(llvm_bin) / "llvm-cov"),
            "export",
            "-format=lcov",
            "-skip-expansions",
            "-instr-profile={}".format(profdata),
            str(binary),
        ]
        + sources
    )
    records = []
    counts = {}
    current = None
    prefix = str(Path(coverage_root)) + "/"
    for line in lcov.split("\n"):
        if line.startswith("SF:"):
            current = line[3:]
            if current.startswith(prefix):
                current = current[len(prefix) :]
        elif line.startswith("FN:"):
            number, mangled = line[3:].split(",", 1)
            records.append((current, int(number), mangled))
        elif line.startswith("FNDA:"):
            count, mangled = line[5:].split(",", 1)
            counts[(current, mangled)] = int(count)
    if not records:
        raise GeneratorError("llvm-cov exported no functions for {}".format(sources))
    plain = [re.sub(r"^[^:]*\.(?:cpp|h|ipp|c|cc):", "", r[2]) for r in records]
    demangled = run_tool(
        [str(Path(llvm_bin) / "llvm-cxxfilt"), "-n"], "\n".join(plain) + "\n"
    ).split("\n")
    functions = []
    for (source, number, mangled), name in zip(records, demangled):
        alloc = FACTORY_ALLOC.search(name)
        functions.append(
            {
                "file": source,
                "line": number,
                "count": counts.get((source, mangled), 0),
                "name": name,
                "parts": qualified_parts(name),
                "params": parameter_list(name),
                "alloc_class": alloc.group(1) if alloc else None,
            }
        )
    return functions


OUT_PARAMETER_CALL = re.compile(r"((?:\w+::)*\w+)\s*\(([^;]*?)\beval_(?:func|batch_func|vector_func)_\b")
DISPATCH_TABLES = (
    (DATUM_CAST_FILE, "OB_DATUM_CAST_MYSQL_IMPLICIT"),
    (DATUM_CAST_FILE, "OB_DATUM_CAST_MYSQL_ENUMSET_IMPLICIT"),
)


def dispatch_table_functions(name):
    for relative, table in DISPATCH_TABLES:
        if table != name:
            continue
        text = blank_comments(read_text(relative))
        match = re.search(r"\b{}\s*\[[^=]*=\s*\{{".format(re.escape(table)), text)
        if not match:
            return []
        end = closing_brace(text, match.end() - 1)
        names = []
        for ident in re.finditer(r"\b([A-Za-z_]\w*)\b", text[match.end() : end]):
            if ident.group(1) not in names:
                names.append(ident.group(1))
        return names
    return []


def assigned_functions(body):
    references = []
    tokens = set()
    for match in EVAL_ASSIGNMENT.finditer(body):
        for ident in re.finditer(r"((?:\w+::)*\w+)", match.group(2)):
            token = ident.group(1)
            if token in ("NULL", "nullptr", "true", "false") or token.isdigit():
                continue
            if (token, match.group(1)) not in references:
                references.append((token, match.group(1)))
                tokens.add(token)
    for match in OUT_PARAMETER_CALL.finditer(body):
        token = match.group(1)
        if token not in tokens and token not in ("if", "OB_FAIL", "OB_SUCC", "OZ", "CK"):
            references.append((token, HELPER_CALL))
            tokens.add(token)
    return references


def eval_references(index, chain):
    for cls in chain:
        bodies = index.cg_bodies.get(cls)
        if not bodies:
            continue
        pending = [(cls, "cg_expr", body) for _, body in bodies]
        visited = set()
        for depth in range(4):
            references = []
            sources = []
            calls = []
            for owner, member, body in pending:
                found = [r for r in assigned_functions(body) if r not in references]
                if found:
                    references += found
                    label = "{}::{}".format(owner, member)
                    if label not in sources:
                        sources.append(label)
                for call in re.finditer(r"\b((?:\w+::)?\w+)\s*\(", body):
                    if call.group(1) not in calls:
                        calls.append(call.group(1))
            if references:
                return cls, ", ".join(sources), references
            next_pending = []
            for call in calls:
                if "::" in call:
                    owner, member = call.split("::", 1)
                    candidates = [(owner, member)]
                else:
                    candidates = [(owner, call) for owner in chain]
                for key in candidates:
                    if key in index.member_bodies and key not in visited:
                        visited.add(key)
                        for _, body in index.member_bodies[key]:
                            next_pending.append((key[0], key[1], body))
            if not next_pending:
                break
            pending = next_pending
    return None, None, []


def classify_reach(index, entries, functions):
    by_last = {}
    by_class = {}
    allocations = {}
    compile_members = {}
    for function in functions:
        if function["alloc_class"]:
            allocations[function["alloc_class"]] = allocations.get(function["alloc_class"], 0) + function["count"]
        parts = function["parts"]
        if not parts:
            continue
        by_last.setdefault(parts[-1], []).append(function)
        if len(parts) >= 2:
            by_class.setdefault(parts[-2], []).append(function)
            if CLASS_COMPILE_MEMBER.match(parts[-1]):
                compile_members.setdefault(parts[-2], []).append(function)
    results = {}
    for entry in entries:
        if entry.cls in results:
            continue
        chain = index.chains.get(entry.cls, [entry.cls])
        specific = []
        for cls in chain:
            if cls in GENERIC_BASES:
                break
            specific.append(cls)
        if not specific:
            specific = [entry.cls]
        cg_class, source, references = eval_references(index, chain)
        expanded = []
        for reference, member in references:
            table = dispatch_table_functions(reference)
            if table:
                expanded.extend((name, "eval_func_") for name in table)
            else:
                expanded.append((reference, member))
        evals = []
        for reference, member in expanded:
            parts = reference.split("::")
            candidates = []
            for function in by_last.get(parts[-1], []):
                qualifier = function["parts"][-2] if len(function["parts"]) >= 2 else ""
                if len(parts) >= 2:
                    matched = qualifier == parts[-2]
                else:
                    matched = qualifier in chain or not qualifier.startswith("Ob")
                if matched:
                    candidates.append(function)
            signature = EVAL_SIGNATURES.get(member)
            if signature:
                exact = [f for f in candidates if f["params"] == signature]
                if exact:
                    candidates = exact
            for function in candidates:
                if function not in evals:
                    evals.append(function)
        own = [f for f in evals if len(f["parts"]) >= 2 and f["parts"][-2] == entry.cls]
        if own and cg_class != entry.cls:
            evals = own
        members = []
        for cls in specific:
            for function in by_class.get(cls, []):
                if not COMPILE_TIME_MEMBER.match(function["parts"][-1]) and function not in members:
                    members.append(function)
        if entry.debug_only:
            basis, chosen = "debug build only", []
        elif evals:
            basis, chosen = "eval functions set by {}".format(source), evals
        elif members:
            basis, chosen = "members of {}".format("/".join(specific)), members
        else:
            basis, chosen = "no functions found", []
        compiled = compile_members.get(entry.cls, [])
        if not entry.debug_only and allocations.get(entry.cls, 0) == 0:
            basis += "; never created: ObExprOperatorFactory::alloc<{}> ran 0 times".format(entry.cls)
            gate = False
        elif not entry.debug_only and compiled and not any(f["count"] > 0 for f in compiled):
            basis += "; created but never compiled: {} calc_result_type and cg_expr ran 0 times".format(entry.cls)
            gate = False
        else:
            gate = True
        results[entry.cls] = {
            "reached": gate and any(f["count"] > 0 for f in chosen),
            "basis": basis,
            "functions": len(chosen),
            "functions_run": sum(1 for f in chosen if f["count"] > 0),
        }
    return results


SESSION_SQL_MODE = "STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_AUTO_CREATE_USER"
FIXED_TIMESTAMP = "1709214296.654321"
SESSION_SETTINGS = (
    "SET NAMES utf8mb4 COLLATE utf8mb4_general_ci, @@session.time_zone = '+00:00', @@session.sql_mode = '{}', "
    "@@session.timestamp = {}, @@session.div_precision_increment = 4, "
    "@@session.block_encryption_mode = 'aes-128-ecb', @@session.group_concat_max_len = 1024".format(
        SESSION_SQL_MODE, FIXED_TIMESTAMP
    ),
)
MATRIX_TABLE = "tm"
PROBES_PER_FILE = 50
LONG_TEXT = "'" + "The quick brown fox jumps over the lazy dog 0123456789. " * 2 + "'"
DECIMAL_65_MAX = "9" * 35 + "." + "9" * 30
MATRIX_COLUMNS = (
    ("c_tinyint", "TINYINT", ("NULL", "0", "-128", "127", "42", "-7", "1", "-1")),
    ("c_utinyint", "TINYINT UNSIGNED", ("NULL", "0", "1", "255", "42", "7", "128", "100")),
    ("c_smallint", "SMALLINT", ("NULL", "0", "-32768", "32767", "1234", "-1234", "1", "-1")),
    ("c_mediumint", "MEDIUMINT", ("NULL", "0", "-8388608", "8388607", "123456", "-123456", "1", "-1")),
    ("c_int", "INT", ("NULL", "0", "-2147483648", "2147483647", "20240229", "-42", "1", "-1")),
    ("c_uint", "INT UNSIGNED", ("NULL", "0", "1", "4294967295", "134556", "3000000000", "2", "20240229")),
    (
        "c_bigint",
        "BIGINT",
        ("NULL", "0", "-9223372036854775808", "9223372036854775807", "20240229134556", "-20240229134556", "1", "-1"),
    ),
    (
        "c_ubigint",
        "BIGINT UNSIGNED",
        ("NULL", "0", "1", "18446744073709551615", "9223372036854775808", "20240229134556", "2", "3"),
    ),
    ("c_float", "FLOAT", ("NULL", "0", "-3.40282e38", "3.40282e38", "3.14159", "-2.5", "0.5", "1.1")),
    (
        "c_double",
        "DOUBLE",
        (
            "NULL",
            "0",
            "-1.7976931348623157e308",
            "1.7976931348623157e308",
            "3.141592653589793",
            "-2.5",
            "0.5",
            "2.2250738585072014e-308",
        ),
    ),
    ("c_dec_9_2", "DECIMAL(9,2)", ("NULL", "0", "-9999999.99", "9999999.99", "42.50", "-2.50", "0.50", "1.05")),
    (
        "c_dec_18_6",
        "DECIMAL(18,6)",
        ("NULL", "0", "-999999999999.999999", "999999999999.999999", "3.141593", "-2.5", "0.5", "1234.5"),
    ),
    (
        "c_dec_38_10",
        "DECIMAL(38,10)",
        (
            "NULL",
            "0",
            "-" + "9" * 28 + "." + "9" * 10,
            "9" * 28 + "." + "9" * 10,
            "20240229134556.123456",
            "-2.5",
            "0.5",
            "0.0000000001",
        ),
    ),
    (
        "c_dec_65_30",
        "DECIMAL(65,30)",
        (
            "NULL",
            "0",
            "-" + DECIMAL_65_MAX,
            DECIMAL_65_MAX,
            "3.141592653589793238462643383279",
            "-2.5",
            "0.5",
            "0.000000000000000000000000000001",
        ),
    ),
    ("c_char", "CHAR(16)", ("NULL", "''", "' '", "'abcdefghijklmnop'", "'Hello'", "' 12.5abc'", "'0.5'", "'héllo'")),
    (
        "c_varchar",
        "VARCHAR(64)",
        (
            "NULL",
            "''",
            "'   '",
            "'9223372036854775808'",
            "'Hello, World!'",
            "'-12.5e3xyz'",
            "'2024-02-29 13:45:56.5'",
            "'中文字符😀'",
        ),
    ),
    ("c_binary", "BINARY(4)", ("NULL", "''", "X'00'", "X'FFFFFFFF'", "'abcd'", "X'80'", "'1'", "X'41'")),
    (
        "c_varbinary",
        "VARBINARY(64)",
        ("NULL", "''", "X'00'", "X'FFFFFFFFFFFFFFFF'", "'abc'", "X'C3A9'", "'12.5'", "X'F09F9880'"),
    ),
    ("c_tinytext", "TINYTEXT", ("NULL", "''", "' '", "'a'", "'tiny text'", "'-1'", "'2.5'", "'é'")),
    ("c_text", "TEXT", ("NULL", "''", "'x'", LONG_TEXT, "'Some text'", "'-42abc'", "'0.5'", "'ünïcödé'")),
    ("c_mediumtext", "MEDIUMTEXT", ("NULL", "''", "'  '", LONG_TEXT, "'medium'", "'7e2'", "'-0.5'", "'中文'")),
    ("c_longtext", "LONGTEXT", ("NULL", "''", "'{\"a\": 1}'", LONG_TEXT, "'long text'", "'12'", "'1e3'", "'😀'")),
    ("c_tinyblob", "TINYBLOB", ("NULL", "''", "X'00'", "X'FF'", "'blob'", "'-1'", "'2.5'", "X'E4B8AD'")),
    ("c_blob", "BLOB", ("NULL", "''", "X'0001'", LONG_TEXT, "'Some bytes'", "'-42'", "'0.5'", "X'F09F9880'")),
    ("c_mediumblob", "MEDIUMBLOB", ("NULL", "''", "X'00'", LONG_TEXT, "'medium blob'", "'3'", "'0.25'", "X'FFFE'")),
    ("c_longblob", "LONGBLOB", ("NULL", "''", "X'7F'", LONG_TEXT, "'long blob'", "'-3'", "'1.5'", "X'C0FFEE'")),
    (
        "c_date",
        "DATE",
        ("NULL", "'1970-01-01'", "'1000-01-01'", "'9999-12-31'", "'2024-02-29'", "'1999-12-31'", "'2000-01-01'", "'2023-12-31'"),
    ),
    (
        "c_time",
        "TIME",
        ("NULL", "'00:00:00'", "'-838:59:59'", "'838:59:59'", "'13:45:56'", "'-00:00:01'", "'23:59:59'", "'100:00:00'"),
    ),
    (
        "c_time6",
        "TIME(6)",
        (
            "NULL",
            "'00:00:00'",
            "'-838:59:59'",
            "'838:59:59'",
            "'13:45:56.123456'",
            "'-12:30:00.5'",
            "'23:59:59.999999'",
            "'00:00:00.000001'",
        ),
    ),
    (
        "c_datetime",
        "DATETIME",
        (
            "NULL",
            "'1970-01-01 00:00:00'",
            "'1000-01-01 00:00:00'",
            "'9999-12-31 23:59:59'",
            "'2024-02-29 13:45:56'",
            "'1999-12-31 23:59:59'",
            "'2023-12-31 23:59:59'",
            "'2000-01-01 12:00:00'",
        ),
    ),
    (
        "c_datetime6",
        "DATETIME(6)",
        (
            "NULL",
            "'1970-01-01 00:00:00'",
            "'1000-01-01 00:00:00'",
            "'9999-12-31 23:59:59.999999'",
            "'2024-02-29 13:45:56.123456'",
            "'1969-12-31 23:59:59.999999'",
            "'2023-12-31 23:59:59.500000'",
            "'2000-02-29 00:00:00.000001'",
        ),
    ),
    (
        "c_timestamp",
        "TIMESTAMP(6) NULL DEFAULT NULL",
        (
            "NULL",
            "'1970-01-01 00:00:01'",
            "'1970-01-01 00:00:01.000001'",
            "'2038-01-19 03:14:07.999999'",
            "'2024-02-29 13:45:56.123456'",
            "'1999-12-31 23:59:59'",
            "'2023-12-31 23:59:59.500000'",
            "'2000-02-29 00:00:00.000001'",
        ),
    ),
    ("c_year", "YEAR", ("NULL", "0", "1901", "2155", "2024", "1999", "2000", "70")),
    ("c_bit", "BIT(64)", ("NULL", "b'0'", "b'1'", "b'" + "1" * 64 + "'", "42", "255", "65", "1234567890123")),
    ("c_enum", "ENUM('a','b','c','中')", ("NULL", "'a'", "'a'", "'中'", "'b'", "'c'", "'b'", "'c'")),
    ("c_set", "SET('x','y','z')", ("NULL", "''", "'x'", "'x,y,z'", "'y'", "'x,z'", "'z'", "'y,z'")),
    (
        "c_json",
        "JSON",
        (
            "NULL",
            "'null'",
            "'[]'",
            "'{\"a\": {\"b\": [1, 2, {\"c\": null}]}, \"d\": \"e\"}'",
            "'{\"a\": 1, \"b\": [true, false]}'",
            "'[1, \"two\", 3.5, null]'",
            "'\"str\"'",
            "'12.5'",
        ),
    ),
    (
        "c_geom",
        "GEOMETRY",
        (
            "NULL",
            "ST_GeomFromText('POINT(0 0)')",
            "ST_GeomFromText('POINT(-1.5 2.5)')",
            "ST_GeomFromText('LINESTRING(0 0,1 1,2 0)')",
            "ST_GeomFromText('POLYGON((0 0,4 0,4 4,0 4,0 0))')",
            "ST_GeomFromText('MULTIPOINT((1 1),(2 2))')",
            "ST_GeomFromText('GEOMETRYCOLLECTION(POINT(1 1),LINESTRING(0 0,1 1))')",
            "ST_GeomFromText('MULTIPOLYGON(((0 0,1 0,1 1,0 1,0 0)),((2 2,3 2,3 3,2 3,2 2)))')",
        ),
    ),
    (
        "c_vec",
        "VECTOR(3)",
        ("NULL", "'[0,0,0]'", "'[-1.5,2,3]'", "'[1e10,-1e10,0.001]'", "'[1,2,3]'", "'[3,4,0]'", "'[0.5,-0.5,1e-7]'", "'[1,1,1]'"),
    ),
    ("c_arr", "ARRAY(INT)", ("NULL", "'[]'", "[0]", "[-2147483648,2147483647]", "[1,2,3]", "[3,1,2,1]", "[5]", "[7,8]")),
)
CORE_COLUMNS = (
    "c_int",
    "c_bigint",
    "c_ubigint",
    "c_float",
    "c_double",
    "c_dec_9_2",
    "c_dec_65_30",
    "c_varchar",
    "c_varbinary",
    "c_text",
    "c_date",
    "c_time6",
    "c_datetime6",
    "c_timestamp",
    "c_year",
    "c_bit",
    "c_enum",
    "c_json",
    "c_geom",
    "c_vec",
)
SMALL_COLUMNS = ("c_int", "c_double", "c_dec_38_10", "c_varchar", "c_text", "c_datetime6", "c_json")
EXTRA_COLUMNS = ("c_double", "c_varchar", "c_datetime6")


class Domain(object):
    def __init__(self, typical, literals, columns=True, keyword=False):
        self.typical = typical
        self.literals = tuple(literals)
        self.columns = columns and not keyword
        self.keyword = keyword


DOMAINS = {
    "A": Domain(
        "1",
        (
            "NULL",
            "0",
            "-2.5e0",
            "18446744073709551615",
            "''",
            "' 12.5x'",
            "'中文😀'",
            "X'00FF'",
            "DATE'2024-02-29'",
            "TIMESTAMP'2024-02-29 13:45:56.123456'",
            "CAST('{\"a\": 1}' AS JSON)",
        ),
    ),
    "N": Domain(
        "2",
        (
            "NULL",
            "0",
            "-2.5",
            "2.5",
            "0.5e0",
            "1e308",
            "-9223372036854775808",
            "18446744073709551615",
            "12345678901234567890.1234567890123",
            "'12.5abc'",
        ),
    ),
    "I": Domain(
        "2",
        ("NULL", "0", "1", "-1", "3", "100", "1.5", "'2'", "4294967297", "-9223372036854775808"),
    ),
    "CNT": Domain("3", ("NULL", "0", "1", "-1", "10", "2.5", "'4'", "'x'"), columns=False),
    "S": Domain(
        "'abc'",
        (
            "NULL",
            "''",
            "'Hello, World!'",
            "'héllo wörld'",
            "'中文字符串'",
            "'x😀y'",
            "'12.5abc'",
            "X'00FF41'",
        ),
    ),
    "T": Domain(
        "TIMESTAMP'2024-02-29 13:45:56.123456'",
        (
            "NULL",
            "DATE'2024-02-29'",
            "DATE'9999-12-31'",
            "TIMESTAMP'1970-01-01 00:00:00.5'",
            "TIME'838:59:59'",
            "TIME'-12:34:56.789'",
            "'0000-00-00'",
            "'2024-02-30'",
            "20240229134556.5",
            "'abc'",
        ),
    ),
    "J": Domain(
        "CAST('{\"a\": 1, \"b\": [1, 2, {\"c\": null}]}' AS JSON)",
        (
            "NULL",
            "'{\"a\": 1, \"b\": [1, 2, {\"c\": null}]}'",
            "'[1, \"two\", 3.5, true, null]'",
            "'\"s\"'",
            "'12.5'",
            "'null'",
            "'{}'",
            "'not json'",
            "CAST('{\"k\": [1, 2]}' AS JSON)",
        ),
    ),
    "JP": Domain(
        "'$.a'",
        ("NULL", "'$'", "'$.b[1]'", "'$[0]'", "'$.*'", "'$**.c'", "'$.b[last]'", "'$.nosuch'", "'bad'"),
        columns=False,
    ),
    "JV": Domain("'v'", ("NULL", "1", "2.5", "TRUE", "CAST('[1]' AS JSON)", "'x'")),
    "ONEALL": Domain("'one'", ("NULL", "'all'", "'some'"), columns=False),
    "JS": Domain(
        "'{\"type\": \"object\", \"properties\": {\"a\": {\"type\": \"number\"}}, \"required\": [\"a\"]}'",
        ("NULL", "'{}'", "'{\"type\": \"array\"}'", "'{\"type\": \"string\", \"maxLength\": 1}'", "'not a schema'"),
        columns=False,
    ),
    "G": Domain(
        "ST_GeomFromText('POINT(1 2)')",
        (
            "NULL",
            "ST_GeomFromText('LINESTRING(0 0,1 1,2 0)')",
            "ST_GeomFromText('POLYGON((0 0,4 0,4 4,0 4,0 0))')",
            "ST_GeomFromText('MULTIPOINT((1 1),(2 2))')",
            "ST_GeomFromText('GEOMETRYCOLLECTION(POINT(1 1),LINESTRING(0 0,1 1))')",
            "ST_GeomFromText('POINT(116.4 39.9)', 4326)",
            "ST_GeomFromText('POLYGON((0 0,0 1,1 1,1 0,0 0))', 4326)",
            "'abc'",
        ),
    ),
    "G2": Domain(
        "ST_GeomFromText('POLYGON((0 0,2 0,2 2,0 2,0 0))')",
        (
            "NULL",
            "ST_GeomFromText('POINT(1 1)')",
            "ST_GeomFromText('POINT(5 5)')",
            "ST_GeomFromText('LINESTRING(-1 1,3 1)')",
            "ST_GeomFromText('POLYGON((1 1,3 1,3 3,1 3,1 1))')",
            "ST_GeomFromText('POINT(1 1)', 4326)",
        ),
    ),
    "WKT": Domain(
        "'POINT(1 2)'",
        (
            "NULL",
            "'LINESTRING(0 0,1 1,2 0)'",
            "'POLYGON((0 0,4 0,4 4,0 4,0 0))'",
            "'MULTIPOINT((1 1),(2 2))'",
            "'GEOMETRYCOLLECTION(POINT(1 1))'",
            "'POINT(1)'",
            "''",
            "'POINT(40 -120)'",
        ),
    ),
    "WKB": Domain(
        "X'0101000000000000000000F03F0000000000000040'",
        ("NULL", "ST_AsBinary(ST_GeomFromText('LINESTRING(0 0,1 1)'))", "X'00'", "'abc'"),
    ),
    "SRID": Domain("4326", ("NULL", "0", "3857", "1", "-1", "4294967296"), columns=False),
    "V": Domain(
        "'[1,2,3]'",
        ("NULL", "'[0,0,0]'", "'[-1.5,2.25,3e10]'", "'[1,2]'", "'[1,2,3,4]'", "'abc'", "[1,2,3]"),
    ),
    "METRIC": Domain(
        "euclidean",
        ("cosine", "dot", "manhattan", "euclidean_squared", "hamming"),
        keyword=True,
    ),
    "AR": Domain(
        "[1,2,3]",
        ("NULL", "[3,1,2,1]", "ARRAY_REMOVE([1], 1)", "[NULL,1]", "['a','b','c']", "[[1,2],[3]]", "'[1,2]'"),
    ),
    "EL": Domain("2", ("NULL", "0", "'a'", "1.5", "4")),
    "IP4": Domain(
        "'192.168.1.1'",
        ("NULL", "'0.0.0.0'", "'255.255.255.255'", "'1.2.3'", "'256.1.1.1'", "''", "'abc'", "3232235777"),
    ),
    "IP6": Domain(
        "'::1'",
        ("NULL", "'::ffff:1.2.3.4'", "'fe80::1:2'", "'2001:db8::'", "'1.2.3.4'", "'bad'", "''"),
    ),
    "IPB": Domain(
        "INET6_ATON('::1')",
        (
            "NULL",
            "INET6_ATON('192.168.1.1')",
            "INET6_ATON('::ffff:1.2.3.4')",
            "UNHEX('00000000000000000000000001020304')",
            "X'00'",
            "'abc'",
        ),
    ),
    "RX": Domain("'b+'", ("NULL", "'^a'", "'[[:digit:]]+'", "'(a)(b)?'", "''", "'('", "'é'", "'(?i)H'"), columns=False),
    "RXM": Domain("'c'", ("NULL", "'i'", "'m'", "'n'", "'u'", "'x'", "'ci'"), columns=False),
    "LK": Domain("'a%'", ("NULL", "'%b_'", "'\\\\%'", "''", "'%'", "'_'", "'H%o%'"), columns=False),
    "CS": Domain("utf8mb4", ("latin1", "binary", "gbk", "utf16", "gb18030"), keyword=True),
    "COLL": Domain(
        "utf8mb4_bin",
        ("utf8mb4_general_ci", "utf8mb4_unicode_ci", "binary", "latin1_bin"),
        keyword=True,
    ),
    "TZ": Domain(
        "'+08:00'",
        ("NULL", "'+00:00'", "'-05:30'", "'+14:00'", "'+14:01'", "'UTC'", "'Asia/Shanghai'", "'SYSTEM'", "'bad'"),
        columns=False,
    ),
    "KEY": Domain("'key'", ("NULL", "''", "'a key that is longer than sixteen bytes'"), columns=False),
    "HS": Domain("'4F4B'", ("NULL", "'41'", "'fg'", "''", "'A'", "414243")),
    "B64": Domain("'SGVsbG8='", ("NULL", "'SGVsbG8'", "'===='", "''", "'not base64!'")),
    "UUIDS": Domain(
        "'6ccd780c-baba-1026-9564-5b8c656024db'",
        ("NULL", "'{6ccd780c-baba-1026-9564-5b8c656024db}'", "'6ccd780cbaba102695645b8c656024db'", "'not-a-uuid'", "''"),
    ),
    "BINU": Domain(
        "UUID_TO_BIN('6ccd780c-baba-1026-9564-5b8c656024db')",
        ("NULL", "UUID_TO_BIN('6ccd780c-baba-1026-9564-5b8c656024db', 1)", "X'00'", "'abc'"),
    ),
    "FMT": Domain(
        "'%Y-%m-%d %H:%i:%s.%f'",
        (
            "NULL",
            "'%W %M %D %y %a %b %e %j'",
            "'%U %u %V %v %X %x %w'",
            "'%p %r %T %l %k %h %I'",
            "'%c %M %% %Q'",
            "''",
            "'plain'",
        ),
        columns=False,
    ),
    "TFMT": Domain("'%H:%i:%s.%f'", ("NULL", "'%h %I %l %p %r %T'", "'%k %S %s'", "'%Y-%m-%d'", "''"), columns=False),
    "STRD": Domain(
        "'2024-02-29 13:45:56.123456'",
        ("NULL", "'2024-02-30'", "'29/02/2024'", "'Feb 29 2024'", "''", "'garbage'", "20240229"),
    ),
    "UNIT": Domain(
        "DAY",
        (
            "MICROSECOND",
            "SECOND",
            "MINUTE",
            "HOUR",
            "WEEK",
            "MONTH",
            "QUARTER",
            "YEAR",
            "SECOND_MICROSECOND",
            "MINUTE_MICROSECOND",
            "MINUTE_SECOND",
            "HOUR_MICROSECOND",
            "HOUR_SECOND",
            "HOUR_MINUTE",
            "DAY_MICROSECOND",
            "DAY_SECOND",
            "DAY_MINUTE",
            "DAY_HOUR",
            "YEAR_MONTH",
        ),
        keyword=True,
    ),
    "TUNIT": Domain(
        "DAY",
        ("MICROSECOND", "SECOND", "MINUTE", "HOUR", "WEEK", "MONTH", "QUARTER", "YEAR"),
        keyword=True,
    ),
    "IV": Domain("1", ("NULL", "-1", "0", "2.5", "'1:2'", "'1 2:3:4.5'", "'-1-2'", "2147483647", "'x'")),
    "BOOL": Domain("TRUE", ("NULL", "FALSE", "2", "-1", "0.5", "'a'", "'1'", "0e0")),
    "BASE": Domain("16", ("NULL", "10", "2", "36", "-10", "1", "37"), columns=False),
    "MODE": Domain("3", ("NULL", "0", "1", "2", "4", "5", "6", "7", "8", "-1"), columns=False),
    "BITS": Domain("256", ("NULL", "0", "224", "384", "512", "1"), columns=False),
    "PERIOD": Domain("202402", ("NULL", "199912", "2402", "0", "-1", "202413", "'202401'")),
    "XML": Domain(
        "'<a><b>1</b><b>2</b><c x=\"y\">3</c></a>'",
        ("NULL", "''", "'<a>text</a>'", "'<a><b>'", "'not xml'"),
        columns=False,
    ),
    "XPATH": Domain("'/a/b'", ("NULL", "'//b[2]'", "'/a/c/@x'", "'count(/a/b)'", "'bad['"), columns=False),
    "SQLTXT": Domain(
        "'SELECT 1'",
        (
            "NULL",
            "'select * from t where a = 1 and b in (1, 2)'",
            "'INSERT INTO t VALUES (1, ''x'')'",
            "''",
            "'not sql ((('",
        ),
        columns=False,
    ),
    "GFU": Domain("DATE", ("TIME", "DATETIME", "TIMESTAMP"), keyword=True),
    "GFS": Domain("'USA'", ("NULL", "'EUR'", "'JIS'", "'ISO'", "'INTERNAL'", "'bad'"), columns=False),
    "LOCALE": Domain("'de_DE'", ("NULL", "'en_US'", "'zh_CN'", "'xx_YY'"), columns=False),
    "LAMBDA": Domain("x -> x + 1", ("x -> x > 1", "x -> NULL", "x -> 'a'"), keyword=True),
    "MAP": Domain("MAP(1, 'a', 2, 'b')", ("NULL", "'{}'", "MAP('k', 1.5)", "MAP(1, NULL)"), columns=False),
}
CATEGORY_COLUMNS = {
    "core": CORE_COLUMNS,
    "json": SMALL_COLUMNS,
    "geo": SMALL_COLUMNS + ("c_geom", "c_varbinary"),
    "vec": SMALL_COLUMNS + ("c_vec", "c_arr"),
    "arr": SMALL_COLUMNS + ("c_arr", "c_vec"),
    "small": SMALL_COLUMNS,
    "none": (),
}


class Spec(object):
    def __init__(self, sql, kind, template=None, args=(), columns="core", statements=(),
                 setup=(), teardown=(), extra_templates=(), name_call=None, error_rows=None,
                 null_flag=False, sweep=(0,), skip_literals=()):
        self.sql = sql
        self.kind = kind
        self.template = template
        self.args = tuple(args)
        self.columns = columns
        self.statements = tuple(statements)
        self.setup = tuple(setup)
        self.teardown = tuple(teardown)
        self.extra_templates = tuple(extra_templates)
        self.name_call = name_call
        self.error_rows = dict(error_rows or {})
        self.null_flag = null_flag
        self.sweep = tuple(sweep)
        self.skip_literals = tuple(skip_literals)


PROBE_OPTIONS = ("error_rows", "null_flag", "sweep", "skip_literals")


def probe_options(options):
    return {key: options[key] for key in PROBE_OPTIONS if key in options}


def parse_argument(token):
    optional = token.endswith("?")
    repeat = token.endswith("*")
    key = token.rstrip("?*")
    typical = None
    if "=" in key:
        key, typical = key.split("=", 1)
    if key not in DOMAINS:
        raise GeneratorError("unknown domain {}".format(key))
    domain = DOMAINS[key]
    return {
        "key": key,
        "domain": domain,
        "typical": typical if typical is not None else domain.typical,
        "optional": optional,
        "repeat": repeat,
    }


def fn(name, *args, **options):
    arguments = [parse_argument(a) for a in args]
    return Spec(
        "{}({})".format(name, ", ".join(a["key"] + ("?" if a["optional"] else "*" if a["repeat"] else "") for a in arguments)),
        "call",
        template=name,
        args=args,
        columns=options.get("columns", "core"),
        extra_templates=options.get("extra", ()),
        setup=options.get("setup", ()),
        teardown=options.get("teardown", ()),
        name_call=options.get("name_call"),
        **probe_options(options)
    )


def op(template, *args, **options):
    return Spec(
        options.get("sql", template.format(*("x{}".format(i) for i in range(len(args))))),
        "template",
        template=template,
        args=args,
        columns=options.get("columns", "core"),
        extra_templates=options.get("extra", ()),
        setup=options.get("setup", ()),
        teardown=options.get("teardown", ()),
        name_call=options.get("name_call"),
        **probe_options(options)
    )


def stmts(sql, *statements, **options):
    return Spec(
        sql,
        "statements",
        statements=statements,
        setup=options.get("setup", ()),
        teardown=options.get("teardown", ()),
        name_call=options.get("name_call"),
    )


def props(sql, *expressions, **options):
    return Spec(
        sql,
        "statements",
        statements=["SELECT {} AS v".format(e) for e in expressions]
        + ["SELECT id, {} AS v FROM {} ORDER BY id".format(e, MATRIX_TABLE) for e in options.get("per_row", ())],
        setup=options.get("setup", ()),
        teardown=options.get("teardown", ()),
        name_call=options.get("name_call"),
    )


def internal(name, *statements, **options):
    return Spec(
        options.get("sql", "internal; name call and indirect statements"),
        "statements",
        statements=statements,
        setup=options.get("setup", ()),
        teardown=options.get("teardown", ()),
        name_call=name,
    )


def value_items(expression, null_flag=False):
    if null_flag:
        return "{e} AS v, ({e}) IS NULL AS n".format(e=expression)
    return "{} AS v".format(expression)


def literal_select(expression, null_flag=False):
    return "SELECT {}".format(value_items(expression, null_flag))


def column_select(expression, null_flag=False):
    return "SELECT id, {} FROM {} ORDER BY id".format(value_items(expression, null_flag), MATRIX_TABLE)


def row_split_selects(expression, rows, null_flag=False):
    items = value_items(expression, null_flag)
    statements = [
        "SELECT id, {} FROM {} WHERE id NOT IN ({}) ORDER BY id".format(
            items, MATRIX_TABLE, ", ".join(str(r) for r in rows)
        )
    ]
    statements += ["SELECT id, {} FROM {} WHERE id = {}".format(items, MATRIX_TABLE, r) for r in rows]
    return statements


def render_call(spec, values):
    if spec.kind == "call":
        return "{}({})".format(spec.template, ", ".join(values))
    return spec.template.format(*values)


def call_probes(spec):
    arguments = [parse_argument(a) for a in spec.args]
    probes = []
    seen = set()

    def add(statement):
        if statement not in seen:
            seen.add(statement)
            probes.append(statement)

    def value(values):
        add(literal_select(render_call(spec, values), spec.null_flag))

    def sweep(values, position, column):
        expression = render_call(spec, values)
        rows = spec.error_rows.get(position, {}).get(column, ())
        if rows:
            for statement in row_split_selects(expression, rows, spec.null_flag):
                add(statement)
        else:
            add(column_select(expression, spec.null_flag))

    if not arguments:
        value([])
        add(column_select(render_call(spec, []), spec.null_flag))
        if spec.kind == "call":
            add(literal_select(render_call(spec, ["1"])))
        return probes + list(spec.extra_templates)
    fixed = [a for a in arguments if not a["repeat"]]
    repeated = [a for a in arguments if a["repeat"]]
    repeated = repeated[0] if repeated else None
    if repeated is None:
        main = list(arguments)
    elif len(fixed) >= 2:
        main = fixed + [repeated]
    else:
        main = fixed + [repeated] * (2 - len(fixed))
    typical = [a["typical"] for a in main]
    for position, argument in enumerate(main):
        domain = argument["domain"]
        if position in spec.sweep:
            columns = () if domain.keyword else CATEGORY_COLUMNS[spec.columns]
        elif position > 0 and domain.columns and spec.columns != "none":
            columns = EXTRA_COLUMNS
        else:
            columns = ()
        literals = () if position in spec.skip_literals else domain.literals
        if position == 0:
            for column in columns:
                values = list(typical)
                values[position] = column
                sweep(values, position, column)
        for literal in literals:
            values = list(typical)
            values[position] = literal
            value(values)
        if position > 0:
            for column in columns:
                values = list(typical)
                values[position] = column
                sweep(values, position, column)
    if repeated is None:
        required = sum(1 for a in arguments if not a["optional"])
        for count in range(required, len(arguments) + 1):
            value([a["typical"] for a in arguments[:count]])
        if spec.kind == "call":
            add(literal_select(render_call(spec, [a["typical"] for a in arguments] + ["1"])))
    else:
        for extra in (0, 1, 3):
            shape = fixed + [repeated] * extra
            value([a["typical"] for a in shape])
    for extra in spec.extra_templates:
        add(extra)
    return probes


DOMAINS["CASTT"] = Domain(
    "CHAR",
    ("SIGNED", "UNSIGNED", "DECIMAL(10,2)", "DOUBLE", "DATETIME(6)", "DATE", "TIME", "JSON", "BINARY(4)", "YEAR", "FLOAT"),
    keyword=True,
)
DOMAINS["JPL"] = Domain(
    "'$.a'",
    ("'$'", "'$.b[1]'", "'$.b[2].c'", "'$.nosuch'", "'$[0]'", "'bad'"),
    columns=False,
)
DOMAINS["WSEP"] = Domain("','", ("NULL", "''", "'|'", "', '", "'中'"), columns=False)
DOMAINS["PT"] = Domain(
    "POINT(1, 2)",
    ("NULL", "POINT(0, 0)", "POINT(-1.5, 2.5)", "POINT(1, 2)", "ST_GeomFromText('LINESTRING(0 0,1 1)')", "1"),
    columns=False,
)
DOMAINS["LS"] = Domain(
    "LINESTRING(POINT(0, 0), POINT(1, 1))",
    ("NULL", "LINESTRING(POINT(0, 0), POINT(0, 1), POINT(1, 1), POINT(0, 0))", "POINT(1, 1)", "1"),
    columns=False,
)
DOMAINS["PG"] = Domain(
    "POLYGON(LINESTRING(POINT(0, 0), POINT(0, 1), POINT(1, 1), POINT(0, 0)))",
    ("NULL", "POINT(1, 1)", "LINESTRING(POINT(0, 0), POINT(1, 1))", "1"),
    columns=False,
)
DOMAINS["SLEEP"] = Domain("0", ("NULL", "0.01", "-1", "'x'", "0e0"), columns=False)
DOMAINS["LOCKNAME"] = Domain("'judge_lock_a'", ("NULL", "''", "'judge_lock_b'"), columns=False)
DOMAINS["GTID"] = Domain(
    "'3E11FA47-71CA-11E1-9E33-C80AA9429562:1-5'",
    (
        "NULL",
        "''",
        "'3E11FA47-71CA-11E1-9E33-C80AA9429562:2-3'",
        "'3E11FA47-71CA-11E1-9E33-C80AA9429562:1-3:5-7,4E11FA47-71CA-11E1-9E33-C80AA9429562:1'",
        "'not a gtid'",
    ),
    columns=False,
)
DOMAINS["SCN"] = Domain("1709214296654321000", ("NULL", "0", "1", "-1", "'x'", "4611686018427387904"), columns=False)
DOMAINS["CERT"] = Domain("'not a certificate'", ("NULL", "''"), columns=False)
DOMAINS["NAMEID"] = Domain("'n'", ("NULL", "'x y'", "1"), columns=False)
DOMAINS["ONEZERO"] = Domain("1", ("NULL", "0", "TRUE", "2"), columns=False)
DOMAINS["TRACE"] = Domain(
    "'YB42AC1E87E6-0005F2A5A13B2B7E-0-0'",
    ("NULL", "''", "'Y0-0000000000000000-0-0'", "'garbage'"),
    columns=False,
)
DOMAINS["SPLITN"] = Domain("2", ("NULL", "0", "1", "-1", "5", "'2'"), columns=False)
DOMAINS["AI"] = Domain("'judge_no_such_model'", ("NULL", "''", "'x'"), columns=False)
DOMAINS["PROMPT"] = Domain("'{0} and {1}'", ("NULL", "''", "'plain'", "'{0}'"), columns=False)
DOMAINS["GENCNT"] = Domain("3", ("NULL", "0", "1", "-1", "'2'"), columns=False)
DOMAINS["BMN"] = Domain("1", ("NULL", "0", "-1", "1e308"), columns=False)
DOMAINS["RANGEN"] = Domain("5", ("NULL", "0", "-3", "1.5", "'4'"), columns=False)


INDEXED_TABLE = "ti"
INDEXED_SETUP = (
    "CREATE TABLE {t} (id INT PRIMARY KEY, c_int INT, c_ubigint BIGINT UNSIGNED, c_double DOUBLE, "
    "c_dec_9_2 DECIMAL(9,2), c_dec_38_10 DECIMAL(38,10), c_varchar VARCHAR(64), c_char CHAR(16), "
    "c_date DATE, c_datetime6 DATETIME(6), c_timestamp TIMESTAMP(6) NULL DEFAULT NULL, c_time6 TIME(6), "
    "c_year YEAR, c_bit BIT(64), c_enum ENUM('a','b','c','中'), "
    "INDEX i_int (c_int), INDEX i_ubigint (c_ubigint), INDEX i_double (c_double), INDEX i_dec_9_2 (c_dec_9_2), "
    "INDEX i_dec_38_10 (c_dec_38_10), INDEX i_varchar (c_varchar), INDEX i_char (c_char), INDEX i_date (c_date), "
    "INDEX i_datetime6 (c_datetime6), INDEX i_timestamp (c_timestamp), INDEX i_time6 (c_time6), "
    "INDEX i_year (c_year), INDEX i_bit (c_bit), INDEX i_enum (c_enum))".format(t=INDEXED_TABLE),
    "INSERT INTO {t} SELECT id, c_int, c_ubigint, c_double, c_dec_9_2, c_dec_38_10, c_varchar, c_char, c_date, "
    "c_datetime6, c_timestamp, c_time6, c_year, c_bit, c_enum FROM {m}".format(t=INDEXED_TABLE, m=MATRIX_TABLE),
)
INDEXED_TEARDOWN = ("DROP TABLE {}".format(INDEXED_TABLE),)


def indexed(*conditions):
    return ["SELECT id FROM {} WHERE {} ORDER BY id".format(INDEXED_TABLE, c) for c in conditions]


FULLTEXT_SETUP = (
    "CREATE TABLE tf (id INT PRIMARY KEY, title VARCHAR(200), body TEXT, FULLTEXT INDEX ft_title (title) WITH PARSER space)",
    "INSERT INTO tf VALUES (1, 'apple banana cherry', 'red fruit'), (2, 'banana split dessert', 'yellow'), "
    "(3, 'cherry pie apple pie', 'baked'), (4, NULL, 'empty title'), (5, 'kiwi', 'green')",
)
FULLTEXT_TEARDOWN = ("DROP TABLE tf",)
FULLTEXT_QUERIES = (
    "SELECT id, MATCH(title) AGAINST('apple') AS v FROM tf ORDER BY id",
    "SELECT id FROM tf WHERE MATCH(title) AGAINST('banana cherry') ORDER BY id",
    "SELECT id, MATCH(title) AGAINST('+apple -pie' IN BOOLEAN MODE) AS v FROM tf ORDER BY id",
    "SELECT id, MATCH(title) AGAINST('nothing') AS v FROM tf ORDER BY id",
)
PARTITION_SETUP = (
    "CREATE TABLE tp (id INT PRIMARY KEY, v VARCHAR(10)) PARTITION BY HASH(id) PARTITIONS 3",
    "CREATE TABLE tpk (k VARCHAR(10) PRIMARY KEY, v INT) PARTITION BY KEY(k) PARTITIONS 2",
    "CREATE TABLE tpr (id INT, d DATE, PRIMARY KEY (id, d)) PARTITION BY RANGE COLUMNS(d) "
    "(PARTITION p0 VALUES LESS THAN ('2000-01-01'), PARTITION p1 VALUES LESS THAN (MAXVALUE))",
)
PARTITION_PROBES = (
    "INSERT INTO tp VALUES (1, 'a'), (2, 'b'), (3, 'c'), (4, 'd'), (-5, 'e')",
    "INSERT INTO tpk VALUES ('x', 1), ('y', 2), ('中', 3)",
    "INSERT INTO tpr VALUES (1, '1999-12-31'), (2, '2024-02-29')",
    "UPDATE tp SET id = id + 10 WHERE id = 1",
    "SELECT id, v FROM tp ORDER BY id",
    "SELECT k, v FROM tpk WHERE k = 'y'",
    "SELECT id, d FROM tpr WHERE d > '2000-01-01' ORDER BY id",
    "DELETE FROM tp WHERE id = 2",
    "SELECT id, v FROM tp ORDER BY id",
)
PARTITION_TEARDOWN = ("DROP TABLE tp", "DROP TABLE tpk", "DROP TABLE tpr")
ENUMSET_PROBES = tuple(
    column_select(e)
    for e in (
        "CONCAT(c_enum, '|', c_set)",
        "c_enum + 0",
        "c_set + 0",
        "c_enum = 'b'",
        "c_set = 'x,z'",
        "FIND_IN_SET('y', c_set)",
        "CAST(c_enum AS CHAR)",
        "CAST(c_set AS SIGNED)",
        "LENGTH(c_enum)",
        "c_enum IN ('a', 'c')",
        "IF(c_enum > 'a', c_enum, c_set)",
    )
)
SPARSE_VECTOR_SETUP = (
    "CREATE TABLE tsv (id INT PRIMARY KEY, sv SPARSEVECTOR)",
    "INSERT INTO tsv VALUES (1, '{1:0.1, 2:0.2, 3:0.3}'), (2, '{3:0.3, 2:0.2, 4:0.4}'), (3, '{5:1.5}'), (4, NULL)",
)
SPARSE_VECTOR_PROBES = (
    "CREATE VECTOR INDEX isv ON tsv (sv) WITH (type=sindi, distance=inner_product)",
    "INSERT INTO tsv VALUES (5, '{2:0.25, 7:0.75}')",
    "UPDATE tsv SET sv = '{4:0.5}' WHERE id = 1",
    "DELETE FROM tsv WHERE id = 2",
    "SELECT id, sv FROM tsv ORDER BY id",
    "SELECT id, NEGATIVE_INNER_PRODUCT(sv, '{3:1, 4:1}') AS v FROM tsv ORDER BY id",
)
SPARSE_VECTOR_TEARDOWN = ("DROP TABLE tsv",)
AGGREGATE_COLUMNS = ("c_int", "c_ubigint", "c_double", "c_dec_38_10", "c_varchar", "c_datetime6", "c_year", "c_bit")


def aggregate_probes(*functions):
    return [
        "SELECT {}({}) AS v FROM {}".format(f, c, MATRIX_TABLE)
        for f in functions
        for c in AGGREGATE_COLUMNS
    ]


SUBQUERY_COLUMNS = ("c_int", "c_double", "c_varchar", "c_datetime6")


def subquery_probes(operator):
    return [
        "SELECT id, {c} {o} {q} (SELECT {c} FROM {m} AS s WHERE s.id BETWEEN 2 AND 6) AS v FROM {m} ORDER BY id".format(
            c=c, o=operator, q=q, m=MATRIX_TABLE
        )
        for c in SUBQUERY_COLUMNS
        for q in ("ANY", "ALL")
    ] + [
        "SELECT {o} ANY (SELECT c_int FROM {m} WHERE id > 8) AS v".format(o="1 " + operator, m=MATRIX_TABLE),
        "SELECT {o} ALL (SELECT c_int FROM {m} WHERE id > 8) AS v".format(o="1 " + operator, m=MATRIX_TABLE),
        "SELECT NULL {o} ANY (SELECT c_int FROM {m}) AS v".format(o=operator, m=MATRIX_TABLE),
    ]


ERROR_ROWS = {
    "+": {0: {"c_bigint": (4,), "c_ubigint": (4,), "c_bit": (4,), "c_dec_65_30": (4,)}},
    "-": {0: {"c_bigint": (3,), "c_ubigint": (2, 3), "c_bit": (2, 3), "c_dec_65_30": (3,), "c_year": (2,)}},
    "*": {
        0: {"c_bigint": (3, 4), "c_ubigint": (4, 5), "c_bit": (4,), "c_double": (3, 4), "c_dec_65_30": (3, 4)},
        1: {"c_double": (3, 4)},
    },
    "/": {0: {"c_dec_65_30": (3, 4)}},
    "div": {0: {"c_double": (3, 4), "c_float": (3, 4), "c_dec_65_30": (3, 4)}, 1: {"c_double": (8,)}},
    "neg": {0: {"c_bigint": (3,), "c_ubigint": (4,), "c_bit": (4,)}},
    "abs": {0: {"c_bigint": (3,)}},
    "exp": {
        0: {
            "c_int": (4, 5),
            "c_bigint": (4, 5),
            "c_ubigint": (4, 5, 6),
            "c_float": (4,),
            "c_double": (4,),
            "c_dec_9_2": (4,),
            "c_dec_65_30": (4,),
            "c_varchar": (4, 7),
            "c_time6": (4, 5, 7),
            "c_year": (3, 4, 5, 6, 7, 8),
            "c_bit": (4, 8),
        }
    },
    "pow": {
        0: {
            "c_int": (3, 6, 8),
            "c_bigint": (3, 6, 8),
            "c_float": (3, 6),
            "c_double": (3, 6),
            "c_dec_9_2": (3, 6),
            "c_dec_65_30": (3, 6),
            "c_varchar": (6,),
            "c_text": (6,),
            "c_time6": (3, 6),
        },
        1: {"c_double": (4,), "c_varchar": (4, 7)},
    },
    "cot": {
        0: {
            "c_int": (2,),
            "c_bigint": (2,),
            "c_ubigint": (2,),
            "c_float": (2,),
            "c_double": (2,),
            "c_dec_9_2": (2,),
            "c_dec_65_30": (2,),
            "c_varchar": (2, 3, 5, 8),
            "c_varbinary": (2, 3, 4, 5, 6, 8),
            "c_text": (2, 3, 4, 5, 8),
            "c_time6": (2,),
            "c_year": (2,),
            "c_bit": (2,),
            "c_json": (2, 3, 4, 5, 6, 7),
        }
    },
    "from_unixtime": {
        0: {
            "c_bigint": (3, 4, 5, 6),
            "c_ubigint": (4, 5, 6),
            "c_float": (3, 4),
            "c_double": (3, 4),
            "c_dec_65_30": (3, 4),
            "c_varchar": (4,),
            "c_bit": (4,),
        }
    },
}
GEO_POINT_4326 = "ST_GeomFromText('POINT(116.4 39.9)', 4326)"
GEO_SQUARE = "ST_GeomFromText('POLYGON((0 0,2 0,2 2,0 2,0 0))')"


def bare_probes(call, column_call=None, *more):
    probes = [literal_select(call)]
    if column_call:
        probes.append(column_select(column_call))
    probes.extend(literal_select(m) for m in more)
    return probes


def build_specs():
    s = OrderedDict()
    s["+"] = op("{0} + {1}", "N", "N", error_rows=ERROR_ROWS["+"])
    s["+@T_OP_AGG_ADD"] = stmts(
        "no SQL form creates it in 834bbee1e; SUM and AVG over the matrix",
        *aggregate_probes("SUM")
    )
    s["&&"] = op("{0} AND {1}", "BOOL", "BOOL", extra=[literal_select("TRUE && NULL"), column_select("c_int && c_varchar")])
    s["arg_case"] = internal(
        "arg_case",
        column_select("CASE c_int WHEN 1 THEN 'one' WHEN -1 THEN 'minus one' WHEN NULL THEN 'null' ELSE 'other' END"),
        column_select("CASE c_varchar WHEN 'Hello, World!' THEN 1 WHEN '' THEN 2 END"),
        column_select("CASE c_date WHEN '2024-02-29' THEN c_date WHEN 20000101 THEN 'int' ELSE c_int END"),
        column_select("CASE c_double WHEN 0.5 THEN 'half' WHEN '0.5' THEN 'string half' ELSE c_double END"),
        literal_select("CASE 1 WHEN 1.0 THEN 'a' WHEN '1' THEN 'b' END"),
        literal_select("CASE NULL WHEN NULL THEN 'null matches' ELSE 'no match' END"),
        sql="CASE x WHEN ... END",
    )
    s["assign"] = stmts(
        "@v := x",
        literal_select("@j_a := 5"),
        literal_select("@j_a"),
        literal_select("@j_b := 'abc'"),
        literal_select("@j_c := 2.5e0 + @j_a"),
        literal_select("@j_d := NULL"),
        literal_select("@j_e := DATE'2024-02-29'"),
        literal_select("CONCAT(@j_a, @j_b, @j_c, IFNULL(@j_d, 'null'), @j_e)"),
        "SET @j_f := 18446744073709551615",
        literal_select("@j_f"),
        literal_select("@j_g := CAST('{\"k\": 1}' AS JSON)"),
    )
    s["between"] = op(
        "{0} BETWEEN {1} AND {2}", "A", "A=0", "A=100",
        extra=[column_select("c_date BETWEEN '2000-01-01' AND 20241231"), column_select("c_varchar BETWEEN 'A' AND 'Z'")],
    )
    s["&"] = op("{0} & {1}", "N", "N")
    s["case"] = op(
        "CASE WHEN {0} THEN 'true' WHEN {0} IS NULL THEN 'null' ELSE 'false' END", "A",
        extra=[
            column_select("CASE WHEN c_int > 0 THEN c_int WHEN c_int < 0 THEN c_double ELSE c_varchar END"),
            column_select("CASE WHEN id > 4 THEN c_datetime6 ELSE c_date END"),
            column_select("CASE WHEN id > 4 THEN c_dec_9_2 ELSE c_dec_65_30 END"),
            literal_select("CASE WHEN FALSE THEN 1 END"),
        ],
    )
    s["cast"] = op(
        "CAST({0} AS {1})", "A", "CASTT", sweep=(), skip_literals=(0,),
        sql="CAST(1 AS type) per target; the full source by target matrix is in the s3_cast files",
    )
    s["timestampadd"] = op("TIMESTAMPADD({2}, {1}, {0})", "T", "IV", "TUNIT")
    s["to_type"] = internal("to_type")
    s["char"] = fn(
        "char", "N*",
        extra=[
            literal_select("CHAR(77, 121, 83, 81, 76 USING utf8mb4)"),
            literal_select("CHAR(0xE4B8AD USING utf8mb4)"),
            literal_select("CHAR(256, 65536 USING latin1)"),
            literal_select("CHAR(0xFF USING utf8mb4)"),
            literal_select("CHARSET(CHAR(65))"),
        ],
    )
    s["convert"] = op(
        "CONVERT({0} USING {1})", "S", "CS",
        extra=[literal_select("CONVERT('abc', CHAR(2))"), literal_select("CONVERT(12.345, DECIMAL(5,1))")],
    )
    s["coalesce"] = fn("coalesce", "A*")
    s["nvl"] = fn("nvl", "A", "A")
    s["concat"] = fn(
        "concat", "S*",
        extra=[
            "SET sql_mode = 'PIPES_AS_CONCAT'",
            literal_select("'a' || 'b' || NULL"),
            column_select("c_varchar || c_int"),
            "SET sql_mode = '{}'".format(SESSION_SQL_MODE),
        ],
    )
    s["current_user"] = stmts(
        "CURRENT_USER(), CURRENT_USER",
        literal_select("CURRENT_USER()"),
        literal_select("CURRENT_USER"),
        literal_select("LENGTH(CURRENT_USER())"),
        literal_select("CURRENT_USER(1)"),
    )
    s["current_user_priv"] = fn("current_user_priv")
    s["year"] = fn("year", "T")
    s["/"] = op("{0} / {1}", "N", "N", error_rows=ERROR_ROWS["/"])
    s["/@T_OP_AGG_DIV"] = stmts("AVG(x), expanded to SUM / COUNT", *aggregate_probes("AVG"))
    s["="] = op("{0} = {1}", "A", "A")
    s["<=>"] = op("{0} <=> {1}", "A", "A")
    s["get_user_var"] = internal(
        "get_user_var",
        "SET @j_u1 = 42, @j_u2 = 'abc', @j_u3 = 2.5, @j_u4 = NULL, @j_u5 = DATE'2024-02-29'",
        literal_select("@j_u1"),
        literal_select("@j_u2"),
        literal_select("@j_u3"),
        literal_select("@j_u4"),
        literal_select("@j_u5"),
        literal_select("@j_never_set"),
        literal_select("@j_u1 + @j_u3"),
        column_select("@j_u1 + c_int"),
        column_select("CONCAT(@j_u2, c_varchar)"),
        sql="@var",
    )
    s[">="] = op("{0} >= {1}", "A", "A")
    s[">"] = op("{0} > {1}", "A", "A")
    s["greatest"] = fn("greatest", "A", "A*")
    s["hex"] = fn("hex", "A")
    s["password"] = fn("password", "S")
    s["in"] = op(
        "{0} IN ({1}, {2}, NULL)", "A", "A=1", "A='abc'",
        extra=[
            column_select("c_int IN (0, 1, -1, 20240229)"),
            column_select("c_varchar IN ('', 'Hello, World!', 0)"),
            column_select("c_date IN ('2024-02-29', 20000101, DATE'1999-12-31')"),
            column_select("(c_int, c_varchar) IN ((1, '2024-02-29 13:45:56.5'), (0, ''))"),
            column_select("c_int IN (SELECT c_int FROM tm AS s WHERE s.id < 4)"),
            literal_select("1 IN (1.0, '1', NULL)"),
            literal_select("NULL IN (1, 2)"),
            literal_select("'a' IN ('A', 'b')"),
        ],
    )
    s["not_in"] = op(
        "{0} NOT IN ({1}, {2}, NULL)", "A", "A=1", "A='abc'",
        extra=[
            column_select("c_int NOT IN (0, 1, -1)"),
            column_select("c_varchar NOT IN ('', 'Hello, World!')"),
            column_select("(c_int, c_double) NOT IN ((1, 0.5), (0, 0))"),
            column_select("c_int NOT IN (SELECT c_int FROM tm AS s WHERE s.id > 1)"),
            literal_select("3 NOT IN (1, 2)"),
        ],
        sql="x NOT IN (...)",
    )
    s["int2ip"] = fn("int2ip", "N", extra=[literal_select("INET_NTOA(3232235777)"), literal_select("INET_NTOA(4294967296)")])
    s["ip2int"] = fn("ip2int", "IP4")
    s["inet_aton"] = fn("inet_aton", "IP4")
    s["inet6_ntoa"] = fn("inet6_ntoa", "IPB")
    s["inet6_aton"] = fn("inet6_aton", "IP6", extra=[literal_select("HEX(INET6_ATON('fe80::1:2'))")])
    s["is_ipv4"] = fn("is_ipv4", "IP4")
    s["is_ipv6"] = fn("is_ipv6", "IP6")
    s["is_ipv4_mapped"] = fn("is_ipv4_mapped", "IPB")
    s["is_ipv4_compat"] = fn("is_ipv4_compat", "IPB")
    s["insert"] = fn("insert", "S", "I", "CNT", "S")
    s["is"] = op(
        "{0} IS NULL", "A",
        extra=[column_select("{} IS {}".format(c, t)) for c in ("c_int", "c_varchar", "c_double", "c_json") for t in ("TRUE", "FALSE", "UNKNOWN")],
        sql="x IS NULL | TRUE | FALSE | UNKNOWN",
    )
    s["is_not"] = op(
        "{0} IS NOT NULL", "A",
        extra=[column_select("{} IS NOT {}".format(c, t)) for c in ("c_int", "c_varchar", "c_datetime6") for t in ("TRUE", "FALSE", "UNKNOWN")],
        sql="x IS NOT NULL | TRUE | FALSE | UNKNOWN",
    )
    s["least"] = fn("least", "A", "A*")
    s["length"] = fn("length", "A", extra=[literal_select("OCTET_LENGTH('中文')")])
    s["<="] = op("{0} <= {1}", "A", "A")
    s["<"] = op("{0} < {1}", "A", "A")
    s["like"] = op(
        "{0} LIKE {1}", "S", "LK",
        extra=[
            column_select("c_varchar LIKE '%o%' ESCAPE '|'"),
            column_select("c_varchar LIKE 'H|%%' ESCAPE '|'"),
            column_select("c_varchar NOT LIKE '%1%'"),
            column_select("c_int LIKE '2%'"),
            column_select("c_datetime6 LIKE '2024%'"),
            column_select("c_varbinary LIKE 'a%'"),
            literal_select("'a' LIKE 'A'"),
            literal_select("'a' LIKE 'A' COLLATE utf8mb4_bin"),
            literal_select("'ab' LIKE 'a' ESCAPE 'xy'"),
        ],
    )
    s["lower"] = fn("lower", "S", extra=[literal_select("LCASE('ÀÉÎ ABC')")])
    s["-"] = op("{0} - {1}", "N", "N", error_rows=ERROR_ROWS["-"])
    s["-@T_OP_AGG_MINUS"] = stmts(
        "VARIANCE and STDDEV, expanded with the aggregate arithmetic operators",
        *aggregate_probes("VARIANCE", "STDDEV_SAMP")
    )
    s["%"] = op("{0} % {1}", "N", "N", extra=[literal_select("MOD(-7, 3)"), literal_select("MOD(7.5, -2)"), literal_select("MOD(1, 0)")])
    s["md5"] = fn("md5", "A")
    s["time"] = fn("time", "T")
    s["hour"] = fn("hour", "T")
    s["rpad"] = fn("rpad", "S", "CNT", "S")
    s["lpad"] = fn("lpad", "S", "CNT", "S")
    s["column_conv"] = internal(
        "column_conv",
        "CREATE TABLE tcc (id INT PRIMARY KEY, a TINYINT, b VARCHAR(3), c DECIMAL(5,2), d DATETIME(2), e ENUM('x','y'))",
        "INSERT INTO tcc VALUES (1, 127, 'abc', 123.45, '2024-02-29 13:45:56.125', 'x')",
        "INSERT INTO tcc VALUES (2, 128, 'abc', 1.005, '2024-02-29', 'y')",
        "INSERT INTO tcc VALUES (3, 1, 'abcd', 1, '2024-02-29', 'x')",
        "INSERT INTO tcc VALUES (4, '12', 12, '9.999', 20240229134556.999, 2)",
        "INSERT IGNORE INTO tcc VALUES (5, 300, 'abcdef', 1234.5, '2024-13-01', 'z')",
        "UPDATE tcc SET c = c * 10 WHERE id = 4",
        "SELECT * FROM tcc ORDER BY id",
        "DROP TABLE tcc",
        sql="INSERT and UPDATE into typed columns",
    )
    s["values"] = stmts(
        "VALUES(col) in INSERT ... ON DUPLICATE KEY UPDATE",
        "CREATE TABLE tval (id INT PRIMARY KEY, a INT, b VARCHAR(10))",
        "INSERT INTO tval VALUES (1, 10, 'x'), (2, 20, 'y')",
        "INSERT INTO tval VALUES (1, 11, 'z') ON DUPLICATE KEY UPDATE a = VALUES(a) + a, b = CONCAT(VALUES(b), b)",
        "INSERT INTO tval VALUES (3, NULL, NULL), (2, 5, 'w') ON DUPLICATE KEY UPDATE a = VALUES(a), b = VALUES(b)",
        "SELECT * FROM tval ORDER BY id",
        "SELECT id, VALUES(a) AS v FROM tval ORDER BY id",
        "DROP TABLE tval",
    )
    s["default"] = stmts(
        "DEFAULT(col)",
        "CREATE TABLE tdef (id INT PRIMARY KEY, a INT DEFAULT 42, b VARCHAR(10) DEFAULT 'dflt', "
        "c DATETIME(3) DEFAULT '2024-02-29 13:45:56.789', d DOUBLE DEFAULT 2.5, e INT)",
        "INSERT INTO tdef (id) VALUES (1)",
        "INSERT INTO tdef VALUES (2, DEFAULT, DEFAULT, DEFAULT, DEFAULT, DEFAULT)",
        "SELECT id, DEFAULT(a), DEFAULT(b), DEFAULT(c), DEFAULT(d), DEFAULT(e) FROM tdef ORDER BY id",
        "UPDATE tdef SET a = DEFAULT(a) + id, b = DEFAULT(b) WHERE id = 2",
        "SELECT * FROM tdef ORDER BY id",
        "SELECT DEFAULT(nosuch) FROM tdef",
        "DROP TABLE tdef",
    )
    s["div"] = op("{0} DIV {1}", "N", "N", error_rows=ERROR_ROWS["div"])
    s["*"] = op("{0} * {1}", "N", "N", error_rows=ERROR_ROWS["*"])
    s["*@T_OP_AGG_MUL"] = stmts(
        "VAR_POP and STDDEV_POP, expanded with the aggregate arithmetic operators",
        *aggregate_probes("VAR_POP", "STDDEV_POP", "STDDEV")
    )
    s["abs"] = fn("abs", "N", error_rows=ERROR_ROWS["abs"])
    s["uuid"] = props(
        "UUID() as stable properties",
        "LENGTH(UUID())",
        "IS_UUID(UUID())",
        "UUID() = UUID()",
        "SUBSTR(UUID(), 15, 1)",
        "CHARSET(UUID())",
        per_row=("LENGTH(UUID())",),
    )
    s["neg"] = op(
        "-({0})", "A", error_rows=ERROR_ROWS["neg"],
        extra=[literal_select("-(-9223372036854775808)"), literal_select("- -1"), literal_select("-'abc'"), column_select("-c_int")],
        sql="-x",
    )
    s["from_unixtime"] = fn("from_unixtime", "N", "FMT?", error_rows=ERROR_ROWS["from_unixtime"])
    s["not_between"] = op("{0} NOT BETWEEN {1} AND {2}", "A", "A=0", "A=100", sql="x NOT BETWEEN y AND z")
    s["!="] = op("{0} != {1}", "A", "A", extra=[column_select("c_int <> c_double"), column_select("c_varchar <> c_text")])
    s["!"] = op("NOT {0}", "A", extra=[column_select("!c_int"), literal_select("!NULL"), literal_select("NOT NOT 'a'")])
    s["||"] = op("{0} OR {1}", "BOOL", "BOOL", extra=[column_select("c_int || c_varchar"), literal_select("NULL OR TRUE")])
    s["^"] = op("{0} XOR {1}", "BOOL", "BOOL", sql="x XOR y")
    s["regexp"] = op(
        "{0} REGEXP {1}", "S", "RX",
        extra=[column_select("c_varchar RLIKE '[0-9]'"), column_select("c_varchar NOT REGEXP '^H'"), literal_select("'Abc' REGEXP BINARY 'a'")],
    )
    s["regexp_substr"] = fn("regexp_substr", "S", "RX", "I?", "I?", "RXM?")
    s["regexp_instr"] = fn("regexp_instr", "S", "RX", "I?", "I?", "ONEZERO?", "RXM?")
    s["regexp_replace"] = fn("regexp_replace", "S", "RX", "S='<X>'", "I?", "I?", "RXM?")
    s["regexp_like"] = fn("regexp_like", "S", "RX", "RXM?")
    s["sleep"] = fn("sleep", "SLEEP", columns="none")
    s["strcmp"] = fn("strcmp", "S", "S")
    s["substr"] = fn(
        "substr", "S", "I", "I?",
        extra=[
            literal_select("SUBSTRING('Hello' FROM 2)"),
            literal_select("SUBSTRING('Hello' FROM -3 FOR 2)"),
            literal_select("SUBSTRING('中文字符' FROM 2 FOR 2)"),
        ],
    )
    s["mid"] = fn("mid", "S", "I", "I?")
    s["substring_index"] = fn("substring_index", "S", "S=','", "I")
    s["sys_view_bigint_param"] = internal("sys_view_bigint_param")
    s["inner_trim"] = internal("inner_trim")
    s["trim"] = fn(
        "trim", "S",
        extra=[
            literal_select("TRIM(LEADING 'x' FROM 'xxabcxx')"),
            literal_select("TRIM(TRAILING 'x' FROM 'xxabcxx')"),
            literal_select("TRIM(BOTH 'xy' FROM 'xyxyabcxy')"),
            literal_select("TRIM('a' FROM 'aaa')"),
            literal_select("TRIM(LEADING FROM '  a  ')"),
            literal_select("TRIM(BOTH NULL FROM 'abc')"),
            literal_select("TRIM(BOTH '' FROM 'abc')"),
            column_select("TRIM(LEADING '1' FROM c_int)"),
            column_select("TRIM(BOTH ' ' FROM c_char)"),
        ],
    )
    s["ltrim"] = fn("ltrim", "S")
    s["space"] = fn("space", "CNT", columns="none")
    s["rtrim"] = fn("rtrim", "S")
    s["unhex"] = fn("unhex", "HS")
    s["upper"] = fn("upper", "S", null_flag=True, extra=[literal_select("UCASE('àéî abc ß')")])
    s["conv"] = fn(
        "conv", "A", "BASE", "BASE=10",
        extra=[literal_select("BIN(12)"), literal_select("OCT(12)"), literal_select("BIN(-1)"), literal_select("CONV('zz', 36, -10)")],
    )
    s["user"] = stmts(
        "USER(), SESSION_USER(), SYSTEM_USER()",
        literal_select("USER()"),
        literal_select("SESSION_USER()"),
        literal_select("SYSTEM_USER()"),
        literal_select("USER() = SESSION_USER()"),
    )
    s["date"] = fn("date", "T")
    s["month"] = fn("month", "T")
    s["monthname"] = fn("monthname", "T")
    s["soundex"] = fn("soundex", "S", extra=[literal_select("'Robert' SOUNDS LIKE 'Rupert'")])
    s["date_add"] = op(
        "DATE_ADD({0}, INTERVAL {1} {2})", "T", "IV", "UNIT",
        extra=[
            column_select("c_datetime6 + INTERVAL 1 DAY"),
            column_select("INTERVAL 1 MONTH + c_date"),
            column_select("ADDDATE(c_date, 31)"),
            literal_select("ADDDATE('2024-01-31', INTERVAL 1 MONTH)"),
            literal_select("DATE_ADD('9999-12-31', INTERVAL 1 DAY)"),
            literal_select("DATE_ADD(TIMESTAMP'2024-02-29 13:45:56', INTERVAL -1 MICROSECOND)"),
        ],
    )
    s["date_sub"] = op(
        "DATE_SUB({0}, INTERVAL {1} {2})", "T", "IV", "UNIT",
        extra=[
            column_select("c_datetime6 - INTERVAL 1 SECOND"),
            column_select("SUBDATE(c_date, 1)"),
            literal_select("DATE_SUB('1000-01-01', INTERVAL 1 DAY)"),
            literal_select("SUBDATE('2024-03-31', INTERVAL 1 MONTH)"),
        ],
    )
    s["subtime"] = fn("subtime", "T", "T=TIME'01:02:03.5'")
    s["addtime"] = fn("addtime", "T", "T=TIME'01:02:03.5'")
    s["datediff"] = fn("datediff", "T", "T=DATE'2000-01-01'")
    s["timestampdiff"] = op("TIMESTAMPDIFF({2}, {1}, {0})", "T", "T=TIMESTAMP'2000-02-29 00:00:00'", "TUNIT")
    s["timediff"] = fn("timediff", "T", "T=TIME'12:00:00'")
    s["period_diff"] = fn("period_diff", "PERIOD", "PERIOD=199912")
    s["period_add"] = fn("period_add", "PERIOD", "I")
    s["unix_timestamp"] = fn("unix_timestamp", "T?")
    s["maketime"] = fn("maketime", "N", "N=30", "N=15.5")
    s["makedate"] = fn("makedate", "N=2024", "N=60")
    s["extract"] = op("EXTRACT({1} FROM {0})", "T", "UNIT")
    s["to_days"] = fn("to_days", "T")
    s["position"] = op("POSITION({1} IN {0})", "S", "S='l'", sql="POSITION(y IN x)")
    s["from_days"] = fn("from_days", "N")
    s["date_format"] = fn("date_format", "T", "FMT")
    s["get_format"] = op("GET_FORMAT({0}, {1})", "GFU", "GFS")
    s["str_to_date"] = fn(
        "str_to_date", "STRD", "FMT",
        extra=[
            literal_select("STR_TO_DATE('29/02/2024 13:45:56', '%d/%m/%Y %H:%i:%s')"),
            literal_select("STR_TO_DATE('Feb 29 2024', '%b %d %Y')"),
            literal_select("STR_TO_DATE('13:45', '%H:%i')"),
            literal_select("STR_TO_DATE('2024', '%Y')"),
            literal_select("STR_TO_DATE('2024-02-30', '%Y-%m-%d')"),
            literal_select("STR_TO_DATE('20240229 134556.5', '%Y%m%d %H%i%s.%f')"),
        ],
    )
    s["cur_date"] = stmts(
        "CURDATE(), CURRENT_DATE (the name itself is internal)",
        literal_select("CURDATE()"),
        literal_select("CURRENT_DATE"),
        literal_select("CURRENT_DATE()"),
        literal_select("CURDATE() + 0"),
        column_select("DATEDIFF(CURDATE(), c_date)"),
        literal_select("cur_date()"),
    )
    s["curtime"] = stmts(
        "CURTIME([n]), CURRENT_TIME",
        *[literal_select("CURTIME({})".format(n)) for n in ("", "0", "3", "6", "7")]
        + [literal_select("CURRENT_TIME"), literal_select("CURRENT_TIME(2)"), literal_select("CURTIME() + 0")]
    )
    s["sysdate"] = props(
        "SYSDATE() as stable properties (it reads the clock, not the statement time)",
        "SYSDATE() > NOW()",
        "MICROSECOND(SYSDATE())",
        "MICROSECOND(SYSDATE(3)) % 1000",
        "LENGTH(SYSDATE(6))",
        "CHARSET(SYSDATE())",
    )
    s["current_timestamp"] = stmts(
        "NOW([n]), CURRENT_TIMESTAMP, LOCALTIME, LOCALTIMESTAMP",
        *[literal_select("NOW({})".format(n)) for n in ("", "0", "1", "3", "6", "7", "-1")]
        + [
            literal_select("CURRENT_TIMESTAMP"),
            literal_select("CURRENT_TIMESTAMP(3)"),
            literal_select("LOCALTIME"),
            literal_select("LOCALTIMESTAMP(6)"),
            literal_select("NOW() + 0"),
            literal_select("NOW(6) + 0"),
            literal_select("NOW() = NOW(0)"),
            literal_select("DATE_ADD(NOW(), INTERVAL -1 MICROSECOND)"),
            column_select("TIMESTAMPDIFF(SECOND, c_datetime6, NOW())"),
        ]
    )
    s["utc_timestamp"] = stmts(
        "UTC_TIMESTAMP([n])",
        *[literal_select("UTC_TIMESTAMP({})".format(n)) for n in ("", "0", "3", "6", "7")]
        + [literal_select("UTC_TIMESTAMP() + 0")]
    )
    s["utc_time"] = stmts(
        "UTC_TIME([n])",
        *[literal_select("UTC_TIME({})".format(n)) for n in ("", "0", "3", "6", "7")]
        + [literal_select("UTC_TIME() + 0")]
    )
    s["utc_date"] = stmts("UTC_DATE()", literal_select("UTC_DATE()"), literal_select("UTC_DATE() + 0"), literal_select("UTC_DATE(1)"))
    s["time_to_usec"] = fn("time_to_usec", "T")
    s["usec_to_time"] = fn("usec_to_time", "N")
    s["round"] = fn("round", "N", "I?")
    s["floor"] = fn("floor", "N")
    s["ceil"] = fn("ceil", "N")
    s["ceiling"] = fn("ceiling", "N")
    s["dump"] = fn("dump", "A", null_flag=True)
    s["repeat"] = fn("repeat", "S", "CNT")
    s["export_set"] = fn("export_set", "N", "S='Y'", "S='N'", "WSEP?", "I?")
    s["replace"] = fn("replace", "S", "S='l'", "S='L'")
    s["partition_hash"] = fn(
        "partition_hash", "A",
        setup=PARTITION_SETUP, extra=list(PARTITION_PROBES), teardown=PARTITION_TEARDOWN,
    )
    s["partition_key"] = fn("partition_key", "A")
    s["database"] = stmts("DATABASE(), SCHEMA()", literal_select("DATABASE()"), literal_select("SCHEMA()"), literal_select("DATABASE(1)"))
    s["nextval"] = stmts(
        "AUTO_INCREMENT columns",
        "CREATE TABLE tauto (id BIGINT AUTO_INCREMENT PRIMARY KEY, v INT)",
        "INSERT INTO tauto (v) VALUES (1), (2), (3)",
        "INSERT INTO tauto (id, v) VALUES (100, 4)",
        "INSERT INTO tauto (v) VALUES (5)",
        "INSERT INTO tauto (id, v) VALUES (0, 6)",
        "INSERT INTO tauto (id, v) VALUES (NULL, 7)",
        "SELECT id, v FROM tauto ORDER BY id",
        "DROP TABLE tauto",
        name_call="nextval",
    )
    s["last_insert_id"] = stmts(
        "LAST_INSERT_ID([x]) after AUTO_INCREMENT inserts",
        "CREATE TABLE tlast (id BIGINT AUTO_INCREMENT PRIMARY KEY, v INT)",
        literal_select("LAST_INSERT_ID()"),
        "INSERT INTO tlast (v) VALUES (1), (2)",
        literal_select("LAST_INSERT_ID()"),
        "INSERT INTO tlast (id, v) VALUES (50, 3)",
        literal_select("LAST_INSERT_ID()"),
        "INSERT INTO tlast (v) VALUES (4)",
        literal_select("LAST_INSERT_ID()"),
        literal_select("LAST_INSERT_ID(42)"),
        literal_select("LAST_INSERT_ID()"),
        literal_select("LAST_INSERT_ID(-1)"),
        literal_select("LAST_INSERT_ID(18446744073709551615)"),
        literal_select("LAST_INSERT_ID('abc')"),
        literal_select("LAST_INSERT_ID(NULL)"),
        literal_select("LAST_INSERT_ID(2.5)"),
        "DROP TABLE tlast",
    )
    s["instr"] = fn("instr", "S", "S='l'")
    s["lnnvl"] = fn("lnnvl", "BOOL")
    s["locate"] = fn("locate", "S='l'", "S", "I?")
    s["version"] = stmts("VERSION()", literal_select("VERSION()"), literal_select("LENGTH(VERSION()) > 0"))
    s["ob_version"] = stmts("OB_VERSION()", literal_select("OB_VERSION()"))
    s["connection_id"] = props("CONNECTION_ID() as stable properties", "CONNECTION_ID() > 0", "CONNECTION_ID() = CONNECTION_ID()")
    s["charset"] = fn("charset", "A")
    s["collation"] = fn("collation", "A")
    s["coercibility"] = fn("coercibility", "A")
    s["convert_TZ"] = fn("convert_tz", "T", "TZ", "TZ='+00:00'")
    s["set_collation"] = op(
        "{0} COLLATE {1}", "S", "COLL",
        extra=[literal_select("'a' COLLATE utf8mb4_bin = 'A'"), literal_select("'a' COLLATE latin1_bin")],
        name_call="set_collation",
    )
    s["reverse"] = fn("reverse", "S")
    s["right"] = fn("right", "S", "I")
    s["sign"] = fn("sign", "N")
    s["^@T_OP_BIT_XOR"] = op("{0} ^ {1}", "N", "N")
    s["sqrt"] = fn("sqrt", "N")
    s["log2"] = fn("log2", "N")
    s["log10"] = fn("log10", "N")
    s["pow"] = fn(
        "pow", "N", "N=0.5", error_rows=ERROR_ROWS["pow"],
        extra=[literal_select("POWER(2, 10)"), literal_select("POW(-8, 1/3)"), literal_select("POW(0, -1)")],
    )
    s["row_count"] = stmts(
        "ROW_COUNT() after DML",
        "CREATE TABLE trc (id INT PRIMARY KEY, v INT)",
        "INSERT INTO trc VALUES (1, 1), (2, 2), (3, 3)",
        literal_select("ROW_COUNT()"),
        "UPDATE trc SET v = v + 1 WHERE id >= 2",
        literal_select("ROW_COUNT()"),
        "UPDATE trc SET v = v WHERE id = 1",
        literal_select("ROW_COUNT()"),
        "INSERT INTO trc VALUES (1, 9) ON DUPLICATE KEY UPDATE v = 9",
        literal_select("ROW_COUNT()"),
        "DELETE FROM trc WHERE id = 3",
        literal_select("ROW_COUNT()"),
        literal_select("ROW_COUNT()"),
        "DROP TABLE trc",
    )
    s["found_rows"] = stmts(
        "FOUND_ROWS() after SELECT",
        "SELECT SQL_CALC_FOUND_ROWS id FROM tm ORDER BY id LIMIT 2",
        literal_select("FOUND_ROWS()"),
        "SELECT id FROM tm WHERE id > 5 ORDER BY id",
        literal_select("FOUND_ROWS()"),
        "SELECT SQL_CALC_FOUND_ROWS id FROM tm WHERE id > 100 LIMIT 1",
        literal_select("FOUND_ROWS()"),
    )
    s["agg_param_list"] = internal(
        "agg_param_list",
        "SELECT COUNT(DISTINCT c_int, c_varchar) AS v FROM tm",
        "SELECT COUNT(DISTINCT c_date, c_enum) AS v FROM tm",
        "SELECT GROUP_CONCAT(DISTINCT c_int, c_varchar ORDER BY id SEPARATOR '|') AS v FROM tm",
    )
    s["sys_privilege_check"] = fn(
        "sys_privilege_check", "S='table_acc'", "I=1", "S='test'", "S='tm'",
        columns="none",
    )
    s["field"] = fn("field", "A", "A=1", "A*")
    s["elt"] = fn("elt", "I", "S", "S*")
    s["nullif"] = fn("nullif", "A", "A")
    s["timestamp_nvl"] = fn("timestamp_nvl", "T", "T")
    s["DES_HEX_STR"] = fn("des_hex_str", "S")
    s["ascii"] = fn("ascii", "S")
    s["ord"] = fn("ord", "S")
    s["bit_count"] = fn("bit_count", "N")
    s["find_in_set"] = fn("find_in_set", "S='b'", "S='a,b,c'")
    s["left"] = fn("left", "S", "I")
    s["rand"] = stmts(
        "RAND(seed) values; RAND() as stable properties",
        *[literal_select("RAND({})".format(v)) for v in ("0", "1", "42", "-1", "NULL", "1.5", "'7'", "18446744073709551615")]
        + [
            column_select("RAND(42)"),
            column_select("RAND(c_int)"),
            column_select("RAND() BETWEEN 0 AND 1"),
            literal_select("RAND() < 1"),
        ]
    )
    s["make_set"] = fn("make_set", "N", "S='a'", "S*")
    s["estimate_ndv"] = internal(
        "estimate_ndv",
        *["SELECT APPROX_COUNT_DISTINCT({}) AS v FROM tm".format(c) for c in AGGREGATE_COLUMNS]
    )
    s["sys_op_opnsize"] = fn("sys_op_opnsize", "A")
    s["dayofmonth"] = fn("dayofmonth", "T")
    s["dayofweek"] = fn("dayofweek", "T")
    s["dayofyear"] = fn("dayofyear", "T")
    s["second"] = fn("second", "T")
    s["minute"] = fn("minute", "T")
    s["microsecond"] = fn("microsecond", "T")
    s["to_seconds"] = fn("to_seconds", "T")
    s["time_to_sec"] = fn("time_to_sec", "T")
    s["sec_to_time"] = fn("sec_to_time", "N")
    s["interval"] = fn("interval", "N", "N=1", "N*")
    s["truncate"] = fn("truncate", "N", "I")
    s["exp"] = fn("exp", "N", error_rows=ERROR_ROWS["exp"])
    s["any_value"] = fn("any_value", "A", extra=["SELECT c_bit, ANY_VALUE(c_int) AS v FROM tm WHERE id = 5 GROUP BY c_bit"])
    s["uuid_short"] = props("UUID_SHORT() as stable properties", "UUID_SHORT() > 0", "UUID_SHORT() < UUID_SHORT()")
    s["random_bytes"] = stmts(
        "RANDOM_BYTES(n) as stable properties",
        *[literal_select("LENGTH(RANDOM_BYTES({}))".format(n)) for n in ("1", "16", "1024")]
        + [literal_select("RANDOM_BYTES(0)"), literal_select("RANDOM_BYTES(1025)"), literal_select("RANDOM_BYTES(NULL)")]
    )
    s["ref_query"] = internal(
        "ref_query",
        "SELECT id, (SELECT c_int FROM tm AS s WHERE s.id = tm.id) AS v FROM tm ORDER BY id",
        "SELECT id, (SELECT MAX(c_double) FROM tm AS s WHERE s.id < tm.id) AS v FROM tm ORDER BY id",
        "SELECT (SELECT c_varchar FROM tm WHERE id = 5) AS v",
        "SELECT (SELECT c_varchar FROM tm WHERE id > 100) AS v",
        "SELECT (SELECT c_varchar FROM tm) AS v",
        "SELECT (SELECT 1, 2) AS v",
        sql="scalar subquery",
    )
    s["subquery_equal"] = internal("subquery_equal", *subquery_probes("="), sql="x = ANY | ALL (subquery)")
    s["subquery_not_equal"] = internal("subquery_not_equal", *subquery_probes("<>"), sql="x <> ANY | ALL (subquery)")
    s["subquery_null_safe_equal"] = internal("subquery_null_safe_equal", *subquery_probes("<=>"), sql="x <=> ANY | ALL (subquery)")
    s["subquery_greater_equal"] = internal("subquery_greater_equal", *subquery_probes(">="), sql="x >= ANY | ALL (subquery)")
    s["subquery_greater_than"] = internal("subquery_greater_than", *subquery_probes(">"), sql="x > ANY | ALL (subquery)")
    s["subquery_less_equal"] = internal("subquery_less_equal", *subquery_probes("<="), sql="x <= ANY | ALL (subquery)")
    s["subquery_less_than"] = internal("subquery_less_than", *subquery_probes("<"), sql="x < ANY | ALL (subquery)")
    s["remove_const"] = internal("remove_const")
    s["exists"] = stmts(
        "EXISTS (subquery)",
        "SELECT id, EXISTS (SELECT 1 FROM tm AS s WHERE s.c_int = tm.c_int AND s.id <> tm.id) AS v FROM tm ORDER BY id",
        "SELECT EXISTS (SELECT * FROM tm WHERE id > 100) AS v",
        "SELECT EXISTS (SELECT NULL) AS v",
        "SELECT id FROM tm WHERE EXISTS (SELECT 1 FROM tm AS s WHERE s.id = tm.id + 1) ORDER BY id",
    )
    s["not exists"] = stmts(
        "NOT EXISTS (subquery)",
        "SELECT id, NOT EXISTS (SELECT 1 FROM tm AS s WHERE s.c_double > tm.c_double) AS v FROM tm ORDER BY id",
        "SELECT NOT EXISTS (SELECT * FROM tm WHERE id > 100) AS v",
        "SELECT id FROM tm WHERE NOT EXISTS (SELECT 1 FROM tm AS s WHERE s.id = tm.id + 1) ORDER BY id",
    )
    s["char_length"] = fn("char_length", "S", extra=[literal_select("CHARACTER_LENGTH('中文😀')")])
    s["|"] = op("{0} | {1}", "N", "N")
    s["~"] = op("~{0}", "N", sql="~x")
    s["<<"] = op("{0} << {1}", "N", "I")
    s["bit_length"] = fn("bit_length", "S")
    s[">>"] = op("{0} >> {1}", "N", "I")
    s["ifnull"] = fn("ifnull", "A", "A")
    s["concat_ws"] = fn("concat_ws", "WSEP", "S", "S*")
    s["cmp_meta"] = fn("cmp_meta", "A")
    s["quote"] = fn("quote", "S", null_flag=True)
    s["pad"] = fn("pad", "S", "CNT", "S")
    s["host_ip"] = props("HOST_IP() as stable properties", "HOST_IP() IS NOT NULL", "LENGTH(HOST_IP()) > 0")
    s["rpc_port"] = props("RPC_PORT() as stable properties", "RPC_PORT() > 0")
    s["mysql_port"] = props("MYSQL_PORT() as stable properties", "MYSQL_PORT() > 0", "MYSQL_PORT() = @@port")
    s["get_sys_var"] = internal(
        "get_sys_var",
        literal_select("@@time_zone"),
        literal_select("@@session.sql_mode"),
        literal_select("@@div_precision_increment"),
        literal_select("@@autocommit"),
        literal_select("@@session.timestamp"),
        literal_select("@@collation_connection"),
        literal_select("@@global.div_precision_increment"),
        literal_select("@@nosuch_variable"),
        literal_select("@@time_zone = '+00:00'"),
        sql="@@variable",
    )
    s["last_trace_id"] = props("LAST_TRACE_ID() as stable properties", "LAST_TRACE_ID() IS NOT NULL", "LENGTH(LAST_TRACE_ID()) > 0")
    s["last_execution_id"] = props("LAST_EXECUTION_ID() as stable properties", "LAST_EXECUTION_ID() IS NOT NULL", "LAST_EXECUTION_ID() >= 0")
    s["doc_id"] = stmts(
        "DOC_ID() without arguments (an argument addresses a tablet) and fulltext queries",
        *([literal_select("DOC_ID()")] + list(FULLTEXT_QUERIES)),
        setup=FULLTEXT_SETUP,
        teardown=FULLTEXT_TEARDOWN
    )
    s["doc_length"] = fn("doc_length", "S", columns="none", setup=FULLTEXT_SETUP, extra=list(FULLTEXT_QUERIES[:1]), teardown=FULLTEXT_TEARDOWN)
    s["word_segment"] = fn("word_segment", "S", columns="none", extra=[literal_select("WS('a b c')")])
    s["word_count"] = fn("word_count", "S", columns="none")
    s["obj_access"] = internal("obj_access")
    s["enum_to_str"] = internal("enum_to_str", *ENUMSET_PROBES[:6], sql="ENUM and SET columns in string context")
    s["set_to_str"] = internal("set_to_str", *ENUMSET_PROBES[6:], sql="ENUM and SET columns in string context")
    s["enum_to_inner_type"] = internal(
        "enum_to_inner_type",
        column_select("c_enum = c_varchar"),
        column_select("GREATEST(c_enum, 'b')"),
        column_select("CASE WHEN id > 4 THEN c_enum ELSE c_set END"),
        sql="ENUM and SET columns mixed with other types",
    )
    s["set_to_inner_type"] = internal(
        "set_to_inner_type",
        column_select("c_set = c_varchar"),
        column_select("COALESCE(c_set, c_enum)"),
        column_select("c_set IN ('x', 'x,y,z')"),
        sql="ENUM and SET columns mixed with other types",
    )
    s["get_package_var"] = internal("get_package_var")
    s["get_subprogram_var"] = internal("get_subprogram_var")
    s["shadow_uk_project"] = internal(
        "shadow_uk_project",
        "CREATE TABLE tuk (a INT, b INT, UNIQUE KEY uk (b))",
        "INSERT INTO tuk VALUES (1, NULL), (2, NULL), (3, 3)",
        "INSERT INTO tuk VALUES (4, 3)",
        "SELECT a, b FROM tuk ORDER BY a",
        "SELECT a FROM tuk WHERE b IS NULL ORDER BY a",
        "DROP TABLE tuk",
        sql="unique key on a nullable column of a table without primary key",
    )
    s["user_define_function"] = internal(
        "user_define_function",
        "CREATE FUNCTION judge_udf1(x INT) RETURNS INT DETERMINISTIC RETURN x * 2 + 1",
        "CREATE FUNCTION judge_udf2(s VARCHAR(20)) RETURNS VARCHAR(40) DETERMINISTIC RETURN CONCAT('[', s, ']')",
        "CREATE PROCEDURE judge_proc1(IN x INT) SELECT x * 3 AS v",
        literal_select("judge_udf1(20)"),
        literal_select("judge_udf1(NULL)"),
        literal_select("judge_udf1('7')"),
        column_select("judge_udf1(c_int)"),
        column_select("judge_udf2(c_varchar)"),
        "CALL judge_proc1(14)",
        literal_select("judge_udf1()"),
        "DROP FUNCTION judge_udf1",
        "DROP FUNCTION judge_udf2",
        "DROP PROCEDURE judge_proc1",
        sql="stored functions and a procedure",
    )
    s["weekofyear"] = fn("weekofyear", "T")
    s["weekday"] = fn("weekday", "T")
    s["yearweek"] = fn("yearweek", "T", "MODE?")
    s["week"] = fn("week", "T", "MODE?")
    s["quarter"] = fn("quarter", "T")
    s["aes_decrypt"] = fn(
        "aes_decrypt", "S=AES_ENCRYPT('secret', 'key')", "KEY",
        extra=[
            literal_select("AES_DECRYPT(AES_ENCRYPT('中文', 'k'), 'k')"),
            literal_select("AES_DECRYPT(UNHEX('00'), 'key')"),
            column_select("AES_DECRYPT(AES_ENCRYPT(c_varchar, 'k2'), 'k2')"),
        ],
    )
    s["aes_encrypt"] = op(
        "HEX(AES_ENCRYPT({0}, {1}))", "S", "KEY",
        extra=bare_probes("AES_ENCRYPT('abc', 'key')", "AES_ENCRYPT(c_varchar, 'key')"),
        sql="AES_ENCRYPT(x, key)",
    )
    s["bool"] = stmts(
        "a non-boolean expression in a boolean context",
        *["SELECT id FROM tm WHERE {} ORDER BY id".format(c) for c in ("c_int", "c_double", "c_varchar", "c_datetime6", "c_json", "c_bit", "c_dec_9_2")]
        + [column_select("IF(c_varchar, 'true', 'false')"), column_select("c_text AND 1")]
    )
    s["sin"] = fn("sin", "N")
    s["cos"] = fn("cos", "N")
    s["tan"] = fn("tan", "N")
    s["cot"] = fn("cot", "N", error_rows=ERROR_ROWS["cot"])
    s["calc_partition_id"] = internal(
        "calc_partition_id", *(list(PARTITION_SETUP) + list(PARTITION_PROBES) + list(PARTITION_TEARDOWN)),
        sql="DML on partitioned tables",
    )
    s["calc_tablet_id"] = internal("calc_tablet_id", sql="DML on partitioned tables (see calc_partition_id)")
    s["calc_partition_tablet_id"] = internal("calc_partition_tablet_id", sql="DML on partitioned tables (see calc_partition_id)")
    s["pdml_partition_id"] = internal(
        "pdml_partition_id",
        "CREATE TABLE tpd (id INT PRIMARY KEY, v INT) PARTITION BY HASH(id) PARTITIONS 2",
        "INSERT /*+ ENABLE_PARALLEL_DML PARALLEL(2) */ INTO tpd SELECT id, c_int FROM tm",
        "UPDATE /*+ ENABLE_PARALLEL_DML PARALLEL(2) */ tpd SET v = v + 1 WHERE v < 100",
        "SELECT id, v FROM tpd ORDER BY id",
        "DROP TABLE tpd",
        sql="parallel DML on a partitioned table",
    )
    s["stmt_id"] = internal("stmt_id")
    s["radians"] = fn("radians", "N")
    s["JOIN_BLOOM_FILTER"] = internal(
        "join_bloom_filter",
        "SELECT /*+ PARALLEL(2) USE_HASH(a b) PX_JOIN_FILTER(b) */ a.id, b.id FROM tm a JOIN tm b ON a.c_int = b.c_int ORDER BY a.id, b.id",
        sql="join filter in a parallel hash join",
    )
    s["asin"] = fn("asin", "N")
    s["acos"] = fn("acos", "N")
    s["atan"] = fn("atan", "N", "N?")
    s["atan2"] = fn("atan2", "N", "N=1")
    s["to_outfile_row"] = internal("to_outfile_row", sql="SELECT ... INTO OUTFILE only; not generated, it writes files")
    s["format"] = fn("format", "N", "I", "LOCALE?")
    s["last_day"] = fn("last_day", "T")
    s["pi"] = fn("pi")
    s["log"] = fn("log", "N", "N?", extra=[literal_select("LN(2.718281828459045)"), literal_select("LN(0)"), literal_select("LOG(1, 2)")])
    s["time_format"] = fn("time_format", "T", "TFMT")
    s["timestamp"] = fn("timestamp", "T", "T?")
    s["output_pack"] = internal("output_pack")
    s["wrapper_inner"] = internal("wrapper_inner")
    s["degrees"] = fn("degrees", "N")
    s["validate_password_strength"] = fn("validate_password_strength", "S")
    s["day"] = fn("day", "T")
    s["benchmark"] = stmts(
        "BENCHMARK(n, expr) with small counts",
        *[literal_select("BENCHMARK({}, {})".format(n, e)) for n, e in (("1", "1"), ("3", "MD5('a')"), ("0", "1"), ("-1", "1"), ("NULL", "1"), ("2", "NULL"))]
    )
    s["weight_string"] = fn(
        "weight_string", "S",
        extra=[
            literal_select("HEX(WEIGHT_STRING('ab' AS CHAR(4)))"),
            literal_select("HEX(WEIGHT_STRING('ab' AS BINARY(4)))"),
            literal_select("HEX(WEIGHT_STRING('aB' COLLATE utf8mb4_bin))"),
            literal_select("HEX(WEIGHT_STRING('a' LEVEL 1))"),
            literal_select("HEX(WEIGHT_STRING('abc', 1, 2, 3, 0))"),
        ],
    )
    s["crc32"] = fn("crc32", "A")
    s["to_base64"] = fn("to_base64", "A")
    s["from_base64"] = fn("from_base64", "B64")
    s["pl_subquery_construct"] = internal("pl_subquery_construct")
    s["encode_sortkey"] = internal("encode_sortkey")
    s["hash"] = internal("hash")
    s["json_object"] = fn(
        "json_object", "S='k'", "A",
        extra=[
            literal_select("JSON_OBJECT()"),
            literal_select("JSON_OBJECT('a', 1, 'a', 2)"),
            literal_select("JSON_OBJECT('a')"),
            literal_select("JSON_OBJECT(NULL, 1)"),
            literal_select("JSON_OBJECT('b', 1, 'a', JSON_ARRAY(1, 2), 'c', JSON_OBJECT())"),
        ],
    )
    s["json_extract"] = fn(
        "json_extract", "J", "JP", "JP*", columns="json",
        extra=[
            column_select("c_json->'$.a'"),
            column_select("c_json->>'$.d'"),
            column_select("c_json->'$[1]'"),
            column_select("JSON_EXTRACT(c_json, '$.a', '$.d')"),
        ],
    )
    s["json_schema_valid"] = fn("json_schema_valid", "JS", "J", columns="json")
    s["json_schema_validation_report"] = fn("json_schema_validation_report", "JS", "J", columns="json")
    s["json_contains"] = fn("json_contains", "J", "J='1'", "JP?", columns="json")
    s["json_contains_path"] = fn("json_contains_path", "J", "ONEALL", "JP", "JP*", columns="json")
    s["json_depth"] = fn("json_depth", "J", columns="json")
    s["json_keys"] = fn("json_keys", "J", "JP?", columns="json")
    s["json_quote"] = fn("json_quote", "S")
    s["json_unquote"] = fn("json_unquote", "J")
    s["json_array"] = fn("json_array", "A*")
    s["json_overlaps"] = fn("json_overlaps", "J", "J='[1, 3]'", columns="json")
    s["json_remove"] = fn("json_remove", "J", "JP", "JP*", columns="json")
    s["json_search"] = fn("json_search", "J", "ONEALL", "S='%1%'", "S?", "JP?", columns="json")
    s["json_valid"] = fn("json_valid", "A")
    s["json_array_append"] = fn("json_array_append", "J", "JP='$.b'", "JV", columns="json")
    s["json_append"] = fn("json_append", "J", "JP='$.b'", "JV", columns="json")
    s["json_array_insert"] = fn("json_array_insert", "J", "JP='$.b[1]'", "JV", columns="json")
    s["json_value"] = op(
        "JSON_VALUE({0}, {1})", "J", "JPL", columns="json",
        extra=[
            literal_select("JSON_VALUE('{\"a\": \"2024-02-29\"}', '$.a' RETURNING DATE)"),
            literal_select("JSON_VALUE('{\"a\": 12.345}', '$.a' RETURNING DECIMAL(5,2))"),
            literal_select("JSON_VALUE('{\"a\": \"x\"}', '$.a' RETURNING SIGNED)"),
            literal_select("JSON_VALUE('{\"a\": \"x\"}', '$.a' RETURNING SIGNED DEFAULT 0 ON ERROR)"),
            literal_select("JSON_VALUE('{}', '$.a' DEFAULT 'none' ON EMPTY)"),
            literal_select("JSON_VALUE('{\"a\": \"x\"}', '$.a' RETURNING SIGNED ERROR ON ERROR)"),
        ],
        sql="JSON_VALUE(j, path [RETURNING ...] [ON EMPTY] [ON ERROR])",
    )
    s["json_replace"] = fn("json_replace", "J", "JP='$.a'", "JV", columns="json")
    s["json_type"] = fn("json_type", "J", columns="json", null_flag=True)
    s["json_length"] = fn("json_length", "J", "JP?", columns="json")
    s["json_insert"] = fn("json_insert", "J", "JP='$.z'", "JV", columns="json")
    s["json_storage_size"] = fn("json_storage_size", "J", columns="json")
    s["json_storage_free"] = fn("json_storage_free", "J", columns="json")
    s["json_set"] = fn("json_set", "J", "JP='$.a'", "JV", columns="json")
    s["json_merge_preserve"] = fn("json_merge_preserve", "J", "J='{\"a\": 2}'", "J*", columns="json")
    s["json_merge"] = fn("json_merge", "J", "J='[9]'", "J*", columns="json")
    s["json_merge_patch"] = fn("json_merge_patch", "J", "J='{\"a\": null, \"n\": 1}'", "J*", columns="json")
    s["json_pretty"] = fn("json_pretty", "J", columns="json")
    s["json_member_of"] = op(
        "{0} MEMBER OF ({1})", "A", "J='[1, \"abc\", 2.5, null]'", columns="json", sweep=(0, 1),
        sql="x MEMBER OF (j)",
    )
    s["extractvalue"] = fn("extractvalue", "XML", "XPATH")
    s["updatexml"] = fn("updatexml", "XML", "XPATH", "S='<d>new</d>'")
    s["sha"] = fn("sha", "A")
    s["sha1"] = fn(
        "sha1", "A", sweep=(), skip_literals=(0,), extra=[column_select("SHA1(c_varchar)")],
        sql="sha1(A); the same class and eval function as sha, so only the name is probed",
    )
    s["sha2"] = fn("sha2", "A", "BITS")
    s["compress"] = op(
        "HEX(COMPRESS({0}))", "S",
        extra=[literal_select("LENGTH(COMPRESS(REPEAT('a', 1000)))"), literal_select("COMPRESS('')"), literal_select("COMPRESS(NULL)")]
        + bare_probes("COMPRESS('abc')", "COMPRESS(c_varchar)"),
        sql="COMPRESS(x)",
    )
    s["uncompress"] = fn(
        "uncompress", "S=COMPRESS('hello hello hello')",
        extra=[literal_select("UNCOMPRESS(COMPRESS(REPEAT('ab', 10)))"), literal_select("UNCOMPRESS('not compressed')")],
    )
    s["uncompressed_length"] = fn("uncompressed_length", "S=COMPRESS('hello')")
    s["statement_digest"] = fn("statement_digest", "SQLTXT")
    s["statement_digest_text"] = fn("statement_digest_text", "SQLTXT")
    s["TIMESTAMP_TO_SCN"] = fn("timestamp_to_scn", "T")
    s["SCN_TO_TIMESTAMP"] = fn("scn_to_timestamp", "SCN")
    s["sql_mode_convert"] = fn("sql_mode_convert", "N=281018368")
    s["can_access_trigger"] = internal(
        "can_access_trigger",
        "CREATE TABLE ttrg (id INT PRIMARY KEY, v INT)",
        "CREATE TRIGGER judge_trg BEFORE INSERT ON ttrg FOR EACH ROW SET NEW.v = NEW.v * 10",
        "INSERT INTO ttrg VALUES (1, 2)",
        "SELECT * FROM ttrg ORDER BY id",
        "SELECT TRIGGER_NAME, EVENT_MANIPULATION, ACTION_TIMING, ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = 'test' ORDER BY TRIGGER_NAME",
        "DROP TABLE ttrg",
        sql="information_schema.TRIGGERS",
    )
    s["mysql_proc_info"] = internal(
        "mysql_proc_info",
        "CREATE PROCEDURE judge_proc2(IN a INT, OUT b VARCHAR(10)) SET b = CONCAT('v', a)",
        "SELECT name, type, param_list, returns FROM mysql.proc WHERE db = 'test' AND name = 'judge_proc2'",
        "SELECT ROUTINE_NAME, ROUTINE_TYPE, DATA_TYPE FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = 'test' AND ROUTINE_NAME = 'judge_proc2'",
        "DROP PROCEDURE judge_proc2",
        sql="mysql.proc and information_schema.ROUTINES",
    )
    s["inner_type_to_enumset"] = fn("inner_type_to_enumset", "A", "A=1", columns="none")
    s["errno"] = fn("errno", "I", columns="none")
    s["point"] = fn("point", "N", "N=2", columns="geo", extra=[literal_select("ST_AsText(POINT(1, 2))")])
    s["linestring"] = fn("linestring", "PT", "PT*", columns="none", extra=[literal_select("ST_AsText(LINESTRING(POINT(0, 0), POINT(1, 1)))")])
    s["multipoint"] = fn("multipoint", "PT", "PT*", columns="none", extra=[literal_select("ST_AsText(MULTIPOINT(POINT(0, 0), POINT(1, 1)))")])
    s["multilinestring"] = fn("multilinestring", "LS", "LS*", columns="none")
    s["polygon"] = fn("polygon", "LS", "LS*", columns="none", extra=[literal_select("ST_AsText(POLYGON(LINESTRING(POINT(0, 0), POINT(0, 1), POINT(1, 1), POINT(0, 0))))")])
    s["multipolygon"] = fn("multipolygon", "PG", "PG*", columns="none")
    s["geomcollection"] = fn("geomcollection", "PT", "PT*", columns="none", extra=[literal_select("ST_AsText(GEOMCOLLECTION())")])
    s["geometrycollection"] = fn("geometrycollection", "PT", "PT*", columns="none", extra=[literal_select("ST_AsText(GEOMETRYCOLLECTION(POINT(1, 1), LINESTRING(POINT(0, 0), POINT(1, 1))))")])
    s["st_geomfromtext"] = op(
        "ST_AsText(ST_GeomFromText({0}, {1}))", "WKT", "SRID", columns="geo",
        extra=[
            literal_select("ST_AsText(ST_GeomFromText('POINT(1 2)', 4326, 'axis-order=long-lat'))"),
            literal_select("ST_SRID(ST_GeomFromText('POINT(1 2)', 4326))"),
            literal_select("ST_GeomFromText('POINT(1 2)')"),
            literal_select("ST_GeomFromText('POINT(1 2)', 4326)"),
            literal_select("ST_GeomFromText('LINESTRING(0 0,1 1,2 0)', 3857)"),
        ],
        sql="ST_GeomFromText(wkt [, srid [, options]])",
    )
    s["st_area"] = fn(
        "st_area", "G", columns="geo",
        extra=[column_select("AREA(c_geom)"), literal_select("AREA({})".format(GEO_SQUARE))],
    )
    s["st_intersects"] = fn("st_intersects", "G", "G2", columns="geo")
    s["st_x"] = fn("st_x", "G", "N?", columns="geo")
    s["st_y"] = fn("st_y", "G", "N?", columns="geo")
    s["st_latitude"] = fn("st_latitude", "G=ST_GeomFromText('POINT(39.9 116.4)', 4326)", "N?", columns="geo")
    s["st_longitude"] = fn("st_longitude", "G=ST_GeomFromText('POINT(39.9 116.4)', 4326)", "N?", columns="geo")
    s["st_transform"] = op(
        "ST_AsText(ST_Transform({0}, {1}))", "G=ST_GeomFromText('POINT(39.9 116.4)', 4326)", "SRID", columns="geo",
        extra=bare_probes(
            "ST_Transform(ST_GeomFromText('POINT(39.9 116.4)', 4326), 4326)",
            None,
            "ST_Transform(ST_GeomFromText('POINT(39.9 116.4)', 4326), 3857)",
        ),
        sql="ST_Transform(g, srid)",
    )
    s["_st_transform"] = fn("_st_transform", "G=ST_GeomFromText('POINT(39.9 116.4)', 4326)", "SRID", "S?", columns="geo")
    s["_st_covers"] = fn("_st_covers", "G2", "G", columns="geo")
    s["_st_bestsrid"] = fn("_st_bestsrid", "G=ST_GeomFromText('POINT(39.9 116.4)', 4326)", "G?", columns="geo")
    s["st_astext"] = fn("st_astext", "G", columns="geo")
    s["st_aswkt"] = fn("st_aswkt", "G", columns="geo")
    s["st_buffer_strategy"] = fn(
        "st_buffer_strategy", "S='point_circle'", "I=8", columns="none",
        extra=[literal_select("ST_BUFFER_STRATEGY('{}'{})".format(k, a)) for k, a in (("join_round", ", 4"), ("join_miter", ", 2"), ("end_round", ", 4"), ("end_flat", ""), ("point_square", ""))],
    )
    s["st_buffer"] = op(
        "ST_AsText(ST_Buffer({0}, {1}))", "G", "N=1", columns="geo",
        extra=[literal_select("ST_AsText(ST_Buffer(ST_GeomFromText('POINT(0 0)'), 1, ST_BUFFER_STRATEGY('point_circle', 6)))")]
        + bare_probes(
            "ST_Buffer(ST_GeomFromText('POINT(1 2)'), 1)",
            "ST_Buffer(c_geom, 1)",
            "ST_Buffer({}, 1)".format(GEO_POINT_4326),
        ),
        sql="ST_Buffer(g, d [, strategies])",
    )
    s["spatial_cellid"] = fn("spatial_cellid", "G", columns="geo")
    s["spatial_mbr"] = fn("spatial_mbr", "G", columns="geo")
    s["_st_geomfromewkb"] = fn("_st_geomfromewkb", "WKB", "SRID?", columns="geo")
    s["st_geomfromwkb"] = op(
        "ST_AsText(ST_GeomFromWKB({0}, {1}))", "WKB", "SRID", columns="geo",
        extra=bare_probes(
            "ST_GeomFromWKB(X'0101000000000000000000F03F0000000000000040')",
            None,
            "ST_GeomFromWKB(X'0101000000000000000000F03F0000000000000040', 4326)",
        ),
        sql="ST_GeomFromWKB(wkb [, srid])",
    )
    s["st_geometryfromwkb"] = fn("st_geometryfromwkb", "WKB", "SRID?", columns="geo")
    s["_st_geomfromewkt"] = fn("_st_geomfromewkt", "S=CONCAT('SRID=4326', CHAR(59), 'POINT(1 2)')", columns="geo")
    s["_st_asewkt"] = fn("_st_asewkt", "G", columns="geo")
    s["st_srid"] = fn("st_srid", "G", "SRID?", columns="geo")
    s["st_distance"] = fn("st_distance", "G", "G2", "S?", columns="geo")
    s["_st_geogfromtext"] = fn("_st_geogfromtext", "WKT", columns="geo")
    s["_st_geographyfromtext"] = fn("_st_geographyfromtext", "WKT", columns="geo")
    s["_st_setsrid"] = fn("_st_setsrid", "G", "SRID", columns="geo")
    s["st_geometryfromtext"] = fn("st_geometryfromtext", "WKT", "SRID?", columns="geo")
    s["_st_point"] = fn("_st_point", "N", "N=2", "SRID?", columns="geo")
    s["st_isvalid"] = fn("st_isvalid", "G", columns="geo")
    s["_st_buffer"] = fn("_st_buffer", "G", "N=1", "S?", columns="geo")
    s["_st_dwithin"] = fn("_st_dwithin", "G", "G2", "N=1", columns="geo")
    s["st_aswkb"] = op(
        "HEX(ST_AsWKB({0}))", "G", columns="geo",
        extra=bare_probes("ST_AsWKB(ST_GeomFromText('POINT(1 2)'))", "ST_AsWKB(c_geom)", "ST_AsWKB({})".format(GEO_POINT_4326)),
        sql="ST_AsWKB(g)",
    )
    s["_st_asewkb"] = op(
        "HEX(_ST_AsEWKB({0}))", "G", columns="geo",
        extra=bare_probes("_ST_AsEWKB(ST_GeomFromText('POINT(1 2)'))", "_ST_AsEWKB(c_geom)", "_ST_AsEWKB({})".format(GEO_POINT_4326)),
        sql="_ST_AsEWKB(g)",
    )
    s["st_asbinary"] = op(
        "HEX(ST_AsBinary({0}))", "G", columns="geo",
        extra=bare_probes("ST_AsBinary(ST_GeomFromText('POINT(1 2)'))", "ST_AsBinary(c_geom)", "ST_AsBinary({})".format(GEO_POINT_4326)),
        sql="ST_AsBinary(g)",
    )
    s["st_distance_sphere"] = fn("st_distance_sphere", "G=ST_GeomFromText('POINT(116.4 39.9)', 4326)", "G2=ST_GeomFromText('POINT(121.5 31.2)', 4326)", "N?", columns="geo")
    s["st_contains"] = fn("st_contains", "G2", "G", columns="geo")
    s["st_within"] = fn("st_within", "G", "G2", columns="geo")
    s["format_bytes"] = fn("format_bytes", "N")
    s["format_pico_time"] = fn("format_pico_time", "N")
    s["uuid_to_bin"] = op(
        "HEX(UUID_TO_BIN({0}, {1}))", "UUIDS", "ONEZERO",
        extra=bare_probes(
            "UUID_TO_BIN('6ccd780c-baba-1026-9564-5b8c656024db')",
            None,
            "UUID_TO_BIN('6ccd780c-baba-1026-9564-5b8c656024db', 1)",
        ),
        sql="UUID_TO_BIN(u [, swap])",
    )
    s["is_uuid"] = fn("is_uuid", "UUIDS")
    s["bin_to_uuid"] = fn("bin_to_uuid", "BINU", "ONEZERO?")
    s["name_const"] = op("NAME_CONST({1}, {0})", "A", "NAMEID", sql="NAME_CONST(name, value)")
    s["dayname"] = fn("dayname", "T")
    s["des_decrypt"] = fn("des_decrypt", "S=DES_ENCRYPT('secret', 'key')", "KEY?")
    s["des_encrypt"] = op(
        "HEX(DES_ENCRYPT({0}, {1}))", "S", "KEY",
        extra=bare_probes("DES_ENCRYPT('abc', 'key')", "DES_ENCRYPT(c_varchar, 'key')"),
        sql="DES_ENCRYPT(x [, key])",
    )
    s["encrypt"] = fn("encrypt", "S", "S='ab'")
    s["current_scn"] = props("CURRENT_SCN() as stable properties", "CURRENT_SCN() > 0", "CURRENT_SCN() IS NOT NULL")
    s["encode"] = op(
        "HEX(ENCODE({0}, {1}))", "S", "KEY",
        extra=bare_probes("ENCODE('abc', 'key')", "ENCODE(c_varchar, 'key')"),
        sql="ENCODE(x, key)",
    )
    s["decode"] = fn("decode", "S=ENCODE('secret', 'key')", "KEY")
    s["icu_version"] = fn("icu_version")
    s["generator"] = stmts(
        "TABLE(GENERATOR(n))",
        "SELECT COUNT(*) AS v FROM TABLE(GENERATOR(5))",
        "SELECT COUNT(*) AS v FROM TABLE(GENERATOR(0))",
        "SELECT COUNT(*) AS v FROM TABLE(GENERATOR(-1))",
        "SELECT COUNT(*) AS v FROM TABLE(GENERATOR(NULL))",
        "SELECT COUNT(*) AS v FROM TABLE(GENERATOR('3'))",
        literal_select("GENERATOR(3)"),
    )
    s["zipf"] = fn("zipf", "N=1.5", "GENCNT=10", "N=42", columns="none")
    s["normal"] = fn("normal", "N=0", "N=1", "N=42", columns="none")
    s["uniform"] = fn("uniform", "N=1", "N=10", "N=42", columns="none")
    s["random"] = stmts(
        "RANDOM(seed) values; RANDOM() as stable properties",
        *[literal_select("RANDOM({})".format(v)) for v in ("0", "1", "42", "-1", "NULL", "'7'")]
        + [
            "SELECT RANDOM(42) AS v FROM TABLE(GENERATOR(3)) ORDER BY v",
            literal_select("RANDOM() IS NOT NULL"),
            literal_select("RANDSTR(8, 42)"),
            literal_select("UNIFORM(1, 100, RANDOM(7))"),
        ]
    )
    s["randstr"] = fn("randstr", "GENCNT=8", "N=42", columns="none")
    s["prefix_pattern"] = fn("prefix_pattern", "S='abc%'", "I=2", "S='\\\\'", columns="none")
    s["_st_numinteriorrings"] = fn("_st_numinteriorrings", "G", columns="geo")
    s["_st_iscollection"] = fn("_st_iscollection", "G", columns="geo")
    s["st_equals"] = fn("st_equals", "G", "G2", columns="geo")
    s["_st_touches"] = fn("_st_touches", "G", "G2", columns="geo")
    s["align_date4cmp"] = internal(
        "align_date4cmp",
        *(list(INDEXED_SETUP) + indexed(
            "c_date = '2024-02-29 00:00:00'",
            "c_date > 20240228.5",
            "c_date < '2000-01-01 12:00:00'",
            "c_date >= TIMESTAMP'1999-12-31 23:59:59.5'",
            "c_date IN ('2024-02-29', 19991231)",
            "c_datetime6 = DATE'2024-02-29'",
            "c_datetime6 > 20231231235959.4",
        ) + list(INDEXED_TEARDOWN)),
        sql="date and datetime comparisons on indexed columns",
    )
    s["json_query"] = op(
        "JSON_QUERY({0}, {1})", "J", "JPL", columns="json",
        extra=[
            literal_select("JSON_QUERY('{\"a\": [1, 2]}', '$.a' WITH WRAPPER)"),
            literal_select("JSON_QUERY('{\"a\": [1, 2]}', '$.a[*]' WITH CONDITIONAL WRAPPER)"),
            literal_select("JSON_QUERY('{\"a\": 1}', '$.b' EMPTY ON EMPTY)"),
            literal_select("JSON_QUERY('{\"a\": 1}', '$.a' RETURNING JSON PRETTY)"),
            "CREATE TABLE tmv (id INT PRIMARY KEY, j JSON, INDEX mvi ((CAST(j->'$.a' AS UNSIGNED ARRAY))))",
            "INSERT INTO tmv VALUES (1, '{\"a\": [1, 2]}'), (2, '{\"a\": [3]}'), (3, '{\"a\": []}')",
            "SELECT id FROM tmv WHERE 2 MEMBER OF (j->'$.a') ORDER BY id",
            "SELECT id FROM tmv WHERE JSON_CONTAINS(j->'$.a', '[3]') ORDER BY id",
            "DROP TABLE tmv",
        ],
        sql="JSON_QUERY(j, path ...) and CAST(... AS ... ARRAY)",
    )
    s["bm25"] = fn(
        "bm25", "BMN=3", "BMN=10", "BMN=5", "BMN=1.5", "BMN=4.5", "BMN=2", columns="none",
        setup=FULLTEXT_SETUP, extra=list(FULLTEXT_QUERIES[:2]), teardown=FULLTEXT_TEARDOWN,
    )
    s["get_lock"] = stmts(
        "GET_LOCK, IS_FREE_LOCK, IS_USED_LOCK, RELEASE_LOCK, RELEASE_ALL_LOCKS",
        literal_select("GET_LOCK('judge_lock_a', 0)"),
        literal_select("GET_LOCK('judge_lock_a', 0)"),
        literal_select("GET_LOCK('judge_lock_b', 0)"),
        literal_select("GET_LOCK(NULL, 0)"),
        literal_select("GET_LOCK('', 0)"),
        literal_select("GET_LOCK('judge_lock_c', -1)"),
    )
    s["is_free_lock"] = fn("is_free_lock", "LOCKNAME", columns="none")
    s["is_used_lock"] = stmts(
        "IS_USED_LOCK(name) as stable properties",
        literal_select("IS_USED_LOCK('judge_lock_a') IS NOT NULL"),
        literal_select("IS_USED_LOCK('judge_lock_a') = CONNECTION_ID()"),
        literal_select("IS_USED_LOCK('judge_lock_none')"),
        literal_select("IS_USED_LOCK(NULL)"),
    )
    s["release_lock"] = fn("release_lock", "LOCKNAME", columns="none")
    s["release_all_locks"] = stmts(
        "RELEASE_ALL_LOCKS()",
        literal_select("GET_LOCK('judge_lock_d', 0)"),
        literal_select("GET_LOCK('judge_lock_d', 0)"),
        literal_select("RELEASE_ALL_LOCKS()"),
        literal_select("RELEASE_ALL_LOCKS()"),
    )
    s["extract_cert_expired_time"] = fn("extract_cert_expired_time", "CERT", columns="none")
    s["ob_transaction_id"] = props(
        "OB_TRANSACTION_ID() as stable properties",
        "OB_TRANSACTION_ID() >= 0",
        "OB_TRANSACTION_ID() IS NOT NULL",
    )
    s["inner_row_cmp_value"] = internal(
        "inner_row_cmp_value",
        *(list(INDEXED_SETUP) + indexed(
            "(c_dec_9_2, id) > (1.055, 3)",
            "(c_dec_9_2, id) <= (42.5, 5)",
            "(c_dec_38_10, c_int) = (0.5, 1)",
        ) + list(INDEXED_TEARDOWN)),
        sql="row comparisons with decimal constants on indexed columns",
    )
    s["PUSHDOWN_TOPN_FILTER"] = internal(
        "pushdown_topn_filter",
        "SELECT id, c_int FROM tm ORDER BY c_int, id LIMIT 3",
        "SELECT id, c_varchar FROM tm ORDER BY c_varchar DESC, id LIMIT 2",
        "SELECT a.id, b.id FROM tm a JOIN tm b ON a.c_int = b.c_int ORDER BY a.c_double, a.id, b.id LIMIT 3",
        sql="ORDER BY ... LIMIT",
    )
    s["_st_makeenvelope"] = fn("_st_makeenvelope", "N=0", "N=0", "N=1", "N=1", "SRID?", columns="none")
    s["_st_clipbybox2d"] = fn("_st_clipbybox2d", "G", "G2", columns="geo")
    s["_st_pointonsurface"] = fn("_st_pointonsurface", "G", columns="geo")
    s["_st_geometrytype"] = fn("_st_geometrytype", "G", columns="geo")
    s["st_crosses"] = fn("st_crosses", "G", "G2", columns="geo")
    s["st_overlaps"] = fn("st_overlaps", "G", "G2", columns="geo")
    s["st_union"] = op(
        "ST_AsText(ST_Union({0}, {1}))", "G", "G2", columns="geo",
        extra=bare_probes(
            "ST_Union(ST_GeomFromText('POINT(1 2)'), {})".format(GEO_SQUARE),
            "ST_Union(c_geom, {})".format(GEO_SQUARE),
            "ST_Union({}, ST_GeomFromText('POINT(121.5 31.2)', 4326))".format(GEO_POINT_4326),
        ),
        sql="ST_Union(g1, g2)",
    )
    s["st_length"] = fn("st_length", "G", "S?", columns="geo")
    s["st_difference"] = op(
        "ST_AsText(ST_Difference({0}, {1}))", "G", "G2", columns="geo",
        extra=bare_probes(
            "ST_Difference({}, ST_GeomFromText('POINT(1 1)'))".format(GEO_SQUARE),
            "ST_Difference(c_geom, {})".format(GEO_SQUARE),
            "ST_Difference({}, ST_GeomFromText('POINT(121.5 31.2)', 4326))".format(GEO_POINT_4326),
        ),
        sql="ST_Difference(g1, g2)",
    )
    s["st_asgeojson"] = fn("st_asgeojson", "G", "I?", "I?", columns="geo")
    s["st_centroid"] = op(
        "ST_AsText(ST_Centroid({0}))", "G", columns="geo",
        extra=[literal_select("ST_AsText(CENTROID({}))".format(GEO_SQUARE))]
        + bare_probes(
            "ST_Centroid({})".format(GEO_SQUARE),
            "ST_Centroid(c_geom)",
            "ST_Centroid(ST_GeomFromText('POLYGON((0 0,0 1,1 1,1 0,0 0))', 4326))",
        ),
        sql="ST_Centroid(g)",
    )
    s["st_symdifference"] = op(
        "ST_AsText(ST_SymDifference({0}, {1}))", "G", "G2", columns="geo",
        extra=bare_probes(
            "ST_SymDifference({}, ST_GeomFromText('POLYGON((1 1,3 1,3 3,1 3,1 1))'))".format(GEO_SQUARE),
            "ST_SymDifference(c_geom, {})".format(GEO_SQUARE),
            "ST_SymDifference({}, ST_GeomFromText('POINT(121.5 31.2)', 4326))".format(GEO_POINT_4326),
        ),
        sql="ST_SymDifference(g1, g2)",
    )
    s["_st_asmvtgeom"] = fn("_st_asmvtgeom", "G", "G2", "I?", "I?", columns="geo")
    s["_st_makevalid"] = fn("_st_makevalid", "G", columns="geo")
    s["_st_geohash"] = fn("_st_geohash", "G=ST_GeomFromText('POINT(116.4 39.9)', 4326)", "I?", columns="geo")
    s["_st_makepoint"] = fn("_st_makepoint", "N", "N=2", "N?", columns="geo")
    s["current_role"] = stmts("CURRENT_ROLE()", literal_select("CURRENT_ROLE()"), literal_select("CURRENT_ROLE(1)"))
    s["array"] = fn(
        "array", "A", "A*", columns="arr",
        extra=[
            literal_select("[1, 2, 3]"),
            literal_select("[ARRAY_REMOVE([1], 1), NULL, [1, 2]]"),
            literal_select("[[1], [2, 3]]"),
            literal_select("['a', 1]"),
        ],
    )
    s["demote_cast"] = internal(
        "demote_cast",
        *(list(INDEXED_SETUP) + indexed(
            "c_int > 1.5",
            "c_int = 2.0000000001",
            "c_int <= 20240229.9",
            "c_ubigint < -1.5",
            "c_dec_9_2 = 42.505",
            "c_dec_9_2 > 1.049999",
            "c_int BETWEEN 0.5 AND 1.5",
            "c_int IN (1.0, 2.5)",
        ) + list(INDEXED_TEARDOWN)),
        sql="index range comparisons with constants of a wider type",
    )
    s["range_placement"] = internal(
        "range_placement",
        *(list(INDEXED_SETUP) + indexed(
            "c_int >= 1e10",
            "c_int < -3000000000",
            "c_ubigint > 1.8446744073709552e19",
            "c_dec_9_2 < 99999999.5",
        ) + list(INDEXED_TEARDOWN)),
        sql="index range comparisons with out-of-range constants",
    )
    s["map"] = fn(
        "map", "A", "A='v'", columns="small",
        extra=[
            literal_select("MAP(NULL, NULL)"),
            literal_select("MAP(1, 'a', 1, 'b')"),
            literal_select("MAP(1)"),
            literal_select("MAP_KEYS(MAP('a', 1, 'b', 2))"),
        ],
    )
    for name in (
        "vec_ivf_center_id",
        "vec_ivf_center_vector",
        "vec_ivf_flat_data_vector",
        "vec_ivf_sq8_data_vector",
        "vec_ivf_meta_id",
        "vec_ivf_meta_vector",
        "vec_ivf_pq_center_id",
        "vec_ivf_pq_center_ids",
        "vec_ivf_pq_center_vector",
        "vec_vid",
        "vec_type",
        "vec_vector",
        "vec_scn",
        "vec_key",
        "vec_data",
        "vec_chunk",
        "embedded_vec",
    ):
        s[name] = stmts(
            "{}() only: a vector index internal whose arguments address tablets".format(name),
            literal_select("{}()".format(name)),
        )
    s["spiv_dim"] = stmts(
        "the generated columns of a sparse vector index: CREATE VECTOR INDEX on a filled table, then DML",
        *(list(SPARSE_VECTOR_PROBES) + [literal_select("spiv_dim()")]),
        setup=SPARSE_VECTOR_SETUP,
        teardown=SPARSE_VECTOR_TEARDOWN
    )
    s["spiv_value"] = stmts(
        "spiv_value() only; the sparse vector index statements are under spiv_dim",
        literal_select("spiv_value()"),
    )
    s["l2_distance"] = fn("l2_distance", "V", "V", columns="vec")
    s["cosine_distance"] = fn("cosine_distance", "V", "V", columns="vec")
    s["inner_product"] = fn("inner_product", "V", "V", columns="vec")
    s["negative_inner_product"] = fn("negative_inner_product", "V", "V", columns="vec")
    s["l1_distance"] = fn("l1_distance", "V", "V", columns="vec")
    s["vector_dims"] = fn("vector_dims", "V", columns="vec")
    s["vector_norm"] = fn("vector_norm", "V", columns="vec")
    s["vector_distance"] = op("VECTOR_DISTANCE({0}, {1}, {2})", "V", "V", "METRIC", columns="vec", extra=[literal_select("VECTOR_DISTANCE('[1,2]', '[3,4]')")], sql="VECTOR_DISTANCE(v1, v2 [, metric])")
    s["semantic_distance"] = fn("semantic_distance", "S", "S='abc'", columns="none")
    s["semantic_vector_distance"] = fn("semantic_vector_distance", "V", "V", "S?", columns="none")
    s["l2_similarity"] = fn("l2_similarity", "V", "V", columns="vec")
    s["cosine_similarity"] = fn("cosine_similarity", "V", "V", columns="vec")
    s["inner_product_similarity"] = fn("inner_product_similarity", "V", "V", columns="vec")
    s["vector_similarity"] = op("VECTOR_SIMILARITY({0}, {1}, {2})", "V", "V", "METRIC", columns="vec", extra=[literal_select("VECTOR_SIMILARITY('[1,2]', '[3,4]')")], sql="VECTOR_SIMILARITY(v1, v2 [, metric])")
    s["inner_table_option_printer"] = internal(
        "inner_table_option_printer",
        "SHOW CREATE TABLE tm",
        "SELECT TABLE_NAME, ENGINE, ROW_FORMAT, TABLE_COLLATION, CREATE_OPTIONS, TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'test' AND TABLE_NAME = 'tm'",
        sql="SHOW CREATE TABLE and information_schema.TABLES",
    )
    s["inner_table_sequence_getter"] = internal(
        "inner_table_sequence_getter",
        "CREATE TABLE tseq (id INT AUTO_INCREMENT PRIMARY KEY, v INT)",
        "INSERT INTO tseq (v) VALUES (1), (2)",
        "SELECT TABLE_NAME, AUTO_INCREMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'test' AND TABLE_NAME = 'tseq'",
        "DROP TABLE tseq",
        sql="information_schema.TABLES.AUTO_INCREMENT",
    )
    s["get_path"] = fn("get_path", "S='a'", "S='b'", columns="none")
    s["gtid_subset"] = fn("gtid_subset", "GTID", "GTID=''")
    s["gtid_subtract"] = fn("gtid_subtract", "GTID", "GTID='3E11FA47-71CA-11E1-9E33-C80AA9429562:2'")
    s["wait_for_executed_gtid_set"] = stmts(
        "WAIT_FOR_EXECUTED_GTID_SET(set, timeout) with zero timeouts",
        literal_select("WAIT_FOR_EXECUTED_GTID_SET('', 0)"),
        literal_select("WAIT_FOR_EXECUTED_GTID_SET('3E11FA47-71CA-11E1-9E33-C80AA9429562:1', 0)"),
        literal_select("WAIT_FOR_EXECUTED_GTID_SET('bad', 0)"),
    )
    s["wait_until_sql_thread_after_gtids"] = stmts(
        "WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS(set, timeout) with zero timeouts",
        literal_select("WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS('', 0)"),
        literal_select("WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS('3E11FA47-71CA-11E1-9E33-C80AA9429562:1', 0)"),
    )
    s["array_contains"] = fn("array_contains", "AR", "EL", columns="arr")
    s["array_to_string"] = fn("array_to_string", "AR", "WSEP", "S?", columns="arr")
    s["string_to_array"] = fn("string_to_array", "S='a,b,,c'", "WSEP", "S?", columns="none")
    s["array_append"] = fn("array_append", "AR", "EL", columns="arr")
    s["array_length"] = fn("array_length", "AR", columns="arr")
    s["array_prepend"] = fn("array_prepend", "AR", "EL", columns="arr")
    s["array_concat"] = fn("array_concat", "AR", "AR*", columns="arr")
    s["array_difference"] = fn("array_difference", "AR", columns="arr")
    s["array_compact"] = fn("array_compact", "AR", columns="arr")
    s["array_sort"] = fn("array_sort", "AR", columns="arr")
    s["array_sortby"] = op(
        "ARRAY_SORTBY({0}, {1}, {2})", "LAMBDA", "AR", "AR=[3,2,1]", columns="arr", sweep=(1,),
        sql="ARRAY_SORTBY(lambda, array, array)",
    )
    s["array_filter"] = op("ARRAY_FILTER({0}, {1})", "LAMBDA", "AR", columns="arr", sweep=(1,), sql="ARRAY_FILTER(lambda, array)")
    s["element_at"] = fn("element_at", "AR", "I", columns="arr")
    s["cardinality"] = fn("cardinality", "AR", columns="arr")
    s["array_max"] = fn("array_max", "AR", columns="arr")
    s["array_min"] = fn("array_min", "AR", columns="arr")
    s["array_avg"] = fn("array_avg", "AR", columns="arr")
    s["array_first"] = op("ARRAY_FIRST({0}, {1})", "LAMBDA", "AR", columns="arr", sweep=(1,), sql="ARRAY_FIRST(lambda, array)")
    s["decode_trace_id"] = fn("decode_trace_id", "TRACE", columns="none")
    s["is_enabled_role"] = internal(
        "is_enabled_role",
        "SELECT * FROM information_schema.ENABLED_ROLES ORDER BY ROLE_NAME, ROLE_HOST, IS_DEFAULT, IS_MANDATORY",
        "SELECT * FROM information_schema.APPLICABLE_ROLES ORDER BY 1, 2, 3, 4, 5, 6, 7, 8, 9",
        sql="information_schema role views",
    )
    s["sm3"] = fn("sm3", "S")
    s["sm4_encrypt"] = op(
        "HEX(SM4_ENCRYPT({0}, {1}))", "S", "KEY",
        extra=bare_probes("SM4_ENCRYPT('abc', 'key')", "SM4_ENCRYPT(c_varchar, 'key')"),
        sql="SM4_ENCRYPT(x, key [, iv])",
    )
    s["sm4_decrypt"] = fn("sm4_decrypt", "S=SM4_ENCRYPT('secret', 'key')", "KEY")
    s["split_part"] = fn("split_part", "S='a,b,,c'", "WSEP", "SPLITN", "SPLITN?", columns="core")
    s["inner_is_true"] = internal(
        "inner_is_true",
        *(list(INDEXED_SETUP) + indexed("c_int IS TRUE", "c_double IS FALSE", "c_varchar IS NOT TRUE", "c_dec_9_2 IS NOT FALSE") + list(INDEXED_TEARDOWN)),
        sql="IS TRUE and IS FALSE on indexed columns",
    )
    s["inner_decode_like"] = internal(
        "inner_decode_like",
        *(list(INDEXED_SETUP) + indexed("c_varchar LIKE 'He%'", "c_varchar LIKE '%o'", "c_char LIKE 'a_c%'", "c_varchar LIKE '9|%%' ESCAPE '|'", "c_varchar LIKE ''") + list(INDEXED_TEARDOWN)),
        sql="LIKE on indexed columns",
    )
    s["inner_double_to_int"] = internal(
        "inner_double_to_int",
        *(list(INDEXED_SETUP) + indexed("c_int = 2.5e0", "c_int > 1.5e0", "c_int < -2147483648.5e0", "c_ubigint >= 1.8446744073709552e19", "c_int = 1e0") + list(INDEXED_TEARDOWN)),
        sql="integer index columns compared with DOUBLE constants",
    )
    s["inner_decimal_to_year"] = internal(
        "inner_decimal_to_year",
        *(list(INDEXED_SETUP) + indexed("c_year = 2024.0", "c_year > 1999.5", "c_year < 70.5", "c_year = 0.4") + list(INDEXED_TEARDOWN)),
        sql="YEAR index columns compared with DECIMAL constants",
    )
    s["tokenize"] = fn("tokenize", "S", "S?", "S?", columns="core")
    s["array_overlaps"] = fn("array_overlaps", "AR", "AR=[3,4]", columns="arr")
    s["array_contains_all"] = fn("array_contains_all", "AR", "AR=[1,2]", columns="arr")
    s["array_distinct"] = fn("array_distinct", "AR", columns="arr")
    s["array_remove"] = fn("array_remove", "AR", "EL", columns="arr")
    s["array_map"] = op(
        "ARRAY_MAP({0}, {1})", "LAMBDA", "AR", columns="arr", sweep=(1,),
        extra=[literal_select("ARRAY_MAP((x, y) -> x + y, [1, 2], [10, 20])")],
        sql="ARRAY_MAP(lambda, array ...)",
    )
    s["array_sum"] = fn("array_sum", "AR", columns="arr")
    s["array_position"] = fn("array_position", "AR", "EL", columns="arr")
    s["array_slice"] = fn("array_slice", "AR", "I", "I?", columns="arr")
    s["array_range"] = fn("array_range", "RANGEN", "RANGEN?", "RANGEN?", columns="none")
    s["array_except"] = fn("array_except", "AR", "AR=[1]", columns="arr")
    s["array_intersect"] = fn("array_intersect", "AR", "AR=[2,3,4]", "AR*", columns="arr")
    s["array_union"] = fn("array_union", "AR", "AR=[3,4]", "AR*", columns="arr")
    s["get_mysql_routine_parameter_type_str"] = internal(
        "get_mysql_routine_parameter_type_str",
        "CREATE FUNCTION judge_udf3(a INT UNSIGNED, b VARCHAR(10), c DECIMAL(5,2), d DATETIME(3)) RETURNS DOUBLE DETERMINISTIC RETURN a + c",
        "SELECT PARAMETER_NAME, ORDINAL_POSITION, DATA_TYPE, DTD_IDENTIFIER FROM information_schema.PARAMETERS WHERE SPECIFIC_SCHEMA = 'test' AND SPECIFIC_NAME = 'judge_udf3' ORDER BY ORDINAL_POSITION",
        "DROP FUNCTION judge_udf3",
        sql="information_schema.PARAMETERS",
    )
    s["to_pinyin"] = fn("to_pinyin", "S")
    s["url_encode"] = fn("url_encode", "S")
    s["url_decode"] = fn("url_decode", "S='a%20b%2Bc%E4%B8%AD'")
    s["keyvalue"] = fn("keyvalue", "S='a:1&b:2&c:3'", "S='&'", "S=':'", "S='b'", columns="none")
    s["map_keys"] = fn("map_keys", "MAP", columns="none")
    s["map_values"] = fn("map_values", "MAP", columns="none")
    info_cols = internal(
        "inner_info_cols_column_def_printer",
        "SELECT COLUMN_NAME, ORDINAL_POSITION, COLUMN_DEFAULT, IS_NULLABLE, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
        "CHARACTER_OCTET_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, DATETIME_PRECISION, CHARACTER_SET_NAME, "
        "COLLATION_NAME, COLUMN_TYPE, COLUMN_KEY, EXTRA, PRIVILEGES FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = 'test' AND TABLE_NAME = 'tm' ORDER BY ORDINAL_POSITION",
        "SHOW FULL COLUMNS FROM tm",
        sql="information_schema.COLUMNS and SHOW FULL COLUMNS",
    )
    s["inner_info_cols_column_def_printer"] = info_cols
    for name in (
        "inner_info_cols_char_len_printer",
        "inner_info_cols_char_name_printer",
        "inner_info_cols_coll_name_printer",
        "inner_info_cols_priv_printer",
        "inner_info_cols_extra_printer",
        "inner_info_cols_data_type_printer",
        "inner_info_cols_column_type_printer",
        "inner_info_cols_column_key_printer",
    ):
        s[name] = internal(name, sql="information_schema.COLUMNS (see inner_info_cols_column_def_printer)")
    s["l2_squared"] = fn("l2_squared", "V", "V", columns="vec")
    s["ai_complete"] = fn("ai_complete", "AI", "S", columns="none")
    s["ai_embed"] = fn("ai_embed", "AI", "S", columns="none")
    s["ai_rerank"] = fn("ai_rerank", "AI", "S", "S='[\"a\", \"b\"]'", columns="none")
    s["ai_prompt"] = fn("ai_prompt", "PROMPT", "A", "A*", columns="none")
    return s


def spec_for(specs, entry):
    for key in ("{}@{}".format(entry.name, entry.item_type), entry.name):
        if key in specs:
            return specs[key]
    return None


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def grammar_alternatives(rule):
    text = read_text(PARSER_FILE)
    match = re.search(r"^{}:[ \t]*$".format(re.escape(rule)), text, re.M)
    if not match:
        raise GeneratorError("grammar rule {} not found in {}".format(rule, PARSER_FILE))
    alternatives = [[]]
    depth = 0
    position = match.end()
    token = []
    while position < len(text):
        char = text[position]
        if depth == 0 and char == ";" and text[position - 1] == "\n":
            break
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0:
            if char == "|":
                alternatives.append([])
            elif char.isalnum() or char == "_":
                token.append(char)
                position += 1
                continue
            if token:
                alternatives[-1].append("".join(token))
                token = []
        position += 1
    if token:
        alternatives[-1].append("".join(token))
    return [a for a in alternatives if a]


CAST_TARGETS_BY_HEAD = OrderedDict(
    (
        ("BINARY", (("BINARY",), ("BINARY(3)",))),
        ("CHARACTER", (("CHAR", "CHAR CHARACTER SET latin1"), ("CHAR(3)", "CHAR(4) BINARY", "CHAR(4) CHARSET gbk"))),
        ("DATETIME", (("DATETIME(6)",), ("DATETIME", "DATETIME(3)"))),
        ("DATE", (("DATE",), ())),
        ("TIME", (("TIME(6)",), ("TIME", "TIME(2)"))),
        ("YEAR", (("YEAR",), ())),
        ("NUMBER", (("NUMBER",), ("NUMBER(10,2)",))),
        ("DECIMAL", (("DECIMAL(65,30)",), ("DECIMAL", "DECIMAL(10,2)", "DECIMAL(5)"))),
        ("FIXED", ((), ("FIXED(10,2)",))),
        ("NUMERIC", ((), ("NUMERIC(10,2)",))),
        ("SIGNED", (("SIGNED",), ("SIGNED INTEGER",))),
        ("UNSIGNED", (("UNSIGNED",), ("UNSIGNED INTEGER",))),
        ("DOUBLE", (("DOUBLE",), ())),
        ("FLOAT", (("FLOAT",), ("FLOAT(30)", "FLOAT(10)"))),
        ("JSON", (("JSON",), ())),
        ("POINT", (("POINT",), ())),
        ("LINESTRING", (("LINESTRING",), ())),
        ("POLYGON", (("POLYGON",), ())),
        ("MULTIPOINT", (("MULTIPOINT",), ())),
        ("MULTILINESTRING", (("MULTILINESTRING",), ())),
        ("MULTIPOLYGON", (("MULTIPOLYGON",), ())),
        ("GEOMETRYCOLLECTION", (("GEOMETRYCOLLECTION",), ("GEOMCOLLECTION",))),
        ("NCHAR", ((), ("NCHAR", "NCHAR(4)"))),
        ("NATIONAL", ((), ("NATIONAL CHAR(4)",))),
    )
)
GEOMETRY_TARGET_HEADS = ("POINT", "LINESTRING", "POLYGON", "MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON", "GEOMETRYCOLLECTION")
DERIVED_CAST_SOURCES = ("CAST(c_dec_38_10 AS NUMBER)", "CAST(c_varchar AS NUMBER(30,5))")
GEOMETRY_SOURCES = ("c_geom", "c_varbinary", "c_varchar", "c_blob", "c_json", "c_int", "c_vec")
CAST_ERROR_ROWS = {
    ("c_char", "JSON"): (2, 3, 4, 5, 6, 8),
    ("c_varchar", "JSON"): (2, 3, 5, 6, 7, 8),
    ("c_tinytext", "JSON"): (2, 3, 4, 5, 8),
    ("c_text", "JSON"): (2, 3, 4, 5, 6, 8),
    ("c_mediumtext", "JSON"): (2, 3, 4, 5, 8),
    ("c_longtext", "JSON"): (2, 4, 5, 8),
}
COMPARE_OPERATORS = ("=", "<", "<=>")
COMPARE_COLUMNS = CORE_COLUMNS + ("c_dec_18_6", "c_dec_38_10", "c_char", "c_set", "c_arr")
COMPARE_ONE_OPERATOR = ("c_geom", "c_vec", "c_arr", "c_json")
COMPARE_TYPE_ERROR = ("c_geom", "c_vec", "c_arr")
COMPARE_WARNING_STRINGS = ("c_varchar", "c_varbinary", "c_text", "c_char")
COMPARE_CONVERTED = (
    "c_int",
    "c_bigint",
    "c_ubigint",
    "c_float",
    "c_double",
    "c_dec_9_2",
    "c_dec_18_6",
    "c_dec_38_10",
    "c_dec_65_30",
    "c_date",
    "c_time6",
    "c_datetime6",
    "c_timestamp",
    "c_year",
    "c_bit",
)
ARITH_ALL = ("+",)
ARITH_NUMERIC = ("-", "*", "/")
ARITH_INTEGER = ("DIV", "%")
ARITH_NUMERIC_COLUMNS = (
    "c_int",
    "c_ubigint",
    "c_double",
    "c_float",
    "c_dec_9_2",
    "c_dec_18_6",
    "c_dec_38_10",
    "c_dec_65_30",
    "c_varchar",
    "c_datetime6",
    "c_year",
    "c_bit",
    "c_arr",
)
ARITH_INTEGER_COLUMNS = ("c_int", "c_ubigint", "c_double", "c_dec_9_2", "c_dec_38_10", "c_varchar", "c_bit")
STORE_LITERALS = (
    "NULL",
    "0",
    "-2.5e0",
    "18446744073709551615",
    "'12.5abc'",
    "'中文😀'",
    "X'00FF'",
    "TIMESTAMP'2024-02-29 13:45:56.123456'",
    "TIME'13:45:56.5'",
    "CAST('{\"a\": 1}' AS JSON)",
)
STORE_SELECT_SOURCES = ("c_bigint", "c_double", "c_dec_65_30", "c_varchar", "c_datetime6", "c_json")
RANGE_LITERALS = (
    "NULL",
    "0",
    "1.5",
    "-2.5e0",
    "18446744073709551615",
    "'12.5x'",
    "'2024-02-29'",
    "DATE'2024-02-29'",
    "TIMESTAMP'2024-02-29 13:45:56.123456'",
    "TIME'13:45:56.5'",
)
RANGE_COLUMNS = (
    "c_int",
    "c_ubigint",
    "c_double",
    "c_dec_9_2",
    "c_dec_38_10",
    "c_varchar",
    "c_char",
    "c_date",
    "c_datetime6",
    "c_timestamp",
    "c_time6",
    "c_year",
    "c_bit",
    "c_enum",
)


def cast_targets():
    heads = []
    for alternative in grammar_alternatives("cast_data_type"):
        head = alternative[0]
        if head == "cast_datetime_type_i":
            for inner in grammar_alternatives("cast_datetime_type_i"):
                heads.append(inner[0])
        elif head == "geometry_collection":
            heads.append("GEOMETRYCOLLECTION")
        elif head not in heads:
            heads.append(head)
    unknown = [h for h in heads if h not in CAST_TARGETS_BY_HEAD]
    if unknown:
        raise GeneratorError("CAST targets in the grammar without a spelling here: {}".format(", ".join(unknown)))
    stale = [h for h in CAST_TARGETS_BY_HEAD if h not in heads]
    if stale:
        raise GeneratorError("CAST targets here that the grammar no longer has: {}".format(", ".join(stale)))
    main, variants, geometry = [], [], []
    for head in heads:
        head_main, head_variants = CAST_TARGETS_BY_HEAD[head]
        if head in GEOMETRY_TARGET_HEADS:
            geometry.extend(head_main + head_variants)
        else:
            main.extend(head_main)
            variants.extend(head_variants)
    return heads, main, variants, geometry


def matrix_column_names():
    return [c[0] for c in MATRIX_COLUMNS]


def cast_probe_groups():
    heads, main, variants, geometry = cast_targets()
    groups = []
    for column in matrix_column_names():
        probes = []
        for target in main:
            expression = "CAST({} AS {})".format(column, target)
            rows = CAST_ERROR_ROWS.get((column, target), ())
            probes.extend(row_split_selects(expression, rows) if rows else [column_select(expression)])
        groups.append(("CAST from column {}".format(column), probes))
    for source in DERIVED_CAST_SOURCES:
        groups.append(
            (
                "CAST from {}".format(source),
                [column_select("CAST({} AS {})".format(source, target)) for target in main],
            )
        )
    groups.append(
        (
            "CAST to geometry types",
            [column_select("ST_AsText(CAST({} AS {}))".format(c, t)) for t in geometry for c in GEOMETRY_SOURCES]
            + [literal_select("CAST(NULL AS {})".format(t)) for t in geometry],
        )
    )
    groups.append(
        (
            "CAST with other precisions and spellings",
            [column_select("CAST({} AS {})".format(c, t)) for t in variants for c in SMALL_COLUMNS],
        )
    )
    for literal in DOMAINS["A"].literals:
        groups.append(
            (
                "CAST from literal {}".format(literal),
                [literal_select("CAST({} AS {})".format(literal, target)) for target in main],
            )
        )
    groups.append(
        (
            "CONVERT and CAST ... IGNORE spellings",
            [
                column_select("CAST(c_int AS CHAR IGNORE)"),
                literal_select("CAST('12.5x' AS SIGNED IGNORE)"),
                column_select("CONVERT(c_varchar, SIGNED)"),
                column_select("CONVERT(c_datetime6, DATE)"),
                column_select("CONVERT(c_double, DECIMAL(10,3))"),
                column_select("CONVERT(c_text USING latin1)"),
                column_select("CONVERT(c_varbinary USING utf8mb4)"),
                column_select("BINARY c_varchar"),
                literal_select("CAST('abc' AS CHAR(2)) = 'ab'"),
                literal_select("CAST(1 AS SIGNED) = CAST('1' AS UNSIGNED)"),
            ],
        )
    )
    return heads, groups


def compare_one_operator(left, right):
    if left in COMPARE_ONE_OPERATOR or right in COMPARE_ONE_OPERATOR:
        return True
    return (left in COMPARE_WARNING_STRINGS and right in COMPARE_CONVERTED) or (
        right in COMPARE_WARNING_STRINGS and left in COMPARE_CONVERTED
    )


def less_than_repeats(left, right, operator):
    if operator != "<":
        return False
    specials = [c for c in (left, right) if c in COMPARE_TYPE_ERROR]
    if not specials:
        return False
    partners = [c for c in (left, right) if c not in COMPARE_TYPE_ERROR]
    return bool(partners) and partners[0] != COMPARE_COLUMNS[0]


def compare_probe_groups():
    groups = []
    columns = COMPARE_COLUMNS
    for index, left in enumerate(columns):
        probes = []
        for right in columns[index:]:
            if compare_one_operator(left, right):
                operator_sets = [(o,) for o in COMPARE_OPERATORS if not less_than_repeats(left, right, o)]
            else:
                operator_sets = [COMPARE_OPERATORS]
            for operators in operator_sets:
                items = ", ".join(
                    "a.{l} {o} b.{r} AS `{o}`".format(l=left, r=right, o=o) for o in operators
                )
                probes.append(
                    "SELECT a.id, b.id, {} FROM {m} a, {m} b ORDER BY a.id, b.id".format(items, m=MATRIX_TABLE)
                )
        groups.append(("comparison of {} with other types".format(left), probes))
    return groups


ARITH_ROWS = {
    "+": "(1, 2, 6, 7, 8)",
    "-": "(1, 2, 6, 7, 8)",
    "*": "(1, 2, 7, 8)",
    "/": "(1, 2, 5, 6, 7, 8)",
    "DIV": "(1, 2, 5, 6, 7, 8)",
    "%": "(1, 2, 5, 6, 7, 8)",
}
ARITH_TYPE_COLUMNS = tuple(c for c in COMPARE_COLUMNS if c not in ("c_json", "c_geom", "c_vec", "c_arr"))


def arithmetic_probe_groups():
    groups = []

    def pair_probes(columns, operators):
        probes = []
        for index, left in enumerate(columns):
            for right in columns[index:]:
                for operator in operators:
                    probes.append(
                        "SELECT a.id, b.id, a.{l} {o} b.{r} AS v FROM {m} a, {m} b "
                        "WHERE a.id IN {rows} AND b.id IN {rows} ORDER BY a.id, b.id".format(
                            l=left, r=right, o=operator, m=MATRIX_TABLE, rows=ARITH_ROWS[operator]
                        )
                    )
        return probes

    groups.append(("addition over all type pairs of the comparison columns", pair_probes(COMPARE_COLUMNS, ARITH_ALL)))
    groups.append(("subtraction, multiplication and division", pair_probes(ARITH_NUMERIC_COLUMNS, ARITH_NUMERIC)))
    groups.append(("integer division and modulo", pair_probes(ARITH_INTEGER_COLUMNS, ARITH_INTEGER)))
    types = []
    for operator in ARITH_ALL + ARITH_NUMERIC + ARITH_INTEGER:
        for left in ARITH_TYPE_COLUMNS:
            items = ", ".join(
                "a.{l} {o} b.{r} AS {r}".format(l=left, o=operator, r=right) for right in ARITH_TYPE_COLUMNS
            )
            types.append("SELECT {} FROM {m} a, {m} b WHERE 1 = 0".format(items, m=MATRIX_TABLE))
    groups.append(("result types of arithmetic between column types (metadata only)", types))
    return groups


def store_units():
    ddl = ", ".join("{} {}".format(name, kind) for name, kind, _ in MATRIX_COLUMNS)
    setup = ("CREATE TABLE ts (id INT PRIMARY KEY, {})".format(ddl),)
    teardown = ("DROP TABLE ts",)
    strict_blocks = []
    next_id = 1
    for name, kind, _ in MATRIX_COLUMNS:
        first = next_id
        block = []
        for literal in STORE_LITERALS:
            block.append("INSERT INTO ts (id, {}) VALUES ({}, {})".format(name, next_id, literal))
            next_id += 1
        block.append("SELECT id, {} FROM ts WHERE id BETWEEN {} AND {} ORDER BY id".format(name, first, next_id - 1))
        strict_blocks.append(block)
    ignore_blocks = []
    base = 1000
    for name in CORE_COLUMNS:
        first = base
        block = []
        for source in STORE_SELECT_SOURCES:
            block.append(
                "INSERT IGNORE INTO ts (id, {t}) SELECT id + {b}, {s} FROM {m}".format(t=name, b=base, s=source, m=MATRIX_TABLE)
            )
            base += 10
        block.append("SELECT id, {} FROM ts WHERE id BETWEEN {} AND {} ORDER BY id".format(name, first, base - 1))
        ignore_blocks.append(block)
    return [
        Unit("strict INSERT of literals into every column type (column conversion)", setup, strict_blocks, teardown),
        Unit("INSERT IGNORE ... SELECT between column types (column conversion)", setup, ignore_blocks, teardown),
    ]


CTAS_GROUPS = (
    (
        "casts",
        (
            "CAST(c_varchar AS BINARY)",
            "CAST(c_varchar AS CHAR)",
            "CAST(c_varchar AS CHAR CHARACTER SET latin1)",
            "CAST(c_varchar AS DATETIME(6))",
            "CAST(c_varchar AS DATE)",
            "CAST(c_varchar AS TIME(6))",
            "CAST(c_varchar AS YEAR)",
            "CAST(c_varchar AS NUMBER)",
        ),
    ),
    (
        "casts, continued",
        (
            "CAST(c_varchar AS DECIMAL(65,30))",
            "CAST(c_varchar AS SIGNED)",
            "CAST(c_varchar AS UNSIGNED)",
            "CAST(c_varchar AS DOUBLE)",
            "CAST(c_varchar AS FLOAT)",
            "CAST(c_varchar AS JSON)",
            "CAST(c_int AS DECIMAL(10,2))",
            "CAST(c_double AS CHAR(3))",
        ),
    ),
    (
        "arithmetic",
        (
            "c_int + c_bigint",
            "c_int + c_double",
            "c_int + c_dec_9_2",
            "c_dec_9_2 * c_dec_65_30",
            "c_bigint / c_int",
            "c_ubigint - c_int",
            "c_int DIV c_int",
            "c_double % c_int",
        ),
    ),
    (
        "arithmetic on other types",
        (
            "c_varchar + 0",
            "c_datetime6 + 0",
            "c_date + 0",
            "c_time6 + 0",
            "c_year + 0",
            "c_bit + 0",
            "c_float * c_float",
            "c_enum + 0",
        ),
    ),
    (
        "string functions",
        (
            "CONCAT(c_varchar, c_text)",
            "CONCAT(c_char, c_int)",
            "UPPER(c_text)",
            "SUBSTR(c_varchar, 2)",
            "REPEAT(c_char, 3)",
            "LPAD(c_varchar, 100, 'x')",
            "REPLACE(c_text, 'a', 'b')",
            "HEX(c_blob)",
        ),
    ),
    (
        "string functions, continued",
        (
            "MD5(c_varchar)",
            "CONVERT(c_varchar USING latin1)",
            "c_varchar COLLATE utf8mb4_bin",
            "CHAR(65)",
            "SPACE(3)",
            "LEFT(c_varchar, 3)",
            "TRIM(c_char)",
            "CONCAT(c_set, '')",
        ),
    ),
    (
        "temporal functions",
        (
            "NOW()",
            "NOW(6)",
            "CURDATE()",
            "CURTIME(3)",
            "DATE_ADD(c_date, INTERVAL 1 DAY)",
            "DATE_ADD(c_date, INTERVAL 1 SECOND)",
            "DATE_ADD(c_datetime6, INTERVAL 1 MICROSECOND)",
            "TIMEDIFF(c_datetime6, c_datetime6)",
        ),
    ),
    (
        "temporal functions, continued",
        (
            "FROM_UNIXTIME(c_int)",
            "UNIX_TIMESTAMP(c_datetime6)",
            "DATE_FORMAT(c_date, '%Y')",
            "STR_TO_DATE(c_varchar, '%Y')",
            "MAKETIME(1, 2, 3.5)",
            "SEC_TO_TIME(c_double)",
            "TIMESTAMP(c_date)",
            "LAST_DAY(c_datetime6)",
        ),
    ),
    (
        "numeric functions",
        (
            "ROUND(c_dec_65_30, 2)",
            "ROUND(c_double)",
            "TRUNCATE(c_dec_9_2, 1)",
            "FLOOR(c_dec_38_10)",
            "CEIL(c_double)",
            "ABS(c_ubigint)",
            "SIGN(c_dec_9_2)",
            "POW(c_int, 2)",
        ),
    ),
    (
        "numeric functions, continued",
        (
            "SQRT(c_int)",
            "LOG(c_double)",
            "CONV(c_int, 10, 16)",
            "FORMAT(c_double, 2)",
            "CRC32(c_varchar)",
            "BIT_COUNT(c_bigint)",
            "c_int & c_bigint",
            "~c_int",
        ),
    ),
    (
        "control flow and comparison",
        (
            "IF(c_int, c_varchar, c_double)",
            "IFNULL(c_int, c_dec_9_2)",
            "COALESCE(c_date, c_datetime6)",
            "CASE WHEN c_int > 0 THEN c_int ELSE c_varchar END",
            "NULLIF(c_int, 1)",
            "GREATEST(c_int, c_double)",
            "LEAST(c_date, c_varchar)",
            "c_int = c_varchar",
        ),
    ),
    (
        "JSON, spatial, vector, ENUM and SET",
        (
            "JSON_EXTRACT(c_json, '$.a')",
            "JSON_UNQUOTE(c_json)",
            "JSON_OBJECT('k', c_int)",
            "JSON_LENGTH(c_json)",
            "ST_AsText(c_geom)",
            "ST_SRID(c_geom)",
            "VECTOR_DIMS(c_vec)",
            "c_enum",
        ),
    ),
    (
        "arrays",
        (
            "[1, 2, 3]",
            "[1.5, NULL]",
            "['a', 'b']",
            "[[1, 2], [3]]",
            "ARRAY(c_int, c_int)",
            "c_arr",
            "c_vec",
            "c_arr + c_arr",
        ),
    ),
    (
        "array functions",
        (
            "ARRAY_APPEND(c_arr, 4)",
            "ARRAY_PREPEND(c_arr, 0)",
            "ARRAY_CONCAT(c_arr, [9])",
            "ARRAY_COMPACT(c_arr)",
            "ARRAY_SORT(c_arr)",
            "ARRAY_DISTINCT(c_arr)",
            "ARRAY_REMOVE(c_arr, 1)",
            "ARRAY_SLICE(c_arr, 1, 2)",
        ),
    ),
    (
        "array functions, continued",
        (
            "ARRAY_EXCEPT(c_arr, [1])",
            "ARRAY_INTERSECT(c_arr, [1, 2])",
            "ARRAY_UNION(c_arr, [3, 4])",
            "ARRAY_DIFFERENCE(c_arr)",
            "ARRAY_RANGE(1, 5)",
            "ARRAY_MAP(x -> x + 1, c_arr)",
            "ARRAY_FILTER(x -> x > 1, c_arr)",
            "ARRAY_SORTBY(x -> x, c_arr)",
        ),
    ),
    (
        "maps, vectors and array arithmetic",
        (
            "STRING_TO_ARRAY('a,b', ',')",
            "MAP(1, 'a')",
            "MAP(c_int, c_varchar)",
            "MAP_KEYS(MAP(1, 'a'))",
            "MAP_VALUES(MAP(1, 'a'))",
            "c_vec + c_vec",
            "c_arr - [1]",
            "IF(id > 0, c_arr, NULL)",
        ),
    ),
    (
        "geometry constructors",
        (
            "c_geom",
            "POINT(1, 2)",
            "LINESTRING(POINT(0, 0), POINT(1, 1))",
            "POLYGON(LINESTRING(POINT(0, 0), POINT(0, 1), POINT(1, 1), POINT(0, 0)))",
            "MULTIPOINT(POINT(0, 0), POINT(1, 1))",
            "MULTILINESTRING(LINESTRING(POINT(0, 0), POINT(1, 1)))",
            "MULTIPOLYGON(POLYGON(LINESTRING(POINT(0, 0), POINT(0, 1), POINT(1, 1), POINT(0, 0))))",
            "GEOMETRYCOLLECTION(POINT(1, 1))",
        ),
    ),
    (
        "geometry with SRID",
        (
            "GEOMCOLLECTION(POINT(1, 1))",
            "ST_GeomFromText('POINT(1 2)')",
            "ST_GeomFromText('POINT(1 2)', 4326)",
            "ST_GeomFromWKB(X'0101000000000000000000F03F0000000000000040', 4326)",
            "ST_SRID(c_geom, 4326)",
            "_ST_SetSRID(c_geom, 4326)",
            "ST_Transform(ST_GeomFromText('POINT(39.9 116.4)', 4326), 3857)",
            "_ST_GeomFromEWKT(CONCAT('SRID=4326', CHAR(59), 'POINT(1 2)'))",
        ),
    ),
    (
        "geometry functions and casts",
        (
            "ST_Buffer(c_geom, 1)",
            "ST_Union(c_geom, c_geom)",
            "ST_Centroid(c_geom)",
            "_ST_Point(1, 2)",
            "_ST_MakePoint(1, 2)",
            "_ST_MakeEnvelope(0, 0, 1, 1)",
            "CAST(c_geom AS POINT)",
            "CAST(c_geom AS POLYGON)",
        ),
    ),
    (
        "geometry in control flow, ENUM and SET",
        (
            "CAST(c_geom AS GEOMETRYCOLLECTION)",
            "COALESCE(c_geom, POINT(0, 0))",
            "c_set",
            "IF(id > 0, c_enum, c_enum)",
            "IFNULL(c_enum, c_enum)",
            "COALESCE(c_set, c_set)",
            "CASE WHEN id > 4 THEN c_enum ELSE c_enum END",
            "GREATEST(c_enum, c_enum)",
        ),
    ),
    (
        "ENUM and SET, continued",
        (
            "NULLIF(c_set, 'x')",
            "IF(id > 0, c_enum, c_set)",
        ),
    ),
)
CTAS_TABLE = "tct{:02d}"
CTAS_PER_BLOCK = 16


def ctas_unit():
    blocks = []
    flat = [e for _, expressions in CTAS_GROUPS for e in expressions]
    for first in range(0, len(flat), CTAS_PER_BLOCK):
        expressions = flat[first : first + CTAS_PER_BLOCK]
        names = [CTAS_TABLE.format(n) for n in range(1, len(expressions) + 1)]
        block = [
            "CREATE TABLE {} AS SELECT {} AS e FROM {} WHERE 1 = 0".format(name, expression, MATRIX_TABLE)
            for name, expression in zip(names, expressions)
        ]
        block.append(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, CHARACTER_SET_NAME, "
            "COLLATION_NAME, SRS_ID FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = 'test' "
            "AND TABLE_NAME IN ({}) ORDER BY TABLE_NAME, ORDINAL_POSITION".format(
                ", ".join("'{}'".format(name) for name in names)
            )
        )
        block.append("DROP TABLE IF EXISTS {}".format(", ".join(names)))
        blocks.append(block)
    return Unit(
        "result types as column types: one CREATE TABLE ... AS SELECT per expression, read back from "
        "information_schema.COLUMNS",
        (),
        blocks,
        (),
    )


DEFAULT_LITERALS = (
    "0",
    "-2.5",
    "2.5e0",
    "18446744073709551615",
    "'12.5abc'",
    "'中文😀'",
    "X'00FF'",
    "TIMESTAMP'2024-02-29 13:45:56.123456'",
    "TIME'13:45:56.5'",
)
DEFAULT_TABLE = "td"


def default_units():
    ddl = ", ".join("{} {}".format(name, kind) for name, kind, _ in MATRIX_COLUMNS)
    setup = ("CREATE TABLE {} (id INT PRIMARY KEY, {})".format(DEFAULT_TABLE, ddl),)
    teardown = ("DROP TABLE {}".format(DEFAULT_TABLE),)
    units = []
    for literal in DEFAULT_LITERALS:
        block = [
            "ALTER TABLE {} ALTER COLUMN {} SET DEFAULT {}".format(DEFAULT_TABLE, name, literal)
            for name, _, _ in MATRIX_COLUMNS
        ]
        block.append("SHOW CREATE TABLE {}".format(DEFAULT_TABLE))
        units.append(
            Unit("column DEFAULT {} for every column type (object cast)".format(literal), setup, [block], teardown)
        )
    return units


def range_units():
    units = []
    for column in RANGE_COLUMNS:
        blocks = []
        for literal in RANGE_LITERALS:
            blocks.append(["SELECT id FROM {} WHERE {} = {} ORDER BY id".format(INDEXED_TABLE, column, literal)])
            blocks.append(["SELECT id FROM {} WHERE {} > {} ORDER BY id".format(INDEXED_TABLE, column, literal)])
        units.append(Unit("index range on {} against constants (object cast)".format(column), INDEXED_SETUP, blocks, INDEXED_TEARDOWN))
    units.append(
        Unit(
            "partition pruning and system variable assignment (object cast)",
            (),
            [[
                "CREATE TABLE tpp (id INT, k VARCHAR(10), d DATE, PRIMARY KEY (id, k, d)) PARTITION BY KEY(k) PARTITIONS 3",
                "INSERT INTO tpp VALUES (1, '1', '2024-02-29'), (2, '2.5', '1999-12-31'), (3, 'x', '2000-01-01')",
                "SELECT id FROM tpp WHERE k = 1 ORDER BY id",
                "SELECT id FROM tpp WHERE k = 2.5 ORDER BY id",
                "SELECT id FROM tpp WHERE k IN (1, 'x') ORDER BY id",
                "DROP TABLE tpp",
                "CREATE TABLE tpq (id INT, d DATE, PRIMARY KEY (id, d)) PARTITION BY RANGE COLUMNS(d) "
                "(PARTITION p0 VALUES LESS THAN ('2000-01-01'), PARTITION p1 VALUES LESS THAN (MAXVALUE))",
                "INSERT INTO tpq VALUES (1, '1999-12-31'), (2, '2024-02-29')",
                "SELECT id FROM tpq WHERE d = 20240229 ORDER BY id",
                "SELECT id FROM tpq WHERE d < '2000-01-01 00:00:01' ORDER BY id",
                "SELECT id FROM tpq WHERE d = TIMESTAMP'1999-12-31 00:00:00' ORDER BY id",
                "DROP TABLE tpq",
                "SET div_precision_increment = '6'",
                literal_select("1 / 3"),
                "SET div_precision_increment = 4.4",
                literal_select("@@div_precision_increment"),
                "SET div_precision_increment = 'x'",
                "SET div_precision_increment = 4",
                "SET @@session.autocommit = '1'",
                literal_select("@@autocommit"),
                "SET @@session.ob_query_timeout = 1e7",
                literal_select("@@ob_query_timeout"),
            ]],
            (),
        )
    )
    return units


Unit = namedtuple("Unit", "title setup blocks teardown")


def entry_unit(specs, entry):
    spec = spec_for(specs, entry)
    if spec is None:
        raise GeneratorError("no probe specification for {} ({})".format(entry.name, entry.item_type))
    blocks = []
    if spec.kind in ("call", "template"):
        body = call_probes(spec)
        extras = [e for e in spec.extra_templates if e in body]
        singles = [p for p in body if p not in extras]
        blocks.extend([p] for p in singles)
        if extras:
            blocks.append(list(extras))
    else:
        if spec.statements:
            blocks.append(list(spec.statements))
    call_name = spec.name_call
    if call_name is None and entry.internal and IDENTIFIER.match(entry.name):
        call_name = entry.name
    if call_name and IDENTIFIER.match(call_name):
        probe = literal_select("{}(1)".format(call_name))
        if not any(probe in block for block in blocks):
            blocks.append([probe])
    title = "{} [{} {}]".format(entry.name, entry.item_type, entry.cls)
    return Unit(title, tuple(spec.setup), blocks, tuple(spec.teardown)), spec


def unit_size(unit):
    return len(unit.setup) + sum(len(b) for b in unit.blocks) + len(unit.teardown)


def pack_units(units, limit, smallest=10):
    files = []
    current = []
    size = 0
    for unit in units:
        overhead = len(unit.setup) + len(unit.teardown)
        blocks = list(unit.blocks)
        pieces = []
        while blocks:
            room = limit - size - overhead
            need = sum(len(b) for b in blocks)
            if current and (room < min(need, smallest) or len(blocks[0]) > room):
                files.append(current)
                current = []
                size = 0
                continue
            taken = []
            count = 0
            while blocks and (not taken or count + len(blocks[0]) <= room):
                count += len(blocks[0])
                taken.append(blocks.pop(0))
            piece = Unit(unit.title, unit.setup, taken, unit.teardown)
            pieces.append((len(files), len(current)))
            current.append(piece)
            size += count + overhead
        if len(pieces) > 1:
            for number, (file_index, position) in enumerate(pieces, 1):
                holder = files[file_index] if file_index < len(files) else current
                old = holder[position]
                holder[position] = old._replace(title="{} (part {} of {})".format(unit.title, number, len(pieces)))
    if current:
        files.append(current)
    return files


def matrix_setup():
    ddl = ", ".join("{} {}".format(name, kind) for name, kind, _ in MATRIX_COLUMNS)
    statements = ["CREATE TABLE {} (id INT PRIMARY KEY, {})".format(MATRIX_TABLE, ddl)]
    names = ", ".join(name for name, _, _ in MATRIX_COLUMNS)
    tuples = []
    for row in range(8):
        tuples.append("({}, {})".format(row + 1, ", ".join(values[row] for _, _, values in MATRIX_COLUMNS)))
    statements.append("INSERT INTO {} (id, {}) VALUES {}".format(MATRIX_TABLE, names, ", ".join(tuples)))
    return statements


def mask_quoted(statement):
    masked = []
    quote = None
    index = 0
    while index < len(statement):
        char = statement[index]
        if quote is None:
            if char in "'\"`":
                quote = char
                masked.append("q")
            else:
                masked.append(char)
            index += 1
            continue
        if char == "\\" and quote != "`":
            masked.append("qq")
            index += 2
            continue
        if char == quote:
            if statement[index + 1 : index + 2] == quote:
                masked.append("qq")
                index += 2
                continue
            quote = None
        masked.append("q")
        index += 1
    if quote is not None:
        return None
    return "".join(masked)


STATEMENT_PROBLEMS = (
    (re.compile(r"#"), "a # comment marker"),
    (re.compile(r"--(?:\s|$)"), "a -- comment marker"),
    (re.compile(r"/\*(?!\+)"), "a /* comment that is not a hint"),
    (re.compile(r"\[\s*\]"), "an empty [] array, which the grammar rejects"),
    (re.compile(r"\b(?:ARRAY|MAP)\s*\(\s*\)", re.I), "an empty ARRAY() or MAP(), which the grammar rejects"),
)


def check_statement(statement):
    if ";" in statement.replace("CHAR(59)", ""):
        raise GeneratorError("statement contains a semicolon: {}".format(statement))
    if "\n" in statement:
        raise GeneratorError("statement spans lines: {}".format(statement))
    masked = mask_quoted(statement)
    if masked is None:
        raise GeneratorError("statement has an unterminated quote: {}".format(statement))
    for pattern, problem in STATEMENT_PROBLEMS:
        if pattern.search(masked):
            raise GeneratorError("statement has {} outside quotes: {}".format(problem, statement))


CaseFile = namedtuple("CaseFile", "text statements roles")


def render_file(section, pieces, with_matrix):
    lines = [
        "--disable_abort_on_error",
        "--enable_metadata",
        "--enable_warnings",
        "--echo # {}".format(section),
    ]
    statements = []
    roles = []

    def emit(statement, role):
        check_statement(statement)
        lines.append(statement + ";")
        lines.append("--echo errno $mysql_errno")
        statements.append(statement)
        roles.append(role)

    for statement in SESSION_SETTINGS:
        emit(statement, "session")
    if with_matrix:
        for statement in matrix_setup():
            emit(statement, "matrix")
    for piece in pieces:
        lines.append("--echo # {}".format(piece.title))
        for statement in piece.setup:
            emit(statement, "setup")
        for block in piece.blocks:
            for statement in block:
                emit(statement, "probe")
        for statement in piece.teardown:
            emit(statement, "teardown")
    if with_matrix:
        emit("DROP TABLE {}".format(MATRIX_TABLE), "teardown")
    return CaseFile("\n".join(lines) + "\n", statements, roles)


def group_units(groups):
    return [Unit(title, (), [[p] for p in probes], ()) for title, probes in groups]


TEMPORAL_TIMESTAMPS = (
    FIXED_TIMESTAMP,
    "1704067199.999999",
    "1709214296.5",
    "1709214296.499999",
)
TEMPORAL_MODES = (SESSION_SQL_MODE, SESSION_SQL_MODE + ",TIME_TRUNCATE_FRACTIONAL")
NOW_SOURCES = (
    "NOW()",
    "NOW(1)",
    "NOW(3)",
    "NOW(6)",
    "CURRENT_TIMESTAMP",
    "LOCALTIMESTAMP(4)",
    "UTC_TIMESTAMP(6)",
    "CURTIME(6)",
    "CURDATE()",
    "DATE_ADD(NOW(), INTERVAL -1 MICROSECOND)",
    "DATE_ADD(NOW(6), INTERVAL 500000 MICROSECOND)",
    "FROM_UNIXTIME(UNIX_TIMESTAMP(NOW(6)))",
)
TEMPORAL_LITERALS = (
    "TIMESTAMP'2024-02-29 23:59:59.999999'",
    "'2024-02-29 23:59:59.9999995'",
    "'2024-02-29 13:44:56.5'",
    "20240229235959.5",
    "20240229235959.4999995e0",
    "TIME'23:59:59.5'",
    "DATE'2024-02-29'",
    "FROM_UNIXTIME(1709251199.9999995)",
    "'9999-12-31 23:59:59.9999999'",
)
DATETIME_SCALES = tuple("d{}".format(n) for n in range(7))
OTHER_TEMPORAL_COLUMNS = ("s0", "s3", "s6", "t0", "t3", "t6", "dd")
TEMPORAL_CHUNK = 3
TEMPORAL_TABLE = (
    "CREATE TABLE tn (id INT PRIMARY KEY, src VARCHAR(80), "
    + ", ".join("d{n} DATETIME({n})".format(n=n) for n in range(7))
    + ", s0 TIMESTAMP(0) NULL DEFAULT NULL, s3 TIMESTAMP(3) NULL DEFAULT NULL, s6 TIMESTAMP(6) NULL DEFAULT NULL"
    + ", t0 TIME(0), t3 TIME(3), t6 TIME(6), dd DATE)"
)
TEMPORAL_RESET = (
    "SET sql_mode = '{}'".format(SESSION_SQL_MODE),
    "SET timestamp = {}".format(FIXED_TIMESTAMP),
    "SET time_zone = '+00:00'",
)


def quoted(text):
    return "'" + text.replace("'", "''") + "'"


def datetime_insert(row, source):
    return "INSERT INTO tn (id, src, {}) VALUES ({}, {}, {})".format(
        ", ".join(DATETIME_SCALES), row, quoted(source), ", ".join([source] * len(DATETIME_SCALES))
    )


def temporal_units():
    setup = (TEMPORAL_TABLE,)
    teardown = ("DROP TABLE tn",)
    units = []
    select_all = "SELECT id, src, {} FROM tn ORDER BY id".format(", ".join(DATETIME_SCALES))
    compare = (
        "SELECT id, d0 <= DATE_ADD(NOW(), INTERVAL -1 MICROSECOND) AS a, d0 = NOW() AS b, "
        "d6 = NOW(6) AS c, d0 < NOW(6) AS d, d3 >= NOW(3) AS e FROM tn ORDER BY id"
    )
    for timestamp in TEMPORAL_TIMESTAMPS:
        for mode in TEMPORAL_MODES:
            block = ["SET timestamp = {}".format(timestamp), "SET sql_mode = '{}'".format(mode)]
            block.append(
                "SELECT NOW() AS a, NOW(1) AS b, NOW(3) AS c, NOW(6) AS d, CURTIME(3) AS e, UTC_TIME(6) AS f, "
                "UNIX_TIMESTAMP() AS g, UNIX_TIMESTAMP(NOW(6)) AS h, CURDATE() AS i, UTC_DATE() AS j, "
                "CURRENT_TIMESTAMP(2) AS k"
            )
            for row, source in enumerate(NOW_SOURCES, 1):
                block.append(datetime_insert(row, source))
            block.append(select_all)
            block.append(compare)
            block.extend(TEMPORAL_RESET)
            units.append(
                Unit(
                    "now() into DATETIME(0..6): timestamp {} sql_mode {}".format(timestamp, mode),
                    setup,
                    [block],
                    teardown,
                )
            )
    for mode in TEMPORAL_MODES:
        blocks = []
        for first in range(0, len(TEMPORAL_LITERALS), TEMPORAL_CHUNK):
            block = []
            rows = []
            for row, source in enumerate(TEMPORAL_LITERALS[first : first + TEMPORAL_CHUNK], first + 1):
                rows.append(str(row))
                block.append("INSERT INTO tn (id, src) VALUES ({}, {})".format(row, quoted(source)))
                block.extend(
                    "UPDATE tn SET {c} = {e} WHERE id = {r}".format(c=column, e=source, r=row) for column in DATETIME_SCALES
                )
            block.append(
                "SELECT id, src, {} FROM tn WHERE id IN ({}) ORDER BY id".format(", ".join(DATETIME_SCALES), ", ".join(rows))
            )
            blocks.append(block)
        units.append(
            Unit(
                "temporal values into DATETIME(0..6), one scale per statement: sql_mode {}".format(mode),
                setup + ("SET sql_mode = '{}'".format(mode),),
                blocks,
                TEMPORAL_RESET + teardown,
            )
        )
    block = []
    for row, source in enumerate(NOW_SOURCES, 1):
        block.append("INSERT INTO tn (id, src) VALUES ({}, {})".format(row, quoted(source)))
        block.append("UPDATE tn SET s0 = {e}, s3 = {e}, s6 = {e} WHERE id = {r}".format(e=source, r=row))
        block.append("UPDATE tn SET t0 = {e}, t3 = {e}, t6 = {e} WHERE id = {r}".format(e=source, r=row))
        block.append("UPDATE tn SET dd = {e} WHERE id = {r}".format(e=source, r=row))
    block.append("SELECT id, src, s0, s3, s6, t0, t3, t6, dd FROM tn ORDER BY id")
    units.append(Unit("now() into TIMESTAMP(0, 3, 6), TIME(0, 3, 6) and DATE", setup, [block], teardown))
    blocks = []
    for first in range(0, len(TEMPORAL_LITERALS), TEMPORAL_CHUNK):
        block = []
        rows = []
        for row, source in enumerate(TEMPORAL_LITERALS[first : first + TEMPORAL_CHUNK], first + 1):
            rows.append(str(row))
            block.append("INSERT INTO tn (id, src) VALUES ({}, {})".format(row, quoted(source)))
            block.extend(
                "UPDATE tn SET {c} = {e} WHERE id = {r}".format(c=column, e=source, r=row) for column in OTHER_TEMPORAL_COLUMNS
            )
        block.append(
            "SELECT id, src, {} FROM tn WHERE id IN ({}) ORDER BY id".format(", ".join(OTHER_TEMPORAL_COLUMNS), ", ".join(rows))
        )
        blocks.append(block)
    units.append(
        Unit("temporal values into TIMESTAMP(0, 3, 6), TIME(0, 3, 6) and DATE, one column per statement", setup, blocks, teardown)
    )
    block = [
        "SET time_zone = '+08:00'",
        "INSERT INTO tn (id, src, d6, s6) VALUES (1, 'NOW(6) at +08:00', NOW(6), NOW(6))",
        "SELECT id, d6, s6, NOW(6) AS n, UTC_TIMESTAMP(6) AS u FROM tn ORDER BY id",
        "SET time_zone = '-05:30'",
        "SELECT id, d6, s6, NOW(6) AS n FROM tn ORDER BY id",
        "SET time_zone = '+00:00'",
        "SELECT id, d6, s6, UNIX_TIMESTAMP(s6) AS a, UNIX_TIMESTAMP(d6) AS b FROM tn ORDER BY id",
    ]
    block.extend(TEMPORAL_RESET)
    units.append(Unit("now() under other time zones", setup, [block], teardown))
    real = ["SET timestamp = DEFAULT"]
    for mode in TEMPORAL_MODES:
        real.append("SET sql_mode = '{}'".format(mode))
        real.append("DELETE FROM tn")
        real.append(
            "INSERT INTO tn (id, src, {}) VALUES (1, 'NOW(n) real clock', {})".format(
                ", ".join(DATETIME_SCALES), ", ".join("NOW({})".format(n) for n in range(7))
            )
        )
        real.append(datetime_insert(2, "NOW(6)"))
        real.append(
            "SELECT id, MICROSECOND(d0) = 0 AS a, MICROSECOND(d3) % 1000 = 0 AS b, MICROSECOND(d5) % 10 = 0 AS c, "
            "d6 >= d0 AS d, ABS(TIMESTAMPDIFF(MICROSECOND, d0, d6)) < 1000000 AS e, "
            "ABS(TIMESTAMPDIFF(MICROSECOND, d3, d6)) < 1000 AS f FROM tn WHERE id = 1"
        )
        real.append(
            "SELECT id, MICROSECOND(d0) = 0 AS a, ABS(TIMESTAMPDIFF(MICROSECOND, d0, d6)) <= 1000000 AS b, "
            "ABS(TIMESTAMPDIFF(MICROSECOND, d3, d6)) <= 1000 AS c, MICROSECOND(d3) % 1000 = 0 AS d FROM tn WHERE id = 2"
        )
    real.append(
        "SELECT NOW() = NOW(0) AS a, MICROSECOND(NOW()) = 0 AS b, NOW(6) >= NOW() AS c, "
        "TIMESTAMPDIFF(MICROSECOND, NOW(), NOW(6)) BETWEEN 0 AND 999999 AS d, "
        "TIMESTAMPDIFF(SECOND, NOW(6), SYSDATE(6)) BETWEEN -1 AND 60 AS e, CURDATE() = DATE(NOW()) AS f, "
        "UTC_TIMESTAMP() = NOW() AS g, CURTIME() = TIME(NOW()) AS h, UNIX_TIMESTAMP() = UNIX_TIMESTAMP(NOW()) AS i, "
        "MICROSECOND(NOW(3)) % 1000 = 0 AS j, NOW(6) > TIMESTAMP'2026-01-01 00:00:00' AS k"
    )
    real.extend(TEMPORAL_RESET)
    units.append(Unit("now() on the real clock: stable properties only", setup, [real], teardown))
    return units


MEASURED_COLUMNS = ("reached", "reach_basis", "eval_functions", "eval_functions_run")
CASE_FILE = re.compile(r"^s[1-7]_[a-z]+_\d{4}\.test$")


def entry_key(entry):
    return (entry.name, entry.item_type, entry.cls)


def build_corpus(entries, measured):
    specs = build_specs()
    unreached = []
    reached = []
    entry_units = OrderedDict()
    for entry in entries:
        unit, spec = entry_unit(specs, entry)
        entry_units[entry_key(entry)] = (unit, spec)
        if measured[entry_key(entry)]["reached"] == "1":
            reached.append(unit)
        else:
            unreached.append(unit)
    heads, cast_groups = cast_probe_groups()
    sections = (
        ("s1_unreached", unreached, True),
        ("s2_reached", reached, True),
        ("s3_cast", group_units(cast_groups), True),
        ("s4_compare", group_units(compare_probe_groups()), True),
        ("s5_arith", group_units(arithmetic_probe_groups()), True),
        ("s6_store", store_units() + default_units() + range_units() + [ctas_unit()], True),
        ("s7_temporal", temporal_units(), False),
    )
    files = OrderedDict()
    unit_files = {}
    for prefix, units, with_matrix in sections:
        for index, pieces in enumerate(pack_units(units, PROBES_PER_FILE), 1):
            name = "{}_{:04d}".format(prefix, index)
            files[name + ".test"] = render_file("{} {}".format(prefix, index), pieces, with_matrix)
            for piece in pieces:
                base = re.sub(r" \(part \d+ of \d+\)$", "", piece.title)
                unit_files.setdefault(base, [])
                if name not in unit_files[base]:
                    unit_files[base].append(name)
    rows = []
    for entry in entries:
        unit, spec = entry_units[entry_key(entry)]
        values = measured[entry_key(entry)]
        rows.append(
            OrderedDict(
                (
                    ("order", str(entry.order)),
                    ("name", entry.name),
                    ("item_type", entry.item_type),
                    ("class", entry.cls),
                    ("param_num", entry.param_num or ""),
                    ("internal", "1" if entry.internal else "0"),
                    ("registered", "debug build only" if entry.debug_only else "release"),
                    ("reached", values["reached"]),
                    ("reach_basis", values["reach_basis"]),
                    ("eval_functions", values["eval_functions"]),
                    ("eval_functions_run", values["eval_functions_run"]),
                    ("probes", str(unit_size(unit))),
                    ("sql", spec.sql),
                    ("constructor", entry.constructor),
                )
            )
        )
        rows[-1]["files"] = ",".join(unit_files.get(unit.title, []))
    return files, rows, heads


def tsv_text(rows):
    columns = TSV_COLUMNS + ("files",)
    lines = ["\t".join(columns)]
    for row in rows:
        cells = []
        for column in columns:
            cell = row[column]
            if "\t" in cell or "\n" in cell:
                raise GeneratorError("TSV cell with a tab or newline: {!r}".format(cell))
            cells.append(cell)
        lines.append("\t".join(cells))
    return "\n".join(lines) + "\n"


def read_measured(entries, tsv_path):
    try:
        lines = Path(tsv_path).read_text(encoding="utf-8").split("\n")
    except OSError as exc:
        raise GeneratorError("cannot read {}: {} (run the reach command first)".format(tsv_path, exc))
    header = lines[0].split("\t")
    missing = [c for c in ("name", "item_type", "class") + MEASURED_COLUMNS if c not in header]
    if missing:
        raise GeneratorError("{} lacks columns {}".format(tsv_path, ", ".join(missing)))
    measured = {}
    for line in lines[1:]:
        if not line:
            continue
        cells = dict(zip(header, line.split("\t")))
        measured[(cells["name"], cells["item_type"], cells["class"])] = {c: cells[c] for c in MEASURED_COLUMNS}
    keys = [entry_key(e) for e in entries]
    unknown = [k for k in keys if k not in measured]
    stale = [k for k in measured if k not in set(keys)]
    if unknown or stale:
        raise GeneratorError(
            "{} does not match the registry (new: {}; gone: {}); run the reach command".format(
                tsv_path, unknown[:5], stale[:5]
            )
        )
    return measured


def measure(entries, index, args):
    functions = export_function_counts(args.profdata, args.coverage_binary, args.llvm_bin, args.coverage_root)
    results = classify_reach(index, entries, functions)
    measured = {}
    for entry in entries:
        result = results[entry.cls]
        measured[entry_key(entry)] = {
            "reached": "1" if result["reached"] else "0",
            "reach_basis": result["basis"],
            "eval_functions": str(result["functions"]),
            "eval_functions_run": str(result["functions_run"]),
        }
    return measured


def write_outputs(files, rows, out_dir, tsv_path):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and CASE_FILE.match(path.name) and path.name not in files:
            path.unlink()
    for name, case in files.items():
        target = out_dir / name
        data = case.text.encode("utf-8")
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)
    Path(tsv_path).write_bytes(tsv_text(rows).encode("utf-8"))


def check_outputs(files, rows, out_dir, tsv_path):
    problems = []
    out_dir = Path(out_dir)
    present = sorted(p.name for p in out_dir.iterdir() if p.is_file() and CASE_FILE.match(p.name)) if out_dir.is_dir() else []
    for name in present:
        if name not in files:
            problems.append("stale file {}".format(name))
    for name, case in files.items():
        target = out_dir / name
        if not target.is_file():
            problems.append("missing file {}".format(name))
        elif target.read_bytes() != case.text.encode("utf-8"):
            problems.append("different file {}".format(name))
    tsv = Path(tsv_path)
    if not tsv.is_file() or tsv.read_bytes() != tsv_text(rows).encode("utf-8"):
        problems.append("different {}".format(tsv))
    return problems


def summary(files, rows, heads):
    sections = OrderedDict()
    for name, case in files.items():
        section = name.rsplit("_", 1)[0]
        stats = sections.setdefault(section, [0, 0])
        stats[0] += 1
        stats[1] += len(case.statements)
    reached = sum(1 for r in rows if r["reached"] == "1")
    lines = [
        "registered entries: {} (reached {}, unreached {})".format(len(rows), reached, len(rows) - reached),
        "CAST target heads from the grammar: {}".format(len(heads)),
    ]
    for section, (count, statements) in sections.items():
        lines.append("{}: {} files, {} statements".format(section, count, statements))
    lines.append(
        "total: {} files, {} statements".format(
            len(files), sum(len(case.statements) for case in files.values())
        )
    )
    return "\n".join(lines)


ERRNO_LINE = re.compile(r"^errno (-?\d+)$")
SETUP_ROLES = ("session", "matrix", "setup")


def recorded_errnos(text, statements):
    lines = text.split("\n")
    position = 0
    errnos = []
    for statement in statements:
        echo = statement + ";"
        found = None
        for index in range(position, len(lines)):
            if lines[index] == echo:
                found = index
                break
        errno = None
        if found is not None:
            for index in range(found + 1, len(lines)):
                match = ERRNO_LINE.match(lines[index])
                if match:
                    errno = int(match.group(1))
                    position = index + 1
                    break
        errnos.append(errno)
    return errnos


def recording_findings(files, record_dir):
    problems = []
    notes = []
    record_dir = Path(record_dir)
    for name, case in files.items():
        stem = name[: -len(".test")]
        result = record_dir / (stem + ".result")
        if not result.is_file():
            partial = record_dir / (stem + ".partial")
            problems.append("{}: no recording{}".format(stem, " (a .partial log exists)" if partial.is_file() else ""))
            continue
        text = result.read_text(encoding="utf-8", errors="replace")
        for statement, role, errno in zip(case.statements, case.roles, recorded_errnos(text, case.statements)):
            shown = statement if len(statement) <= 160 else statement[:157] + "..."
            if errno is None:
                problems.append("{}: statement or its errno line not found: {}".format(stem, shown))
            elif errno == 0:
                continue
            elif role in SETUP_ROLES:
                problems.append("{}: setup statement failed with errno {}: {}".format(stem, errno, shown))
            elif statement.startswith("CREATE TABLE tct"):
                notes.append("{}: CREATE TABLE ... AS SELECT failed with errno {}: {}".format(stem, errno, shown))
            elif "WHERE 1 = 0" in statement:
                notes.append("{}: type-only statement failed with errno {}: {}".format(stem, errno, shown))
            elif statement.startswith("SELECT") and "ORDER BY" in statement and "WHERE id = " not in statement:
                notes.append("{}: multi-row statement failed with errno {}: {}".format(stem, errno, shown))
    return problems, notes


def command_check_recording(args):
    index, entries = load_registry()
    measured = read_measured(entries, args.tsv)
    files, rows, heads = build_corpus(entries, measured)
    problems, notes = recording_findings(files, args.record_dir)
    for line in problems + notes:
        print(line)
    print(
        "{} files checked: {} problems (missing recordings, statements not found, failed setup "
        "statements), {} failed statements to review".format(len(files), len(problems), len(notes))
    )
    return 1 if problems else 0


def command_reach(args):
    index, entries = load_registry()
    measured = measure(entries, index, args)
    files, rows, heads = build_corpus(entries, measured)
    Path(args.tsv).write_bytes(tsv_text(rows).encode("utf-8"))
    print(summary(files, rows, heads))
    return 0


def command_cases(args):
    index, entries = load_registry()
    measured = read_measured(entries, args.tsv)
    files, rows, heads = build_corpus(entries, measured)
    if args.check:
        problems = check_outputs(files, rows, args.out, args.tsv)
        for problem in problems:
            print(problem)
        print(summary(files, rows, heads))
        return 1 if problems else 0
    write_outputs(files, rows, args.out, args.tsv)
    print(summary(files, rows, heads))
    return 0


def create_parser():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    subparsers = parser.add_subparsers(dest="command")
    reach = subparsers.add_parser(
        "reach",
        help="read the registry and the coverage profile and write expressions.tsv",
    )
    reach.add_argument("--profdata", default=str(DEFAULT_PROFDATA), help="merged llvm profile")
    reach.add_argument("--coverage-binary", default=str(DEFAULT_COVERAGE_BINARY), help="coverage-instrumented seekdb")
    reach.add_argument("--coverage-root", default=str(COVERAGE_ROOT), help="source tree the coverage binary was built from")
    reach.add_argument("--llvm-bin", default=str(DEFAULT_LLVM_BIN), help="directory holding llvm-cov and llvm-cxxfilt")
    reach.add_argument("--tsv", default=str(TSV_PATH), help="expressions.tsv to write")
    cases = subparsers.add_parser(
        "cases",
        help="write the .test files from the source and the reach flags in expressions.tsv",
    )
    cases.add_argument("--out", default=str(CASES_DIR), help="directory for the .test files")
    cases.add_argument("--tsv", default=str(TSV_PATH), help="expressions.tsv to read and rewrite")
    cases.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 1 if the files on disk differ from a fresh generation",
    )
    recording = subparsers.add_parser(
        "check-recording",
        help="read a recording of the corpus (the runner's --record-dir) and report failed setup "
        "statements, missing recordings and failed multi-row statements",
    )
    recording.add_argument("--record-dir", required=True, help="the runner's --record-dir of one corpus run")
    recording.add_argument("--tsv", default=str(TSV_PATH), help="expressions.tsv the corpus was generated from")
    return parser


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    try:
        if args.command == "reach":
            return command_reach(args)
        if args.command == "check-recording":
            return command_check_recording(args)
        return command_cases(args)
    except GeneratorError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
