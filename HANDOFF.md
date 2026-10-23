# Handoff (2026-10-23)

For a Claude Code session continuing this work locally. Read CLAUDE.md and CONVENTIONS.md first.

## Where things stand

HANDOFF tasks 1 to 5 from 2026-10-22 are done and merged (PRs #1 to #6). CI green.

| Tool | Does | State |
|---|---|---|
| `tools/extract_client.py` | wago.tools DB2 -> `client/{items,spells,recipes,npcs,objects}` | Ran for beta 1.60.1.70374. Computed stats, damage and armor not yet checked against a tooltip |
| `tools/import_cache.py` | client WDB cache -> `client/quests`, cache-only creatures in `client/npcs`, objects in `client/objects` | Ran on the owner's cache (build 1.60.1.70205, last written 2026-10-04): 46 quests, 350 creatures, 634 objects |
| `tools/addons/APISurface/` + `tools/import_api.py` + `tools/api_diff.py` | in-game API dump -> `data/api/<build>/` | Never run in a client. `data/api/` is empty |
| `tools/addons/OpenDataCollector/` + `tools/uploader.py` | in-game observations -> signed JSON batches in `.cache/batches/` | Never run in a client |
| `tools/reconcile.py` | batches -> `world/{loot,spawns,vendors,quest_givers,recipe_sources,npc_levels}` | Tested on synthetic batches only. `data/world/` is empty |
| `tools/savedvariables.py` | stdlib SavedVariables parser | Used by import_api and uploader |

`validate.py`: 0 errors, 20 warnings (references to quests and NPCs nobody has seen yet; expected, see decisions).

## Decisions made with the owner (do not relitigate)

- Items with an `Item` row but no `ItemSparse` row (12599 in 70374) are emitted as stubs with `sparse: false`: class, subclass, slot, icon only. Their names come from hotfixes or the server.
- Recipe spells under several SkillLines carry `skill_ids`.
- `client/quests` and `client/npcs` are partial by design (cache-fed), so a dangling reference into them is a warning, not an error. CONVENTIONS section 2 says so.
- NPC levels are observations and live in `world/npc_levels`, never in `client/npcs` (its `min_level`/`max_level` stay empty).
- Each tool owns its rows in a shared file by `source.kind`: the extractor writes `datamine` rows and keeps `client_cache` rows; the cache importer the reverse. The reconciler regenerates `observation` rows and keeps `emu_seed`/`manual`.
- No version bump for the contract and schema changes so far: nothing has been released. The first release is `2026.11.1`.

## Facts you need

- Forever is client 1.60.1 on the modern engine, Interface/TOC 16001. Launch is 2026-11-04.
- The owner's install: `/Applications/World of Warcraft/_classic_beta_/` (Cache/WDB/enUS, WTF/Account/*/SavedVariables, Interface/AddOns). No addons from this repo are installed there yet.
- wago.tools products as of 2026-10-22: `wow_classic_beta` (1.60.1.70338 ... 70532) and a new `wowf` (1.60.1.70245, 70532), which is likely the live product. Re-check with `python tools/extract_client.py --list-builds`. Be polite: serial requests, descriptive User-Agent.
- wago CSV quirks: arrays are `Name_0..Name_n`; reserved names get an underscore (`Lock._Index_k`).
- The GameObjects DB2 for 1.60 only has decorative objects (type 5). Every chest, herb and ore node comes from `gameobjectcache.wdb`.
- Lock entries of type 2 name a LockType (1 pick lock, 2 herbalism, 3 mining, 5 open), not a SkillLine. `required_skill_id` is the top-level SkillLine named like the LockType (Herbalism 182, Mining 186); Pick Lock has no match and keeps only `lock_type`.
- WDB layouts for 1.60 are documented at the top of `tools/import_cache.py`. Every record must decode to exactly its stored length or it is skipped and listed. Quest 97920 (a Forever skill objective) is the one known failure: its objective is 12 bytes longer and only one sample exists.
- The creature cache has no level, faction or npc flags. Quest XP and reputation are difficulty indexes in the cache; `import_cache.py` resolves them through `QuestXP` / `QuestFactionReward` (xp is the table value at quest level; the quest's `xp_multiplier` is kept beside it, not applied, since the server's scaling and rounding are not client data). Observed `quest_turnin` events record the XP actually received and can confirm this.
- Item stats, damage and armor are computed with the standard client formulas from `RandPropPoints`, `ItemDamage*`, `ItemArmor*`; raw inputs are on each row. `INV_BUCKET` (slot to budget column) and the caster-weapon flag `Flags_1 & 0x200` are the likeliest errors if a tooltip disagrees.
- Owner's other tools (separate, not public): `stefan-routes`, `Stockkeeper` (its `tools/sync.py` merges SavedVariables across accounts; `uploader.py --wow` follows the same pattern), `gearfinder.py`.

## Next, in order

### 1. In-game checks (owner, beta client)

- One caster weapon's damage range, one cloth chest's armor, one item's stats, against `data/client/items.jsonl`. If they match, add a CHANGELOG line and soften the README caveat.
- Quest 303 "The Dark Iron War" against `data/client/quests.jsonl` (level 30, min 25, 25s reward).

### 2. First real play session (owner)

Install `tools/addons/APISurface/` and `tools/addons/OpenDataCollector/`, play, log out. Then:

```bash
python tools/import_api.py "<install>/WTF/Account/<ACCOUNT>/SavedVariables/APISurface.lua"
python tools/uploader.py --wow "<install>" --contributor github:<owner>   # first run creates the key
python tools/reconcile.py .cache/batches
python tools/import_cache.py --wdb "<install>/Cache/WDB/enUS"            # cache is refreshed by playing
python tools/validate.py && python tools/build_sqlite.py
```

Expect first-contact breakage in the addons (they have never run): an event payload that differs in 1.60, a nil from an API that is missing. Errors print in chat as `APISurface: ...` / `OpenDataCollector: EVENT failed: ...`. Things to watch: whether the `C_Container.UseContainerItem` hook fires (bag-item loot), and whether herb/ore nodes resolve against `client/objects`.

### 3. Launch day (2026-11-04)

`tools/launch.py` runs the sequence (see its docstring): list builds, register and extract the live build, import the cache and API dump if present, validate, build. Tag `2026.11.1` after review; the release workflow builds SQLite + Parquet + SHA256SUMS.

### Later, not now

The 12-byte objective variant in quest 97920 once more samples exist; MCP server + OpenAPI over the SQLite; a static site; AH market aggregates (blocked on what Forever's addon API allows for AH scans); hosted ingest (Cloudflare Worker + R2) replacing the local batch directory; emulator seeding of `world/`.

## Open questions

- Is the launch product `wowf`? `--list-builds` on launch day answers it.
- Do the computed item numbers match tooltips? (step 1)
- Do the two addons work unmodified on 1.60? (step 2)
