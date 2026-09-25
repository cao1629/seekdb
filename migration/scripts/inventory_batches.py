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
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SWEEP = os.path.join(ROOT, 'migration', 'inventory', 'sweep')
OUT = os.path.join(ROOT, 'migration', 'inventory', 'batches')


def load(category):
    with open(os.path.join(SWEEP, category + '.tsv'), encoding='utf-8') as f:
        lines = f.read().split('\n')
    header = lines[0]
    rows = [l for l in lines[1:] if l]
    return header, rows


def batches_for(category, size):
    header, rows = load(category)
    by_file = {}
    order = []
    for r in rows:
        f = r.split('\t', 1)[0]
        if f not in by_file:
            by_file[f] = []
            order.append(f)
        by_file[f].append(r)
    out = []
    cur = []
    for f in order:
        group = by_file[f]
        while len(group) > size:
            if cur:
                out.append(cur)
                cur = []
            out.append(group[:size])
            group = group[size:]
        if len(cur) + len(group) > size:
            out.append(cur)
            cur = []
        cur.extend(group)
    if cur:
        out.append(cur)
    return header, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('spec', nargs='+', help='category=rows_per_batch')
    args = ap.parse_args()
    manifest = []
    for spec in args.spec:
        category, size = spec.split('=')
        header, out = batches_for(category, int(size))
        d = os.path.join(OUT, category)
        os.makedirs(d, exist_ok=True)
        for i, rows in enumerate(out, 1):
            name = '%s-%03d' % (category, i)
            path = os.path.join(d, name + '.tsv')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(header + '\n' + '\n'.join(rows) + '\n')
            manifest.append({'category': category, 'batch': name, 'rows': len(rows),
                             'path': os.path.relpath(path, ROOT)})
    total = sum(m['rows'] for m in manifest)
    for spec in args.spec:
        category = spec.split('=')[0]
        _, rows = load(category)
        got = sum(m['rows'] for m in manifest if m['category'] == category)
        if got != len(rows):
            sys.exit('row count mismatch for %s: %d != %d' % (category, got, len(rows)))
    json.dump(manifest, sys.stdout, indent=0)
    sys.stdout.write('\n')
    sys.stderr.write('batches=%d rows=%d\n' % (len(manifest), total))


if __name__ == '__main__':
    main()
