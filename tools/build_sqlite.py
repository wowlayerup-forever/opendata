#!/usr/bin/env python3
"""Build the release SQLite file (and optionally Parquet) from the JSONL tables.

Table names are <layer>_<table>, for example client_items, world_loot. Columns come
from the schema: scalar properties become typed columns, nested objects and arrays
are stored as JSON text. Every table gets an index on each *_id column and on
build_last_seen. A `meta` table records the dataset version, the builds included
and when the file was generated.

Usage:
  python tools/build_sqlite.py                      # data/ -> dist/opendata.sqlite
  python tools/build_sqlite.py --root examples --out dist/examples.sqlite
  python tools/build_sqlite.py --version 2026.11.1 --parquet
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMAS = REPO / "schemas"

TYPE_MAP = {"integer": "INTEGER", "number": "REAL", "string": "TEXT", "boolean": "INTEGER"}


def load_schemas() -> tuple[dict[str, dict], dict]:
    by_table: dict[str, dict] = {}
    defs: dict = {}
    for path in sorted(SCHEMAS.rglob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SCHEMAS)
        if rel.parent != Path("."):
            by_table[f"{rel.parent}/{rel.name[: -len('.schema.json')]}"] = schema
        elif rel.name == "defs.schema.json":
            defs = schema["$defs"]
    return by_table, defs


def resolve(prop: dict, defs: dict) -> dict:
    ref = prop.get("$ref", "")
    if "#/$defs/" in ref:
        return resolve(defs[ref.split("#/$defs/")[1]], defs)
    if "allOf" in prop and "type" not in prop:
        merged: dict = {"properties": {}}
        for part in prop["allOf"]:
            r = resolve(part, defs)
            merged["properties"].update(r.get("properties", {}))
            merged.setdefault("type", r.get("type"))
        return merged
    if "const" in prop and "type" not in prop:
        return {"type": "string" if isinstance(prop["const"], str) else "integer"}
    return prop


def columns(schema: dict, defs: dict) -> list[tuple[str, str]]:
    """Ordered (name, sqlite_type) for a table. Header fields first, then the table's own."""
    props: dict[str, dict] = {}
    for part in schema.get("allOf", []):
        props.update(resolve(part, defs).get("properties", {}))
    props.update(schema.get("properties", {}))
    cols = []
    for name, prop in props.items():
        p = resolve(prop, defs)
        t = p.get("type")
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), "string")
        if t in ("object", "array") or t is None:
            cols.append((name, "TEXT"))  # JSON
        else:
            cols.append((name, TYPE_MAP.get(t, "TEXT")))
    return cols


def table_name(root: Path, path: Path) -> str:
    parts = path.relative_to(root).parts
    if parts[0] == "api":
        return f"api_{parts[-1][:-len('.jsonl')]}"
    return f"{parts[0]}_{parts[1][:-len('.jsonl')]}"


def schema_key(root: Path, path: Path) -> str:
    parts = path.relative_to(root).parts
    if parts[0] == "api":
        return f"api/{parts[-1][:-len('.jsonl')]}"
    return f"{parts[0]}/{parts[1][:-len('.jsonl')]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data")
    ap.add_argument("--out", default="dist/opendata.sqlite")
    ap.add_argument("--version", default="dev")
    ap.add_argument("--parquet", action="store_true", help="also write dist/parquet/<table>.parquet (needs pyarrow)")
    args = ap.parse_args()

    root = REPO / args.root
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    schemas, defs = load_schemas()
    con = sqlite3.connect(out)
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")

    builds: set[str] = set()
    counts: dict[str, int] = {}
    created: set[str] = set()
    files = sorted(root.rglob("*.jsonl"))

    # Every table exists in every release, even when empty, so consumers can rely on the names.
    for key, schema in schemas.items():
        tname = key.replace("/", "_")
        cols = columns(schema, defs)
        col_sql = ", ".join(f'"{n}" {t}' for n, t in cols)
        con.execute(f'CREATE TABLE "{tname}" ({col_sql})')
        created.add(tname)
        counts[tname] = 0

    for path in files:
        key = schema_key(root, path)
        tname = table_name(root, path)
        schema = schemas[key]
        cols = columns(schema, defs)
        names = [n for n, _ in cols]
        placeholders = ", ".join("?" for _ in names)
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            vals = []
            for n, t in cols:
                v = row.get(n)
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
                elif isinstance(v, bool):
                    v = int(v)
                vals.append(v)
            rows.append(vals)
            for f in ("build_first_seen", "build_last_seen", "build"):
                if isinstance(row.get(f), str):
                    builds.add(row[f])
        con.executemany(f'INSERT INTO "{tname}" ({", ".join(chr(34)+n+chr(34) for n in names)}) VALUES ({placeholders})', rows)
        counts[tname] += len(rows)

    for tname in created:
        info = con.execute(f'PRAGMA table_info("{tname}")').fetchall()
        for _, name, *_ in info:
            if name.endswith("_id") or name == "build_last_seen" or name == "id":
                con.execute(f'CREATE INDEX IF NOT EXISTS "idx_{tname}_{name}" ON "{tname}" ("{name}")')
        pk = schemas[next(k for k in schemas if k.replace("/", "_") == tname)].get("x-primary-key", [])
        pk = [p for p in pk if "." not in p]
        if pk:
            con.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS "pk_{tname}" ON "{tname}" ({", ".join(chr(34)+p+chr(34) for p in pk)})')

    try:
        commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        commit = "unknown"
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany(
        "INSERT INTO meta VALUES (?, ?)",
        [
            ("dataset_version", args.version),
            ("generated_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
            ("git_commit", commit),
            ("root", args.root),
            ("builds", json.dumps(sorted(builds))),
            ("license_data", "CC-BY-4.0"),
            ("license_code", "MIT"),
            ("source", "https://github.com/wowlayerup-forever/opendata"),
        ],
    )
    con.commit()
    con.execute("VACUUM")
    con.close()

    if args.parquet:
        try:
            import pyarrow as pa  # type: ignore
            import pyarrow.parquet as pq  # type: ignore
        except ImportError:
            sys.exit("--parquet needs pyarrow: pip install pyarrow")
        pdir = out.parent / "parquet"
        pdir.mkdir(exist_ok=True)
        con = sqlite3.connect(out)
        con.row_factory = sqlite3.Row
        for tname in sorted(created):
            rows = [dict(r) for r in con.execute(f'SELECT * FROM "{tname}"')]
            if rows:
                pq.write_table(pa.Table.from_pylist(rows), pdir / f"{tname}.parquet")
        con.close()

    total = sum(counts.values())
    shown = out.relative_to(REPO) if out.is_relative_to(REPO) else out
    print(f"wrote {shown}: {len(created)} tables, {total} rows, builds {sorted(builds) or '[]'}")
    for t in sorted(counts):
        print(f"  {t}: {counts[t]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
