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
- client/quests: `rewards.xp` (QuestXP table value at quest level) and `rewards.reputation` (QuestFactionReward base value) resolved from the cache's difficulty indexes; new optional `rewards.xp_multiplier` keeps the quest's RewardXPMultiplier unapplied. All 46 cached quests have XP, 37 have reputation; several Forever quests carry multipliers of 1.8 to 2.5. The extractor also fetches QuestXP and QuestFactionReward.
- `tests/run_addons.py`: both addons run under LuaJIT (Lua 5.1, like the game) against a mocked WoW API (`tests/wowmock.lua`, which also flags reads of undefined globals), and their SavedVariables go through uploader, reconcile and import_api to validated rows; 33 checks, run in CI. It found a bug: two sessions could get the same batch id (same second, same RNG sequence), which made the uploader report the second session as a conflict. Batch ids now mix in GetTime() and are checked against the ids already in SavedVariables, including on `/odc reset`.
- `tools/reconcile.py --wago-cache` (default `.cache/wago`) for where UiMapAssignment is read from.
- `tools/launch.py --install-addons` copies APISurface and OpenDataCollector into the client; the script warns when the client's interface number differs from the `.toc` files, prefers a non-beta product folder, and imports an API dump made with only OpenDataCollector loaded. HANDOFF.md moves all in-game steps to launch day (the beta closed on 2026-10-23).
- `tools/launch.py`: the launch-day sequence (pick and register the build, extract, import API dump, cache and observations, validate, build). Never commits or tags.
- `tools/extract_client.py` keeps build history: unchanged rows keep `build_first_seen`, rows removed in a newer build stay with their last build, older-than-on-disk extractions are refused. Rehearsed 70374 -> 70532: 31530 of 31879 items unchanged, 10 IDs removed across tables and kept.
- HANDOFF.md rewritten for the state after tasks 1 to 5.
- `tools/import_cache.py` also reads `gameobjectcache.wdb` into `client/objects`: 634 objects from build 1.60.1.70205 (the GameObjects DB2 only ships decorative objects, so every chest, herb and ore node comes from here). Herb and ore ranks decode to the familiar values (Copper Vein 1, Mithril 175, Black Lotus 300).
- client/objects: optional `lock_type` (LockType id). `required_skill_id` is now the top-level SkillLine named like the LockType; it was never set before, because the extractor read `Index_k` where wago exports `_Index_k` and treated a LockType id as a SkillLine id. `tools/extract_client.py` also fetches LockType and keeps cache rows in `objects.jsonl`.
- `tools/reconcile.py`: observation batches -> `world/{loot,spawns,vendors,quest_givers,recipe_sources,npc_levels}`. Deterministic, regenerates all observation rows from the batch directory, verifies batch signatures, keeps emu_seed and manual rows (superseded when observed, disputed when the holder is observed often enough without the item).
- `coords.map_id` allows 0 (Eastern Kingdoms is map 0). It was wrongly bound to the generic id minimum of 1.
- `tools/extract_client.py` also fetches UiMapAssignment (used by the reconciler for zone ids).
- `tools/addons/OpenDataCollector/` addon and `tools/uploader.py`: record loot, vendors, quest givers, trainers and creature levels in game; turn SavedVariables from every account into HMAC-signed JSON batches (one per session, safe to re-run) in `.cache/batches/` for the reconciler.
- New table `world/npc_levels` (observed levels with counts). Levels are a world fact: the creature cache does not carry them.
- `tools/import_cache.py`: reads the client's WDB cache into `client/quests` (questcache.wdb) and `client/npcs` (creaturecache.wdb), `source.kind: client_cache`. Layouts worked out on 1.60.1.70205 and checked per record; records that do not decode to their exact length are skipped and listed. Imports accumulate across runs and accounts. First import: 46 quests and 350 creatures from build 1.60.1.70205 (registered). The creature cache has no level, faction or npc flags; quest XP and reputation amounts are left out (the cache stores difficulty indexes only).
- client/quests: optional `description` (quest text).
- CONVENTIONS section 2: a reference into `client/quests` or `client/npcs` that is not there yet is a warning, since both tables are cache-fed and partial. Not a version bump: nothing has been released.
- `tools/extract_client.py` keeps cache-sourced rows in `npcs.jsonl` when it rewrites the file.
- api/functions: optional `present` (false when documented but not callable at login). api/functions and api/events: optional `inner_type` and `mixin` on arguments, returns and payload fields.
