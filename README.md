# opendata

Open, versioned game data for World of Warcraft: Forever, keyed by Blizzard IDs, built for humans and agents.

The goal is a dataset that any tool, addon author, guide writer, or AI agent (Claude, OpenAI, Grok, local models) can build on without scraping a website and without guessing. Every row says where it came from, which client build it was seen in, and how much to trust it.

**Status: scaffolding.** Schemas, conventions, validator and build tooling exist. The data directories are empty until the first extraction from a Forever client build lands. Forever launches 2026-11-04.

## What is in here

| Path | What | Trust |
|---|---|---|
| `data/client/` | Items, spells, recipes, NPC and object templates from the Forever client's DB2 tables; quests and NPC levels from the client cache | Exact for the build it was extracted from; computed fields carry their raw inputs |
| `data/world/` | Loot, spawns, vendor stock, quest givers. Server-side facts that only player observations can establish | Carries `confidence` and sample size on every row |
| `data/market/` | Auction house aggregates (daily per-item stats per realm and faction). Raw snapshots are not stored in git | Observed |
| `data/derived/` | BiS lists, leveling routes, craft routes, talent builds. Opinion, versioned and attributed, referencing IDs from the layers above | Opinion, never fact |
| `data/api/` | The addon API surface per client build: functions, events, widget methods, and diffs between builds | Exact for the build |
| `schemas/` | JSON Schema for every table | |
| `tools/` | Validator and SQLite builder | |
| `builds.json` | Registry of Forever client builds this dataset has been extracted from | |

Read [CONVENTIONS.md](CONVENTIONS.md) before adding or consuming data. It is short and it is the contract. If you are an agent, read [llms.txt](llms.txt) first.

## Using the data

Each tagged release ships:

- `opendata-<version>.sqlite`: every table, one file. Open it with any SQLite client, or query it over HTTP range requests with sqlite-wasm or sql.js-httpvfs without downloading the whole thing.
- Parquet files per table, for pandas, DuckDB, Polars.
- The raw JSONL under `data/`, which is what git tracks.

Lookups by ID are the primary access pattern. Names are for display and are localized; IDs are the contract.

```sql
-- what drops a given item, and how sure are we
SELECT holder_kind, holder_id, drops, kills, confidence, build_last_seen
FROM world_loot WHERE item_id = :item_id;
```

### From an AI agent (MCP)

`tools/mcp_server.py` serves the SQLite file over the Model Context Protocol (stdio, standard library only, read-only): `search`, `get_item`, `get_npc`, `get_quest`, `get_object`, `get_recipe`, `dataset_info`, and a guarded read-only `query_sql`. Every row it returns carries a citation in the CONVENTIONS section 7 form, and it reports counts, not rates.

```bash
python tools/build_sqlite.py                    # or download opendata-<version>.sqlite from a release
claude mcp add opendata -- python3 /path/to/opendata/tools/mcp_server.py --db /path/to/opendata.sqlite
```

Opening this repo in Claude Code offers the server through `.mcp.json` (it reads `dist/opendata.sqlite`).

## Building locally

```bash
pip install -r tools/requirements.txt
python tools/validate.py            # schema + cross-reference checks on data/ and examples/
python tools/build_sqlite.py        # writes dist/opendata.sqlite from data/
```

## Extracting a client build

`tools/extract_client.py` pulls a build's DB2 tables from [wago.tools](https://wago.tools) (cached under `.cache/wago/`) and writes `data/client/{items,spells,recipes,npcs,objects}.jsonl`. The "extract client build" workflow in the Actions tab does the same and opens a PR.

```bash
python tools/extract_client.py --list-builds                 # which products carry 1.x builds
python tools/extract_client.py --build 1.60.1.70374 --register
```

Forever beta builds are published under the wago product `wow_classic_beta`. Known builds are in `builds.json`.

What the extractor does not cover, and why:

- `client/quests` and NPC levels. On the modern engine these are not in DB2; the server sends them and the client caches them in `Cache/WDB/<locale>/questcache.wdb` and `creaturecache.wdb`. A cache importer (`source.kind: client_cache`) is the next tool to write.
- Item stats, weapon damage and armor are computed from the client's budget tables (`RandPropPoints`, `ItemDamage*`, `ItemArmor*`) using the client's formulas. The raw inputs stay on the row (`stat_budget`, `damage.table`, `armor_source`). Treat the computed numbers as unverified until spot-checked against in-game tooltips.
- The yellow skill threshold for recipes is not in the client data; green and gray are.

CI runs both on every pull request. A PR that adds an ID not present in the current client build fails.

## Contributing

Pull requests, with CI green. See [CONTRIBUTING.md](CONTRIBUTING.md). Observations collected in-game go through the uploader and reconciler (separate repos, not yet published) and arrive here as bot-authored PRs with sample sizes attached. Hand-entered world facts are welcome but must say so in `source.kind`.

## License

Data (everything under `data/`, `examples/`, and release files) is licensed [CC BY 4.0](LICENSE-DATA). Code (`tools/`, CI, schemas) is [MIT](LICENSE).

World of Warcraft and all related names, items, quests and lore are trademarks and copyrights of Blizzard Entertainment, Inc. This is a fan project and is not affiliated with Blizzard. The license above covers this compilation and its code, not Blizzard's underlying content.

## Related

- [pfQuest](https://github.com/shagu/pfQuest) and the [VMaNGOS](https://github.com/vmangos/core) / [CMaNGOS](https://github.com/cmangos/classic-db) world databases: Classic Era 1.12 reconstructions. Used only as a seed for `data/world/`, marked `confidence: unverified` until a Forever observation confirms a row.
- [wago.tools](https://wago.tools): datamined DB2 tables per client build. The source for `data/client/`.
- [ForeverWowCraft](https://foreverwowcraft.com/): live AH-priced crafting routes for Forever.
- Polestar guide packs, which consume quest and item IDs from this dataset.
