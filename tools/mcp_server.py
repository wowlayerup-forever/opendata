#!/usr/bin/env python3
"""MCP server over the opendata SQLite release: answers item, NPC, quest, object and recipe
questions with citations. Standard library only; read-only.

  python tools/build_sqlite.py                       # builds dist/opendata.sqlite from data/
  python tools/mcp_server.py [--db dist/opendata.sqlite]

Speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0). Register it
with an MCP client, for example in Claude Code:

  claude mcp add opendata -- python /path/to/opendata/tools/mcp_server.py --db /path/to/opendata.sqlite

The repo's .mcp.json registers it for anyone who opens the repo in Claude Code.

Tools:
  dataset_info   version, builds, row counts, what the data does and does not cover
  search         find items, NPCs, quests, objects, spells, recipes by name or ID
  get_item       an item with who drops it, sells it, crafts it, uses it, rewards it
  get_npc        a creature with its levels, loot, wares, quests, spawns, trainer recipes
  get_quest      a quest with who starts and ends it and its chain neighbours
  get_object     an object (chest, herb, ore) with its loot, spawns and quests
  get_recipe     a recipe with what it makes and uses and where it is learned
  query_sql      a read-only SELECT over all tables, for anything the others do not cover

Every row comes with a citation in the CONVENTIONS section 7 form, for example
"opendata 2026.11.1, world/loot npc 568 item 2589 (verified, 4/4)". Facts carry counts,
not rates: a drop rate is drops/kills and is for the reader to compute and qualify.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = {"name": "opendata", "version": "1.0.0"}
PROTOCOLS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
SQL_ROW_LIMIT = 500
SQL_STEP_BUDGET = 20_000_000  # VM steps before a query is aborted
LIST_LIMIT = 50               # rows per cross-reference list

KINDS = {  # search kind -> (table, id column)
    "item": ("client_items", "item_id"),
    "npc": ("client_npcs", "npc_id"),
    "quest": ("client_quests", "quest_id"),
    "object": ("client_objects", "object_id"),
    "spell": ("client_spells", "spell_id"),
    "recipe": ("client_recipes", "recipe_spell_id"),
}
HOLDER_TABLE = {"npc": "npc", "skinning": "npc", "pickpocket": "npc", "object": "object", "item": "item"}


def build_key(b: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in (b or "").split("."))
    except ValueError:
        return ()


class ToolError(Exception):
    pass


class Data:
    def __init__(self, path: Path):
        self.path = path
        self.con: sqlite3.Connection | None = None
        self.meta: dict[str, str] = {}
        self.columns: dict[str, dict[str, str]] = {}

    def open(self) -> sqlite3.Connection:
        if self.con is None:
            if not self.path.exists():
                raise ToolError(f"{self.path} does not exist; build it with `python tools/build_sqlite.py` "
                                "or download a release from github.com/wowlayerup-forever/opendata/releases")
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only = ON")
            self.meta = {k: v for k, v in con.execute("SELECT key, value FROM meta")}
            for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
                self.columns[t] = {r["name"]: r["type"] for r in con.execute(f'PRAGMA table_info("{t}")')}
            self.con = con
        return self.con

    @property
    def version(self) -> str:
        self.open()
        return self.meta.get("dataset_version", "dev")

    # ── rows ──────────────────────────────────────────────────────────
    def decode(self, table: str, row: sqlite3.Row) -> dict:
        out = {}
        for k in row.keys():
            v = row[k]
            if v is None:
                continue
            if self.columns[table].get(k) == "TEXT" and isinstance(v, str) and v[:1] in "[{":
                try:
                    v = json.loads(v)
                except ValueError:
                    pass
            elif self.columns[table].get(k) == "INTEGER" and k in BOOLEAN_COLUMNS:
                v = bool(v)
            out[k] = v
        return out

    def select(self, table: str, where: str, params: tuple, order: str = "", limit: int = LIST_LIMIT) -> list[dict]:
        sql = f'SELECT * FROM "{table}" WHERE {where}' + (f" ORDER BY {order}" if order else "") + f" LIMIT {int(limit)}"
        return [self.decode(table, r) for r in self.open().execute(sql, params)]

    def latest(self, table: str, key: str, value: int) -> dict | None:
        rows = self.select(table, f'"{key}" = ?', (value,), limit=100)
        return max(rows, key=lambda r: build_key(r.get("build_last_seen"))) if rows else None

    def name_of(self, kind: str, id_: int | None) -> str | None:
        if id_ is None or kind not in KINDS:
            return None
        table, key = KINDS[kind]
        r = self.open().execute(f'SELECT name FROM "{table}" WHERE "{key}" = ? AND name IS NOT NULL LIMIT 1', (id_,)).fetchone()
        return r["name"] if r else None

    # ── citations ─────────────────────────────────────────────────────
    def cite(self, table: str, row: dict) -> str:
        layer, _, name = table.partition("_")
        if table == "world_loot":
            key = f"{row['holder_kind']} {row['holder_id']} item {row['item_id']}"
        elif table == "world_vendors":
            key = f"npc {row['npc_id']} item {row['item_id']}"
        elif table == "world_quest_givers":
            key = f"quest {row['quest_id']} {row['role']} {row['holder_kind']} {row['holder_id']}"
        elif table == "world_recipe_sources":
            key = f"recipe {row['recipe_spell_id']} {row['source_kind']} {row['source_id']}"
        elif table == "world_npc_levels":
            key = f"npc {row['npc_id']} level {row['level']}"
        elif table == "world_spawns":
            c = row.get("coords") or {}
            key = f"{row['holder_kind']} {row['holder_id']} at {c.get('map_id')}:{c.get('x')},{c.get('y')}"
        elif name[:-1] in KINDS:  # client/items -> "item 2589", client/recipes -> "recipe 2660"
            key = f"{name[:-1]} {row.get(KINDS[name[:-1]][1])}"
        else:  # api/functions, api/events
            key = str(row.get("name"))
        counts = ""
        if "drops" in row and "kills" in row:
            counts = f", {row['drops']}/{row['kills']}"
        elif "n" in row:
            counts = f", n={row['n']}"
        build = row.get("build_last_seen")
        return f"opendata {self.version}, {layer}/{name} {key} ({row.get('confidence', '?')}{counts}{', build ' + build if build else ''})"

    def cited(self, table: str, rows: list[dict]) -> list[dict]:
        return [{**r, "cite": self.cite(table, r)} for r in rows]


BOOLEAN_COLUMNS = {"sparse", "documented", "present", "protected", "repeatable", "skinning_loot"}


# ───────────────────────────── tools ─────────────────────────────

def t_dataset_info(d: Data, args: dict) -> dict:
    con = d.open()
    counts = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in sorted(d.columns) if t != "meta"}
    return {
        "dataset_version": d.version,
        "meta": {k: (json.loads(v) if v[:1] in "[{" else v) for k, v in d.meta.items()},
        "row_counts": counts,
        "coverage": COVERAGE,
        "citation_format": "opendata <version>, <layer>/<table> <key> (<confidence>, <counts>)",
    }


def t_search(d: Data, args: dict) -> dict:
    q = str(args.get("query", "")).strip()
    if not q:
        raise ToolError("query is required")
    kinds = [args["kind"]] if args.get("kind") else list(KINDS)
    for k in kinds:
        if k not in KINDS:
            raise ToolError(f"unknown kind {k!r}; one of {', '.join(KINDS)}")
    limit = max(1, min(int(args.get("limit", 20)), 100))
    hits = []
    con = d.open()
    for k in kinds:
        table, key = KINDS[k]
        if q.isdigit():
            sql = f'SELECT "{key}" AS id, name, build_last_seen FROM "{table}" WHERE "{key}" = ? AND name IS NOT NULL'
            params: tuple = (int(q),)
        else:
            sql = (f"SELECT \"{key}\" AS id, name, build_last_seen FROM \"{table}\" WHERE name LIKE ? ESCAPE '\\' "
                   f"ORDER BY (lower(name) = lower(?)) DESC, (name LIKE ? ESCAPE '\\') DESC, length(name), \"{key}\" LIMIT ?")
            like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params = (f"%{like}%", q, f"{like}%", limit)
        seen = set()
        for r in con.execute(sql, params):
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            hits.append({"kind": k, "id": r["id"], "name": r["name"], "build_last_seen": r["build_last_seen"]})
    hits.sort(key=lambda h: (h["name"].lower() != q.lower(), not h["name"].lower().startswith(q.lower()), len(h["name"])))
    return {"query": q, "results": hits[:limit], "truncated": len(hits) > limit}


def need_int(args: dict, key: str) -> int:
    try:
        return int(args[key])
    except (KeyError, TypeError, ValueError):
        raise ToolError(f"{key} (integer) is required")


def with_names(d: Data, rows: list[dict], fields: dict[str, str]) -> list[dict]:
    """fields: id column -> kind (or 'holder' to use holder_kind)."""
    for r in rows:
        for col, kind in fields.items():
            k = HOLDER_TABLE.get(r.get("holder_kind", ""), "") if kind == "holder" else kind
            n = d.name_of(k, r.get(col))
            if n:
                r[col.replace("_id", "") + "_name"] = n
    return rows


def quests_where(d: Data, json_path_sql: str, id_: int) -> list[dict]:
    sql = (f"SELECT * FROM client_quests WHERE EXISTS (SELECT 1 FROM json_each({json_path_sql}) "
           f"WHERE json_extract(value, '$.item_id') = ? OR json_extract(value, '$.npc_id') = ?) LIMIT {LIST_LIMIT}")
    return [d.decode("client_quests", r) for r in d.open().execute(sql, (id_, id_))]


def brief_quests(d: Data, rows: list[dict]) -> list[dict]:
    return [{"quest_id": q["quest_id"], "name": q.get("name"), "quest_level": q.get("quest_level"),
             "cite": d.cite("client_quests", q)} for q in rows]


def t_get_item(d: Data, args: dict) -> dict:
    item_id = need_int(args, "item_id")
    item = d.latest("client_items", "item_id", item_id)
    if not item:
        raise ToolError(f"no item {item_id} in opendata {d.version}")
    item["cite"] = d.cite("client_items", item)
    created_by = d.select("client_recipes", "json_extract(creates, '$.item_id') = ?", (item_id,))
    reagent_in = [d.decode("client_recipes", r) for r in d.open().execute(
        f"SELECT * FROM client_recipes WHERE EXISTS (SELECT 1 FROM json_each(reagents) "
        f"WHERE json_extract(value, '$.item_id') = ?) LIMIT {LIST_LIMIT}", (item_id,))]
    teaches = d.select("client_recipes", "taught_by_item_id = ?", (item_id,))
    return {
        "item": item,
        "dropped_by": d.cited("world_loot", with_names(d, d.select("world_loot", "item_id = ?", (item_id,),
                                                                   "drops * 1.0 / max(kills, 1) DESC"), {"holder_id": "holder"})),
        "sold_by": d.cited("world_vendors", with_names(d, d.select("world_vendors", "item_id = ?", (item_id,)), {"npc_id": "npc"})),
        "created_by": d.cited("client_recipes", created_by),
        "reagent_in": d.cited("client_recipes", reagent_in),
        "teaches_recipe": d.cited("client_recipes", teaches),
        "quest_reward": brief_quests(d, quests_where(d, "json_extract(rewards, '$.items')", item_id)
                                     + quests_where(d, "json_extract(rewards, '$.choice_items')", item_id)),
        "quest_objective": brief_quests(d, quests_where(d, "objective_items", item_id)),
    }


def t_get_npc(d: Data, args: dict) -> dict:
    npc_id = need_int(args, "npc_id")
    npc = d.latest("client_npcs", "npc_id", npc_id)
    if not npc:
        raise ToolError(f"no NPC {npc_id} in opendata {d.version} (client/npcs only holds creatures some player has seen)")
    npc["cite"] = d.cite("client_npcs", npc)
    levels = d.select("world_npc_levels", "npc_id = ?", (npc_id,), "level")
    givers = d.select("world_quest_givers", "holder_kind = 'npc' AND holder_id = ?", (npc_id,))
    return {
        "npc": npc,
        "levels_observed": d.cited("world_npc_levels", levels),
        "loot": d.cited("world_loot", with_names(d, d.select(
            "world_loot", "holder_kind IN ('npc', 'skinning', 'pickpocket') AND holder_id = ?", (npc_id,),
            "holder_kind, drops * 1.0 / max(kills, 1) DESC"), {"item_id": "item"})),
        "sells": d.cited("world_vendors", with_names(d, d.select("world_vendors", "npc_id = ?", (npc_id,)), {"item_id": "item"})),
        "teaches": d.cited("world_recipe_sources", with_names(d, d.select(
            "world_recipe_sources", "source_kind = 'trainer' AND source_id = ?", (npc_id,)), {"recipe_spell_id": "recipe"})),
        "quests": d.cited("world_quest_givers", with_names(d, givers, {"quest_id": "quest"})),
        "quest_objective": brief_quests(d, quests_where(d, "objective_npcs", npc_id)),
        "spawns": d.cited("world_spawns", d.select("world_spawns", "holder_kind = 'npc' AND holder_id = ?", (npc_id,), "n DESC")),
    }


def t_get_quest(d: Data, args: dict) -> dict:
    quest_id = need_int(args, "quest_id")
    q = d.latest("client_quests", "quest_id", quest_id)
    if not q:
        raise ToolError(f"no quest {quest_id} in opendata {d.version} (client/quests only holds quests some player has seen)")
    q["cite"] = d.cite("client_quests", q)
    for r in (q.get("rewards") or {}).get("items", []) + (q.get("rewards") or {}).get("choice_items", []) + q.get("objective_items", []):
        r["name"] = d.name_of("item", r["item_id"])
    for r in q.get("objective_npcs", []):
        r["name"] = d.name_of("npc", r["npc_id"])
    givers = d.select("world_quest_givers", "quest_id = ?", (quest_id,), "role DESC, n DESC")
    return {
        "quest": q,
        "givers": d.cited("world_quest_givers", with_names(d, givers, {"holder_id": "holder"})),
        "next_quest": {"quest_id": q["next_quest_id"], "name": d.name_of("quest", q["next_quest_id"])} if q.get("next_quest_id") else None,
        "previous_quests": brief_quests(d, d.select("client_quests", "next_quest_id = ?", (quest_id,))),
    }


def t_get_object(d: Data, args: dict) -> dict:
    object_id = need_int(args, "object_id")
    o = d.latest("client_objects", "object_id", object_id)
    if not o:
        raise ToolError(f"no object {object_id} in opendata {d.version}")
    o["cite"] = d.cite("client_objects", o)
    return {
        "object": o,
        "loot": d.cited("world_loot", with_names(d, d.select("world_loot", "holder_kind = 'object' AND holder_id = ?", (object_id,),
                                                              "drops * 1.0 / max(kills, 1) DESC"), {"item_id": "item"})),
        "spawns": d.cited("world_spawns", d.select("world_spawns", "holder_kind = 'object' AND holder_id = ?", (object_id,), "n DESC")),
        "quests": d.cited("world_quest_givers", with_names(d, d.select(
            "world_quest_givers", "holder_kind = 'object' AND holder_id = ?", (object_id,)), {"quest_id": "quest"})),
    }


def t_get_recipe(d: Data, args: dict) -> dict:
    sid = need_int(args, "recipe_spell_id")
    r = d.latest("client_recipes", "recipe_spell_id", sid)
    if not r:
        raise ToolError(f"no recipe {sid} in opendata {d.version}")
    r["cite"] = d.cite("client_recipes", r)
    r["creates"]["name"] = d.name_of("item", r["creates"]["item_id"])
    for x in r.get("reagents", []):
        x["name"] = d.name_of("item", x["item_id"])
    sources = d.select("world_recipe_sources", "recipe_spell_id = ?", (sid,), "n DESC")
    for s in sources:
        kind = {"trainer": "npc", "quest": "quest", "item": "item"}[s["source_kind"]]
        s["source_name"] = d.name_of(kind, s["source_id"])
    return {
        "recipe": r,
        "taught_by_item": {"item_id": r["taught_by_item_id"], "name": d.name_of("item", r["taught_by_item_id"])} if r.get("taught_by_item_id") else None,
        "learned_from": d.cited("world_recipe_sources", sources),
    }


def t_query_sql(d: Data, args: dict) -> dict:
    sql = str(args.get("sql", "")).strip().rstrip(";")
    if not sql:
        raise ToolError("sql is required")
    if ";" in sql:
        raise ToolError("one statement only")
    limit = max(1, min(int(args.get("limit", 100)), SQL_ROW_LIMIT))
    con = d.open()
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
    con.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
    steps = {"n": 0}

    def budget() -> int:
        steps["n"] += 1
        return 1 if steps["n"] > SQL_STEP_BUDGET // 1000 else 0
    con.set_progress_handler(budget, 1000)
    try:
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(limit + 1)]
    except sqlite3.DatabaseError as e:
        raise ToolError(f"query failed: {e}" + (" (only read-only SELECT is allowed)" if "authoriz" in str(e) else "")
                        + (" (query too expensive; narrow it)" if "interrupt" in str(e) else ""))
    finally:
        con.set_authorizer(None)
        con.set_progress_handler(None, 0)
    return {"columns": cols, "rows": rows[:limit], "truncated": len(rows) > limit,
            "note": f"opendata {d.version}; cite rows as <layer>/<table> <key> with their confidence and counts"}


COVERAGE = {
    "client": "Datamined from the client build (items, spells, recipes, a few NPCs and decorative objects) and read from "
              "players' client caches (quests, most NPCs, chests and gathering nodes). client/quests and client/npcs only "
              "hold what some player has seen; a missing quest or NPC is not evidence it does not exist.",
    "items": "Item stats, weapon damage and armor are computed from the client's budget tables with the standard "
             "formulas and have not yet been checked against an in-game tooltip; raw inputs are on each row. "
             "Rows with sparse=false are stubs (no name or stats in the client tables).",
    "quests": "rewards.xp is the QuestXP table value at quest level; rewards.xp_multiplier, when present, is not "
              "applied, and the server also scales XP by level difference.",
    "world": "Observations from players' collector addon (loot, vendors, quest givers, trainers, spawns, levels). "
             "Counts, never rates: a drop chance is drops/kills, and small kills means a weak estimate. "
             "Empty until the first observations are reconciled.",
    "builds": "Each row carries build_first_seen/build_last_seen; an ID removed in a newer build keeps its old row.",
}

INSTRUCTIONS = (
    "opendata is an open dataset for World of Warcraft: Forever keyed by Blizzard IDs. Use search to find IDs, then the "
    "get_* tools; use query_sql for anything else (call dataset_info for tables). When you state a fact, cite the row's "
    "`cite` string. Report counts as counts (for example 'dropped 4 times in 1203 looted corpses'), and if you give a "
    "rate, say it is computed from those counts. client/quests and client/npcs are partial (only what players have "
    "seen); computed item stats are unverified against tooltips; world/ data is empty until observations arrive. "
    "Never fill gaps from memory of other WoW versions or websites and present it as opendata."
)

ID_SCHEMA = lambda key, what: {"type": "object", "properties": {key: {"type": "integer", "description": what}},  # noqa: E731
                               "required": [key], "additionalProperties": False}
TOOLS = [
    ("dataset_info", "Dataset version, client builds, row counts per table, and what the data does and does not cover. "
     "Call first if unsure what is available.", {"type": "object", "properties": {}, "additionalProperties": False}, t_dataset_info),
    ("search", "Find items, NPCs, quests, objects, spells or recipes by name (substring, case-insensitive) or exact ID. "
     "Exact and prefix matches first.",
     {"type": "object", "properties": {
         "query": {"type": "string", "description": "name fragment or numeric ID"},
         "kind": {"type": "string", "enum": list(KINDS), "description": "limit to one kind"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
      "required": ["query"], "additionalProperties": False}, t_search),
    ("get_item", "An item (stats, slot, prices) with who drops it (counts), who sells it, recipes that create or use it, "
     "the recipe it teaches, and quests that reward or require it.", ID_SCHEMA("item_id", "Blizzard item ID"), t_get_item),
    ("get_npc", "A creature with observed levels, its loot (normal, skinning, pickpocket; counts), what it sells, recipes "
     "it trains, quests it starts or ends, quests that ask to kill it, and where it spawns.", ID_SCHEMA("npc_id", "Blizzard creature ID"), t_get_npc),
    ("get_quest", "A quest (level, text, objectives, rewards with names) with who starts and ends it and the quests "
     "before and after it in its chain.", ID_SCHEMA("quest_id", "Blizzard quest ID"), t_get_quest),
    ("get_object", "A game object such as a chest, herb or ore node (lock, required skill and rank) with its loot "
     "(counts), spawn points and quests.", ID_SCHEMA("object_id", "Blizzard game object ID"), t_get_object),
    ("get_recipe", "A profession recipe by its spell ID: what it creates and consumes, skill brackets, the recipe item "
     "that teaches it, and trainers that teach it.", ID_SCHEMA("recipe_spell_id", "recipe spell ID"), t_get_recipe),
    ("query_sql", "Run one read-only SELECT against the dataset (tables <layer>_<table>, e.g. client_items, world_loot; "
     "JSON columns work with json_extract/json_each). Returns at most 500 rows.",
     {"type": "object", "properties": {
         "sql": {"type": "string", "description": "a single SELECT statement"},
         "limit": {"type": "integer", "minimum": 1, "maximum": SQL_ROW_LIMIT, "default": 100}},
      "required": ["sql"], "additionalProperties": False}, t_query_sql),
]
HANDLERS = {name: fn for name, _, _, fn in TOOLS}


# ───────────────────────────── protocol ─────────────────────────────

def tool_list() -> list[dict]:
    return [{"name": n, "description": desc, "inputSchema": schema,
             "annotations": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}}
            for n, desc, schema, _ in TOOLS]


def handle(d: Data, msg: dict) -> dict | None:
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:  # notification
        return None

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code: int, text: str) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER,
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_list()})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if fn is None:
            return err(-32602, f"unknown tool {name!r}")
        try:
            result = fn(d, args)
        except ToolError as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
        except sqlite3.Error as e:
            return ok({"content": [{"type": "text", "text": f"database error: {e}"}], "isError": True})
        return ok({"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, separators=(",", ":"))}],
                   "structuredContent": result})
    if method in ("resources/list", "prompts/list"):
        return ok({method.split("/")[0]: []})
    return err(-32601, f"method not found: {method}")


def serve(d: Data, stdin=sys.stdin, stdout=sys.stdout) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            if isinstance(msg, list):
                replies = [r for r in (handle(d, m) for m in msg if isinstance(m, dict)) if r]
                if replies:
                    stdout.write(json.dumps(replies, ensure_ascii=False) + "\n")
                    stdout.flush()
                continue
            reply = handle(d, msg) if isinstance(msg, dict) else {"jsonrpc": "2.0", "id": None,
                                                                    "error": {"code": -32600, "message": "invalid request"}}
        if reply:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(REPO / "dist" / "opendata.sqlite"), help="SQLite file from tools/build_sqlite.py or a release")
    args = ap.parse_args()
    serve(Data(Path(args.db).expanduser()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
