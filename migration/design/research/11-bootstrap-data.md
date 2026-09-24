# Research 11: bootstrap and data

The question: what the first start with an empty base dir does (inner tables, the server runtime that replaced the sys tenant, the root user, system variables, and the generated code behind them); what the reduced init (migration/judge/reduced-init/) needs; what Decision 10's narrow path needs from bootstrap; PLAN.md section 3, "What is dropped or regenerated rather than translated"; how the generators change to emit Rust; and what the core must implement for bootstrap.

Every file:line is at the frozen base 834bbee1e (`git diff --stat 834bbee1e HEAD -- src rust CMakeLists.txt cmake tools/deploy` is empty in this worktree). Generated files were read in the reference build, /Users/colin/seekdb-dev/ref-834bbee1e/build_release/generated/. Section 12 lists the commands behind the counts. "Assumption" marks a figure with no evidence behind it.

## Summary

- The code that exists for bootstrap is small (about 11.9K lines in the files section 12 lists, counting the SQLite meta store and the data-version gate), but it runs on top of nearly everything: the DDL service, the schema service, the whole SQL engine (every catalog row is written by an inner-SQL `INSERT`), storage, transactions, and, after start, the PL compiler for 13 system packages.
- The inner-table code generated at build time is 104,297 lines of C++ for 640 schema objects with 4,282 columns. It comes from generate_inner_table_schema.py (3,417 lines) reading ob_inner_table_schema_def.py (14,510 lines). ob_inner_table_init_data.py (51 lines) only adds a 16-row privilege array.
- The reduced init needs only root with an empty password, the `oceanbase` and `test` databases, and CREATE USER, CREATE DATABASE IF NOT EXISTS and GRANT.
- The narrow path needs from bootstrap: the 776 system variables at their bootstrap values, root, the two databases, those three statements and CREATE TABLE. It needs no system packages, virtual tables, system views or inner-SQL schema loading, as long as the statement list avoids SHOW/DESC, views, information_schema, PL and `dbms_*`. 100 of the 128 plain-SQL cases avoid all of them.
- Finding for the plan: the core build's exit ("the 130 plain-SQL cases pass under the reduced init") cannot be met by the core alone. 28 of the 128 cases need virtual tables, views, user PL, system packages or information_schema, which are Step 3 leaves, and every case needs the DDL service.
- Recommendation:
  - The generators keep their inputs unchanged and gain Rust back ends. Their output is checked in and guarded by a regenerate-and-diff check. The inner-table output becomes static data plus one hand-written builder.
  - The schema service reads and writes the inner tables through typed row access instead of inner SQL. System table schemas come from the generated data on every start. meta.db stays SQLite (through rusqlite) and opens after the data-version gate.
  - Bootstrap is built in three stages that match Step 2a, the core build and Step 5.

## 1. How a start chooses between bootstrap and restart

1. main.cpp:705 changes the working directory to the base dir; later paths are relative to it (the Decision 8 guideline asks the engine not to do this).
2. `ObServer::init` calls `init_config` first (ob_server.cpp:634). `init_config` builds the meta.db path from `getcwd()`, opens `./store/sstable/meta.db` (SQLite) and loads the configuration from it (ob_server.cpp:1698-1715), and only then runs the data-version gate (:1716-1719).
   - A missing etc/seekdb.data_version.bin counts as a fresh install and is written at once (ob_data_version_mgr.cpp:58-60, `dump_current_version_to_file_` at :170; the path is set at ob_data_version_mgr.h:91).
   - Any version other than `DATA_CURRENT_VERSION` (1.4.0.0, ob_version_def.h:53) gives `OB_NOT_SUPPORTED` (ob_data_version_mgr.cpp:61-65).
3. `check_need_initialize` (ob_server.cpp:410-433, called at :665-671) makes the choice:
   - no block file and an empty clog directory: bootstrap;
   - both present: restart;
   - anything else: `OB_ERR_UNEXPECTED` ("The status of deployment environment is not consistent").
4. `--variable key=value` options apply only on a bootstrap (ob_server.cpp:706 → `ObPreProcessSysVars::init_sys_var`, ob_system_variable.cpp:2617-2626). sdb.py never passes them; it passes only `--parameter` (sdb.py:121-132).

## 2. What the first start does today

### 2.1 The bootstrap sequence

| # | Step | Where |
|---|---|---|
| 1 | Create a bootstrap server runtime (the resource container that replaced the tenant unit) | ob_server.cpp:1338-1349 |
| 2 | The standby module calls `bootstrap_primary`, which calls `ObService::bootstrap` | standby_module.cpp:340-367; ob_server.cpp:162-165; ob_service.cpp:670-702 |
| 3 | `ObPreBootstrap`: check that the server is empty, set `GCTX.in_bootstrap_`, create the one log stream (palf plus the tx ctx, tx data and lock tablets) | ob_bootstrap.cpp:68-123; ob_ls_service.cpp:591-630; ob_ls.cpp:47-49, :120 |
| 4 | `ObLocalManagementService::execute_bootstrap` runs `ObBootstrap::execute_bootstrap` | ob_local_management_service.cpp:399-485; ob_bootstrap.cpp:171-239 |
| 4a | Refuse to run if the schema version is not `OB_CORE_SCHEMA_VERSION` | ob_bootstrap.cpp:640-661 |
| 4b | Create the tablets of the 6 core-related tables, with their indexes and LOB tables, in one transaction | :397-436 |
| 4c | Write the `__all_global_stat` values, which live inside `__all_core_table` | :663-700; ob_global_stat_proxy.cpp:262 |
| 4d | Build all 640 schemas from the generated creator functions, then assign "hard-coded" schema versions (a pure function of the list order) | :549-559; ob_schema_utils.cpp:390-436, :452-503 |
| 4e | Publish them in memory, so inner SQL can resolve system tables before any row exists | :561-572; ob_multi_version_schema_service.cpp:1148-1182 |
| 4f | A background thread creates the remaining system tablets | ob_partition_creator.cpp:77-160 |
| 4g | Write the catalog rows with inner-SQL `INSERT ... VALUES`: 100 rows per statement, 1,000 per transaction, one batch after another | ob_bootstrap.cpp:241-270; ob_load_inner_table_schema_executor.cpp:62-106, :215-233; .h:56-57 |
| 4h | Create the server runtime `"sys"` (ob_define.h:810) and everything in it (table in 2.2) | ob_bootstrap.cpp:702-742; ob_runtime_ddl_service.cpp:182-226 |
| 4i | Refresh the schema from the inner tables | ob_bootstrap.cpp:221 |
| 5 | Start local services; load the `MYSQL_SPECIAL` system packages (the list is empty, syspack_codegen.py:143-158); write a no-op DDL operation, `OB_DDL_FINISH_BOOTSTRAP`; store the baseline schema version; run `ALTER SYSTEM SET` for 4 parameters | ob_local_management_service.cpp:424-436, :559-588, :2778-2806 |
| 6 | Wait for the system packages only if `_enable_async_load_sys_package` is false; its default is True | ob_local_management_service.cpp:437-449; ob_parameter_seed.ipp:1133 |
| 7 | Commit point: write the local, then the server, checkpoint | ob_server.cpp:1195-1221 |
| 8 | Report telemetry over HTTPS to openwebapi.oceanbase.com; the call runs on every successful start, but the report is sent once per base dir, and the environment variable `TELEMETRY_ENABLED=false` turns it off | standby_module.cpp:359-363; ob_telemetry.cpp:57, :1452-1500 |

Dead code inside bootstrap, which the port skips:
- `init_debug_database` (ob_local_management_service.cpp:1753-1809) never runs: `debug_` starts false (:92) and `set_debug()` (ob_local_management_service.h:127) has no caller.
- `ObBootstrap::batch_create_schema` (ob_bootstrap.cpp:574), `construct_schema` (:627) and `TableIdCompare` (:125) have no callers either.

A kill before step 7 leaves a directory that the next start refuses: it finds block file and clog present, but the checkpoint has no runtime entry (ob_server.cpp:1140-1145, :1338-1349). Under Decision 9 every stop is a kill, so the Rust bootstrap needs the same single commit point.

### 2.2 What bootstrap writes

| What | Count | Lands in | Written by |
|---|---|---|---|
| Catalog rows for the 640 schemas | 6 core, 96 system, 143 virtual, 139 system views, 101+101 LOB meta/piece, 54 system indexes; 4,282 columns | `__all_table`, `__all_column`, both history tables, `__all_ddl_operation`; core tables as key-value rows in `__all_core_table` | inner SQL (ob_dump_inner_table_schema.cpp:165-360) |
| Tablets | 358 (every schema outside the virtual and view ranges) | storage | `ObTableCreator` |
| System variables | 776 generated defaults, then 8 overrides: read_only, parallel_servers_target (from the CPU count), 4 charset and collation variables, lower_case_table_names, ob_tcp_invited_nodes = '%' | `__all_sys_variable` and its history table | ob_runtime_ddl_service.cpp:254-372 |
| Values from the environment, set in memory first | version_comment, system_time_zone, 8 charset/collation variables, server_uuid, pid_file (`<cwd>/run/observer.pid`, although the real pid file is run/seekdb.pid, main.cpp:670), port, socket (`<cwd>/run/sql.sock`), datadir | the same | ob_system_variable.cpp:2489-2615 |
| Databases | 6: oceanbase, `__recyclebin` and `__public` (read-only), mysql, information_schema, test; each with a database privilege for root | `__all_database`, privilege tables | ob_ddl_operator.cpp:3423-3530 |
| Root user | root@'%', empty password, all privileges | `__all_user` | ob_ddl_operator.cpp:3596-3656; ob_define.h:804, :811 |
| Statistics | global preference rows; 3 dbms_scheduler jobs for the maintenance window | optimizer stats tables, scheduler tables | ob_ddl_operator.cpp:3532-3560; ob_dbms_stats_maintenance_window.cpp:42-100 |
| Freeze info, SRS 0, max-id counters | 1 freeze row carrying `DATA_CURRENT_VERSION`; 1 SRS row; 5 counters (object ids start after `OB_INITIAL_TEST_DATABASE_ID`) | `__all_freeze_info`, `__all_spatial_reference_systems`, `__all_sys_stat` | ob_ddl_operator.cpp:3659-3700, :64-89; ob_runtime_ddl_service.cpp:118-180 |
| Global merge info; 4 parameters; the system-package job | 1 + 4 + 1 rows | meta.db (SQLite): `__all_merge_info`, `__all_sys_parameter`, `__all_rootservice_job` | ob_runtime_ddl_service.cpp:228-237; ob_local_management_service.cpp:2778-2806; ob_admin_job_table_operator.cpp:103-107 |
| System packages | 13 packages from 26 .sql files (1,544 lines) | package tables | `CREATE PACKAGE` through the PL compiler, in a timer task (ob_system_package_load_task.cpp:83-132; ob_pl_package_manager.cpp:452-476) |

On a restart nothing above is rewritten. Storage recovers, then the schema service reloads everything through inner SQL: `ObSchemaServiceSQLImpl` (6,464 lines, 147 methods, 56 `ORDER BY` clauses; the SQL templates are at ob_schema_service_sql_impl.cpp:28-72). Only `__all_core_table`'s own schema is compiled in (ob_schema_cache.cpp:355-361; ob_schema_service_sql_impl.cpp:249-254).

## 3. The generators and their data

### 3.1 Inventory

| Generator | Input | Output today | When it runs |
|---|---|---|---|
| src/share/inner_table/generate_inner_table_schema.py (3,417) | ob_inner_table_schema_def.py (14,510), ob_inner_table_init_data.py (51) | 51 files, 104,297 lines of C++ (99,497 .cpp, 4,800 .h/.ipp), plus table_id_to_name (591), all in build_release/generated | at configure time when the SHA-256 of the three inputs changes (CMakeLists.txt:33-71); Bazel genrule (src/share/BUILD.bazel:1451) |
| src/share/system_variable/gen_ob_sys_variables.py (1,200) | ob_system_variable_init.json (10,979 lines, 776 variables) | 8 files, 29,946 lines, checked in (gen_ob_sys_variables.py:1170-1200) | by hand; no build rule |
| src/share/gen_errno.pl (467) | ob_errno.def (1,914 lines: 858 `DEFINE_ERROR`, 326 `_EXT`, 172 `_DEP`, 190 `_EXT_DEP`, 2 `DEFINE_OTHER_MSG_FMT`) | ob_errno.cpp, ob_errno.h, src/oblib/lib/ob_errno.h: 18,824 lines, checked in (gen_errno.pl:11, :110, :199, :244) | by hand |
| src/share/inner_table/sys_package/syspack_codegen.py (176) | 26 .sql files, 13 packages | syspack_source.cpp, 1,690 lines | at build time (src/pl/CMakeLists.txt:27-45; sys_package/BUILD.bazel:46) |
| gen_str_datum_func_parts.py (165) | 3 collations | 150 lines, checked in | by hand |
| gen_expr_str_cmp_func.py (325) | the collation list | 9 files, 425 lines, checked in | by hand |
| tools/ob_error/src/gen_os_errno.pl (184); **not in PLAN's list of six** | os_errno.def (138) | os_errno.cpp/.h, 640 lines | by hand; the ob_error tool that family 6 runs needs it |
| generate_{scalar,simd}_bitpakcing.py (384 + 782) | none | 24,406 lines under lib/codec | dropped with lib/codec |

### 3.2 The inner-table generator

The definition file has 384 top-level `def_table_schema` calls; 28 of them build history tables from another table's definition and 7 build SQLite-backed virtual tables. `table_type` is set 347 times (74 `SYSTEM_TABLE`, 134 `VIRTUAL_TABLE`, 139 `SYSTEM_VIEW`). There are also 54 `def_sys_index_table` calls and 7 `gen_sqlite_table_def` calls (the meta.db tables). The generator then adds the LOB tables. The output:
- 640 `ObInnerTableSchema::*_schema` builder functions, all of the same shape (setters, then one `ADD_COLUMN_SCHEMA*` macro per column);
- the TID and TNAME constants;
- the creator arrays, in a fixed order (core, core-related, system, virtual, views, and the two index groups);
- the LOB mapping;
- the counts `OB_CORE_TABLE_COUNT` = 6 through `OB_BOOTSTRAP_SCHEMA_VERSION` = 388 (generated ob_inner_table_schema.h:2216-2222);
- X-macro tables in ob_inner_table_schema_misc.ipp;
- the SQLite `CREATE TABLE` text for meta.db;
- two C++ classes with `inner_open` logic for the SQLite virtual tables (ob_all_virtual_sqlite_tables.cpp, 1,547 lines).

The generator writes C++ logic as well as data (generate_inner_table_schema.py:1420-2051). The definition file is not language-neutral either. It is Python run with `exec` (:3389-3394), with helper functions (`gen_history_table_def`, `gen_sqlite_virtual_table_def`), and its values spell 333 distinct C++ constant names, 155 symbolic column lengths (`varchar:OB_MAX_COLUMN_NAME_LENGTH`) and three C++ expressions (`ObCharset::get_default_charset()` and others, ob_inner_table_schema_def.py:287-288, :426). Several outputs are empty at 834bbee1e: the cluster-private switch (misc.ipp:18-21), `cluster_distributed_vtables` and `restrict_access_virtual_tables`. 41 source files include the generated headers; 85 files use the `OB_ALL_*_TID` and `OB_ALL_*_TNAME` constants, 897 times.

### 3.3 The system-variable generator

Each JSON entry has id, name, default and base values, type, flags and optional min, max and enum names. 8 variables have a base value different from the default. Per-variable behavior is named, not described:
- 35 `on_check_and_convert_func`;
- 16 `to_select_obj_func` and 16 `to_show_str_func`;
- 15 `get_meta_type_func`;
- 6 `on_update_func` and 6 `session_special_update_func`;
- 23 `base_class` overrides.

The C++ output turns these into 720 classes (`grep -c '^class ObSysVar'` in ob_system_variable_factory.h). The fields publish_version, info_cn, background_cn, ref_url and placeholder are ignored (gen_ob_sys_variables.py:42-43). At start-up the C++ writes into the generated static arrays (`ObSysVariables::set_value`/`set_base_value`, ob_system_variable.cpp:2475-2615). Sessions load every variable from the runtime's system-variable schema when they connect (obmp_connect.cpp:345).

### 3.4 The errno generator

The output has one static record per code (name, cause, solution, MySQL errno, SQLSTATE, `str_error`, `str_user_error`) and five lookup functions. Their fallbacks differ in small ways that are part of the contract: `ob_strerror` returns "Unknown error" for an out-of-range code but "Unknown Error" for an empty entry (ob_errno.cpp:15532-15539). `str_user_error` is a printf format; ob_errno.def uses 17 specifier forms, of which `%.*s` (366 uses), `%s` (206) and `%ld` (44) are the most common. Error codes also cross into the kept C++:
- share/geo (180 files, 4 of them include ob_errno.h directly) uses about 50 distinct error-code names; the S2 and vsag adapters use OB codes too (6 lines in ob_s2adapter.cpp, 28 in ob_vsag_adaptor.cpp);
- the parser's C core keeps its own copies of the values (parse_define.h:26-35).

### 3.5 PLAN section 3's list, checked at 834bbee1e

| Item | PLAN's claim | Check |
|---|---|---|
| lib/codec | 32,109 lines, no callers | 21 files, 32,109 lines; only the Bazel inventories name it |
| Timezone tables | 38,963 lines, never read | ob_timezone_info.cpp is 39,989 lines; `TIME_ZONE_NAMES` (:26) and `TIME_ZONE_TRANS` (:604) are used by no other file. No configured test sets a named time zone. The `__all_time_zone*` tables exist but bootstrap leaves them empty |
| tzcode, ob_ctype_uca.cc | dead | tzcode is localtime.c, private.h and tzfile.h. Neither they nor ob_ctype_uca.cc (7,129 lines) appear in any build file or source inventory (.bzl), so they are not even compiled; ob_timezone_info.cpp is compiled (oblib_source_inventory.bzl) |
| IK dictionary | 276,043 lines, becomes a data file | three word lists as `const char*` arrays (main, quantifier, stop; ob_ik_dic.h). They are built lazily into a cache (ob_ft_dict_hub.cpp:76), not at bootstrap; the three `__ft_*_ik_utf8` inner tables stay empty |
| Six generators | "about 87K checked-in lines plus about 104K" | the six generators' checked-in output is 49,345 lines (errno 18,824, system variables 29,946, 150, 425). 87K is the tree's total checked-in generated code: it also counts bit-packing (24,406, dropped with lib/codec) and the gRPC/protobuf stubs (12,345, replaced by prost/tonic). The build-time part is 104,297 inner-table lines plus syspack_source.cpp (1,690) |

## 4. What the judge sees of bootstrap and data

- The runner waits for `select 1` as root with no password (sdb.py:164-177; mysqltest_for_seekdb.py:486-496). It pipes init.sql to obclient as root with `-D oceanbase`, and init_user.sql with `-D test` (:400-423).
- Cases run as admin/admin in database `test` (mysqltest_for_seekdb.py:28-30, :724-731).
- Pinned text: 1,546 error-catalog entries and 2,688 `--error` directives; 289 parameter names (PLAN section 3); system variables through SHOW VARIABLES, which reads `__all_virtual_session_variable` and `__all_virtual_global_variable` (ob_show_resolver.cpp:357, :360).
- Inner tables are read directly by 60 test and include files (evidence-full.md:1756). information_schema views read oceanbase.__all_table (for example `TABLES`, ob_inner_table_schema_def.py:6810-6900). So catalog rows must be real, SQL-visible rows with the same values.
- Not pinned: version_comment is masked by the test that prints it (special_hook.test:12, `--replace_column`). No configured .result contains the version strings or `data_version`; only 4 of the 22 inner_table .result files match, and no .test runs those 22. So bumping `DATA_CURRENT_VERSION` (Decision 11) changes no compared line, even though it reaches bootstrap rows (ob_bootstrap.cpp:336, :382; ob_ddl_operator.cpp:3665).
- Timing: system packages load after start in a timer task, so a case that calls `dbms_stats` very early races the load in the C++ build too (PLAN section 8, item 21).

## 5. What the reduced init needs

The profile (migration/judge/reduced-init/) has three statements in init.sql and one in init_user.sql. To run them the server needs:
- login as root with an empty password, and as admin with the stage-2 password hash (`ObEncryptedHelper::encrypt_passwd_to_stage2`, ob_ddl_operator.cpp:3616);
- the `oceanbase` and `test` databases, for `-D` and the mysqltest `--database`. Bootstrap creates `test` (ob_ddl_operator.cpp:3523-3524), so `create database if not exists test` changes nothing;
- CREATE USER and GRANT through the DDL path, which writes `__all_user` and its history and refreshes the schema;
- the system variables every session loads.

It needs no tracepoints, no PL, no `ANALYZE TABLE` of the core virtual tables, and no parameter changes; those are what it drops (reduced-init/README.md). PLAN section 4, item 2 fixes the profile at 00b's gate: it changes only by a recorded amendment followed by recording the C++ reference again.

## 6. What the Decision 10 narrow path needs from bootstrap

The narrow run (PLAN section 6, Step 2a) runs single-table SELECT, INSERT and CREATE TABLE on memtable-only storage, diffed against the C++ reference under the reduced init. From bootstrap it needs:

1. The 776 system variables at their bootstrap values: the generated defaults, the 8 overrides of section 2.2, and the values taken from the environment.
2. The server runtime, root, and the `oceanbase` and `test` databases (the other four cost nothing to add).
3. The max-id counters with the C++ starting values, so objects get the same ids.
4. Schema writes for CREATE USER, GRANT, CREATE DATABASE IF NOT EXISTS and CREATE TABLE. The reduced init must run, so the first three are needed even though the statement list has none of them.
5. A schema snapshot the resolver can read through the schema guard.

It does not need the 640 catalog rows, the 358 system tablets, `__all_core_table`, meta.db, virtual tables, system views, system packages or any inner SQL, as long as the schema writes of item 4 go through an interface the narrow run can back with memory (section 8.1). Decision 10's switch condition names exactly "system-package PL, inner-SQL schema loading, virtual tables". Under the recommendation below, bootstrap is not a reason for the switch.

The statement list must be chosen accordingly. `SHOW TABLES` and `SHOW CREATE TABLE` are rewritten into queries on `__all_virtual_show_tables` and `__all_virtual_show_create_table` (ob_show_resolver.cpp:229-259, :493-496). Over the 128 plain-SQL cases (python3 migration/design/evidence/plain_sql_features.py, which follows `--source` includes):

| Feature in the case | Cases | Needs |
|---|---|---|
| SHOW or DESC | 20 | virtual tables |
| CREATE VIEW | 6 | views |
| `dbms_stats.*` (3), `dbms_ai_service.*` (2) | 5 | system packages, PL |
| CREATE PROCEDURE/FUNCTION/TRIGGER | 4 | PL |
| information_schema | 3 | system views, virtual tables |
| none of the five | **100** | the core alone |

The same table shows a gap in the plan. The core build's exit requires "the 130 plain-SQL cases pass under the reduced init" (PLAN section 6). 28 of the 128 need leaves that Step 3 translates, and all 128 need the DDL service (ObDDLService, 23,564 lines; research 01 section 3 traces CREATE TABLE through it). Five of the cases call system packages, which the C++ reference loads after bootstrap whatever the init profile says.

## 7. What the decisions and the plan require here

- **Decision 1 (b), row 1a:** start-up and restart time are recorded, not gated, so bootstrap may be slower (for example, creating tablets one after another).
- **Decisions 2 and 3:** the fork is frozen, so the generator inputs never change during the port, and nobody has to keep the C++ back ends up to date.
- **Decision 6:** exact comparison, and no mask covers catalog rows, system-variable values or error text.
- **Decision 7:** macOS arm64 first, and nothing may rule out wasm, Android or Windows. So generated code is plain Rust, a cargo build must not need Python, and meta.db sits behind a trait.
- **Decision 8 guideline:** no `chdir`. The values taken from `getcwd()` (pid_file and socket, ob_system_variable.cpp:2496-2530; the meta.db path, ob_server.cpp:1698-1709) come from the absolute base dir in the server context instead.
- **Decision 9:** every stop is a kill, so bootstrap keeps one commit point (section 2.1, step 7).
- **Decision 10:** the switch condition (section 6).
- **Decision 11:** bump `DATA_CURRENT_VERSION`; check the version before meta.db opens; close the missing-file hole (one way: accept a missing version file only when `check_need_initialize`, ob_server.cpp:410-433, would choose bootstrap); print a clear message.
- **Decision 13:** the islands stay C++, so the errno generator must still write a C header for them.
- **Decision 14:** `forbid(unsafe_code)`, so the mutable defaults do not become `static mut`. rusqlite also needs a named rule in the design document's dependency table (RULEBOOK section 1).
- **Decision 16:** stable Rust only, so generated code uses only `const`/`static` data and ordinary functions.
- **PLAN section 8, items 17-23:**
  - Items 17 and 20 do not touch bootstrap.
  - Item 18 (scan order): the typed catalog scans of section 8.1 return rows in rowkey order; where a C++ fetch query orders otherwise, the Rust code sorts the same way.
  - Item 19: the island errno header is part of the kept C++ subset.
  - Item 21: the asynchronous package load is timing-sensitive.
  - Item 22: every run executes bootstrap, but only indirectly checks its output; section 9.4 adds direct checks.
  - Item 23: catalog rows and variable values are exact, so a wrong default produces a false failure far from its cause, which is another reason for the checks in 9.4.

## 8. Options

### 8.1 How the schema service reads and writes the inner tables

Size of what depends on inner SQL:
- 1,709 references to `ObMySQLProxy`, `ObISQLClient` or `ObMySQLTransaction` in src/share/schema, src/observer/schema and src/rootserver;
- `ObDMLSqlSplicer` used 470 times in 65 files, with 105 `exec_*` calls through `ObDMLExecHelper`;
- 62 raw writes in share/schema and 76 raw reads in share/schema plus observer/schema;
- the 20 `*_sql_service` writers (10,243 lines), `ObSchemaServiceSQLImpl` (6,464), `ObCoreTableProxy` (1,323) and `ObGlobalStatProxy` (738).

| Option | What changes | Cost | Risk |
|---|---|---|---|
| A. Inner SQL as today | The `ObISQLClient` counterpart is a trait in a low crate (research 01 puts it in ob-share); the SQL crates implement it; the server context connects the two | Least rewriting: the code above translates file by file | Bootstrap and every restart need the parser, resolver, optimizer, DAS and storage working before any schema exists. The narrow run would need the refresh queries (`ORDER BY`, `LIMIT`, `IN` lists) or a separate shortcut, so its schema path would not be the real one. This is the "inner-SQL schema loading" of Decision 10 |
| B. Typed row access for the schema service; inner SQL for the rest | Same inner tables, same rows, still visible to SQL. `ObDMLSqlSplicer` keeps its API (`add_column`, `add_pk_column`), but its execution becomes a direct row write that takes the table's generated definition, casts the values with the core's cast library and joins the caller's transaction. The schema load becomes typed key-range scans. Other leaf code (DDL tasks, statistics, scheduler, PL) keeps inner SQL through the trait of option A | Rewrite rather than translate `ObSchemaServiceSQLImpl`, `ObCoreTableProxy`, `ObGlobalStatProxy`, the 62 + 76 raw statements and `ObDMLExecHelper`: about 9-10K C++ lines (estimate: the three classes are 8,525 lines), inside ob-schema, which research 01 already puts in the core | Wherever the C++ relied on SQL: value casting from literals, `gmt_create`/`gmt_modified` defaults, `ON DUPLICATE KEY UPDATE`/`REPLACE` in `exec_insert_update`/`exec_replace`, history-table semantics, and the order of the 56 `ORDER BY` fetches. The golden catalog check of 9.4 catches all of them |
| C. An in-memory catalog persisted in its own file, with `__all_*` served as virtual tables | Catalog outside storage | Simplest bootstrap | Changes what SQL sees (transactions over catalog and data, history tables, snapshot reads of `__all_table`), and every test reading `__all_*` goes through new code. Not a faithful port |

Crate direction under B: ob-schema (research 01, crate 6) sits below the storage crates. It therefore defines the row-access trait, and a crate above storage implements it. This is the pattern research 01 section 5.2 already uses.

### 8.2 Where system-table schemas come from on a restart

- **As today:** read them back from `__all_core_table` and `__all_table`.
- **From the generated data on every start**, reading only user objects and `__all_global_stat` from storage. Decision 11 guarantees the data dir was written by this binary, so both give the same schemas; the second needs no chicken-and-egg order.
- Assumption to check in the design work: no path rewrites system-table rows after bootstrap. The upgrade machinery was deleted in 3609383cd (evidence-full.md, the data-version section), and `force_create_sys_table` returns `OB_NOT_SUPPORTED` (ob_local_management_service.cpp:2626-2629).

### 8.3 meta.db

- **Keep SQLite through rusqlite** with its bundled C library. The 7 generated `CREATE TABLE` statements carry over. It is a C dependency, and wasm support is an open assumption.
- **Move the 7 tables into ordinary inner tables and the parameters into a plain file.** The parameters are needed before storage starts (memory_limit, data_dir, log_disk_size), which is why the C++ uses SQLite.
- **Either way:** the data-version gate runs first (Decision 11).

### 8.4 How the generators produce Rust

| Option | Cost | Risk |
|---|---|---|
| G1. Add a Rust back end to each script, keep the inputs byte-identical, check the output in, and add a CI step that regenerates and runs `git diff --exit-code` | One emitter per script; the inner-table one is the largest (assumption: 800-1,500 lines of Python) | Python and Perl stay as developer tools, but cargo never runs them |
| G2. Rewrite the generators in Rust as `build.rs` | The Python definition file must first become data (for example by dumping JSON once), so the source of truth moves; about 5,100 lines of generator logic to rewrite | Translation errors in id, rowkey and schema-version logic |
| G3. Generate once and hand-maintain the Rust output from then on | Cheapest now | The declarative input is lost for the product that comes after parity (Decision 1) |

### 8.5 System packages at bootstrap

Keep the C++ behavior: `_enable_async_load_sys_package` defaults to True, and the job row goes into `__all_rootservice_job`. Loading needs the PL parser, resolver and package DDL, which are Step 3 leaves, so packages arrive in Step 5. The alternative, a synchronous load before the listener opens, would remove the race in section 4 but changes when clients can see packages, which a family 11 startup race could observe.

## 9. Recommendation

### 9.1 The generators (G1)

| Today | Rust output | Notes |
|---|---|---|
| generate_inner_table_schema.py + definition + init data | Constants (640 TIDs and TNAMEs, the index names, the 5 column-position enums); one static definition per schema (640, with 4,282 columns, index and storing columns, LOB mapping, view text); the creator groups in today's order; the counts; `ALL_PRIVILEGES` (16 rows); the 7 SQLite `CREATE TABLE` texts; column maps for the 7 SQLite virtual tables; table_id_to_name | One hand-written builder replaces the 640 generated functions; schema versions are computed at start with the same algorithm and order (ob_schema_utils.cpp:452-503). The emitter writes the 333 C++ constant names as they are, and the Rust constants keep the C++ names; a short table maps the 3 C++ expressions. Empty lists are dropped |
| gen_ob_sys_variables.py + JSON | `SysVarClassType`, the `OB_SV_*` names, a static table of 776 definitions, the by-name lookup order used today, `ESSENTIAL_SYS_VARS`, and hook slots that name hand-written functions (35 + 16 + 16 + 15 + 6 + 6, plus 23 value kinds) | No per-variable types. The mutable defaults live in the server context, filled at start from the table, the environment and `--variable` |
| gen_errno.pl + ob_errno.def | Code constants with the C++ names; a static table indexed by -code; the five lookups with the same fallbacks; the 2 other-message formats; a C header of the codes, for the islands | The printf subset (17 forms) is hand-written once. The output belongs in the lowest crate (ob-base in research 01), since every crate returns these codes |
| gen_os_errno.pl + os_errno.def | A static table for the ob_error tool | Family 6 needs the tool built from the regenerated catalogs (PLAN section 4) |
| syspack_codegen.py | none | A hand-written 13-entry table with `include_str!` of the unchanged .sql files replaces the script and the Bazel copy of its list (sys_package/BUILD.bazel:2) |
| gen_str_datum_func_parts.py, gen_expr_str_cmp_func.py | none | Their only job is splitting C++ template instantiation across object files; generics or a `match` over the same collation lists replace them |
| IK dictionary | three text files in today's order, read with `include_str!` | Extracted once by a script; the lazy cache stays |
| Timezone tables, tzcode, ob_ctype_uca.cc, lib/codec and its two generators | none | Dead (section 3.5) |

Conventions from the sql-nio port (prior art), adopted or rejected:
- **Adopt:** generated files are checked in with a DO-NOT-EDIT banner, and CI regenerates them and fails on a diff (ffi-mechanics.md, "header 用 cbindgen 生成"). Agents and the declaration index can then read generated code like any other file.
- **Reject:** cbindgen for the errno header. Its source of truth is ob_errno.def, not a Rust crate, and exporting 1,546 constants through cbindgen would tie the error crate's root to the island ABI.
- **Reject:** island calls that return 0/-1 with a C++ layer mapping them to OB codes (abi-naming.md). The islands already produce OB codes, and a shared generated header gives both sides the same numbers.
- **Reject:** the `<tag>_` prefix on these constants. The kept C++ already uses the `OB_*` names, and renaming would edit C++ that Decision 13 keeps as it is.

### 9.2 Catalog access and data files

- Option B of 8.1: typed row access for the schema service and bootstrap, inner SQL for the rest through a trait.
- Option 2 of 8.2: system schemas from the generated data on every start. The rows are still written exactly as the C++ writes them, so SQL sees the same catalog.
- SQLite through rusqlite for meta.db in the first release, behind a trait, opened after the data-version gate.
- The top-level layout stays: etc/, log/, run/seekdb.pid (read by sdb.py:322, :389), run/sql.sock, store/. Inside store/, Decision 11 leaves the Rust build free.

### 9.3 What the core implements for bootstrap, by stage

| Piece | Step 2a narrow run (disposable, memory only) | Core build exit | Step 5 (full init.sql, 272 cases) |
|---|---|---|---|
| Absolute base dir in the server context; etc/log/run/store created | yes | yes | yes |
| Data-version gate with the Decision 11 fixes; then meta.db | gate optional | yes | yes |
| One log stream (palf plus 3 LS tablets), 358 system tablets, checkpoints as the single commit point | no | yes | yes |
| 640 system schemas from the generated data, with hard-coded versions | yes (cheap) | yes | yes |
| Catalog rows written as the C++ writes them (core key-value rows, table/column rows and history, DDL operations, global stat) | no (memory) | yes | yes |
| Server runtime "sys", 776 variables with overrides and environment values, 6 databases, root, max-id counters | yes | yes | yes |
| Schema writes for CREATE USER, GRANT, CREATE DATABASE, CREATE TABLE (backed by memory in Step 2a) | yes | yes | yes |
| Freeze info, SRS 0, global merge info, 4 parameters | no | yes | yes |
| Statistics preferences and scheduler jobs | no | only if dbms_stats cases are in the exit list | yes |
| Virtual tables and system views served | no | only for the 28 cases | yes |
| 13 system packages, loaded asynchronously | no | only for the 5 cases | yes |
| Telemetry | no | no | the developer's call (Q3) |

### 9.4 Checks that show the Rust bootstrap matches

1. **Golden catalog dump.** Once, from the archived C++ reference after bootstrap on an empty dir, dump over SQL `__all_table`, `__all_column`, `__all_ddl_operation`, `__all_core_table`, `__all_sys_variable`, `__all_database` and `__all_user`, leaving out `gmt_*` and the time-based versions of user objects. Keep the dump under migration/judge/, and diff the Rust build's dump against it at the core build's exit. This is an entry-gate check (family 2), not a mask.
2. **Variables.** `SELECT @@global.x, @@session.x` for all 776 names on both builds.
3. **Error catalog.** `ob_error <code>` for every one of the 1,546 codes from the C++ tool, diffed against the Rust tool (family 6 runs 18 today).
4. **Unit test.** The static definitions against a normalized dump of the 640 C++ `ObTableSchema` objects, so Step 2a is covered before any SQL works.

## 10. Corrections and additions for PLAN.md

1. **Section 3, "Six generators ... about 87K checked-in lines":** the six generators' checked-in output is 49,345 lines. 87K is the tree's whole checked-in generated code, including bit-packing (dropped) and the gRPC stubs (prost/tonic). Add gen_os_errno.pl as a seventh generator.
2. **The task's framing:** the 104K lines come from ob_inner_table_schema_def.py; ob_inner_table_init_data.py writes only `all_privileges` (16 rows, used by two virtual tables, ob_virtual_privilege.cpp:58 and ob_all_virtual_privilege.cpp:58).
3. **Section 6, the core build's exit:** 28 of the 128 plain-SQL cases need virtual tables, views, PL, system packages or information_schema, and all of them need the DDL service (Q1).
4. **Section 4, item 2 ("no system-package PL"):** true of the init files. The C++ reference still loads the 13 packages after every bootstrap, and 5 plain-SQL cases call them.

## 11. Questions only the developer can answer

- **Q1.** For the core build's exit, is the list the 100 plain-SQL cases that need none of virtual tables, views, PL, system packages or information_schema, or do those leaves and a DDL slice move into the core build?
- **Q2.** Do you accept option B of 8.1? It turns about 9-10K lines of schema-service C++ from translation into rewriting, in exchange for a bootstrap and restart that do not need the SQL engine.
- **Q3.** Should the fork's Rust build keep the bootstrap telemetry to openwebapi.oceanbase.com (on by default today), drop it, or keep it off by default?
- **Q4.** Which `DATA_CURRENT_VERSION` should the Rust build write, and does the package version (1.4.0.0, in `version` and `version_comment`) change with it? No configured .result pins either.
- **Q5.** May meta.db stay SQLite (rusqlite with its bundled C library) for the first release?

## 12. How the counts were made

Run in /Users/colin/seekdb-dev/migrate-to-rust unless noted.
- Generated output: `find . -type f | xargs wc -l` and `find . -name '*.cpp' | xargs cat | wc -l` in the reference build's generated/ directory; id ranges by a Python pass over `_TID = (\d+)` in ob_inner_table_schema_constants.h; `grep -c 'ADD_COLUMN_SCHEMA'`, `grep -c '^int ObInnerTableSchema::'`, `grep -c set_view_definition` over the generated .cpp files.
- The definition file: `grep -c '^def_table_schema'`, `grep -o "table_type *= *'[A-Z_]*'" | sort | uniq -c`, `grep -c '^def_sys_index_table('`, `grep -c 'gen_history_table_def('`; the C++ names with `grep -oE "\b(OB|MAX|TABLE|INDEX|USING|ENCODING|PARTITION|SYSTEM|VIRTUAL|CS|INT|COLUMN|SERVER|DEFAULT)_[A-Z0-9_]+\b" | sort -u | wc -l`.
- JSON: a Python `json.load` counting keys, flags and hook fields. errno: `grep -c '^DEFINE_ERROR('` and the like; the specifier forms with a `grep -o` printf regex over ob_errno.def.
- Checked-in output: `git ls-files <files> | xargs cat | wc -l` per generator.
- Inner-SQL use: `git grep -c` for `ObDMLSqlSplicer`, `ObDMLExecHelper` and `exec_(insert|update|delete|replace|insert_update)`; `git grep -E 'ObMySQLProxy|ObISQLClient|ObMySQLTransaction' -- src/share/schema src/observer/schema src/rootserver | wc -l`; `git grep -E '\.write\(' -- src/share/schema` and `'\.read\('`; `grep -c 'int ObSchemaServiceSQLImpl::'` and `grep -c 'ORDER BY'`.
- Dead code: `git grep -n 'set_debug()'`, `git grep -n 'TableIdCompare\|batch_create_schema\|construct_schema('`, `git grep -n 'TIME_ZONE_NAMES\|TIME_ZONE_TRANS'`, `git grep -n ob_ctype_uca`, and `git grep -l '<file>' -- '*.bzl' '*.bazel' '*CMakeLists.txt' '*.cmake'` for ob_ctype_uca, timezone/localtime, ob_timezone_info.cpp and ob_ik_dic.cpp.
- Plain-SQL features: python3 migration/design/evidence/plain_sql_features.py (the list of 100 cases is saved as plain_sql_no_vt_pl.txt beside it).
- Bootstrap-specific C++, about 11.9K lines: `wc -l` over ob_bootstrap, ob_load_inner_table_schema_executor, ob_partition_creator, ob_table_creator, ob_tablet_creator, ob_dump_inner_table_schema, ob_load_inner_table_schema, ob_runtime_ddl_service, ob_system_bootstrap_service.h, ob_system_package_load_{task,service}, ob_core_table_proxy, ob_global_stat_proxy, ob_dml_sql_splicer, ob_data_version_mgr, src/share/storage and ob_config_storage.
- Prior art: the storage notes (written at 073e9b2f1) describe log-stream creation; at 834bbee1e only the palf and three-tablet creation was checked again (ob_ls_service.cpp:591-630).
