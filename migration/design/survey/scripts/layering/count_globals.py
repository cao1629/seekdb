"""Count uses of global-state macros/singletons in tracked src/ C/C++ files, per module.

Usage: python3 -I count_globals.py <repo>
Counts token occurrences (word-bounded) after stripping // and /* */ comments.
Excludes the definition lines (#define NAME).
"""
import collections, os, re, subprocess, sys

repo = os.path.abspath(sys.argv[1])
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE,
                       check=True).stdout.decode().split("\0")
files = [f for f in files if re.search(r"\.(h|hpp|ipp|c|cc|cpp|cxx|inl|def)$", f)]
PATTERNS = {
    "GCTX": r"\bGCTX\b",
    "GCONF": r"\bGCONF\b",
    "MTL(": r"\bMTL\s*\(",
    "MTL_*": r"\bMTL_[A-Z_]+\b",
    "OBSERVER": r"\bOBSERVER\b",
    "THIS_WORKER": r"\bTHIS_WORKER\b",
    "GET_MY_SESSION": r"\bGET_MY_SESSION\b",
    "::get_instance()": r"::\s*get_instance\s*\(\s*\)",
    "server_service<": r"\bserver_service\s*<",
    "server_runtime()": r"\bserver_runtime\s*\(\s*\)",
    "OB_WEAK_SYMBOL": r"\bOB_WEAK_SYMBOL\b",
    "RLOCAL": r"\bRLOCAL(?:_INIT|_EXTERN|_STATIC|_INLINE)?\b",
    "thread_local": r"\b(?:thread_local|__thread|_RLOCAL)\b",
    "OB_SERVER_BLOCK_MGR": r"\bOB_SERVER_BLOCK_MGR\b",
    "OB_IO_MANAGER": r"\bOB_IO_MANAGER\b",
    "GCTX.x_": r"\bGCTX\s*\.\s*[a-z_]+_\b",
    "GLOBAL_EXTERN/static Ob instance": r"\bstatic\s+Ob\w+\s+\w+\s*;",
    "ObTenantBase/ObTenantEnv": r"\bObTenant(?:Base|Env)\b",
    "MTL_SWITCH": r"\bMTL_SWITCH\b",
    "CURRENT_CONTEXT": r"\bCURRENT_CONTEXT\b",
    "CURRENT_ENTITY": r"\bCURRENT_ENTITY\b",
    "WITH_CONTEXT": r"\bWITH_CONTEXT\b",
    "OB_LOGGER": r"\bOB_LOGGER\b",
    "SERVER_STORAGE_META_SERVICE": r"\bSERVER_STORAGE_META_SERVICE\b",
    "ObCurTraceId::": r"\bObCurTraceId\s*::",
    "server_obj_pool/borrow": r"\b(?:server_obj_pool|borrow_server_object|return_server_object)\s*<",
    "g_server_runtime/server_runtime": r"\bg_server_runtime\b",
    "GCTX.schema_service_": r"\bGCTX\s*\.\s*schema_service_\b",
    "GCTX.sql_proxy_": r"\bGCTX\s*\.\s*sql_proxy_\b",
}
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
by_mod = collections.defaultdict(collections.Counter)
tot = collections.Counter()
files_hit = collections.defaultdict(set)
gctx_members = collections.Counter()
for f in files:
    try:
        text = open(os.path.join(repo, f), encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    text = COMMENT.sub(" ", text)
    text = re.sub(r"^\s*#\s*define\s+(GCTX|GCONF|MTL|OBSERVER|THIS_WORKER)\b[^\n]*", " ", text, flags=re.M)
    mod = f.split("/")[1]
    for k, p in PATTERNS.items():
        n = len(re.findall(p, text))
        if n:
            by_mod[k][mod] += n
            tot[k] += n
            files_hit[k].add(f)
    for m in re.findall(r"\bGCTX\s*\.\s*([a-z_]+_)\b", text):
        gctx_members[m] += 1
mods = sorted({m for c in by_mod.values() for m in c})
print("pattern\ttotal\tfiles\t" + "\t".join(mods))
for k in PATTERNS:
    print("%s\t%d\t%d\t%s" % (k, tot[k], len(files_hit[k]), "\t".join(str(by_mod[k][m]) for m in mods)))
print()
print("GCTX members used (top 40):")
for m, n in gctx_members.most_common(40):
    print("  %s\t%d" % (m, n))
print("distinct GCTX members used:", len(gctx_members))
