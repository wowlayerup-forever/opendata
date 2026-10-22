# Changelog

Dataset releases are tagged `YYYY.MM.N`. Schema or convention changes that break consumers are called out here.

## Unreleased

- Repository scaffold: conventions, JSON Schemas for 16 tables across client, world, market, derived and api layers, validator, SQLite and Parquet builder, CI and release workflows, examples.
- `tools/extract_client.py` and the "extract client build" workflow: items, spells, recipes, NPCs and objects from wago.tools for a given build. Quests and NPC levels need a client-cache importer, not yet written.
- Added `client_cache` source kind; items carry `stat_budget`, `armor_source`, `damage.table`; recipes drop `skill_yellow` (not in client data).
- Registered Forever beta build 1.60.1.70374 (interface 16001).
- No Forever data yet. `data/` fills when the first client build is extracted.
