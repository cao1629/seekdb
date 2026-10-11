import re, sys
# Usage: python3 -I bison_stats.py file.output ...
for path in sys.argv[1:]:
    txt = open(path, encoding='utf-8', errors='replace').read()
    g = txt.index('\nGrammar\n'); t = txt.index('\nTerminals, with rules where they appear\n')
    n = txt.index('\nNonterminals, with rules where they appear\n')
    s = txt.index('\nstate 0\n')
    rules = re.findall(r'^\s+(\d+)\s+\S+:|^\s+(\d+)\s+\|', txt[g:t], re.M)
    nrules = max(int(a or b) for a, b in rules)
    terms = re.findall(r'^(\S+)\s+\(\d+\)', txt[t:n], re.M)
    nonterms = re.findall(r'^(\S+)\s+\(\d+\)', txt[n:s], re.M)
    states = len(re.findall(r'^state \d+$', txt[s:], re.M))
    unused_terms = txt[:g].split('Terminals unused in grammar')[1].split('\n\n')[1].split() if 'Terminals unused' in txt[:g] else []
    errtok = len(re.findall(r'\berror\b', txt[g:t]))
    print(f"{path}: rules={nrules} terminals={len(terms)} nonterminals={len(nonterms)} states={states} unused_terminals={len(unused_terms)} 'error'-token rule mentions={errtok}")
