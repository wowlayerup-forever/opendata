#!/usr/bin/env python3
"""Check and package an addon from tools/addons/ as an installable zip.

  python tools/package_addon.py OpenDataCollector                      # -> dist/OpenDataCollector-<version>.zip
  python tools/package_addon.py OpenDataCollector --expect-version 1.0.0   # fail unless the .toc says 1.0.0
  python tools/package_addon.py OpenDataCollector --check              # checks only, no zip

Checks: the .toc has Interface, Title, Version (semver) and SavedVariables; every file
the .toc lists exists; the folder holds nothing besides those files and README.md. The
zip unpacks to <Addon>/... (what Interface/AddOns/ expects), includes the repo's MIT
LICENSE, and is reproducible: fixed timestamps and order, so the same source gives the
same bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADDONS = REPO / "tools" / "addons"
SEMVER = re.compile(r"\d+\.\d+\.\d+")
REQUIRED = ("Interface", "Title", "Version", "SavedVariables")
EXTRA_OK = {"README.md"}
STAMP = (2026, 1, 1, 0, 0, 0)


def read_toc(addon: str) -> tuple[dict[str, str], list[str]]:
    toc = ADDONS / addon / f"{addon}.toc"
    if not toc.exists():
        raise SystemExit(f"{toc} not found")
    meta, files = {}, []
    for line in toc.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("##"):
            k, _, v = line[2:].partition(":")
            meta[k.strip()] = v.strip()
        elif line and not line.startswith("#"):
            files.append(line.replace("\\", "/"))
    return meta, files


def check(addon: str, expect_version: str | None) -> tuple[dict[str, str], list[str]]:
    meta, files = read_toc(addon)
    problems = [f"missing ## {k}" for k in REQUIRED if not meta.get(k)]
    if meta.get("Version") and not SEMVER.fullmatch(meta["Version"]):
        problems.append(f"Version {meta['Version']!r} is not MAJOR.MINOR.PATCH")
    if meta.get("Interface") and not meta["Interface"].replace(",", "").replace(" ", "").isdigit():
        problems.append(f"Interface {meta['Interface']!r} is not a number list")
    if expect_version and meta.get("Version") != expect_version:
        problems.append(f".toc Version is {meta.get('Version')!r} but the tag says {expect_version!r}; bump the .toc in a PR first")
    folder = ADDONS / addon
    for f in files:
        if not (folder / f).is_file():
            problems.append(f".toc lists {f}, which does not exist")
    present = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    stray = present - set(files) - EXTRA_OK - {f"{addon}.toc"}
    if stray:
        problems.append(f"files not listed in the .toc: {', '.join(sorted(stray))}")
    if problems:
        raise SystemExit(f"{addon}: " + "; ".join(problems))
    return meta, files


def package(addon: str, meta: dict[str, str], files: list[str], out_dir: Path) -> Path:
    folder = ADDONS / addon
    members = [(f"{addon}/{addon}.toc", folder / f"{addon}.toc")]
    members += [(f"{addon}/{f}", folder / f) for f in files]
    if (folder / "README.md").exists():
        members.append((f"{addon}/README.md", folder / "README.md"))
    members.append((f"{addon}/LICENSE", REPO / "LICENSE"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{addon}-{meta['Version']}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, src in sorted(members):
            info = zipfile.ZipInfo(arc, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, src.read_bytes())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("addon")
    ap.add_argument("--expect-version")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default=str(REPO / "dist"))
    args = ap.parse_args()
    meta, files = check(args.addon, args.expect_version)
    print(f"{args.addon} {meta['Version']} (interface {meta['Interface']}): .toc ok, {len(files)} file(s)")
    if args.check:
        return 0
    out = package(args.addon, meta, files, Path(args.out))
    print(f"wrote {out} sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
