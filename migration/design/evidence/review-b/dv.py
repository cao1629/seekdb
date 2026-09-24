import struct
def crc32c_raw(init, data):
    crc = init & 0xffffffff
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc
def s16(v): 
    v &= 0xffff
    return v
def hdr(payload):
    magic=-16674 & 0xffffffffffffffff  # 0xBEDE sign-extended
    hl=32; ver=2; ts=0; dl=len(payload); dz=len(payload); dc=crc32c_raw(0,payload)
    cs=0
    for i in range(4): cs ^= (magic >> (16*i)) & 0xffff
    cs ^= hl; cs ^= ver; cs ^= 0; cs ^= ts & 0xffff
    for i in range(2): cs ^= (dl >> (16*i)) & 0xffff
    for i in range(2): cs ^= (dz >> (16*i)) & 0xffff
    for i in range(4): cs ^= (dc >> (16*i)) & 0xffff
    h = struct.pack('>hhhhqiiq', -16674, hl, ver, (cs if cs<0x8000 else cs-0x10000), ts, dl, dz, dc)
    return h+payload
real=open('/Users/colin/seekdb-dev/mysqltest-runs/00b/perf-834bbee1e-rw/base/etc/seekdb.data_version.bin','rb').read()
mine=hdr(b"1.4.0.0 4295229440\n")
print('1.4.0.0 matches real file:', mine==real)
v2=hdr(b"2.0.0.0 8589934592\n")
print(v2.hex())
print('design hex:', 'bede00200002a21c000000000000000000000013000000130000000000408ca393'+ b"2.0.0.0 8589934592\n".hex())
print('equal:', v2.hex()=='bede00200002a21c000000000000000000000013000000130000000000408ca393'+ b"2.0.0.0 8589934592\n".hex())
print((2<<32), (1<<32)|(4<<16))
