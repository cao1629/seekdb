#!/usr/bin/env python3
"""Contract tests for lossless active mysqltest classification and adapters."""

import json
import os
from pathlib import Path
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

    def test_local_host_gate_runs_tracked_runner_before_validating(self):
        """Generate evidence through tracked run and merge commands in one flow."""
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
        self.assertEqual("runner-id", summary["run_id"])

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

    def test_host_binary_inputs_must_be_local_regular_executables(self):
        """Reject missing, non-executable, and symlink host binary inputs."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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

    def test_controlled_host_runner_cleans_group_on_interrupt_or_exception(self):
        """SIGINT and unexpected failures must terminate the whole process group."""
        for failure in (KeyboardInterrupt(), RuntimeError("failure")):
            with self.subTest(failure=type(failure).__name__):
                process = mock.Mock(pid=12345, returncode=None)
                process.communicate.side_effect = [failure, (b"", b"")]
                with mock.patch.object(
                        phase.subprocess, "Popen", return_value=process) as popen, \
                        mock.patch.object(phase.os, "killpg") as killpg:
                    with self.assertRaises(type(failure)):
                        phase._run_controlled_process(
                            ["runner"], Path.cwd(), time.monotonic() + 10)
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
                killpg.assert_called_once_with(12345, phase.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
