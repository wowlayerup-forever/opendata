#!/usr/bin/env python3
"""Smoke-test a built opendata-uploader binary on the OS it was built for.

  python tests/smoke_uploader.py <path to binary> <expected version>

Runs it in a temporary home against a fake WoW install holding tests/fixtures/
OpenDataCollector.lua: --version, then a real run with --no-send. No network.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    binary, version = Path(sys.argv[1]).resolve(), sys.argv[2]
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        home = tmp / "home"
        sv = tmp / "WoW" / "_classic_" / "WTF" / "Account" / "ACC" / "SavedVariables"
        sv.mkdir(parents=True)
        home.mkdir()
        shutil.copy(REPO / "tests" / "fixtures" / "OpenDataCollector.lua", sv / "OpenDataCollector.lua")
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "OPENDATA_KEY_FILE": str(home / "key.json")}

        r = subprocess.run([str(binary), "--version"], capture_output=True, text=True, env=env, timeout=120)
        print(r.stdout.strip())
        if r.returncode or r.stdout.strip() != f"opendata-uploader {version}":
            print(r.stderr)
            return 1
        r = subprocess.run([str(binary), "--wow", str(tmp / "WoW" / "_classic_"), "--no-send", "--contributor", "local:ci"],
                           capture_output=True, text=True, env=env, timeout=120)
        print(r.stdout.strip())
        batches = list((home / ".config" / "opendata" / "batches").glob("*.json"))
        if r.returncode or "1 new" not in r.stdout or len(batches) != 1:
            print(r.stderr)
            return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
