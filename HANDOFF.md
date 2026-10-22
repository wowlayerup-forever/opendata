# Handoff (2026-10-22)

For a Claude Code session continuing this work locally. Read CLAUDE.md and CONVENTIONS.md first.

## Where things stand

Two commits on `main`, CI green:

1. `47b6450` scaffold: conventions, 16 schemas, validator, SQLite/Parquet builder, CI, release workflow, licenses, llms.txt, examples.
2. `88a710b` extractor: `tools/extract_client.py`, the "extract client build" Actions workflow, `client_cache` source kind, Forever beta build `1.60.1.70374` registered.

`data/` is empty. No real extraction has run yet because the authoring environment could not reach wago.tools. Everything is tested against synthetic fixtures only.

## Facts you need

- Forever is client version 1.60.1 on the modern engine. Beta build 70374 (2026-10-12), Interface/TOC number 16001. Launch is 2026-11-04 and the launch build is unknown.
- wago.tools: `GET /api/builds` returns `{product: [{version, created_at, ...}]}`; `GET /db2/<Table>/csv?build=<full version>` returns CSV with arrays flattened to `Name_0..Name_n`. Forever beta builds are listed under product `wow_classic_beta` (as of late Sept; the launch product may differ, check `--list-builds`). Be polite: serial requests, descriptive User-Agent, it is community-run.
- Column layouts per build come from github.com/wowdev/WoWDBDefs `definitions/<Table>.dbd` (find the LAYOUT block whose BUILD line contains the build string).
- Quest names/text/rewards and NPC levels are NOT in DB2 on the modern engine. They are server-sent and cached in `<WoW>/_classic_/Cache/WDB/enUS/questcache.wdb` and `creaturecache.wdb` (path may differ for the Forever product directory; look for `Cache/WDB`). The WDB format is documented on wowdev.wiki ("WDB" and "questcache"); the record layout changes per build, so parse defensively and verify against a quest you can see in the client.
  - Correction (2026-10-22, from the real 1.60.1.70205 cache): `creaturecache.wdb` has NO level, faction or npc flags; those are unit fields the client only sees on a spawned unit. The Forever install on this Mac is `/Applications/World of Warcraft/_classic_beta_/`. Layouts as decoded are documented in `tools/import_cache.py`.
- Item stats, weapon damage and armor are computed, not stored: `stats = StatPercentEditor * RandPropPoints[ilvl].<Epic|Superior|Good>_<bucket> / 10000`, damage from `ItemDamage*[ilvl].Quality_<q>` × `ItemDelay/1000` ± `DmgVariance/2`, armor from `ItemArmorQuality × ItemArmorTotal[material] × ArmorLocation modifier` (shields from `ItemArmorShield`). The formulas are the standard SimC/client ones but are UNVERIFIED against a Forever tooltip. Raw inputs are kept on each row (`stat_budget`, `damage.table`, `armor_source`).
- Owner's other tools (separate repos, not public): `stefan-routes` (Polestar guide pack), `Stockkeeper` (addon tracking items across characters with a `tools/sync.py` that merges SavedVariables across accounts), `gearfinder.py` (ranks leveling gear from Classic data). The collector addon below should reuse Stockkeeper's SavedVariables + sync pattern. Stockkeeper's `.toc` needs `## Interface: 16001`.

## Next tasks, in order

### 1. Run the first real extraction (needs network)

```bash
pip install -r tools/requirements.txt
python tools/extract_client.py --list-builds
python tools/extract_client.py --build 1.60.1.70374 --register
python tools/validate.py && python tools/build_sqlite.py
```

Expect breakage on first contact: a column name that differs from the DBD, a table wago does not publish for this build (400/404), an ItemSparse row with no Item row. Fix in the extractor, never by hand-editing output. Commit `data/client/*.jsonl` and `builds.json` on a branch, open a PR, let CI run.

Then spot-check in the beta client: one caster weapon's damage range, one cloth chest's armor, one item's stats, against the JSONL rows. If they match, note it in CHANGELOG and the README's caveat can soften. If not, the raw inputs on the row are what you debug with. Check `INV_BUCKET` in the extractor first; the slot-to-bucket mapping is the most likely error.

### 2. API surface dump (small, high leverage for addon authors)

Write a tiny addon `tools/addons/APISurface/` that on `PLAYER_LOGIN` walks `_G` for functions and every `C_*` namespace table, plus `APIDocumentation` if `Blizzard_APIDocumentation` is loaded (`APIDocumentation:GetAPITableByTypeName("function")` etc.), and writes `{functions = {...}, events = {...}, build = GetBuildInfo()}` to SavedVariables. Then `tools/import_api.py <SavedVariables.lua> --build <build>` writes `data/api/<build>/functions.jsonl` and `events.jsonl` per the schemas. Add a `tools/api_diff.py` that diffs two builds and against Classic Era if we ever dump that. Validate.

### 3. Client cache importer

`tools/import_cache.py --build <build> --wdb <dir>` parsing `questcache.wdb` into `data/client/quests.jsonl` (`source.kind: client_cache`, `source.ref: questcache.wdb`) and merging `creaturecache.wdb` levels into npcs (min_level/max_level). The quests schema already exists; fields that the cache does not carry stay absent. The importer will only cover quests the owner's characters have seen; that is expected and is why the layer exists.

### 4. Observation collector addon + uploader

`tools/addons/OpenDataCollector/` (or a module inside Stockkeeper): hook `LOOT_OPENED` (record source GUID -> npc_id/object_id, item ids and counts, kill counter per holder), `MERCHANT_SHOW` (npc_id, items, prices, limited stock), `QUEST_ACCEPTED`/`QUEST_TURNED_IN` (quest_id, giver GUID, map position via `C_Map.GetPlayerMapPosition`), `TRADE_SKILL_SHOW` (recipes known), and gathering via loot source GUID of objects. Write compact event batches to SavedVariables. `tools/uploader.py` reads them, signs with a per-contributor key, and (v1) just writes JSON batches to a local directory; the reconciler reads that directory. Hosted ingest (Cloudflare Worker + R2) comes later.

### 5. Reconciler

`tools/reconcile.py <batches dir>` aggregates batches into `world/{loot,spawns,vendors,quest_givers,recipe_sources}.jsonl` with `drops`/`kills`/`n`, `source.kind: observation`, `confidence: verified`. Rows seeded from an emulator DB (not done yet; VMaNGOS `creature_loot_template` etc.) would be `emu_seed`/`unverified` and get promoted or marked `disputed` when observations disagree. Keep it deterministic so re-running on the same batches produces identical files (git diffs stay readable).

### 6. Launch day (2026-11-04)

`--list-builds`, register the live build, run the extraction workflow, run the API dump and cache import, tag `2026.11.1`. The release workflow builds SQLite + Parquet + SHA256SUMS.

### Later, not now

MCP server + OpenAPI over the SQLite; a static site; AH market aggregates (blocked on learning what Forever's addon API allows for AH scans); x402 or API keys for a hosted tier; emulator seeding of `world/`.

## Things deliberately left open

- Whether the launch product on wago.tools is still `wow_classic_beta` or something new.
- Whether `INV_BUCKET` and the caster-weapon flag (`Flags_1 & 0x200`) are right for 1.60. Verify in step 1.
- Quest cache record layout for 1.60; wowdev.wiki documents older layouts.
- Object required-skill resolution only handles lock type 2 (skill). Lockpicking and keys are not mapped.
