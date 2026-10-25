#!/usr/bin/env python3
"""Run service/app.py locally in both modes, against file:// storage and keys, and check it.

  python tests/test_service.py

Ingest: tools/uploader.py --endpoint against the service; idempotent re-send, a grown
session, a same-id conflict, bad signature, unknown contributor, oversized and malformed
bodies, unknown event types. MCP: the Streamable HTTP transport (POST JSON-RPC, 202 for
notifications, 405 for GET, CORS preflight), a tool call, and serving a new release file
without a restart.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / ".cache" / "tests" / "service"
failures: list[str] = []
sys.path.insert(0, str(REPO / "tools"))
import uploader  # noqa: E402


def check(cond: bool, what: str) -> None:
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        failures.append(what)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(env: dict) -> tuple[subprocess.Popen, str]:
    port = free_port()
    p = subprocess.Popen([sys.executable, "service/app.py"], cwd=REPO, env={**os.environ, **env, "PORT": str(port)},
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/health", timeout=1)
            return p, base
        except OSError:
            time.sleep(0.05)
    p.kill()
    raise SystemExit("service did not start:\n" + (p.stdout.read() if p.stdout else ""))


def http(method: str, url: str, body=None, headers: dict | None = None) -> tuple[int, dict, object]:
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, dict(r.headers), json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, dict(e.headers), json.loads(raw) if raw else None
        except ValueError:
            return e.code, dict(e.headers), raw


def batch(key: dict, bid: str, events: list[str]) -> dict:
    raw = {"id": bid, "build": "1.60.1.70374", "locale": "enUS", "faction": "Alliance", "started": 1, "events": events}
    b, _ = uploader.make_batch(raw, key)
    return b


def ingest_tests() -> None:
    print("ingest")
    d = OUT / "ingest"
    store, kdir = d / "store", d / "keys"
    store.mkdir(parents=True)
    kdir.mkdir()
    me = {"contributor": "github:harness", "key": "11" * 32}
    keys_file = d / "contributor-keys.json"
    keys_file.write_text(json.dumps({me["contributor"]: me["key"]}))
    p, base = start({"MODE": "ingest", "BATCH_STORE": f"file://{store}", "CONTRIBUTOR_KEYS": f"file://{keys_file}",
                     "MAX_BATCH_BYTES": "200000"})
    url = base + "/v1/batches"
    try:
        T = "\t"
        e1 = T.join(["L", "100", "C:568:SPAWN1", "0", "1437", "53.2", "40.1", "", "20", "2589:2"])
        e2 = T.join(["S", "101", "568", "SPAWN1", "19"])
        b1 = batch(me, "00000000000000000000bb01", [e1])
        s, _, r = http("POST", url, b1)
        check(s == 201 and r["status"] == "stored", "new batch stored (201)")
        stored = store / "batches" / "github" / "harness" / "00000000000000000000bb01.json"
        check(stored.exists(), "stored under batches/<kind>/<handle>/<batch_id>.json")
        s, _, r = http("POST", url, b1)
        check(s == 200 and r["status"] == "unchanged", "re-send is a no-op (200 unchanged)")
        s, _, r = http("POST", url, batch(me, "00000000000000000000bb01", [e1, e2]))
        check(s == 200 and r["status"] == "grown" and len(json.loads(stored.read_text())["events"]) == 2, "grown session replaces it")
        s, _, r = http("POST", url, batch(me, "00000000000000000000bb01", [e2]))
        check(s == 409, "different content under the same id refused (409)")
        bad = dict(b1, batch_id="00000000000000000000bb02")
        s, _, _ = http("POST", url, bad)
        check(s == 401, "signature over a modified batch refused (401)")
        s, _, _ = http("POST", url, batch({"contributor": "github:stranger", "key": "22" * 32}, "00000000000000000000bb03", [e1]))
        check(s == 403, "unregistered contributor refused (403)")
        s, _, _ = http("POST", url, b"{" + b" " * 300000 + b"}")
        check(s == 413, "oversized body refused (413)")
        s, _, _ = http("POST", url, b"not json")
        check(s == 400, "malformed body refused (400)")
        odd = batch(me, "00000000000000000000bb04", [e1])
        odd["events"][0]["type"] = "teleport"
        s, _, _ = http("POST", url, odd)
        check(s == 400, "unknown event type refused (400)")
        s, _, _ = http("GET", base + "/v1/batches")
        check(s == 404, "nothing to GET on ingest")
        t0 = time.time()
        s, _, r = http("POST", url, {"format": 1, "contributor": "github:harness", "batch_id": "00000000000000000000bb09",
                                     "build": "1" * 150000 + "x", "events": [{"type": "loot"}]})
        check(s == 400 and time.time() - t0 < 2, f"oversized field rejected fast ({time.time() - t0:.2f}s)")

        # the real uploader, end to end, with the endpoint taken from the key file
        key_file = kdir / "contributor.json"
        key_file.write_text(json.dumps({**me, "endpoint": url}))
        sv = d / "OpenDataCollector.lua"
        ev = '"' + e1.replace("\t", "\\t") + '"'
        sv.write_text('OpenDataCollectorDB = { ["format"] = 1, ["batches"] = { { ["id"] = "00000000000000000000bb05", '
                      '["build"] = "1.60.1.70374", ["locale"] = "enUS", ["events"] = { ' + ev + ' } } } }\n')
        env = {**os.environ, "OPENDATA_KEY_FILE": str(key_file)}
        r = subprocess.run([sys.executable, "tools/uploader.py", str(sv), "--out", str(d / "local")], cwd=REPO,
                           capture_output=True, text=True, env=env)
        check(r.returncode == 0 and "1 stored" in r.stdout, f"uploader sends to the key file's endpoint ({r.stdout.strip().splitlines()[-2:]})")
        r = subprocess.run([sys.executable, "tools/uploader.py", str(sv), "--out", str(d / "local")], cwd=REPO,
                           capture_output=True, text=True, env=env)
        check(r.returncode == 0 and "1 unchanged" in r.stdout, "uploader re-run: server says unchanged")
        r = subprocess.run([sys.executable, "tools/uploader.py", str(sv), "--out", str(d / "local"), "--endpoint", base + "/nope"],
                           cwd=REPO, capture_output=True, text=True, env=env)
        check(r.returncode == 1 and "failed" in r.stdout, "uploader reports a failed send and exits 1")

        r = subprocess.run([sys.executable, "tools/reconcile.py", str(store), "--keys", str(kdir), "--out", str(d / "world")],
                           cwd=REPO, capture_output=True, text=True, env={**os.environ, "HOME": str(d)})
        check(r.returncode == 0 and "2 batches" in r.stdout, "reconcile reads the nested bucket layout")
    finally:
        p.kill()
        p.wait()


def mcp_tests() -> None:
    print("mcp over http")
    d = OUT / "mcp"
    d.mkdir(parents=True)
    db = d / "release.sqlite"
    subprocess.run([sys.executable, "tools/build_sqlite.py", "--root", "examples", "--out", str(db), "--version", "rel.1"],
                   cwd=REPO, check=True, capture_output=True)
    p, base = start({"MODE": "mcp", "RELEASE_DB": f"file://{db}", "DB_CACHE": str(d / "cache.sqlite"), "RELOAD_S": "1"})
    url = base + "/mcp"
    accept = {"Accept": "application/json, text/event-stream"}
    try:
        s, h, r = http("POST", url, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}, accept)
        check(s == 200 and r["result"]["serverInfo"]["name"] == "opendata", "initialize over HTTP")
        check(h.get("Access-Control-Allow-Origin") == "*", "CORS header on responses")
        s, _, r = http("POST", url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, accept)
        check(s == 202 and r is None, "notification -> 202, no body")
        s, _, r = http("POST", url, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                     "params": {"name": "search", "arguments": {"query": "Example Staff"}}}, accept)
        check(s == 200 and r["result"]["structuredContent"]["results"][0]["id"] == 100001, "tool call over HTTP")
        s, _, r = http("POST", url, [{"jsonrpc": "2.0", "id": 3, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/x"}], accept)
        check(s == 200 and isinstance(r, list) and len(r) == 1, "JSON-RPC batch: replies only for requests")
        s, h, _ = http("GET", url)
        check(s == 405 and h.get("Allow") == "POST", "GET /mcp -> 405 (no SSE stream)")
        s, h, _ = http("OPTIONS", url)
        check(s == 204 and "POST" in h.get("Access-Control-Allow-Methods", ""), "CORS preflight")
        s, _, r = http("POST", url, b"{oops", accept)
        check(s == 400 and r["error"]["code"] == -32700, "malformed JSON -> 400 / -32700")

        time.sleep(1.1)
        subprocess.run([sys.executable, "tools/build_sqlite.py", "--root", "examples", "--out", str(db), "--version", "rel.2"],
                       cwd=REPO, check=True, capture_output=True)
        time.sleep(1.1)
        s, _, r = http("POST", url, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                     "params": {"name": "dataset_info", "arguments": {}}}, accept)
        check(r["result"]["structuredContent"]["dataset_version"] == "rel.2", "new release served without a restart")
    finally:
        p.kill()
        p.wait()

    p, base = start({"MODE": "mcp", "RELEASE_DB": f"file://{d / 'absent.sqlite'}", "DB_CACHE": str(d / 'none.sqlite')})
    try:
        s, _, r = http("POST", base + "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                               "params": {"name": "dataset_info", "arguments": {}}})
        check(s == 200 and r["result"].get("isError") is True, "no release yet: tool error, service stays up")
    finally:
        p.kill()
        p.wait()


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    ingest_tests()
    mcp_tests()
    print(f"\n{'all checks passed' if not failures else f'{len(failures)} checks failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
