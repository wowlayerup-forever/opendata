#!/usr/bin/env python3
"""opendata Cloud Run service: batch ingest and public MCP over HTTP. Standard library only.

One image, two modes (MODE env var), deployed as two Cloud Run services with different
service accounts so the public read side holds no write permission:

  MODE=ingest   POST /v1/batches     signed observation batches from tools/uploader.py
  MODE=mcp      POST /mcp            Model Context Protocol, Streamable HTTP transport
  both          GET  /healthz

Ingest: a batch is accepted only if its HMAC verifies with the key registered for its
contributor (see infra/contributors.py). It is stored as batches/<contributor>/<batch_id>.json.
Re-sending the same batch is a no-op (200 unchanged); a batch that extends the stored one
(the same session uploaded again later) replaces it (200 grown); anything else under the
same id is refused (409). Writes are conditional on the object generation, so two
concurrent uploads cannot lose events. The bucket keeps object versions.

MCP: tools/mcp_server.py's protocol handling behind HTTP. Each POST carries one JSON-RPC
message (or a batch); requests get application/json, notifications 202. No SSE stream:
GET /mcp is 405, which the transport allows. The SQLite file is fetched from the public
releases bucket at start and re-checked every RELOAD_S seconds, so a new release is served
without a redeploy. Read-only, no credentials, public data: any Origin is allowed.

Configuration (env):
  MODE                 ingest | mcp
  PORT                 set by Cloud Run (default 8080)
  BATCH_STORE          gs://bucket[/prefix] or file:///dir          (ingest)
  CONTRIBUTOR_KEYS     sm://projects/<p>/secrets/<s> or file:///path.json   (ingest)
  RELEASE_DB           gs://bucket/object or file:///path.sqlite    (mcp)
  MAX_BATCH_BYTES      default 8 MiB
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import mcp_server  # noqa: E402

MAX_BATCH_BYTES = int(os.environ.get("MAX_BATCH_BYTES", 8 * 1024 * 1024))
MAX_EVENTS = 200_000
KEY_CACHE_S = 300
RELOAD_S = int(os.environ.get("RELOAD_S", 900))
EVENT_TYPES = {"loot", "vendor", "quest_accept", "quest_turnin", "trainer", "sighting"}
BATCH_ID = re.compile(r"[0-9a-f]{16,64}")
CONTRIBUTOR = re.compile(r"[a-z]+:[A-Za-z0-9_.-]{1,64}")
BUILD = re.compile(r"\d+\.\d+\.\d+\.\d+")


def log(**fields) -> None:
    """One JSON line per event; Cloud Logging parses it into a structured entry."""
    print(json.dumps({"severity": "INFO", **fields}, separators=(",", ":")), flush=True)


# ───────────────────────────── Google APIs over REST ─────────────────────────────

_token: dict = {"value": None, "expires": 0.0}
_token_lock = threading.Lock()


def access_token() -> str:
    with _token_lock:
        if _token["value"] and time.time() < _token["expires"] - 60:
            return _token["value"]
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=10) as r:
            tok = json.load(r)
        _token.update(value=tok["access_token"], expires=time.time() + tok["expires_in"])
        return _token["value"]


def google(method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": f"Bearer {access_token()}", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# ───────────────────────────── batch storage ─────────────────────────────

class Conflict(Exception):
    pass


class FileStore:
    """Local directory, for tests and local runs. Generation = file mtime_ns."""

    def __init__(self, root: str):
        self.root = Path(root)
        self.lock = threading.Lock()

    def get(self, name: str) -> tuple[bytes, str] | None:
        p = self.root / name
        return (p.read_bytes(), str(p.stat().st_mtime_ns)) if p.exists() else None

    def put(self, name: str, data: bytes, if_generation: str | None) -> None:
        with self.lock:
            p = self.root / name
            current = str(p.stat().st_mtime_ns) if p.exists() else "0"
            if (if_generation or "0") != current:
                raise Conflict(name)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(p)


class GcsStore:
    def __init__(self, bucket: str, prefix: str):
        self.bucket, self.prefix = bucket, prefix.strip("/")

    def _name(self, name: str) -> str:
        return f"{self.prefix}/{name}" if self.prefix else name

    def get(self, name: str) -> tuple[bytes, str] | None:
        obj = urllib.parse.quote(self._name(name), safe="")
        status, headers, body = google("GET", f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{obj}?alt=media")
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"GCS get {name}: {status} {body[:200]!r}")
        return body, headers.get("x-goog-generation") or headers.get("X-Goog-Generation", "")

    def put(self, name: str, data: bytes, if_generation: str | None) -> None:
        q = urllib.parse.urlencode({"uploadType": "media", "name": self._name(name), "ifGenerationMatch": if_generation or "0"})
        status, _, body = google("POST", f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o?{q}", data,
                                 {"Content-Type": "application/json"})
        if status == 412:
            raise Conflict(name)
        if status not in (200, 201):
            raise RuntimeError(f"GCS put {name}: {status} {body[:200]!r}")


def make_store(url: str):
    if url.startswith("file://"):
        return FileStore(url[len("file://"):])
    if url.startswith("gs://"):
        bucket, _, prefix = url[len("gs://"):].partition("/")
        return GcsStore(bucket, prefix)
    raise SystemExit(f"BATCH_STORE must be gs:// or file://, not {url!r}")


# ───────────────────────────── contributor keys ─────────────────────────────

class Keys:
    """{contributor: hex key}, from a JSON file or the latest Secret Manager version, cached."""

    def __init__(self, url: str):
        self.url = url
        self.cache: dict[str, str] = {}
        self.loaded = 0.0
        self.lock = threading.Lock()

    def get(self, contributor: str) -> str | None:
        with self.lock:
            if time.time() - self.loaded > KEY_CACHE_S:
                self.cache = self._load()
                self.loaded = time.time()
            return self.cache.get(contributor)

    def _load(self) -> dict[str, str]:
        if self.url.startswith("file://"):
            return json.loads(Path(self.url[len("file://"):]).read_text(encoding="utf-8"))
        if self.url.startswith("sm://"):
            status, _, body = google("GET", f"https://secretmanager.googleapis.com/v1/{self.url[len('sm://'):]}/versions/latest:access")
            if status != 200:
                raise RuntimeError(f"Secret Manager: {status} {body[:200]!r}")
            return json.loads(base64.b64decode(json.loads(body)["payload"]["data"]))
        raise SystemExit(f"CONTRIBUTOR_KEYS must be sm:// or file://, not {self.url!r}")


def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signature_ok(batch: dict, key_hex: str) -> bool:
    body = {k: v for k, v in batch.items() if k != "signature"}
    want = "hmac-sha256:" + hmac.new(bytes.fromhex(key_hex), canonical(body), hashlib.sha256).hexdigest()
    return hmac.compare_digest(str(batch.get("signature", "")), want)


# ───────────────────────────── ingest ─────────────────────────────

def ingest(batch, store, keys: Keys) -> tuple[int, dict]:
    if not isinstance(batch, dict):
        return 400, {"error": "body must be a JSON object"}
    contributor, bid = str(batch.get("contributor", "")), str(batch.get("batch_id", ""))
    if batch.get("format") != 1:
        return 400, {"error": "unknown batch format"}
    if not CONTRIBUTOR.fullmatch(contributor) or not BATCH_ID.fullmatch(bid):
        return 400, {"error": "bad contributor or batch_id"}
    if not BUILD.fullmatch(str(batch.get("build", ""))):
        return 400, {"error": "bad build"}
    events = batch.get("events")
    if not isinstance(events, list) or not events or len(events) > MAX_EVENTS:
        return 400, {"error": f"events must be a non-empty list of at most {MAX_EVENTS}"}
    if any(not isinstance(e, dict) or e.get("type") not in EVENT_TYPES for e in events):
        return 400, {"error": "unknown event type"}
    key = keys.get(contributor)
    if key is None:
        return 403, {"error": f"{contributor} is not a registered contributor"}
    if not signature_ok(batch, key):
        return 401, {"error": "signature does not verify"}

    name = f"batches/{contributor.replace(':', '/')}/{bid}.json"
    data = json.dumps(batch, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    for _ in range(3):  # retry only on a concurrent write between our read and our write
        existing = store.get(name)
        if existing is None:
            status, generation = "stored", None
        else:
            old = json.loads(existing[0])
            n = len(old.get("events", []))
            if old.get("events") == events:
                return 200, {"status": "unchanged", "batch_id": bid, "events": n}
            if old.get("events") != events[:n]:
                return 409, {"error": "a different batch is stored under this id", "batch_id": bid}
            status, generation = "grown", existing[1]
        try:
            store.put(name, data, generation)
        except Conflict:
            continue
        log(event="ingest", status=status, contributor=contributor, batch_id=bid, events=len(events), build=batch["build"])
        return (201 if status == "stored" else 200), {"status": status, "batch_id": bid, "events": len(events)}
    return 409, {"error": "concurrent writes to this batch; retry", "batch_id": bid}


# ───────────────────────────── MCP database ─────────────────────────────

class ReleaseDb:
    """Keeps a local copy of the release SQLite and swaps in a new one when it changes."""

    def __init__(self, url: str, local: Path):
        self.url, self.local = url, local
        self.generation = None
        self.data = mcp_server.Data(local)
        self.checked = 0.0
        self.lock = threading.Lock()

    def current(self) -> mcp_server.Data:
        with self.lock:
            if time.time() - self.checked > RELOAD_S or self.generation is None:
                self.checked = time.time()
                try:
                    self._refresh()
                except Exception as e:  # keep serving the copy we have
                    log(severity="WARNING", event="release_refresh_failed", error=str(e))
            return self.data

    def _refresh(self) -> None:
        if self.url.startswith("file://"):
            src = Path(self.url[len("file://"):])
            gen = str(src.stat().st_mtime_ns) if src.exists() else None
            fetch = (lambda: src.read_bytes()) if gen else None
        elif self.url.startswith("gs://"):
            # the releases bucket is public: read it anonymously, no token needed
            bucket, _, obj = self.url[len("gs://"):].partition("/")
            meta_url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{urllib.parse.quote(obj, safe='')}"
            status, body = public_get(meta_url)
            gen = json.loads(body)["generation"] if status == 200 else None
            fetch = (lambda: public_get(meta_url + "?alt=media", expect_ok=True)[1]) if gen else None
        else:
            raise SystemExit(f"RELEASE_DB must be gs:// or file://, not {self.url!r}")
        if gen is None or gen == self.generation:
            return
        tmp = self.local.with_suffix(".download")
        tmp.write_bytes(fetch())
        new = mcp_server.Data(self.local)
        old = self.data
        tmp.replace(self.local)
        self.data, self.generation = new, gen
        if old.con is not None:
            old.con.close()
        log(event="release_loaded", generation=gen, bytes=self.local.stat().st_size)


def public_get(url: str, expect_ok: bool = False) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        if expect_ok:
            raise RuntimeError(f"GET {url}: {e.code}")
        return e.code, e.read()


# ───────────────────────────── HTTP ─────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "opendata/1"
    mode = ""
    store = None
    keys: Keys | None = None
    release: ReleaseDb | None = None

    def log_message(self, fmt, *args):  # structured logs instead
        pass

    def reply(self, status: int, payload=None, extra: dict | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if self.mode == "mcp":
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> bytes | None:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BATCH_BYTES:
            self.reply(413, {"error": f"body over {MAX_BATCH_BYTES} bytes"})
            return None
        return self.rfile.read(n)

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"ok": True, "mode": self.mode})
        if self.mode == "mcp" and self.path.split("?")[0] == "/mcp":
            return self.reply(405, {"error": "this server does not offer an SSE stream; POST JSON-RPC"}, {"Allow": "POST"})
        self.reply(404, {"error": "not found"})

    def do_OPTIONS(self):
        if self.mode != "mcp":
            return self.reply(404, {"error": "not found"})
        self.reply(204, None, {"Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                               "Access-Control-Allow-Headers": "Content-Type, Accept, Mcp-Session-Id, MCP-Protocol-Version",
                               "Access-Control-Max-Age": "86400"})

    def do_DELETE(self):
        # session termination: we keep no sessions
        self.reply(405 if self.mode == "mcp" else 404, {"error": "no sessions to end"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if self.mode == "ingest" and path == "/v1/batches":
            body = self.read_body()
            if body is None:
                return
            try:
                batch = json.loads(body)
            except ValueError:
                return self.reply(400, {"error": "body is not JSON"})
            try:
                status, payload = ingest(batch, self.store, self.keys)
            except Exception as e:
                log(severity="ERROR", event="ingest_failed", error=str(e))
                return self.reply(500, {"error": "storage unavailable, retry later"})
            return self.reply(status, payload)
        if self.mode == "mcp" and path == "/mcp":
            body = self.read_body()
            if body is None:
                return
            try:
                msg = json.loads(body)
            except ValueError:
                return self.reply(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            data = self.release.current()
            msgs = msg if isinstance(msg, list) else [msg]
            replies = [r for r in (mcp_server.handle(data, m) for m in msgs if isinstance(m, dict)) if r]
            if any(isinstance(m, dict) and m.get("method") == "tools/call" for m in msgs):
                log(event="mcp_call", tools=[m.get("params", {}).get("name") for m in msgs if isinstance(m, dict)])
            if not replies:
                return self.reply(202)
            return self.reply(200, replies if isinstance(msg, list) else replies[0])
        self.reply(404, {"error": "not found"})


def main() -> int:
    mode = os.environ.get("MODE", "")
    Handler.mode = mode
    if mode == "ingest":
        Handler.store = make_store(os.environ["BATCH_STORE"])
        Handler.keys = Keys(os.environ["CONTRIBUTOR_KEYS"])
    elif mode == "mcp":
        Handler.release = ReleaseDb(os.environ["RELEASE_DB"], Path(os.environ.get("DB_CACHE", "/tmp/opendata.sqlite")))
        Handler.release.current()
    else:
        raise SystemExit("MODE must be ingest or mcp")
    port = int(os.environ.get("PORT", 8080))
    log(event="start", mode=mode, port=port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
