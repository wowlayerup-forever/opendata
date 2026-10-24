#!/usr/bin/env python3
"""opendata uploader for players: the app version of tools/uploader.py.

Double-click it (or run it with no arguments) and it
  1. imports a contributor.json lying next to it into ~/.config/opendata/ (once),
  2. finds World of Warcraft (usual install places, or asks once and remembers),
  3. reads OpenDataCollector's SavedVariables for every account and product folder,
  4. writes the batches to ~/.config/opendata/batches/ and sends them to the
     ingest service if the key file names one,
  5. prints a summary and waits for Enter so the window stays open.

With arguments it behaves like tools/uploader.py (same flags), for example
  opendata-uploader --wow "D:/Games/World of Warcraft/_classic_"
  opendata-uploader --version

Nothing here reads or sends anything except the collector's SavedVariables, which hold
Blizzard IDs, counts, prices, levels and map positions.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uploader  # noqa: E402

try:
    from _build_info import VERSION  # written by the release build
except ImportError:
    VERSION = "dev"

HOME = Path.home()
CONFIG = HOME / ".config" / "opendata"
SETTINGS = CONFIG / "settings.json"
KEY_FILE = Path(os.environ.get("OPENDATA_KEY_FILE", CONFIG / "contributor.json"))


def app_dir() -> Path:
    """Folder the program sits in (the executable when frozen, else this file)."""
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def candidate_roots() -> list[Path]:
    roots = []
    if sys.platform == "win32":
        for drive in "CDEFG":
            for base in (r"Program Files (x86)", r"Program Files", r"Games", ""):
                roots.append(Path(f"{drive}:/") / base / "World of Warcraft")
    elif sys.platform == "darwin":
        roots += [Path("/Applications/World of Warcraft"), HOME / "Applications" / "World of Warcraft"]
    else:  # Wine, Lutris, Bottles
        for prefix in (HOME / ".wine", HOME / "Games" / "world-of-warcraft", HOME / "Games" / "battlenet"):
            roots += [prefix / "drive_c" / "Program Files (x86)" / "World of Warcraft",
                      prefix / "drive_c" / "Program Files" / "World of Warcraft"]
    return roots


def product_dirs(root: Path) -> list[Path]:
    """Product folders (_classic_, _retail_, ...) under a WoW root, or the folder itself."""
    if (root / "WTF").is_dir():
        return [root]
    return sorted(d for d in root.glob("_*_") if (d / "WTF").is_dir()) if root.is_dir() else []


def with_collector_data(dirs: list[Path]) -> list[Path]:
    return [d for d in dirs if uploader.find_savedvariables(d)]


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(s: dict) -> None:
    CONFIG.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2), encoding="utf-8")


def import_key() -> None:
    """A contributor.json dropped next to the app is moved into place once (mode 600)."""
    beside = app_dir() / "contributor.json"
    if not beside.exists() or KEY_FILE.exists():
        return
    try:
        key = json.loads(beside.read_text(encoding="utf-8"))
        assert key.get("contributor") and key.get("key")
    except (OSError, ValueError, AssertionError):
        print(f"! {beside} is not a contributor key file; ignoring it")
        return
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(beside, KEY_FILE)
    os.chmod(KEY_FILE, 0o600)
    beside.unlink()
    print(f"Imported your contributor key ({key['contributor']}) into {KEY_FILE}")


def find_installs(interactive: bool) -> list[Path]:
    settings = load_settings()
    remembered = [Path(p) for p in settings.get("wow_dirs", [])]
    found = with_collector_data([d for r in remembered for d in product_dirs(r)])
    if found:
        return found
    found = with_collector_data([d for r in candidate_roots() for d in product_dirs(r)])
    if found:
        return found
    if not interactive:
        return []
    print("Could not find World of Warcraft with OpenDataCollector data in the usual places.")
    while True:
        entry = input("Paste the path of your World of Warcraft folder (or press Enter to quit): ").strip().strip('"')
        if not entry:
            return []
        dirs = with_collector_data(product_dirs(Path(entry).expanduser()))
        if dirs:
            settings["wow_dirs"] = sorted({*settings.get("wow_dirs", []), str(Path(entry).expanduser())})
            save_settings(settings)
            return dirs
        print(f"No OpenDataCollector data under {entry}. Has the addon run, and did you log out once?")


def main(argv: list[str]) -> int:
    uploader.USER_AGENT = f"opendata-uploader/{VERSION}"
    if argv and argv[0] in ("--version", "-V"):
        print(f"opendata-uploader {VERSION}")
        return 0
    if argv:  # same flags as tools/uploader.py
        if not any(a == "--out" or a.startswith("--out=") for a in argv):
            argv = [*argv, "--out", str(CONFIG / "batches")]
        return uploader.main(argv)

    interactive = sys.stdin is not None and sys.stdin.isatty()
    print(f"opendata uploader {VERSION}\n")
    import_key()
    installs = find_installs(interactive)
    if not installs:
        print("Nothing to upload: no OpenDataCollector SavedVariables found.")
        return 1
    if not KEY_FILE.exists():
        print("No contributor key yet. Ask the opendata maintainers for a key file and put contributor.json\n"
              f"next to this program (or at {KEY_FILE}). Batches are still saved locally.\n")
        uploader.KEY_FILE = CONFIG / "local-key.json"  # local-only key so batches can be signed and kept
        if not uploader.KEY_FILE.exists():
            uploader.load_key("local:player")  # never sent anywhere; no real name in it
    else:
        uploader.KEY_FILE = KEY_FILE
    for d in installs:
        print(f"Reading {d}")
    print()
    code = uploader.main([*sum((["--wow", str(d)] for d in installs), []), "--out", str(CONFIG / "batches")])
    print("\nDone." if code == 0 else "\nSomething went wrong; see above.")
    return code


if __name__ == "__main__":
    interactive_start = len(sys.argv) == 1
    try:
        rc = main(sys.argv[1:])
    except SystemExit as e:  # uploader errors are SystemExit with a message
        if e.code not in (None, 0) and not isinstance(e.code, int):
            print(f"\nError: {e.code}")
            rc = 1
        else:
            rc = e.code or 0
    except KeyboardInterrupt:
        rc = 130
    if interactive_start and sys.stdin is not None and sys.stdin.isatty():
        try:
            input("\nPress Enter to close.")
        except EOFError:
            pass
    sys.exit(rc)
