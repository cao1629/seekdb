#!/usr/bin/env python3
"""Reverse include reach (files that transitively include any seed) over migration/depmap/edges.tsv.
Usage: python3 -I rev_reach.py REPO SEED [SEED ...]"""
import os, sys, collections
repo, seeds = sys.argv[1], sys.argv[2:]
rev = collections.defaultdict(set)
for line in open(os.path.join(repo, 'migration/depmap/edges.tsv')):
    a, b, *_ = line.rstrip('\n').split('\t')
    if a != 'from':
        rev[b].add(a)
seen = set(seeds); stack = list(seeds)
while stack:
    x = stack.pop()
    for y in rev.get(x, ()):
        if y not in seen:
            seen.add(y); stack.append(y)
print(len(seen))
