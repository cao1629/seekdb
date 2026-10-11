"""For each source-level weak definition (OB_WEAK_SYMBOL / __attribute__((weak))), report from nm
whether any object defines the same symbol strongly, and in which module (same or other).

Usage: python3 -I weak_all.py <repo>
"""
import collections, concurrent.futures, os, re, subprocess, sys
repo = os.path.abspath(sys.argv[1])
root = os.path.join(repo, "build_release", "src")
LINE = re.compile(r"^\s*(?:[0-9a-fA-F]+\s+)?\((?P<sect>[^)]*)\)\s+(?:\(alignment [^)]*\)\s+)?(?P<attrs>.*?)external\s+(?P<name>\S+)\s*$")
objs = []
for d, dn, fn in os.walk(root):
    for f in fn:
        if f.endswith(".o") and "/CMakeFiles/" in os.path.join(d, f):
            objs.append(os.path.relpath(os.path.join(d, f), root))


def rd(rel):
    o = subprocess.run(["nm", "-m", "-g", os.path.join(root, rel)], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL).stdout.decode("utf-8", "replace")
    s, w = set(), set()
    for line in o.splitlines():
        m = LINE.match(line)
        if not m or m.group("sect") == "undefined":
            continue
        (w if "weak" in m.group("attrs").split() else s).add(m.group("name"))
    return s, w


with concurrent.futures.ThreadPoolExecutor(6) as p:
    res = list(p.map(rd, objs))
strong = collections.defaultdict(set)
weak = collections.defaultdict(set)
for o, (s, w) in zip(objs, res):
    mod = o.split("/")[0]
    for x in s:
        strong[x].add(mod)
    for x in w:
        weak[x].add(mod)
names = sorted(set(strong) | set(weak))
plain = [n[1:] if n.startswith("_") else n for n in names]
dem = subprocess.run(["c++filt"], input="\n".join(plain) + "\n", stdout=subprocess.PIPE,
                     universal_newlines=True).stdout.splitlines()
dmap = dict(zip(names, dem))
FUNCS = ["create_inner_sql_connection_for_proxy", "ob_obj_read_lob_data", "has_unfree_callback", "get_cpu_count",
         "futex_hook", "ob_abort", "allow_next_syslog", "ob_strerror", "need_to_print", "need_to_print_dba",
         "current_resource_owner_id", "lib_server_runtime_dispatch", "get_max_thread_num", "get_flt_span_manager",
         "handle_span_record", "smart_alloc", "smart_free", "do_breakpad_init", "alloc_worker", "common_yield",
         "request_finish_callback", "print_all_thread", "check_mem_status", "try_check_mem_status",
         "DefaultAllocator::alloc", "DefaultAllocator::free", "MdsAllocator::alloc", "MdsAllocator::free",
         "ObLSTxCtxMgr::init", "errsim_notify_mds_", "ObTxData::dec_ref", "ObTxDataAllocator::init",
         "ObTxDataAllocator::alloc", "ObTxCtxTable::acquire_ref_", "ObTxCtxTable::release_ref_", "mmap", "munmap", "mmap64"]
for fn in FUNCS:
    hits = [n for n in names if re.search(r"(^|[\s:])" + re.escape(fn) + r"\(", dmap[n]) or dmap[n] == fn]
    if not hits:
        print("%-40s not in any object (not compiled in this build)" % fn)
        continue
    for n in hits:
        print("%-40s weak_in=%s strong_in=%s  [%s]" % (fn, ",".join(sorted(weak[n])) or "-", ",".join(sorted(strong[n])) or "-", dmap[n][:90]))
