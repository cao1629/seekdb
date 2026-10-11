"""Per against edge into observer: how many symbols are vector-index (defined under src/observer/vector_index)
vs inner-SQL / ex_rpc / other. Usage: python3 -I vector_share.py <classified.tsv>"""
import collections, sys
rows = [l.rstrip("\n").split("\t") for i, l in enumerate(open(sys.argv[1])) if i]
c = collections.defaultdict(collections.Counter)
for r in rows:
    if r[1] != "src/observer":
        continue
    d = r[8]
    if "observer/vector_index" in d or "/vector/" in r[6]:
        k = "vector_index"
    elif "ob_inner_sql_connection" in d or "create_inner_sql_connection" in r[4]:
        k = "inner_sql"
    elif "ob_ex_rpc" in d:
        k = "ex_rpc"
    elif "ob_req_time_service" in d:
        k = "req_time"
    elif "ob_mysql_end_trans_cb" in d:
        k = "end_trans_cb"
    elif "ob_tablet_runtime_meta_updater" in d:
        k = "tablet_report"
    elif "ob_server_runtime_controller" in d:
        k = "io_print"
    else:
        k = "other:" + r[4]
    c[r[0][4:]][k] += 1
tot = collections.Counter()
for e, cc in sorted(c.items()):
    print(e, "-> observer", dict(cc), "total", sum(cc.values()))
    tot.update(cc)
print("ALL", dict(tot), sum(tot.values()))
