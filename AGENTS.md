# AGENTS.md

Working rules for this repository, for people and coding agents alike (Claude Code reads it through CLAUDE.md). Read [CONVENTIONS.md](CONVENTIONS.md) too: it is the data contract, and it is short.

## What this repo is

An open, versioned game dataset for World of Warcraft: Forever, keyed by Blizzard IDs, for people and AI agents to build on. Layers: `client` (exact client data), `world` (player observations), `market` (AH aggregates), `derived` (attributed opinion), plus `api` (the addon API per build). Data is CC BY 4.0, code is MIT. Public repo at github.com/wowlayerup-forever/opendata. [HANDOFF.md](HANDOFF.md) holds the maintainers' current state and next steps.

## Non-negotiables

- **Never invent** an ID, a name, a drop rate, a quest reward or a field meaning. If it is not in a client table, the client cache, or an in-game observation, it does not go in `data/`. When a binary layout or a field's meaning is unclear, leave the field out and say so; do not guess from memory of other WoW versions.
- **Never scrape** Wowhead or any other site into this repo. Sources are: wago.tools DB2 exports, the client's WDB cache, in-game observations through the collector, openly licensed emulator DBs (`emu_seed`, `unverified`), or a contributor typing it in (`manual`).
- **Counts, not rates.** Opinion lives only in `data/derived/`, attributed.
- **Agents** may write `data/derived/` records with `generator.kind: agent` and the model named. An agent never authors `client`, `world` or `market` rows directly; those come from the tools.
- **`data/client/` and `data/api/` are generated.** Never hand-edit them; fix the extractor or importer and regenerate.
- **Dependencies:** `tools/` stays on the Python standard library plus `jsonschema` (build-time tools like PyInstaller are fine in workflows). No new runtime dependency without a reason in the PR.
- **Public repo, so no infrastructure details.** No GCP identifiers (project id or number, billing account, service account emails, bucket names other than the public release URL, Workload Identity paths) and no secrets in any tracked file. Workflows read them from repository secrets, which are masked in the public logs. The concrete infrastructure is in a private repository.
- **Workflow security.** Only workflows that run on `main` pushes, schedules, manual dispatch or tags may have `id-token: write`. Never give it to a workflow that runs on pull requests, never use `pull_request_target`, and gate every cloud job on `github.repository == 'wowlayerup-forever/opendata'`.
- **Pin every action to a full commit SHA** with the version in a comment (`uses: owner/action@<40-char sha> # vX.Y.Z`). Dependabot updates both. Never reference a branch or a bare tag.
- **Raw observations stay out of git.** Batches live in object storage; only reconciled aggregates are committed. Test fixtures under `tests/` are synthetic and must never reach `data/`.
- Plain punctuation in docs and messages; no em dashes.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
brew install luajit        # or: apt-get install luajit   (only for tests/run_addons.py)
```

Python 3.11+. Docker is not needed locally; CI builds the service image.

## Before every commit

```bash
python tools/validate.py                     # must print 0 errors (warnings about unseen quests/NPCs are expected)
python tools/build_sqlite.py                 # must build; every table exists even when empty
python tests/run_addons.py                   # when tools/addons/, uploader, reconcile or import_api change (needs luajit)
python tests/test_mcp_server.py              # when tools/mcp_server.py or the SQLite layout changes
python tests/test_service.py                 # when service/ or the uploader changes
python tools/package_addon.py OpenDataCollector --check   # when an addon's files or .toc change
```

If a schema changes: update `examples/`, bump nothing unless a field is removed or its meaning changes (then bump the major version in CONVENTIONS.md and the schema `$id`), and add a CHANGELOG line.

## How changes land

- `main` is protected: open a PR; the `validate`, `addons` and `image` checks must pass. Merging to `main` deploys the Cloud Run services automatically when `service/` or `tools/mcp_server.py` changed.
- Commit messages: imperative subject; the body says what and why. Attribution trailers are fine.
- Every user-visible change gets a CHANGELOG line under "Unreleased".
- **Dataset release:** tag `YYYY.MM.N` on `main` (`.0` = pre-release). The release workflow builds SQLite + Parquet, attaches them to a GitHub release, and publishes to the public bucket the hosted MCP reads.
- **Addon release:** bump `## Version:` in `tools/addons/OpenDataCollector/OpenDataCollector.toc` in a PR, then tag `collector-vX.Y.Z` (must match). The workflow runs the addon tests, builds the addon zip and the standalone uploader for four platforms, and publishes them. `0.x` = pre-release.
- PRs opened by the nightly `reconcile` and by `extract` use the built-in token, which does not trigger CI; they validate inside their own workflow, and a maintainer merges them with the admin bypass.

## Layout

```
builds.json            registry of client builds; every build string in data/ must be here
schemas/<layer>/*.schema.json
data/<layer>/<table>.jsonl        git-tracked JSON Lines, sorted by primary key
data/api/<build>/{functions,events}.jsonl
examples/              placeholder build 0.0.0.1, invented IDs, validated in CI, never released
tools/validate.py      schema, build registry, PK uniqueness, stray fields, cross-table ID refs
tools/build_sqlite.py  dist/opendata.sqlite (+ --parquet)
tools/extract_client.py  wago.tools DB2 -> data/client/{items,spells,recipes,npcs,objects}; keeps build history
tools/import_cache.py  client WDB cache -> client/quests, cache-only creatures and objects in npcs, objects
tools/addons/APISurface/   in-game addon (maintainers): dumps the API to SavedVariables
tools/import_api.py    APISurface SavedVariables -> data/api/<build>/; tools/api_diff.py diffs two builds
tools/addons/OpenDataCollector/  in-game observation collector (players)
tools/uploader.py      collector SavedVariables -> signed JSON batches (local dir, and the ingest service)
tools/opendata_uploader.py  player app around uploader.py (double-click); frozen with PyInstaller in CI
tools/package_addon.py .toc checks + reproducible addon zip
tools/reconcile.py     batches -> data/world/*.jsonl, deterministic
tools/savedvariables.py  SavedVariables (Lua subset) parser, shared by the importers
tools/launch.py        launch-day sequence over the tools above (--dry-run first)
tools/mcp_server.py    MCP server (stdio) over the SQLite; read-only, cites rows
service/               Cloud Run image (stdlib): MODE=ingest (POST /v1/batches) or MODE=mcp (MCP over HTTP)
tests/                 addons under LuaJIT against a mocked WoW API (wowmock.lua); MCP server; service; uploader smoke test
.github/workflows/     ci (PRs; no cloud access), deploy (main -> Cloud Run), reconcile (nightly -> PR),
                       release (YYYY.MM.N -> GitHub release + public bucket), addon-release (collector-v* -> addon + uploader),
                       uploader-build (reused by ci and addon-release), extract (manual -> PR)
```

## Things that have bitten us

- **wago CSV quirks:** arrays are `Name_0..Name_n`; reserved column names get an underscore (`Lock._Index_k`).
- **The GameObjects DB2 ships only decorative objects.** Chests, herbs and ore nodes come from `gameobjectcache.wdb`.
- **Lock entries of type 2 name a LockType, not a SkillLine.** `required_skill_id` is derived only where a top-level SkillLine has the LockType's exact name.
- **The creature cache has no levels.** Levels are observations (`world/npc_levels`).
- **Cache record layouts change per build.** `import_cache.py` decodes 1.60 layouts and refuses any record that does not add up to its exact length; it never guesses. Keep it that way.
- **`validate.py` and `build_sqlite.py` do not follow symlinked directories.** Test roots must copy (hard-link) `data/client`, or the reference checks silently do not run.
- **Cloud Run reserves `/healthz`**; the service uses `/health`.
- **`macos-13` runners are gone**; Intel macOS builds use `macos-15-intel`.

## Testing the extractor without network

wago.tools is not reachable from every environment. The transform can be tested offline against synthetic CSVs that use the real 1.60 column names (see WoWDBDefs, github.com/wowdev/WoWDBDefs, `definitions/<Table>.dbd`, layout block containing the build string). Write CSVs into `<cache>/<build>/<Table>.csv` and run:

```bash
python tools/extract_client.py --build 0.0.0.1 --cache /tmp/fx --out /tmp/fxout --offline
```

## Repository secrets the cloud workflows read

`GCP_PROJECT`, `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`, `GCP_RECONCILE_SA`, `GCP_RELEASE_SA`, `GCP_INGEST_SA`, `GCP_MCP_SA`, `GCP_BATCH_BUCKET`, `GCP_RELEASE_BUCKET`. They are set from the private infrastructure repo. In a fork they are absent, so deploy and reconcile skip with a notice and release skips the bucket upload.
