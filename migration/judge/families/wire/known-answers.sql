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

--echo # CRC32() against the CRC-32 check value and other fixed inputs
select crc32('123456789') as crc, crc32('123456789') = 3421780262 as is_known_answer;
select crc32('') = 0 as empty_is_0, crc32(null) is null as null_is_null;
select crc32('a') as crc, crc32('a') = 3904355907 as is_known_answer;
select crc32('The quick brown fox jumps over the lazy dog') as crc, crc32('The quick brown fox jumps over the lazy dog') = 1095738169 as is_known_answer;
select crc32(123456789) as crc, crc32(123456789) = 3421780262 as is_known_answer;
select crc32(x'00') as crc, crc32(x'00') = 3523407757 as is_known_answer;
select crc32(x'ff00ff00') as crc, crc32(x'ff00ff00') = 1818567839 as is_known_answer;
select crc32(repeat('a', 1000)) as crc, crc32(repeat('a', 1000)) = 2587417091 as is_known_answer;
select crc32(x'636166c3a9') as crc, crc32(x'636166c3a9') = 2561491637 as is_known_answer;

--echo # COMPRESS() is a 4-byte little-endian length and a zlib stream from compress() at the default level
select hex(compress('')) as h, compress('') = '' as empty_stays_empty, compress(null) is null as null_is_null;
select hex(compress('a')) as h, hex(compress('a')) = '01000000789C4B040000620062' as is_known_answer;
select hex(compress('hello world')) as h, hex(compress('hello world')) = '0B000000789CCB48CDC9C95728CF2FCA4901001A0B045D' as is_known_answer;
select hex(compress('abc ')) as h, hex(compress('abc ')) = '04000000789C4B4C4A56000003940147' as is_known_answer;
select hex(compress(x'00')) as h, hex(compress(x'00')) = '01000000789C63000000010001' as is_known_answer;
select hex(compress(repeat('abc', 1000))) as h, hex(compress(repeat('abc', 1000))) = 'B80B0000789CEDC2411100000C02A0AC6AFF0EABB1071CE9A2AAAAFE7EECD47CAD' as is_known_answer;
select hex(compress(repeat('0123456789abcdefghijklmnopqrstuvwxyz', 100))) as h, hex(compress(repeat('0123456789abcdefghijklmnopqrstuvwxyz', 100))) = '100E0000789CEDCA591640200040D12D656639A85066196AF5F6D179F7FB8A24CDF2A2ACEAA6ED7AA9F4304EC6CECBBAEDC779B9FB793F1F0487C3E170381C0E87C3E170A23B3F8E38257C' as is_known_answer;
select hex(compress(x'8a5e3c1f00ff7b2d91c4e6a3b8f01d52')) as h, hex(compress(x'8a5e3c1f00ff7b2d91c4e6a3b8f01d52')) = '10000000789CEB8AB39167F85FAD3BF1C8B3C53B3EC80601003D1807E0' as is_known_answer;
select hex(compress(repeat(x'00010203', 256))) as h, hex(compress(repeat(x'00010203', 256))) = '00040000789C636064626618C5A378148F480C0002B40601' as is_known_answer;
select hex(compress(x'1f')) as h, hex(compress(x'1f')) = '01000000789C93070000200020' as is_known_answer;

--echo # COMPRESS() of long inputs built from a number sequence, shown as lengths and md5
--disable_warnings
drop table if exists ka_seq, ka_digits;
--enable_warnings
create table ka_digits (d int primary key);
insert into ka_digits values (0), (1), (2), (3), (4), (5), (6), (7), (8), (9);
create table ka_seq (n int primary key);
insert into ka_seq select a.d * 10000 + b.d * 1000 + c.d * 100 + e.d * 10 + f.d + 1 from ka_digits a, ka_digits b, ka_digits c, ka_digits e, ka_digits f where a.d * 10000 + b.d * 1000 + c.d * 100 + e.d * 10 + f.d < 30000;
set session group_concat_max_len = 2097152;
select length(x) as n, md5(x) = '3da2388d8b2e0057ecf2b57434b7a962' as is_known_input, length(compress(x)) as compressed_length, md5(compress(x)) as compressed_md5, md5(compress(x)) = 'c7313a9028b111782188880b41db7537' as is_known_answer from (select group_concat(md5(n) order by n separator '') as x from ka_seq where n <= 3200) t;
select uncompressed_length(compress(x)) = length(x) as header_matches, md5(uncompress(compress(x))) = md5(x) as round_trip from (select group_concat(md5(n) order by n separator '') as x from ka_seq where n <= 3200) t;
select length(x) as n, md5(x) = '371c2a6e069d40feababa898d7d441ec' as is_known_input, length(compress(x)) as compressed_length, md5(compress(x)) as compressed_md5, md5(compress(x)) = 'b544697ba5b96837758f90d813667d78' as is_known_answer from (select group_concat(md5(n) order by n separator '') as x from ka_seq where n <= 30000) t;
select uncompressed_length(compress(x)) = length(x) as header_matches, md5(uncompress(compress(x))) = md5(x) as round_trip from (select group_concat(md5(n) order by n separator '') as x from ka_seq where n <= 30000) t;
select length(x) as n, md5(x) = '10450154b84baf39593a6d1f052581a3' as is_known_input, length(compress(x)) as compressed_length, md5(compress(x)) as compressed_md5, md5(compress(x)) = '73d80535db88c0e20b9eeecd5f4a7d63' as is_known_answer from (select group_concat(concat(elt(locate(substr(md5(n), 1, 1), '0123456789abcdef'), 'the', 'of', 'and', 'a', 'to', 'in', 'is', 'was', 'for', 'on', 'that', 'with', 'as', 'by', 'at', 'from'), ' ', elt(locate(substr(md5(n), 2, 1), '0123456789abcdef'), 'river', 'stone', 'light', 'market', 'signal', 'garden', 'engine', 'letter', 'window', 'harbor', 'forest', 'number', 'bridge', 'winter', 'pocket', 'silver'), ' ', elt(locate(substr(md5(n), 3, 1), '0123456789abcdef'), 'runs', 'holds', 'turns', 'waits', 'falls', 'moves', 'stays', 'grows', 'burns', 'opens', 'keeps', 'finds', 'draws', 'pulls', 'rests', 'sings'), ' ', n, elt(locate(substr(md5(n), 4, 1), '0123456789abcdef'), '.', ',', ';', '!', '?', ':', '.', ',', '.', ',', ' -', '.', ',', '.', '...', '.')) order by n separator ' ') as x from ka_seq where n <= 6000) t;
select uncompressed_length(compress(x)) = length(x) as header_matches, md5(uncompress(compress(x))) = md5(x) as round_trip from (select group_concat(concat(elt(locate(substr(md5(n), 1, 1), '0123456789abcdef'), 'the', 'of', 'and', 'a', 'to', 'in', 'is', 'was', 'for', 'on', 'that', 'with', 'as', 'by', 'at', 'from'), ' ', elt(locate(substr(md5(n), 2, 1), '0123456789abcdef'), 'river', 'stone', 'light', 'market', 'signal', 'garden', 'engine', 'letter', 'window', 'harbor', 'forest', 'number', 'bridge', 'winter', 'pocket', 'silver'), ' ', elt(locate(substr(md5(n), 3, 1), '0123456789abcdef'), 'runs', 'holds', 'turns', 'waits', 'falls', 'moves', 'stays', 'grows', 'burns', 'opens', 'keeps', 'finds', 'draws', 'pulls', 'rests', 'sings'), ' ', n, elt(locate(substr(md5(n), 4, 1), '0123456789abcdef'), '.', ',', ';', '!', '?', ':', '.', ',', '.', ',', ' -', '.', ',', '.', '...', '.')) order by n separator ' ') as x from ka_seq where n <= 6000) t;
select length(x) as n, md5(x) = '71c3493a9123efd70d37e5ed945a4761' as is_known_input, length(compress(x)) as compressed_length, md5(compress(x)) as compressed_md5, md5(compress(x)) = 'a9e38618577d00656fe12388f51f456f' as is_known_answer from (select unhex(group_concat(sha2(n, 256) order by n separator '')) as x from ka_seq where n <= 4096) t;
select uncompressed_length(compress(x)) = length(x) as header_matches, md5(uncompress(compress(x))) = md5(x) as round_trip from (select unhex(group_concat(sha2(n, 256) order by n separator '')) as x from ka_seq where n <= 4096) t;
set session group_concat_max_len = default;
drop table ka_seq, ka_digits;

--echo # UNCOMPRESS() and UNCOMPRESSED_LENGTH() read the same format back
select uncompress(compress('hello world')) as v, uncompressed_length(compress('hello world')) as n;
select uncompress(unhex('0B000000789CCB48CDC9C95728CF2FCA4901001A0B045D')) = 'hello world' as known_bytes_inflate;
select length(uncompress(unhex('B80B0000789CEDC2411100000C02A0AC6AFF0EABB1071CE9A2AAAAFE7EECD47CAD'))) as n, uncompress(unhex('B80B0000789CEDC2411100000C02A0AC6AFF0EABB1071CE9A2AAAAFE7EECD47CAD')) = repeat('abc', 1000) as known_bytes_inflate;
select hex(uncompress(unhex('10000000789CEB8AB39167F85FAD3BF1C8B3C53B3EC80601003D1807E0'))) as h;
select uncompressed_length(compress(repeat('abc', 1000))) as n, uncompressed_length(unhex('00040000789C636064626618C5A378148F480C0002B40601')) as m;
select uncompress('') as v, uncompressed_length('') as n, uncompress(null) is null as a, uncompressed_length(null) is null as b;
select uncompress('abc') as v;
show warnings;
select uncompressed_length(x'0a000000') as n;
show warnings;
select uncompressed_length(x'0a00000078') as n;
select uncompress(x'05000000ffffffffff') as v;
show warnings;
select uncompress(x'01000001789c030000000001') as v;
show warnings;

--echo # UNCOMPRESS() when the length header disagrees with a complete stream
select hex(uncompress(x'00000000789C4B040000620062')) as h, uncompress(x'00000000789C4B040000620062') = '' as is_empty;
show warnings;
select hex(uncompress(x'05000000789C4B040000620062')) as h;
show warnings;
select uncompress(x'01000000789CCB48CDC9C95728CF2FCA4901001A0B045D') as v;
show warnings;
