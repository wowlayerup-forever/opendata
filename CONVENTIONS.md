# Conventions

This is the contract. Tools, agents and contributors can rely on everything here. Changes to this file are breaking changes and bump the dataset major version.

## 1. Four layers, never mixed

Every record lives in exactly one layer, and the layer is encoded in the directory and in the `layer` field.

| Layer | Directory | What it is | How it is established |
|---|---|---|---|
| `client` | `data/client/` | Things the game client has: items, spells, recipes, NPC and object templates from DB2; quest templates and NPC levels from the client cache | Datamined from the client build or read from its cache. Exact. |
| `world` | `data/world/` | Things only the server knows: who drops what, where things spawn, what vendors sell, who gives a quest | Observed by players in-game, or seeded from a Classic Era emulator DB and marked unverified |
| `market` | `data/market/` | Auction house prices | Observed by addon scans, aggregated |
| `derived` | `data/derived/` | BiS lists, routes, builds, craft paths | Written by a person or an agent. Opinion. |

Rules that follow from this:

- A `derived` record may only reference IDs. It never restates a fact. "Twilight Maul drops from X" belongs in `world/loot`, not in a BiS list's notes.
- A `world` record never gets promoted to `client`, however confident we are. The layer is about where the knowledge comes from, not how sure we are.
- A fact table never contains an opinion field. No "recommended", no "best", no "worth farming".

## 2. IDs are the key, names are display

- Every entity is keyed by its Blizzard ID: `item_id`, `spell_id`, `quest_id`, `npc_id`, `object_id`, `map_id`, `zone_id`, `recipe_spell_id`.
- A record that references an entity references it by ID. Names may be included for readability and are always in the `name` field, always enUS.
- An ID that does not exist in `data/client/` for the current build is a validation error anywhere it appears. The validator enforces this once `data/client/` is populated.
- IDs are never reused and never renumbered by us. If Forever renumbers something, the old ID gets a `superseded_by` row, it is not edited in place.

## 3. Every row carries provenance

All records in `client`, `world` and `market` have:

```json
{
  "layer": "world",
  "source": { "kind": "observation", "ref": "reconciler:2026-11-20T04:00Z" },
  "build_first_seen": "1.60.1.70058",
  "build_last_seen": "1.60.1.70058",
  "confidence": "verified"
}
```

`source.kind` is one of:

| kind | meaning |
|---|---|
| `datamine` | extracted from the client build's DB2 tables. `ref` names the table. |
| `client_cache` | read from the client's WDB cache (quest and creature templates the server sent to a player's client). Verbatim Blizzard data, but only for what some player has seen. `ref` names the cache file. |
| `observation` | aggregated from in-game observations by the reconciler. `ref` names the reconciler run. |
| `emu_seed` | copied from a Classic Era emulator database (VMaNGOS, CMaNGOS). `ref` names the DB and commit. |
| `manual` | typed in by a person. `ref` is the PR or the contributor handle. |

`confidence` is one of:

| value | meaning |
|---|---|
| `exact` | from the client build or client cache, verbatim. Only `client` and `api` rows may use it. Computed fields (item stats, damage, armor) are derived from exact inputs with the client's formulas and ship with their raw inputs; spot-check them before relying on them. |
| `verified` | confirmed by in-game observation on Forever, with sample size recorded |
| `unverified` | plausible (usually `emu_seed` or `manual`) but no Forever observation yet |
| `disputed` | observations contradict the row. Kept so consumers can see the conflict. |

Build strings are the full four-part client version (for example `1.60.1.70058`) and must appear in `builds.json`.

## 4. Observations keep their sample size

A drop rate is never stored as a bare percentage. `world/loot` rows store `drops` and `kills`; a rate is something a consumer computes. The same applies to spawn sightings (`n`), vendor stock observations (`n`) and quest giver sightings (`n`). If you cannot state a count, the row is `confidence: unverified`.

## 5. Derived content is attributed and reproducible

Every `derived` record has:

```json
{
  "author": "github:someone",
  "generator": { "kind": "agent", "model": "claude-fable-5-1", "prompt_ref": "..." },
  "dataset_version": "2026.11.1",
  "criteria": "Elemental shaman, levels 20-30, dungeon drops allowed, no world epics",
  "market_snapshot": "us-east-nightsong-alliance-2026-11-20T03:00Z"
}
```

`generator.kind` is `human` or `agent`. If an agent wrote it, say which. `market_snapshot` is required when the output depends on prices. A derived record whose `dataset_version` is older than the current major version is flagged stale by the validator but not removed.

## 6. Versioning

- The dataset version is `YYYY.MM.N` and is a git tag. Every release ships `dist/opendata-<version>.sqlite` and Parquet files.
- `data/client/` and `data/api/` are regenerated per client build. The build string is on every row, so one release can hold multiple builds.
- Schema changes that remove or rename a field, or change the meaning of a field, bump the major version in `CONVENTIONS.md` and the schema `$id`. Adding optional fields does not.

## 7. Citing the dataset

Tools and agents that answer from this data should cite the dataset version and the row ID, for example `opendata 2026.11.1, world/loot npc 92341 item 92831 (verified, 47/1203)`. That lets a human check the claim in one step.

## 8. Files and formats

- Git tracks JSON Lines (`.jsonl`), one record per line, UTF-8, sorted by primary key, no trailing whitespace. This keeps diffs readable.
- One file per table: `data/<layer>/<table>.jsonl`. The schema for it is `schemas/<layer>/<table>.schema.json`.
- Build artifacts (`dist/`) are never committed.
- Raw market snapshots and raw observation batches are not in this repo. They live in object storage; the reconciler publishes aggregates here.

## 9. What we do not do

- No scraping of Wowhead or any other site. Everything here is datamined from the client, observed in-game, copied from an openly licensed emulator DB, or typed in by a contributor who says so.
- No AI-generated facts. An agent may write `derived` content and must be labeled. An agent may never author a `client`, `world` or `market` row.
- No bare percentages, no unattributed opinion, no silent edits to IDs.
