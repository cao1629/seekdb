#!/usr/bin/env python3
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zlib


DESCRIPTION = (
    "Run the judge's golden-bytes scenarios (the MySQL wire, and the files "
    "SELECT ... INTO OUTFILE writes and LOAD DATA reads) against one seekdb "
    "binary and record them in the recording format of mysqltest_for_seekdb.py, "
    "so that two recordings can be compared with its compare subcommand."
)
FAMILY_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[4]
RUNNER_PATH = REPO_ROOT / ".github" / "script" / "seekdb" / "mysqltest_for_seekdb.py"
SDB_PATH = RUNNER_PATH.with_name("sdb.py")
DEPLOY_DIR = REPO_ROOT / "tools" / "deploy"
CLIENT_PATH = FAMILY_DIR / "wire_client.py"
RECORDER_NAME = "migration/judge/families/wire/wire_scenarios.py"
HOST = "127.0.0.1"
DATABASE = b"test"
ADMIN_USER = b"admin"
ADMIN_PASSWORD = b"admin"
CONNECT_TIMEOUT = 30
RESPONSE_TIMEOUT = 120
CLOSE_TIMEOUT = 10
PROBE_TIMEOUT = 30
GLOBAL_WAIT_TIMEOUT = 120
GLOBAL_WAIT_INTERVAL = 0.5
TOOL_TIMEOUT = 120
HEX_LIMIT = 65536
DIGEST_EDGE = 64
DUMP_WIDTH = 32
SUMMARY_LIMIT = 120
REQUEST_LIMIT = 300
DEFAULT_FILE_DIR = "/tmp/seekdb-judge-wire-files"
OPEN_CURSORS_DEFAULT = 50
FILE_TABLE_ROWS = 300
FILE_INSERT_BATCH = 100
LOAD_ROWS = 60
LARGE_TEXT_LENGTHS = (16777210, 16777211, 16777212, 16777216)
RAISED_MAX_ALLOWED_PACKET = 67108864
THREE_PACKET_TEXT_LENGTH = 33554421
BINARY_ONE_CHUNK_LENGTH = 16777209
OVERSIZED_QUERY_FILL = 16777216 + 64
KILL_UNKNOWN_ID = 2000000000
REFRESH_GRANT = 0x01
LOAD_GZIP_HEX = (
    "1f8b08000000000000034dd4496edb401085e13d4f61f4ba5de8aea1874be404dad0b60209b1e549"
    "19ae9473e462a9a2ed2a02daa89f44e2ff40a9e6b43ebe9cd6942ba0e4f4f2b89e2f69c19ceeded6"
    "5fcff9e6f7f97abab97f7e7ad28fdc1248c9876f0be5f4faf3f97abc39a4f3e5fdfc703ca4940b94"
    "92d39fb4b07e79bdff7138bc3faeef27bd72d1a9e6745def0ed7d3f1ed9816bdd3fdfafddfdf9471"
    "fbd6f1f2909696b7ab547d9f96aef7f9782d23a7cbfa74bc2d65e8e7198a1dd8dd6b5aa66f53b70e"
    "657e6e9816bbd0c7584bcaf471e56d241dab8f554782edc046d6117d441d1b6c07368a8ee423e938"
    "613bb0b1e9c83e72ca8cb01dd85874141f454781ede0b3a4361f9b8e03b683af94ee634f592a6c07"
    "5f290e54154818ead8a5b8505521e95067a4a00ba10ab5025822055d0855a811608d14742154a1d6"
    "003152d0855085da04a44841174215ea08c891822e842ad4657b26bf52d0855085fa006cbb141742"
    "151afa34f75d8a0ba10a0d061cbb141742151a1d70460ab910a9d02c402552c88548852601d54821"
    "1722159a0d0823855c8854684e208a14722162fbf52010470b391189ad022411436e44cdd601d476"
    "358e448a546b05eabb1c57a2612b038d5d8f33d1b4b503cd0862776275aa58804b14b14371b59580"
    "6b14b14b31dada80318ad8a9986c9dc01445ec566c5684c01c45ec566c5624c01245ec566c563480"
    "dbaec8add8acb802f75d915bb15931038f5d915bb15971079e51246e25662505a44491b8959895e8"
    "7f6e8d22712b312b69201845e25662563241288ac4adc4ac1a827014895b89593501912812b712b3"
    "6a03a4ed8adc4accaa5790be2b722b31abce206357e4566256bd83cc286a6ed5cc6a1468258afe03"
    "7c0b9529be060000"
)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_module("mysqltest_for_seekdb", RUNNER_PATH)
wire = load_module("wire_client", CLIENT_PATH)


class ScenarioFailed(Exception):
    pass


class Recording(object):
    def __init__(self):
        self.content = bytearray()
        self.problems = []

    def line(self, text):
        self.content += text.encode("utf-8") + b"\n"

    def check(self, description, holds):
        self.line("-- check: {}: {}".format(description, "yes" if holds else "no"))
        if not holds:
            self.problems.append("check failed: {}".format(description))
        return holds


def quoted(data, limit=SUMMARY_LIMIT):
    if data is None:
        return "NULL"
    shown = "".join(
        chr(byte)
        if 0x20 <= byte < 0x7F and byte not in (0x22, 0x5C)
        else "\\x{:02x}".format(byte)
        for byte in data[:limit]
    )
    text = '"{}"'.format(shown)
    if len(data) > limit:
        text += " ({} bytes)".format(len(data))
    return text


def names_of(value, table):
    names = [name for bit, name in table if value & bit]
    known = 0
    for bit, _ in table:
        known |= bit
    if value & ~known:
        names.append("0x{:x}".format(value & ~known))
    return "|".join(names) if names else "none"


def status_text(status):
    return "0x{:04x} {}".format(status, names_of(status, wire.STATUS_NAMES))


def normalized_masks(masks, length):
    spans = []
    for start, size, name in sorted(masks):
        begin = max(0, start)
        end = min(length, start + size)
        if spans and begin < spans[-1][1]:
            begin = spans[-1][1]
        if end > begin:
            spans.append((begin, end, name))
    return spans


def blanked(data, spans):
    copy = bytearray(data)
    for begin, end, _ in spans:
        copy[begin:end] = bytes(end - begin)
    return bytes(copy)


def body_text(data, masks=()):
    spans = normalized_masks(masks, len(data))
    if len(data) > HEX_LIMIT:
        clean = blanked(data, spans)
        text = "sha256={} head={} tail={}".format(
            hashlib.sha256(clean).hexdigest(),
            clean[:DIGEST_EDGE].hex(),
            clean[-DIGEST_EDGE:].hex(),
        )
        if spans:
            text += " masked={}".format(
                ",".join(
                    "{}@{}:{}".format(name, begin, end - begin) for begin, end, name in spans
                )
            )
        return text
    parts = []
    position = 0
    for begin, end, name in spans:
        parts.append(data[position:begin].hex())
        parts.append("{" + name + ":" + str(end - begin) + "}")
        position = end
    parts.append(data[position:].hex())
    return "".join(parts)


def piece_masks(packet):
    result = []
    position = 0
    for piece in packet.pieces:
        size = len(piece.payload)
        local = [
            (start - position, length, name)
            for start, length, name in packet.masks
            if start < position + size and start + length > position
        ]
        result.append((piece, local))
        position += size
    return result


def stream_masks(packets):
    spans = []
    for packet in packets:
        position = 0
        for piece in packet.pieces:
            size = len(piece.payload)
            for start, length, name in packet.masks:
                begin = max(start, position)
                end = min(start + length, position + size)
                if end > begin and piece.offset is not None:
                    base = piece.offset + wire.HEADER_SIZE - position
                    spans.append((base + begin, base + end, name))
            position += size
    return spans


def frame_line(frame, spans):
    start = frame.plain_offset
    end = start + frame.plain_length
    local = [
        (max(begin, start) - start, min(finish, end) - max(begin, start), name)
        for begin, finish, name in spans
        if begin < end and finish > start
    ]
    if frame.uncompressed_length and local:
        return "Z {} {{compressed_length}} {} {{deflated_body_with_{}}}".format(
            frame.seq, frame.uncompressed_length, local[0][2]
        )
    return "Z {} {} {} {}".format(
        frame.seq,
        frame.compressed_length,
        frame.uncompressed_length,
        body_text(frame.body, () if frame.uncompressed_length else local),
    )


def column_text(info):
    text = (
        "schema {}, table {}, org_table {}, name {}, org_name {}, charset {}, length {}, "
        "type {} {}, flags 0x{:04x} {}, decimals {}".format(
            quoted(info["schema"]),
            quoted(info["table"]),
            quoted(info["org_table"]),
            quoted(info["name"]),
            quoted(info["org_name"]),
            info["charset"],
            info["length"],
            info["type"],
            wire.TYPE_NAMES.get(info["type"], "unknown"),
            info["flags"],
            names_of(info["flags"], wire.FIELD_FLAG_NAMES),
            info["decimals"],
        )
    )
    if info["catalog"] != b"def":
        text += ", catalog {}".format(quoted(info["catalog"]))
    if info["fixed_tail"] != b"\x00\x00":
        text += ", filler {}".format(info["fixed_tail"].hex())
    if info["extra"]:
        text += ", extra {}".format(info["extra"].hex())
    return text


def binary_value_text(value):
    if value is None:
        return "NULL"
    if value[0] == "number":
        number = value[1]
        return repr(number) if isinstance(number, float) else str(number)
    if value[0] == "date":
        length = value[1]
        year, month, day, hour, minute, second, micro = value[2]
        text = "{:04d}-{:02d}-{:02d}".format(year, month, day)
        if length >= 7:
            text += " {:02d}:{:02d}:{:02d}".format(hour, minute, second)
        if length == 11:
            text += ".{:06d}".format(micro)
        return "{} (length {})".format(text, length)
    if value[0] == "time":
        length = value[1]
        negative, days, hour, minute, second, micro = value[2]
        text = "{}{}d {:02d}:{:02d}:{:02d}".format(
            "-" if negative else "", days, hour, minute, second
        )
        if length == 12:
            text += ".{:06d}".format(micro)
        return "{} (length {})".format(text, length)
    return quoted(value[1])


def packet_summary(packet):
    info = packet.info
    kind = packet.kind
    if kind == "handshake":
        if info.get("unparsed"):
            return "handshake that could not be parsed; everything after its first byte is masked"
        text = (
            "handshake: protocol {}, version {}, connection id {{connection_id}}, scramble "
            "{{scramble:{}}} then {}, filler {}, capabilities 0x{:08x} {}, charset {}, "
            "status {}, auth data length {}, reserved {}".format(
                info["protocol"],
                quoted(info["version"]),
                wire.SCRAMBLE_LENGTH,
                info["scramble_tail"].hex(),
                info["filler"],
                info["capabilities"],
                names_of(info["capabilities"], wire.CAPABILITY_NAMES),
                info["charset"],
                status_text(info["status"]),
                info["auth_data_length"],
                info["reserved"].hex(),
            )
        )
        if "plugin" in info:
            text += ", plugin {}".format(quoted(info["plugin"]))
        if info["trailing"]:
            text += ", trailing {}".format(info["trailing"].hex())
        return text
    if kind == "ok":
        text = "OK: affected rows {}, last insert id {}, status {}, warnings {}".format(
            info["affected_rows"],
            info["last_insert_id"],
            status_text(info["status"]),
            info["warnings"],
        )
        if info.get("info"):
            text += ", info {}".format(quoted(info["info"]))
        if "session_state" in info:
            text += ", session state {}".format(quoted(info["session_state"]))
        if info.get("trailing"):
            text += ", trailing {}".format(info["trailing"].hex())
        return text
    if kind == "err":
        return "ERR {} {}: {}".format(
            info.get("code"),
            quoted(info["sqlstate"]) if "sqlstate" in info else "(no sqlstate)",
            quoted(info.get("message"), 400),
        )
    if kind == "eof":
        if "status" not in info:
            return "EOF shorter than 5 bytes"
        return "EOF: warnings {}, status {}".format(info["warnings"], status_text(info["status"]))
    if kind == "column_count":
        return "column count {}".format(info["count"])
    if kind in ("column", "param"):
        return "{}: {}".format("column" if kind == "column" else "parameter", column_text(info))
    if kind == "row":
        if info.get("undecoded"):
            return "text row that could not be decoded"
        return "text row: {}".format(", ".join(quoted(value) for value in info["values"]))
    if kind == "binary_row":
        if info.get("undecoded"):
            return "binary row that could not be decoded"
        text = "binary row: null bitmap {}, values {}".format(
            info["bitmap"].hex(), ", ".join(binary_value_text(value) for value in info["values"])
        )
        if info["trailing"]:
            text += ", trailing {}".format(info["trailing"].hex())
        return text
    if kind == "prepare_ok":
        text = "prepare OK: statement id {}, columns {}, parameters {}, filler {}, warnings {}".format(
            info["statement_id"], info["columns"], info["params"], info["filler"], info["warnings"]
        )
        if info["trailing"]:
            text += ", trailing {}".format(info["trailing"].hex())
        return text
    if kind == "auth_switch":
        if info.get("plugin") is None:
            return "auth switch request without a plugin name"
        masked = min(wire.SCRAMBLE_LENGTH, len(info["data"]))
        return "auth switch request: plugin {}, data {{scramble:{}}} then {}".format(
            quoted(info["plugin"]), masked, info["data"][masked:].hex()
        )
    if kind == "auth_more_data":
        return "auth more data {}".format(info["data"].hex())
    if kind == "local_infile":
        return "LOCAL INFILE request for {}".format(quoted(info["file"]))
    if kind == "string":
        return "string {}".format(quoted(info["text"], 400))
    return "packet of unknown kind, first byte {}".format(
        "none" if not packet.payload else "0x{:02x}".format(packet.payload[0])
    )


def summary(packet):
    try:
        return packet_summary(packet)
    except (KeyError, TypeError, ValueError, IndexError):
        return "packet of kind {} that could not be summarized".format(packet.kind)


def expectation_met(expect, outcome):
    if expect is None:
        return True
    if expect == "err":
        return outcome.startswith("err:")
    return expect == outcome


class Scenario(object):
    def __init__(self, args, recording, name):
        self.args = args
        self.recording = recording
        self.name = name
        self.connections = 0
        self.requests = 0
        self.sessions = []
        self.scrambles = []
        self.connection_ids = []

    def connect(self):
        return self.args.connector()

    def session(self, **options):
        session = Session(self, **options)
        self.sessions.append(session)
        return session

    def close_all(self):
        for session in self.sessions:
            session.close()

    def check_connection_id(self):
        result = probe(self, "select connection_id()")
        self.recording.check(
            "a probe connection's SELECT CONNECTION_ID() equals the connection id in its "
            "greeting (the probe is not recorded)",
            result is not None and result[1] == str(result[0]).encode("ascii"),
        )

    def check_identities(self):
        count = len(self.scrambles)
        self.recording.check(
            "the {} greetings of this scenario carried {} different scrambles".format(count, count),
            len(set(self.scrambles)) == count,
        )
        self.recording.check(
            "the {} greetings of this scenario carried {} different connection ids".format(
                count, count
            ),
            len(set(self.connection_ids)) == count,
        )


class Session(object):
    def __init__(self, scenario, capabilities=None, charset=None, user=b"root", password=b"",
                 database=DATABASE, plugin=None, attributes=None, auth_response=None,
                 local_files=None):
        self.scenario = scenario
        self.recording = scenario.recording
        self.capabilities = (
            wire.DEFAULT_CAPABILITIES if capabilities is None else capabilities
        )
        self.charset = wire.DEFAULT_CHARSET if charset is None else charset
        self.user = user
        self.password = password
        self.database = database
        self.plugin = wire.NATIVE_PASSWORD_PLUGIN if plugin is None else plugin
        self.attributes = wire.DEFAULT_ATTRIBUTES if attributes is None else attributes
        self.auth_response = auth_response
        self.local_files = local_files
        self.conn = None
        self.number = None
        self.scramble = None
        self.cursor_columns = {}

    def describe(self):
        parts = ["user {}".format(quoted(self.user))]
        if self.password:
            parts.append("password {}".format(quoted(self.password)))
        if self.capabilities & wire.CLIENT_CONNECT_WITH_DB:
            parts.append("database {}".format(quoted(self.database or b"")))
        parts.append(
            "capabilities 0x{:08x} {}".format(
                self.capabilities, names_of(self.capabilities, wire.CAPABILITY_NAMES)
            )
        )
        parts.append("charset {}".format(self.charset))
        if self.capabilities & wire.CLIENT_PLUGIN_AUTH:
            parts.append("plugin {}".format(quoted(self.plugin)))
        if self.auth_response is not None:
            parts.append(
                "fixed auth response {}".format(self.auth_response.hex() or "(empty)")
            )
        return ", ".join(parts)

    def begin(self, description, expect):
        self.scenario.connections += 1
        self.number = self.scenario.connections
        self.recording.line(
            "-- connection {}: {} (expect {})".format(
                self.number, description, expect or "anything"
            )
        )
        try:
            sock = self.scenario.connect()
        except OSError as exc:
            self.recording.line(
                "-- connection {}: cannot connect ({})".format(
                    self.number, exc.__class__.__name__
                )
            )
            raise ScenarioFailed("connection {} could not connect".format(self.number))
        self.conn = wire.Connection(sock, RESPONSE_TIMEOUT)
        greeting, scramble = wire.read_greeting(self.conn)
        self.render(greeting)
        if scramble is None:
            raise ScenarioFailed("connection {}: no usable handshake".format(self.number))
        self.scramble = scramble
        self.scenario.scrambles.append(scramble)
        self.scenario.connection_ids.append(greeting.packets[0].info["connection_id"])
        self.recording.check(
            "connection {}: the scramble is {} characters from 33 to 126".format(
                self.number, wire.SCRAMBLE_LENGTH
            ),
            len(scramble) == wire.SCRAMBLE_LENGTH and all(33 <= byte <= 126 for byte in scramble),
        )
        return greeting.packets[0]

    def open(self, expect="ok"):
        handshake = self.begin(self.describe(), expect)
        auth = (
            self.auth_response
            if self.auth_response is not None
            else wire.native_password_response(self.password, self.scramble)
        )
        payload, (auth_offset, auth_length) = wire.handshake_response(
            self.capabilities,
            self.charset,
            self.user,
            auth,
            self.database,
            self.plugin,
            self.attributes,
        )
        masks = (
            [(auth_offset, auth_length, "auth_response")]
            if self.auth_response is None and auth_length
            else []
        )
        response = self.exchange(
            payload, (handshake.last_seq + 1) & 0xFF, wire.MODE_SINGLE, masks, continuation=True
        )
        response = self.follow_auth_switch(response, self.password)
        outcome = self.settle(response, expect, "connection {} login".format(self.number))
        if outcome == "ok" and self.capabilities & wire.CLIENT_COMPRESS:
            self.conn.enable_compression()
            self.recording.line(
                "-- connection {}: the compressed protocol starts here".format(self.number)
            )
        if outcome.startswith("err:"):
            self.expect_closed()
        return outcome

    def open_raw(self, payload, seq, expect, description):
        self.begin(description, expect)
        response = self.exchange(payload, seq, wire.MODE_SINGLE, continuation=True)
        outcome = self.settle(response, expect, "connection {} login".format(self.number))
        if outcome.startswith("err:"):
            self.expect_closed()
        else:
            self.close()
        return outcome

    def exchange(self, payload, seq, mode, masks=(), continuation=False, columns=None):
        try:
            sent = self.conn.send(payload, seq=seq, continuation=continuation)
        except wire.ProtocolError as exc:
            self.recording.line(
                "-- connection {}: the request could not be sent ({})".format(self.number, exc)
            )
            raise ScenarioFailed("connection {}: sending failed".format(self.number))
        self.render_sent(sent, masks)
        response = wire.read_response(
            self.conn, mode, self.capabilities, columns, self.local_files
        )
        self.render(response)
        return response

    def render_sent(self, sent, masks):
        position = 0
        for piece in sent.pieces:
            size = len(piece.payload)
            local = [(start - position, length, name) for start, length, name in masks]
            self.recording.line(
                "C {} {} {}".format(piece.seq, size, body_text(piece.payload, local))
            )
            position += size
        for frame in sent.frames:
            self.recording.line(
                "-- client frame: compressed seq {}, {} bytes before compression, {}".format(
                    frame.seq,
                    frame.plain_length,
                    "deflated by the client" if frame.deflated else "sent uncompressed",
                )
            )

    def render(self, response):
        spans = stream_masks(response.packets)
        for frame in response.frames:
            self.recording.line(frame_line(frame, spans))
        for packet in response.packets:
            for piece, local in piece_masks(packet):
                self.recording.line(
                    "S {} {} {}".format(piece.seq, len(piece.payload), body_text(piece.payload, local))
                )
            self.recording.line(". " + summary(packet))
            if packet.reply is not None:
                self.render_sent(packet.reply, ())
        if response.closed:
            self.recording.line("-- connection {}: closed by the server".format(self.number))
        if response.timed_out:
            self.recording.line(
                "-- connection {}: no complete response within {} seconds".format(
                    self.number, RESPONSE_TIMEOUT
                )
            )
        if response.error is not None:
            self.recording.line(
                "-- connection {}: protocol error: {}".format(self.number, response.error)
            )

    def follow_auth_switch(self, response, password, fixed_reply=None):
        if response.closed or not response.packets or response.packets[-1].kind != "auth_switch":
            return response
        switch = response.packets[-1]
        data = switch.info.get("data", b"")
        self.recording.check(
            "connection {}: the auth switch request carries the handshake scramble".format(
                self.number
            ),
            data[: wire.SCRAMBLE_LENGTH] == self.scramble,
        )
        reply = (
            fixed_reply
            if fixed_reply is not None
            else wire.native_password_response(password, data)
        )
        masks = [(0, len(reply), "auth_response")] if fixed_reply is None and reply else []
        return self.exchange(
            reply, (switch.last_seq + 1) & 0xFF, wire.MODE_SINGLE, masks, continuation=True
        )

    def settle(self, response, expect, what):
        outcome = response.outcome()
        if not expectation_met(expect, outcome):
            self.recording.check("{} ended as {}, expected {}".format(what, outcome, expect), False)
        if (
            response.timed_out
            or response.error is not None
            or (response.closed and expect != "closed")
        ):
            raise ScenarioFailed("{}: {}".format(what, outcome))
        return outcome

    def announce(self, description, expect):
        self.scenario.requests += 1
        number = self.scenario.requests
        self.recording.line(
            "-- request {} on connection {}: {} (expect {})".format(
                number, self.number, description, expect or "anything"
            )
        )
        return number

    def request(self, payload, mode, expect, description, masks=(), columns=None):
        number = self.announce(description, expect)
        response = self.exchange(payload, 0, mode, masks, columns=columns)
        self.settle(response, expect, "request {}".format(number))
        return response

    def query(self, sql, expect="ok"):
        data = sql.encode("utf-8")
        return self.request(
            wire.query_payload(data),
            wire.MODE_QUERY,
            expect,
            "COM_QUERY {}".format(quoted(data, REQUEST_LIMIT)),
        )

    def prepare(self, sql, expect="prepare"):
        data = sql.encode("utf-8")
        response = self.request(
            wire.command(wire.COM_STMT_PREPARE, data),
            wire.MODE_PREPARE,
            expect,
            "COM_STMT_PREPARE {}".format(quoted(data, REQUEST_LIMIT)),
        )
        for packet in response.packets:
            if packet.kind == "prepare_ok":
                return packet.info["statement_id"]
        return None

    def execute(self, statement, params=(), expect="rows", flags=wire.CURSOR_TYPE_NO_CURSOR,
                send_types=True):
        params = list(params)
        description = "COM_STMT_EXECUTE statement {}, flags 0x{:02x}, {} parameters{}".format(
            statement,
            flags,
            len(params),
            "" if send_types or not params else ", types not sent again",
        )
        response = self.request(
            wire.execute_payload(statement, params, flags, send_types),
            wire.MODE_EXECUTE,
            expect,
            description,
        )
        if response.cursor:
            self.cursor_columns[statement] = response.columns
        return response

    def fetch(self, statement, rows, expect="rows"):
        return self.request(
            wire.fetch_payload(statement, rows),
            wire.MODE_FETCH,
            expect,
            "COM_STMT_FETCH statement {}, {} rows".format(statement, rows),
            columns=self.cursor_columns.get(statement, []),
        )

    def close_statement(self, statement):
        return self.request(
            wire.statement_payload(wire.COM_STMT_CLOSE, statement),
            wire.MODE_NONE,
            "none",
            "COM_STMT_CLOSE statement {}".format(statement),
        )

    def reset_statement(self, statement, expect="ok"):
        return self.request(
            wire.statement_payload(wire.COM_STMT_RESET, statement),
            wire.MODE_SINGLE,
            expect,
            "COM_STMT_RESET statement {}".format(statement),
        )

    def long_data(self, statement, parameter, data):
        return self.request(
            wire.long_data_payload(statement, parameter, data),
            wire.MODE_NONE,
            "none",
            "COM_STMT_SEND_LONG_DATA statement {}, parameter {}, {}".format(
                statement, parameter, quoted(data)
            ),
        )

    def ping(self, expect="ok"):
        return self.request(wire.command(wire.COM_PING), wire.MODE_SINGLE, expect, "COM_PING")

    def init_db(self, database, expect="ok"):
        return self.request(
            wire.init_db_payload(database),
            wire.MODE_SINGLE,
            expect,
            "COM_INIT_DB {}".format(quoted(database)),
        )

    def field_list(self, table, wildcard=b"", expect="eof"):
        return self.request(
            wire.field_list_payload(table, wildcard),
            wire.MODE_FIELD_LIST,
            expect,
            "COM_FIELD_LIST table {}, wildcard {}".format(quoted(table), quoted(wildcard)),
        )

    def simple(self, payload, expect, description):
        return self.request(payload, wire.MODE_SINGLE, expect, description)

    def string_command(self, payload, expect, description):
        return self.request(payload, wire.MODE_STRING, expect, description)

    def change_user(self, user, password, database, expect="ok", initial_auth=b"",
                    switch_reply=None, derived_initial=False):
        payload, (auth_offset, auth_length) = wire.change_user_payload(
            user,
            initial_auth,
            database,
            self.charset,
            self.plugin,
            self.attributes,
            self.capabilities,
        )
        masks = [(auth_offset, auth_length, "auth_response")] if derived_initial and auth_length else []
        description = "COM_CHANGE_USER to {}, database {}, {} auth response".format(
            quoted(user),
            quoted(database or b""),
            "scramble-derived" if derived_initial else "fixed {}".format(initial_auth.hex() or "(empty)"),
        )
        if switch_reply is not None:
            description += ", then answers an auth switch with {}".format(switch_reply.hex())
        number = self.announce(description, expect)
        response = self.exchange(payload, 0, wire.MODE_SINGLE, masks)
        response = self.follow_auth_switch(response, password, switch_reply)
        outcome = self.settle(response, expect, "request {}".format(number))
        if outcome.startswith("err:"):
            self.expect_closed()
        return outcome

    def quit(self):
        self.request(wire.command(wire.COM_QUIT), wire.MODE_NONE, "none", "COM_QUIT")
        self.expect_closed()

    def expect_closed(self):
        state, packet = self.conn.wait_closed(CLOSE_TIMEOUT)
        if state == "closed":
            self.recording.line("-- connection {}: closed by the server".format(self.number))
        elif state == "open":
            self.recording.check(
                "connection {} was closed by the server within {} seconds".format(
                    self.number, CLOSE_TIMEOUT
                ),
                False,
            )
        else:
            response = wire.Response(wire.MODE_SINGLE)
            response.packets.append(packet)
            wire.classify_single(packet, self.capabilities)
            response.frames = self.conn.take_frames()
            self.render(response)
            self.recording.check(
                "connection {} sent nothing more before closing".format(self.number), False
            )
        self.close()

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def probe(scenario, sql):
    try:
        sock = scenario.connect()
    except OSError:
        return None
    conn = wire.Connection(sock, PROBE_TIMEOUT)
    try:
        greeting, scramble = wire.read_greeting(conn)
        if scramble is None:
            return None
        payload, _ = wire.handshake_response(
            wire.DEFAULT_CAPABILITIES,
            wire.DEFAULT_CHARSET,
            b"root",
            b"",
            DATABASE,
            wire.NATIVE_PASSWORD_PLUGIN,
            wire.DEFAULT_ATTRIBUTES,
        )
        conn.send(payload, seq=(greeting.packets[0].last_seq + 1) & 0xFF)
        login = wire.read_response(conn, wire.MODE_SINGLE, wire.DEFAULT_CAPABILITIES)
        if login.outcome() != "ok":
            return None
        conn.send(wire.query_payload(sql.encode("utf-8")), seq=0)
        response = wire.read_response(conn, wire.MODE_QUERY, wire.DEFAULT_CAPABILITIES)
        rows = [packet for packet in response.packets if packet.kind == "row"]
        if len(rows) != 1 or not rows[0].info.get("values"):
            return None
        conn.send(wire.command(wire.COM_QUIT), seq=0)
        return greeting.packets[0].info["connection_id"], rows[0].info["values"][0]
    except (wire.ProtocolError, OSError):
        return None
    finally:
        conn.close()


def wait_for_new_sessions(scenario, sql, expected):
    scenario.recording.line(
        "-- waiting until a new connection reads {} from {} (these probe connections are "
        "not recorded)".format(quoted(expected), quoted(sql.encode("utf-8")))
    )
    deadline = time.monotonic() + GLOBAL_WAIT_TIMEOUT
    while True:
        result = probe(scenario, sql)
        if result is not None and result[1] == expected:
            return True
        if time.monotonic() >= deadline:
            scenario.recording.check(
                "a new connection read the value within {} seconds".format(GLOBAL_WAIT_TIMEOUT),
                False,
            )
            raise ScenarioFailed("the global variable change did not reach new connections")
        time.sleep(GLOBAL_WAIT_INTERVAL)


TYPE_CASES = (
    ("tinyint", "tinyint", ("-128", "127", "0")),
    ("tinyint_unsigned", "tinyint unsigned", ("0", "255")),
    ("boolean", "boolean", ("true", "false")),
    ("smallint", "smallint", ("-32768", "32767")),
    ("smallint_unsigned", "smallint unsigned", ("65535",)),
    ("mediumint", "mediumint", ("-8388608", "8388607")),
    ("mediumint_unsigned", "mediumint unsigned", ("16777215",)),
    ("int", "int", ("-2147483648", "2147483647")),
    ("int_unsigned", "int unsigned", ("4294967295",)),
    ("int_zerofill", "int(6) zerofill", ("42",)),
    ("bigint", "bigint", ("-9223372036854775808", "9223372036854775807")),
    ("bigint_unsigned", "bigint unsigned", ("18446744073709551615",)),
    ("float", "float", ("-1.5", "3.4e38", "1.17549e-38", "0.1")),
    ("float_unsigned", "float unsigned", ("2.5",)),
    ("float_scale", "float(7,3)", ("1234.567",)),
    ("double", "double", ("-2.5", "1.7976931348623157e308", "2.2250738585072014e-308", "0.1")),
    ("double_unsigned", "double unsigned", ("0.25",)),
    ("double_scale", "double(12,4)", ("12345678.1234",)),
    ("decimal", "decimal(10,2)", ("-12345678.90", "0.01")),
    ("decimal_unsigned", "decimal(10,2) unsigned", ("99999999.99",)),
    (
        "decimal_wide",
        "decimal(65,30)",
        ("12345678901234567890123456789012345.123456789012345678901234567890",),
    ),
    ("decimal_integer", "decimal(18,0)", ("-999999999999999999",)),
    ("numeric", "numeric(5,1)", ("-9999.9",)),
    ("date", "date", ("'1000-01-01'", "'9999-12-31'", "'2024-02-29'", "'0000-00-00'")),
    (
        "datetime",
        "datetime",
        ("'1000-01-01 00:00:00'", "'9999-12-31 23:59:59'", "'0000-00-00 00:00:00'"),
    ),
    (
        "datetime6",
        "datetime(6)",
        (
            "'2024-02-29 12:34:56.789012'",
            "'2000-01-01 00:00:00.000001'",
            "'2024-02-29 12:34:56.000000'",
        ),
    ),
    ("timestamp", "timestamp null", ("'1970-01-02 00:00:00'", "'2038-01-19 03:14:07'")),
    ("timestamp3", "timestamp(3) null", ("'2024-02-29 12:34:56.789'",)),
    ("time", "time", ("'-838:59:59'", "'838:59:59'", "'00:00:00'")),
    ("time6", "time(6)", ("'-12:34:56.000001'", "'23:59:59.999999'", "'01:02:03.000000'")),
    ("year", "year", ("1901", "2155", "0")),
    ("char", "char(10)", ("'abc'", "''")),
    ("varchar", "varchar(20)", ("'hello world'", "'O''Brien'", "'tab\\there'")),
    ("varchar_utf8mb4", "varchar(10)", ("'caf\u00e9 \u20ac \U0001d11e'",)),
    ("varchar_latin1", "varchar(10) character set latin1", ("'caf\u00e9'",)),
    ("varchar_gbk", "varchar(10) character set gbk", ("'\u4e2d\u6587'",)),
    ("nchar", "nchar(5)", ("'ab'",)),
    ("nvarchar", "nvarchar(5)", ("'xyz'",)),
    ("binary", "binary(4)", ("x'00ff10'", "'ab'")),
    ("varbinary", "varbinary(8)", ("x'deadbeef'", "''")),
    ("tinytext", "tinytext", ("'tiny text'",)),
    ("text", "text", ("'text value'",)),
    ("mediumtext", "mediumtext", ("'medium text'",)),
    ("longtext", "longtext", ("repeat('long ', 100)",)),
    ("tinyblob", "tinyblob", ("x'000102'",)),
    ("blob", "blob", ("x'fffefd'",)),
    ("mediumblob", "mediumblob", ("x'0a0b0c'",)),
    ("longblob", "longblob", ("x'cafebabe'",)),
    ("bit1", "bit(1)", ("b'1'", "b'0'")),
    ("bit12", "bit(12)", ("b'101010101010'",)),
    ("bit64", "bit(64)", ("x'ffffffffffffffff'",)),
    ("enum", "enum('a','b','c')", ("'a'", "'c'")),
    ("set", "set('x','y','z')", ("'x,z'", "''")),
    ("json", "json", ("'{\"a\": 1, \"b\": [true, null, \"s\"]}'", "'[]'", "'\"str\"'")),
    ("geometry", "geometry", ("st_geomfromtext('POINT(1 2)')",)),
    ("point", "point", ("st_geomfromtext('POINT(1.5 -2.5)')",)),
    ("linestring", "linestring", ("st_geomfromtext('LINESTRING(0 0,1 1,2 2)')",)),
    ("polygon", "polygon", ("st_geomfromtext('POLYGON((0 0,4 0,4 4,0 4,0 0))')",)),
    ("multipoint", "multipoint", ("st_geomfromtext('MULTIPOINT((0 0),(1 1))')",)),
    (
        "multilinestring",
        "multilinestring",
        ("st_geomfromtext('MULTILINESTRING((0 0,1 1),(2 2,3 3))')",),
    ),
    (
        "multipolygon",
        "multipolygon",
        ("st_geomfromtext('MULTIPOLYGON(((0 0,1 0,1 1,0 1,0 0)))')",),
    ),
    (
        "geometrycollection",
        "geometrycollection",
        ("st_geomfromtext('GEOMETRYCOLLECTION(POINT(1 1),LINESTRING(0 0,1 1))')",),
    ),
    ("vector", "vector(3)", ("'[1,2,3]'", "'[0.5,-1.25,0.003]'")),
    ("array_int", "array(int)", ("'[1,2,3]'", "'[]'")),
    ("array_varchar", "array(varchar(10))", ("'[\"a\",\"b\"]'",)),
    ("int_brackets", "int[]", ("'[4,5]'",)),
    ("map", "map(int, int)", ("map(1, 10, 2, 20)",)),
    ("sparsevector", "sparsevector", ("'{1:0.5,3:1.5}'",)),
)
EXPRESSION_SELECTS = (
    "select null, 1, -1, 18446744073709551615, -9223372036854775808, 1.5, -0.001, "
    "12345678901234567890, 1e10, 1.5e-7, 'abc', '', x'4142', 0x4142, b'1010'",
    "select date '2024-02-29', time '12:34:56.789', timestamp '2024-02-29 12:34:56.123456', "
    "cast('2024-02-29' as date), cast('2024-02-29 01:02:03.5' as datetime(1)), "
    "cast('-01:02:03' as time)",
    "select cast(1 as unsigned), cast(-1 as signed), cast(1.25 as decimal(5,3)), "
    "cast(1 as char), cast('abc' as binary), convert('abc' using latin1), _binary'abc', 1 = 1",
    "select cast('{\"a\": [1, 2.5, \"x\", null, true]}' as json), json_object('k', 1), "
    "json_array(1, 'a')",
    "select st_geomfromtext('POINT(1 2)'), st_astext(st_geomfromtext('POINT(1 2)'))",
)


COMPRESSED_TYPE_CASES = (
    "int",
    "double",
    "decimal_wide",
    "datetime6",
    "time6",
    "varchar_utf8mb4",
    "bit64",
    "json",
    "geometry",
)


def create_type_tables(session, names=None):
    for name, column_type, values in TYPE_CASES:
        if names is not None and name not in names:
            continue
        session.query("create table t_{} (id int primary key, v {})".format(name, column_type))
        rows = ["({}, {})".format(index + 1, value) for index, value in enumerate(values)]
        rows.append("({}, null)".format(len(values) + 1))
        session.query("insert into t_{} values {}".format(name, ", ".join(rows)))


def scenario_wire_handshake(scenario):
    session = scenario.session()
    session.open()
    session.quit()

    session = scenario.session(
        capabilities=wire.DEFAULT_CAPABILITIES & ~wire.CLIENT_CONNECT_WITH_DB, database=None
    )
    session.open()
    session.query("select database()", "rows")
    session.quit()

    session = scenario.session(capabilities=wire.MINIMAL_CAPABILITIES)
    session.open()
    session.query("select 1", "rows")
    session.query("select 1; select 2", None)
    session.quit()

    session = scenario.session(
        capabilities=wire.DEFAULT_CAPABILITIES | wire.CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA
    )
    session.open()
    session.quit()

    session = scenario.session(charset=63)
    session.open()
    session.query("select 'abc', x'ff', @@character_set_client, @@collation_connection", "rows")
    session.quit()

    session = scenario.session(charset=33)
    session.open()
    session.query("select 'abc', @@character_set_client, @@collation_connection", "rows")
    session.quit()

    scenario.session(user=b"nosuchuser").open("err:1045")
    scenario.session(database=b"nosuchdb").open("err:1049")
    scenario.session(auth_response=bytes(range(1, 21))).open("err:1045")

    session = scenario.session(plugin=wire.SHA2_PASSWORD_PLUGIN)
    session.open()
    session.quit()

    session = scenario.session(
        user=ADMIN_USER, password=ADMIN_PASSWORD, plugin=wire.SHA2_PASSWORD_PLUGIN, auth_response=b""
    )
    session.open()
    session.query("select current_user()", "rows")
    session.quit()

    session = scenario.session(user=ADMIN_USER, password=ADMIN_PASSWORD)
    session.open()
    session.query("select current_user()", "rows")
    session.quit()

    scenario.session(user=ADMIN_USER, auth_response=bytes(20)).open("err:1045")

    scenario.session().open_raw(
        wire.ssl_request(wire.DEFAULT_CAPABILITIES, wire.DEFAULT_CHARSET),
        1,
        "err:1043",
        "an SSL request although the handshake offers no TLS",
    )
    payload, _ = wire.handshake_response(
        wire.DEFAULT_CAPABILITIES,
        wire.DEFAULT_CHARSET,
        b"root",
        b"",
        DATABASE,
        wire.NATIVE_PASSWORD_PLUGIN,
        wire.DEFAULT_ATTRIBUTES,
    )
    scenario.session().open_raw(
        payload, 5, "closed", "a handshake response with sequence id 5 instead of 1"
    )
    scenario.session().open_raw(
        bytes(10), 1, "closed", "a handshake response of 10 zero bytes"
    )


def scenario_wire_ok_err_eof(scenario):
    session = scenario.session()
    session.open()
    query = session.query
    query("create table t_ok (id int primary key, v varchar(10), n int not null)")
    query("insert into t_ok values (1, 'a', 1), (2, 'b', 2), (3, 'c', 3)")
    query("update t_ok set n = n + 1 where id <= 2")
    query("update t_ok set n = n where id = 3")
    query("delete from t_ok where id = 3")
    query("insert into t_ok values (1, 'dup', 0)", "err:1062")
    query("select * from t_missing", "err:1146")
    query("selec 1", "err:1064")
    query("select nosuchcolumn from t_ok", "err:1054")
    query("insert into t_ok (id, v) values (9, 'x')", "err:1364")
    query("insert into t_ok values (10, 'x', null)", "err:1048")
    query("insert into t_ok values (11, 'waytoolongvalue', 1)", "err:1406")
    query("show warnings", "rows")
    query("insert ignore into t_ok values (1, 'dup', 0)")
    query("show warnings", "rows")
    query("insert ignore into t_ok values (12, 'waytoolongvalue', 1)")
    query("show warnings", "rows")
    query("select 1 / 0", None)
    query("show warnings", "rows")
    query("select cast('abc' as signed)", None)
    query("show warnings", "rows")
    query("select * from t_ok order by id", "rows")
    query("select id from t_ok where id < 0", "rows")
    query("begin")
    query("insert into t_ok values (20, 't', 20)")
    query("select count(*) from t_ok", "rows")
    query("commit")
    query("set autocommit = 0")
    query("insert into t_ok values (21, 'u', 21)")
    query("select count(*) from t_ok", "rows")
    query("rollback")
    query("set autocommit = 1")
    query("start transaction read only")
    query("select count(*) from t_ok", "rows")
    query("commit")
    query("set sql_mode = 'NO_BACKSLASH_ESCAPES'")
    query("select 'a\\b'", "rows")
    query("set sql_mode = default")
    query("use test")
    query("set @u = 5")
    query("select @u", "rows")
    query("do 1", None)
    query("create table t_ai (id int auto_increment primary key, v int)")
    query("insert into t_ai (v) values (10), (20)")
    query("insert into t_ai (v) values (30)")
    query("select last_insert_id()", "rows")
    query("insert into t_ai values (100, 40)")
    query("insert into t_ai (v) values (50)")
    query("select * from t_ai order by id", "rows")
    session.quit()

    session = scenario.session(
        capabilities=(wire.DEFAULT_CAPABILITIES & ~wire.CLIENT_SESSION_TRACK)
        | wire.CLIENT_FOUND_ROWS
    )
    session.open()
    session.query("update t_ok set n = n where id = 1")
    session.query("insert into t_ok values (1, 'dup', 0)", "err:1062")
    session.query("set autocommit = 0")
    session.query("insert into t_ok values (30, 'w', 30)")
    session.query("rollback")
    session.query("set autocommit = 1")
    session.query("use test")
    session.quit()


def scenario_wire_text_types(scenario):
    session = scenario.session()
    session.open()
    create_type_tables(session)
    for name, _, _ in TYPE_CASES:
        session.query("select id, v from t_{} order by id".format(name), "rows")
    for sql in EXPRESSION_SELECTS:
        session.query(sql, "rows")
    session.quit()


PARAMS_FIRST = (
    wire.param_int(-5, wire.MYSQL_TYPE_TINY),
    wire.param_int(-300, wire.MYSQL_TYPE_SHORT),
    wire.param_int(-70000, wire.MYSQL_TYPE_LONG),
    wire.param_int(-5000000000, wire.MYSQL_TYPE_LONGLONG),
    wire.param_int(18446744073709551615, wire.MYSQL_TYPE_LONGLONG, unsigned=True),
    wire.param_float(1.5),
    wire.param_double(-2.25),
    wire.param_bytes(b"123.45", wire.MYSQL_TYPE_NEWDECIMAL),
    wire.param_bytes("caf\u00e9".encode("utf-8")),
    wire.param_bytes(b"\x00\x01\xff", wire.MYSQL_TYPE_BLOB),
    wire.param_date(2024, 2, 29),
    wire.param_datetime(2024, 2, 29, 12, 34, 56, 789012),
    wire.param_time(True, 1, 2, 3, 4, 5),
    wire.param_null(),
)
PARAMS_SECOND = (
    wire.param_int(127, wire.MYSQL_TYPE_TINY),
    wire.param_int(32767, wire.MYSQL_TYPE_SHORT),
    wire.param_int(2147483647, wire.MYSQL_TYPE_LONG),
    wire.param_int(-1, wire.MYSQL_TYPE_LONGLONG),
    wire.param_int(0, wire.MYSQL_TYPE_LONGLONG, unsigned=True),
    wire.param_float(-0.25),
    wire.param_double(1e100),
    wire.param_bytes(b"-0.001", wire.MYSQL_TYPE_NEWDECIMAL),
    wire.param_bytes(b""),
    wire.param_bytes(b"", wire.MYSQL_TYPE_BLOB),
    wire.param_date(1000, 1, 1),
    wire.param_datetime(9999, 12, 31, 23, 59, 59),
    wire.param_time(False, 0, 0, 0, 0),
    wire.param_null(),
)
PARAMS_NULL = tuple(wire.param_null(param_type) for param_type, _, _ in PARAMS_FIRST)


def scenario_wire_binary_types(scenario):
    session = scenario.session()
    session.open()
    create_type_tables(session)
    for name, _, _ in TYPE_CASES:
        statement = session.prepare("select id, v from t_{} order by id".format(name))
        if statement is not None:
            session.execute(statement)
            session.close_statement(statement)
    for sql in EXPRESSION_SELECTS:
        statement = session.prepare(sql)
        if statement is not None:
            session.execute(statement)
            session.close_statement(statement)

    statement = session.prepare("select " + ", ".join("?" for _ in PARAMS_FIRST))
    if statement is not None:
        session.execute(statement, PARAMS_FIRST)
        session.execute(statement, PARAMS_SECOND, send_types=False)
        session.execute(statement, PARAMS_NULL)
        session.close_statement(statement)

    session.query("create table t_params (id int primary key, a varchar(20), b double, c datetime(6))")
    statement = session.prepare("insert into t_params values (?, ?, ?, ?)")
    if statement is not None:
        session.execute(
            statement,
            (
                wire.param_int(1, wire.MYSQL_TYPE_LONG),
                wire.param_bytes(b"one"),
                wire.param_double(1.0),
                wire.param_datetime(2024, 1, 1, 0, 0, 0, 1),
            ),
            "ok",
        )
        session.execute(
            statement,
            (
                wire.param_int(2, wire.MYSQL_TYPE_LONG),
                wire.param_null(wire.MYSQL_TYPE_VAR_STRING),
                wire.param_null(wire.MYSQL_TYPE_DOUBLE),
                wire.param_null(wire.MYSQL_TYPE_DATETIME),
            ),
            "ok",
        )
        session.execute(
            statement,
            (
                wire.param_int(1, wire.MYSQL_TYPE_LONG),
                wire.param_bytes(b"again"),
                wire.param_double(2.0),
                wire.param_datetime(2024, 1, 2, 0, 0, 0),
            ),
            "err:1062",
        )
        session.close_statement(statement)
    session.query("select * from t_params order by id", "rows")
    statement = session.prepare("select * from t_params order by id")
    if statement is not None:
        session.execute(statement)
        session.close_statement(statement)

    statement = session.prepare("select ?")
    if statement is not None:
        session.long_data(statement, 0, b"long ")
        session.long_data(statement, 0, b"data")
        session.execute(statement, (wire.param_long_data(wire.MYSQL_TYPE_BLOB),))
        session.close_statement(statement)

    statement = session.prepare("select id, v from t_int order by id")
    if statement is not None:
        session.execute(statement, (), "cursor", flags=wire.CURSOR_TYPE_READ_ONLY)
        session.fetch(statement, 1)
        session.fetch(statement, 10)
        session.fetch(statement, 1, None)
        session.reset_statement(statement)
        session.fetch(statement, 1, None)
        session.close_statement(statement)

    session.execute(999, (), "err")
    session.reset_statement(999, "err")
    session.close_statement(999)
    session.ping()
    session.prepare("selec 1", "err:1064")
    first = session.prepare("select 1")
    second = session.prepare("select 1")
    for statement in (first, second):
        if statement is not None:
            session.close_statement(statement)
    session.quit()

    session = scenario.session()
    session.open()
    scenario.recording.line(
        "-- this connection prepares {} statements, the default of open_cursors, and then "
        "one more".format(OPEN_CURSORS_DEFAULT)
    )
    statements = [session.prepare("select {}".format(index)) for index in range(1, OPEN_CURSORS_DEFAULT + 1)]
    session.prepare("select {}".format(OPEN_CURSORS_DEFAULT + 1), "err:5930")
    if statements[0] is not None:
        session.close_statement(statements[0])
        session.prepare("select {}".format(OPEN_CURSORS_DEFAULT + 1))
    session.quit()


def scenario_wire_multi_results(scenario):
    session = scenario.session()
    session.open()
    query = session.query
    query("select 1; select 'two', 2", "results:2")
    query(
        "create table t_multi (id int primary key, v int); "
        "insert into t_multi values (1, 10), (2, 20); "
        "update t_multi set v = v + 1 where id = 1; "
        "select * from t_multi order by id",
        "results:4",
    )
    query("select 1; select * from t_missing; select 3", "err:1146")
    query("select 1;", None)
    query(
        "begin; insert into t_multi values (3, 30); select count(*) from t_multi; commit",
        "results:4",
    )
    query("set @a = 1; select @a", "results:2")
    query("create procedure p_two() begin select 1 as a; select 2 as b, 'x' as c; end")
    query("call p_two()", "results:3")
    query("create procedure p_fail() begin select 1 as a; select * from t_missing; end")
    query("call p_fail()", "err:1146")
    query("create procedure p_out(out x int) begin set x = 42; end")
    query("call p_out(@x); select @x", "results:2")
    statement = session.prepare("call p_two()")
    if statement is not None:
        session.execute(statement, (), "results:3")
        session.close_statement(statement)
    statement = session.prepare("call p_out(?)")
    if statement is not None:
        response = session.execute(statement, (wire.param_null(wire.MYSQL_TYPE_LONG),), "rows")
        scenario.recording.check(
            "request {}: an EOF of the prepared CALL carries PS_OUT_PARAMS".format(
                scenario.requests
            ),
            any(
                packet.kind == "eof"
                and packet.info.get("status", 0) & wire.SERVER_PS_OUT_PARAMS
                for packet in response.packets
            ),
        )
        session.close_statement(statement)
    session.quit()

    session = scenario.session(
        capabilities=wire.DEFAULT_CAPABILITIES
        & ~(wire.CLIENT_MULTI_RESULTS | wire.CLIENT_PS_MULTI_RESULTS)
    )
    session.open()
    session.query("call p_two()", None)
    session.query("select 1; select 2", None)
    session.quit()


def oversized_query():
    return "select length('" + "x" * OVERSIZED_QUERY_FILL + "')"


def scenario_wire_large_packets(scenario):
    session = scenario.session()
    session.open()
    session.query("select @@max_allowed_packet", "rows")
    for length in LARGE_TEXT_LENGTHS:
        session.query("select repeat('a', {})".format(length), "rows")
    session.query("select repeat('a', {})".format(LARGE_TEXT_LENGTHS[-1] + 1), "err:1301")
    session.query("select repeat('a', 8388608), repeat('b', 8388608)", "rows")
    session.query(oversized_query(), "err:1153")
    session.query("set global max_allowed_packet = {}".format(RAISED_MAX_ALLOWED_PACKET))
    session.quit()

    wait_for_new_sessions(
        scenario, "select @@max_allowed_packet", str(RAISED_MAX_ALLOWED_PACKET).encode("ascii")
    )
    session = scenario.session()
    session.open()
    session.query("select @@max_allowed_packet", "rows")
    session.query("select repeat('a', {})".format(THREE_PACKET_TEXT_LENGTH), "rows")
    session.query(oversized_query(), "rows")
    statement = session.prepare("select repeat('a', {})".format(BINARY_ONE_CHUNK_LENGTH))
    if statement is not None:
        session.execute(statement)
        session.close_statement(statement)
    session.quit()


def scenario_wire_commands(scenario):
    session = scenario.session()
    session.open()
    session.ping()
    session.init_db(b"test")
    session.init_db(b"nosuchdb", "err:1049")
    session.init_db(b"information_schema")
    session.query("select database()", "rows")
    session.init_db(b"test")
    session.query(
        "create table t_fields (id int primary key, name varchar(20) not null default 'n/a', "
        "amount decimal(8,2) default 1.50, noted date, flag tinyint(1) default 1, body text)"
    )
    session.field_list(b"t_fields")
    session.field_list(b"t_fields", b"n%")
    session.field_list(b"t_missing", expect="err:1146")
    statement = session.prepare("select ?")
    if statement is not None:
        session.reset_statement(statement)
    session.reset_statement(999, "err")
    if statement is not None:
        session.close_statement(statement)
        session.ping()
    session.close_statement(999)
    session.ping()
    if statement is not None:
        session.execute(statement, (wire.param_int(1),), "err")
    session.simple(wire.command(wire.COM_RESET_CONNECTION), "ok", "COM_RESET_CONNECTION")
    session.query("select database(), @@autocommit", "rows")
    session.simple(
        wire.set_option_payload(1), None, "COM_SET_OPTION MYSQL_OPTION_MULTI_STATEMENTS_OFF"
    )
    session.query("select 1; select 2", None)
    session.simple(
        wire.set_option_payload(0), None, "COM_SET_OPTION MYSQL_OPTION_MULTI_STATEMENTS_ON"
    )
    session.simple(
        wire.command(wire.COM_TIME), "err:1235", "COM_TIME, a command the server does not implement"
    )
    session.string_command(wire.command(wire.COM_STATISTICS), "string", "COM_STATISTICS")
    session.simple(wire.command(wire.COM_DEBUG), "eof", "COM_DEBUG")
    session.simple(
        wire.refresh_payload(REFRESH_GRANT), "ok", "COM_REFRESH with REFRESH_GRANT"
    )
    session.simple(
        wire.process_kill_payload(KILL_UNKNOWN_ID),
        "err:1094",
        "COM_PROCESS_KILL of connection id {}, which no connection has".format(KILL_UNKNOWN_ID),
    )
    session.change_user(b"root", b"", b"test")
    session.query("select current_user(), database()", "rows")
    session.change_user(ADMIN_USER, ADMIN_PASSWORD, b"test")
    session.query("select current_user(), database()", "rows")
    session.change_user(ADMIN_USER, b"", b"test", "err:1045", switch_reply=bytes(20))

    session = scenario.session(capabilities=wire.DEFAULT_CAPABILITIES & ~wire.CLIENT_PLUGIN_AUTH)
    session.open()
    session.change_user(b"root", b"", b"test")
    session.query("select current_user()", "rows")
    session.change_user(
        ADMIN_USER,
        ADMIN_PASSWORD,
        b"test",
        initial_auth=wire.native_password_response(ADMIN_PASSWORD, session.scramble),
        derived_initial=True,
    )
    session.query("select current_user()", "rows")
    session.quit()


def digits_insert(table):
    return "insert into {} values {}".format(table, ", ".join("({})".format(digit) for digit in range(10)))


def scenario_wire_compressed(scenario):
    session = scenario.session(capabilities=wire.DEFAULT_CAPABILITIES | wire.CLIENT_COMPRESS)
    session.open()
    session.ping()
    session.query("select 1", "rows")
    session.query("select repeat('x', 200), 'y'", "rows")
    session.query("create table t_comp (id int primary key, v varchar(200))")
    session.query(
        "insert into t_comp values (1, repeat('a', 100)), (2, repeat('b', 100)), (3, null)"
    )
    session.query("select * from t_comp order by id", "rows")
    session.query("select 1; select 2", "results:2")
    session.query("select * from t_missing", "err:1146")
    statement = session.prepare("select id, v from t_comp order by id")
    if statement is not None:
        session.execute(statement)
        session.close_statement(statement)
    session.init_db(b"test")
    session.field_list(b"t_comp")
    session.query("create table t_digits (d int primary key)")
    session.query(digits_insert("t_digits"))
    session.query(
        "select a.d * 10 + b.d as n, repeat(char(65 + (a.d * 10 + b.d) % 26), 1000) as v "
        "from t_digits a, t_digits b order by n",
        "rows",
    )
    number = "a.d * 100 + b.d * 10 + c.d"
    session.query(
        "select {0} as n, md5({0}) as h from t_digits a, t_digits b, t_digits c "
        "where {0} < 200 order by n".format(number),
        "rows",
    )
    session.query(
        "select {0} as n, md5({0}) as h, sha2({0}, 256) as s "
        "from t_digits a, t_digits b, t_digits c order by n".format(number),
        "rows",
    )
    create_type_tables(session, COMPRESSED_TYPE_CASES)
    for name in COMPRESSED_TYPE_CASES:
        session.query("select id, v from t_{} order by id".format(name), "rows")
    session.query("select repeat('a', 16777216)", "rows")
    session.change_user(b"root", b"", b"test")
    session.query("select current_user()", "rows")
    session.quit()


OUTFILE_VARIANTS = (
    ("none", "NONE", ""),
    ("gzip", "GZIP", ".gz"),
    ("deflate", "DEFLATE", ".deflate"),
    ("zstd", "ZSTD", ".zst"),
)


def sql_text(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def file_table_rows():
    rows = []
    for index in range(1, FILE_TABLE_ROWS + 1):
        if index % 7 == 0:
            name = "null"
        elif index == 1:
            name = sql_text("tab\there")
        elif index == 2:
            name = sql_text("new\nline")
        elif index == 3:
            name = sql_text("back\\slash")
        elif index == 4:
            name = sql_text("comma, and 'quote'")
        elif index == 5:
            name = sql_text("caf\u00e9")
        else:
            name = sql_text("name-{:03d}".format(index))
        amount = "null" if index % 11 == 0 else "{}.{:02d}".format(index * 7 - 1000, index % 100)
        note = (
            "null"
            if index % 5 == 0
            else sql_text("note {} {}".format(index % 13, "x" * (index % 17)))
        )
        rows.append("({}, {}, {}, {})".format(index, name, amount, note))
    return rows


def outfile_statement(table, columns, path, compression, options=""):
    return (
        "select {} from {} order by id into outfile '{}' "
        "format = (type = 'csv', compression = '{}'){}".format(
            columns, table, path, compression, options
        )
    )


def run_tool(command):
    try:
        result = subprocess.run(
            [str(item) for item in command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TOOL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, b""
    return result.returncode, result.stdout


def dump_file(scenario, path):
    recording = scenario.recording
    try:
        data = path.read_bytes()
    except OSError:
        recording.check("file {} exists".format(path.name), False)
        return None
    recording.line("-- file {}: {} bytes".format(path.name, len(data)))
    if len(data) > HEX_LIMIT:
        recording.line("F " + body_text(data))
    else:
        for offset in range(0, len(data), DUMP_WIDTH):
            recording.line("F {:08x} {}".format(offset, data[offset : offset + DUMP_WIDTH].hex()))
    return data


def check_decompresses(scenario, tool, arguments, path, expected, expected_name):
    code, output = run_tool([tool] + list(arguments) + [path])
    holds = code == 0 and expected is not None and output == expected
    scenario.recording.check(
        "{} {} turns {} into the bytes of {}".format(
            Path(str(tool)).name, " ".join(arguments), path.name, expected_name
        ),
        holds,
    )
    if not holds:
        scenario.recording.line(
            "-- {} exit status {}, {} bytes of output".format(Path(str(tool)).name, code, len(output))
        )


def scenario_directory(scenario, name):
    directory = scenario.args.file_dir / name
    directory.mkdir()
    return directory


def keep_files(scenario, name):
    directory = scenario.args.file_dir / name
    if directory.exists():
        target = scenario.args.work_dir / "{}-files".format(name)
        shutil.move(str(directory), str(target))


def rows_of(response):
    return [
        packet.payload for packet in response.packets if packet.kind in ("row", "binary_row")
    ]


def scenario_file_outfile(scenario):
    try:
        run_file_outfile(scenario)
    finally:
        keep_files(scenario, "outfile")


def run_file_outfile(scenario):
    args = scenario.args
    directory = scenario_directory(scenario, "outfile")
    session = scenario.session()
    session.open()
    session.query(
        "create table t_file (id int primary key, name varchar(40), amount decimal(10,2), "
        "note varchar(100))"
    )
    rows = file_table_rows()
    for start in range(0, len(rows), FILE_INSERT_BATCH):
        session.query("insert into t_file values " + ", ".join(rows[start : start + FILE_INSERT_BATCH]))
    reference = rows_of(session.query("select * from t_file order by id", "rows"))

    exports = {}
    for label, compression, suffix in OUTFILE_VARIANTS:
        path = directory / "t_file_{}.csv{}".format(label, suffix)
        session.query(outfile_statement("t_file", "id, name, amount, note", path, compression))
        exports[label] = dump_file(scenario, path)
    plain_name = "t_file_none.csv"
    check_decompresses(scenario, args.gzip, ("-dc",), directory / "t_file_gzip.csv.gz", exports["none"], plain_name)
    check_decompresses(scenario, args.gzip, ("-dc",), directory / "t_file_deflate.csv.deflate", exports["none"], plain_name)
    check_decompresses(scenario, args.zstd, ("-dcq",), directory / "t_file_zstd.csv.zst", exports["none"], plain_name)

    session.query("create table t_digits (d int primary key)")
    session.query(digits_insert("t_digits"))
    session.query("create table t_file_big (id int primary key, h varchar(64))")
    session.query(
        "insert into t_file_big select a.d * 1000 + b.d * 100 + c.d * 10 + e.d, "
        "md5(a.d * 1000 + b.d * 100 + c.d * 10 + e.d) "
        "from t_digits a, t_digits b, t_digits c, t_digits e"
    )
    big_none = directory / "t_file_big_none.csv"
    session.query(outfile_statement("t_file_big", "id, h", big_none, "NONE"))
    big_plain = dump_file(scenario, big_none)
    for label, compression, suffix, options in (
        ("gzip", "GZIP", ".gz", ""),
        ("gzip_buffer4k", "GZIP", ".gz", " buffer_size = 4096"),
        ("zstd", "ZSTD", ".zst", ""),
        ("zstd_buffer4k", "ZSTD", ".zst", " buffer_size = 4096"),
    ):
        path = directory / "t_file_big_{}.csv{}".format(label, suffix)
        session.query(outfile_statement("t_file_big", "id, h", path, compression, options))
        dump_file(scenario, path)
        if compression == "GZIP":
            check_decompresses(scenario, args.gzip, ("-dc",), path, big_plain, big_none.name)
        else:
            check_decompresses(scenario, args.zstd, ("-dcq",), path, big_plain, big_none.name)

    session.query("create table t_file_empty (id int primary key)")
    for label, compression, suffix in OUTFILE_VARIANTS:
        path = directory / "t_file_empty_{}.csv{}".format(label, suffix)
        session.query(outfile_statement("t_file_empty", "id", path, compression))
        dump_file(scenario, path)

    session.query(outfile_statement("t_file", "id", directory / "t_file_none.csv", "NONE"), "err")
    session.query(outfile_statement("t_file", "id", directory / "missing" / "x.csv", "NONE"), "err")
    session.query(outfile_statement("t_file", "id", directory / "t_file_lz4.csv", "LZ4"), "err")

    session.query("create table t_file_copy like t_file")
    for label, _, suffix in OUTFILE_VARIANTS:
        name = "t_file_{}.csv{}".format(label, suffix)
        session.query("delete from t_file_copy")
        session.query(
            "load data infile '{}' into table t_file_copy compression = 'AUTO'".format(directory / name)
        )
        copied = rows_of(session.query("select * from t_file_copy order by id", "rows"))
        scenario.recording.check(
            "the rows loaded back from {} equal the rows of t_file".format(name), copied == reference
        )
    session.quit()


LOAD_FORMAT = (
    "fields terminated by ',' optionally enclosed by '\"' escaped by '\\\\' "
    "lines terminated by '\\n'"
)


def load_csv_bytes():
    lines = [
        b'1,"alpha",1.25,"plain"',
        b'2,"bravo, with comma",-3.50,\\N',
        b'3,"quote \\"inside\\"",0.00,"x"',
        b'4,"back\\\\slash",100.01,"tab\\there"',
        '5,"caf\u00e9",2.00,"end"'.encode("utf-8"),
        b'6,"",0.10,""',
        b"7,\\N,\\N,\\N",
    ]
    for index in range(8, LOAD_ROWS + 1):
        lines.append(
            '{},"name-{:03d}",{}.{:02d},"note {}"'.format(
                index, index, index * 3, index % 100, index % 7
            ).encode("ascii")
        )
    return b"\n".join(lines) + b"\n"


def load_statement(path, compression, table="t_load", local=False):
    clause = "" if compression is None else " compression = '{}'".format(compression)
    return "load data {}infile '{}' into table {}{} {}".format(
        "local " if local else "", path, table, clause, LOAD_FORMAT
    )


def note_input(scenario, path, how):
    data = path.read_bytes()
    scenario.recording.line(
        "-- input {}: {}; {} bytes, sha256 {}".format(
            path.name, how, len(data), hashlib.sha256(data).hexdigest()
        )
    )


def gzip_inflates_to(data, expected):
    try:
        return zlib.decompress(data, 31) == expected
    except zlib.error:
        return False


def write_tool_output(scenario, command, target):
    code, output = run_tool(command)
    if code != 0:
        scenario.recording.check("{} made {}".format(Path(str(command[0])).name, target.name), False)
        raise ScenarioFailed("{} failed with exit status {}".format(command[0], code))
    target.write_bytes(output)
    return output


def scenario_file_load_data(scenario):
    try:
        run_file_load_data(scenario)
    finally:
        keep_files(scenario, "load")


def run_file_load_data(scenario):
    args = scenario.args
    recording = scenario.recording
    directory = scenario_directory(scenario, "load")
    plain = load_csv_bytes()
    fixed_gzip = bytes.fromhex("".join(LOAD_GZIP_HEX))
    recording.check(
        "the script's {} fixed gzip bytes inflate to the bytes of in_plain.csv".format(
            len(fixed_gzip)
        ),
        gzip_inflates_to(fixed_gzip, plain),
    )
    plain_path = directory / "in_plain.csv"
    plain_path.write_bytes(plain)
    dump_file(scenario, plain_path)
    gzip_path = directory / "in_gzip.csv.gz"
    gzip_bytes = write_tool_output(scenario, [args.gzip, "-n", "-6", "-c", plain_path], gzip_path)
    note_input(scenario, gzip_path, "gzip -n -6 of in_plain.csv")
    zlib_path = directory / "in_zlib.csv.deflate"
    zlib_path.write_bytes(zlib.compress(plain, 6))
    note_input(scenario, zlib_path, "a zlib stream of in_plain.csv from Python's zlib at level 6")
    gzip_named_path = directory / "in_gzip_named.csv.deflate"
    gzip_named_path.write_bytes(gzip_bytes)
    note_input(scenario, gzip_named_path, "the bytes of in_gzip.csv.gz")
    zstd_path = directory / "in_zstd.csv.zst"
    zstd_bytes = write_tool_output(
        scenario, [args.zstd, "-q", "-3", "-T1", "-c", plain_path], zstd_path
    )
    note_input(scenario, zstd_path, "zstd -3 -T1 of in_plain.csv")
    zstd_long_path = directory / "in_zstd.csv.zstd"
    zstd_long_path.write_bytes(zstd_bytes)
    note_input(scenario, zstd_long_path, "the bytes of in_zstd.csv.zst")
    split = plain.index(b"\n", len(plain) // 2) + 1
    first_path = directory / "part1.csv"
    second_path = directory / "part2.csv"
    first_path.write_bytes(plain[:split])
    second_path.write_bytes(plain[split:])
    concat_path = directory / "in_concat.csv.gz"
    concat = write_tool_output(scenario, [args.gzip, "-n", "-6", "-c", first_path], directory / "part1.csv.gz")
    concat += write_tool_output(scenario, [args.gzip, "-n", "-6", "-c", second_path], directory / "part2.csv.gz")
    concat_path.write_bytes(concat)
    note_input(
        scenario,
        concat_path,
        "two gzip members from gzip -n -6, lines 1-{} of in_plain.csv and the rest".format(
            plain[:split].count(b"\n")
        ),
    )
    truncated_path = directory / "in_truncated.csv.gz"
    truncated_path.write_bytes(fixed_gzip[: len(fixed_gzip) // 2])
    note_input(scenario, truncated_path, "the first half of the script's fixed gzip bytes")

    session = scenario.session()
    session.open()
    session.query(
        "create table t_load (id int primary key, name varchar(40), amount decimal(10,2), "
        "note varchar(100))"
    )
    reference = None
    for path, compression in (
        (plain_path, "NONE"),
        (plain_path, None),
        (gzip_path, "GZIP"),
        (zlib_path, "DEFLATE"),
        (gzip_path, "DEFLATE"),
        (zstd_path, "ZSTD"),
        (gzip_path, "AUTO"),
        (zlib_path, "AUTO"),
        (gzip_named_path, "AUTO"),
        (zstd_path, "AUTO"),
        (zstd_long_path, "AUTO"),
        (plain_path, "AUTO"),
        (concat_path, "GZIP"),
    ):
        session.query("delete from t_load")
        session.query(load_statement(path, compression))
        rows = rows_of(session.query("select * from t_load order by id", "rows"))
        if reference is None:
            reference = rows
            recording.check(
                "{} rows were loaded from in_plain.csv".format(LOAD_ROWS), len(rows) == LOAD_ROWS
            )
        else:
            recording.check(
                "the rows loaded from {} with {} equal the rows loaded from in_plain.csv".format(
                    path.name, "no COMPRESSION clause" if compression is None else "COMPRESSION = " + compression
                ),
                rows == reference,
            )
    for number, (path, compression, expect) in enumerate(
        (
            (plain_path, "LZ4", "err"),
            (plain_path, "GZIP", "err"),
            (gzip_path, "ZSTD", "err"),
            (truncated_path, "GZIP", None),
        ),
        1,
    ):
        table = "t_load_error_{}".format(number)
        session.query("create table {} like t_load".format(table))
        session.query(load_statement(path, compression, table), expect)
    session.query(load_statement("in_plain.csv", None, local=True), "err:3948")
    session.quit()

    local_files = {
        b"in_plain.csv": plain,
        b"in_plain.csv.gz": fixed_gzip,
        b"in_empty.csv": b"",
    }
    for capabilities, cases in (
        (
            wire.DEFAULT_CAPABILITIES | wire.CLIENT_LOCAL_FILES,
            (("in_plain.csv", None), ("in_plain.csv.gz", "GZIP"), ("in_empty.csv", None)),
        ),
        (
            wire.DEFAULT_CAPABILITIES | wire.CLIENT_LOCAL_FILES | wire.CLIENT_COMPRESS,
            (("in_plain.csv", None),),
        ),
    ):
        session = scenario.session(capabilities=capabilities, local_files=local_files)
        session.open()
        for name, compression in cases:
            session.query("delete from t_load")
            session.query(load_statement(name, compression, local=True))
            rows = rows_of(session.query("select * from t_load order by id", "rows"))
            expected = [] if local_files[name.encode("ascii")] == b"" else reference
            recording.check(
                "the rows loaded from the client's {} with {} equal {}".format(
                    name,
                    "no COMPRESSION clause" if compression is None else "COMPRESSION = " + compression,
                    "no rows" if not expected else "the rows loaded from in_plain.csv",
                ),
                rows == expected,
            )
        session.quit()


SCENARIOS = (
    ("wire_handshake", scenario_wire_handshake),
    ("wire_ok_err_eof", scenario_wire_ok_err_eof),
    ("wire_text_types", scenario_wire_text_types),
    ("wire_binary_types", scenario_wire_binary_types),
    ("wire_multi_results", scenario_wire_multi_results),
    ("wire_large_packets", scenario_wire_large_packets),
    ("wire_commands", scenario_wire_commands),
    ("wire_compressed", scenario_wire_compressed),
    ("file_outfile", scenario_file_outfile),
    ("file_load_data", scenario_file_load_data),
)
SCENARIO_NAMES = tuple(name for name, _ in SCENARIOS)
FILE_SCENARIOS = ("file_outfile", "file_load_data")


def sdb(command, arguments, description):
    runner.run_sdb(SDB_PATH, command, arguments, description, REPO_ROOT)


def start_server(args):
    sdb(
        "start",
        ("--binary", args.seekdb, "--base-dir", args.base_dir, "--port", args.port, "--nodaemon"),
        "start seekdb",
    )


def wait_ready(args):
    sdb(
        "wait-ready",
        (
            "--client",
            args.obclient,
            "--base-dir",
            args.base_dir,
            "--host",
            HOST,
            "--port",
            args.port,
            "--user",
            "root",
            "--timeout",
            runner.READY_TIMEOUT,
        ),
        "wait for seekdb",
    )


def execute_init(args, scenario):
    runner.execute_init_sql(args, DEPLOY_DIR, scenario)


def instance_pid(args):
    sdb_module = runner.load_sdb_module(SDB_PATH)
    base_dir = sdb_module._base_dir(str(args.base_dir))
    try:
        binary = sdb_module.read_instance_binary(base_dir)
        return sdb_module.inspect_instance_process(base_dir, binary)
    except (OSError, RuntimeError, ValueError):
        return None


def server_running(args):
    return instance_pid(args) is not None


def save_outputs(args, name):
    runner.save_instance_outputs(args, SDB_PATH, name)


def destroy(args):
    return runner.destroy_instance(SDB_PATH, args.base_dir, REPO_ROOT, check=False)


def default_connector(args):
    return lambda: wire.connect_tcp(HOST, args.port, CONNECT_TIMEOUT)


def lifecycle_line(done, failed_step):
    steps = list(done)
    if failed_step is not None:
        steps.append("{} failed".format(failed_step))
    return "-- {}".format(", ".join(steps))


def bring_up(args, recording, scenario):
    done = []
    for step, action in (
        ("start", lambda: start_server(args)),
        ("ready", lambda: wait_ready(args)),
        ("init", lambda: execute_init(args, scenario)),
    ):
        try:
            action()
        except runner.RunnerError:
            recording.line(lifecycle_line(done, step))
            raise
        done.append(step)
    recording.line(lifecycle_line(done, None))


def write_recorded_file(path, content):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(str(temporary), str(path))


def run_scenario(args, name, function):
    recording = Recording()
    recording.line("-- {}".format(name))
    recording.line("-- recorder sha256 {}".format(args.recorder_sha256))
    recording.line("-- client sha256 {}".format(args.client_sha256))
    error = None
    print("[ RUN      ] {}".format(name), flush=True)
    started = time.monotonic()
    scenario = Scenario(args, recording, name)
    try:
        bring_up(args, recording, name)
        scenario.check_connection_id()
        function(scenario)
        scenario.check_identities()
        recording.check("seekdb is still running at the end of the scenario", server_running(args))
    except ScenarioFailed as exc:
        recording.problems.append(str(exc))
    except Exception as exc:
        error = "{}: {}".format(name, exc)
    finally:
        scenario.close_all()
        save_outputs(args, name)
        cleanup_error = destroy(args)
        if cleanup_error:
            error = "{}; {}".format(error, cleanup_error) if error else cleanup_error
    passed = error is None and not recording.problems
    suffix = ".result" if passed else ".partial"
    write_recorded_file(args.record_dir / (name + suffix), bytes(recording.content))
    problems = list(recording.problems)
    if error is not None:
        problems.append(error)
    elapsed = time.monotonic() - started
    if passed:
        print("[       OK ] {} ({:.3f}s)".format(name, elapsed), flush=True)
    else:
        print("[  FAILED  ] {} ({:.3f}s)".format(name, elapsed), flush=True)
        for problem in problems:
            print("  {}".format(problem), flush=True)
    outcome = {
        "exit_code": 0 if passed else 1,
        "recorded": passed,
        "partial": not passed,
        "problems": problems,
    }
    return outcome, error


def tool_version(tool):
    if tool is None:
        return None
    code, output = run_tool([tool, "--version"])
    if code != 0:
        return None
    lines = output.decode("utf-8", "replace").strip().splitlines()
    return lines[0] if lines else None


def write_manifest(args, scenarios):
    manifest_path = args.record_dir / "manifest.json"
    try:
        manifest_path.open("x").close()
    except FileExistsError:
        raise runner.RunnerError("record directory is already in use: {}".format(manifest_path))
    obclient_sha256 = runner.file_sha256(args.obclient)
    args.manifest = {
        "finished": False,
        "recorder": RECORDER_NAME,
        "seekdb": str(args.seekdb),
        "seekdb_sha256": runner.file_sha256(args.seekdb),
        "mysqltest": str(args.obclient),
        "mysqltest_sha256": obclient_sha256,
        "obclient": str(args.obclient),
        "obclient_sha256": obclient_sha256,
        "init_sql": str(args.init_sql),
        "init_sql_sha256": runner.file_sha256(args.init_sql),
        "init_user_sql": str(args.init_user_sql),
        "init_user_sql_sha256": runner.file_sha256(args.init_user_sql),
        "sdb_sha256": runner.file_sha256(SDB_PATH),
        "runner_sha256": args.recorder_sha256,
        "client_sha256": args.client_sha256,
        "mysqltest_runner_sha256": runner.file_sha256(RUNNER_PATH),
        "repo_head": runner.stripped_or_none(runner.git_output(REPO_ROOT, ["rev-parse", "HEAD"])),
        "tools_deploy_tree": runner.stripped_or_none(
            runner.git_output(REPO_ROOT, ["rev-parse", "HEAD:tools/deploy"])
        ),
        "tools_deploy_status": runner.git_output(
            REPO_ROOT, ["status", "--porcelain", "--", "tools/deploy"]
        ),
        "cases": list(scenarios),
        "slice_index": 0,
        "slice_count": 1,
        "case_list": None,
        "max_retries": 0,
        "fresh_instance_per_case": True,
        "work_dir": str(args.work_dir),
        "file_dir": str(args.file_dir),
        "gzip": str(args.gzip) if args.gzip else None,
        "gzip_version": tool_version(args.gzip),
        "gzip_sha256": runner.file_sha256(args.gzip) if args.gzip else None,
        "zstd": str(args.zstd) if args.zstd else None,
        "zstd_version": tool_version(args.zstd),
        "zstd_sha256": runner.file_sha256(args.zstd) if args.zstd else None,
        "python_zlib_version": zlib.ZLIB_RUNTIME_VERSION,
        "recorded": "<scenario>.result is the scenario's recording when it ran and every "
        "check and expectation held; otherwise <scenario>.partial is what it recorded up "
        "to the failure, and outcomes gives the exit code and the problems",
    }
    runner.write_json(manifest_path, args.manifest)


def save_dir_source(args):
    if args.save_instance_dir:
        return "--save-instance-dir"
    if os.environ.get(runner.INSTANCE_SAVE_ENVIRONMENT):
        return "${}".format(runner.INSTANCE_SAVE_ENVIRONMENT)
    return None


def command_run(args):
    save_source = save_dir_source(args)
    args.seekdb = runner.absolute_path(args.seekdb)
    args.obclient = runner.absolute_path(args.obclient)
    args.base_dir = runner.absolute_path(args.base_dir)
    args.record_dir = runner.absolute_path(args.record_dir)
    args.file_dir = runner.absolute_path(args.file_dir)
    args.save_instance_dir = runner.instance_save_dir(args)
    args.init_sql = runner.absolute_path(args.init_sql) if args.init_sql else DEPLOY_DIR / "init.sql"
    args.init_user_sql = (
        runner.absolute_path(args.init_user_sql)
        if args.init_user_sql
        else DEPLOY_DIR / "init_user.sql"
    )
    args.host = HOST
    args.manifest = None
    args.recorder_sha256 = runner.file_sha256(Path(__file__).resolve())
    args.client_sha256 = runner.file_sha256(CLIENT_PATH)
    args.work_dir = Path(tempfile.mkdtemp(prefix="wire-scenarios-"))
    args.connector = default_connector(args)
    args.entry_gate = {
        "init_sql": str(args.init_sql),
        "init_user_sql": str(args.init_user_sql),
        "attribution": "each file is piped to one obclient session; a statement's status "
        "is inferred from the 'ERROR ... at line N' lines on that session's stderr, N being "
        "the line where the client started the statement",
        "preparations": [],
    }
    requested = set(args.scenario or SCENARIO_NAMES)
    selected = [(name, function) for name, function in SCENARIOS if name in requested]
    print("work directory: {}".format(args.work_dir), flush=True)
    if args.save_instance_dir is not None:
        print(
            "instance save directory (from {}): {}".format(save_source, args.save_instance_dir),
            flush=True,
        )

    outcomes = {}
    failed_cases = []
    error = None
    created_file_dir = False
    try:
        args.record_dir.mkdir(parents=True, exist_ok=True)
        write_manifest(args, [name for name, _ in selected])
        if any(name in FILE_SCENARIOS for name, _ in selected):
            try:
                args.file_dir.mkdir()
            except FileExistsError:
                raise runner.RunnerError(
                    "file directory exists (another run, or left over): {}".format(args.file_dir)
                )
            created_file_dir = True
        for name, function in selected:
            if error is not None:
                outcomes[name] = {
                    "exit_code": None,
                    "recorded": False,
                    "partial": False,
                    "problems": ["not run after an earlier error"],
                }
                continue
            outcome, scenario_error = run_scenario(args, name, function)
            outcomes[name] = outcome
            if outcome["exit_code"] != 0:
                failed_cases.append(name)
            if scenario_error is not None:
                error = scenario_error
    except Exception as exc:
        error = "{}; {}".format(error, exc) if error else str(exc)
        print("[wire][ERROR] {}".format(exc), file=sys.stderr)
    finally:
        if created_file_dir:
            shutil.rmtree(str(args.file_dir), ignore_errors=True)

    success = not failed_cases and error is None and args.manifest is not None
    runner.write_entry_gate(args)
    if args.manifest is not None:
        args.manifest.update(
            {
                "success": success,
                "failed_cases": failed_cases,
                "retried_cases": {},
                "error": error,
                "init_failed_statements": runner.count_failed_init_statements(args.entry_gate),
                "outcomes": outcomes,
            }
        )
        args.manifest["finished"] = True
        try:
            runner.write_json(args.record_dir / "manifest.json", args.manifest)
        except OSError as exc:
            print("[wire][ERROR] cannot finish the record manifest: {}".format(exc), file=sys.stderr)
            success = False
    print(
        "wire scenarios finished: scenarios={}, failed={}, success={}".format(
            len(selected), len(failed_cases), success
        ),
        flush=True,
    )
    return 0 if success else 1


def create_parser():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--seekdb", required=True, help="seekdb executable")
    parser.add_argument(
        "--obclient",
        required=True,
        help="obclient executable, used only for sdb.py wait-ready and init",
    )
    parser.add_argument(
        "--base-dir",
        required=True,
        help="seekdb base directory, new or empty; destroyed with sdb.py destroy after each "
        "scenario",
    )
    parser.add_argument(
        "--record-dir",
        required=True,
        help="new or empty directory for manifest.json and one <scenario>.result (or "
        ".partial) per scenario",
    )
    parser.add_argument(
        "--port",
        type=runner.positive_int,
        required=True,
        help="the server's SQL port; required because judge runs share this machine",
    )
    parser.add_argument(
        "--init-sql",
        help="SQL file run in database oceanbase after each start; defaults to "
        "tools/deploy/init.sql",
    )
    parser.add_argument(
        "--init-user-sql",
        help="SQL file run in database test after --init-sql; defaults to "
        "tools/deploy/init_user.sql",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=SCENARIO_NAMES,
        help="run only this scenario; repeatable; scenarios always run in the order {}; "
        "default: all".format(", ".join(SCENARIO_NAMES)),
    )
    parser.add_argument(
        "--file-dir",
        default=DEFAULT_FILE_DIR,
        help="directory the file scenarios name in their SQL; it must not exist, is created "
        "for the run and removed at its end; two recordings compare cleanly only when both "
        "used the same value (default: {})".format(DEFAULT_FILE_DIR),
    )
    parser.add_argument("--gzip", help="gzip executable; defaults to gzip on the PATH")
    parser.add_argument("--zstd", help="zstd executable; defaults to zstd on the PATH")
    parser.add_argument(
        "--save-instance-dir",
        help="copy the instance's log/ (and seekdb*.profraw) here before every destroy, as "
        "the runner does; defaults to ${}".format(runner.INSTANCE_SAVE_ENVIRONMENT),
    )
    return parser


def require_new_or_empty(parser, path, what):
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        parser.error("{} must be new or empty: {}".format(what, path))


def resolve_tool(parser, value, name, needed):
    candidate = value or shutil.which(name)
    if candidate is None:
        if needed:
            parser.error("the file scenarios need {}; pass --{}".format(name, name))
        return None
    path = runner.absolute_path(candidate) if value else Path(candidate)
    if needed and not path.is_file():
        parser.error("--{} is not a file: {}".format(name, candidate))
    return path


def main(argv=None):
    parser = create_parser()
    args = parser.parse_args(argv)
    for path, what in ((args.seekdb, "--seekdb"), (args.obclient, "--obclient")):
        if not runner.absolute_path(path).is_file():
            parser.error("{} is not a file: {}".format(what, path))
    for path, what in ((args.init_sql, "--init-sql"), (args.init_user_sql, "--init-user-sql")):
        if path is not None and not runner.absolute_path(path).is_file():
            parser.error("{} is not a file: {}".format(what, path))
    require_new_or_empty(parser, runner.absolute_path(args.base_dir), "base directory")
    require_new_or_empty(parser, runner.absolute_path(args.record_dir), "record directory")
    requested = set(args.scenario or SCENARIO_NAMES)
    needs_files = any(name in requested for name in FILE_SCENARIOS)
    if needs_files and runner.absolute_path(args.file_dir).exists():
        parser.error(
            "--file-dir must not exist (another run, or left over): {}".format(args.file_dir)
        )
    args.gzip = resolve_tool(parser, args.gzip, "gzip", needs_files)
    args.zstd = resolve_tool(parser, args.zstd, "zstd", needs_files)
    save_dir = runner.instance_save_dir(args)
    if save_dir is not None:
        require_new_or_empty(
            parser, save_dir, "instance save directory (from {})".format(save_dir_source(args))
        )
    return command_run(args)


if __name__ == "__main__":
    sys.exit(main())
