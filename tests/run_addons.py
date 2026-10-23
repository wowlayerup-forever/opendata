#!/usr/bin/env python3
"""Run both addons under LuaJIT against tests/wowmock.lua and push their SavedVariables
through the real Python pipeline.

  python tests/run_addons.py            # needs luajit on PATH (brew install luajit / apt install luajit)

OpenDataCollector: two login sessions -> uploader.py -> reconcile.py -> validate.py, with
assertions on the world rows (loot windows, skinning, mining, fishing, bag items, vendor,
quest givers, trainer recipes, levels). Then /odc reset.
APISurface: a login with documentation -> import_api.py (refused with another addon loaded,
accepted with --allow-addons) -> validate.py; and a login without documentation.

Everything is written under .cache/tests/ (git-ignored). Exit status 1 on any failure.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / ".cache" / "tests"
LUAJIT = os.environ.get("LUAJIT") or shutil.which("luajit")
failures: list[str] = []


def check(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        failures.append(what)


def lua(*args: str) -> None:
    r = subprocess.run([LUAJIT, *args], cwd=REPO, capture_output=True, text=True)
    if r.returncode:
        print(r.stdout + r.stderr)
        raise SystemExit(f"luajit {' '.join(args)} failed")


def tool(*args: str, env: dict | None = None, expect_fail: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run([sys.executable, *args], cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    if bool(r.returncode) != expect_fail:
        print(r.stdout + r.stderr)
        raise SystemExit(f"{args[0]} {'succeeded' if expect_fail else 'failed'} unexpectedly")
    return r


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def validate(root: Path) -> None:
    r = tool("tools/validate.py", "--root", str(root.relative_to(REPO)))
    summary = r.stdout.strip().splitlines()[-1]
    check(" 0 errors" in summary, f"validate {root.name}: {summary}")


def collector() -> None:
    print("OpenDataCollector")
    d = OUT / "collector"
    sv, keys, batches, root = d / "OpenDataCollector.lua", d / "keys", d / "batches", d / "root"
    for p in (keys, batches, root / "world"):
        p.mkdir(parents=True)
    (root / "client").symlink_to(REPO / "data" / "client")
    env = {"OPENDATA_KEY_FILE": str(keys / "harness.json")}

    lua("tests/scenario_collector.lua", str(sv), "1")
    lua("tests/scenario_collector.lua", str(sv), "2")
    r = tool("tools/uploader.py", str(sv), "--out", str(batches), "--contributor", "github:harness", env=env)
    check("2 new" in r.stdout, "uploader writes one batch per session (2)")
    r = tool("tools/uploader.py", str(sv), "--out", str(batches), env=env)
    check("0 new and 0 grown" in r.stdout, "uploader re-run writes nothing")
    # the one real UiMapAssignment row the scenario needs, so CI does not depend on .cache/wago
    tool("tools/reconcile.py", str(batches), "--keys", str(keys), "--out", str(root / "world"),
         "--wago-cache", "tests/fixtures/wago")

    w = root / "world"
    loot = {(r["holder_kind"], r["holder_id"], r["item_id"]): r for r in rows(w / "loot.jsonl")}
    cloth = loot.get(("npc", 568, 2589), {})
    check(cloth.get("kills") == 4 and cloth.get("drops") == 4, f"568 cloth: 4 drops in 4 windows across sessions (got {cloth.get('drops')}/{cloth.get('kills')})")
    check((cloth.get("count_min"), cloth.get("count_max")) == (1, 3), "568 cloth count range 1..3 (AoE slot split per corpse)")
    check(loot.get(("npc", 568, 1075), {}).get("drops") == 1, "reopened corpse counted once")
    check(("skinning", 568, 2318) in loot, "skinning loot attributed after the Skinning cast")
    check(("object", 1731, 2770) in loot, "mining node loot under the Copper Vein object")
    check(("fishing", 11, 6303) in loot, "fishing loot keyed by zone 11 (Wetlands)")
    check(("item", 5523, 5498) in loot, "clam loot attributed through the UseContainerItem hook")

    vend = {r["item_id"]: r for r in rows(w / "vendors.jsonl") if r["npc_id"] == 1682}
    check(vend.get(159, {}).get("price") == 25, "vendor price")
    check(vend.get(4470, {}).get("limited_stock") == 5, "vendor limited stock")
    check(vend.get(2901, {}).get("extended_cost") == [{"item_id": 2589, "count": 3}], "vendor extended cost")
    qg = {(r["quest_id"], r["role"], r["holder_id"]) for r in rows(w / "quest_givers.jsonl")}
    check({(303, "start", 1682), (303, "end", 1682)} <= qg, "quest 303 start and end at the NPC in the quest frame")
    rs = {(r["recipe_spell_id"], r["source_id"], r.get("cost")) for r in rows(w / "recipe_sources.jsonl")}
    check((2660, 1682, 100) in rs, "trainer recipe matched by created item")
    check((2657, 1682, 50) in rs, "trainer recipe matched by name when the item is ambiguous")
    lv = {(r["npc_id"], r["level"]): r["n"] for r in rows(w / "npc_levels.jsonl")}
    check(lv == {(568, 19): 1, (568, 20): 1}, f"levels: one per spawn, skull ignored (got {lv})")
    check(len(rows(w / "spawns.jsonl")) >= 2, "spawn points recorded")
    validate(root)

    lua("tests/scenario_collector.lua", str(sv), "reset")
    r = tool("tools/uploader.py", str(sv), "--out", str(batches), env=env)
    check("1 empty" in r.stdout and "0 conflicts" in r.stdout, "after /odc reset: one empty batch, no conflicts")


def apisurface() -> None:
    print("APISurface")
    d = OUT / "api"
    root = d / "root"
    (root / "api").mkdir(parents=True)
    (root / "client").symlink_to(REPO / "data" / "client")
    sv = d / "APISurface.lua"

    lua("tests/scenario_apisurface.lua", str(sv))
    r = tool("tools/import_api.py", str(sv), "--out", str(root / "api"), expect_fail=True)
    check("OpenDataCollector" in (r.stdout + r.stderr), "import refuses a dump made with another addon loaded")
    tool("tools/import_api.py", str(sv), "--out", str(root / "api"), "--allow-addons")
    base = root / "api" / "1.60.1.70374"
    fn = {r["name"]: r for r in rows(base / "functions.jsonl")}
    ev = {r["name"]: r for r in rows(base / "events.jsonl")}
    gp = fn.get("C_Map.GetPlayerMapPosition", {})
    check(gp.get("documented") is True and "present" not in gp, "documented, present function")
    check(gp.get("arguments", [{}, {}])[1].get("default") == "player", "argument default kept")
    check(gp.get("returns", [{}])[0].get("mixin") == "Vector2DMixin", "return mixin kept")
    check("quote" in gp.get("documentation", ""), "documentation lines joined")
    check(fn.get("C_Map.NotInThisFlavor", {}).get("present") is False, "documented but missing -> present false")
    check(fn.get("GetLootSourceInfo", {}).get("documented") is True, "documented global function (no namespace)")
    check(fn.get("C_Undocumented.DoThing", {}).get("documented") is False, "undocumented C_ namespace function")
    check("C_Undocumented.notAFunction" not in fn, "non-function namespace members skipped")
    check(ev.get("LOOT_READY", {}).get("payload", [{}])[0].get("inner_type") == "number", "event payload inner type")
    check(set(ev) == {"LOOT_OPENED", "LOOT_READY", "NEW_WMO_CHUNK"}, "all documented events")
    validate(root)

    sv2 = d / "APISurface-nodocs.lua"
    lua("tests/scenario_apisurface.lua", str(sv2), "nodocs")
    out2 = d / "nodocs"
    r = tool("tools/import_api.py", str(sv2), "--out", str(out2), "--allow-addons")
    check("did not load" in r.stderr, "warns when Blizzard_APIDocumentation did not load")
    check(not rows(out2 / "1.60.1.70374" / "events.jsonl"), "no events without documentation")


def main() -> int:
    if not LUAJIT:
        print("luajit not found (brew install luajit / apt-get install luajit, or set LUAJIT)")
        return 2
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    collector()
    apisurface()
    print(f"\n{'all checks passed' if not failures else f'{len(failures)} checks failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
