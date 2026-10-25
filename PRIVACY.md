# Privacy

What happens to the observations you contribute with OpenDataCollector and `opendata-uploader`. This is a plain description of what the code in this repository does, not legal advice.

## What the addon records

Per play session, as Blizzard IDs and numbers:

- **Loot windows:** the creature, object or bag item looted (with its spawn ID), the items and amounts, money, the spell cast just before (to tell skinning or mining from normal loot).
- **Merchant windows:** the vendor, the items it sells, prices and limited stock.
- **Quests:** which quest you accepted or turned in, at which NPC or object, and the XP and money you received.
- **Trainer windows:** the trainer and the recipes it lists with their cost.
- **Creature levels** seen on nameplates, target and mouseover.
- For each of these: the time, your map position, and your character's level. For the session: the client build and language, your faction, and the addon's version.

It does **not** record character names (yours or anyone else's), realm, guild, chat, other players' GUIDs, or anything outside the game.

## What the uploader sends

The session batches above, signed with your contributor key, over HTTPS to the project's ingest service. Each batch carries your contributor handle (for example `github:you`), which is how the project knows who sent it and can remove it later. Without a key, nothing is sent; batches stay in `~/.config/opendata/batches/` on your machine.

The uploader reads only OpenDataCollector's SavedVariables file.

## Where it is kept and who can see it

- **Raw batches** are stored in a private Google Cloud Storage bucket in the US, with object versioning, and a backup copy in a second US region. Only the maintainers and two automated service accounts (the ingest service, which writes, and the nightly reconcile job, which reads) can access them.
- **Request logs:** Google Cloud keeps request logs for the ingest service (including your IP address and the uploader's version) for 30 days.
- **What becomes public** is only the aggregate in `data/world/` and the releases: counts per creature, object and item, clustered spawn points, vendor prices, quest givers, creature levels. It contains no contributor handles, no timestamps and nothing per contributor.
- **The nightly reconcile pull request** includes a data-quality report with per-contributor volumes. Contributors appear there only as pseudonyms derived with a secret key, so they cannot be matched to a handle from outside.

## How long

Raw batches are kept for as long as the dataset is maintained: every nightly run rebuilds `data/world/` from all of them, so they are the source of truth.

## Removing your data

Ask for deletion by email to [wowlayerup@gmail.com](mailto:wowlayerup@gmail.com), or in your contributor-key issue (do not post private details there). The maintainers then:

1. revoke your key, so nothing more is accepted;
2. delete all your batches, including older versions, from the bucket and the backup;
3. let the next nightly run rebuild `data/world/` without them.

Deleted objects can remain in the storage provider's soft-delete state, and in backups, for up to 90 days before they are gone for good, and request logs expire after 30 days. Releases already published contain only aggregates, which do not identify you, and are not changed. Your local copies under `~/.config/opendata/` are yours to delete.

## Questions

[wowlayerup@gmail.com](mailto:wowlayerup@gmail.com).
