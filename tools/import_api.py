#!/usr/bin/env python3
"""Import an APISurface dump into data/api/<build>/{functions,events}.jsonl.

The APISurface addon (tools/addons/APISurface/) records, on login, every global function,
every function in a C_* namespace, and everything Blizzard_APIDocumentation describes. The
client writes that to SavedVariables on logout or /reload:

  <WoW>/<product dir>/WTF/Account/<ACCOUNT>/SavedVariables/APISurface.lua

Usage:
  python tools/import_api.py path/to/APISurface.lua [--build 1.60.1.70374] [--out data/api]

The build is read from the dump (GetBuildInfo). --build, if given, must match it. The build
must already be in builds.json.

The dump is refused if third-party addons were loaded (their globals would show up as
undocumented API) unless --allow-addons is passed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from savedvariables import load  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FUNC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
EVENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def prov(ref: str, build: str) -> dict:
    return {
        "layer": "api",
        "source": {"kind": "datamine", "ref": ref},
        "build_first_seen": build,
        "build_last_seen": build,
        "confidence": "exact",
    }


def fields(raw, with_default: bool) -> list[dict] | None:
    if not raw:
        return None
    out = []
    for a in raw:
        if not isinstance(a, dict) or "name" not in a or "type" not in a:
            continue
        f = {"name": a["name"], "type": a["type"]}
        if a.get("innerType"):
            f["inner_type"] = a["innerType"]
        if a.get("mixin"):
            f["mixin"] = a["mixin"]
        if a.get("nilable"):
            f["nilable"] = True
        if with_default and "default" in a:
            f["default"] = a["default"]
        out.append(f)
    return out or None


def documentation(raw) -> str | None:
    if not raw:
        return None
    lines = [raw] if isinstance(raw, str) else [str(x) for x in raw]
    text = "\n".join(lines).strip()
    return text or None


def function_rows(funcs: dict, build: str) -> tuple[list[dict], int]:
    rows, skipped = [], 0
    for name, f in funcs.items():
        if not isinstance(name, str) or not FUNC_NAME.match(name) or not isinstance(f, dict):
            skipped += 1
            continue
        documented = bool(f.get("documented"))
        row = {"name": name}
        if f.get("namespace"):
            row["namespace"] = f["namespace"]
        for key, val in (("arguments", fields(f.get("arguments"), True)), ("returns", fields(f.get("returns"), False))):
            if val:
                row[key] = val
        row["documented"] = documented
        if documented and f.get("present") is False:
            row["present"] = False
        doc = documentation(f.get("documentation"))
        if doc:
            row["documentation"] = doc
        row.update(prov("Blizzard_APIDocumentation" if documented else "_G", build))
        rows.append(row)
    rows.sort(key=lambda r: r["name"])
    return rows, skipped


def event_rows(events: dict, build: str) -> tuple[list[dict], int]:
    rows, skipped = [], 0
    for name, e in events.items():
        if not isinstance(name, str) or not EVENT_NAME.match(name) or not isinstance(e, dict):
            skipped += 1
            continue
        row = {"name": name}
        payload = fields(e.get("payload"), False)
        if payload:
            row["payload"] = payload
        row["documented"] = True
        doc = documentation(e.get("documentation"))
        if doc:
            row["documentation"] = doc
        row.update(prov("Blizzard_APIDocumentation", build))
        rows.append(row)
    rows.sort(key=lambda r: r["name"])
    return rows, skipped


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
    print(f"  wrote {rel}: {len(rows)} rows")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("savedvariables", help="path to APISurface.lua from WTF/Account/<ACCOUNT>/SavedVariables/")
    ap.add_argument("--build", help="expected full build string; must match the dump")
    ap.add_argument("--out", default="data/api")
    ap.add_argument("--allow-addons", action="store_true", help="import even if third-party addons were loaded")
    args = ap.parse_args()

    db = load(args.savedvariables).get("APISurfaceDB")
    if not isinstance(db, dict):
        raise SystemExit(f"{args.savedvariables}: no APISurfaceDB table (log out or /reload after the dump so the client writes it)")
    if db.get("format") != 1:
        raise SystemExit(f"unknown APISurfaceDB format {db.get('format')!r}")

    build = f"{db.get('version')}.{db.get('build')}"
    if args.build and args.build != build:
        raise SystemExit(f"dump is from {build}, not {args.build}")
    registry = json.loads((REPO / "builds.json").read_text(encoding="utf-8"))
    if build not in {b["build"] for b in registry["builds"]}:
        raise SystemExit(f"{build} is not in builds.json; register it first")
    addons = db.get("thirdPartyAddOns") or []
    if addons and not args.allow_addons:
        raise SystemExit(f"third-party addons were loaded during the dump ({', '.join(addons)}); disable them and dump again, or pass --allow-addons")
    if not db.get("hasDocumentation"):
        print("warning: Blizzard_APIDocumentation did not load; every function will be undocumented and there are no events", file=sys.stderr)
    if db.get("locale") and db["locale"] != "enUS":
        print(f"warning: dump locale is {db['locale']}; names are unaffected, but check documentation strings are enUS", file=sys.stderr)

    funcs, fskip = function_rows(db.get("functions") or {}, build)
    events, eskip = event_rows(db.get("events") or {}, build)
    out = Path(args.out) if Path(args.out).is_absolute() else REPO / args.out
    print(f"build {build} (interface {db.get('interface')})")
    write_jsonl(out / build / "functions.jsonl", funcs)
    write_jsonl(out / build / "events.jsonl", events)
    documented = sum(1 for r in funcs if r["documented"])
    missing = sum(1 for r in funcs if r.get("present") is False)
    print(f"  {documented} documented ({missing} documented but not present), {len(funcs) - documented} undocumented")
    if fskip or eskip:
        print(f"  skipped {fskip} functions and {eskip} events with names outside the schema pattern")
    return 0


if __name__ == "__main__":
    sys.exit(main())
