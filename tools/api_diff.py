#!/usr/bin/env python3
"""Diff the addon API surface of two client builds.

Each side is a build string (looked up under data/api/<build>/) or a directory holding
functions.jsonl and events.jsonl, so a Classic Era dump kept elsewhere works too.

Usage:
  python tools/api_diff.py 1.60.1.70374 1.60.1.70532
  python tools/api_diff.py 1.15.9.70003 1.60.1.70374 --api-root /path/to/other/api
  python tools/api_diff.py OLD NEW --json > diff.json

Reports functions and events added, removed, changed in signature (arguments, returns,
payload), documented-but-not-present changes, and documented/undocumented flips.
Exit status is 0 whether or not there are differences.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def resolve(side: str, api_root: Path) -> Path:
    p = Path(side)
    if p.is_dir():
        return p
    if (api_root / side).is_dir():
        return api_root / side
    raise SystemExit(f"{side}: not a directory and not found under {api_root}")


def load(dir_: Path, table: str) -> dict[str, dict]:
    path = dir_ / f"{table}.jsonl"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["name"]: r for r in map(json.loads, filter(str.strip, f))}


def signature(row: dict, keys: tuple[str, ...]) -> dict:
    return {k: row.get(k) or [] for k in keys}


def fmt_fields(fields: list[dict]) -> str:
    parts = []
    for f in fields:
        t = f["type"] + (f"<{f['inner_type']}>" if f.get("inner_type") else "")
        parts.append(f"{f['name']}: {t}{'?' if f.get('nilable') else ''}")
    return ", ".join(parts)


def diff_table(old: dict, new: dict, sig_keys: tuple[str, ...]) -> dict:
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    changed, presence, documented = [], [], []
    for name in sorted(old.keys() & new.keys()):
        a, b = old[name], new[name]
        sa, sb = signature(a, sig_keys), signature(b, sig_keys)
        if sa != sb:
            changed.append({"name": name, "old": sa, "new": sb})
        pa, pb = a.get("present", True), b.get("present", True)
        if pa != pb:
            presence.append({"name": name, "old": pa, "new": pb})
        da, db = bool(a.get("documented")), bool(b.get("documented"))
        if da != db:
            documented.append({"name": name, "old": da, "new": db})
    return {"added": added, "removed": removed, "changed": changed, "present": presence, "documented": documented}


def print_table(title: str, d: dict, sig_keys: tuple[str, ...]) -> None:
    counts = ", ".join(f"{len(v)} {k}" for k, v in d.items())
    print(f"## {title}: {counts}")
    for name in d["added"]:
        print(f"+ {name}")
    for name in d["removed"]:
        print(f"- {name}")
    for c in d["changed"]:
        print(f"~ {c['name']}")
        for k in sig_keys:
            if c["old"][k] != c["new"][k]:
                print(f"    {k}: ({fmt_fields(c['old'][k])}) -> ({fmt_fields(c['new'][k])})")
    for c in d["present"]:
        print(f"{'!' if not c['new'] else '*'} {c['name']}: present {c['old']} -> {c['new']}")
    for c in d["documented"]:
        print(f"* {c['name']}: documented {c['old']} -> {c['new']}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", help="build string or directory")
    ap.add_argument("new", help="build string or directory")
    ap.add_argument("--api-root", default=str(REPO / "data" / "api"))
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    root = Path(args.api_root)
    old_dir, new_dir = resolve(args.old, root), resolve(args.new, root)
    fsig, esig = ("arguments", "returns"), ("payload",)
    result = {
        "old": args.old,
        "new": args.new,
        "functions": diff_table(load(old_dir, "functions"), load(new_dir, "functions"), fsig),
        "events": diff_table(load(old_dir, "events"), load(new_dir, "events"), esig),
    }
    if args.json:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        print(f"# API diff {args.old} -> {args.new}\n")
        print_table("functions", result["functions"], fsig)
        print_table("events", result["events"], esig)
    return 0


if __name__ == "__main__":
    sys.exit(main())
