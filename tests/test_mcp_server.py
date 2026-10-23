#!/usr/bin/env python3
"""End-to-end test of tools/mcp_server.py over stdio.

Builds a SQLite file from data/client plus a few world rows written here (real IDs, so the
cross-layer joins have something to find), starts the server as a subprocess, and talks
MCP to it: handshake, tool list, every tool, the read-only SQL guard, protocol errors.

  python tests/test_mcp_server.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / ".cache" / "tests" / "mcp"
failures: list[str] = []


def check(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        failures.append(what)


def world_rows() -> dict[str, list[dict]]:
    p = {"layer": "world", "source": {"kind": "observation", "ref": "reconciler:v1"},
         "build_first_seen": "1.60.1.70374", "build_last_seen": "1.60.1.70374", "confidence": "verified"}
    return {
        "loot": [{"holder_kind": "npc", "holder_id": 568, "item_id": 2589, "drops": 47, "kills": 120, "count_min": 1, "count_max": 2, **p},
                 {"holder_kind": "skinning", "holder_id": 568, "item_id": 2318, "drops": 30, "kills": 31, "count_min": 1, "count_max": 1, **p},
                 {"holder_kind": "object", "holder_id": 1731, "item_id": 2770, "drops": 12, "kills": 12, "count_min": 1, "count_max": 3, **p}],
        "npc_levels": [{"npc_id": 568, "level": 19, "n": 7, **p}, {"npc_id": 568, "level": 20, "n": 5, **p}],
        "vendors": [{"npc_id": 1682, "item_id": 159, "n": 3, "windows": 3, "price": 25, **p}],
        "quest_givers": [{"quest_id": 303, "role": "start", "holder_kind": "npc", "holder_id": 1682,
                          "coords": {"map_id": 0, "x": 49.8, "y": 18.3}, "n": 2, **p}],
        "recipe_sources": [{"recipe_spell_id": 2660, "source_kind": "trainer", "source_id": 1682, "cost": 100, "n": 1, **p}],
        "spawns": [{"holder_kind": "object", "holder_id": 1731, "coords": {"map_id": 0, "zone_id": 11, "x": 55.0, "y": 41.0}, "n": 4, **p}],
    }


def build_db() -> Path:
    root = OUT / "root"
    (root / "world").mkdir(parents=True)
    # a real directory, not a symlink: validate.py and build_sqlite.py do not follow symlinked dirs
    shutil.copytree(REPO / "data" / "client", root / "client", copy_function=os.link)
    for table, rows in world_rows().items():
        (root / "world" / f"{table}.jsonl").write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))
    db = OUT / "opendata.sqlite"
    r = subprocess.run([sys.executable, "tools/validate.py", "--root", str(root.relative_to(REPO))], cwd=REPO, capture_output=True, text=True)
    check(r.returncode == 0, "test dataset validates")
    subprocess.run([sys.executable, "tools/build_sqlite.py", "--root", str(root.relative_to(REPO)), "--out", str(db),
                    "--version", "test.1"], cwd=REPO, check=True, capture_output=True)
    return db


class Client:
    def __init__(self, db: Path):
        self.p = subprocess.Popen([sys.executable, "tools/mcp_server.py", "--db", str(db)], cwd=REPO,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.next_id = 0

    def send_raw(self, line: str) -> dict | None:
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        return self.send_raw(json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}))

    def notify(self, method: str) -> None:
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.p.stdin.flush()

    def call(self, name: str, **args) -> dict:
        return self.request("tools/call", {"name": name, "arguments": args})["result"]

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=10)


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    print("MCP server")
    db = build_db()
    c = Client(db)

    init = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "0"}})["result"]
    check(init["protocolVersion"] == "2025-06-18", "negotiates the client's protocol version")
    check(init["serverInfo"]["name"] == "opendata" and "cite" in init["instructions"], "server info and instructions")
    c.notify("notifications/initialized")
    check(c.request("ping")["result"] == {}, "ping after a notification (notifications get no reply)")
    tools = {t["name"]: t for t in c.request("tools/list")["result"]["tools"]}
    check(set(tools) == {"dataset_info", "search", "get_item", "get_npc", "get_quest", "get_object", "get_recipe", "query_sql"},
          "eight tools listed")
    check(all(t["annotations"]["readOnlyHint"] and t["inputSchema"]["type"] == "object" for t in tools.values()),
          "every tool read-only with an object input schema")

    info = c.call("dataset_info")["structuredContent"]
    check(info["dataset_version"] == "test.1" and info["row_counts"]["client_items"] > 30000, "dataset_info: version and counts")

    s = c.call("search", query="linen cloth", kind="item")["structuredContent"]["results"]
    check(s and s[0]["id"] == 2589 and s[0]["name"] == "Linen Cloth", "search: exact name first, case-insensitive")
    s = c.call("search", query="303", kind="quest")["structuredContent"]["results"]
    check(s and s[0]["name"] == "The Dark Iron War", "search: numeric query is an ID")
    s = c.call("search", query="100%_x")["structuredContent"]["results"]
    check(s == [], "search: LIKE wildcards in the query are literal")

    it = c.call("get_item", item_id=2589)["structuredContent"]
    drop = it["dropped_by"][0] if it["dropped_by"] else {}
    check(drop.get("holder_id") == 568 and drop.get("holder_name") == "Shadowhide Warrior", "get_item: dropped_by with holder name")
    check(drop.get("cite") == "opendata test.1, world/loot npc 568 item 2589 (verified, 47/120, build 1.60.1.70374)",
          f"get_item: CONVENTIONS section 7 citation ({drop.get('cite')})")
    check("rate" not in json.dumps(it["dropped_by"]), "get_item: counts only, no rate")
    check(any(r["name"] == "Linen Bandage" for r in it["reagent_in"]), "get_item: recipes that use it")
    check(it["item"]["cite"].startswith("opendata test.1, client/items item 2589 (exact"), "get_item: item row cited")

    npc = c.call("get_npc", npc_id=568)["structuredContent"]
    check([lv["level"] for lv in npc["levels_observed"]] == [19, 20], "get_npc: observed levels")
    check({r["holder_kind"] for r in npc["loot"]} == {"npc", "skinning"}, "get_npc: normal and skinning loot")
    check(any(r["item_id"] == 2318 and r.get("item_name") for r in npc["loot"]), "get_npc: loot with item names")

    q = c.call("get_quest", quest_id=303)["structuredContent"]
    check(q["quest"]["rewards"]["xp"] == 2450 and q["quest"]["name"] == "The Dark Iron War", "get_quest: rewards")
    check(q["givers"] and q["givers"][0]["holder_name"] == "Yanni Stoutheart", "get_quest: giver with name")
    check(all(o.get("name") is None or isinstance(o["name"], str) for o in q["quest"].get("objective_npcs", [])), "get_quest: objective NPC names (may be unseen)")

    o = c.call("get_object", object_id=1731)["structuredContent"]
    check(o["object"]["required_skill_id"] == 186 and o["object"]["lock_type"] == 3, "get_object: Copper Vein lock and skill")
    check(o["loot"][0]["item_id"] == 2770 and o["spawns"][0]["coords"]["zone_id"] == 11, "get_object: loot and spawns")

    r = c.call("get_recipe", recipe_spell_id=2660)["structuredContent"]
    check(r["recipe"]["creates"]["name"] == "Rough Sharpening Stone", "get_recipe: created item name")
    check(r["learned_from"][0]["source_name"] == "Yanni Stoutheart", "get_recipe: trainer with name")

    res = c.call("query_sql", sql="SELECT count(*) AS n FROM client_quests")["structuredContent"]
    check(res["columns"] == ["n"] and res["rows"] == [[46]], "query_sql: SELECT works")
    res = c.call("query_sql", sql="SELECT item_id FROM client_items", limit=5)["structuredContent"]
    check(len(res["rows"]) == 5 and res["truncated"], "query_sql: row limit and truncation flag")
    for bad, what in (("DELETE FROM client_items", "DELETE"), ("UPDATE client_items SET name = 'x'", "UPDATE"),
                      ("ATTACH DATABASE '/tmp/x.db' AS x", "ATTACH"), ("PRAGMA writable_schema = 1", "PRAGMA"),
                      ("SELECT 1; DELETE FROM meta", "two statements"),
                      ("SELECT count(*) FROM client_items a, client_items b", "runaway cross join")):
        out = c.call("query_sql", sql=bad)
        check(out.get("isError") is True, f"query_sql refuses {what}")
    res = c.call("query_sql", sql="SELECT count(*) FROM client_items")["structuredContent"]
    check(res["rows"][0][0] > 30000, "database unchanged and usable after refused queries")

    check(c.call("get_item", item_id=999999999).get("isError") is True, "unknown ID is a tool error, not a crash")
    check(c.call("get_item").get("isError") is True, "missing argument is a tool error")
    check("error" in c.request("tools/call", {"name": "nope", "arguments": {}}), "unknown tool is a JSON-RPC error")
    check(c.request("nope/method")["error"]["code"] == -32601, "unknown method -> -32601")
    check(c.send_raw("{not json")["error"]["code"] == -32700, "malformed JSON -> -32700")
    c.close()

    c = Client(OUT / "missing.sqlite")
    c.request("initialize", {"protocolVersion": "2099-01-01"})
    out = c.call("dataset_info")
    check(out.get("isError") is True and "build_sqlite" in out["content"][0]["text"], "missing database: clear tool error")
    c.close()

    print(f"\n{'all checks passed' if not failures else f'{len(failures)} checks failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
