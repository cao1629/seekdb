#!/usr/bin/env python3
"""Find ParseNode mutation/construction outside the parser directories.
Usage: python3 -I parsenode_writes.py REPO FILELIST
Heuristics (regex, comment lines skipped), only in files that mention ParseNode:
 - field writes: ->(type_|num_child_|children_|str_value_|str_len_|value_|raw_text_|text_len_|is_*_|flag_|param_num_|pos_)  =  (not ==)
 - children_[i] = ...
 - node construction: new_node/new_terminal_node/new_non_terminal_node/new_list_node/deep_copy_parse_node/push_back_child/append_child
 - const_cast<ParseNode
"""
import os, re, sys, collections
repo, flist = sys.argv[1], sys.argv[2]
EXCL = ('src/sql/parser/', 'src/pl/parser/', 'src/query/api/query/parser/')
files = [l.strip() for l in open(flist) if l.strip()]
W = re.compile(r'(->|\.)(type_|num_child_|children_|str_value_|str_len_|value_|raw_text_|text_len_|param_num_|pos_|flag_|is_[a-z_]+_|raw_param_idx_|raw_sql_offset_|sql_str_off_|pl_str_off_|stmt_loc_)\s*(\[[^\]]*\])?\s*=(?!=)')
C = re.compile(r'\b(new_node|new_terminal_node|new_non_terminal_node|new_list_node|deep_copy_parse_node|deep_copy_parse_node_base|push_back_child|push_front_child|append_child|merge_tree)\s*\(')
K = re.compile(r'const_cast\s*<\s*ParseNode')
w_files = collections.Counter(); c_files = collections.Counter(); k_files = collections.Counter()
for f in files:
    if not f.startswith('src/') or f.startswith(EXCL):
        continue
    if os.path.splitext(f)[1] not in {'.c', '.h', '.cpp', '.ipp', '.cc'}:
        continue
    try:
        txt = open(os.path.join(repo, f), encoding='utf-8', errors='replace').read()
    except FileNotFoundError:
        continue
    if 'ParseNode' not in txt:
        continue
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith(('//', '*', '/*')):
            continue
        if W.search(line):
            w_files[f] += 1
        if C.search(line):
            c_files[f] += 1
        if K.search(line):
            k_files[f] += 1
for name, d in (('field writes', w_files), ('node construction/copy calls', c_files), ('const_cast<ParseNode', k_files)):
    print(f'{name}: files={len(d)} lines={sum(d.values())}  top: ' + ', '.join(f'{k}({v})' for k, v in d.most_common(6)))
