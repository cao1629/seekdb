#!/usr/bin/env python3
"""00b step 3: validate the judge against deliberately broken originals.

  validate.py build --build-dir DIR --bin-dir DIR M1 [M2 ...]
      For each mutation: check its target files are clean, `git apply` the patch
      from mutations/, rebuild seekdb incrementally in DIR (a configured build
      directory separate from build_release), copy the binary to BIN_DIR/seekdb-<M>,
      and always `git apply -R` afterwards. Stops at the first failed revert.
  validate.py run --binary BIN --label LABEL --port PORT --work-root DIR
      Run every runnable mysqltest case (plain protocol) and every lifecycle
      scenario against BIN; copy judge_result.json / lifecycle_result.json to
      results/<LABEL>/.
  validate.py summary
      Print, per label, how many cases ran and failed and which ones caught it.
  validate.py report [--originals L ...] [--mutations M ...]
      Markdown catch table for the 00b report: per mutation, the cases that fail
      on it but pass on every original run.

A mutation is caught when at least one judge case fails against it; the
originals (labels `orig-1`, `orig-2`) must pass everything.
"""
from __future__ import print_function

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUDGE = HERE.parent
REPO = JUDGE.parents[1]
MUTATIONS = HERE / "mutations"
RESULTS = HERE / "results"


def git(*args):
    return subprocess.run(["git", "-C", str(REPO)] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, universal_newlines=True, check=False)


def patch_files(patch):
    return [line[len("+++ b/"):].strip() for line in patch.read_text().splitlines() if line.startswith("+++ b/")]


def command_build(args):
    build_dir, bin_dir = Path(args.build_dir), Path(args.bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    for label in args.mutations:
        patches = sorted(MUTATIONS.glob(label + "-*.patch"))
        if len(patches) != 1:
            sys.exit("expected one patch for {}, found {}".format(label, patches))
        patch = patches[0]
        files = patch_files(patch)
        dirty = git("status", "--porcelain", "--", *files).stdout.strip()
        if dirty:
            sys.exit("refusing to apply {}: target files are not clean:\n{}".format(patch.name, dirty))
        applied = git("apply", str(patch))
        if applied.returncode != 0:
            sys.exit("git apply {} failed:\n{}".format(patch.name, applied.stdout))
        try:
            print("[build] {} ({})".format(label, ", ".join(files)), flush=True)
            build = subprocess.run(["make", "-C", str(build_dir), "-j{}".format(args.jobs), "seekdb"],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True, check=False)
            if build.returncode != 0:
                print(build.stdout[-4000:])
                print("[build] {} FAILED".format(label), flush=True)
            else:
                shutil.copy2(str(build_dir / "src" / "observer" / "seekdb"), str(bin_dir / ("seekdb-" + label)))
                print("[build] {} -> {}".format(label, bin_dir / ("seekdb-" + label)), flush=True)
        finally:
            reverted = git("apply", "-R", str(patch))
            if reverted.returncode != 0:
                sys.exit("REVERT FAILED for {}; fix the tree by hand:\n{}".format(patch.name, reverted.stdout))
    remaining = git("status", "--porcelain", "--", "src", "rust").stdout.strip()
    if remaining:
        sys.exit("source tree not clean after builds:\n{}".format(remaining))
    print("[build] done; source tree clean", flush=True)


def command_run(args):
    work = Path(args.work_root) / args.label
    out = RESULTS / args.label
    out.mkdir(parents=True, exist_ok=True)
    judge_cmd = [sys.executable, "-I", str(JUDGE / "runner" / "judge.py"), "run", "--seekdb", args.binary,
                 "--work-dir", str(work / "mysqltest"), "--port", str(args.port),
                 "--case-timeout", str(args.case_timeout), "--jobs", str(args.jobs)]
    if args.exclude:
        judge_cmd += ["--exclude", args.exclude]
    life_cmd = [sys.executable, "-I", str(JUDGE / "runner" / "lifecycle.py"), "run", "--seekdb", args.binary,
                "--work-dir", str(work / "lifecycle"), "--port", str(args.port)]
    for command, name in ((judge_cmd, "judge_result.json"), (life_cmd, "lifecycle_result.json")):
        log = (work / (name + ".log"))
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w") as handle:
            subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, check=False)
        produced = work / ("mysqltest" if name.startswith("judge") else "lifecycle") / name
        if produced.is_file():
            shutil.copy2(str(produced), str(out / name))
        else:
            print("[run] {}: {} missing, see {}".format(args.label, name, log), flush=True)
    print("[run] {} done".format(args.label), flush=True)


def command_summary(args):
    for label_dir in sorted(p for p in RESULTS.iterdir() if p.is_dir()):
        cases, failed = 0, []
        for name in ("judge_result.json", "lifecycle_result.json"):
            path = label_dir / name
            if not path.is_file():
                failed.append("<{} missing>".format(name))
                continue
            data = json.loads(path.read_text())
            items = data.get("cases") or data.get("scenarios") or []
            cases += len(items)
            failed += [("lifecycle:" if "scenarios" in data else "") + item["name"]
                       for item in items if not item["ok"]]
            if data.get("error"):
                failed.append("<error: {}>".format(data["error"]))
        print("{:6s} ran={:4d} failed={:4d}  {}".format(label_dir.name, cases, len(failed),
                                                      ", ".join(failed[:12]) + (" ..." if len(failed) > 12 else "")))


def load_failures(label):
    failed, ran = set(), 0
    for name in ("judge_result.json", "lifecycle_result.json"):
        path = RESULTS / label / name
        if not path.is_file():
            return None, 0
        data = json.loads(path.read_text())
        items = data.get("cases") or data.get("scenarios") or []
        ran += len(items)
        prefix = "lifecycle:" if "scenarios" in data else ""
        failed |= {prefix + item["name"] for item in items if not item["ok"]}
    return failed, ran


def command_report(args):
    """Markdown table: per mutation, the cases that fail on it but pass on every original run."""
    baseline = set()
    for label in args.originals:
        failed, _ = load_failures(label)
        if failed is None:
            sys.exit("missing results for {}".format(label))
        baseline |= failed
    print("| 缺陷 | 注入位置 | 抓到它的用例数 | 其中生命周期场景 | 例子 |")
    print("|---|---|---|---|---|")
    for label in args.mutations:
        failed, ran = load_failures(label)
        if failed is None:
            print("| {} | (no results) | | | |".format(label))
            continue
        caught = sorted(failed - baseline)
        patch = sorted(MUTATIONS.glob(label + "-*.patch"))[0]
        target = ", ".join("`{}`".format(f) for f in patch_files(patch))
        lifecycle = [c for c in caught if c.startswith("lifecycle:")]
        examples = ", ".join("`{}`".format(c) for c in (lifecycle + [c for c in caught if c not in lifecycle])[:6])
        print("| {} ({}) | {} | {} / {} | {} | {} |".format(
            label, patch.stem.split("-", 1)[1], target, len(caught), ran, len(lifecycle), examples))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command")
    build = sub.add_parser("build")
    build.add_argument("--build-dir", required=True)
    build.add_argument("--bin-dir", required=True)
    build.add_argument("--jobs", type=int, default=6)
    build.add_argument("mutations", nargs="+")
    build.set_defaults(handler=command_build)
    run = sub.add_parser("run")
    run.add_argument("--binary", required=True)
    run.add_argument("--label", required=True)
    run.add_argument("--port", type=int, required=True)
    run.add_argument("--work-root", required=True)
    run.add_argument("--case-timeout", type=int, default=600)
    run.add_argument("--jobs", type=int, default=3, help="mysqltest workers; they use ports PORT..PORT+JOBS-1")
    run.add_argument("--exclude", help="comma-separated mysqltest cases to skip")
    run.set_defaults(handler=command_run)
    summary = sub.add_parser("summary")
    summary.set_defaults(handler=command_summary)
    report = sub.add_parser("report")
    report.add_argument("--originals", nargs="+", default=["orig-1", "orig-2"])
    report.add_argument("--mutations", nargs="+", default=["M1", "M2", "M3", "M4", "M5", "M5b", "M6", "M6c", "M6d"])
    report.set_defaults(handler=command_report)
    args = parser.parse_args()
    if not hasattr(args, "handler"):
        parser.print_usage()
        return 2
    return args.handler(args) or 0


if __name__ == "__main__":
    sys.exit(main())
