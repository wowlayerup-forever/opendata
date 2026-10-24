# Security

Report security problems **privately** through GitHub: [open a security advisory](https://github.com/wowlayerup-forever/opendata/security/advisories/new). Do not open a public issue for them.

In scope: the ingest service (`service/`, `POST /v1/batches`), contributor key handling (`tools/uploader.py`, `tools/opendata_uploader.py`), the GitHub workflows and anything that could let someone publish data, deploy, or read the private batch store, and the addon and uploader downloads.

Out of scope: the content of the data itself (open a "Wrong data" issue), and the public read-only endpoints serving public data (the release bucket and the hosted MCP server), except where they can be made to do something else.

Contributor keys are personal. If yours may have leaked, say so in your contributor-key issue or open a new one; it will be revoked and replaced.
