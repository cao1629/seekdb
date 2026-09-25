#!/usr/bin/env python3
"""Contract tests for lossless active mysqltest classification and adapters."""

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIRECTORY.parents[1]

import sys

sys.path.insert(0, str(SCRIPT_DIRECTORY))

import mysqltest_parser as parser  # noqa: E402
import run_mysqltest_phase as phase  # noqa: E402


class MysqltestParserTest(unittest.TestCase):
    """Protect corpus coverage, translation fidelity, and phase boundaries."""

    def test_discovers_all_active_and_ci_selected_cases(self):
        """Reuse the supported host runner selection without dropping active files."""
        cases = parser.discover_active_cases(REPOSITORY_ROOT)

        self.assertEqual(283, len(cases))
        self.assertEqual(272, sum(case.ci_selected for case in cases))
        self.assertEqual(len(cases), len({case.name for case in cases}))
        self.assertEqual(sorted(case.name for case in cases),
                         [case.name for case in cases])

    def test_recursive_source_preserves_errors_and_provenance(self):
        """Expand nested sources textually while retaining exact origin metadata."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            mysql_root = root / "tools/deploy/mysql_test"
            test = mysql_root / "t/demo.test"
            first = mysql_root / "include/first.inc"
            second = mysql_root / "include/nested/second.inc"
            result = mysql_root / "r/mysql/demo.result"
            second.parent.mkdir(parents=True)
            test.parent.mkdir(parents=True, exist_ok=True)
            first.parent.mkdir(parents=True, exist_ok=True)
            result.parent.mkdir(parents=True, exist_ok=True)
            test.write_text(
                "--source mysql_test/include/first.inc\nSELECT 3;\n",
                encoding="utf-8")
            first.write_text(
                "--error 0,1062,ER_DUP_ENTRY\nSELECT 1;\n"
                "--source nested/second.inc\n",
                encoding="utf-8")
            second.write_text("SELECT 'two;still';\n", encoding="utf-8")
            result.write_text(
                "SELECT 1;\nvalue\n1\n"
                "SELECT 'two;still';\nvalue\ntwo;still\n"
                "SELECT 3;\nvalue\n3\n",
                encoding="utf-8")

            parsed = parser.parse_test_file(
                root, test, "demo", result_path=result)

        self.assertEqual(3, len(parsed.statements))
        self.assertEqual(("0", "1062", "ER_DUP_ENTRY"),
                         parsed.statements[0].expected_errors)
        self.assertEqual("SELECT 'two;still'", parsed.statements[1].sql)
        self.assertEqual("tools/deploy/mysql_test/include/first.inc",
                         parsed.statements[0].provenance.source_path)
        self.assertEqual(
            ("tools/deploy/mysql_test/t/demo.test",
             "tools/deploy/mysql_test/include/first.inc",
             "tools/deploy/mysql_test/include/nested/second.inc"),
            parsed.statements[1].provenance.include_stack)
        self.assertEqual("device-native", parsed.execution_class)
        self.assertEqual("lossless", parsed.device_applicability)
        self.assertEqual(("value", "two;still"),
                         parsed.statements[1].expected_output)

    def test_source_cycle_and_path_escape_are_rejected(self):
        """Fail closed for recursive cycles and includes outside mysql_test."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mysql_root = root / "tools/deploy/mysql_test"
            case = mysql_root / "t/cycle.test"
            include = mysql_root / "include/cycle.inc"
            case.parent.mkdir(parents=True)
            include.parent.mkdir(parents=True)
            case.write_text(
                "--source mysql_test/include/cycle.inc\n", encoding="utf-8")
            include.write_text(
                "--source mysql_test/t/cycle.test\n", encoding="utf-8")
            with self.assertRaisesRegex(parser.MysqltestParseError, "cycle"):
                parser.parse_test_file(root, case, "cycle")

            case.write_text("--source ../../outside.inc\n", encoding="utf-8")
            (root / "tools/outside.inc").write_text("SELECT 1;\n", encoding="utf-8")
            with self.assertRaisesRegex(parser.MysqltestParseError, "escapes"):
                parser.parse_test_file(root, case, "escape")

    def test_sources_results_and_includes_reject_symlinks(self):
        """Use only contained regular corpus files and never follow symlinks."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mysql_root = root / "tools/deploy/mysql_test"
            case = mysql_root / "t/demo.test"
            result = mysql_root / "r/mysql/demo.result"
            include = mysql_root / "include/body.inc"
            outside = root / "outside.txt"
            case.parent.mkdir(parents=True)
            result.parent.mkdir(parents=True)
            include.parent.mkdir(parents=True)
            outside.write_text("SELECT 1;\n", encoding="utf-8")

            case.symlink_to(outside)
            with self.assertRaisesRegex(parser.MysqltestParseError, "regular"):
                parser.parse_test_file(root, case, "demo", result_path=result)
            case.unlink()

            case.write_text("SELECT 1;\n", encoding="utf-8")
            result.symlink_to(outside)
            with self.assertRaisesRegex(parser.MysqltestParseError, "regular"):
                parser.parse_test_file(root, case, "demo", result_path=result)
            result.unlink()

            case.write_text(
                "--source mysql_test/include/body.inc\n", encoding="utf-8")
            include.symlink_to(outside)
            with self.assertRaisesRegex(parser.MysqltestParseError, "regular"):
                parser.parse_test_file(root, case, "demo", result_path=result)

    def test_unsupported_semantics_are_explicit_not_silently_dropped(self):
        """Classify every non-lossless mysqltest feature with a stable reason."""
        fixtures = {
            "connection": "connect (c,$HOST,u,p,test,1);\n",
            "process-shell": "--exec echo unsafe\n",
            "topology": "ALTER SYSTEM MAJOR FREEZE;\n",
            "result-rewrite": "--replace_regex /a/b/\nSELECT 1;\n",
        }
        for expected_reason, content in fixtures.items():
            with self.subTest(reason=expected_reason), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                case = root / "tools/deploy/mysql_test/t/demo.test"
                case.parent.mkdir(parents=True)
                case.write_text(content, encoding="utf-8")

                parsed = parser.parse_test_file(root, case, "demo")

                self.assertEqual("host-only", parsed.execution_class)
                self.assertEqual("not-applicable", parsed.device_applicability)
                self.assertIn(expected_reason, parsed.unsupported_reasons)
                self.assertEqual((), parsed.device_case_ids)

    def test_classification_is_complete_and_device_ids_are_stable(self):
        """Promote only cases backed by a reviewed transcript executor."""
        classified = parser.classify_active_corpus(REPOSITORY_ROOT)
        by_name = {case.name: case for case in classified}

        self.assertEqual(283, len(classified))
        self.assertEqual("device-native", by_name["empty_table"].execution_class)
        self.assertEqual(("ios.mysqltest.empty_table",),
                         by_name["empty_table"].device_case_ids)
        active = {case.name: case for case in parser.discover_active_cases(
            REPOSITORY_ROOT)}
        self.assertTrue(parser._reviewed_transcript_supported(
            active["empty_table"]))
        for name in ("distinct", "aggr_bug200109", "limit",
                     "expr.expr_nseq", "sqlancer_optimizer_regressions"):
            with self.subTest(name=name):
                self.assertEqual("host-only", by_name[name].execution_class)
                self.assertIn("device-transcript-unavailable",
                              by_name[name].unsupported_reasons)
        self.assertEqual("host-only", by_name["connection"].execution_class)
        self.assertIn("connection", by_name["connection"].unsupported_reasons)
        self.assertEqual("host-only",
                         by_name["fork_table.fork_table_merge"].execution_class)
        self.assertIn("source-unavailable",
                      by_name["fork_table.fork_table_merge"].unsupported_reasons)
        self.assertEqual(
            ("tools/deploy/mysql_test/include/show_rpl_debug_info.inc",),
            by_name["fork_table.fork_table_merge"].unsupported_details)

    def test_recursive_closure_matches_reviewed_corpus_shape(self):
        """Audit every include edge, including a selected missing dependency."""
        audit = parser.audit_source_closure(REPOSITORY_ROOT)

        self.assertEqual(316, audit.file_count)
        self.assertEqual(302, audit.source_directive_count)
        self.assertEqual(2, audit.maximum_depth)
        self.assertIn(
            "tools/deploy/mysql_test/include/show_rpl_debug_info.inc",
            audit.missing_sources)

    def test_phase_plan_separates_host_gate_and_one_device_case_per_source(self):
        """Keep host mysqltest evidence distinct from independently resumable device cases."""
        plan = phase.build_phase_plan(REPOSITORY_ROOT)
        device = [case for case in plan if case.execution_class == "device-native"]
        host = [case for case in plan if case.case_id == "ios.mysqltest.host-gate"]

        self.assertTrue(device)
        self.assertEqual(["ios.mysqltest.empty_table"],
                         [case.case_id for case in device])
        self.assertEqual(len(device), len({case.source_name for case in device}))
        self.assertEqual(1, len(host))
        self.assertEqual("host-only", host[0].execution_class)
        self.assertTrue(all(case.failure_key == case.source_name for case in device))
        self.assertTrue(all(case.requires_sql_restart_followup for case in device))

    def test_resume_selects_only_failed_and_pending_source_cases(self):
        """Never rerun passed device sources while retrying failed and pending ones."""
        plan = (
            phase.MysqltestPhaseCase("ios.mysqltest.a", "a", "device-native", "a", True),
            phase.MysqltestPhaseCase("ios.mysqltest.b", "b", "device-native", "b", True),
            phase.MysqltestPhaseCase("ios.mysqltest.c", "c", "device-native", "c", True),
            phase.MysqltestPhaseCase("ios.mysqltest.d", "d", "device-native", "d", True),
        )
        selected = phase.select_resume_cases(
            plan, {"ios.mysqltest.a": "passed", "ios.mysqltest.b": "failed",
                   "ios.mysqltest.c": "pending", "ios.mysqltest.d": "running"})

        self.assertEqual(["ios.mysqltest.b", "ios.mysqltest.c"],
                         [case.case_id for case in selected])

    def test_cpp_registry_and_classification_manifest_are_wired(self):
        """Require the generated device suite and reviewed manifest boundary."""
        source = (SCRIPT_DIRECTORY / "mysqltest_device_cases.cpp").read_text(
            encoding="utf-8")
        cmake = (SCRIPT_DIRECTORY / "app/CMakeLists.txt").read_text(
            encoding="utf-8")
        manifest = json.loads((SCRIPT_DIRECTORY / "ios-test-classification.json")
                              .read_text(encoding="utf-8"))

        self.assertIn("make_mysqltest_device_registry", source)
        self.assertIn("expected_transcript", source)
        self.assertIn("expected_affected_rows", source)
        self.assertIn("g_server_modules_ready", source)
        self.assertIn("get_field_columns", source)
        self.assertIn("cname_", source)
        for fragment in (
                "set @@session.explicit_defaults_for_timestamp=off",
                "select count(*) from t1", "count(*)", "nr\\tb\\tstr"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)
        self.assertIn("../mysqltest_device_cases.cpp", cmake)
        self.assertIn("mysqltest-device-lossless", manifest["classifications"])

    def test_empty_result_contract_rejects_wrong_ordered_field_label(self):
        """Compile the standalone C++ contract and prove labels are ordered."""
        compiler = os.environ.get("CXX", "c++")
        source = (SCRIPT_DIRECTORY / "mysqltest_device_cases.cpp").read_text(
            encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / "contract.cpp"
            executable = root / "contract"
            harness.write_text(
                '#include "mysqltest_result_contract.h"\n'
                '#include <array>\n'
                '#include <string_view>\n'
                'struct Rows {\n'
                '  int status;\n'
                '  int calls = 0;\n'
                '  int next() { ++calls; return status; }\n'
                '};\n'
                'int main() {\n'
                '  using seekdb::ios_test::empty_result_matches;\n'
                '  const std::array<std::string_view, 3> expected = '
                '{"nr", "b", "str"};\n'
                '  const std::array<std::string_view, 3> correct = '
                '{"nr", "b", "str"};\n'
                '  const std::array<std::string_view, 3> wrong = '
                '{"nr", "str", "b"};\n'
                '  Rows good{9}; Rows bad{0};\n'
                '  const bool accepted = empty_result_matches('
                'correct, expected, &good, 9);\n'
                '  const bool wrong_label = empty_result_matches('
                'wrong, expected, &good, 9);\n'
                '  const bool wrong_end = empty_result_matches('
                'correct, expected, &bad, 9);\n'
                '  const bool null_row = empty_result_matches<Rows>('
                'correct, expected, nullptr, 9);\n'
                '  return accepted && !wrong_label && !wrong_end && '
                '!null_row && good.calls == 1 ? 0 : 1;\n'
                '}\n',
                encoding="utf-8")
            compiled = subprocess.run([
                compiler, "-std=c++17", "-I", str(SCRIPT_DIRECTORY),
                str(harness), "-o", str(executable),
            ], check=False, capture_output=True, text=True)
            self.assertEqual(0, compiled.returncode, compiled.stderr)
            executed = subprocess.run(
                [str(executable)], check=False, capture_output=True, text=True)
            self.assertEqual(0, executed.returncode, executed.stderr)

        read_empty = source[source.index("bool read_empty"):
                            source.index("int run_empty_table")]
        self.assertNotIn("get_column_count", read_empty)
        self.assertIn("result_set().get_field_columns", read_empty)
        self.assertIn("empty_result_matches", read_empty)

    def test_host_gate_requires_exact_272_case_success(self):
        """Bind host evidence to source, corpus, binaries, and exact cases."""
        selected = [
            case.name for case in parser.discover_active_cases(REPOSITORY_ROOT)
            if case.ci_selected]
        with tempfile.TemporaryDirectory() as directory:
            host_runner = parser._load_host_discovery(REPOSITORY_ROOT)
            directory_path = Path(directory).resolve()
            binary_paths = {}
            for name in ("seekdb", "obclient", "mysqltest"):
                path = directory_path / name
                path.write_bytes((name + "-binary").encode("utf-8"))
                path.chmod(0o700)
                binary_paths[name] = path
            result = directory_path / "host.json"
            identity = host_runner.build_host_evidence_identity(
                REPOSITORY_ROOT, binary_paths)
            payload = host_runner.build_merged_evidence(
                identity=identity, run_id="host-run", slice_count=1,
                executed_cases=selected, failed_cases=[], errors=[])
            result.write_text(json.dumps(payload), encoding="utf-8")
            summary = phase.validate_host_gate(
                REPOSITORY_ROOT, result, binary_paths)
            self.assertEqual("host-only", summary["execution_class"])
            self.assertEqual(272, summary["case_count"])
            self.assertTrue(summary["success"])
            self.assertEqual("host-run", summary["run_id"])
            self.assertEqual(identity["host_build_identity"],
                             summary["host_build_identity"])

            mutations = {
                "stale-source": {"source_commit": "0" * 40},
                "stale-corpus": {"corpus_digest": "0" * 64},
                "missing-case": {"executed_cases": selected[:-1]},
                "extra-case": {"executed_cases": [*selected, "extra"]},
                "binary-identity": {"host_build_identity": "0" * 64},
            }
            for name, change in mutations.items():
                with self.subTest(name=name):
                    tampered = dict(payload)
                    tampered.update(change)
                    tampered = host_runner.seal_evidence(tampered)
                    result.write_text(json.dumps(tampered), encoding="utf-8")
                    with self.assertRaises(phase.MysqltestPhaseError):
                        phase.validate_host_gate(
                            REPOSITORY_ROOT, result, binary_paths)

            result.write_text(json.dumps(payload), encoding="utf-8")
            changed = result.read_text(encoding="utf-8").replace(
                '"success": true', '"success": false')
            result.write_text(changed, encoding="utf-8")
            with self.assertRaises(phase.MysqltestPhaseError):
                phase.validate_host_gate(
                    REPOSITORY_ROOT, result, binary_paths)

    def test_host_gate_rejects_symlink_and_oversized_evidence(self):
        """Read host evidence through one bounded nofollow regular-file fd."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaises(phase.MysqltestPhaseError):
                phase.validate_host_gate(REPOSITORY_ROOT, link, {})

            oversized = root / "oversized.json"
            oversized.write_bytes(b"{" + b" " * phase.MAX_HOST_RESULT_BYTES)
            with self.assertRaises(phase.MysqltestPhaseError):
                phase.validate_host_gate(REPOSITORY_ROOT, oversized, {})

    def test_slice_reader_rejects_fifo_symlink_and_oversized_json(self):
        """Merge inputs must be bounded stable regular nofollow files."""
        host_runner = parser._load_host_discovery(REPOSITORY_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            fifo = root / "fifo.json"
            os.mkfifo(fifo)
            oversized = root / "oversized.json"
            oversized.write_bytes(
                b"{" + b" " * host_runner.MAX_EVIDENCE_FILE_BYTES)
            for path in (link, fifo, oversized):
                with self.subTest(path=path.name), self.assertRaises(
                        host_runner.RunnerError):
                    host_runner._read_json_evidence(path)

            recursive = root / "recursive.json"
            recursive.write_text("[" * 2000 + "]" * 2000, encoding="utf-8")
            with self.assertRaises(host_runner.RunnerError):
                host_runner._read_json_evidence(recursive)

    def test_host_runner_writes_never_follow_precreated_entries(self):
        """Reject output links and non-regular files without changing victims."""
        host_runner = parser._load_host_discovery(REPOSITORY_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            victim = root / "victim"
            victim.write_text("unchanged", encoding="utf-8")
            output = root / "result.json"
            output.symlink_to(victim)
            with self.assertRaises(host_runner.RunnerError):
                host_runner.write_json(output, {"value": 1})
            self.assertEqual("unchanged", victim.read_text(encoding="utf-8"))

            output.unlink()
            fixed_temporary = root / "result.json.tmp"
            fixed_temporary.symlink_to(victim)
            host_runner.write_json(output, {"value": 2})
            self.assertEqual("unchanged", victim.read_text(encoding="utf-8"))
            self.assertTrue(fixed_temporary.is_symlink())

            work = root / "work"
            infrastructure = work / "failures" / "infrastructure"
            infrastructure.mkdir(parents=True)
            error_output = infrastructure / "error.txt"
            error_output.symlink_to(victim)
            args = argparse.Namespace(work_dir=work, base_dir=root / "absent")
            with self.assertRaises(host_runner.RunnerError):
                host_runner.save_infrastructure_failure(args, "failure")
            self.assertEqual("unchanged", victim.read_text(encoding="utf-8"))

            case_directory = work / "failures" / "demo"
            case_directory.mkdir()
            (case_directory / "mysqltest.log").symlink_to(victim)
            with self.assertRaises(host_runner.RunnerError):
                host_runner.save_case_failure(args, "demo", "failure log")
            self.assertEqual("unchanged", victim.read_text(encoding="utf-8"))

            unsafe_log = work / "mysqltest_log"
            unsafe_log.mkdir()
            (unsafe_log / "timer").symlink_to(victim)
            with self.assertRaises(host_runner.RunnerError):
                host_runner._validate_output_tree(unsafe_log)
            self.assertEqual("unchanged", victim.read_text(encoding="utf-8"))

            tmp_dir = work / "tmp"
            tmp_dir.mkdir()
            case_root = root / "case"
            case_root.mkdir()
            test_file = case_root / "demo.test"
            result_file = case_root / "demo.result"
            test_file.write_text("SELECT 1;\n", encoding="utf-8")
            result_file.write_text("1\n", encoding="utf-8")
            case = host_runner.MysqltestCase(
                "demo", test_file, result_file)
            run_args = argparse.Namespace(
                mysqltest=root / "mysqltest", host="127.0.0.1", port=2881,
                obclient=root / "obclient", base_dir=root / "instance")
            with mock.patch.object(host_runner.subprocess, "run") as run:
                with self.assertRaises(host_runner.RunnerError):
                    host_runner.run_case(
                        run_args, root, case, tmp_dir, unsafe_log)
            run.assert_not_called()

    def test_local_host_gate_runs_tracked_runner_before_validating(self):
        """Execute tracked host tools only from an identity-bound snapshot."""
        selected = [
            case.name for case in parser.discover_active_cases(REPOSITORY_ROOT)
            if case.ci_selected]
        host_runner = parser._load_host_discovery(REPOSITORY_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binaries = {}
            for name in ("seekdb", "obclient", "mysqltest"):
                path = root / name
                path.write_bytes(name.encode("utf-8"))
                path.chmod(0o700)
                binaries[name] = path
            identity = host_runner.build_host_evidence_identity(
                REPOSITORY_ROOT, binaries)
            commands = []

            def fake_run(command, _cwd, _deadline):
                """Materialize the exact tracked runner outputs for each command."""
                commands.append(tuple(command))
                if "run" in command:
                    work = Path(command[command.index("--work-dir") + 1])
                    work.mkdir(parents=True, exist_ok=True)
                    payload = host_runner.build_slice_evidence(
                        identity, 0, 1, selected, [], None)
                    (work / "seekdb_result.json").write_text(
                        json.dumps(payload), encoding="utf-8")
                else:
                    output = Path(command[command.index("--output") + 1])
                    payload = host_runner.build_merged_evidence(
                        identity, "runner-id", 1, selected, [], [])
                    output.write_text(json.dumps(payload), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, b"", b"")

            summary = phase.execute_local_host_gate(
                REPOSITORY_ROOT, root / "work", "runner-id", binaries,
                process_runner=fake_run)

        self.assertEqual(2, len(commands))
        self.assertIn(str(REPOSITORY_ROOT /
                          ".github/script/seekdb/mysqltest_for_seekdb.py"),
                      commands[0])
        self.assertEqual("run", commands[0][2])
        self.assertEqual("merge", commands[1][2])
        for option, name in (("--seekdb", "seekdb"),
                             ("--obclient", "obclient"),
                             ("--mysqltest", "mysqltest")):
            value = Path(commands[0][commands[0].index(option) + 1])
            self.assertEqual(root / "work/binaries" / name, value)
            self.assertNotEqual(binaries[name], value)
        self.assertEqual("runner-id", summary["run_id"])
        self.assertEqual(
            summary["source_host_binaries"],
            summary["snapshot_host_binaries"])

    def test_source_replacement_after_snapshot_fails_only_after_snapshot_exec(self):
        """Execute immutable bytes, then reject a changed current source identity."""
        selected = [
            case.name for case in parser.discover_active_cases(REPOSITORY_ROOT)
            if case.ci_selected]
        host_runner = parser._load_host_discovery(REPOSITORY_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binaries = {}
            for name in ("seekdb", "obclient", "mysqltest"):
                path = root / name
                path.write_bytes(name.encode("utf-8"))
                path.chmod(0o700)
                binaries[name] = path
            executed = []

            def replace_after_exec(command, _cwd, _deadline):
                """Replace one source after observing snapshot-only argv."""
                if "run" in command:
                    snapshot = Path(command[command.index("--seekdb") + 1])
                    replacement = root / "replacement"
                    replacement.write_bytes(b"changed")
                    replacement.chmod(0o700)
                    replacement.replace(binaries["seekdb"])
                    executed.append(snapshot.read_bytes())
                    snapshot_binaries = {
                        name: Path(command[command.index(option) + 1])
                        for option, name in (("--seekdb", "seekdb"),
                                             ("--obclient", "obclient"),
                                             ("--mysqltest", "mysqltest"))
                    }
                    identity = host_runner.build_host_evidence_identity(
                        REPOSITORY_ROOT, snapshot_binaries)
                    work = Path(command[command.index("--work-dir") + 1])
                    work.mkdir(parents=True, exist_ok=True)
                    payload = host_runner.build_slice_evidence(
                        identity, 0, 1, selected, [], None)
                    (work / "seekdb_result.json").write_text(
                        json.dumps(payload), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with mock.patch.object(
                    phase, "_destroy_managed_host_instance") as destroy, \
                    self.assertRaises(phase.MysqltestPhaseError):
                phase.execute_local_host_gate(
                    REPOSITORY_ROOT, root / "work", "runner-id", binaries,
                    process_runner=replace_after_exec)
            destroy.assert_called_once()

        self.assertEqual([b"seekdb"], executed)

    def test_tracked_runner_executes_fake_mysqltest_from_snapshot_path(self):
        """Pass the run-local mysqltest snapshot as subprocess argv zero."""
        host_runner = parser._load_host_discovery(REPOSITORY_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binaries = {}
            for name in ("seekdb", "obclient", "mysqltest"):
                path = root / name
                path.write_bytes(name.encode("utf-8"))
                path.chmod(0o700)
                binaries[name] = path
            bundle = phase.prepare_host_binary_snapshots(
                binaries, root / "work")
            self.assertEqual(
                0o500, stat.S_IMODE((root / "work/binaries").stat().st_mode))
            self.assertTrue(all(
                stat.S_IMODE(path.stat().st_mode) == 0o500
                for path in bundle.snapshot_paths.values()))
            tmp_dir = root / "tmp"
            log_dir = root / "log"
            tmp_dir.mkdir()
            log_dir.mkdir()
            test_file = root / "demo.test"
            result_file = root / "demo.result"
            test_file.write_text("SELECT 1;\n", encoding="utf-8")
            result_file.write_text("1\n", encoding="utf-8")
            case = host_runner.MysqltestCase(
                "demo", test_file, result_file)
            args = argparse.Namespace(
                mysqltest=bundle.snapshot_paths["mysqltest"],
                obclient=bundle.snapshot_paths["obclient"],
                host="127.0.0.1", port=2881,
                base_dir=root / "instance")
            completed = subprocess.CompletedProcess((), 0, "", "")
            with mock.patch.object(
                    host_runner.subprocess, "run",
                    return_value=completed) as run, \
                    mock.patch("builtins.print"):
                return_code, _ = host_runner.run_case(
                    args, root, case, tmp_dir, log_dir)

        self.assertEqual(0, return_code)
        self.assertEqual(
            str(bundle.snapshot_paths["mysqltest"]),
            run.call_args.args[0][0])

    def test_snapshot_parent_symlink_and_binary_replacement_are_rejected(self):
        """Reject changed snapshot bytes and linked snapshot directory parents."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binaries = {}
            for name in ("seekdb", "obclient", "mysqltest"):
                path = root / name
                path.write_bytes(name.encode("utf-8"))
                path.chmod(0o700)
                binaries[name] = path
            bundle = phase.prepare_host_binary_snapshots(
                binaries, root / "work")
            snapshot = bundle.snapshot_paths["mysqltest"]
            snapshot.chmod(0o700)
            snapshot.write_bytes(b"changed")
            snapshot.chmod(0o500)
            with self.assertRaises(phase.MysqltestPhaseError):
                phase.validate_host_binary_snapshots(bundle)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binaries = {}
            for name in ("seekdb", "obclient", "mysqltest"):
                path = root / name
                path.write_bytes(name.encode("utf-8"))
                path.chmod(0o700)
                binaries[name] = path
            bundle = phase.prepare_host_binary_snapshots(
                binaries, root / "work")
            snapshot_directory = root / "work/binaries"
            original_directory = root / "work/original-binaries"
            snapshot_directory.rename(original_directory)
            snapshot_directory.symlink_to(
                original_directory, target_is_directory=True)
            with self.assertRaises(phase.MysqltestPhaseError):
                phase.validate_host_binary_snapshots(bundle)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binaries = {}
            for name in ("seekdb", "obclient", "mysqltest"):
                path = root / name
                path.write_bytes(name.encode("utf-8"))
                path.chmod(0o700)
                binaries[name] = path
            external = root / "external"
            external.mkdir()
            work = root / "work"
            work.symlink_to(external, target_is_directory=True)
            with self.assertRaises(phase.MysqltestPhaseError):
                phase.prepare_host_binary_snapshots(binaries, work)

    def test_macho_snapshot_dependencies_must_be_system_absolute_paths(self):
        """Fail closed when a snapshotted Mach-O needs a relative local dylib."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = {}
            for name in ("seekdb", "obclient", "mysqltest"):
                path = root / name
                path.write_bytes(b"\xcf\xfa\xed\xfe" + name.encode("utf-8"))
                path.chmod(0o500)
                paths[name] = path
            system_only = subprocess.CompletedProcess(
                (), 0,
                b"tool:\n\t/usr/lib/libSystem.B.dylib (compatibility 1)\n",
                b"")
            with mock.patch.object(
                    phase.subprocess, "run", return_value=system_only):
                phase._validate_snapshot_macho_dependencies(paths)

            local_dependency = subprocess.CompletedProcess(
                (), 0,
                b"tool:\n\t@rpath/liblocal.dylib (compatibility 1)\n",
                b"")
            with mock.patch.object(
                    phase.subprocess, "run", return_value=local_dependency), \
                    self.assertRaises(phase.MysqltestPhaseError):
                phase._validate_snapshot_macho_dependencies(paths)

    @unittest.skipUnless(shutil.which("cc"), "requires a host C compiler")
    def test_outer_failures_destroy_detached_sdb_daemon(self):
        """Use tracked sdb cleanup after timeout, interrupt, or runner failure."""
        source = r'''
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static void stop(int signal_number) { (void)signal_number; _exit(0); }
int main(int argc, char **argv) {
  const char *base = NULL;
  for (int index = 1; index < argc; ++index) {
    if (strncmp(argv[index], "--base-dir=", 11) == 0) base = argv[index] + 11;
  }
  if (base == NULL) return 2;
  char run[4096];
  char pid_path[4096];
  if (snprintf(run, sizeof(run), "%s/run", base) >= (int)sizeof(run)) return 3;
  if (mkdir(run, 0700) != 0 && errno != EEXIST) return 4;
  if (snprintf(pid_path, sizeof(pid_path), "%s/seekdb.pid", run)
      >= (int)sizeof(pid_path)) return 5;
  FILE *stream = fopen(pid_path, "w");
  if (stream == NULL) return 6;
  fprintf(stream, "%d\n", (int)getpid());
  fclose(stream);
  signal(SIGTERM, stop);
  for (;;) pause();
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            c_file = root / "daemon.c"
            binary = root / "fake-seekdb"
            c_file.write_text(source, encoding="utf-8")
            subprocess.run(
                [shutil.which("cc"), str(c_file), "-o", str(binary)],
                check=True, capture_output=True)
            binaries = {
                "seekdb": binary,
                "obclient": binary,
                "mysqltest": binary,
            }
            failures = (
                subprocess.TimeoutExpired("host-runner", 1),
                KeyboardInterrupt(),
                RuntimeError("runner failed"),
            )
            for index, failure in enumerate(failures):
                with self.subTest(failure=type(failure).__name__):
                    work = root / "host-{}".format(index)
                    daemon_pid = []

                    def start_then_fail(_command, cwd, _deadline):
                        """Start a detached tracked-sdb instance, then fail."""
                        instance = work / "instance"
                        completed = subprocess.run(
                            [sys.executable,
                             str(REPOSITORY_ROOT /
                                 ".github/script/seekdb/sdb.py"),
                             "start", "--binary", str(binary),
                             "--base-dir", str(instance)],
                            cwd=str(cwd), capture_output=True, check=False)
                        self.assertEqual(
                            0, completed.returncode, completed.stderr)
                        pid_file = instance / "run/seekdb.pid"
                        deadline = time.monotonic() + 3.0
                        while (time.monotonic() < deadline
                               and not pid_file.exists()):
                            time.sleep(0.02)
                        daemon_pid.append(int(
                            pid_file.read_text(encoding="utf-8")))
                        raise failure

                    with self.assertRaises(type(failure)):
                        phase.execute_local_host_gate(
                            REPOSITORY_ROOT, work, "runner-id", binaries,
                            process_runner=start_then_fail)
                    self.assertFalse((work / "instance").exists())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(daemon_pid[0], 0)

    def test_host_workspace_rejects_symlink_components(self):
        """Never create runner files through pre-existing directory symlinks."""
        unsafe_components = (
            ("mysqltest-host",),
            ("mysqltest-host", "slice_0"),
            ("mysqltest-host", "slice_0", "tmp"),
            ("mysqltest-host", "slice_0", "mysqltest_log"),
            ("mysqltest-host", "instance"),
        )
        for components in unsafe_components:
            with self.subTest(component="/".join(components)), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                run = root / "run"
                run.mkdir()
                external = root / "external"
                external.mkdir()
                parent = run
                for component in components[:-1]:
                    parent = parent / component
                    parent.mkdir()
                (parent / components[-1]).symlink_to(external)
                with self.assertRaises(phase.MysqltestPhaseError):
                    phase._prepare_host_workspace(run / "mysqltest-host")
                self.assertEqual([], list(external.iterdir()))

    def test_host_workspace_leaves_instance_for_sdb_handshake(self):
        """Do not create an empty base-dir that the tracked sdb cannot destroy."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            work = root / "mysqltest-host"
            phase._prepare_host_workspace(work)
            instance = work / "instance"
            self.assertFalse(instance.exists())
            completed = subprocess.run(
                [sys.executable,
                 str(REPOSITORY_ROOT / ".github/script/seekdb/sdb.py"),
                 "destroy", "--base-dir", str(instance)],
                cwd=str(REPOSITORY_ROOT), capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_host_binary_inputs_must_be_local_regular_executables(self):
        """Reject missing, non-executable, and symlink host binary inputs."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            executable = root / "tool"
            executable.write_text("tool", encoding="utf-8")
            executable.chmod(0o700)
            link = root / "link"
            link.symlink_to(executable)
            base = {
                variable: str(executable)
                for variable in phase.HOST_BINARY_ENVIRONMENTS.values()
            }
            self.assertEqual(
                {"seekdb", "obclient", "mysqltest"},
                set(phase.resolve_host_binaries(base)))
            for value in (str(link), str(root / "missing")):
                environment = dict(base)
                environment["SEEKDB_IPHONE_HOST_MYSQLTEST"] = value
                with self.assertRaises(phase.MysqltestPhaseError):
                    phase.resolve_host_binaries(environment)

    def test_host_binaries_default_to_repository_canonical_outputs(self):
        """Discover only the three canonical current-repository executables."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            expected = {
                "seekdb": root / "build_release/src/observer/seekdb",
                "obclient": root / "deps/3rd/u01/obclient/bin/obclient",
                "mysqltest": root / "deps/3rd/u01/obclient/bin/mysqltest",
            }
            for name, path in expected.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name, encoding="utf-8")
                path.chmod(0o700)

            self.assertEqual(
                expected, phase.resolve_host_binaries({}, root))

    def test_partial_host_binary_environment_uses_canonical_missing_values(self):
        """Keep each explicit executable and default only its missing peers."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            canonical = {
                "seekdb": root / "build_release/src/observer/seekdb",
                "obclient": root / "deps/3rd/u01/obclient/bin/obclient",
                "mysqltest": root / "deps/3rd/u01/obclient/bin/mysqltest",
            }
            for name, path in canonical.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name, encoding="utf-8")
                path.chmod(0o700)
            explicit = root / "explicit-seekdb"
            explicit.write_text("explicit", encoding="utf-8")
            explicit.chmod(0o700)

            resolved = phase.resolve_host_binaries(
                {"SEEKDB_IPHONE_HOST_SEEKDB": str(explicit)}, root)

            self.assertEqual(explicit, resolved["seekdb"])
            self.assertEqual(canonical["obclient"], resolved["obclient"])
            self.assertEqual(canonical["mysqltest"], resolved["mysqltest"])

    def test_invalid_explicit_host_binary_never_falls_back_to_canonical(self):
        """Treat a supplied invalid path as an error even if its default exists."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for path in (
                    root / "build_release/src/observer/seekdb",
                    root / "deps/3rd/u01/obclient/bin/obclient",
                    root / "deps/3rd/u01/obclient/bin/mysqltest"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("binary", encoding="utf-8")
                path.chmod(0o700)
            with self.assertRaises(phase.MysqltestPhaseError):
                phase.resolve_host_binaries(
                    {"SEEKDB_IPHONE_HOST_SEEKDB": str(root / "missing")},
                    root)

    def test_canonical_host_binary_symlink_is_rejected(self):
        """Never accept a linked canonical executable or search for another one."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "target"
            target.write_text("binary", encoding="utf-8")
            target.chmod(0o700)
            seekdb = root / "build_release/src/observer/seekdb"
            seekdb.parent.mkdir(parents=True)
            seekdb.symlink_to(target)
            for path in (
                    root / "deps/3rd/u01/obclient/bin/obclient",
                    root / "deps/3rd/u01/obclient/bin/mysqltest"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("binary", encoding="utf-8")
                path.chmod(0o700)
            with self.assertRaises(phase.MysqltestPhaseError):
                phase.resolve_host_binaries({}, root)

    def test_canonical_host_binary_parent_symlink_is_rejected(self):
        """Never traverse a canonical parent link into a neighboring tree."""
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root = parent / "current"
            external = parent / "external"
            root.mkdir()
            external_seekdb = external / "src/observer/seekdb"
            external_seekdb.parent.mkdir(parents=True)
            external_seekdb.write_text("external", encoding="utf-8")
            external_seekdb.chmod(0o700)
            (root / "build_release").symlink_to(external, target_is_directory=True)
            for path in (
                    root / "deps/3rd/u01/obclient/bin/obclient",
                    root / "deps/3rd/u01/obclient/bin/mysqltest"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("binary", encoding="utf-8")
                path.chmod(0o700)

            with self.assertRaises(phase.MysqltestPhaseError):
                phase.resolve_host_binaries({}, root)

            self.assertEqual("external", external_seekdb.read_text(encoding="utf-8"))

    def test_canonical_dependency_parent_symlink_is_rejected(self):
        """Never traverse the canonical deps/3rd chain through a link."""
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root = parent / "current"
            external = parent / "external"
            root.mkdir()
            external_obclient = external / "u01/obclient/bin/obclient"
            external_mysqltest = external / "u01/obclient/bin/mysqltest"
            external_obclient.parent.mkdir(parents=True)
            for path in (external_obclient, external_mysqltest):
                path.write_text("external", encoding="utf-8")
                path.chmod(0o700)
            (root / "deps").mkdir()
            (root / "deps/3rd").symlink_to(
                external, target_is_directory=True)
            seekdb = root / "build_release/src/observer/seekdb"
            seekdb.parent.mkdir(parents=True)
            seekdb.write_text("binary", encoding="utf-8")
            seekdb.chmod(0o700)

            with self.assertRaises(phase.MysqltestPhaseError):
                phase.resolve_host_binaries({}, root)

            self.assertEqual(
                "external", external_obclient.read_text(encoding="utf-8"))

    def test_empty_explicit_host_binary_never_uses_canonical_default(self):
        """Treat empty and whitespace explicit overrides as invalid values."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for path in (
                    root / "build_release/src/observer/seekdb",
                    root / "deps/3rd/u01/obclient/bin/obclient",
                    root / "deps/3rd/u01/obclient/bin/mysqltest"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("binary", encoding="utf-8")
                path.chmod(0o700)

            for value in ("", "   "):
                with self.subTest(value=value):
                    with self.assertRaises(phase.MysqltestPhaseError):
                        phase.resolve_host_binaries(
                            {"SEEKDB_IPHONE_HOST_SEEKDB": value}, root)

    def test_host_binary_identity_rejects_parent_symlink(self):
        """Hash binary bytes only through an anchored nofollow parent chain."""
        host_runner = parser._load_host_discovery(REPOSITORY_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            external = root / "external"
            external.mkdir()
            binary = external / "seekdb"
            binary.write_bytes(b"external")
            linked_parent = root / "linked"
            linked_parent.symlink_to(external, target_is_directory=True)

            with self.assertRaises(host_runner.RunnerError):
                host_runner._sha256_regular_file(linked_parent / "seekdb")

    def test_missing_canonical_host_binary_never_searches_path_or_checkout(self):
        """Fail preflight instead of searching PATH or a neighboring checkout."""
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            root = parent / "current"
            sibling = parent / "sibling"
            path_bin = parent / "bin"
            root.mkdir()
            path_bin.mkdir()
            for name in ("seekdb", "obclient", "mysqltest"):
                executable = path_bin / name
                executable.write_text(name, encoding="utf-8")
                executable.chmod(0o700)
            sibling_seekdb = sibling / "build_release/src/observer/seekdb"
            sibling_seekdb.parent.mkdir(parents=True)
            sibling_seekdb.write_text("seekdb", encoding="utf-8")
            sibling_seekdb.chmod(0o700)

            with self.assertRaises(phase.MysqltestPhaseError):
                phase.resolve_host_binaries(
                    {"PATH": str(path_bin)}, root)

    @unittest.skipUnless(hasattr(os, "fork"), "requires process groups")
    def test_controlled_host_runner_kills_term_ignoring_descendants(self):
        """A shared deadline must reap a runner and every inherited child."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child_pid = root / "child.pid"
            program = (
                "import os,signal,time,pathlib; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "pid=os.fork(); "
                f"path=pathlib.Path({str(child_pid)!r}); "
                "path.write_text(str(pid)) if pid else None; "
                "time.sleep(60)")
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                phase._run_controlled_process(
                    [sys.executable, "-c", program], root,
                    time.monotonic() + 0.4)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 6.0)
            pid = int(child_pid.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("TERM-ignoring host runner descendant survived cleanup")

    @unittest.skipUnless(hasattr(os, "fork"), "requires process groups")
    def test_controlled_host_runner_reaps_child_after_parent_exits(self):
        """Kill a pipe-closing descendant even after its process-group leader exits."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child_pid = root / "child.pid"
            program = (
                "import os,signal,time,pathlib; "
                "pid=os.fork(); "
                "(signal.signal(signal.SIGTERM,signal.SIG_IGN), "
                "os.close(1), os.close(2), time.sleep(60)) if pid==0 else "
                f"pathlib.Path({str(child_pid)!r}).write_text(str(pid))")
            completed = phase._run_controlled_process(
                [sys.executable, "-c", program], root,
                time.monotonic() + 2.0)
            self.assertEqual(0, completed.returncode)
            pid = int(child_pid.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("child survived after the process-group leader exited")

    def test_controlled_host_runner_cleans_group_on_interrupt_or_exception(self):
        """SIGINT and unexpected failures must terminate the whole process group."""
        for failure in (KeyboardInterrupt(), RuntimeError("failure")):
            with self.subTest(failure=type(failure).__name__):
                process = mock.Mock(pid=12345, returncode=None)
                process.communicate.side_effect = [failure, (b"", b"")]
                with mock.patch.object(
                        phase.subprocess, "Popen", return_value=process) as popen, \
                        mock.patch.object(phase.os, "killpg") as killpg, \
                        mock.patch.object(
                            phase, "_process_group_exists",
                            return_value=False):
                    with self.assertRaises(type(failure)):
                        phase._run_controlled_process(
                            ["runner"], Path.cwd(), time.monotonic() + 10)
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
                self.assertEqual(
                    [mock.call(12345, phase.signal.SIGTERM),
                     mock.call(12345, phase.signal.SIGKILL)],
                    killpg.call_args_list)


if __name__ == "__main__":
    unittest.main()
