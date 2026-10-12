#!/usr/bin/env python3
"""c17-bugs: code sites of the 15 suspected bugs of migration/00b-judge-report.md §6 D, plus the KV cache
retire condition (RULEBOOK 0.8, verification.md C06), as bug 16.

The site table below was located by reading the code (agent investigation, 2026-10-11) and is checked
mechanically on every run: each anchor must occur on the stated line of the original file, otherwise the
script exits non-zero. The symbol is `<enclosing function>#bugNN` (the scope parser names the function;
for the bison grammar the rule name is used); several sites per bug give several rows.
RULEBOOK §5.2 #17: bugs 13-15 need not be tagged; their sites are listed with low/medium confidence.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sweeplib as L  # noqa: E402

CAT = 'c17-bugs'
# (bug, file, line, anchor substring, fallback name, confidence, note)
SITES = [
    (1, 'src/sql/engine/cmd/ob_merge_table_executor.cpp', 129, 'plan_ctx->set_affected_rows(total_affected_rows);', 'ObMergeTableExecutor::execute', 'high', 'stores INSERT+UPDATE total as the OK-packet affected rows'),
    (1, 'src/sql/ob_result_set.cpp', 234, 'set_affected_rows(get_exec_context().get_physical_plan_ctx()', 'ObResultSet::open_result', 'high', 'command results copy plan-ctx affected rows: client sees 7'),
    (1, 'src/sql/ob_result_set.cpp', 532, 'if (!ObStmt::is_dml_stmt(get_stmt_type())', 'ObResultSet::store_affected_rows', 'high', 'T_MERGE_TABLE is not DML, so session affected_rows becomes 0'),
    (1, 'src/sql/engine/expr/ob_expr_row_count.cpp', 54, 'expr_datum.set_int(session_info->get_affected_rows());', 'ObExprRowCount::eval_row_count', 'high', 'ROW_COUNT() reads the session value'),
    (2, 'src/sql/resolver/cmd/ob_merge_table_resolver.cpp', 218, 'stmt.get_strategy() == MERGE_STRATEGY_THEIRS', 'ObMergeTableResolver::build_merge_sqls_', 'high', 'THEIRS builds a two-way UPDATE without a merge base'),
    (2, 'src/sql/resolver/cmd/ob_merge_table_resolver.cpp', 232, 'update_sql.append(" WHERE NOT ("', 'ObMergeTableResolver::build_merge_sqls_', 'high', 'overwrites every differing row whichever side changed it'),
    (2, 'src/sql/resolver/cmd/ob_merge_table_resolver.cpp', 205, 'insert_sql.append(" WHERE c."', 'ObMergeTableResolver::build_merge_sqls_', 'high', 'anti-join INSERT re-inserts rows current deleted; no DELETE is built'),
    (2, 'src/sql/engine/cmd/ob_merge_table_executor.cpp', 93, 'if (OB_SUCC(ret) && !stmt.get_insert_sql().empty()) {', 'ObMergeTableExecutor::execute', 'medium', 'executor runs only INSERT and UPDATE, no delete step'),
    (3, 'src/sql/resolver/cmd/ob_diff_table_resolver.cpp', 170, "'INSERT' AS `__flag`", 'ObDiffTableResolver::build_diff_sql_', 'high', 'branch 1 (current-only rows) hard-codes INSERT'),
    (3, 'src/sql/resolver/cmd/ob_diff_table_resolver.cpp', 192, "'INSERT' AS `__flag`", 'ObDiffTableResolver::build_diff_sql_', 'high', 'branch 2 (incoming-only rows) hard-codes INSERT'),
    (3, 'src/sql/resolver/cmd/ob_diff_table_resolver.cpp', 215, "'INSERT' AS `__flag`", 'ObDiffTableResolver::build_diff_sql_', 'high', 'branch 3 (current version of a differing row) hard-codes INSERT'),
    (3, 'src/sql/resolver/cmd/ob_diff_table_resolver.cpp', 236, "'INSERT' AS `__flag`", 'ObDiffTableResolver::build_diff_sql_', 'high', 'branch 4 (incoming version of a differing row) hard-codes INSERT'),
    (4, 'src/sql/resolver/ob_resolver_utils.cpp', 7359, 'LOG_USER_ERROR(OB_SCHEMA_ERROR, col_def_msg);', 'ObResolverUtils::collect_and_validate_columns', 'high', 'OB_SCHEMA_ERROR format has no %s: detail text dropped (same at :7346)'),
    (4, 'src/sql/resolver/ob_schema_checker.cpp', 485, '} else if (NULL == table) {', 'ObSchemaChecker::get_table_schema', 'high', 'OB_TABLE_NOT_EXIST without LOG_USER_ERROR(db, table): 1146 has no name'),
    (4, 'src/sql/resolver/cmd/ob_diff_table_resolver.cpp', 57, 'collect_and_validate_columns_(cur_schema, inc_schema', 'ObDiffTableResolver::resolve', 'high', 'column validation runs in the resolver (MERGE: ob_merge_table_resolver.cpp:68)'),
    (4, 'src/sql/ob_sql.cpp', 2270, 'ObPrivilegeCheck::check_privilege_new(sql_ctx,', 'ObSql::generate_physical_plan', 'high', 'privilege check runs after resolve, so 4029 precedes 1142'),
    (5, 'src/rootserver/fork_table/ob_fork_database_service.cpp', 142, 'schema_guard.get_table_schemas_in_database(', 'ObDDLService::fork_database', 'medium', 'only table schemas are enumerated'),
    (5, 'src/rootserver/fork_table/ob_fork_database_service.cpp', 152, '} else if (!table_schema->is_user_table()) {', 'ObDDLService::fork_database', 'high', 'views are skipped silently'),
    (5, 'src/rootserver/fork_table/ob_fork_database_service.cpp', 191, 'parent_table_schema->get_database_id() != src_database_id', 'ObDDLService::fork_database', 'high', 'cross-database foreign keys only logged, never rebuilt'),
    (6, 'src/sql/resolver/tcl/ob_start_trans_resolver.cpp', 74, 'start_stmt->set_with_consistent_snapshot(true);', 'ObStartTransResolver::resolve', 'high', 'flag recorded but never read'),
    (6, 'src/sql/engine/cmd/ob_tcl_executor.cpp', 57, 'explicit_start_trans(ctx, stmt.get_read_only()', 'ObStartTransExecutor::start_trans', 'high', 'snapshot flag not passed on'),
    (6, 'src/storage/tx/ob_tx_api.cpp', 248, 'ObTransService::start_tx(ObTxDesc &tx', 'ObTransService::start_tx', 'high', 'start_tx resets snapshot_version_ and takes no snapshot'),
    (6, 'src/storage/tx/ob_tx_api.cpp', 684, '!tx.snapshot_version_.is_valid()/*version invalid*/', 'ObTransService::get_read_snapshot', 'high', 'RR snapshot taken lazily by the first read'),
    (7, 'src/data_plane/api/data_plane/transaction/ob_tx_options.h', 50, 'is_RR_or_SERIAL_isolevel(const ObTxIsolationLevel isolation)', 'transaction::is_RR_or_SERIAL_isolevel', 'high', 'SERIALIZABLE grouped with RR (snapshot isolation)'),
    (7, 'src/storage/tx/ob_tx_api.cpp', 681, '} else if (is_RR_or_SERIAL_isolevel(isolation)) {', 'ObTransService::get_read_snapshot', 'high', 'SERIAL gets one snapshot; reads not tracked'),
    (7, 'src/storage/memtable/mvcc/ob_mvcc_row.cpp', 1053, 'int ObMvccRow::mvcc_write(ObStoreCtx &ctx,', 'ObMvccRow::mvcc_write', 'medium', 'only per-row write-write conflict check'),
    (8, 'src/sql/parser/sql_parser_mysql_mode.y', 10253, 'LOCK_ IN SHARE MODE', 'opt_lock_in_share_mode', 'high', 'same T_SFU_INT node as FOR UPDATE'),
    (8, 'src/sql/parser/sql_parser_mysql_mode.y', 8503, '| opt_lock_in_share_mode', 'opt_lock_type', 'medium', 'share-mode node indistinguishable from FOR UPDATE'),
    (8, 'src/sql/resolver/dml/ob_select_resolver.cpp', 1127, 'wait_us = node.value_ < 0 ? -1 : node.value_ * 1000000LL;', 'ObSelectResolver::resolve_for_update_clause_mysql', 'high', 'resolved as FOR UPDATE with infinite wait'),
    (8, 'src/sql/resolver/dml/ob_select_resolver.cpp', 1155, 'table_item->for_update_ = true;', 'ObSelectResolver::set_for_update_mysql', 'high', 'exclusive row-lock path'),
    (9, 'src/sql/code_generator/ob_tsc_cg_service.cpp', 1906, 'est_cost_info->table_meta_info_->table_row_count_ /', 'ObTscCgService::generate_text_ir_ctdef', 'high', 'doc count from optimizer estimate at plan time'),
    (9, 'src/sql/code_generator/ob_tsc_cg_service.cpp', 1908, 'ir_scan_ctdef->estimated_total_doc_cnt_ = partition_row_cnt;', 'ObTscCgService::generate_text_ir_ctdef', 'high', 'stored in the cached plan ctdef'),
    (9, 'src/storage/retrieval/ob_inv_idx_param_estimator.cpp', 230, 'total_doc_cnt.set_int(est_ctx_.estimated_total_doc_cnt_);', 'ObBM25ParamEstimator::do_estimation', 'high', 'plan value used as BM25 N at execution'),
    (9, 'src/sql/das/ob_das_ir_define.h', 151, 'return 0 != estimated_total_doc_cnt_;', 'ObDASIRScanCtDef::need_estimate_total_doc_cnt', 'medium', 'non-zero estimate disables the runtime doc count'),
    (10, 'src/observer/composition/retrieval/ob_das_legacy_tr_merge_iter.cpp', 1049, '} else if (BOOLEAN_MODE == ir_ctdef->mode_flag_) {', 'ObDASLegacyTRMergeIter::build_query_tokens', 'medium', 'boolean branch never consults the index parser'),
    (10, 'src/observer/composition/retrieval/ob_das_legacy_tr_merge_iter.cpp', 1081, 'fts_parse_docment(static_cast<char *>(buf)', 'ObDASLegacyTRMergeIter::build_query_tokens', 'high', 'split only by the boolean grammar'),
    (10, 'src/sql/das/iter/ob_das_text_retrieval_eval_node.cpp', 237, 'OB_FAIL(tokens.push_back(token_string))', 'ObFtsEvalNode::fts_boolean_node_create', 'high', 'each boolean term pushed as one token'),
    (10, 'src/observer/composition/retrieval/ob_das_legacy_tr_merge_iter.cpp', 1107, 'table_param_.get_parser_name()', 'ObDASLegacyTRMergeIter::build_query_tokens', 'medium', 'only natural-language mode uses the index parser'),
    (11, 'src/sql/engine/expr/ob_expr_vec_ivf_sq8_data_vector.cpp', 154, '} else if (meta_vectors.empty()) {', 'ObExprVecIVFSQ8DataVector::generate_data_vector', 'high', 'empty SQ8 meta: vectors quantised to zeros'),
    (11, 'src/sql/das/iter/ob_das_ivf_scan_iter.cpp', 2986, 'if (OB_ISNULL(min_vec.ptr()) || OB_ISNULL(step_vec.ptr())) {', 'ObDASIvfSQ8ScanIter::get_real_search_vec_u8', 'high', 'query vector zero too: all distances tie'),
    (11, 'src/sql/das/iter/ob_das_ivf_scan_iter.cpp', 1284, 'if (near_cid_.count() == 0) {', 'ObDASIvfScanIter::get_nearest_limit_rowkeys_in_cids', 'medium', 'no centroids: heap of tied distances'),
    (11, 'src/storage/ddl/ob_ddl_pipeline.cpp', 629, 'helper->get_result(cur_row_pos_, cur_vector)', 'ObIVFSq8MetaRowIterator::get_next_row', 'medium', 'only writer of SQ8 min/max/step rows'),
    (12, 'src/sql/hybrid_search/ob_query_parse.cpp', 97, '} else if (key.case_compare("size") == 0) {', 'ObESQueryParser::parse', 'high', 'case-insensitive check accepts SIZE, ignored'),
    (12, 'src/sql/hybrid_search/ob_query_parse.cpp', 253, 'req_node.get_object_value(size_key, size_node)', 'ObESQueryParser::init_default_params', 'high', 'case-sensitive lookup misses SIZE'),
    (12, 'src/sql/hybrid_search/ob_query_parse.cpp', 245, 'req_node.get_object_value(fusion_key, fusion_node)', 'ObESQueryParser::init_default_params', 'medium', 'same mismatch for RANK (and ES_MODE at :61)'),
    (12, 'src/sql/das/iter/sparse_retrieval/ob_das_match_iter.cpp', 826, 'child_param_key.compare_equal(param_type)', 'ObDASMatchIter::get_match_param', 'high', 'unknown option keys fall through silently'),
    (13, 'src/query/protocol/ob_mysql_protocol_util.cpp', 241, 'out.value_ = static_cast<uint64_t>(value);', 'ObSMUtils::build_cell_value', 'medium', 'binary INT sent unpadded (MySQL-compatible)'),
    (13, 'src/query/protocol/ob_mysql_protocol_util.cpp', 238, 'set_zerofill_bytes(scratch_allocator, ffi.ptr()', 'ObSMUtils::build_cell_value', 'low', 'only the text protocol pads'),
    (13, 'src/oblib/common/ob_field.cpp', 205, 'int ObField::get_field_mb_length(const ObObjType type,', 'ObField::get_field_mb_length', 'low', 'integer column length = display width 255'),
    (13, 'src/observer/mysql/obmp_packet_sender.cpp', 107, 'view.column_length = static_cast<uint32_t>(field.length_);', 'make_field_view', 'medium', 'length 255 + ZEROFILL_FLAG; client pads and reports 101'),
    (14, 'src/oblib/lib/vector/ob_vsag_adaptor.cpp', 708, ',\\"doc_prune_ratio\\":%f", doc_prune_ratio', 'obvsag::construct_vsag_sindi_create_param', 'medium', 'drop_ratio_build passed to the linked VSAG build'),
    (14, 'src/observer/vector_index/ob_plugin_vector_index_adaptor.cpp', 859, 'if(is_sparse_vector_index_type()) {', 'ObPluginVectorIndexAdaptor::init_mem_data', 'low', 'snapshot SINDI index created with ob_sparse_drop_ratio_build_'),
    (14, 'src/oblib/lib/vector/ob_vsag_adaptor.cpp', 782, '"\\"query_prune_ratio\\":%f", query_prune_ratio', 'obvsag::construct_vsag_sindi_search_param', 'low', 'drop_ratio_search passed to VSAG'),
    (14, 'src/oblib/lib/vector/ob_vsag_adaptor.cpp', 1250, 'min_vsag_topk = n_candidate / VSAG_SINDI_N_CANDIDATE_FACTOR', 'obvsag::knn_search', 'low', 'local adjustment of the internal k'),
    (15, 'src/storage/deadlock/ob_lcl_scheme/ob_lcl_node.cpp', 191, '((baseline_ts / lcl_op_interval) + 1) * lcl_op_interval', 'ObLCLNode::register_timer_with_necessary_retry_with_lock_', 'medium', 'ticks aligned to _lcl_op_interval (5 s)'),
    (15, 'src/storage/deadlock/ob_lcl_scheme/ob_lcl_node.cpp', 564, 'if ((current_ts / PHASE_TIME) % 2 == 0) {', 'ObLCLNode::process_lcl_state', 'low', 'detection waits for a tick in the right phase'),
    (15, 'src/storage/tx/ob_trans_deadlock_adapter.cpp', 273, 'register_key(self_tx_id,', 'ObTransDeadlockDetectorAdapter::register_to_deadlock_detector_', 'medium', '3 s start delay, count_down_allow_detect 10'),
    (15, 'src/storage/memtable/ob_lock_wait_mgr.cpp', 478, 'curr_ts - iter->lock_ts_ > MAX_WAIT_TIME_US/2', 'ObLockWaitMgr::check_timeout', 'low', 'forced wake-up resets detector progress'),
    (16, 'src/share/cache/ob_kvcache_hazard_version.cpp', 93, 'while (head != nullptr) {', 'ObKVCacheHazardSlot::retire', 'high', 'walks the whole delete list with no version filter'),
    (16, 'src/share/cache/ob_kvcache_hazard_version.cpp', 96, 'temp_node->retire();', 'ObKVCacheHazardSlot::retire', 'high', 'frees every node regardless of min_version'),
    (16, 'src/share/cache/ob_kvcache_hazard_version.h', 54, 'whose version is less than version', 'ObKVCacheHazardSlot::retire', 'high', 'documented contract: retire only older versions'),
    (16, 'src/share/cache/ob_kvcache_hazard_version.cpp', 255, 'slot.retire(min_version);', 'ObKVCacheHazardStation::release', 'high', 'caller passes min_version that retire ignores (also :273)'),
]


def main():
    rows = []
    errors = []
    for bug, f, line, anchor, fallback, conf, note in SITES:
        lines = L.raw_lines(f)
        if not 0 < line <= len(lines) or anchor not in lines[line - 1]:
            errors.append('%s:%d anchor %r not found' % (f, line, anchor))
            continue
        name = None
        if f.endswith(L.SOURCE_EXT):
            off = L.line_start_offset(f, line) + lines[line - 1].find(anchor)
            name = L.enclosing_function(f, off)
            if name is None and f.endswith('.h'):
                name = L.enclosing_class(f, off)
                name = (name + '::' + fallback.split('::')[-1]) if name else None
        name = name or fallback
        cons = 'suspected bug site (00b §6 D #%d)' % bug if bug <= 15 else 'suspected bug site (KV cache retire condition, RULEBOOK 0.8)'
        rows.append((f, '%s#bug%02d' % (name, bug), cons, line, 'conf:%s;%s' % (conf, note)))
    if errors:
        sys.exit('c17: ' + '; '.join(errors))
    keys = {}
    dedup = []
    for r in sorted(rows, key=lambda r: (r[0], r[3], r[1])):
        k = (r[0], r[1], r[2])
        keys[k] = keys.get(k, 0) + 1
        if keys[k] > 1:
            r = (r[0], '%s#%d' % (r[1], keys[k]), r[2], r[3], r[4])
        dedup.append(r)
    n = L.finish(CAT, dedup, '00b §6 D bugs 1-15 + KV cache retire (bug16); anchors verified against the source on '
                             'every run; bugs 13-15 low/medium confidence')
    return n


if __name__ == '__main__':
    main()
