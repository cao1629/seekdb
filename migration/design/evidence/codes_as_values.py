import subprocess, re, collections, sys
# For every OB_ code: total token occurrences in src, and lines where it is compared with ret/tmp_ret
# (`OB_X == ret`, `ret == OB_X`, `OB_X != ret`, `ret != OB_X`, `case OB_X:`), and lines where it is assigned (`ret = OB_X;`).
out = subprocess.run(["git","grep","-h","-E",r"\bOB_[A-Z][A-Z_0-9]*\b","--","src"],capture_output=True,text=True,cwd="/Users/colin/seekdb-dev/migrate-to-rust").stdout.splitlines()
tok = re.compile(r"\bOB_[A-Z][A-Z_0-9]*\b")
cmp_re = re.compile(r"\b(OB_[A-Z][A-Z_0-9]*)\s*[!=]=\s*(ret|tmp_ret|err|errcode|err_code|ret_code|error_code|retcode|errno_)\b|\b(ret|tmp_ret|err|errcode|err_code|ret_code|error_code|retcode|errno_)\s*[!=]=\s*(OB_[A-Z][A-Z_0-9]*)\b")
case_re = re.compile(r"\bcase\s+(OB_[A-Z][A-Z_0-9]*)\s*:")
asg_re = re.compile(r"\b(?:ret|tmp_ret)\s*=\s*(OB_[A-Z][A-Z_0-9]*)\s*;")
total=collections.Counter(); cmp=collections.Counter(); case=collections.Counter(); asg=collections.Counter()
names=set()
# restrict to error-code names: those defined in ob_errno.def
for l in open("/Users/colin/seekdb-dev/migrate-to-rust/src/share/ob_errno.def"):
    m=re.match(r"DEFINE_(?:ERROR|ERROR_EXT|ERROR_DEP|ERROR_EXT_DEP|OTHER_MSG_FMT|OTHER_MSG_FMT_DEP)\((OB_[A-Z_0-9]+)",l)
    if m: names.add(m.group(1))
for l in out:
    for t in tok.findall(l):
        if t in names: total[t]+=1
    for m in cmp_re.finditer(l):
        n = m.group(1) or m.group(4)
        if n in names: cmp[n]+=1
    for m in case_re.finditer(l):
        if m.group(1) in names: case[m.group(1)]+=1
    for m in asg_re.finditer(l):
        if m.group(1) in names: asg[m.group(1)]+=1
print("codes defined:",len(names)," codes appearing in src:",len(total)," codes never referenced:",len(names-set(total)))
print("distinct codes compared with ret/tmp_ret/err...:",len(cmp)," total compare lines:",sum(cmp.values()))
print("  compare lines excluding OB_SUCCESS:",sum(v for k,v in cmp.items() if k!='OB_SUCCESS'))
print("distinct codes in case labels:",len(case)," total case lines:",sum(case.values()))
print("distinct codes assigned to ret:",len(asg)," total assign lines:",sum(asg.values()))
print()
print("%-45s %7s %7s %6s %7s"%("code","total","cmp","case","assign"))
for n,v in cmp.most_common(60):
    print("%-45s %7d %7d %6d %7d"%(n,total[n],v,case[n],asg[n]))
print()
print("top assigned codes:")
for n,v in asg.most_common(25):
    print("%-45s %7d %7d %6d %7d"%(n,total[n],cmp[n],case[n],v))
