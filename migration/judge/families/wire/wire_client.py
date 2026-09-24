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

import hashlib
import socket
import struct
import time
import zlib


HEADER_SIZE = 4
MAX_PAYLOAD = 0xFFFFFF
COMPRESS_HEADER_SIZE = 7
MIN_COMPRESS_LENGTH = 50
RECV_SIZE = 1 << 20
SCRAMBLE_LENGTH = 20
LOCAL_INFILE_CHUNK = 512

CLIENT_LONG_PASSWORD = 0x00000001
CLIENT_FOUND_ROWS = 0x00000002
CLIENT_LONG_FLAG = 0x00000004
CLIENT_CONNECT_WITH_DB = 0x00000008
CLIENT_NO_SCHEMA = 0x00000010
CLIENT_COMPRESS = 0x00000020
CLIENT_ODBC = 0x00000040
CLIENT_LOCAL_FILES = 0x00000080
CLIENT_IGNORE_SPACE = 0x00000100
CLIENT_PROTOCOL_41 = 0x00000200
CLIENT_INTERACTIVE = 0x00000400
CLIENT_SSL = 0x00000800
CLIENT_IGNORE_SIGPIPE = 0x00001000
CLIENT_TRANSACTIONS = 0x00002000
CLIENT_RESERVED = 0x00004000
CLIENT_SECURE_CONNECTION = 0x00008000
CLIENT_MULTI_STATEMENTS = 0x00010000
CLIENT_MULTI_RESULTS = 0x00020000
CLIENT_PS_MULTI_RESULTS = 0x00040000
CLIENT_PLUGIN_AUTH = 0x00080000
CLIENT_CONNECT_ATTRS = 0x00100000
CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA = 0x00200000
CLIENT_CAN_HANDLE_EXPIRED_PASSWORDS = 0x00400000
CLIENT_SESSION_TRACK = 0x00800000
CLIENT_DEPRECATE_EOF = 0x01000000

DEFAULT_CAPABILITIES = (
    CLIENT_LONG_PASSWORD
    | CLIENT_LONG_FLAG
    | CLIENT_CONNECT_WITH_DB
    | CLIENT_PROTOCOL_41
    | CLIENT_TRANSACTIONS
    | CLIENT_SECURE_CONNECTION
    | CLIENT_MULTI_STATEMENTS
    | CLIENT_MULTI_RESULTS
    | CLIENT_PS_MULTI_RESULTS
    | CLIENT_PLUGIN_AUTH
    | CLIENT_CONNECT_ATTRS
    | CLIENT_SESSION_TRACK
)
MINIMAL_CAPABILITIES = (
    CLIENT_LONG_PASSWORD
    | CLIENT_LONG_FLAG
    | CLIENT_CONNECT_WITH_DB
    | CLIENT_PROTOCOL_41
    | CLIENT_TRANSACTIONS
    | CLIENT_SECURE_CONNECTION
)
DEFAULT_CHARSET = 45
DEFAULT_MAX_PACKET = 16777216
DEFAULT_ATTRIBUTES = (
    (b"_client_name", b"seekdb-judge-wire"),
    (b"_client_version", b"1"),
)
NATIVE_PASSWORD_PLUGIN = b"mysql_native_password"
SHA2_PASSWORD_PLUGIN = b"caching_sha2_password"

CAPABILITY_NAMES = (
    (CLIENT_LONG_PASSWORD, "LONG_PASSWORD"),
    (CLIENT_FOUND_ROWS, "FOUND_ROWS"),
    (CLIENT_LONG_FLAG, "LONG_FLAG"),
    (CLIENT_CONNECT_WITH_DB, "CONNECT_WITH_DB"),
    (CLIENT_NO_SCHEMA, "NO_SCHEMA"),
    (CLIENT_COMPRESS, "COMPRESS"),
    (CLIENT_ODBC, "ODBC"),
    (CLIENT_LOCAL_FILES, "LOCAL_FILES"),
    (CLIENT_IGNORE_SPACE, "IGNORE_SPACE"),
    (CLIENT_PROTOCOL_41, "PROTOCOL_41"),
    (CLIENT_INTERACTIVE, "INTERACTIVE"),
    (CLIENT_SSL, "SSL"),
    (CLIENT_IGNORE_SIGPIPE, "IGNORE_SIGPIPE"),
    (CLIENT_TRANSACTIONS, "TRANSACTIONS"),
    (CLIENT_RESERVED, "RESERVED"),
    (CLIENT_SECURE_CONNECTION, "SECURE_CONNECTION"),
    (CLIENT_MULTI_STATEMENTS, "MULTI_STATEMENTS"),
    (CLIENT_MULTI_RESULTS, "MULTI_RESULTS"),
    (CLIENT_PS_MULTI_RESULTS, "PS_MULTI_RESULTS"),
    (CLIENT_PLUGIN_AUTH, "PLUGIN_AUTH"),
    (CLIENT_CONNECT_ATTRS, "CONNECT_ATTRS"),
    (CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA, "PLUGIN_AUTH_LENENC_CLIENT_DATA"),
    (CLIENT_CAN_HANDLE_EXPIRED_PASSWORDS, "CAN_HANDLE_EXPIRED_PASSWORDS"),
    (CLIENT_SESSION_TRACK, "SESSION_TRACK"),
    (CLIENT_DEPRECATE_EOF, "DEPRECATE_EOF"),
)

COM_QUIT = 0x01
COM_INIT_DB = 0x02
COM_QUERY = 0x03
COM_FIELD_LIST = 0x04
COM_REFRESH = 0x07
COM_STATISTICS = 0x09
COM_PROCESS_KILL = 0x0C
COM_DEBUG = 0x0D
COM_PING = 0x0E
COM_TIME = 0x0F
COM_CHANGE_USER = 0x11
COM_STMT_PREPARE = 0x16
COM_STMT_EXECUTE = 0x17
COM_STMT_SEND_LONG_DATA = 0x18
COM_STMT_CLOSE = 0x19
COM_STMT_RESET = 0x1A
COM_SET_OPTION = 0x1B
COM_STMT_FETCH = 0x1C
COM_RESET_CONNECTION = 0x1F

CURSOR_TYPE_NO_CURSOR = 0x00
CURSOR_TYPE_READ_ONLY = 0x01

SERVER_MORE_RESULTS_EXISTS = 0x0008
SERVER_STATUS_CURSOR_EXISTS = 0x0040
SERVER_PS_OUT_PARAMS = 0x1000
SERVER_SESSION_STATE_CHANGED = 0x4000
STATUS_NAMES = (
    (0x0001, "IN_TRANS"),
    (0x0002, "AUTOCOMMIT"),
    (0x0004, "RESERVED"),
    (0x0008, "MORE_RESULTS_EXISTS"),
    (0x0010, "NO_GOOD_INDEX_USED"),
    (0x0020, "NO_INDEX_USED"),
    (0x0040, "CURSOR_EXISTS"),
    (0x0080, "LAST_ROW_SENT"),
    (0x0100, "DB_DROPPED"),
    (0x0200, "NO_BACKSLASH_ESCAPES"),
    (0x0400, "METADATA_CHANGED"),
    (0x0800, "QUERY_WAS_SLOW"),
    (0x1000, "PS_OUT_PARAMS"),
    (0x2000, "IN_TRANS_READONLY"),
    (0x4000, "SESSION_STATE_CHANGED"),
)

FIELD_FLAG_NAMES = (
    (0x0001, "NOT_NULL"),
    (0x0002, "PRI_KEY"),
    (0x0004, "UNIQUE_KEY"),
    (0x0008, "MULTIPLE_KEY"),
    (0x0010, "BLOB"),
    (0x0020, "UNSIGNED"),
    (0x0040, "ZEROFILL"),
    (0x0080, "BINARY"),
    (0x0100, "ENUM"),
    (0x0200, "AUTO_INCREMENT"),
    (0x0400, "TIMESTAMP"),
    (0x0800, "SET"),
    (0x1000, "NO_DEFAULT_VALUE"),
    (0x2000, "ON_UPDATE_NOW"),
    (0x4000, "PART_KEY"),
    (0x8000, "NUM"),
)
UNSIGNED_FLAG = 0x0020

MYSQL_TYPE_TINY = 1
MYSQL_TYPE_SHORT = 2
MYSQL_TYPE_LONG = 3
MYSQL_TYPE_FLOAT = 4
MYSQL_TYPE_DOUBLE = 5
MYSQL_TYPE_NULL = 6
MYSQL_TYPE_TIMESTAMP = 7
MYSQL_TYPE_LONGLONG = 8
MYSQL_TYPE_INT24 = 9
MYSQL_TYPE_DATE = 10
MYSQL_TYPE_TIME = 11
MYSQL_TYPE_DATETIME = 12
MYSQL_TYPE_YEAR = 13
MYSQL_TYPE_NEWDECIMAL = 246
MYSQL_TYPE_BLOB = 252
MYSQL_TYPE_VAR_STRING = 253
TYPE_NAMES = {
    0: "DECIMAL",
    1: "TINY",
    2: "SHORT",
    3: "LONG",
    4: "FLOAT",
    5: "DOUBLE",
    6: "NULL",
    7: "TIMESTAMP",
    8: "LONGLONG",
    9: "INT24",
    10: "DATE",
    11: "TIME",
    12: "DATETIME",
    13: "YEAR",
    14: "NEWDATE",
    15: "VARCHAR",
    16: "BIT",
    160: "COMPLEX",
    161: "ARRAY",
    162: "STRUCT",
    200: "OB_TIMESTAMP_WITH_TIME_ZONE",
    201: "OB_TIMESTAMP_WITH_LOCAL_TIME_ZONE",
    202: "OB_TIMESTAMP_NANO",
    203: "OB_RAW",
    216: "OB_VECTOR",
    217: "OB_ARRAY",
    218: "OB_MAP",
    219: "OB_SPARSE_VECTOR",
    245: "JSON",
    246: "NEWDECIMAL",
    247: "ENUM",
    248: "SET",
    249: "TINY_BLOB",
    250: "MEDIUM_BLOB",
    251: "LONG_BLOB",
    252: "BLOB",
    253: "VAR_STRING",
    254: "STRING",
    255: "GEOMETRY",
}

MODE_QUERY = "query"
MODE_EXECUTE = "execute"
MODE_PREPARE = "prepare"
MODE_FETCH = "fetch"
MODE_FIELD_LIST = "field_list"
MODE_SINGLE = "single"
MODE_STRING = "string"
MODE_NONE = "none"


class ProtocolError(Exception):
    pass


class ConnectionClosed(ProtocolError):
    pass


class ResponseTimeout(ProtocolError):
    pass


class WirePacket(object):
    __slots__ = ("seq", "payload", "offset")

    def __init__(self, seq, payload, offset):
        self.seq = seq
        self.payload = payload
        self.offset = offset


class Frame(object):
    __slots__ = (
        "seq",
        "compressed_length",
        "uncompressed_length",
        "body",
        "plain_offset",
        "plain_length",
    )

    def __init__(self, seq, compressed_length, uncompressed_length, body,
                 plain_offset, plain_length):
        self.seq = seq
        self.compressed_length = compressed_length
        self.uncompressed_length = uncompressed_length
        self.body = body
        self.plain_offset = plain_offset
        self.plain_length = plain_length


class Packet(object):
    def __init__(self, pieces):
        self.pieces = list(pieces)
        self.payload = b"".join(piece.payload for piece in self.pieces)
        self.kind = "unknown"
        self.info = {}
        self.masks = []
        self.reply = None

    @property
    def last_seq(self):
        return self.pieces[-1].seq

    def first_byte(self):
        return self.payload[0] if self.payload else None

    def is_eof(self):
        return bool(self.payload) and self.payload[0] == 0xFE and len(self.payload) < 9

    def is_err(self):
        return bool(self.payload) and self.payload[0] == 0xFF

    def is_ok(self):
        return bool(self.payload) and self.payload[0] == 0x00


class ClientFrame(object):
    __slots__ = ("seq", "plain_length", "deflated")

    def __init__(self, seq, plain_length, deflated):
        self.seq = seq
        self.plain_length = plain_length
        self.deflated = deflated


class Sent(object):
    def __init__(self, pieces, frames):
        self.pieces = pieces
        self.frames = frames


class Response(object):
    def __init__(self, mode):
        self.mode = mode
        self.packets = []
        self.frames = []
        self.closed = False
        self.timed_out = False
        self.error = None
        self.columns = []
        self.cursor = False

    def outcome(self):
        if self.error is not None:
            return "protocol error"
        if self.timed_out:
            return "timeout"
        if self.mode == MODE_NONE:
            return "none"
        if not self.packets:
            return "closed" if self.closed else "no response"
        last = self.packets[-1]
        if last.kind == "err":
            return "err:{}".format(last.info.get("code"))
        if self.closed:
            return "closed"
        if self.mode in (MODE_QUERY, MODE_EXECUTE):
            if self.cursor:
                return "cursor"
            results = [packet for packet in self.packets if packet.kind in ("ok", "column_count")]
            if len(results) == 1:
                return "ok" if results[0].kind == "ok" else "rows"
            return "results:{}".format(len(results))
        if self.mode == MODE_PREPARE:
            return "prepare"
        if self.mode == MODE_FETCH:
            return "rows"
        if self.mode == MODE_FIELD_LIST:
            return "eof"
        return last.kind


def lenenc_int(value):
    if value < 251:
        return bytes([value])
    if value < 0x10000:
        return b"\xfc" + struct.pack("<H", value)
    if value < 0x1000000:
        return b"\xfd" + struct.pack("<I", value)[:3]
    return b"\xfe" + struct.pack("<Q", value)


def lenenc_bytes(data):
    return lenenc_int(len(data)) + data


def read_lenenc_int(data, pos):
    if pos >= len(data):
        raise ProtocolError("length-encoded integer past the end at {}".format(pos))
    first = data[pos]
    if first < 251:
        return first, pos + 1
    if first == 0xFB:
        return None, pos + 1
    if first == 0xFC:
        width = 2
    elif first == 0xFD:
        width = 3
    elif first == 0xFE:
        width = 8
    else:
        raise ProtocolError("invalid length-encoded integer 0xff at {}".format(pos))
    if pos + 1 + width > len(data):
        raise ProtocolError("truncated length-encoded integer at {}".format(pos))
    return int.from_bytes(data[pos + 1 : pos + 1 + width], "little"), pos + 1 + width


def read_lenenc_bytes(data, pos):
    length, pos = read_lenenc_int(data, pos)
    if length is None:
        return None, pos
    if pos + length > len(data):
        raise ProtocolError("truncated length-encoded string at {}".format(pos))
    return data[pos : pos + length], pos + length


def read_nul_string(data, pos):
    end = data.find(b"\x00", pos)
    if end < 0:
        raise ProtocolError("unterminated string at {}".format(pos))
    return data[pos:end], end + 1


def native_password_response(password, scramble):
    if not password:
        return b""
    stage1 = hashlib.sha1(password).digest()
    stage2 = hashlib.sha1(stage1).digest()
    mix = hashlib.sha1(scramble[:SCRAMBLE_LENGTH] + stage2).digest()
    return bytes(left ^ right for left, right in zip(stage1, mix))


def encode_attributes(attributes):
    body = b"".join(lenenc_bytes(key) + lenenc_bytes(value) for key, value in attributes)
    return lenenc_bytes(body)


def handshake_response(capabilities, charset, user, auth_response, database, plugin,
                       attributes, max_packet=DEFAULT_MAX_PACKET):
    payload = struct.pack("<IIB", capabilities, max_packet, charset) + b"\x00" * 23
    payload += user + b"\x00"
    auth_offset = None
    if capabilities & CLIENT_PLUGIN_AUTH_LENENC_CLIENT_DATA:
        prefix = lenenc_int(len(auth_response))
        auth_offset = len(payload) + len(prefix)
        payload += prefix + auth_response
    elif capabilities & CLIENT_SECURE_CONNECTION:
        auth_offset = len(payload) + 1
        payload += bytes([len(auth_response)]) + auth_response
    else:
        auth_offset = len(payload)
        payload += auth_response + b"\x00"
    if capabilities & CLIENT_CONNECT_WITH_DB:
        payload += (database or b"") + b"\x00"
    if capabilities & CLIENT_PLUGIN_AUTH:
        payload += (plugin or b"") + b"\x00"
    if capabilities & CLIENT_CONNECT_ATTRS:
        payload += encode_attributes(attributes)
    return payload, (auth_offset, len(auth_response))


def ssl_request(capabilities, charset, max_packet=DEFAULT_MAX_PACKET):
    return struct.pack("<IIB", capabilities | CLIENT_SSL, max_packet, charset) + b"\x00" * 23


def command(code, body=b""):
    return bytes([code]) + body


def query_payload(sql):
    return command(COM_QUERY, sql)


def init_db_payload(database):
    return command(COM_INIT_DB, database)


def field_list_payload(table, wildcard=b""):
    return command(COM_FIELD_LIST, table + b"\x00" + wildcard)


def statement_payload(code, statement_id):
    return command(code, struct.pack("<I", statement_id))


def fetch_payload(statement_id, rows):
    return command(COM_STMT_FETCH, struct.pack("<II", statement_id, rows))


def long_data_payload(statement_id, parameter, data):
    return command(COM_STMT_SEND_LONG_DATA, struct.pack("<IH", statement_id, parameter) + data)


def set_option_payload(option):
    return command(COM_SET_OPTION, struct.pack("<H", option))


def refresh_payload(sub_command):
    return command(COM_REFRESH, bytes([sub_command]))


def process_kill_payload(connection_id):
    return command(COM_PROCESS_KILL, struct.pack("<I", connection_id))


def change_user_payload(user, auth_response, database, charset, plugin, attributes,
                        capabilities):
    payload = command(COM_CHANGE_USER, user + b"\x00")
    if capabilities & CLIENT_SECURE_CONNECTION:
        auth_offset = len(payload) + 1
        payload += bytes([len(auth_response)]) + auth_response
    else:
        auth_offset = len(payload)
        payload += auth_response + b"\x00"
    payload += (database or b"") + b"\x00"
    payload += struct.pack("<H", charset)
    if capabilities & CLIENT_PLUGIN_AUTH:
        payload += (plugin or b"") + b"\x00"
    if capabilities & CLIENT_CONNECT_ATTRS:
        payload += encode_attributes(attributes)
    return payload, (auth_offset, len(auth_response))


def param_null(param_type=MYSQL_TYPE_NULL):
    return (param_type, False, None)


def param_int(value, param_type=MYSQL_TYPE_LONGLONG, unsigned=False):
    formats = {
        MYSQL_TYPE_TINY: "<B" if unsigned else "<b",
        MYSQL_TYPE_SHORT: "<H" if unsigned else "<h",
        MYSQL_TYPE_YEAR: "<H",
        MYSQL_TYPE_LONG: "<I" if unsigned else "<i",
        MYSQL_TYPE_INT24: "<I" if unsigned else "<i",
        MYSQL_TYPE_LONGLONG: "<Q" if unsigned else "<q",
    }
    return (param_type, unsigned, struct.pack(formats[param_type], value))


def param_float(value):
    return (MYSQL_TYPE_FLOAT, False, struct.pack("<f", value))


def param_double(value):
    return (MYSQL_TYPE_DOUBLE, False, struct.pack("<d", value))


def param_bytes(value, param_type=MYSQL_TYPE_VAR_STRING):
    return (param_type, False, lenenc_bytes(value))


def param_date(year, month, day):
    return (MYSQL_TYPE_DATE, False, bytes([4]) + struct.pack("<HBB", year, month, day))


def param_datetime(year, month, day, hour, minute, second, microsecond=0,
                   param_type=MYSQL_TYPE_DATETIME):
    if microsecond:
        body = struct.pack("<HBBBBBI", year, month, day, hour, minute, second, microsecond)
    else:
        body = struct.pack("<HBBBBB", year, month, day, hour, minute, second)
    return (param_type, False, bytes([len(body)]) + body)


def param_time(negative, days, hour, minute, second, microsecond=0):
    body = struct.pack("<BIBBB", 1 if negative else 0, days, hour, minute, second)
    if microsecond:
        body += struct.pack("<I", microsecond)
    return (MYSQL_TYPE_TIME, False, bytes([len(body)]) + body)


def param_long_data(param_type=MYSQL_TYPE_BLOB):
    return (param_type, False, b"")


def execute_payload(statement_id, params, flags=CURSOR_TYPE_NO_CURSOR,
                    send_types=True, iteration_count=1):
    payload = command(COM_STMT_EXECUTE, struct.pack("<IBI", statement_id, flags, iteration_count))
    if params:
        bitmap = bytearray((len(params) + 7) // 8)
        for index, (_, _, value) in enumerate(params):
            if value is None:
                bitmap[index // 8] |= 1 << (index % 8)
        payload += bytes(bitmap) + (b"\x01" if send_types else b"\x00")
        if send_types:
            for param_type, unsigned, _ in params:
                payload += struct.pack("<BB", param_type & 0xFF, 0x80 if unsigned else 0)
        for _, _, value in params:
            if value is not None:
                payload += value
    return payload


def split_payload(payload, first_seq):
    pieces = []
    seq = first_seq
    position = 0
    while True:
        chunk = payload[position : position + MAX_PAYLOAD]
        pieces.append((seq, chunk))
        seq = (seq + 1) & 0xFF
        position += len(chunk)
        if len(chunk) < MAX_PAYLOAD:
            return pieces


def packet_bytes(pieces):
    return b"".join(
        struct.pack("<I", len(chunk))[:3] + bytes([seq]) + chunk for seq, chunk in pieces
    )


def local_infile_pieces(content, first_seq):
    pieces = []
    seq = first_seq
    for position in range(0, len(content), LOCAL_INFILE_CHUNK):
        pieces.append((seq, content[position : position + LOCAL_INFILE_CHUNK]))
        seq = (seq + 1) & 0xFF
    pieces.append((seq, b""))
    return pieces


class Connection(object):
    def __init__(self, sock, response_timeout):
        self.sock = sock
        self.response_timeout = response_timeout
        self.buffer = bytearray()
        self.raw_offset = 0
        self.compressed = False
        self.inner = bytearray()
        self.inner_offset = 0
        self.inner_end = 0
        self.frames = []
        self.next_compressed_seq = 0
        self.deadline = None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def set_deadline(self, seconds=None):
        self.deadline = time.monotonic() + (
            self.response_timeout if seconds is None else seconds
        )

    def enable_compression(self):
        self.compressed = True
        self.next_compressed_seq = 0

    def take_frames(self):
        frames = self.frames
        self.frames = []
        return frames

    def _receive(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ResponseTimeout("no data within the time limit")
        self.sock.settimeout(remaining)
        try:
            chunk = self.sock.recv(RECV_SIZE)
        except socket.timeout:
            raise ResponseTimeout("no data within the time limit")
        except OSError as exc:
            raise ConnectionClosed("connection error: {}".format(exc.__class__.__name__))
        if not chunk:
            raise ConnectionClosed("connection closed by the server")
        self.buffer += chunk

    def _take_raw(self, length):
        while len(self.buffer) < length:
            self._receive()
        data = bytes(self.buffer[:length])
        del self.buffer[:length]
        self.raw_offset += length
        return data

    def _read_frame(self):
        header = self._take_raw(COMPRESS_HEADER_SIZE)
        compressed_length = int.from_bytes(header[0:3], "little")
        seq = header[3]
        uncompressed_length = int.from_bytes(header[4:7], "little")
        body = self._take_raw(compressed_length)
        if uncompressed_length == 0:
            plain = body
        else:
            try:
                plain = zlib.decompress(body)
            except zlib.error as exc:
                raise ProtocolError("compressed frame does not inflate: {}".format(exc))
            if len(plain) != uncompressed_length:
                raise ProtocolError(
                    "compressed frame inflates to {} bytes, header says {}".format(
                        len(plain), uncompressed_length
                    )
                )
        self.frames.append(
            Frame(seq, compressed_length, uncompressed_length, body, self.inner_end, len(plain))
        )
        self.inner_end += len(plain)
        self.inner += plain
        self.next_compressed_seq = (seq + 1) & 0xFF

    def _take_inner(self, length):
        while len(self.inner) < length:
            self._read_frame()
        data = bytes(self.inner[:length])
        del self.inner[:length]
        self.inner_offset += length
        return data

    def _read_wire_packet(self):
        if self.compressed:
            offset = self.inner_offset
            header = self._take_inner(HEADER_SIZE)
            payload = self._take_inner(int.from_bytes(header[0:3], "little"))
        else:
            offset = self.raw_offset
            header = self._take_raw(HEADER_SIZE)
            payload = self._take_raw(int.from_bytes(header[0:3], "little"))
        return WirePacket(header[3], payload, offset)

    def read_packet(self):
        pieces = []
        while True:
            piece = self._read_wire_packet()
            pieces.append(piece)
            if len(piece.payload) < MAX_PAYLOAD:
                return Packet(pieces)

    def send(self, payload, seq=0, continuation=False):
        return self.send_pieces(split_payload(payload, seq), continuation)

    def send_pieces(self, pieces, continuation=False):
        plain = packet_bytes(pieces)
        frames = []
        if self.compressed:
            if not continuation:
                self.next_compressed_seq = 0
            wire = bytearray()
            for position in range(0, len(plain), MAX_PAYLOAD):
                chunk = plain[position : position + MAX_PAYLOAD]
                body = chunk
                uncompressed_length = 0
                if len(chunk) >= MIN_COMPRESS_LENGTH:
                    deflated = zlib.compress(chunk)
                    if len(deflated) <= MAX_PAYLOAD:
                        body = deflated
                        uncompressed_length = len(chunk)
                wire += struct.pack("<I", len(body))[:3]
                wire += bytes([self.next_compressed_seq])
                wire += struct.pack("<I", uncompressed_length)[:3]
                wire += body
                frames.append(
                    ClientFrame(self.next_compressed_seq, len(chunk), uncompressed_length != 0)
                )
                self.next_compressed_seq = (self.next_compressed_seq + 1) & 0xFF
            data = bytes(wire)
        else:
            data = plain
        self.sock.settimeout(self.response_timeout)
        try:
            self.sock.sendall(data)
        except socket.timeout:
            raise ResponseTimeout("the request could not be sent within the time limit")
        except OSError as exc:
            raise ConnectionClosed("connection error while sending: {}".format(exc.__class__.__name__))
        return Sent([WirePacket(piece_seq, chunk, None) for piece_seq, chunk in pieces], frames)

    def wait_closed(self, seconds):
        self.set_deadline(seconds)
        try:
            if not self.buffer and not (self.compressed and self.inner):
                self._receive()
            return "data", self.read_packet()
        except ConnectionClosed:
            return "closed", None
        except ResponseTimeout:
            return "open", None
        except ProtocolError:
            return "closed", None


def connect_tcp(host, port, timeout):
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def parse_handshake(packet):
    payload = packet.payload
    info = {}
    masks = []
    info["protocol"] = payload[0]
    info["version"], pos = read_nul_string(payload, 1)
    connection_id_offset = pos
    if pos + 4 > len(payload):
        raise ProtocolError("handshake too short for the connection id")
    info["connection_id"] = struct.unpack_from("<I", payload, pos)[0]
    masks.append((connection_id_offset, 4, "connection_id"))
    pos += 4
    part1 = payload[pos : pos + 8]
    if len(part1) != 8:
        raise ProtocolError("handshake too short for the scramble")
    masks.append((pos, 8, "scramble"))
    pos += 8
    info["filler"] = payload[pos]
    pos += 1
    capabilities_low, charset, status, capabilities_high, auth_length = struct.unpack_from(
        "<HBHHB", payload, pos
    )
    pos += 8
    info["capabilities"] = capabilities_low | (capabilities_high << 16)
    info["charset"] = charset
    info["status"] = status
    info["auth_data_length"] = auth_length
    info["reserved"] = payload[pos : pos + 10]
    pos += 10
    part2_length = max(13, auth_length - 8)
    part2 = payload[pos : pos + part2_length]
    if len(part2) != part2_length:
        raise ProtocolError("handshake too short for the second scramble part")
    masks.append((pos, SCRAMBLE_LENGTH - 8, "scramble"))
    scramble = part1 + part2[: SCRAMBLE_LENGTH - 8]
    info["scramble_tail"] = part2[SCRAMBLE_LENGTH - 8 :]
    pos += part2_length
    if info["capabilities"] & CLIENT_PLUGIN_AUTH:
        plugin, pos = read_nul_string(payload, pos)
        info["plugin"] = plugin
    info["trailing"] = payload[pos:]
    packet.kind = "handshake"
    packet.info = info
    packet.masks = masks
    return scramble


def parse_ok(packet, capabilities):
    payload = packet.payload
    info = {}
    info["affected_rows"], pos = read_lenenc_int(payload, 1)
    info["last_insert_id"], pos = read_lenenc_int(payload, pos)
    info["status"] = 0
    info["warnings"] = 0
    if capabilities & CLIENT_PROTOCOL_41:
        info["status"], info["warnings"] = struct.unpack_from("<HH", payload, pos)
        pos += 4
    elif capabilities & CLIENT_TRANSACTIONS:
        info["status"] = struct.unpack_from("<H", payload, pos)[0]
        pos += 2
    if capabilities & CLIENT_SESSION_TRACK:
        if pos < len(payload):
            info["info"], pos = read_lenenc_bytes(payload, pos)
        if info["status"] & SERVER_SESSION_STATE_CHANGED and pos < len(payload):
            info["session_state"], pos = read_lenenc_bytes(payload, pos)
    else:
        info["info"] = payload[pos:]
        pos = len(payload)
    if pos < len(payload):
        info["trailing"] = payload[pos:]
    packet.kind = "ok"
    packet.info = info


def parse_err(packet):
    payload = packet.payload
    info = {"code": struct.unpack_from("<H", payload, 1)[0] if len(payload) >= 3 else None}
    if len(payload) >= 9 and payload[3:4] == b"#":
        info["sqlstate"] = payload[4:9]
        info["message"] = payload[9:]
    else:
        info["message"] = payload[3:]
    packet.kind = "err"
    packet.info = info


def parse_eof(packet):
    payload = packet.payload
    info = {}
    if len(payload) >= 5:
        info["warnings"], info["status"] = struct.unpack_from("<HH", payload, 1)
    packet.kind = "eof"
    packet.info = info


def parse_column(packet, kind="column"):
    payload = packet.payload
    info = {}
    pos = 0
    for name in ("catalog", "schema", "table", "org_table", "name", "org_name"):
        info[name], pos = read_lenenc_bytes(payload, pos)
    fixed_length, pos = read_lenenc_int(payload, pos)
    if fixed_length is None or fixed_length < 10 or pos + fixed_length > len(payload):
        raise ProtocolError("column definition with a bad fixed-length block")
    info["charset"], info["length"], info["type"], info["flags"], info["decimals"] = (
        struct.unpack_from("<HIBHB", payload, pos)
    )
    info["fixed_tail"] = payload[pos + 10 : pos + fixed_length]
    pos += fixed_length
    info["extra"] = payload[pos:]
    packet.kind = kind
    packet.info = info


def parse_prepare_ok(packet):
    payload = packet.payload
    if len(payload) < 12:
        raise ProtocolError("prepare response shorter than 12 bytes")
    statement_id, columns, params, filler, warnings = struct.unpack_from("<IHHBH", payload, 1)
    packet.kind = "prepare_ok"
    packet.info = {
        "statement_id": statement_id,
        "columns": columns,
        "params": params,
        "filler": filler,
        "warnings": warnings,
        "trailing": payload[12:],
    }


def parse_auth_switch(packet):
    payload = packet.payload
    info = {}
    if len(payload) == 1:
        info["plugin"] = None
        info["data"] = b""
    else:
        info["plugin"], pos = read_nul_string(payload, 1)
        info["data"] = payload[pos:]
        masked = min(SCRAMBLE_LENGTH, len(info["data"]))
        if masked:
            packet.masks = [(pos, masked, "scramble")]
    packet.kind = "auth_switch"
    packet.info = info


def parse_text_row(packet, count):
    payload = packet.payload
    values = []
    pos = 0
    while pos < len(payload):
        value, pos = read_lenenc_bytes(payload, pos)
        values.append(value)
    packet.kind = "row"
    packet.info = {"values": values, "count_matches": len(values) == count}


def decode_binary_value(column, payload, pos):
    column_type = column.get("type")
    unsigned = bool(column.get("flags", 0) & UNSIGNED_FLAG)
    fixed = {
        MYSQL_TYPE_TINY: ("<B" if unsigned else "<b", 1),
        MYSQL_TYPE_SHORT: ("<H" if unsigned else "<h", 2),
        MYSQL_TYPE_YEAR: ("<H", 2),
        MYSQL_TYPE_LONG: ("<I" if unsigned else "<i", 4),
        MYSQL_TYPE_INT24: ("<I" if unsigned else "<i", 4),
        MYSQL_TYPE_LONGLONG: ("<Q" if unsigned else "<q", 8),
        MYSQL_TYPE_FLOAT: ("<f", 4),
        MYSQL_TYPE_DOUBLE: ("<d", 8),
    }
    if column_type in fixed:
        form, width = fixed[column_type]
        if pos + width > len(payload):
            raise ProtocolError("binary value past the end of the row")
        return ("number", struct.unpack_from(form, payload, pos)[0]), pos + width
    if column_type in (MYSQL_TYPE_DATE, MYSQL_TYPE_DATETIME, MYSQL_TYPE_TIMESTAMP):
        length = payload[pos]
        body = payload[pos + 1 : pos + 1 + length]
        if len(body) != length or length not in (0, 4, 7, 11):
            raise ProtocolError("bad binary date length {}".format(length))
        parts = [0, 0, 0, 0, 0, 0, 0]
        if length >= 4:
            parts[0], parts[1], parts[2] = struct.unpack_from("<HBB", body, 0)
        if length >= 7:
            parts[3], parts[4], parts[5] = struct.unpack_from("<BBB", body, 4)
        if length == 11:
            parts[6] = struct.unpack_from("<I", body, 7)[0]
        return ("date", length, tuple(parts)), pos + 1 + length
    if column_type == MYSQL_TYPE_TIME:
        length = payload[pos]
        body = payload[pos + 1 : pos + 1 + length]
        if len(body) != length or length not in (0, 8, 12):
            raise ProtocolError("bad binary time length {}".format(length))
        parts = [0, 0, 0, 0, 0, 0]
        if length >= 8:
            parts[0], parts[1], parts[2], parts[3], parts[4] = struct.unpack_from(
                "<BIBBB", body, 0
            )
        if length == 12:
            parts[5] = struct.unpack_from("<I", body, 8)[0]
        return ("time", length, tuple(parts)), pos + 1 + length
    value, pos = read_lenenc_bytes(payload, pos)
    return ("bytes", value), pos


def parse_binary_row(packet, columns):
    payload = packet.payload
    count = len(columns)
    bitmap_length = (count + 9) // 8
    if len(payload) < 1 + bitmap_length:
        raise ProtocolError("binary row shorter than its null bitmap")
    bitmap = payload[1 : 1 + bitmap_length]
    pos = 1 + bitmap_length
    values = []
    for index, column in enumerate(columns):
        bit = index + 2
        if bitmap[bit // 8] & (1 << (bit % 8)):
            values.append(None)
            continue
        value, pos = decode_binary_value(column, payload, pos)
        values.append(value)
    packet.kind = "binary_row"
    packet.info = {"values": values, "bitmap": bitmap, "trailing": payload[pos:]}


def classify_single(packet, capabilities):
    first = packet.first_byte()
    if first == 0x00:
        parse_ok(packet, capabilities)
    elif first == 0xFF:
        parse_err(packet)
    elif first == 0xFE and len(packet.payload) < 9 and len(packet.payload) != 1:
        parse_eof(packet)
    elif first == 0xFE:
        parse_auth_switch(packet)
    elif first == 0x01:
        packet.kind = "auth_more_data"
        packet.info = {"data": packet.payload[1:]}
    else:
        packet.kind = "unknown"


def _read_result_sets(conn, response, capabilities, binary, local_files):
    while True:
        packet = conn.read_packet()
        response.packets.append(packet)
        first = packet.first_byte()
        if first == 0x00:
            parse_ok(packet, capabilities)
            if packet.info["status"] & SERVER_MORE_RESULTS_EXISTS:
                continue
            return
        if first == 0xFF:
            parse_err(packet)
            return
        if first == 0xFB:
            packet.kind = "local_infile"
            packet.info = {"file": packet.payload[1:]}
            content = (local_files or {}).get(packet.info["file"], b"")
            packet.reply = conn.send_pieces(
                local_infile_pieces(content, (packet.last_seq + 1) & 0xFF), continuation=True
            )
            continue
        if first is None:
            raise ProtocolError("empty packet where a result was expected")
        count, _ = read_lenenc_int(packet.payload, 0)
        if count is None or count == 0:
            raise ProtocolError("bad column count")
        packet.kind = "column_count"
        packet.info = {"count": count}
        columns = []
        for _ in range(count):
            column = conn.read_packet()
            response.packets.append(column)
            parse_column(column)
            columns.append(column.info)
        response.columns = columns
        eof = conn.read_packet()
        response.packets.append(eof)
        if not eof.is_eof():
            raise ProtocolError("no EOF after the column definitions")
        parse_eof(eof)
        if binary and eof.info.get("status", 0) & SERVER_STATUS_CURSOR_EXISTS:
            response.cursor = True
            return
        if _read_rows(conn, response, columns, binary):
            continue
        return


def _read_rows(conn, response, columns, binary):
    while True:
        packet = conn.read_packet()
        response.packets.append(packet)
        if packet.is_eof():
            parse_eof(packet)
            return bool(packet.info.get("status", 0) & SERVER_MORE_RESULTS_EXISTS)
        if packet.is_err():
            parse_err(packet)
            return False
        if binary:
            try:
                parse_binary_row(packet, columns)
            except (ProtocolError, IndexError, struct.error):
                packet.kind = "binary_row"
                packet.info = {"undecoded": True}
        else:
            try:
                parse_text_row(packet, len(columns))
            except ProtocolError:
                packet.kind = "row"
                packet.info = {"undecoded": True}


def _read_prepare(conn, response):
    packet = conn.read_packet()
    response.packets.append(packet)
    if packet.is_err():
        parse_err(packet)
        return
    if not packet.is_ok():
        raise ProtocolError("prepare response is neither OK nor ERR")
    parse_prepare_ok(packet)
    for count, kind in ((packet.info["params"], "param"), (packet.info["columns"], "column")):
        if count == 0:
            continue
        columns = []
        for _ in range(count):
            column = conn.read_packet()
            response.packets.append(column)
            parse_column(column, kind)
            columns.append(column.info)
        if kind == "column":
            response.columns = columns
        eof = conn.read_packet()
        response.packets.append(eof)
        if not eof.is_eof():
            raise ProtocolError("no EOF after the {} definitions".format(kind))
        parse_eof(eof)


def _read_field_list(conn, response):
    while True:
        packet = conn.read_packet()
        response.packets.append(packet)
        if packet.is_eof():
            parse_eof(packet)
            return
        if packet.is_err():
            parse_err(packet)
            return
        parse_column(packet)


def read_response(conn, mode, capabilities, columns=None, local_files=None):
    response = Response(mode)
    if mode == MODE_NONE:
        return response
    conn.set_deadline()
    try:
        if mode in (MODE_QUERY, MODE_EXECUTE):
            _read_result_sets(conn, response, capabilities, mode == MODE_EXECUTE, local_files)
        elif mode == MODE_PREPARE:
            _read_prepare(conn, response)
        elif mode == MODE_FETCH:
            response.columns = list(columns or [])
            _read_rows(conn, response, response.columns, True)
        elif mode == MODE_FIELD_LIST:
            _read_field_list(conn, response)
        elif mode == MODE_STRING:
            packet = conn.read_packet()
            response.packets.append(packet)
            if packet.is_err():
                parse_err(packet)
            else:
                packet.kind = "string"
                packet.info = {"text": packet.payload}
        else:
            packet = conn.read_packet()
            response.packets.append(packet)
            classify_single(packet, capabilities)
    except ConnectionClosed:
        response.closed = True
    except ResponseTimeout:
        response.timed_out = True
    except (ProtocolError, IndexError, struct.error) as exc:
        response.error = str(exc) or exc.__class__.__name__
    response.frames = conn.take_frames()
    return response


def read_greeting(conn):
    response = Response(MODE_SINGLE)
    conn.set_deadline()
    scramble = None
    try:
        packet = conn.read_packet()
        response.packets.append(packet)
        if packet.is_err():
            parse_err(packet)
        else:
            scramble = parse_handshake(packet)
    except ConnectionClosed:
        response.closed = True
    except ResponseTimeout:
        response.timed_out = True
    except (ProtocolError, IndexError, struct.error) as exc:
        response.error = str(exc) or exc.__class__.__name__
        if response.packets:
            packet = response.packets[-1]
            packet.kind = "handshake"
            packet.info = {"unparsed": True}
            packet.masks = [(1, max(0, len(packet.payload) - 1), "unparsed_handshake")]
    response.frames = conn.take_frames()
    return response, scramble
