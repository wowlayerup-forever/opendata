# Handoff (2026-10-23, beta closed)

For a Claude Code session continuing this work locally. Read CLAUDE.md and CONVENTIONS.md first.

## Where things stand

HANDOFF tasks 1 to 5 from 2026-10-22 are done and merged (PRs #1 to #6). CI green.

| Tool | Does | State |
|---|---|---|
| `tools/extract_client.py` | wago.tools DB2 -> `client/{items,spells,recipes,npcs,objects}` | Ran for beta 1.60.1.70374. Computed stats, damage and armor not yet checked against a tooltip |
| `tools/import_cache.py` | client WDB cache -> `client/quests`, cache-only creatures in `client/npcs`, objects in `client/objects` | Ran on the owner's cache (build 1.60.1.70205, last written 2026-10-04): 46 quests, 350 creatures, 634 objects |
| `tools/addons/APISurface/` + `tools/import_api.py` + `tools/api_diff.py` | in-game API dump -> `data/api/<build>/` | Never run in a client (the beta closed first). `data/api/` is empty |
| `tools/addons/OpenDataCollector/` + `tools/uploader.py` | in-game observations -> signed JSON batches in `.cache/batches/` | Never run in a client (the beta closed first) |
| `tools/launch.py` | launch-day sequence, `--install-addons` copies both addons into the client | Dry-run only |
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
- The owner's beta install: `/Applications/World of Warcraft/_classic_beta_/`, last on build 1.60.1.70532 (not registered; no data from it). Both addons were copied into its `Interface/AddOns/` on 2026-10-23 but never loaded. Its cache was last written by 70205 on 2026-10-04 and is fully imported. The live client's folder is not known yet.
- wago.tools products as of 2026-10-22: `wow_classic_beta` (1.60.1.70338 ... 70532) and a new `wowf` (1.60.1.70245, 70532), which is likely the live product. Re-check with `python tools/extract_client.py --list-builds`. Be polite: serial requests, descriptive User-Agent.
- wago CSV quirks: arrays are `Name_0..Name_n`; reserved names get an underscore (`Lock._Index_k`).
- The GameObjects DB2 for 1.60 only has decorative objects (type 5). Every chest, herb and ore node comes from `gameobjectcache.wdb`.
- Lock entries of type 2 name a LockType (1 pick lock, 2 herbalism, 3 mining, 5 open), not a SkillLine. `required_skill_id` is the top-level SkillLine named like the LockType (Herbalism 182, Mining 186); Pick Lock has no match and keeps only `lock_type`.
- WDB layouts for 1.60 are documented at the top of `tools/import_cache.py`. Every record must decode to exactly its stored length or it is skipped and listed. Quest 97920 (a Forever skill objective) is the one known failure: its objective is 12 bytes longer and only one sample exists.
- The creature cache has no level, faction or npc flags. Quest XP and reputation are difficulty indexes in the cache; `import_cache.py` resolves them through `QuestXP` / `QuestFactionReward` (xp is the table value at quest level; the quest's `xp_multiplier` is kept beside it, not applied, since the server's scaling and rounding are not client data). Observed `quest_turnin` events record the XP actually received and can confirm this.
- Item stats, damage and armor are computed with the standard client formulas from `RandPropPoints`, `ItemDamage*`, `ItemArmor*`; raw inputs are on each row. `INV_BUCKET` (slot to budget column) and the caster-weapon flag `Flags_1 & 0x200` are the likeliest errors if a tooltip disagrees.
- Owner's other tools (separate, not public): `stefan-routes`, `Stockkeeper` (its `tools/sync.py` merges SavedVariables across accounts; `uploader.py --wow` follows the same pattern), `gearfinder.py`.

## Next, in order

The Forever beta closed on 2026-10-23 before the addons could run, so every in-game step moves to launch day. Nothing can be checked in a client until then. Both addons have never run: expect the first live session to need fixes.

### Launch day (2026-11-04)

1. **Extract and install.** `python tools/launch.py --dry-run --install-addons`, check the build and install it picked (the live client likely installs into a new product folder such as `_classic_`; pass `--install` if unsure), then the same without `--dry-run`. This registers and extracts the live build (unchanged rows keep their beta `build_first_seen`) and copies APISurface and OpenDataCollector into `Interface/AddOns/`.
2. **Play (owner).** Quit and relaunch the client (new addon folders are not picked up by `/reload`). If the AddOns list says out of date, the `.toc` interface is not 16001: fix `## Interface:` in `tools/addons/*/*.toc` and run step 1 again. In game: log in (chat prints `APISurface: recorded N functions...`), kill and loot, open a vendor, accept and turn in a quest, visit a trainer, gather a node; `/odc` shows the count. Errors print as `APISurface: dump failed: ...` / `OpenDataCollector: EVENT failed: ...`. Log out.
3. **Import.** First time only: `python tools/uploader.py --wow "<install>" --contributor github:<owner>` to create the key. Then `python tools/launch.py` again: it fills the interface number into builds.json, warns if it differs from the `.toc` files, and imports the API dump, the refreshed client cache and the observations, then validates and builds. OpenDataCollector is loaded during the API dump but adds no global functions, so `launch.py` allows it; any other loaded addon makes the API import refuse.
4. **Check (owner, in game).** One caster weapon's damage range, one cloth chest's armor, one item's stats against `data/client/items.jsonl`; one quest's level, XP and money against `data/client/quests.jsonl` and the XP the collector recorded at turn-in. If they match, add a CHANGELOG line and soften the README caveat. If not, debug from the raw inputs on the row (`INV_BUCKET` first).
5. **Release.** Branch, commit `builds.json` and `data/`, PR, merge, then tag `2026.11.1`; the release workflow builds SQLite + Parquet + SHA256SUMS.

Things to watch on first contact: event payloads that differ in 1.60, an API that is missing, whether the `C_Container.UseContainerItem` hook fires (bag-item loot), and whether herb and ore nodes resolve against `client/objects`.

### Later, not now

The 12-byte objective variant in quest 97920 once more samples exist; MCP server + OpenAPI over the SQLite; a static site; AH market aggregates (blocked on what Forever's addon API allows for AH scans); hosted ingest (Cloudflare Worker + R2) replacing the local batch directory; emulator seeding of `world/`.

## Open questions

- Is the launch product `wowf`? `--list-builds` on launch day answers it.
- Do the computed item numbers match tooltips? (launch day, step 4)
- Do the two addons work unmodified on 1.60? (launch day, step 2)
- Is the live interface number still 16001? (launch day, step 2)
