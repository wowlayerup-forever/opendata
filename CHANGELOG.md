# Changelog

Dataset releases are tagged `YYYY.MM.N`. Schema or convention changes that break consumers are called out here.

## Unreleased

- Repository scaffold: conventions, JSON Schemas for 16 tables across client, world, market, derived and api layers, validator, SQLite and Parquet builder, CI and release workflows, examples.
- `tools/extract_client.py` and the "extract client build" workflow: items, spells, recipes, NPCs and objects from wago.tools for a given build. Quests and NPC levels need a client-cache importer, not yet written.
- Added `client_cache` source kind; items carry `stat_budget`, `armor_source`, `damage.table`; recipes drop `skill_yellow` (not in client data).
- Registered Forever beta build 1.60.1.70374 (interface 16001).
- First extraction: `data/client/` for beta build 1.60.1.70374 (31847 items, 31850 spells, 2350 recipes, 180 NPCs, 1415 objects). Computed stats, damage and armor are not yet spot-checked against a Forever tooltip.
- client/items: optional `sparse` field. 12599 items ship an Item row but no ItemSparse row (name and stats come from hotfixes or the server); they are emitted as stubs with `sparse: false` and only class, subclass, slot and icon. `name`, `quality`, `binding` and `stats` stay required for every other row. Consumers that assume every item has a name should filter on `sparse`.
- client/items: item effects that point at a spell the build does not ship are dropped (118 in 70374).
- client/recipes: optional `skill_ids` for recipe spells listed under more than one SkillLine; duplicate SkillLineAbility rows collapse to one recipe.
- `tools/addons/APISurface/` addon, `tools/import_api.py` and `tools/api_diff.py`: dump the addon API surface in game and import it into `data/api/<build>/`. `tools/savedvariables.py` parses SavedVariables files (stdlib only).
- `tools/import_cache.py`: reads the client's WDB cache into `client/quests` (questcache.wdb) and `client/npcs` (creaturecache.wdb), `source.kind: client_cache`. Layouts worked out on 1.60.1.70205 and checked per record; records that do not decode to their exact length are skipped and listed. Imports accumulate across runs and accounts. First import: 46 quests and 350 creatures from build 1.60.1.70205 (registered). The creature cache has no level, faction or npc flags; quest XP and reputation amounts are left out (the cache stores difficulty indexes only).
- client/quests: optional `description` (quest text).
- CONVENTIONS section 2: a reference into `client/quests` or `client/npcs` that is not there yet is a warning, since both tables are cache-fed and partial. Not a version bump: nothing has been released.
- `tools/extract_client.py` keeps cache-sourced rows in `npcs.jsonl` when it rewrites the file.
- api/functions: optional `present` (false when documented but not callable at login). api/functions and api/events: optional `inner_type` and `mixin` on arguments, returns and payload fields.
