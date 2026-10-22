#!/usr/bin/env python3
"""Validate every JSONL table against its schema and the conventions.

Checks, per root (data/ and examples/):
  1. every line parses as JSON and validates against schemas/<layer>/<table>.schema.json
  2. every build string appears in builds.json
  3. primary keys (schema x-primary-key) are unique within a file, and files are sorted by them
  4. api/<build>/ rows carry that build
  5. IDs referenced from world/ and derived/ rows exist in the root's client/ tables
     (skipped for a client table that is empty in that root, so an empty data/ passes)

Exit status 1 on any error. Warnings do not fail the run.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ImportError:  # pragma: no cover
    sys.exit("pip install -r tools/requirements.txt")

REPO = Path(__file__).resolve().parent.parent
SCHEMAS = REPO / "schemas"
BUILDS = REPO / "builds.json"

# Which client table an ID field points at. holder_id / source_id are resolved by their *_kind.
ID_FIELDS = {
    "item_id": "items",
    "quest_id": "quests",
    "npc_id": "npcs",
    "object_id": "objects",
    "spell_id": "spells",
    "recipe_spell_id": "recipes",
    "taught_by_item_id": "items",
    "prev_quest_id": "quests",
    "next_quest_id": "quests",
}
KIND_TABLE = {"npc": "npcs", "object": "objects", "item": "items", "quest": "quests", "trainer": "npcs"}
CLIENT_KEY = {
    "items": "item_id",
    "quests": "quest_id",
    "npcs": "npc_id",
    "objects": "object_id",
    "spells": "spell_id",
    "recipes": "recipe_spell_id",
}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.rows = 0

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


def load_registry() -> tuple[Registry, dict[str, dict], dict]:
    registry = Registry()
    by_table: dict[str, dict] = {}
    defs: dict = {}
    for path in sorted(SCHEMAS.rglob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        rel = path.relative_to(SCHEMAS)
        if rel.parent != Path("."):
            by_table[f"{rel.parent}/{rel.name[: -len('.schema.json')]}"] = schema
        elif rel.name == "defs.schema.json":
            defs = schema["$defs"]
    return registry, by_table, defs


def allowed_keys(schema: dict, defs: dict) -> set[str]:
    """Top-level property names a table row may have: its own plus those of the
    shared header it includes via allOf $ref. Anything else is a stray field,
    which the conventions forbid (no bare percentages, no opinion in fact tables)."""
    keys: set[str] = set(schema.get("properties", {}))

    def collect(sub: dict) -> None:
        keys.update(sub.get("properties", {}))
        for part in sub.get("allOf", []):
            ref = part.get("$ref", "")
            if "#/$defs/" in ref:
                collect(defs[ref.split("#/$defs/")[1]])
            else:
                collect(part)

    collect(schema)
    return keys


def table_key(root: Path, path: Path) -> tuple[str, str | None]:
    """Return (layer/table, build-from-path-or-None) for a jsonl file."""
    rel = path.relative_to(root)
    parts = rel.parts
    if parts[0] == "api" and len(parts) == 3:
        return f"api/{parts[2][:-len('.jsonl')]}", parts[1]
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1][:-len('.jsonl')]}", None
    return str(rel), None


def get_path(row: dict, dotted: str):
    cur = row
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def walk_ids(obj, out: list[tuple[str, int, str]], kind_ctx: str | None = None) -> None:
    """Collect (field, id, client_table) triples from a row, recursively."""
    if isinstance(obj, dict):
        hk = obj.get("holder_kind") or obj.get("source_kind") or kind_ctx
        for k, v in obj.items():
            if k in ID_FIELDS and isinstance(v, int):
                out.append((k, v, ID_FIELDS[k]))
            elif k in ("holder_id", "source_id") and isinstance(v, int):
                table = KIND_TABLE.get(hk or "")
                if table:
                    out.append((k, v, table))
            else:
                walk_ids(v, out, hk)
    elif isinstance(obj, list):
        for v in obj:
            walk_ids(v, out, kind_ctx)


def validate_root(root: Path, registry: Registry, schemas: dict[str, dict], defs: dict, builds: set[str], rep: Report) -> None:
    files = sorted(p for p in root.rglob("*.jsonl"))
    client_ids: dict[str, set[int]] = defaultdict(set)
    deferred_refs: list[tuple[str, str, int, str]] = []

    for path in files:
        where = str(path.relative_to(REPO))
        key, build_from_path = table_key(root, path)
        schema = schemas.get(key)
        if schema is None:
            rep.error(where, f"no schema for table {key}")
            continue
        validator = Draft202012Validator(schema, registry=registry)
        allowed = allowed_keys(schema, defs)
        pk = schema.get("x-primary-key", [])
        seen_pks: set[tuple] = set()
        last_pk: tuple | None = None
        layer = key.split("/")[0]
        table = key.split("/")[1]

        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            loc = f"{where}:{lineno}"
            if not line.strip():
                rep.error(loc, "blank line")
                continue
            if line != line.rstrip():
                rep.error(loc, "trailing whitespace")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                rep.error(loc, f"invalid JSON: {e}")
                continue
            rep.rows += 1

            for err in sorted(validator.iter_errors(row), key=lambda e: list(e.path)):
                p = "/".join(str(x) for x in err.path) or "<root>"
                rep.error(loc, f"{p}: {err.message}")
            if isinstance(row, dict):
                for stray in sorted(set(row) - allowed):
                    rep.error(loc, f"unknown field {stray!r} (not in {key} schema)")

            for f in ("build_first_seen", "build_last_seen", "build"):
                b = row.get(f)
                if isinstance(b, str) and b not in builds:
                    rep.error(loc, f"{f} {b} is not in builds.json")
            if build_from_path and row.get("build_last_seen") != build_from_path:
                rep.error(loc, f"row build_last_seen {row.get('build_last_seen')} does not match directory {build_from_path}")

            if pk:
                pkv = tuple(get_path(row, k) for k in pk)
                if pkv in seen_pks:
                    rep.error(loc, f"duplicate primary key {pkv}")
                seen_pks.add(pkv)
                if last_pk is not None:
                    try:
                        if pkv < last_pk:
                            rep.warn(loc, f"not sorted by primary key {pk}")
                    except TypeError:
                        pass
                last_pk = pkv

            if layer == "client" and table in CLIENT_KEY:
                v = row.get(CLIENT_KEY[table])
                if isinstance(v, int):
                    client_ids[table].add(v)
            if layer in ("world", "derived", "market", "client"):
                refs: list[tuple[str, int, str]] = []
                walk_ids(row, refs)
                for field, rid, ctable in refs:
                    if layer == "client" and field == CLIENT_KEY.get(table):
                        continue  # a row's own key
                    deferred_refs.append((loc, field, rid, ctable))

    for loc, field, rid, ctable in deferred_refs:
        ids = client_ids.get(ctable)
        if not ids:
            continue  # client table empty in this root: nothing to check against yet
        if rid not in ids:
            rep.error(loc, f"{field} {rid} not found in {root.name}/client/{ctable}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", help="directory to validate (default: data and examples)")
    args = ap.parse_args()
    roots = [REPO / r for r in args.root] if args.root else [REPO / "data", REPO / "examples"]

    builds = {b["build"] for b in json.loads(BUILDS.read_text(encoding="utf-8"))["builds"]}
    registry, schemas, defs = load_registry()
    for key, schema in schemas.items():
        Draft202012Validator.check_schema(schema)

    rep = Report()
    for root in roots:
        if root.exists():
            validate_root(root, registry, schemas, defs, builds, rep)

    for w in rep.warnings:
        print(f"warning: {w}")
    for e in rep.errors:
        print(f"error: {e}")
    print(f"{rep.rows} rows, {len(schemas)} tables, {len(rep.errors)} errors, {len(rep.warnings)} warnings")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
