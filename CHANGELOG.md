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
- api/functions: optional `present` (false when documented but not callable at login). api/functions and api/events: optional `inner_type` and `mixin` on arguments, returns and payload fields.
