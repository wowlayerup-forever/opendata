#!/usr/bin/env python3
"""Turn OpenDataCollector SavedVariables into signed JSON observation batches.

Batches are always written to a local directory (which tools/reconcile.py can read), and
sent to the project's ingest service when an endpoint is known: --endpoint, or the
"endpoint" field of the key file the maintainers give you. The service stores them for the
nightly reconcile; sending the same batch twice is harmless.

Usage:
  python tools/uploader.py --wow "/Applications/World of Warcraft/_classic_"
  python tools/uploader.py path/to/OpenDataCollector.lua [more.lua ...] [--out DIR]
  python tools/uploader.py --wow <dir> --contributor github:someone     # local-only key, no ingest
  python tools/uploader.py --wow <dir> --endpoint https://.../v1/batches

To contribute to the shared dataset, ask the maintainers for a key file and save it as
~/.config/opendata/contributor.json (keys made with --contributor are local only: the
service does not know them).

--wow finds WTF/Account/*/SavedVariables/OpenDataCollector.lua for every account, the
same way Stockkeeper's sync does. Output goes to .cache/batches/ by default (git-ignored:
raw observations never go in the repo, see CONVENTIONS section 8). A batch is one login
session and is named by the session id the addon gave it. Running this again mid-session
replaces the file with the longer batch; nothing is counted twice. After a run, type
/odc reset in game to clear the addon's buffer (it starts a new session id).

Signing: each batch carries an HMAC-SHA256 over its canonical JSON, keyed with the
per-contributor secret in ~/.config/opendata/contributor.json. This proves a batch is
unmodified and lets the ingest service, which holds the same key, attribute it. It is not
a public-key signature. Keep the file private.

Event grammar (one tab-separated string per event in the addon, see the .lua):
  L  t source map uiMap x y spell playerLevel entries     loot window, one per source
  V  t npc spawn map uiMap x y items                      merchant window
  QA t quest giver map uiMap x y playerLevel              quest accepted
  QT t quest ender map uiMap x y playerLevel xp money     quest turned in
  T  t npc spawn map uiMap x y services                   trainer window
  S  t npc spawn level                                    creature level sighting
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import re
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from savedvariables import load  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
KEY_FILE = Path(os.environ.get("OPENDATA_KEY_FILE", Path.home() / ".config" / "opendata" / "contributor.json"))
BATCH_FORMAT = 1


class BadEvent(ValueError):
    pass


# ───────────────────────────── event parsing ─────────────────────────────

def num(s: str, conv=int):
    if s == "":
        return None
    try:
        return conv(s)
    except ValueError:
        raise BadEvent(f"not a number: {s!r}")


def pos(f: list[str]) -> dict | None:
    """map, uiMap, x, y -> coords-like dict (x, y in percent of the UiMap)."""
    m, ui, x, y = f
    out = {}
    if m != "":
        out["map_id"] = num(m)
    if ui != "":
        out["ui_map_id"] = num(ui)
    if x != "" and y != "":
        out["x"], out["y"] = num(x, float), num(y, float)
    return out or None


def holder(s: str) -> dict | None:
    if s == "":
        return None
    parts = s.split(":")
    kind = {"C": "npc", "G": "object", "I": "item", "F": "fishing", "P": "player"}.get(parts[0])
    if kind is None:
        raise BadEvent(f"unknown holder {s!r}")
    out = {"kind": kind}
    if len(parts) > 1 and parts[1] != "":
        out["id"] = num(parts[1])
    if len(parts) > 2 and parts[2] != "":
        out["spawn"] = parts[2]
    return out


def split_list(s: str) -> list[str]:
    return [x for x in s.split(",") if x] if s else []


def parse_event(line: str) -> dict:
    f = line.split("\t")
    kind = f[0]
    want = {"L": 10, "V": 9, "QA": 9, "QT": 11, "T": 9, "S": 5}.get(kind)
    if want is None:
        raise BadEvent(f"unknown event type {kind!r}")
    if len(f) != want:
        raise BadEvent(f"{kind} has {len(f)} fields, expected {want}")
    ev: dict = {"type": {"L": "loot", "V": "vendor", "QA": "quest_accept", "QT": "quest_turnin",
                         "T": "trainer", "S": "sighting"}[kind], "t": num(f[1])}
    if kind == "L":
        ev["source"] = holder(f[2])
        if p := pos(f[3:7]):
            ev["pos"] = p
        if f[7]:
            ev["spell_id"] = num(f[7])
        if f[8]:
            ev["player_level"] = num(f[8])
        items, money = [], 0
        for e in split_list(f[9]):
            a, _, b = e.partition(":")
            if a == "$":
                money += num(b)
            else:
                items.append({"item_id": num(a), "count": num(b) or 1})
        ev["items"] = items
        if money:
            ev["money"] = money
    elif kind in ("V", "T"):
        ev["npc_id"] = num(f[2])
        if f[3]:
            ev["spawn"] = f[3]
        if p := pos(f[4:8]):
            ev["pos"] = p
        rows = []
        for e in split_list(f[8]):
            if kind == "V":
                item, price, stack, avail, costs = (e.split(":") + [""] * 5)[:5]
                r = {"item_id": num(item), "price": num(price), "stack": num(stack)}
                if avail not in ("", "-1"):
                    r["available"] = num(avail)
                if costs:
                    r["extended_cost"] = [{"item_id": num(c.split("*")[0]), "count": num(c.split("*")[1])} for c in costs.split("+")]
            else:
                created, cost, name = (e.split(":", 2) + ["", ""])[:3]
                r = {"cost": num(cost), "name": name}
                if created:
                    r["creates_item_id"] = num(created)
            rows.append(r)
        ev["items" if kind == "V" else "services"] = rows
    elif kind in ("QA", "QT"):
        ev["quest_id"] = num(f[2])
        if h := holder(f[3]):
            ev["holder"] = h
        if p := pos(f[4:8]):
            ev["pos"] = p
        if f[8]:
            ev["player_level"] = num(f[8])
        if kind == "QT":
            if f[9]:
                ev["xp"] = num(f[9])
            if f[10]:
                ev["money"] = num(f[10])
    elif kind == "S":
        ev["npc_id"] = num(f[2])
        if f[3]:
            ev["spawn"] = f[3]
        ev["level"] = num(f[4])
    return ev


# ───────────────────────────── batches ─────────────────────────────

def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_key(contributor: str | None) -> dict:
    if KEY_FILE.exists():
        key = json.loads(KEY_FILE.read_text(encoding="utf-8"))
        if contributor and contributor != key["contributor"]:
            raise SystemExit(f"{KEY_FILE} belongs to {key['contributor']}; remove it to start over as {contributor}")
        return key
    if not contributor:
        raise SystemExit(f"no contributor key yet; run once with --contributor github:<handle> (creates {KEY_FILE})")
    key = {"contributor": contributor, "key": secrets.token_hex(32)}
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(key, f, indent=2)
    print(f"created contributor key {KEY_FILE} for {contributor}")
    return key


def make_batch(raw: dict, key: dict) -> tuple[dict | None, int]:
    events, bad = [], 0
    for line in raw.get("events") or []:
        try:
            events.append(parse_event(line))
        except (BadEvent, IndexError) as e:
            bad += 1
            print(f"  skipped event ({e}): {line[:80]!r}", file=sys.stderr)
    if not events:
        return None, bad
    batch_id = str(raw.get("id") or "")
    if not re.fullmatch(r"[0-9a-f]{16,64}", batch_id):
        raise SystemExit(f"batch without a valid session id ({batch_id!r}); update the addon")
    body = {
        "batch_id": batch_id,
        "format": BATCH_FORMAT,
        "build": raw.get("build"),
        "locale": raw.get("locale"),
        "faction": raw.get("faction"),
        "started": raw.get("started"),
        "events": events,
    }
    signed = {"contributor": key["contributor"], **body}
    signed["signature"] = "hmac-sha256:" + hmac.new(bytes.fromhex(key["key"]), canonical(signed), hashlib.sha256).hexdigest()
    return signed, bad


def verify(batch: dict, key_hex: str) -> bool:
    """For the reconciler or an ingest server that holds the contributor's key."""
    sig = batch.get("signature", "")
    body = {k: v for k, v in batch.items() if k != "signature"}
    want = "hmac-sha256:" + hmac.new(bytes.fromhex(key_hex), canonical(body), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, want)


def find_savedvariables(wow: Path) -> list[Path]:
    return sorted(wow.glob("WTF/Account/*/SavedVariables/OpenDataCollector.lua"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="OpenDataCollector.lua SavedVariables files")
    ap.add_argument("--wow", help="WoW product directory (e.g. .../_classic_beta_); scans every account")
    ap.add_argument("--out", default=str(REPO / ".cache" / "batches"))
    ap.add_argument("--contributor", help="handle for a new local-only key, e.g. github:someone")
    ap.add_argument("--endpoint", help="ingest URL (default: the key file's endpoint, if any)")
    ap.add_argument("--no-send", action="store_true", help="only write local files, even if an endpoint is known")
    args = ap.parse_args()

    files = [Path(f) for f in args.files]
    if args.wow:
        files += find_savedvariables(Path(args.wow).expanduser())
    if not files:
        ap.error("no SavedVariables files (pass paths or --wow)")
    key = load_key(args.contributor)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    known_builds = {b["build"] for b in json.loads((REPO / "builds.json").read_text(encoding="utf-8"))["builds"]}

    written = replaced = existing = empty = bad_total = conflicts = 0
    outgoing: list[dict] = []
    for path in files:
        db = load(path).get("OpenDataCollectorDB")
        if not isinstance(db, dict):
            print(f"{path}: no OpenDataCollectorDB, skipped")
            continue
        if db.get("format") != 1:
            raise SystemExit(f"{path}: unknown OpenDataCollectorDB format {db.get('format')!r}")
        for raw in db.get("batches") or []:
            batch, bad = make_batch(raw, key)
            bad_total += bad
            if batch is None:
                empty += 1
                continue
            if batch["build"] not in known_builds:
                print(f"  note: build {batch['build']} is not in builds.json yet; the reconciler will need it registered")
            dest = out / f"{batch['batch_id']}.json"
            if dest.exists():
                old = json.loads(dest.read_text(encoding="utf-8"))
                n = len(old["events"])
                if old["events"] == batch["events"]:
                    existing += 1
                    outgoing.append(batch)
                    continue
                if old["events"] != batch["events"][:n]:
                    conflicts += 1
                    print(f"  conflict: {dest.name} on disk is not a prefix of the batch in {path}; left as is", file=sys.stderr)
                    continue
                replaced += 1
            else:
                written += 1
            tmp = dest.with_suffix(".part")
            tmp.write_text(json.dumps(batch, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            tmp.replace(dest)
            outgoing.append(batch)
    print(f"{written} new and {replaced} grown batches written to {out}, {existing} unchanged, {empty} empty, "
          f"{bad_total} bad events skipped, {conflicts} conflicts")
    endpoint = None if args.no_send else (args.endpoint or key.get("endpoint"))
    failed = 0
    if endpoint:
        statuses: dict[str, int] = {}
        for batch in outgoing:
            status, reply = send(endpoint, batch)
            if status >= 400:
                failed += 1
                print(f"  {batch['batch_id']}: HTTP {status} {reply.get('error', '')}", file=sys.stderr)
            else:
                statuses[reply.get("status", "?")] = statuses.get(reply.get("status", "?"), 0) + 1
        sent = ", ".join(f"{n} {s}" for s, n in sorted(statuses.items())) or "nothing"
        print(f"sent to {endpoint}: {sent}{f', {failed} failed' if failed else ''}")
    if conflicts or failed:
        return 1
    if written or replaced or existing:
        print("you can now type /odc reset in game")
    return 0


def send(endpoint: str, batch: dict) -> tuple[int, dict]:
    req = urllib.request.Request(endpoint, data=json.dumps(batch, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "opendata-uploader/1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError) as e:
        return 599, {"error": f"unreachable: {e}"}


if __name__ == "__main__":
    sys.exit(main())
