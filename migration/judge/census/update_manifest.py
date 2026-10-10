#!/usr/bin/env python3
"""Edit migration/judge/suites/mysqltest/cases.tsv (the orchestrator is its only writer).

  update_manifest.py <repo_root> set NAME field=value [field=value ...]
  update_manifest.py <repo_root> merge SHARD.tsv [SHARD.tsv ...]

`merge` applies rewriter shards (header: name decision test_file removed kept
gray_kept dropped note). A `rewritten` row points the case at its rewritten
test and at the .result next to it (recorded later from the original binary);
a `quarantined` row keeps the original paths and records what was guarded.
Only `pending` cases are merged; anything else is reported and skipped.
"""
import csv
import sys
from pathlib import Path

FIELDS = ("name", "status", "test_file", "result_file", "origin", "note")
SHARD_FIELDS = ("name", "decision", "test_file", "removed", "kept", "gray_kept", "dropped", "note")

root = Path(sys.argv[1])
manifest = root / "migration/judge/suites/mysqltest/cases.tsv"


def load():
    with manifest.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def save(rows):
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def clean(text):
    return " ".join((text or "").split())


rows = load()
by_name = {row["name"]: row for row in rows}
command = sys.argv[2]

if command == "set":
    row = by_name[sys.argv[3]]
    for assignment in sys.argv[4:]:
        field, _, value = assignment.partition("=")
        if field not in FIELDS or field == "name":
            sys.exit("unknown field {}".format(field))
        row[field] = clean(value)
    save(rows)
    print("updated {}".format(sys.argv[3]))
elif command == "merge":
    applied, skipped = 0, []
    for shard in sys.argv[3:]:
        with open(shard, encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != SHARD_FIELDS:
                sys.exit("{}: unexpected header {}".format(shard, reader.fieldnames))
            for item in reader:
                row = by_name.get(item["name"])
                if row is None or row["status"] != "pending":
                    skipped.append("{} ({})".format(item["name"], "unknown" if row is None else row["status"]))
                    continue
                if item["decision"] == "rewritten":
                    test_file = item["test_file"].strip()
                    if not test_file.endswith(".test") or not (root / test_file).is_file():
                        skipped.append("{} (missing test {})".format(item["name"], test_file))
                        continue
                    row["status"] = "rewritten"
                    row["test_file"] = test_file
                    row["result_file"] = test_file[:-len(".test")] + ".result"
                elif item["decision"] == "quarantined":
                    row["status"] = "quarantined"
                else:
                    skipped.append("{} (decision {!r})".format(item["name"], item["decision"]))
                    continue
                row["note"] = clean("removed: {} | kept: {} | gray: {} | dropped: {} | {}".format(
                    item["removed"], item["kept"], item["gray_kept"], item["dropped"], item["note"]))
                applied += 1
    save(rows)
    print("merged {} rows; skipped {}: {}".format(applied, len(skipped), "; ".join(skipped)))
elif command == "add-scenarios":
    # Register new scenario files (index: name, area, behaviors, suspected_bug, note).
    index = Path(sys.argv[3]).resolve()
    added = 0
    with index.open(encoding="utf-8", newline="") as handle:
        for item in csv.DictReader(handle, delimiter="\t"):
            name = "scenario." + item["name"]
            base = index.parent.relative_to(root.resolve()) / item["name"]
            test_file, result_file = str(base) + ".test", str(base) + ".result"
            if not (root / test_file).is_file() or not (root / result_file).is_file():
                sys.exit("missing files for {}".format(item["name"]))
            note = "behaviors: {}".format(item["behaviors"])
            if item["suspected_bug"].strip():
                note += " | suspected original bug (recorded as is): {}".format(item["suspected_bug"])
            if item["note"].strip():
                note += " | {}".format(item["note"])
            row = {"name": name, "status": "scenario", "test_file": test_file,
                   "result_file": result_file, "origin": "new scenario", "note": clean(note)}
            if name in by_name:
                by_name[name].update(row)
            else:
                rows.append(row)
                by_name[name] = row
            added += 1
    rows.sort(key=lambda row: row["name"])
    save(rows)
    print("registered {} scenarios".format(added))
elif command == "renote":
    # Refresh the note of already-merged `rewritten`/`quarantined` rows from a newer shard.
    refreshed = 0
    for shard in sys.argv[3:]:
        with open(shard, encoding="utf-8", newline="") as handle:
            for item in csv.DictReader(handle, delimiter="\t"):
                row = by_name.get(item["name"])
                if row is None or row["status"] not in ("rewritten", "quarantined"):
                    continue
                row["note"] = clean("removed: {} | kept: {} | gray: {} | dropped: {} | {}".format(
                    item["removed"], item["kept"], item["gray_kept"], item["dropped"], item["note"]))
                refreshed += 1
    save(rows)
    print("refreshed {} notes".format(refreshed))
else:
    sys.exit("unknown command {}".format(command))
