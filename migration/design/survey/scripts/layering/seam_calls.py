"""Call sites (occurrences outside the implementing module) of key seams, per using module.
Usage: python3 -I seam_calls.py <repo>"""
import collections, os, re, subprocess, sys
repo = os.path.abspath(sys.argv[1])
files = subprocess.run(["git", "-C", repo, "ls-files", "-z", "src"], stdout=subprocess.PIPE, check=True).stdout.decode().split("\0")
files = [f for f in files if re.search(r"\.(h|hpp|ipp|c|cc|cpp|cxx|inl)$", f)]
COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
SEAMS = {
    "ex_rpc::sync_call/async_call": (r"\bex_rpc::(?:sync_call|async_call)\w*\s*[<(]", "observer/ob_ex_rpc"),
    "ObInnerSQLConnectionAccess::": (r"\bObInnerSQLConnectionAccess::\w+\s*\(", "observer/ob_inner_sql_connection"),
    "ObVectorIndexUtil::": (r"\bObVectorIndexUtil::\w+\s*\(", "observer/vector_index/"),
    "ObPluginVectorIndexAdaptor (type uses)": (r"\bObPluginVectorIndexAdaptor\b", "observer/vector_index/"),
    "data_plane::tx_desc_*": (r"\bdata_plane::tx_desc_\w+\s*\(", "storage/tx/ob_tx_api"),
    "data_plane::tmp_file_*": (r"\bdata_plane::tmp_file_\w+\s*\(", "storage/tmp_file/"),
    "query::das_scan_*": (r"\bquery::das_scan_\w+\s*\(", "sql/das/"),
    "query::*spill_row_store*": (r"\bquery::\w*spill_row_store\w*\s*\(", "sql/engine/basic/ob_chunk_datum_store"),
    "data_plane::(report|renew|rebuild|load)_ddl/vector": (r"\bdata_plane::(?:report_column_checksum_response|report_ddl_single_replica_response|renew_ddl_task_lease|rebuild_vector_index|load_idempotent_ddl_tablet_slice_counts)\s*\(", "rootserver/"),
    "data_plane::*tablet_update*": (r"\bdata_plane::\w*tablet_update\w*\s*\(", "observer/ob_tablet_runtime_meta_updater"),
}
res = collections.defaultdict(collections.Counter)
for f in files:
    t = COMMENT.sub(" ", open(os.path.join(repo, f), encoding="utf-8", errors="replace").read())
    for k, (p, impl) in SEAMS.items():
        if impl in f:
            continue
        n = len(re.findall(p, t))
        if n:
            res[k][f.split("/")[1]] += n
for k in SEAMS:
    print("%-48s total=%4d  %s" % (k, sum(res[k].values()), dict(res[k].most_common())))
