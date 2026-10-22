# APISurface

Dumps the addon API surface of the running client to SavedVariables, for `data/api/<build>/`.

1. Copy this folder to `<WoW>/<product dir>/Interface/AddOns/APISurface/`.
2. Disable every other addon (the importer refuses a dump made with third-party addons loaded).
3. Log in to any character. Chat prints how many functions and events were recorded.
4. `/reload` or log out so the client writes the file.
5. Import:

```bash
python tools/import_api.py "<WoW>/<product dir>/WTF/Account/<ACCOUNT>/SavedVariables/APISurface.lua"
python tools/validate.py
```

What it records:

- every function in `_G`, and every function in a `C_*` table or a namespace Blizzard_APIDocumentation names (`documented: false` unless the docs describe it). FrameXML's own Lua functions are in `_G` too and show up as undocumented.
- everything Blizzard_APIDocumentation describes, with arguments, returns and documentation. A documented function that is not callable at login gets `present: false`.
- events only from Blizzard_APIDocumentation; the client cannot list events.

The `.toc` declares `## Interface: 16001` (Forever 1.60.1). Bump it when the interface number changes.
