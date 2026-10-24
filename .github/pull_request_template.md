## What and why

<!-- What changes, and why. For data: where it came from (tool, build, cache, observation, or typed in). -->

## Kind of change

- [ ] Code (`tools/`, `service/`, addons, workflows)
- [ ] Generated data (`data/client/`, `data/api/`, `data/world/`) from a tool run; say which tool and build
- [ ] Derived content (`data/derived/`); author, generator and criteria filled in
- [ ] Schema or conventions
- [ ] Docs

## Checklist

- [ ] `python tools/validate.py` prints 0 errors
- [ ] The tests for what I touched pass (see the table in [AGENTS.md](https://github.com/wowlayerup-forever/opendata/blob/main/AGENTS.md#before-every-commit))
- [ ] No hand edits to generated files; no invented IDs, names, rates or rewards; counts, not rates
- [ ] Schema changed? `examples/` updated, and a version bump if a field was removed or changed meaning
- [ ] A CHANGELOG line under "Unreleased"
- [ ] No secrets or cloud identifiers in tracked files, and no new `id-token` permission on a workflow that runs on pull requests
