# CLAUDE.md

Working rules for this repository. Read CONVENTIONS.md too; it is the data contract and it is short.

## What this repo is

An open, versioned game dataset for World of Warcraft: Forever, keyed by Blizzard IDs, for humans and AI agents to build on. Four layers (client, world, market, derived) plus the addon API surface. Data is CC BY 4.0, code is MIT. Public repo at github.com/wowlayerup-forever/opendata.

## Non-negotiables

- Never invent an ID, a name, a drop rate, or a quest reward. If it is not in a client table or an in-game observation, it does not go in `data/`.
- Never scrape Wowhead or any other site into this repo. Sources are: wago.tools DB2 exports, the client's WDB cache, in-game observations through the collector, openly licensed emulator DBs (marked `emu_seed`, `unverified`), or a contributor typing it in (marked `manual`).
- Facts carry counts, not rates. Opinion lives only in `data/derived/` and is attributed.
- An agent (you) may write `data/derived/` records with `generator.kind: agent` and the model named. An agent never authors `client`, `world` or `market` rows directly; those come from the tools.
- `data/client/` and `data/api/` are generated. Do not hand-edit them; fix the extractor.
- Keep `tools/` on the Python standard library plus `jsonschema`. No new runtime dependencies without a reason in the PR.
- Plain punctuation in docs and messages; no em dashes.

## Before every commit

```bash
python tools/validate.py                     # must print 0 errors
python tools/build_sqlite.py                 # must build; every table exists even when empty
```

If a schema changes: update `examples/`, bump nothing unless a field is removed or its meaning changes (then bump the major version in CONVENTIONS.md and the schema `$id`), and add a CHANGELOG line.

## Layout

```
builds.json            registry of client builds; every build string in data/ must be here
schemas/<layer>/*.schema.json
data/<layer>/<table>.jsonl        git-tracked JSON Lines, sorted by primary key
data/api/<build>/{functions,events}.jsonl
examples/              placeholder build 0.0.0.1, invented IDs, validated in CI, never released
tools/validate.py      schema, build registry, PK uniqueness, stray fields, cross-table ID refs
tools/build_sqlite.py  dist/opendata.sqlite (+ --parquet)
tools/extract_client.py  wago.tools DB2 -> data/client/{items,spells,recipes,npcs,objects}
tools/addons/APISurface/ in-game addon: dumps functions, C_ namespaces, documented events to SavedVariables
tools/import_api.py    APISurface SavedVariables -> data/api/<build>/{functions,events}.jsonl
tools/api_diff.py      diff the API surface of two builds
tools/savedvariables.py  SavedVariables (Lua subset) parser, shared by the importers
.github/workflows/     ci (PRs), release (tags YYYY.MM.N), extract (workflow_dispatch -> PR)
```

## Testing the extractor without network

wago.tools is not reachable from every environment. The transform can be tested offline against synthetic CSVs that use the real 1.60 column names (see WoWDBDefs, github.com/wowdev/WoWDBDefs, `definitions/<Table>.dbd`, layout block containing the build string). Write CSVs into `<cache>/<build>/<Table>.csv` and run:

```bash
python tools/extract_client.py --build 0.0.0.1 --cache /tmp/fx --out /tmp/fxout --offline
```

## Commit messages

Imperative subject, body explains what and why. Attribution trailers are fine.
