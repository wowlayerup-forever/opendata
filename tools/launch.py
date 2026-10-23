#!/usr/bin/env python3
"""Launch-day sequence: register and extract a new client build, then fold in everything local.

  python tools/launch.py --dry-run                      # show what would happen
  python tools/launch.py                                 # newest build of --product (default wowf)
  python tools/launch.py --build 1.60.1.71000 --product wowf --install "/Applications/World of Warcraft/_classic_"

Steps, each the existing tool run as a subprocess (stops at the first failure):
  1. list wago.tools builds and pick the build (newest 1.x of --product unless --build)
  2. extract_client.py --build B --product P --register   (unchanged rows keep build_first_seen)
  3. fill builds.json "interface" for B from the APISurface dump, if the dump is from B
  4. import_api.py on APISurface.lua, if the dump is from B
  5. import_cache.py on <install>/Cache/WDB/enUS, if present
  6. uploader.py + reconcile.py, if OpenDataCollector SavedVariables and a contributor key exist
  7. validate.py, build_sqlite.py

It never commits, pushes or tags. At the end it prints the git and tag commands to run
after reviewing the diff; pushing a YYYY.MM.N tag starts the release workflow.

--install defaults to the WoW product directory under /Applications/World of Warcraft whose
Cache/WDB was written most recently (the one just played).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_client import list_builds  # noqa: E402
from savedvariables import load  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
WOW_ROOT = Path("/Applications/World of Warcraft")
KEY_FILE = Path.home() / ".config" / "opendata" / "contributor.json"


def build_key(b: str) -> tuple[int, ...]:
    return tuple(int(x) for x in b.split("."))


def run(args: list[str], dry: bool) -> None:
    shown = " ".join(f'"{a}"' if " " in a else a for a in args)
    print(f"\n$ {shown}")
    if dry:
        return
    r = subprocess.run([sys.executable, *args], cwd=REPO)
    if r.returncode:
        raise SystemExit(f"stopped: {args[0]} exited with {r.returncode}")


def pick_install(given: str | None) -> Path | None:
    if given:
        p = Path(given).expanduser()
        if not p.is_dir():
            raise SystemExit(f"--install {p}: not a directory")
        return p
    dirs = [d for d in WOW_ROOT.glob("_*_") if (d / "Cache" / "WDB").is_dir()] if WOW_ROOT.is_dir() else []
    if not dirs:
        return None
    return max(dirs, key=lambda d: max((f.stat().st_mtime for f in (d / "Cache" / "WDB").rglob("*.wdb")), default=0))


def api_dumps(install: Path | None) -> list[tuple[Path, dict]]:
    out = []
    if install:
        for p in sorted(install.glob("WTF/Account/*/SavedVariables/APISurface.lua")):
            db = load(p).get("APISurfaceDB")
            if isinstance(db, dict):
                out.append((p, db))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="full build string; default: newest 1.x build of --product on wago.tools")
    ap.add_argument("--product", default="wowf", help="wago product (default wowf, the likely live product)")
    ap.add_argument("--install", help="WoW product directory, e.g. /Applications/World of Warcraft/_classic_")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    args = ap.parse_args()
    dry = args.dry_run

    # 1. which build
    builds = list_builds(REPO / ".cache" / "wago")
    entries = [e for e in builds.get(args.product, []) if e.get("version", "").startswith("1.")]
    if args.build:
        build = args.build
        if not any(e["version"] == build for es in builds.values() for e in es):
            raise SystemExit(f"{build} is not listed on wago.tools")
    else:
        if not entries:
            listed = ", ".join(p for p, es in builds.items() if any(e.get("version", "").startswith("1.6") for e in es))
            raise SystemExit(f"no 1.x builds under product {args.product!r}; products with 1.6x builds: {listed}")
        build = max((e["version"] for e in entries), key=build_key)
    registry_path = REPO / "builds.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    known = {b["build"]: b for b in registry["builds"]}
    print(f"build {build} (product {args.product}){' already registered' if build in known else ''}")
    for e in sorted(entries, key=lambda e: build_key(e["version"]), reverse=True)[:5]:
        print(f"  {args.product}: {e['version']}  {e.get('created_at', '')}")

    install = pick_install(args.install)
    print(f"install: {install or 'none found'}")

    # 2. extract
    run(["tools/extract_client.py", "--build", build, "--product", args.product, "--register"], dry)

    # 3 + 4. API dump from this build
    dumps = [(p, db) for p, db in api_dumps(install) if f"{db.get('version')}.{db.get('build')}" == build]
    others = [f"{db.get('version')}.{db.get('build')}" for p, db in api_dumps(install) if (p, db) not in dumps]
    if others:
        print(f"\nAPISurface dumps from other builds ignored: {', '.join(sorted(set(others)))}")
    if dumps:
        path, db = dumps[0]
        if not dry:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            for b in registry["builds"]:
                if b["build"] == build and not b.get("interface") and db.get("interface"):
                    b["interface"] = db["interface"]
                    b["notes"] = f"{b.get('notes', '')} Interface from the APISurface dump.".strip()
                    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
                    print(f"\nbuilds.json: interface {db['interface']} for {build}")
        run(["tools/import_api.py", str(path), "--build", build], dry)
    else:
        print(f"\nno APISurface dump from {build}: log in once with the addon enabled, /reload, rerun "
              f"(builds.json interface for {build} stays empty)")

    # 5. client cache
    wdb = install / "Cache" / "WDB" / "enUS" if install else None
    if wdb and wdb.is_dir():
        run(["tools/import_cache.py", "--wdb", str(wdb)], dry)
    else:
        print("\nno Cache/WDB/enUS found; cache import skipped")

    # 6. observations
    have_sv = bool(install and list(install.glob("WTF/Account/*/SavedVariables/OpenDataCollector.lua")))
    if have_sv and KEY_FILE.exists():
        run(["tools/uploader.py", "--wow", str(install)], dry)
    elif have_sv:
        print(f"\nOpenDataCollector data found but no key at {KEY_FILE}; run tools/uploader.py once with --contributor")
    if (REPO / ".cache" / "batches").is_dir() or (have_sv and KEY_FILE.exists()):
        run(["tools/reconcile.py", ".cache/batches"], dry)

    # 7. checks
    run(["tools/validate.py"], dry)
    run(["tools/build_sqlite.py", "--version", f"launch-{build}"], dry)

    print(f"""
Done{' (dry run)' if dry else ''}. Review, then:
  git checkout -b launch/{build}
  git add builds.json data/ && git commit     # body: what was imported and from which build
  git push -u origin launch/{build} && gh pr create
After merge, spot-check a few items and quests in game, then tag the release:
  git tag YYYY.MM.N && git push origin YYYY.MM.N    # e.g. 2026.11.1; starts the release workflow""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
