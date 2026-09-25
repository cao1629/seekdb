#!/usr/bin/env python3
"""Parse active mysqltest sources into conservative iOS device classifications."""

from dataclasses import dataclass, replace
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import stat
from typing import Iterable, Optional


PRESENTATION_DIRECTIVES = frozenset((
    "disable_query_log", "enable_query_log",
    "disable_warnings", "enable_warnings",
))
RESULT_REWRITE_DIRECTIVES = frozenset((
    "disable_info", "disable_metadata", "disable_result_log",
    "enable_info", "enable_metadata", "enable_result_log",
    "enable_sorted_result", "replace_column", "replace_numeric_round",
    "replace_regex", "result_format", "sorted_result",
))
PROCESS_SHELL_DIRECTIVES = frozenset((
    "exec", "perl", "remove_file", "write_file", "append_file",
    "copy_file", "move_file", "chmod", "mkdir", "rmdir",
    "remove_files_wildcard", "system",
))
CONTROL_DIRECTIVES = frozenset((
    "eval", "if", "inc", "let", "real_sleep", "send", "reap",
    "sleep", "while",
))
BARE_CONNECTION_COMMANDS = frozenset((
    "connect", "connection", "dirty_close", "disconnect", "send", "reap",
))
BARE_PROCESS_COMMANDS = frozenset(("exec", "perl", "system"))
BARE_CONTROL_COMMANDS = frozenset((
    "eval", "let", "real_sleep", "sleep",
))
TOPOLOGY_SQL = re.compile(
    r"(?is)^\s*(?:alter\s+system\b|create\s+resource\s+unit\b|"
    r"alter\s+resource\s+tenant\b|.*\b__all_(?:server|zone)\b)")
DIRECTIVE = re.compile(r"^\s*--([A-Za-z_][A-Za-z0-9_]*)\b(.*)$")
BARE_COMMAND = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\b")
DEVICE_TRANSCRIPT_DIGESTS = {
    "empty_table": "731fcca0d5f83bf102ba6a3ea7b70776b706a64943c10ccf62f7c838501ffe9e",
}
MAX_CORPUS_FILE_BYTES = 64 * 1024 * 1024


class MysqltestParseError(RuntimeError):
    """Report an unsafe include or structurally incomplete mysqltest source."""

    def __init__(self, message: str, detail: Optional[str] = None):
        """Retain a safe repository-relative detail for static audit output."""
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True)
class ActiveCase:
    """Describe one tracked active mysqltest source and host CI selection."""

    name: str
    source_path: Path
    result_path: Path
    ci_selected: bool


@dataclass(frozen=True)
class Provenance:
    """Identify the exact source location and recursive include stack."""

    source_path: str
    line: int
    include_stack: tuple[str, ...]


@dataclass(frozen=True)
class SqlStatement:
    """Retain one SQL statement, expected errors, and original provenance."""

    sql: str
    expected_errors: tuple[str, ...]
    provenance: Provenance
    query_logged: bool
    expected_output: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedMysqltestCase:
    """Hold one complete source classification without omitted semantics."""

    name: str
    source_path: str
    ci_selected: bool
    statements: tuple[SqlStatement, ...]
    included_sources: tuple[str, ...]
    directives: tuple[tuple[str, str, Provenance], ...]
    unsupported_reasons: tuple[str, ...]
    unsupported_details: tuple[str, ...]
    execution_class: str
    device_applicability: str
    device_case_ids: tuple[str, ...]


@dataclass(frozen=True)
class SourceClosureAudit:
    """Summarize recursive source coverage without treating missing files as absent."""

    file_count: int
    source_directive_count: int
    maximum_depth: int
    missing_sources: tuple[str, ...]


class _CorpusReader:
    """Read each contained regular corpus file once through a nofollow fd."""

    def __init__(self, root: Path):
        """Bind all reads to one resolved mysql_test directory."""
        self.root = Path(root).resolve()
        self._bytes: dict[Path, bytes] = {}

    def contained(self, path: Path) -> Path:
        """Normalize a lexical path without following a corpus entry symlink."""
        candidate = Path(os.path.abspath(path))
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as error:
            raise MysqltestParseError(
                "mysqltest source escapes mysql_test root") from error
        current = self.root
        for part in relative.parts:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                break
            if stat.S_ISLNK(metadata.st_mode):
                raise MysqltestParseError(
                    "mysqltest corpus entry must be regular and non-symlink")
        return self.root / relative

    def read_bytes(self, path: Path) -> bytes:
        """Return stable bounded bytes, rejecting type or identity changes."""
        candidate = self.contained(path)
        if candidate in self._bytes:
            return self._bytes[candidate]
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(candidate, flags)
        except OSError as error:
            raise MysqltestParseError(
                "mysqltest corpus entry must be regular and non-symlink") from error
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode)
                    or before.st_size > MAX_CORPUS_FILE_BYTES):
                raise MysqltestParseError(
                    "mysqltest corpus entry must be regular and bounded")
            chunks = []
            remaining = MAX_CORPUS_FILE_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(descriptor)
            identity = lambda value: (
                value.st_dev, value.st_ino, value.st_size,
                value.st_mtime_ns)
            if (len(content) > MAX_CORPUS_FILE_BYTES
                    or identity(before) != identity(after)
                    or len(content) != after.st_size):
                raise MysqltestParseError(
                    "mysqltest corpus entry changed during its bounded read")
        finally:
            os.close(descriptor)
        self._bytes[candidate] = content
        return content

    def read_text(self, path: Path) -> str:
        """Decode one cached corpus byte sequence as strict UTF-8."""
        try:
            return self.read_bytes(path).decode("utf-8")
        except UnicodeError as error:
            raise MysqltestParseError(
                "mysqltest source is unreadable") from error


def _load_host_discovery(repo_root: Path):
    """Load the established host runner so CI selection semantics stay shared."""
    script = repo_root / ".github/script/seekdb/mysqltest_for_seekdb.py"
    spec = importlib.util.spec_from_file_location(
        "seekdb_mysqltest_parser_discovery", script)
    if spec is None or spec.loader is None:
        raise MysqltestParseError("host mysqltest discovery is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _active_name(mysql_root: Path, source: Path) -> str:
    """Return the stable host case name for one active source path."""
    relative = source.relative_to(mysql_root)
    if relative.parts[0] == "t":
        return source.stem
    if (len(relative.parts) == 4 and relative.parts[0] == "test_suite"
            and relative.parts[2] == "t"):
        return f"{relative.parts[1]}.{source.stem}"
    raise MysqltestParseError("active mysqltest path has an unsupported layout")


def discover_active_cases(repo_root: Path) -> tuple[ActiveCase, ...]:
    """Discover every active file and mark the established 272-case CI set."""
    repo_root = Path(repo_root).resolve()
    mysql_root = repo_root / "tools/deploy/mysql_test"
    sources = list((mysql_root / "t").glob("*.test"))
    sources.extend((mysql_root / "test_suite").glob("*/t/*.test"))
    module = _load_host_discovery(repo_root)
    selected = {case.name for case in module.discover_cases(repo_root)}
    def active_case(source: Path) -> ActiveCase:
        """Build one source/result pair using the host runner layout."""
        name = _active_name(mysql_root, source)
        relative = source.relative_to(mysql_root)
        if relative.parts[0] == "t":
            result = mysql_root / "r/mysql" / f"{source.stem}.result"
        else:
            result = source.parent.parent / "r/mysql" / f"{source.stem}.result"
        return ActiveCase(name, source.absolute(), result.absolute(), name in selected)

    cases = tuple(sorted(
        (active_case(source) for source in sources if source.is_file()),
        key=lambda case: case.name))
    available = {case.name for case in cases}
    if selected - available:
        raise MysqltestParseError("host selection references an inactive case")
    return cases


def _reviewed_transcript_supported(
        case: ActiveCase, reader: Optional[_CorpusReader] = None) -> bool:
    """Bind a device adapter to the exact reviewed source and result bytes."""
    expected = DEVICE_TRANSCRIPT_DIGESTS.get(case.name)
    if expected is None:
        return False
    if reader is None:
        mysql_root = next(
            (parent for parent in Path(case.source_path).parents
             if parent.name == "mysql_test"), None)
        if mysql_root is None:
            return False
        reader = _CorpusReader(mysql_root)
    try:
        content = (reader.read_bytes(case.source_path) + b"\0"
                   + reader.read_bytes(case.result_path))
    except MysqltestParseError:
        return False
    return hashlib.sha256(content).hexdigest() == expected


class _StatementSplitter:
    """Split SQL on unquoted semicolons while retaining the first source line."""

    def __init__(self):
        """Initialize an empty scanner with no active quoted context."""
        self._characters: list[str] = []
        self._quote: Optional[str] = None
        self._escaped = False
        self.provenance: Optional[Provenance] = None

    def feed(
            self, text: str,
            provenance: Provenance) -> tuple[tuple[str, Provenance], ...]:
        """Consume one physical line and return complete SQL statements."""
        completed = []
        if self.provenance is None and text.strip():
            self.provenance = provenance
        for character in text:
            if self._escaped:
                self._characters.append(character)
                self._escaped = False
                continue
            if self._quote is not None and character == "\\":
                self._characters.append(character)
                self._escaped = True
                continue
            if character in ("'", '"', "`"):
                if self._quote is None:
                    self._quote = character
                elif self._quote == character:
                    self._quote = None
                self._characters.append(character)
                continue
            if character == ";" and self._quote is None:
                statement = "".join(self._characters).strip()
                statement_provenance = self.provenance or provenance
                self._characters.clear()
                if statement:
                    completed.append((statement, statement_provenance))
                self.provenance = None
                continue
            self._characters.append(character)
        self._characters.append("\n")
        return tuple(completed)

    def pending(self) -> str:
        """Return any unterminated SQL remaining after the final source line."""
        return "".join(self._characters).strip()

    def discard_pending(self) -> None:
        """Clear non-translatable parser state after recording an exact reason."""
        self._characters.clear()
        self._quote = None
        self._escaped = False
        self.provenance = None


class _ParseState:
    """Own mutable textual-include parsing state for one source case."""

    def __init__(
            self, repo_root: Path, mysql_root: Path,
            transcript_supported: bool = True,
            reader: Optional[_CorpusReader] = None):
        """Initialize shared directives, statements, and include bookkeeping."""
        self.repo_root = repo_root
        self.mysql_root = mysql_root
        self.reader = reader or _CorpusReader(mysql_root)
        self.splitter = _StatementSplitter()
        self.statements: list[SqlStatement] = []
        self.directives: list[tuple[str, str, Provenance]] = []
        self.included_sources: list[str] = []
        self.unsupported: set[str] = set()
        self.expected_errors: tuple[str, ...] = ()
        self.query_logged = True
        self.transcript_supported = transcript_supported
        if not transcript_supported:
            self.record_unsupported("device-transcript-unavailable")

    def relative(self, path: Path) -> str:
        """Return one repository-relative POSIX provenance path."""
        return path.relative_to(self.repo_root).as_posix()

    def resolve_source(self, current: Path, argument: str) -> Path:
        """Resolve one source argument while rejecting mysql_test path escape."""
        normalized = argument.strip().strip("'\"")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.startswith("mysql_test/"):
            candidate = self.mysql_root.parent / normalized
        else:
            local = current.parent / normalized
            candidate = local if local.exists() else self.mysql_root / normalized
        resolved = self.reader.contained(candidate)
        if not os.path.lexists(resolved):
            detail = candidate.relative_to(self.repo_root).as_posix()
            raise MysqltestParseError(
                "mysqltest source does not exist", detail=detail)
        return resolved

    def record_unsupported(self, reason: str) -> None:
        """Retain one stable unsupported semantic category."""
        self.unsupported.add(reason)


def _directive_reason(command: str) -> Optional[str]:
    """Map a non-translatable directive to one reviewed semantic category."""
    if command in RESULT_REWRITE_DIRECTIVES or command == "echo":
        return "result-rewrite"
    if command in PROCESS_SHELL_DIRECTIVES:
        return "process-shell"
    if command in CONTROL_DIRECTIVES:
        return "process-control"
    if command in ("connect", "connection", "disconnect"):
        return "connection"
    if command in ("shutdown_server", "restart_server", "start_server", "stop_server"):
        return "topology"
    if command in ("disable_abort_on_error", "enable_abort_on_error"):
        return "error-policy"
    if command in ("disable_parsing", "enable_parsing", "delimiter"):
        return "parser-control"
    return f"unsupported-directive:{command}"


def _parse_file(state: _ParseState, path: Path, stack: tuple[Path, ...]) -> None:
    """Parse one source recursively with textual include and cycle semantics."""
    resolved = state.reader.contained(path)
    if resolved in stack:
        raise MysqltestParseError("mysqltest source cycle detected")
    next_stack = (*stack, resolved)
    relative = state.relative(resolved)
    if stack and relative not in state.included_sources:
        state.included_sources.append(relative)
    try:
        lines = state.reader.read_text(resolved).splitlines()
    except MysqltestParseError:
        raise
    for line_number, line in enumerate(lines, 1):
        provenance = Provenance(
            relative, line_number,
            tuple(state.relative(item) for item in next_stack))
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        directive = DIRECTIVE.match(line)
        if directive is not None:
            if state.splitter.pending():
                state.record_unsupported("parser-control")
                state.splitter.discard_pending()
            command = directive.group(1).lower()
            argument = directive.group(2).strip()
            state.directives.append((command, argument, provenance))
            if command == "source":
                _parse_file(
                    state, state.resolve_source(resolved, argument), next_stack)
            elif command == "error":
                errors = tuple(
                    item.strip() for item in argument.split(",") if item.strip())
                if not errors:
                    raise MysqltestParseError("mysqltest error directive is empty")
                state.expected_errors = errors
            elif command == "disable_query_log":
                state.query_logged = False
            elif command == "enable_query_log":
                state.query_logged = True
            elif command not in PRESENTATION_DIRECTIVES:
                state.record_unsupported(_directive_reason(command))
            continue
        bare = BARE_COMMAND.match(line)
        bare_command = bare.group(1).lower() if bare is not None else ""
        if bare_command in BARE_CONNECTION_COMMANDS:
            state.record_unsupported("connection")
        elif bare_command in BARE_PROCESS_COMMANDS:
            state.record_unsupported("process-shell")
        elif bare_command in BARE_CONTROL_COMMANDS:
            state.record_unsupported("process-control")
        if not state.transcript_supported:
            if TOPOLOGY_SQL.match(line):
                state.record_unsupported("topology")
            continue
        completed = state.splitter.feed(line, provenance)
        for sql, sql_provenance in completed:
            if TOPOLOGY_SQL.match(sql):
                state.record_unsupported("topology")
            state.statements.append(SqlStatement(
                sql, state.expected_errors, sql_provenance,
                state.query_logged))
            state.expected_errors = ()


def _find_lines(
        lines: tuple[str, ...], marker: tuple[str, ...],
        start: int) -> Optional[int]:
    """Find one exact multi-line statement marker at or after an offset."""
    for index in range(start, len(lines) - len(marker) + 1):
        if lines[index:index + len(marker)] == marker:
            return index
    return None


def _attach_expected_results(
        statements: tuple[SqlStatement, ...],
        result_path: Path,
        reader: _CorpusReader) -> tuple[tuple[SqlStatement, ...], Optional[str]]:
    """Bind every query-logged statement to its exact mysqltest output lines."""
    try:
        result_lines = tuple(reader.read_text(result_path).splitlines())
    except MysqltestParseError:
        raise
    logged = [statement for statement in statements if statement.query_logged]
    positions = []
    cursor = 0
    for statement in logged:
        marker_lines = statement.sql.splitlines()
        marker = tuple((*marker_lines[:-1], f"{marker_lines[-1]};"))
        position = _find_lines(result_lines, marker, cursor)
        if position is None:
            return statements, "result-mismatch"
        positions.append((position, len(marker)))
        cursor = position + len(marker)
    updated = []
    logged_index = 0
    for statement in statements:
        if not statement.query_logged:
            updated.append(statement)
            continue
        position, marker_length = positions[logged_index]
        output_begin = position + marker_length
        output_end = (positions[logged_index + 1][0]
                      if logged_index + 1 < len(positions)
                      else len(result_lines))
        updated.append(replace(
            statement,
            expected_output=result_lines[output_begin:output_end]))
        logged_index += 1
    return tuple(updated), None


def parse_test_file(
        repo_root: Path, source_path: Path, name: str,
        ci_selected: bool = False,
        result_path: Optional[Path] = None,
        transcript_supported: bool = True,
        reader: Optional[_CorpusReader] = None) -> ParsedMysqltestCase:
    """Parse and conservatively classify one active mysqltest source."""
    input_root = Path(os.path.abspath(repo_root))
    repo_root = input_root.resolve()
    mysql_root = (repo_root / "tools/deploy/mysql_test").resolve()
    source_input = Path(os.path.abspath(source_path))
    try:
        source_relative = source_input.relative_to(input_root)
    except ValueError as error:
        raise MysqltestParseError("active source escapes mysql_test root") from error
    source_path = repo_root / source_relative
    normalized_result = None
    if result_path is not None:
        result_input = Path(os.path.abspath(result_path))
        try:
            result_relative = result_input.relative_to(input_root)
        except ValueError as error:
            raise MysqltestParseError(
                "mysqltest result escapes mysql_test root") from error
        normalized_result = repo_root / result_relative
    reader = reader or _CorpusReader(mysql_root)
    state = _ParseState(
        repo_root, mysql_root, transcript_supported, reader=reader)
    _parse_file(state, source_path, ())
    if state.splitter.pending():
        state.record_unsupported("parser-control")
        state.splitter.discard_pending()
    if state.expected_errors:
        state.record_unsupported("error-policy")
    if not state.statements and transcript_supported:
        state.record_unsupported("empty-case")
    statements = tuple(state.statements)
    if normalized_result is None and not state.unsupported:
        state.record_unsupported("result-unavailable")
    elif normalized_result is not None and not state.unsupported:
        statements, result_issue = _attach_expected_results(
            statements, normalized_result, reader)
        if result_issue is not None:
            state.record_unsupported(result_issue)
    unsupported = tuple(sorted(state.unsupported))
    lossless = not unsupported
    case_ids = (f"ios.mysqltest.{name}",) if lossless else ()
    return ParsedMysqltestCase(
        name=name,
        source_path=state.relative(source_path),
        ci_selected=ci_selected,
        statements=statements,
        included_sources=tuple(state.included_sources),
        directives=tuple(state.directives),
        unsupported_reasons=unsupported,
        unsupported_details=(),
        execution_class="device-native" if lossless else "host-only",
        device_applicability="lossless" if lossless else "not-applicable",
        device_case_ids=case_ids,
    )


def _corpus_digest(
        repo_root: Path, reader: Optional[_CorpusReader] = None) -> str:
    """Hash every parser, source, include, SQL, and result byte that affects output."""
    repo_root = Path(repo_root).resolve()
    mysql_root = repo_root / "tools/deploy/mysql_test"
    reader = reader or _CorpusReader(mysql_root)
    repository_reader = _CorpusReader(repo_root)
    paths = [
        path for path in mysql_root.rglob("*")
        if path.suffix in {".test", ".inc", ".sql", ".result"}
    ]
    paths.extend((
        repo_root / ".github/script/seekdb/mysqltest_for_seekdb.py",
        repo_root / "tools/deploy/mysqltest_config.yaml",
        Path(__file__).resolve(),
    ))
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        content = (reader.read_bytes(path) if path.is_relative_to(mysql_root)
                   else repository_reader.read_bytes(path))
        digest.update(path.relative_to(repo_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).hexdigest().encode("ascii"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _classify_active_corpus(
        repo_root: Path, corpus_reader: _CorpusReader
        ) -> tuple[ParsedMysqltestCase, ...]:
    """Parse classifications from bytes retained by one corpus reader."""
    classified = []
    for case in discover_active_cases(repo_root):
        try:
            parsed = parse_test_file(
                repo_root, case.source_path, case.name, case.ci_selected,
                case.result_path,
                transcript_supported=_reviewed_transcript_supported(
                    case, corpus_reader),
                reader=corpus_reader)
        except MysqltestParseError as error:
            reason = "source-rejected"
            if "cycle" in str(error):
                reason = "source-cycle"
            elif "escape" in str(error) or "does not exist" in str(error):
                reason = "source-unavailable"
            parsed = ParsedMysqltestCase(
                name=case.name,
                source_path=case.source_path.relative_to(
                    Path(repo_root).resolve()).as_posix(),
                ci_selected=case.ci_selected,
                statements=(),
                included_sources=(),
                directives=(),
                unsupported_reasons=(reason,),
                unsupported_details=((error.detail,) if error.detail else ()),
                execution_class="host-only",
                device_applicability="not-applicable",
                device_case_ids=(),
            )
        classified.append(parsed)
    return tuple(classified)


def classify_active_corpus(repo_root: Path) -> tuple[ParsedMysqltestCase, ...]:
    """Return a complete deterministic classification of the active corpus."""
    resolved = Path(repo_root).resolve()
    reader = _CorpusReader(resolved / "tools/deploy/mysql_test")
    _corpus_digest(resolved, reader)
    return _classify_active_corpus(resolved, reader)


def device_cases(cases: Iterable[ParsedMysqltestCase]) -> tuple[ParsedMysqltestCase, ...]:
    """Filter only losslessly translatable cases in stable source-name order."""
    return tuple(case for case in cases if case.device_applicability == "lossless")


def audit_source_closure(repo_root: Path) -> SourceClosureAudit:
    """Count the active recursive include closure and retain missing references."""
    repo_root = Path(repo_root).resolve()
    mysql_root = (repo_root / "tools/deploy/mysql_test").resolve()
    reader = _CorpusReader(mysql_root)
    visited: set[Path] = set()
    missing: set[str] = set()
    source_count = 0
    maximum_depth = 0

    def visit(path: Path, depth: int, stack: tuple[Path, ...]) -> None:
        """Visit one source once while still counting every source directive."""
        nonlocal maximum_depth, source_count
        resolved = path.resolve()
        if resolved in stack or resolved in visited:
            return
        visited.add(resolved)
        maximum_depth = max(maximum_depth, depth)
        state = _ParseState(
            repo_root, mysql_root, transcript_supported=False, reader=reader)
        try:
            lines = reader.read_text(resolved).splitlines()
        except MysqltestParseError:
            missing.add(state.relative(resolved))
            return
        for line in lines:
            directive = DIRECTIVE.match(line)
            if directive is None or directive.group(1).lower() != "source":
                continue
            source_count += 1
            argument = directive.group(2).strip()
            try:
                child = state.resolve_source(resolved, argument)
            except MysqltestParseError as error:
                normalized = argument.strip().strip("'\"")
                missing.add(error.detail or normalized)
                continue
            visit(child, depth + 1, (*stack, resolved))

    for case in discover_active_cases(repo_root):
        visit(case.source_path, 0, ())
    return SourceClosureAudit(
        file_count=len(visited),
        source_directive_count=source_count,
        maximum_depth=maximum_depth,
        missing_sources=tuple(sorted(missing)),
    )
