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
import fnmatch
import math
import os
import posixpath
import re
import sys
from collections import Counter, defaultdict

IMPL_EXTS = {".c", ".cc", ".cpp", ".cxx"}
SOURCE_RANK = {".cpp": 0, ".cc": 0, ".c": 0, ".cxx": 0, ".h": 1, ".hpp": 1, ".hxx": 1, ".ipp": 2}
RESERVED_STEMS = {"lib", "main", "mod"}
RESERVED_FILES = {"lib.rs", "main.rs", "mod.rs"}
UNMAPPED = "(unmapped)"
REASONS = ("island", "generated", "data", "vendored", "dead", "dropped", "deferred")
RUST_KEYWORDS = {"as", "break", "const", "continue", "crate", "else", "enum", "extern", "false", "fn", "for", "if",
                 "impl", "in", "let", "loop", "match", "mod", "move", "mut", "pub", "ref", "return", "self", "Self",
                 "static", "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while", "async",
                 "await", "dyn", "abstract", "become", "box", "do", "final", "macro", "override", "priv", "typeof",
                 "unsized", "virtual", "yield", "try", "gen"}
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LEX_RE = re.compile(r'R"([^()\\\s]{0,16})\(.*?\)\1"|//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'',
                    re.S)
BLANK_RE = re.compile(r"[^\n]")
PP_RE = re.compile(r"^\s*#\s*(\w*)(.*)$")
OPENER_RE = re.compile(r"(?:\bnamespace\b[\w:\s]*|\bextern\s*)$")


class Tree:
    def __init__(self, root):
        self.root = root
        self.dirs = {}
        self.texts = {}

    def is_dir(self, rel):
        r = self.dirs.get(rel)
        if r is None:
            r = os.path.isdir(os.path.join(self.root, rel))
            self.dirs[rel] = r
        return r

    def text(self, rel):
        t = self.texts.get(rel)
        if t is None:
            with open(os.path.join(self.root, rel), "rb") as f:
                t = f.read().decode("utf-8", "surrogateescape")
            self.texts[rel] = t
        return t

    def lines(self, rel):
        t = self.text(rel)
        return t.count("\n") + (0 if not t or t.endswith("\n") else 1)

    def match(self, rows, path):
        for row in rows:
            prefix = row[0]
            if path == prefix or path.startswith(prefix + "/") or (not self.is_dir(prefix) and path.startswith(prefix)):
                return row
        return None


def load_prefix_map(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = [p.strip() for p in line.split("\t")]
            rows.append((parts[0].rstrip("/"), parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""))
    rows.sort(key=lambda r: (-len(r[0]), r[0]))
    return rows


def load_core(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = [p.strip() for p in line.split("\t")]
            rows.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return rows


def core_row(rows, path):
    for pattern, subsystem in rows:
        if fnmatch.fnmatchcase(path, pattern) if "*" in pattern else path.startswith(pattern):
            return pattern, subsystem
    return None


def load_units(path):
    units = {}
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            units[parts[0]] = (parts[1].split(","), int(parts[2]), parts[4] == "yes")
    return units


def load_order(path):
    sections = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("# crate "):
                sections.append((line[len("# crate "):].split(":", 1)[0], []))
            elif line and not line.startswith("#"):
                if not sections:
                    sys.exit("ERROR: %s has a batch before its first '# crate' line" % path)
                sections[-1][1].extend(u for u in line.split("\t") if u)
    return sections


def load_forwarders(path):
    fwd = set()
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) > 1 and "," not in parts[1]:
                fwd.add(parts[0])
    return fwd


def parse_excludes(specs):
    rows = []
    for spec in specs:
        body, sep, reason = spec.rpartition("=")
        if not sep or reason not in REASONS:
            sys.exit("ERROR: --exclude must look like 'PATH[:FROM-TO]=REASON' with REASON one of %s, got %r"
                     % ("/".join(REASONS), spec))
        m = re.match(r"^(.*):(\d+)-(\d+)$", body)
        if m:
            rows.append((m.group(1), (int(m.group(2)), int(m.group(3))), reason))
        else:
            rows.append((body.rstrip("/"), None, reason))
    return rows


def blank(m):
    return BLANK_RE.sub(" ", m.group(0))


def cut_points(text):
    lines = LEX_RE.sub(blank, text).split("\n")
    stack, depth, paren, last, recent = [], 0, 0, "", ""
    pp, pp_stack, in_pp, dead = 0, [], False, 0
    term = 0
    cands = []

    def snapshot():
        return depth, list(stack), paren, last

    for no, line in enumerate(lines, 1):
        if term == no - 1 and not in_pp and not dead and depth == 0 and paren == 0 and last in (";", "}", "#"):
            cands.append((no, pp))
        stripped = line.strip()
        if in_pp or stripped.startswith("#"):
            if not in_pp:
                m = PP_RE.match(line)
                word, rest = (m.group(1), m.group(2).strip()) if m else ("", "")
                if word in ("if", "ifdef", "ifndef"):
                    pp += 1
                    if dead:
                        dead += 1
                    elif word == "if" and rest in ("0", "(0)"):
                        dead = 1
                    else:
                        pp_stack.append([snapshot(), None])
                elif word in ("else", "elif", "elifdef", "elifndef"):
                    if dead == 1:
                        dead = 0
                        pp_stack.append([snapshot(), None])
                    elif not dead and pp_stack:
                        entry = pp_stack[-1]
                        if entry[1] is None:
                            entry[1] = snapshot()
                        depth, stack, paren, last = entry[0][0], list(entry[0][1]), entry[0][2], entry[0][3]
                elif word == "endif":
                    pp = max(0, pp - 1)
                    if dead:
                        dead -= 1
                    elif pp_stack:
                        entry = pp_stack.pop()
                        if entry[1] is not None:
                            depth, stack, paren, last = entry[1][0], list(entry[1][1]), entry[1][2], entry[1][3]
            if not dead and depth == 0 and paren == 0:
                last, term = "#", no
            in_pp = stripped.endswith("\\")
            continue
        if dead:
            continue
        for ch in line:
            if ch == "{":
                opener = bool(OPENER_RE.search(recent))
                stack.append(opener)
                if not opener:
                    depth += 1
                recent, last = "", "{"
            elif ch == "}":
                if stack and not stack.pop():
                    depth = max(0, depth - 1)
                recent, last, term = "", "}", no
            elif ch == ";":
                recent, last, term = "", ";", no
            else:
                if ch == "(":
                    paren += 1
                elif ch == ")":
                    paren = max(0, paren - 1)
                recent += ch
                if not ch.isspace():
                    last = ch
        recent = recent[-200:] + " "
    return cands


def pack(start, end, cuts, prefix, cap_lines, cap_chars):
    pieces = []
    cur = start
    inner = [c for c in cuts if start < c <= end]
    while True:
        rest_lines = end - cur + 1
        rest_chars = prefix[end] - prefix[cur - 1]
        if rest_lines <= cap_lines and rest_chars <= cap_chars:
            pieces.append((cur, end))
            return pieces
        n = max(2, math.ceil(rest_lines / cap_lines), math.ceil(rest_chars / cap_chars))
        target = rest_chars / n
        best, best_score = None, None
        for c in inner:
            if c <= cur:
                continue
            size_lines, size_chars = c - cur, prefix[c - 1] - prefix[cur - 1]
            if size_lines > cap_lines or size_chars > cap_chars:
                break
            score = abs(size_chars - target)
            if best is None or score < best_score:
                best, best_score = c, score
        if best is None:
            best = next((c for c in inner if c > cur), None)
            if best is None:
                pieces.append((cur, end))
                return pieces
        pieces.append((cur, best - 1))
        cur = best



def load_rows(path, ncols):
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for no, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            cols = [c.strip() for c in line.split("\t")]
            cols += [""] * (ncols - len(cols))
            rows.append((no, cols))
    return rows


def split_source(spec):
    m = re.match(r"^(.*?):(\d+)-(\d+)$", spec)
    if m:
        return m.group(1), (int(m.group(2)), int(m.group(3)))
    return spec, None


def load_aliases(path):
    out = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            next(f, None)
            for line in f:
                cols = line.rstrip("\n").split("\t")
                if len(cols) >= 3 and cols[1] not in ("", "-"):
                    out[cols[0]] = (cols[1], cols[2])
    return out


def subtract(ranges, cut):
    out = []
    for a, b in ranges:
        if cut[1] < a or cut[0] > b:
            out.append((a, b))
            continue
        if cut[0] > a:
            out.append((a, cut[0] - 1))
        if cut[1] < b:
            out.append((cut[1] + 1, b))
    return out


def covered(ranges, rng):
    return any(a <= rng[0] and rng[1] <= b for a, b in ranges)


class Manifest:
    def __init__(self, args):
        self.args = args
        self.tree = Tree(args.root)
        self.units = load_units(args.units)
        self.unit_of = {f: u for u, (fs, _, _) in self.units.items() for f in fs}
        self.crates = load_prefix_map(args.crates)
        self.crate_names = {r[1] for r in self.crates}
        self.islands = load_prefix_map(args.islands) if args.islands else []
        self.core = load_core(args.core) if args.core else []
        self.forwarders = load_forwarders(args.forwarders) if args.forwarders else set()
        self.aliases = load_aliases(args.aliases)
        self.excludes = parse_excludes(args.exclude)
        errors = []
        for no, (src, reason, design) in load_rows(args.exclusions, 3):
            path, rng = split_source(src)
            if reason not in REASONS:
                errors.append("%s:%d: reason %r is not one of %s" % (args.exclusions, no, reason, "/".join(REASONS)))
            self.excludes.append((path.rstrip("/"), rng, reason, "%s: %s" % (os.path.basename(args.exclusions),
                                                                            design)))
        self.placements = []
        for no, (src, crate, module, design) in load_rows(args.placements, 4):
            path, rng = split_source(src)
            if path not in self.unit_of:
                errors.append("%s:%d: %s is not a file of any map unit" % (args.placements, no, path))
                continue
            if crate not in self.crate_names:
                errors.append("%s:%d: crate %s is on no crate-map row" % (args.placements, no, crate))
                continue
            if rng is not None and not (1 <= rng[0] <= rng[1] <= self.tree.lines(path)):
                errors.append("%s:%d: %s:%d-%d is outside the file" % (args.placements, no, path, rng[0], rng[1]))
                continue
            self.placements.append((path, rng, crate, "" if module in ("", "-") else module, design))
        if errors:
            sys.exit("ERROR: " + "\nERROR: ".join(errors))
        self.cap_lines = args.piece_lines
        self.cap_chars = args.piece_tokens * args.chars_per_token
        self.warnings = []
        self.cut_cache = {}

    def island(self, path):
        row = self.tree.match(self.islands, path) if self.islands else None
        return row if row and row[1] != "-" else None

    def whole_exclusion(self, path):
        for prefix, rng, reason, rule in self.excludes:
            if rng is None and (path == prefix or path.startswith(prefix + "/")
                                or (not self.tree.is_dir(prefix) and path.startswith(prefix))):
                return rule, reason
        if path in self.aliases:
            kept, guard = self.aliases[path]
            return ("aliases.tsv: a copy of %s (include guard %s), translated once as that file" % (kept, guard),
                    "dropped")
        crate = self.placement(path)
        if crate == "x-dropped":
            return "crate map: x-dropped", "dropped"
        if crate == "standby":
            return "crate map: standby (ARCHITECTURE 7.4)", "deferred"
        return None

    def range_exclusions(self, path):
        return sorted((rng, reason, rule) for p, rng, reason, rule in self.excludes if rng is not None and p == path)

    def included_ranges(self, path):
        n = self.tree.lines(path)
        ranges, cur = [], 1
        for (a, b), _, _ in self.range_exclusions(path):
            if a > cur:
                ranges.append((cur, min(a - 1, n)))
            cur = max(cur, b + 1)
        if cur <= n:
            ranges.append((cur, n))
        return ranges

    def unit_source(self, uid, files):
        cands = [f for f in files if not self.island(f)] or list(files)
        own = [f for f in cands if posixpath.splitext(f)[0] == uid and f not in self.forwarders]
        pool = own or [f for f in cands if f not in self.forwarders] or cands
        return sorted(pool, key=lambda p: (SOURCE_RANK.get(posixpath.splitext(p)[1], 3), p))[0]

    def target_of(self, source, crate_override=None):
        row = self.tree.match(self.crates, source)
        if row is None:
            sys.exit("ERROR: no crate-map row matches %s" % source)
        prefix, crate, subdir = row
        if crate_override:
            crate = crate_override
        base = prefix if self.tree.is_dir(prefix) else posixpath.dirname(prefix)
        d = posixpath.dirname(source)
        rel = "" if d == base else posixpath.relpath(d, base)
        parts = [p for p in (subdir, rel) if p and p != "."]
        directory = posixpath.join("rust", crate, "src", *parts)
        stem = posixpath.splitext(posixpath.basename(source))[0]
        want = stem + ("_" if stem in RESERVED_STEMS else "") + ".rs"
        if not self.subs:
            return crate, posixpath.join(directory, want)
        target = posixpath.join(directory, posixpath.basename(source))
        for old, new in self.subs:
            target = target.replace(old, new)
        name = posixpath.basename(target)
        if target == source or name != want or posixpath.dirname(target) != directory:
            sys.exit("ERROR: the naming rules turn %r into %r; expected %s/%s (check the --sub pairs)"
                     % (source, target, directory, want))
        return crate, target

    def cuts_for(self, path):
        c = self.cut_cache.get(path)
        if c is None:
            c = cut_points(self.tree.text(path))
            self.cut_cache[path] = c
        return c

    def prefix_chars(self, path):
        text = self.tree.text(path)
        line_chars = [len(l) + 1 for l in text.split("\n")]
        prefix = [0]
        for n in line_chars:
            prefix.append(prefix[-1] + n)
        return prefix

    def chunks(self, path, ranges):
        prefix = self.prefix_chars(path)
        cands = self.cuts_for(path)
        depths = Counter(d for _, d in cands)
        base = min(depths, key=lambda d: (-depths[d], d)) if depths else 0
        best = None
        for extra in (0, 1, 2):
            cuts = sorted({no for no, d in cands if d <= base + extra})
            pieces = []
            for a, b in ranges:
                pieces.extend(pack(a, b, cuts, prefix, self.cap_lines, self.cap_chars))
            over = [(a, b) for a, b in pieces
                    if b - a + 1 > self.cap_lines or prefix[b] - prefix[a - 1] > self.cap_chars]
            if best is None or len(over) < best[1]:
                best = (pieces, len(over))
            if not over:
                break
        return best[0]

    def size(self, entries):
        chars = lines = 0
        for path, rng in entries:
            text = self.tree.text(path)
            if rng is None:
                chars += len(text)
                lines += self.tree.lines(path)
            else:
                body = text.split("\n")[rng[0] - 1:rng[1]]
                chars += sum(len(l) + 1 for l in body)
                lines += rng[1] - rng[0] + 1
        return lines, chars

    def fmt(self, entries):
        out = []
        for p, r in entries:
            if r is None or r == (1, self.tree.lines(p)):
                out.append(p)
            else:
                out.append("%s:%d-%d" % (p, r[0], r[1]))
        return ",".join(out)

    def placement(self, source):
        row = self.tree.match(self.crates, source)
        return row[1] if row else None

    def partition(self, sections):
        keep = set(self.args.outside_build)
        whole_place = {}
        range_place = defaultdict(list)
        for path, rng, crate, module, design in self.placements:
            if rng is None:
                whole_place[path] = (crate, module, design)
            else:
                range_place[path].append((rng, crate, module, design))
        specs = []
        groups = {}
        skipped = []
        for crate_hdr, uids in sections:
            for uid in uids:
                files, _, in_build = self.units[uid]
                islands = [f for f in files if self.island(f)]
                excluded = {f: self.whole_exclusion(f) for f in files if f not in islands}
                rest = [f for f in files if f not in islands and not excluded[f]]
                source0 = self.unit_source(uid, files)
                crate = self.placement(source0) or UNMAPPED
                if crate != crate_hdr:
                    sys.exit("ERROR: %s is under '# crate %s' in %s, but its source %s maps to %s"
                             % (uid, crate_hdr, self.args.order, source0, crate))
                reasons = sorted({excluded[f] for f in files if excluded.get(f)},
                                 key=lambda e: (REASONS.index(e[1]), e[0]))
                if not in_build and uid not in keep:
                    reason, rule = (reasons[0][1], reasons[0][0]) if reasons and not rest else \
                        ("dead", "not compiled by the reference build")
                    skipped.append((uid, reason, ",".join(files), rule))
                    continue
                ported = [(f, p) for f in islands for p in range_place.get(f, ())]
                if not rest and not ported:
                    if islands:
                        reason, rule = "island", "islands.txt " + ",".join(sorted({self.island(f)[0]
                                                                                   for f in islands}))
                    else:
                        reason, rule = reasons[0][1], reasons[0][0]
                    skipped.append((uid, reason, ",".join(files), rule))
                    continue
                if crate == UNMAPPED:
                    sys.exit("ERROR: %s (source %s) is in the build but no crate-map row matches it" % (uid, source0))
                is_core = any(core_row(self.core, f) for f in rest)
                for f in islands:
                    skipped.append((uid, "island", f, "islands.txt " + self.island(f)[0]))
                for f in files:
                    if excluded.get(f):
                        skipped.append((uid, excluded[f][1], f, excluded[f][0]))
                entries = {}
                for f in rest:
                    entries[f] = self.included_ranges(f)
                    for rng, reason, rule in self.range_exclusions(f):
                        skipped.append((uid, reason, "%s:%d-%d" % (f, rng[0], rng[1]), rule))

                def add_group(pcrate, pmod, f, rngs, design, ported_from_island=False):
                    if pmod:
                        target = "rust/%s/src/%s.rs" % (pcrate, pmod)
                    else:
                        target = self.target_of(f, pcrate)[1]
                    g = groups.setdefault(target, {"crate": pcrate, "entries": [], "units": [], "designs": [],
                                                   "ported": []})
                    g["entries"].extend((f, r) for r in rngs)
                    if uid not in g["units"]:
                        g["units"].append(uid)
                    if design not in g["designs"]:
                        g["designs"].append(design)
                    if ported_from_island:
                        g["ported"].extend((f, r) for r in rngs)

                for f in rest:
                    if f in whole_place:
                        continue
                    for rng, pcrate, pmod, design in range_place.get(f, ()):
                        if not covered(entries[f], rng):
                            sys.exit("ERROR: placement %s:%d-%d overlaps an exclusion or another placement"
                                     % (f, rng[0], rng[1]))
                        entries[f] = subtract(entries[f], rng)
                        add_group(pcrate, pmod, f, [rng], design)
                for f in rest:
                    if f in whole_place:
                        pcrate, pmod, design = whole_place[f]
                        add_group(pcrate, pmod, f, entries[f], design)
                for f, (rng, pcrate, pmod, design) in ported:
                    add_group(pcrate, pmod, f, [rng], design, ported_from_island=True)
                stay = [f for f in rest if f not in whole_place and entries[f]]
                if stay:
                    source = self.unit_source(uid, stay)
                    tcrate, target = self.target_of(source)
                    specs.append({"section": crate_hdr, "uid": uid, "source": source, "target": target,
                                  "crate": tcrate, "core": is_core, "entries": [(f, entries[f]) for f in stay],
                                  "units": [uid]})
        return specs, groups, skipped

    def build(self):
        sections = load_order(self.args.order)
        seen = Counter(u for _, us in sections for u in us)
        dup = sorted(u for u, n in seen.items() if n > 1)
        missing = sorted(set(self.units) - set(seen))
        unknown = sorted(set(seen) - set(self.units))
        if dup or missing or unknown:
            sys.exit("ERROR: %s and %s disagree: %d units listed twice, %d missing, %d unknown (%s)" % (
                self.args.order, self.args.units, len(dup), len(missing), len(unknown),
                " ".join((dup + missing + unknown)[:5])))
        self.stats = Counter()
        self.big = []
        self.over = []
        self.subsystems = Counter()
        specs, groups, skipped = self.partition(sections)
        self.groups = groups
        spec_targets = {s["target"] for s in specs}
        new_modules = defaultdict(list)
        for spec in self.args.new_module:
            crate, _, module = spec.partition("/")
            if not module:
                sys.exit("ERROR: --new-module must look like CRATE/MODULE, got %r" % spec)
            new_modules[crate].append(module)
        by_section = defaultdict(list)
        for s in specs:
            by_section[s["section"]].append(s)
        step3, core = [], []
        placed_groups = set()
        for crate_hdr, _ in sections:
            for module in new_modules.pop(crate_hdr, []):
                target = "rust/%s/src/%s.rs" % (crate_hdr, module)
                core.append(("-", target, "core/%s/%s" % (crate_hdr, module), "subsystem", "-", "-", 0, 0))
            for target in sorted(t for t, g in groups.items() if g["crate"] == crate_hdr and t not in spec_targets):
                g = groups[target]
                placed_groups.add(target)
                files = []
                for f, _ in g["entries"]:
                    if f not in files:
                        files.append(f)
                source = sorted(files, key=lambda p: (SOURCE_RANK.get(posixpath.splitext(p)[1], 3), p))[0]
                entries = [(f, [r for f2, r in g["entries"] if f2 == f]) for f in files]
                self.subsystems["design placements"] += 1
                core.extend(self.unit_rows(g["units"][0], entries, source, target, crate_hdr, True, g["units"]))
            for s in by_section.get(crate_hdr, []):
                entries = list(s["entries"])
                units = list(s["units"])
                is_core = s["core"]
                if s["target"] in groups:
                    g = groups[s["target"]]
                    placed_groups.add(s["target"])
                    for f, r in g["entries"]:
                        for i, (f2, rs) in enumerate(entries):
                            if f2 == f:
                                entries[i] = (f2, sorted(rs + [r]))
                                break
                        else:
                            entries.append((f, [r]))
                    for u in g["units"]:
                        if u not in units:
                            units.append(u)
                    is_core = True
                if is_core:
                    hit = next((core_row(self.core, f) for f, _ in entries if core_row(self.core, f)), None)
                    self.subsystems[hit[1] if hit else "design placements"] += 1
                rows = self.unit_rows(s["uid"], entries, s["source"], s["target"], s["crate"], is_core, units)
                (core if is_core else step3).extend(rows)
        left = sorted(set(groups) - placed_groups)
        if left:
            sys.exit("ERROR: placements target crates the order file lacks: %s" % " ".join(left))
        if new_modules:
            sys.exit("ERROR: --new-module names crates that the order file lacks: %s" % " ".join(sorted(new_modules)))
        return step3, core, skipped

    def unit_rows(self, uid, entries, source, target, crate, is_core, units):
        flat = [(f, r) for f, rs in entries for r in rs]
        lines, chars = self.size(flat)
        stem_dir, stem_name = posixpath.split(target)
        stem = stem_name[:-3]
        if is_core:
            module = posixpath.relpath(target, "rust/%s/src" % crate)[:-3]
            head_id = "core/%s/%s" % (crate, module)
        else:
            head_id = uid
        kind = "subsystem" if is_core else "stem"
        units_col = ",".join(units)
        big = [f for f, rs in entries if sum(b - a + 1 for a, b in rs) >= self.args.split_lines]
        if not big and chars <= self.cap_chars:
            self.stats[kind] += 1
            if lines > self.cap_lines:
                self.stats["rows over %d lines, under the token cap" % self.cap_lines] += 1
            return [(source, target, head_id, kind, self.fmt(flat), units_col, lines, chars)]
        if not is_core:
            kind = "split"
        for f in big:
            self.big.append((f, uid, sum(b - a + 1 for a, b in dict(entries)[f]), is_core))
        own = posixpath.splitext(source)[0]

        def impl_like(f):
            ext = posixpath.splitext(f)[1]
            return ext in IMPL_EXTS or ext == ".ipp"

        headers = sorted((e for e in entries if not impl_like(e[0])),
                         key=lambda e: (posixpath.splitext(e[0])[0] != own, e[0]))
        impls = sorted((e for e in entries if impl_like(e[0])),
                       key=lambda e: (e[0] != source, posixpath.splitext(e[0])[1] == ".ipp", e[0]))
        head, pieces = [], []
        hl = hc = 0
        for f, rs in headers:
            fl, fc = self.size([(f, r) for r in rs])
            if hl + fl <= self.cap_lines and hc + fc <= self.cap_chars:
                head.extend((f, r) for r in rs)
                hl, hc = hl + fl, hc + fc
                continue
            if fl <= self.cap_lines and fc <= self.cap_chars:
                pieces.append([(f, r) for r in rs])
                continue
            parts = self.chunks(f, rs)
            for p in parts:
                pl, pc = self.size([(f, p)])
                if not pieces and hl + pl <= self.cap_lines and hc + pc <= self.cap_chars:
                    head.append((f, p))
                    hl, hc = hl + pl, hc + pc
                else:
                    pieces.append([(f, p)])
        for f, rs in impls:
            fl, fc = self.size([(f, r) for r in rs])
            if fl <= self.cap_lines and fc <= self.cap_chars:
                pieces.append([(f, r) for r in rs])
            else:
                pieces.extend([[(f, p)] for p in self.chunks(f, rs)])
        if not head:
            head = pieces.pop(0)
        rows = []
        for i, ins in enumerate([head] + pieces):
            pl, pc = self.size(ins)
            if pl > self.cap_lines or pc > self.cap_chars:
                self.over.append((head_id if i == 0 else "%s.p%02d" % (head_id, i), pl, pc, self.fmt(ins)))
            if i == 0:
                rows.append((source, target, head_id, kind, self.fmt(ins), units_col, pl, pc))
            else:
                rows.append((ins[0][0], posixpath.join(stem_dir, "%s_p%02d.rs" % (stem, i)),
                             "%s.p%02d" % (head_id, i), kind, self.fmt(ins), units_col, pl, pc))
        self.stats[kind] += len(rows)
        self.stats["%s pieces" % ("core" if is_core else "step3")] += len(pieces)
        return rows


def parse_inputs(text, tree):
    out = []
    if not text or text == "-":
        return out
    for item in text.split(","):
        path, rng = split_source(item)
        if rng is None:
            rng = (1, tree.lines(path))
        out.append((path, rng))
    return out


def coverage(m, step3, core, skipped, in_build):
    cover = defaultdict(list)
    for which, rows in (("manifest", step3), ("core", core)):
        for r in rows:
            for path, rng in parse_inputs(r[4], m.tree):
                cover[path].append((rng, which, r[2]))
    for uid, reason, files, rule in skipped:
        for path, rng in parse_inputs(files, m.tree):
            cover[path].append((rng, "not-translated:" + reason, uid))
    errors = []
    ported = 0
    for u in sorted(in_build):
        for f in m.units[u][0]:
            n = m.tree.lines(f)
            diff = [0] * (n + 2)
            kinds = defaultdict(set)
            for (a, b), which, who in cover.get(f, ()):
                if a < 1 or b > n or a > b:
                    errors.append("%s:%d-%d (%s %s) is outside the file's %d lines" % (f, a, b, which, who, n))
                    continue
                diff[a] += 1
                diff[b + 1] -= 1
                kinds[which].add((a, b))
            cur = 0
            bad = []
            for line in range(1, n + 1):
                cur += diff[line]
                if cur == 1:
                    continue
                if cur == 2 and "not-translated:island" in kinds and any(
                        a <= line <= b for w in ("core", "manifest") for a, b in kinds.get(w, ())):
                    ported += 1
                    continue
                bad.append((line, cur))
            if bad:
                spans = []
                for line, cnt in bad:
                    if spans and spans[-1][1] == line - 1 and spans[-1][2] == cnt:
                        spans[-1][1] = line
                    else:
                        spans.append([line, line, cnt])
                errors.append("%s (unit %s): %s" % (f, u, "; ".join(
                    "lines %d-%d placed %d times" % (a, b, c) for a, b, c in spans[:4])))
    return errors, ported


def check_targets(rows):
    errors = []
    by_target = defaultdict(list)
    for r in rows:
        by_target[r[1]].append(r[2])
    for t, ids in sorted(by_target.items()):
        if len(ids) > 1:
            errors.append("target %s is written by %s" % (t, ", ".join(ids)))
    modules = set()
    for t in by_target:
        parts = t.split("/")
        if parts[-1] in RESERVED_FILES:
            errors.append("target %s is a file the crate-root generator writes" % t)
        if len(parts) > 3 and parts[3] == "generated":
            errors.append("target %s is under the generator output directory" % t)
        modules.add(t[:-3])
    dir_modules = set()
    for t in by_target:
        parts = t[:-3].split("/")
        for i in range(4, len(parts)):
            dir_modules.add("/".join(parts[:i]))
    clash = sorted(modules & dir_modules)
    notes = []
    for m in clash:
        notes.append("module %s is both a unit's file (%s.rs) and a directory of other units" % (m, m))
    names = Counter()
    for t in by_target:
        for comp in t[:-3].split("/")[3:]:
            if comp in RUST_KEYWORDS:
                names["keyword: " + comp] += 1
            elif not IDENT_RE.match(comp):
                names["not an identifier: " + comp] += 1
    return errors, notes, names


def write_tsv(path, header, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for r in rows:
            f.write("\t".join(r) + "\n")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.normpath(os.path.join(here, "..", ".."))
    depmap = os.path.join(root, "migration", "depmap")
    ap = argparse.ArgumentParser(description="Write the Step 3 manifest, the core manifest and the not-translated "
                                             "list from the dependency map (RULEBOOK section 4; ARCHITECTURE 14).")
    ap.add_argument("--root", default=root)
    ap.add_argument("--order", required=True, help="depmap order-crates.txt: '# crate' sections of unit batches")
    ap.add_argument("--out", required=True, help="the Step 3 manifest to write")
    ap.add_argument("--core-out", default=None,
                    help="the core manifest to write (default core-manifest.tsv beside --out)")
    ap.add_argument("--not-translated-out", default=None,
                    help="the not-translated list to write (default not-translated.tsv beside --out)")
    ap.add_argument("--units", default=os.path.join(depmap, "units.tsv"))
    ap.add_argument("--forwarders", default=os.path.join(depmap, "forwarders.tsv"))
    ap.add_argument("--islands", default=os.path.join(depmap, "islands.txt"))
    ap.add_argument("--core", default=os.path.join(depmap, "core-scope.txt"))
    ap.add_argument("--aliases", default=os.path.join(depmap, "aliases.tsv"),
                    help="header copies from the map: each copy is dropped and translated once as the kept header")
    ap.add_argument("--placements", default=os.path.join(depmap, "core-placements.txt"),
                    help="core placements the design names: a file or line range, its crate, its module, the source")
    ap.add_argument("--exclusions", default=os.path.join(depmap, "exclusions.txt"),
                    help="files, prefixes and line ranges that are not translated, with reason and source")
    ap.add_argument("--crates", required=True, help="the crate prefix map: prefix, crate, optional dir")
    ap.add_argument("--sub", action="append", default=[], metavar="OLD=NEW",
                    help="string substitution applied in order to rust/<crate>/src/<dir>/<source file name>; without "
                         "any, the file name is the source's stem, with a trailing _ for lib, main and mod, and .rs")
    ap.add_argument("--exclude", action="append", default=[], metavar="PATH[:FROM-TO]=REASON",
                    help="a file, a prefix or a line range that is not translated, with its reason")
    ap.add_argument("--outside-build", action="append", default=[], metavar="UNIT",
                    help="a unit translated although the reference build does not compile it")
    ap.add_argument("--new-module", action="append", default=[], metavar="CRATE/MODULE",
                    help="a core module of new code with no C++ source")
    ap.add_argument("--split-lines", type=int, default=4000)
    ap.add_argument("--piece-lines", type=int, default=3999)
    ap.add_argument("--piece-tokens", type=int, default=30000)
    ap.add_argument("--chars-per-token", type=int, default=4)
    args = ap.parse_args()
    out_dir = os.path.dirname(os.path.abspath(args.out))
    core_out = args.core_out or os.path.join(out_dir, "core-manifest.tsv")
    nt_out = args.not_translated_out or os.path.join(out_dir, "not-translated.tsv")

    m = Manifest(args)
    m.subs = []
    for s in args.sub:
        old, sep, new = s.partition("=")
        if not sep or not old:
            sys.exit("ERROR: --sub must look like 'old=new', got: %r" % s)
        m.subs.append((old, new))
    step3, core, skipped = m.build()
    errors, notes, names = check_targets(step3 + core)
    in_build = {u for u, (_, _, b) in m.units.items() if b} | set(args.outside_build)
    seen_units = set()
    for r in step3 + core:
        seen_units.update(u for u in r[5].split(",") if u and u != "-")
    seen_units.update(s[0] for s in skipped)
    absent = sorted(u for u in in_build if u not in seen_units)
    if absent:
        errors.append("completeness: %d in-build units placed nowhere (%s)" % (len(absent), " ".join(absent[:5])))
    cov_errors, ported = coverage(m, step3, core, skipped, in_build)
    errors.extend("coverage: " + e for e in cov_errors)
    if errors:
        for e in errors[:60]:
            print("ERROR: " + e, file=sys.stderr)
        sys.exit(1)

    write_tsv(args.out, ("source", "target", "unit_id", "kind", "inputs"), [r[:5] for r in step3])
    write_tsv(core_out, ("source", "target", "unit_id", "kind", "inputs", "units"), [r[:6] for r in core])
    write_tsv(nt_out, ("unit_id", "reason", "files", "rule"), skipped)

    cpt = args.chars_per_token
    whole_units = {s[0] for s in skipped if s[2] == ",".join(m.units[s[0]][0])}
    print("wrote %s: %d rows (%s)" % (args.out, len(step3), " ".join(
        "%s=%d" % (k, sum(1 for r in step3 if r[3] == k)) for k in ("stem", "split"))))
    print("wrote %s: %d rows (subsystem=%d, of them %d new-code modules)" % (
        core_out, len(core), len(core), sum(1 for r in core if r[0] == "-")))
    print("wrote %s: %d rows: %d whole units (%s), %d parts of translated units (%s)" % (
        nt_out, len(skipped), len(whole_units), " ".join(
            "%s=%d" % (k, n) for k, n in sorted(Counter(s[1] for s in skipped if s[0] in whole_units).items(),
                                                key=lambda kv: REASONS.index(kv[0]))),
        len(skipped) - len(whole_units), " ".join(
            "%s=%d" % (k, n) for k, n in sorted(Counter(s[1] for s in skipped if s[0] not in whole_units).items(),
                                                key=lambda kv: REASONS.index(kv[0])))))
    print("map units: %d; in the reference build (with --outside-build): %d; every line of their files placed "
          "exactly once (%d island lines also ported into a Rust row by a placement)" % (
              len(m.units), len(in_build), ported))
    print("core units by subsystem (core-scope.txt; design placements): %s" % " ".join(
        "%s=%d" % kv for kv in sorted(m.subsystems.items(), key=lambda kv: -kv[1])))
    print("header copies dropped as aliases: %d; design placements: %d (%d targets)" % (
        len(m.aliases), len(m.placements), len(m.groups)))
    big_core = sum(1 for b in m.big if b[3])
    print("files of %d lines or more split: %d (core %d, Step 3 %d); pieces besides the heads: core %d, Step 3 %d"
          % (args.split_lines, len(m.big), big_core, len(m.big) - big_core, m.stats["core pieces"],
             m.stats["step3 pieces"]))
    for f, uid, n, is_core in sorted(m.big, key=lambda b: -b[2]):
        print("  %-7s %6d lines  %s" % ("core" if is_core else "step3", n, f))
    allrows = [("manifest", r) for r in step3] + [("core", r) for r in core]
    print("largest rows by tokens (characters / %d):" % cpt)
    for which, r in sorted(allrows, key=lambda x: -x[1][7])[:15]:
        print("  %7d tokens %6d lines  %-8s %-9s %s" % (r[7] // cpt, r[6], which, r[3], r[2]))
    toks = sorted(r[7] // cpt for _, r in allrows if r[7])
    if toks:
        print("tokens per row: median %d, mean %d, p90 %d, max %d, total %d" % (
            toks[len(toks) // 2], sum(toks) // len(toks), toks[int(len(toks) * 0.9)], toks[-1], sum(toks)))
    print("rows over the cap of %d lines or %d tokens: %d (a single class or function the cut points cannot "
          "divide)" % (args.piece_lines, args.piece_tokens, len(m.over)))
    for rid, pl, pc, ins in sorted(m.over, key=lambda x: -x[2]):
        print("  %7d tokens %6d lines  %s  %s" % (pc // cpt, pl, rid, ins[:160]))
    for k, n in sorted(m.stats.items()):
        if k.startswith("rows over"):
            print("%s: %d" % (k, n))
    for n in notes:
        print("NOTE: " + n)
    for k, n in sorted(names.items()):
        print("NOTE: %d target path components are a Rust %s" % (n, k))
    for w in m.warnings:
        print("WARNING: " + w)


if __name__ == "__main__":
    main()
