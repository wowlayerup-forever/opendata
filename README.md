# opendata

Open, versioned game data for **World of Warcraft: Forever**, keyed by Blizzard IDs, built for people and AI agents.

Any tool, addon author, guide writer or AI agent (Claude, OpenAI, Grok, local models) should be able to build on this without scraping a website and without guessing. Every row says where it came from, which client build it was seen in, and how far to trust it. Data is [CC BY 4.0](LICENSE-DATA), code is [MIT](LICENSE).

## Status

Forever launches **2026-11-04**. Until then the dataset holds what could be gathered during the beta:

| Layer | Now | After launch |
|---|---|---|
| `client` | 31.8k items, 31.9k spells, 2.35k recipes, 530 NPCs, 2k objects (beta build 1.60.1.70374) and 46 quests from a player's client cache | Re-extracted for the live build; quests and NPCs grow with every imported cache |
| `world` | Empty: the collector addon had no chance to run before the beta closed | Loot, vendors, quest givers, trainers, spawns and levels from players' sessions, reconciled nightly |
| `api` | Empty | The live client's addon API, dumped on launch day |
| `market`, `derived` | Empty | Later |

The current release is the beta-data pre-release [`2026.10.0`](https://github.com/wowlayerup-forever/opendata/releases/tag/2026.10.0); `2026.11.1` follows the launch. Computed item stats, weapon damage and armor have not yet been checked against in-game tooltips.

## Use the data

Every release has `opendata-<version>.sqlite` (all tables in one file), Parquet per table, and `SHA256SUMS`. The newest SQLite is also at a stable public URL that supports HTTP range requests and CORS, so a web page can query it in place with sql.js-httpvfs or sqlite-wasm without downloading all of it:

```
https://storage.googleapis.com/wowlayerup-forever-releases/opendata-latest.sqlite
```

Look things up by ID; names are for display. Tables are `<layer>_<table>`:

```sql
-- what drops an item, from how many looted corpses
SELECT holder_kind, holder_id, drops, kills, confidence, build_last_seen
FROM world_loot WHERE item_id = 2589;

-- a quest and its XP (JSON columns work with json_extract)
SELECT name, quest_level, json_extract(rewards, '$.xp') AS xp FROM client_quests WHERE quest_id = 303;
```

Before relying on a row, read [CONVENTIONS.md](CONVENTIONS.md) (short; it is the contract). The rules that matter most:

- **Layers never mix.** `client` is exact client data, `world` is what players observed, `derived` is attributed opinion.
- **Counts, not rates.** A drop chance is `drops / kills`, and 3 out of 4 is not "75%" in any useful sense. Quote the counts.
- **`client/quests` and `client/npcs` are partial.** They come from players' client caches and only hold what someone has seen. A missing quest or NPC is unknown, not nonexistent.
- **Cite the version and row:** `opendata 2026.11.1, world/loot npc 568 item 2589 (verified, 47/120)`.

## Use it from an AI agent

A hosted MCP server answers from the latest release (Streamable HTTP, read-only, no sign-in):

```bash
claude mcp add --transport http opendata https://opendata-mcp-4fn67a62qq-uc.a.run.app/mcp
```

Tools: `search`, `get_item`, `get_npc`, `get_quest`, `get_object`, `get_recipe`, `dataset_info`, and a guarded read-only `query_sql`. Every row it returns has a `cite` string, and it reports counts, not rates. To run it yourself against a local file: `python tools/mcp_server.py --db opendata-<version>.sqlite` (stdio, standard library only). Agents answering from the data should read [llms.txt](llms.txt) first.

## Contribute observations (players)

The `world` layer only exists if players record what they see. From the [releases named "OpenDataCollector"](https://github.com/wowlayerup-forever/opendata/releases), download the addon and the `opendata-uploader` for your system (Windows, macOS Apple Silicon or Intel, Linux).

1. Unzip the addon into `<WoW>/_classic_/Interface/AddOns/` (the Forever folder name may differ; it is the one with `Interface` and `WTF` in it) and restart the game fully.
2. Play normally. `/odc` in chat shows what has been recorded. Log out when done; the game writes the addon's data on logout.
3. Double-click `opendata-uploader`. It finds your install, saves the session and, if you have a contributor key, sends it. After a successful upload, `/odc reset` in game clears the addon's buffer.

**What is recorded:** loot windows, merchant windows, quest accepts and turn-ins, trainer windows and creature levels, as Blizzard IDs, counts, prices and map positions. No character names, chat or player GUIDs.

**Getting a contributor key:** [open a "Contributor key" issue](https://github.com/wowlayerup-forever/opendata/issues/new?template=contributor-key.yml) with your GitHub handle. Do not post an email address or anything private there; a maintainer replies on the issue to arrange sending the key privately. Put the `contributor.json` you receive next to `opendata-uploader`; it is imported on the first run. Without a key, your batches stay on your machine.

The programs are not code-signed yet, so Windows SmartScreen and macOS Gatekeeper warn on first run; the release notes say how to proceed. Checksums are attached to every release.

## Contribute code or data (developers)

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) (the working rules, for people and coding agents alike). Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md). Quick start:

```bash
git clone https://github.com/wowlayerup-forever/opendata && cd opendata
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
.venv/bin/python tools/validate.py          # schemas, build registry, keys, cross-references: 0 errors
.venv/bin/python tools/build_sqlite.py      # dist/opendata.sqlite
```

Everything in `tools/` is Python 3.11+ with the standard library plus `jsonschema`. The addon tests need [LuaJIT](https://luajit.org) (`brew install luajit`, `apt install luajit`).

### How data gets in

```
wago.tools DB2 tables  --extract_client.py-->  data/client/
player's WDB cache     --import_cache.py---->  data/client/   (quests, NPCs, chests and nodes)
APISurface addon       --import_api.py------>  data/api/<build>/
OpenDataCollector  -->  opendata-uploader  -->  ingest service  -->  batch bucket
                        nightly reconcile.py  -->  pull request  -->  data/world/
validate.py on every PR;  a YYYY.MM.N tag  -->  SQLite + Parquet release, public bucket, hosted MCP
```

- `data/client/` and `data/api/` are generated; fix the tool, never the file.
- `data/world/` is regenerated from all observation batches by `tools/reconcile.py`, deterministically. Rows seeded from an openly licensed emulator database would be marked `emu_seed` / `unverified`, and observations supersede or dispute them.
- `data/derived/` (BiS lists, routes) is open to anyone, attributed, and may only reference IDs.

### What is where

| Path | What |
|---|---|
| `CONVENTIONS.md` | The data contract: layers, IDs, provenance, confidence, versioning, citation |
| `schemas/` | JSON Schema per table |
| `data/<layer>/<table>.jsonl` | The data, one JSON object per line, sorted by primary key |
| `builds.json` | Every client build the data mentions |
| `tools/` | Extractor, cache and API importers, uploader, reconciler, validator, SQLite builder, MCP server, packager, launch script |
| `tools/addons/` | `OpenDataCollector` (for players) and `APISurface` (for maintainers) |
| `service/` | The Cloud Run image: batch ingest and MCP over HTTP |
| `tests/` | Addons under LuaJIT against a mocked WoW API, the MCP server, the service |
| `examples/` | One or two invented rows per table, validated in CI, never released |

The cloud setup (Google Cloud Run, Cloud Storage, Workload Identity Federation for GitHub Actions) is defined in a private infrastructure repository. This repo holds only the service code and the workflows, which read every identifier from repository secrets.

## Reporting problems

- **A wrong row:** [open a "Wrong data" issue](https://github.com/wowlayerup-forever/opendata/issues/new?template=wrong-data.yml) with the release, table, key, what you saw instead, and how often.
- **A security problem** (ingest, keys, workflows): report it privately through [GitHub security advisories](https://github.com/wowlayerup-forever/opendata/security/advisories/new), not in a public issue. See [SECURITY.md](SECURITY.md).

## License

Data (everything under `data/`, `examples/`, and the release files) is [CC BY 4.0](LICENSE-DATA): use it for anything, with attribution. Code (`tools/`, `service/`, `tests/`, CI, schemas) is [MIT](LICENSE).

World of Warcraft and all related names, items, quests and lore are trademarks and copyrights of Blizzard Entertainment, Inc. This is a fan project, not affiliated with Blizzard. The licenses cover this compilation and its code, not Blizzard's underlying content.

## Related

- [wago.tools](https://wago.tools): datamined DB2 tables per client build, the source of `data/client/`.
- [WoWDBDefs](https://github.com/wowdev/WoWDBDefs): DB2 column layouts per build.
- [VMaNGOS](https://github.com/vmangos/core), [CMaNGOS](https://github.com/cmangos/classic-db), [pfQuest](https://github.com/shagu/pfQuest): Classic Era reconstructions; candidates for seeding `data/world/` as `unverified` (not done yet).
- [ForeverWowCraft](https://foreverwowcraft.com/): live AH-priced crafting routes for Forever.
- Polestar guide packs, which consume quest and item IDs from this dataset.
