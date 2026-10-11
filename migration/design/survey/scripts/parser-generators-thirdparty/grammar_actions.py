#!/usr/bin/env python3
"""Split a bison .y rules section into grammar lines vs. action (C code) lines.
Usage: python3 -I grammar_actions.py file.y ...
Brace-depth scanner over the rules section (between the first two '%%' lines);
ignores braces inside string/char literals and comments. A line is an 'action line'
if any part of it is at brace depth >= 1. Also counts $$/$N/@N uses and actions."""
import sys, re
for path in sys.argv[1:]:
    lines = open(path, encoding='utf-8', errors='replace').read().split('\n')
    seps = [i for i, l in enumerate(lines) if l.strip() == '%%']
    a, b = seps[0], (seps[1] if len(seps) > 1 else len(lines))
    prologue = a
    rules = lines[a + 1:b]
    depth = 0; action_lines = 0; grammar_lines = 0; actions = 0
    in_block_comment = False
    for line in rules:
        i = 0; touched = depth > 0; in_str = None
        while i < len(line):
            c = line[i]
            if in_block_comment:
                if line.startswith('*/', i):
                    in_block_comment = False; i += 2; continue
                i += 1; continue
            if in_str:
                if c == '\\':
                    i += 2; continue
                if c == in_str:
                    in_str = None
                i += 1; continue
            if line.startswith('/*', i):
                in_block_comment = True; i += 2; continue
            if line.startswith('//', i):
                break
            if c in '"\'':
                in_str = c
            elif c == '{':
                if depth == 0:
                    actions += 1
                depth += 1; touched = True
            elif c == '}':
                depth -= 1
            i += 1
        if line.strip() == '':
            continue
        if touched:
            action_lines += 1
        else:
            grammar_lines += 1
    body = '\n'.join(rules)
    print(f'{path}: prologue_lines={prologue} rules_section_lines={len(rules)} epilogue_lines={len(lines)-b-1} '
          f'grammar_lines={grammar_lines} action_lines={action_lines} actions={actions} '
          f'$$={len(re.findall(r"[$][$]", body))} $N={len(re.findall(r"[$][0-9]+", body))} @N={len(re.findall(r"@[0-9]+", body))} '
          f'YYERROR/YYABORT={len(re.findall(r"YYERROR|YYABORT", body))}')
