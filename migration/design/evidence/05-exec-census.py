import collections, pathlib, re, subprocess
ns = {"__name__": "census"}
exec(subprocess.check_output(["git", "show", "834bbee1e:.github/script/seekdb/mysqltest_for_seekdb.py"]), ns)
root = pathlib.Path.cwd()
SKIP_WORDS = {"let", "connect", "connection", "disconnect", "sleep", "real_sleep", "inc", "dec", "echo", "source",
              "die", "exit", "reap", "vertical_results", "horizontal_results", "disable_query_log", "enable_query_log",
              "disable_warnings", "enable_warnings", "disable_info", "enable_info", "disable_abort_on_error",
              "enable_abort_on_error", "replace_result", "replace_column", "replace_regex", "result_format", "system",
              "exec", "remove_file", "write_file", "append_file", "cat_file", "perl", "ping", "skip", "require",
              "explain_protocol", "disable_ps_protocol", "enable_ps_protocol", "while", "if", "end"}
TRIGGERS = [
    ("join", re.compile(r"\b(join|straight_join)\b")),
    ("group by", re.compile(r"\bgroup\s+by\b")),
    ("union/intersect/except/minus", re.compile(r"\b(union|intersect|except|minus)\b")),
    ("select distinct", re.compile(r"^(\(\s*)*select\s+(/\*\+.*?\*/\s*)?(all\s+)?(distinct|distinctrow)\b")),
    ("window function", re.compile(r"\bover\s*\(|\bover\s+[a-z_]\w*")),
    ("with (CTE)", re.compile(r"^(\(\s*)*with\b")),
]
SET_OP = re.compile(r"\b(union|intersect|except|minus)\b")
def mask(sql):
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"`":
            j = i + 1
            while j < n and sql[j] != ch:
                j += 2 if sql[j] == "\\" else 1
            out.append(" x ")
            i = j + 1
        elif sql.startswith("/*", i) and not sql.startswith("/*+", i):
            j = sql.find("*/", i + 2)
            out.append(" ")
            i = n if j < 0 else j + 2
        elif ch == "#" or sql.startswith("-- ", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        else:
            out.append(ch)
            i += 1
    return " ".join("".join(out).lower().split())
def depth0(sql):
    out, depth = [], 0
    for ch in sql:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)
def split_statements(text, delim):
    parts, start, i, n, quote, comment = [], 0, 0, len(text), None, None
    while i < n:
        ch = text[i]
        if comment == "line":
            if ch == "\n":
                comment = None
        elif comment == "block":
            if text.startswith("*/", i):
                comment, i = None, i + 1
        elif quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == "#" or text.startswith("-- ", i):
            comment = "line"
        elif text.startswith("/*", i):
            comment, i = "block", i + 1
        elif text.startswith(delim, i):
            parts.append(text[start:i])
            start = i + len(delim)
            i = start
            continue
        i += 1
    return parts, text[start:], quote is not None or comment == "block"
def statements(path):
    delim, buf, first_line = ";", "", None
    sorted_next, result_on, error_next = False, True, None
    def directive(word, rest):
        nonlocal sorted_next, result_on, error_next, delim
        if word == "sorted_result":
            sorted_next = True
        elif word == "disable_result_log":
            result_on = False
        elif word == "enable_result_log":
            result_on = True
        elif word == "error":
            error_next = rest
        elif word == "delimiter":
            delim = rest.strip()
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not buf.strip():
            buf = ""
            if not line or line.startswith("#"):
                continue
            if line.startswith("--"):
                parts = line[2:].strip().split(None, 1)
                word = parts[0].lower().rstrip(";") if parts else ""
                rest = parts[1] if len(parts) > 1 else ""
                if word in ("eval", "send", "query") and rest:
                    yield number, rest.rstrip().rstrip(";"), sorted_next, result_on, error_next
                    sorted_next, error_next = False, None
                else:
                    directive(word, rest)
                continue
            if re.match(r"^(if|while)\s*\(.*\)\s*\{?\s*(#.*)?$", line) or re.match(r"^\}?\s*(else\s*)?\{?\s*$", line):
                continue
            first_line = number
        buf += raw + "\n"
        parts, buf, open_ = split_statements(buf, delim)
        for stmt in parts:
            stmt = stmt.strip()
            if not stmt:
                continue
            words = stmt.split(None, 1)
            word, rest = words[0].lower(), (words[1] if len(words) > 1 else "")
            if word in ("sorted_result", "disable_result_log", "enable_result_log", "error", "delimiter"):
                directive(word, rest)
                continue
            if word in SKIP_WORDS or word.startswith("$") or word in ("{", "}"):
                continue
            if word in ("eval", "send", "query"):
                stmt = rest
            yield first_line, stmt, sorted_next, result_on, error_next
            sorted_next, error_next = False, None
            first_line = number
        if buf.strip():
            first_line = first_line if first_line else number
stats = collections.Counter()
triggers = collections.Counter()
rows = []
for case in ns["discover_cases"](root):
    for line, stmt, sorted_flag, result_on, error in statements(case.test_file):
        sql = mask(stmt)
        if not re.match(r"^(\(\s*)*(select|with)\b", sql):
            continue
        stats["SELECT or WITH statements"] += 1
        top = depth0(sql)
        if re.search(r"\border\s+by\b", top):
            stats["skipped: ORDER BY outside parentheses"] += 1
            continue
        if not re.search(r"\bfrom\b", sql):
            stats["skipped: no FROM"] += 1
            continue
        if re.search(r"\binto\b", top):
            stats["skipped: SELECT ... INTO"] += 1
            continue
        if error is not None and not re.search(r"(^|[\s,])0([\s,;]|$)", error.strip()):
            stats["skipped: under --error (expected to fail)"] += 1
            continue
        if sorted_flag:
            stats["skipped: under --sorted_result"] += 1
            continue
        if not result_on:
            stats["skipped: result log disabled"] += 1
            continue
        hits = [name for name, pattern in TRIGGERS if pattern.search(sql)]
        if len(re.findall(r"\bselect\b", sql)) > 1 + len(SET_OP.findall(sql)):
            hits.append("subquery")
        clause = re.search(r"\bfrom\b(.*?)(\bwhere\b|\bgroup\b|\bhaving\b|\blimit\b|\bwindow\b|\bfor\b|\block\b|\bunion\b|$)", top)
        if clause and "," in clause.group(1):
            hits.append("comma in FROM")
        if not hits:
            stats["skipped: single-table"] += 1
            continue
        stats["candidates"] += 1
        triggers.update(hits)
        rows.append((case.name, line, " ".join(stmt.split())))
for name in ["SELECT or WITH statements", "skipped: no FROM", "skipped: ORDER BY outside parentheses",
             "skipped: SELECT ... INTO", "skipped: under --error (expected to fail)", "skipped: under --sorted_result",
             "skipped: result log disabled", "skipped: single-table", "candidates"]:
    print("# {}: {}".format(name, stats[name]))
for name, count in sorted(triggers.items(), key=lambda item: (-item[1], item[0])):
    print("# candidates with {}: {}".format(name, count))
for row in rows:
    print(*row, sep="\t")
