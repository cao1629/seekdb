#!/usr/bin/env python3
"""Count word-boundary references to key storage/txn types per top-level module.

Runs over the comment/string-stripped mirror. Each occurrence of the
identifier as a whole word counts once.

Usage: python3 -I type_refs.py <mirror_root>
"""
import collections
import os
import re
import sys

TYPES = [
    'ObTabletHandle', 'ObTableHandleV2', 'ObTablesHandleArray', 'ObLSHandle',
    'ObStorageMetaHandle', 'ObMacroBlockHandle', 'ObStorageObjectHandle',
    'ObTabletPointerHandle', 'ObMetaObjGuard', 'ObTabletTableIterator',
    'ObTxDesc', 'ObTxCtx', 'ObTransCtx', 'ObPartTransCtx', 'ObTxDataGuard',
    'ObTxTableGuard', 'ObTxTableGuards', 'ObStoreCtx', 'ObMemtableCtx',
    'ObMemtable', 'ObMvccRow', 'ObMvccTransNode', 'ObITransCallback',
    'ObMvccRowCallback', 'ObTxCallbackList', 'ObLSTxCtxMgr',
    'ObStorageMetaMemMgr', 'ObTabletPointer', 'ObTablet', 'ObSSTable',
    'ObITable', 'MdsTable', 'MdsTableHandle', 'ObLogHandler', 'PalfHandle',
    'AppendCb', 'ObTxLogCb', 'ObKeyBtree',
]
RX = {t: re.compile(r'\b' + t + r'\b') for t in TYPES}


def main():
    root = os.path.join(sys.argv[1], 'src')
    cnt = collections.defaultdict(collections.Counter)
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root)
            mod = rel.split(os.sep)[0]
            if mod == 'storage':
                sub = rel.split(os.sep)[1] if len(rel.split(os.sep)) > 2 else '(top)'
                mod = 'storage/' + sub
            with open(p, encoding='utf-8', errors='replace') as fh:
                s = fh.read()
            for t, r in RX.items():
                n = len(r.findall(s))
                if n:
                    cnt[t][mod] += n
    for t in TYPES:
        c = cnt[t]
        total = sum(c.values())
        outside_storage = sum(v for k, v in c.items() if not k.startswith('storage'))
        top = ', '.join(f'{k}={v}' for k, v in c.most_common(6))
        print(f'{t}\ttotal={total}\toutside_storage={outside_storage}\t{top}')


if __name__ == '__main__':
    main()
