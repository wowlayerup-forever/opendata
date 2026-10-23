# OpenDataCollector

Records what you see in game for the opendata `world/` layer: loot windows, merchant windows, quest accepts and turn-ins, trainer windows, and creature levels. Only Blizzard IDs, counts, prices, levels and map positions are stored. No character names, chat, or player GUIDs.

## Use

1. Copy this folder to `<WoW>/<product dir>/Interface/AddOns/OpenDataCollector/`.
2. Play. `/odc` shows how much is recorded.
3. Log out (or `/reload`) so the client writes SavedVariables, then:

```bash
python tools/uploader.py --wow "/Applications/World of Warcraft/_classic_beta_" --contributor github:<you>   # first time
python tools/uploader.py --wow "/Applications/World of Warcraft/_classic_beta_"                             # after that
```

4. `/odc reset` in game to clear the buffer. Uploading twice before a reset is safe.

## What each event means

| event | when | what the reconciler gets from it |
|---|---|---|
| loot | `LOOT_OPENED`, one per source GUID (AoE loot is split) | `world/loot` drops per loot window; corpse position for `world/spawns`. The last spell cast within 5 s is kept so skinning, herbalism, mining and pickpocket can be told apart |
| vendor | `MERCHANT_SHOW` | `world/vendors`. Prices are as shown, so reputation discounts are in them; the reconciler takes the highest price seen |
| quest_accept / quest_turnin | `QUEST_ACCEPTED`, `QUEST_TURNED_IN` | `world/quest_givers` start and end, with position; XP and money actually received |
| trainer | `TRAINER_SHOW` | `world/recipe_sources`, matched to recipes by created item. Only what the trainer frame lists with its current filter |
| sighting | nameplate, target, mouseover; once per spawn per session | `world/npc_levels` |

Limits: loot from bag items (clams, lockboxes) is attributed only if the item was used from a bag in the last 5 s; corpses with nothing to loot do not open a window, so they are not counted.

The `.toc` declares `## Interface: 16001`.
