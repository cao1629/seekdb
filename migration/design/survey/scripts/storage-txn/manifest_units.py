#!/usr/bin/env python3
"""Count manifest units (rows) per storage/logservice subdirectory.

Usage: python3 -I manifest_units.py <manifest.tsv>
"""
import collections
import sys

GROUPS = ['src/storage/memtable/mvcc', 'src/storage/memtable', 'src/storage/tx_table',
          'src/storage/tx_storage', 'src/storage/tx', 'src/storage/multi_data_source',
          'src/storage/tablelock', 'src/storage/concurrency_control', 'src/storage/deadlock',
          'src/storage/meta_mem', 'src/storage/tablet', 'src/storage/ls',
          'src/storage/blocksstable', 'src/storage/blockstore', 'src/storage/slog_ckpt',
          'src/storage/slog', 'src/storage/checkpoint', 'src/storage/meta_store',
          'src/logservice/palf', 'src/logservice', 'src/storage']


def main():
    c = collections.Counter()
    total = 0
    with open(sys.argv[1]) as fh:
        next(fh)
        for line in fh:
            src = line.split('\t')[0]
            total += 1
            for g in GROUPS:
                if src.startswith(g + '/'):
                    c[g] += 1
                    break
    topic = 0
    for g in GROUPS:
        print(f'{g}\t{c[g]}')
        if g != 'src/storage':
            topic += c[g]
    print(f'topic_units(excluding storage other)\t{topic}\nall_units\t{total}')


if __name__ == '__main__':
    main()
