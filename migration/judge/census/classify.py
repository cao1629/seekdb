#!/usr/bin/env python3
"""Classify mysqltest / obtest cases by the implementation internals they reach through SQL.

Read-only. Usage: python3 -I classify.py <repo_root> <out_dir>
Each case = one .test file. Content scanned = the .test file plus every --source'd
include that exists in the repo (transitively). Comment lines (#...) and --echo lines
are stripped before matching SQL patterns. Expected-output (.result) checks are separate.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(sys.argv[1])
OUT = Path(sys.argv[2])
DEPLOY = ROOT / "tools/deploy"
MT = DEPLOY / "mysql_test"
OBT = ROOT / "tools/obtest"

I = re.I | re.M

# MySQL 8.0 / 5.7 standard information_schema tables (upper-case)
STD_IS = set("""
ADMINISTRABLE_ROLE_AUTHORIZATIONS APPLICABLE_ROLES CHARACTER_SETS CHECK_CONSTRAINTS COLLATIONS
COLLATION_CHARACTER_SET_APPLICABILITY COLUMNS COLUMNS_EXTENSIONS COLUMN_PRIVILEGES COLUMN_STATISTICS
ENABLED_ROLES ENGINES EVENTS FILES KEYWORDS KEY_COLUMN_USAGE OPTIMIZER_TRACE PARAMETERS PARTITIONS
PLUGINS PROCESSLIST PROFILING REFERENTIAL_CONSTRAINTS RESOURCE_GROUPS ROLE_COLUMN_GRANTS
ROLE_ROUTINE_GRANTS ROLE_TABLE_GRANTS ROUTINES SCHEMATA SCHEMATA_EXTENSIONS SCHEMA_PRIVILEGES
STATISTICS ST_GEOMETRY_COLUMNS ST_SPATIAL_REFERENCE_SYSTEMS ST_UNITS_OF_MEASURE TABLES
TABLES_EXTENSIONS TABLESPACES TABLESPACES_EXTENSIONS TABLE_CONSTRAINTS TABLE_CONSTRAINTS_EXTENSIONS
TABLE_PRIVILEGES TRIGGERS USER_ATTRIBUTES USER_PRIVILEGES VIEWS VIEW_ROUTINE_USAGE VIEW_TABLE_USAGE
GLOBAL_STATUS GLOBAL_VARIABLES SESSION_STATUS SESSION_VARIABLES
""".split())

CHARSET_INTRODUCERS = {"_utf8", "_utf8mb4", "_binary", "_latin1", "_gbk", "_gb18030", "_utf16",
                       "_utf16le", "_utf32", "_ascii", "_big5", "_ujis", "_sjis", "_euckr",
                       "_gb2312", "_cp1251", "_ucs2", "_gb18030_2022", "_dec8", "_hkscs"}

# --- categories over test content (SQL side) ---
SQL_CATS = {
    # tier 1: implementation internals reached through SQL
    "internal_tables": [r"__all_\w+", r"__tenant_virtual_\w+", r"(?<![\w$])g?v\$\w+",
                        r"\b(?:DBA|CDB)_(?:OB|TAB|IND|PART|SUBPART)_\w+", r"\boceanbase\s*\.\s*(?:__|dba_|cdb_|g?v\$)\w+"],
    "hidden_aux_objects": [r"(?<![\w$])__(?:idx|doc_id|word_segment|word_count|doc_length|pk_increment|ivf|vec)\w*",
                           r"(?<![\w$@.])_idx\d+_\w+", r"(?<![\w$@.])_vec_idx\w*"],
    "underscore_functions": [r"(?<![\w$@.])_st_\w+\s*\("],
    "hidden_params": [r"\balter\s+system\s+set\s+_\w+", r"(?<![\w$@.])_(?!st_|idx\d|vec_idx|fts_index)(?:enable|ob|lcl|show|mini|private|fast|transfer|parallel|backup|recyclebin|rowsets|force|restore|sort|datafile|px|bloom|optimizer)_[a-z0-9_]+",
                      r"(?<![\w$])__(?!all_|tenant_|idx|doc_|word_|pk_|ivf|vec)[a-z][a-z0-9]*_[a-z0-9_]+\s*="],
    "ob_internal_vars": [r"@@(?:global\.|session\.)?ob_(?!query_timeout|trx_timeout|trx_idle_timeout)\w+", r"\bset\s+(?:global\s+|session\s+|@@global\.|@@session\.|@@)?ob_(?!query_timeout|trx_timeout|trx_idle_timeout)\w+"],
    "ob_timeout_vars": [r"\bob_(?:query_timeout|trx_timeout|trx_idle_timeout)\b"],
    "alter_system_internal": [r"\balter\s+system\s+(?!set\s+(?:vector_memory_limit|vector_index_optimize_duty_time|ob_vector_memory_limit_percentage)\b)\w+"],
    "alter_system_public_knob": [r"\balter\s+system\s+set\s+(?:vector_memory_limit|vector_index_optimize_duty_time|ob_vector_memory_limit_percentage)\b"],
    "show_ob_specific": [r"\bshow\s+parameters\b", r"\bshow\s+trace\b", r"\bshow\s+tenant\b", r"\bshow\s+proxy\w*"],
    "explain_plan": [r"^\s*explain\b", r"\bdbms_xplan\b"],
    "trace_log": [r"\bshow\s+trace\b", r"sql_audit", r"last_trace_id", r"ob_enable_show_trace",
                  r"\b(?:observer|seekdb|rootservice|election)\.log\b", r"\bob_log_level\b"],
    "compaction_freeze": [r"\bmajor\s+freeze\b", r"\bminor\s+freeze\b", r"merger_check_interval",
                          r"__all_virtual_tablet_memstore_info", r"\bdaily_merge\b", r"\bfreeze\b"],
    "shell_proc_file": [r"^\s*-*\s*(?:exec|system|perl|remove_file|write_file|copy_file|cat_file|file_exists|mkdir|rmdir|shutdown_server|exec_in_background|move_file|diff_files|list_files|remove_files_wildcard)\b",
                        r"\$OBSERVER_DIR"],
    "debug_errsim": [r"\bdebug_sync\b", r"ob_global_debug_sync", r"\berrsim\b", r"\bset_tp\b", r"\btp_no\b", r"(?-i:\bEN_[A-Z_]{3,}\b)"],
    # tier 2: public but fragile / fidelity-forcing
    "timing_sleep": [r"^\s*-*\s*(?:real_)?sleep\b", r"\bsleep\s*\(", r"wait_condition"],
    "multi_session": [r"^\s*-*\s*connect\s*\(", r"^\s*-*\s*send\b", r"^\s*-*\s*reap\b"],
    "hints": [r"/\*\+"],
    "ob_packages": [r"\bdbms_\w+"],
    "load_data_file": [r"\bload\s+data\b", r"\binto\s+outfile\b"],
}
TIER1 = ["internal_tables", "hidden_aux_objects", "underscore_functions", "hidden_params", "ob_internal_vars", "alter_system_internal", "show_ob_specific", "explain_plan",
         "trace_log", "compaction_freeze", "shell_proc_file", "debug_errsim", "info_schema_nonstd"]
TIER1_RES = ["result_has_plan_text", "result_has_ob_storage_opts"]
TIER2 = ["error_code_ob_specific", "timing_sleep", "multi_session", "ob_timeout_vars", "alter_system_public_knob"]

# obtest-only categories
OBT_CATS = {
    "multi_node_or_multi_cluster": None,  # computed from OBI(...) specs
    "process_kill_restart": [r"\.(?:force_stop|nstart|stop|restart|kill)\b", r"\.start\b"],
    "network_fault": [r"\.(?:block_net|clean_net)\b"],
    "log_file_inspection": [r"\.(?:check_log_until_success|check_rs_log_until_success|count_log)\b"],
    "shell": [r"\.(?:sh|sh_p|rmdir)\b", r"^\s*-*\s*(?:exec|system)\b"],
    "distributed_internals": [r"\.(?:switch_partition|switch_rs|check_leader|check_state_in_congest|get_congestion_info)\b",
                              r"\bresource\s+(?:unit|pool)\b", r"\bcreate\s+tenant\b", r"\bzone_list\b|\blocality\b"],
}

SRC_RE = re.compile(r"^[ \t]*-*[ \t]*source[ \t]+([^;\s]+)", I)
ERR_RE = re.compile(r"^[ \t]*(?:--)?[ \t]*error[ \t]+([^;\n]+)", I)


def strip_for_sql(text):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if re.match(r"-*\s*echo\b", s, re.I):
            continue
        out.append(line)
    return "\n".join(out)


def resolve_include(ref, test_file, bases):
    ref = ref.strip().strip("'\"")
    cands = [b / ref for b in bases] + [test_file.parent / ref]
    for c in cands:
        if c.is_file():
            return c.resolve()
    return None


def gather(test_file, bases):
    seen, missing, order = set(), [], []
    stack = [test_file.resolve()]
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        order.append(f)
        try:
            txt = f.read_text(errors="replace")
        except OSError:
            continue
        for m in SRC_RE.finditer(txt):
            r = resolve_include(m.group(1), f, bases)
            if r is None:
                missing.append(m.group(1))
            else:
                stack.append(r)
    return order, missing


def error_codes(text):
    codes = []
    for m in ERR_RE.finditer(text):
        for tok in re.split(r"[,\s]+", m.group(1).strip()):
            if tok:
                codes.append(tok)
    return codes


def is_mysql_code(tok):
    if re.match(r"^ER_|^WARN_|^CR_", tok, re.I):
        return True
    if tok.isdigit():
        n = int(tok)
        return n < 5000  # >=5000 = OB-native error space (heuristic); 4000-4999 ambiguous (MySQL 8.0 extends there)
    if tok.startswith("$"):
        return True
    return True  # SQLSTATE-like or unknown -> treat as standard


def classify(test_file, bases, result_file=None, obtest=False):
    files, missing = gather(test_file, bases)
    raw = "\n".join(f.read_text(errors="replace") for f in files)
    sql = strip_for_sql(raw)
    hits = {}
    for cat, pats in SQL_CATS.items():
        for p in pats:
            m = re.search(p, sql, I)
            if m:
                tok = m.group(0)
                if cat == "hidden_params" and tok.lower() in CHARSET_INTRODUCERS:
                    # retry ignoring charset introducers
                    toks = [t for t in re.findall(p, sql, I) if t.lower() not in CHARSET_INTRODUCERS]
                    if not toks:
                        continue
                    tok = toks[0]
                hits[cat] = tok.strip()[:60]
                break
    codes = error_codes("\n".join(l for l in raw.splitlines() if not l.strip().startswith("#")))
    if codes:
        hits["error_directive"] = codes[0]
        ob_codes = [c for c in codes if not is_mysql_code(c)]
        if ob_codes:
            hits["error_code_ob_specific"] = ob_codes[0]
    # non-standard information_schema references
    nonstd = sorted({t.upper() for t in re.findall(r"information_schema\s*\.\s*`?(\w+)", sql, I)} - STD_IS)
    if nonstd:
        hits["info_schema_nonstd"] = ",".join(nonstd)[:60]
    if obtest:
        specs = re.findall(r"=\s*OBI\(([^)]*)\)", sql)
        multi = len(specs) > 1
        for s in specs:
            m = re.search(r"cluster\s*=\s*([0-9:]+)", s)
            if m and (":" in m.group(1) or int(m.group(1).split(":")[0]) > 1):
                multi = True
        if multi:
            hits["multi_node_or_multi_cluster"] = ";".join(specs)[:60]
        for cat, pats in OBT_CATS.items():
            if pats is None:
                continue
            for p in pats:
                m = re.search(p, sql, I)
                if m:
                    hits[cat] = m.group(0)[:60]
                    break
    # expected-output side
    res = {}
    if result_file is not None and result_file.is_file():
        rt = result_file.read_text(errors="replace")
        if re.search(r"\|\s*ID\s*\|\s*OPERATOR|Outputs & filters|Query Plan", rt):
            res["result_has_plan_text"] = True
        if re.search(r"BLOCK_SIZE\s*=|TABLET_SIZE\s*=|PCTFREE\s*=|COMPRESSION\s*=\s*'|ORGANIZATION\s+(?:INDEX|HEAP)", rt):
            res["result_has_ob_storage_opts"] = True
        if re.search(r"^ERROR\s+[0-9A-Z]{5}:", rt, re.M):
            res["result_has_error_text"] = True
        res["result_lines"] = rt.count("\n")
    return {
        "files": [str(f.relative_to(ROOT)) for f in files],
        "missing_includes": missing,
        "hits": hits,
        "res": res,
        "lines": raw.count("\n"),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ci = set((OUT / "ci_enabled.txt").read_text().split())
    rows = {}
    # mysqltest
    for f in sorted((MT / "t").glob("*.test")):
        name = f.stem
        rows["mt:" + name] = dict(classify(f, [DEPLOY, MT], MT / "r/mysql" / (name + ".result")), suite="<top>", ci=name in ci)
    for f in sorted((MT / "test_suite").glob("*/t/*.test")):
        suite = f.parent.parent.name
        name = f"{suite}.{f.stem}"
        rows["mt:" + name] = dict(classify(f, [DEPLOY, MT], f.parent.parent / "r/mysql" / (f.stem + ".result")), suite=suite, ci=name in ci)
    # obtest
    for f in sorted((OBT / "t").rglob("*.test")):
        rel = f.relative_to(OBT / "t")
        suite = rel.parts[0] if len(rel.parts) > 1 else "<top>"
        rpath = OBT / "r" / rel.with_suffix(".result")
        rows["ob:" + str(rel)] = dict(classify(f, [OBT, OBT / "t" / suite, ROOT / "tools"], rpath, obtest=True), suite=suite, ci=False)
    (OUT / "classification.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))

    def report(prefix, label, only_ci=False):
        sel = {k: v for k, v in rows.items() if k.startswith(prefix) and (v["ci"] or not only_ci)}
        n = len(sel)
        print(f"\n===== {label}: {n} cases =====")
        cats = Counter()
        for v in sel.values():
            for c in v["hits"]:
                cats[c] += 1
            for c in v["res"]:
                if c != "result_lines":
                    cats[c] += 1
        for c, k in sorted(cats.items(), key=lambda x: -x[1]):
            print(f"  {c:32s} {k:4d}  ({100.0*k/n:4.1f}%)")
        t1 = [k for k, v in sel.items() if any(c in v["hits"] for c in TIER1) or any(v["res"].get(c) for c in TIER1_RES) or (prefix == "ob:" and any(c in v["hits"] for c in OBT_CATS))]
        t2only = [k for k, v in sel.items() if k not in t1 and any(c in v["hits"] for c in TIER2)]
        clean = [k for k in sel if k not in t1 and k not in t2only]
        print(f"  -> tier1 internal-bound (>=1 tier1 hit): {len(t1)}")
        print(f"  -> tier2-only (fragile but no internals): {len(t2only)}")
        print(f"  -> clean public SQL (no tier1/tier2 hits): {len(clean)}")
        miss = Counter(m for v in sel.values() for m in v["missing_includes"])
        if miss:
            print("  missing includes:", dict(miss.most_common(8)))
        return t1, t2only, clean

    out = {}
    out["mt_all"] = report("mt:", "mysqltest ALL")
    out["mt_ci"] = report("mt:", "mysqltest CI-enabled", only_ci=True)
    out["ob_all"] = report("ob:", "obtest ALL")
    for key, (t1, t2, cl) in out.items():
        (OUT / f"{key}_tier1.txt").write_text("\n".join(t1) + "\n")
        (OUT / f"{key}_tier2only.txt").write_text("\n".join(t2) + "\n")
        (OUT / f"{key}_clean.txt").write_text("\n".join(cl) + "\n")


if __name__ == "__main__":
    main()
