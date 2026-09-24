# Decode a C++ etc/seekdb.data_version.bin and build the file a Rust build with
# DATA_CURRENT_VERSION 2.0.0.0 must write, using the C++ layout:
# ObRecordHeader (ob_record_header.cpp:112-122, big-endian encode_i16/i32/i64),
# magic 0xBEDE, format 2 (ob_data_version_mgr.h:93-94), payload "%s %lu\n",
# data checksum = ob_crc64 (CRC-32C, init 0, no final xor; ob_crc64.cpp:372-398),
# header checksum = XOR of 16-bit pieces (ob_record_header.cpp:31-45).
import struct, sys

def crc32c_ob(crc, buf):
    poly = 0x82F63B78
    for b in buf:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
    return crc

def header_checksum(magic, hlen, ver, ts, dlen, dzlen, dcs):
    cs = 0
    def f64(v, cs):
        for i in range(4):
            cs ^= (v >> (i * 16)) & 0xFFFF
        return cs
    def f32(v, cs):
        for i in range(2):
            cs ^= (v >> (i * 16)) & 0xFFFF
        return cs
    cs = f64(magic, cs)
    cs ^= hlen
    cs ^= ver
    cs ^= 0
    cs ^= ts & 0xFFFF
    cs = f32(dlen, cs)
    cs = f32(dzlen, cs)
    cs = f64(dcs, cs)
    return cs & 0xFFFF

def build(major, minor, mpatch, mipatch):
    v = (major << 32) + (minor << 16) + (mpatch << 8) + mipatch
    payload = b"%d.%d.%d.%d %d\n" % (major, minor, mpatch, mipatch, v)
    dcs = crc32c_ob(0, payload)
    magic = 0xBEDE - 0x10000  # int16_t, sign-extended by format_i64 (ob_record_header.h:30-37)
    hcs = header_checksum(magic, 32, 2, 0, len(payload), len(payload), dcs)
    hdr = struct.pack('>hhhHqiiq', magic, 32, 2, hcs, 0, len(payload), len(payload), dcs)
    return hdr + payload

if len(sys.argv) > 1:
    data = open(sys.argv[1], 'rb').read()
    print('C++ file matches rebuilt 1.4.0.0 file:', data == build(1, 4, 0, 0))
f = build(2, 0, 0, 0)
print(len(f), f.hex())
