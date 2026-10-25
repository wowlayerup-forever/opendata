#!/usr/bin/env python3
"""Aggregate observation batches into data/world/.

Reads the JSON batches tools/uploader.py writes (one per play session) and produces
  world/loot.jsonl            drops per loot window, by npc, object, item, fishing zone, skinning, pickpocket
  world/spawns.jsonl          where creatures and objects were looted or interacted with
  world/vendors.jsonl         what merchants sell, price, limited stock
  world/quest_givers.jsonl    who starts and ends each quest
  world/recipe_sources.jsonl  which trainer teaches which recipe, and for how much
  world/npc_levels.jsonl      creature levels seen

Usage:
  python tools/reconcile.py .cache/batches
  python tools/reconcile.py <dir> --keys <dir of contributor key files> [--trust-unknown]

The batch directory is the source of truth for observations: every run regenerates all
rows with source.kind observation from all batches, so keep the batches (CONVENTIONS
section 8: they live in object storage, not here). Rows of other kinds (emu_seed, manual)
are kept; an emu_seed loot or vendor row is dropped once an observation of the same holder
and item exists, and marked disputed when the holder has been observed often enough without
the item (--dispute-loot windows, --dispute-vendor merchant windows).

Deterministic: same batches in, byte-identical files out. Batches are read in batch_id
order, rows are sorted by primary key, and source.ref is the reconciler version, not a
timestamp, so a re-run only changes lines whose counts changed.

Signatures: a batch is accepted if its HMAC verifies with the contributor's key (from
--keys, plus your own ~/.config/opendata/contributor.json). Batches from contributors with
no key are skipped unless --trust-unknown.

Exclusions: --exclude FILE ({"contributors": {handle: reason}, "batches": {batch_id: reason}})
drops those batches before anything is counted. Because every run regenerates all
observation rows, excluding a contributor also removes everything they sent before. The
nightly workflow keeps this file in the private batch bucket, not in this public repo.

Quality report: --report FILE writes a markdown summary for the reconcile PR: per
contributor volume, rows backed by a single contributor, drop counts that disagree sharply
with everyone else's (two-proportion z-test), and implausible timestamps. Contributors
appear only as pseudonyms, because the PR is public: c-<6 hex of HMAC-SHA256(key, handle)>
with the key from --pseudonym-key (kept in the private bucket, so a guessed handle cannot be
checked against the report). Without a key, local runs fall back to a plain sha256.

Interpretation rules:
  - A loot window is one source (corpse, node, chest, item) by its spawn GUID. A corpse
    opened twice is one window with the union of what was seen. kills = windows.
  - A loot event right after a spell named "Skinning" counts as skinning loot of that
    creature, after "Pick Pocket" as pickpocket loot. Herbalism and mining loot comes from
    an object and stays object loot. Spell names come from data/client/spells.
  - Fishing loot is keyed by zone (AreaTable id via UiMapAssignment). Without the mapping
    for the build it is skipped.
  - Vendor price is the highest seen: reputation discounts only ever lower it.
  - Trainer services map to recipes by the item they create, or by exact name when the
    recipe creates nothing or the item is ambiguous. enUS batches only for name matching.
  - A level sighting counts once per spawn GUID.
"""
from __future__ import annotations

import argparse
import csv
import math
import time
import hashlib
import hmac
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REF = "reconciler:v1"
TABLE_PK = {
    "loot": ("holder_kind", "holder_id", "item_id"),
    "spawns": ("holder_kind", "holder_id", "coords.map_id", "coords.x", "coords.y"),
    "vendors": ("npc_id", "item_id"),
    "quest_givers": ("quest_id", "role", "holder_kind", "holder_id"),
    "recipe_sources": ("recipe_spell_id", "source_kind", "source_id"),
    "npc_levels": ("npc_id", "level"),
}
SPAWN_GRID = 0.5  # percent of map; sightings in the same cell are one spawn point


def build_key(b: str) -> tuple[int, ...]:
    return tuple(int(x) for x in b.split("."))


def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def get(row: dict, dotted: str):
    for part in dotted.split("."):
        row = row.get(part) if isinstance(row, dict) else None
    return row


# ───────────────────────────── inputs ─────────────────────────────

def load_keys(keys_dir: str | None) -> dict[str, str]:
    paths = [Path.home() / ".config" / "opendata" / "contributor.json"]
    if keys_dir:
        paths += sorted(Path(keys_dir).glob("*.json"))
    keys = {}
    for p in paths:
        if p.exists():
            k = json.loads(p.read_text(encoding="utf-8"))
            keys[k["contributor"]] = k["key"]
    return keys


def signature_ok(batch: dict, key_hex: str) -> bool:
    body = {k: v for k, v in batch.items() if k != "signature"}
    want = "hmac-sha256:" + hmac.new(bytes.fromhex(key_hex), canonical(body), hashlib.sha256).hexdigest()
    return hmac.compare_digest(batch.get("signature", ""), want)


def load_exclusions(path: str | None) -> dict[str, dict[str, str]]:
    if not path or not Path(path).exists():
        return {"contributors": {}, "batches": {}}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"contributors": dict(raw.get("contributors") or {}), "batches": dict(raw.get("batches") or {})}


def load_batches(dir_: Path, keys: dict[str, str], trust_unknown: bool, builds: set[str],
                 exclusions: dict | None = None) -> list[dict]:
    out, skipped = [], Counter()
    exclusions = exclusions or {"contributors": {}, "batches": {}}
    for path in sorted(dir_.rglob("*.json")):  # flat locally; batches/<kind>/<handle>/ when synced from the bucket
        b = json.loads(path.read_text(encoding="utf-8"))
        if b.get("format") != 1:
            skipped["unknown format"] += 1
            continue
        if b.get("contributor") in exclusions["contributors"]:
            skipped["excluded contributor"] += 1
            continue
        if b.get("batch_id") in exclusions["batches"]:
            skipped["excluded batch"] += 1
            continue
        key = keys.get(b.get("contributor", ""))
        if key is None and not trust_unknown:
            skipped["no key for contributor"] += 1
            continue
        if key is not None and not signature_ok(b, key):
            skipped["bad signature"] += 1
            continue
        if b.get("build") not in builds:
            skipped[f"build {b.get('build')} not in builds.json"] += 1
            continue
        out.append(b)
    out.sort(key=lambda b: b["batch_id"])
    for why, n in sorted(skipped.items()):
        print(f"  skipped {n} batches: {why}", file=sys.stderr)
    return out


def load_uimap_zones(builds: set[str], wago_cache: Path) -> dict[str, dict[int, tuple[int, int]]]:
    """build -> {ui_map_id: (map_id, area_id)} from cached UiMapAssignment, full-map rows only."""
    out: dict[str, dict[int, tuple[int, int]]] = {}
    for b in builds:
        path = wago_cache / b / "UiMapAssignment.csv"
        if not path.exists():
            continue
        best: dict[int, tuple[int, int, int]] = {}
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                full = (float(r["UiMin_0"]), float(r["UiMin_1"]), float(r["UiMax_0"]), float(r["UiMax_1"])) == (0, 0, 1, 1)
                if not full or int(r["AreaID"]) <= 0:
                    continue
                ui, order = int(r["UiMapID"]), int(r["OrderIndex"])
                if ui not in best or order < best[ui][0]:
                    best[ui] = (order, int(r["MapID"]), int(r["AreaID"]))
        out[b] = {ui: (m, a) for ui, (_, m, a) in best.items()}
    return out


def zone_for(zones: dict, build: str, pos: dict | None) -> int | None:
    if not pos or "ui_map_id" not in pos:
        return None
    hit = zones.get(build, {}).get(pos["ui_map_id"])
    if hit is None:
        # same UiMap ids across builds of one version are the norm; use any build that knows it
        for b in sorted(zones, key=build_key, reverse=True):
            if pos["ui_map_id"] in zones[b]:
                hit = zones[b][pos["ui_map_id"]]
                break
    return hit[1] if hit else None


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ───────────────────────────── aggregation ─────────────────────────────

class Agg:
    """Counts plus the build range they were seen in."""

    def __init__(self):
        self.builds: dict[tuple, list[str]] = {}

    def seen(self, key: tuple, build: str) -> None:
        r = self.builds.get(key)
        if r is None:
            self.builds[key] = [build, build]
        else:
            if build_key(build) < build_key(r[0]):
                r[0] = build
            if build_key(build) > build_key(r[1]):
                r[1] = build

    def prov(self, key: tuple) -> dict:
        first, last = self.builds[key]
        return {
            "layer": "world",
            "source": {"kind": "observation", "ref": REF},
            "build_first_seen": first,
            "build_last_seen": last,
            "confidence": "verified",
        }


def reconcile(batches: list[dict], spells: dict[int, str], recipes: list[dict], zones: dict) -> dict[str, list[dict]]:
    skin_spells = {sid for sid, n in spells.items() if n == "Skinning"}
    pick_spells = {sid for sid, n in spells.items() if n == "Pick Pocket"}
    recipes_by_item: dict[int, list[dict]] = defaultdict(list)
    recipes_by_name: dict[str, list[dict]] = defaultdict(list)
    for r in recipes:
        recipes_by_item[r["creates"]["item_id"]].append(r)
        recipes_by_name[r["name"]].append(r)

    agg = Agg()
    # loot: window key -> {"holder": (kind, id), "items": {item: max count}, "build"}
    windows: dict[tuple, dict] = {}
    spawn_points: dict[tuple, tuple] = {}     # (kind, id, spawn) -> (map, zone, x, y, build), first seen
    vendor_windows: Counter = Counter()
    vendor_items: dict[tuple, dict] = {}
    givers: Counter = Counter()
    giver_pos: dict[tuple, dict] = {}
    trainer_windows: dict[tuple, dict] = {}
    levels: dict[tuple, tuple[int, str]] = {}  # (npc, spawn) -> (level, build)
    unmatched_services = 0

    def note_spawn(kind: str, hid: int | None, spawn: str | None, pos: dict | None, build: str) -> None:
        if hid is None or not spawn or not pos or "map_id" not in pos or "x" not in pos:
            return
        spawn_points.setdefault((kind, hid, spawn), (pos["map_id"], zone_for(zones, build, pos), pos["x"], pos["y"], build))

    for b in batches:
        build, bid = b["build"], b["batch_id"]
        enus = b.get("locale") == "enUS"
        for n, ev in enumerate(b["events"]):
            t = ev["type"]
            if t == "loot":
                src = ev.get("source") or {}
                kind, hid, spawn = src.get("kind"), src.get("id"), src.get("spawn")
                if kind == "npc":
                    sp = ev.get("spell_id")
                    hk = "skinning" if sp in skin_spells else "pickpocket" if sp in pick_spells else "npc"
                elif kind == "object":
                    hk = "object"
                elif kind == "item":
                    hk = "item"
                elif kind == "fishing":
                    hk, hid, spawn = "fishing", zone_for(zones, build, ev.get("pos")), None
                else:
                    continue
                if hid is None:
                    continue
                wkey = (hk, hid, spawn) if spawn else (hk, hid, f"{bid}:{n}")
                w = windows.setdefault(wkey, {"items": {}, "builds": set()})
                w["builds"].add(build)
                for it in ev.get("items", []):
                    w["items"][it["item_id"]] = max(w["items"].get(it["item_id"], 0), it["count"])
                if hk in ("npc", "object"):
                    note_spawn(hk, hid, spawn, ev.get("pos"), build)
            elif t == "vendor":
                npc = ev["npc_id"]
                vendor_windows[npc] += 1
                note_spawn("npc", npc, ev.get("spawn"), ev.get("pos"), build)
                for it in ev.get("items", []):
                    k = (npc, it["item_id"])
                    v = vendor_items.setdefault(k, {"n": 0, "price": 0, "stock": 0, "costs": Counter()})
                    v["n"] += 1
                    v["price"] = max(v["price"], it.get("price") or 0)
                    v["stock"] = max(v["stock"], it.get("available") or 0)
                    if it.get("extended_cost"):
                        v["costs"][json.dumps(it["extended_cost"], sort_keys=True)] += 1
                    agg.seen(("vendors",) + k, build)
            elif t in ("quest_accept", "quest_turnin"):
                h = ev.get("holder") or {}
                if h.get("kind") not in ("npc", "object", "item") or h.get("id") is None:
                    continue
                k = (ev["quest_id"], "start" if t == "quest_accept" else "end", h["kind"], h["id"])
                givers[k] += 1
                agg.seen(("quest_givers",) + k, build)
                pos = ev.get("pos")
                if pos and "map_id" in pos and "x" in pos and k not in giver_pos:
                    giver_pos[k] = {"map_id": pos["map_id"], "x": round(pos["x"], 1), "y": round(pos["y"], 1)}
                    z = zone_for(zones, build, pos)
                    if z:
                        giver_pos[k]["zone_id"] = z
                if h["kind"] in ("npc", "object"):
                    note_spawn(h["kind"], h["id"], h.get("spawn"), pos, build)
            elif t == "trainer":
                npc = ev["npc_id"]
                note_spawn("npc", npc, ev.get("spawn"), ev.get("pos"), build)
                for s in ev.get("services", []):
                    item, name = s.get("creates_item_id"), s.get("name", "")
                    cands = recipes_by_item.get(item, []) if item else []
                    if len(cands) != 1 and enus:
                        # no item, or several recipes make it: fall back to the exact recipe name
                        named = [r for r in (cands or recipes_by_name.get(name, [])) if r["name"] == name]
                        if len(named) == 1:
                            cands = named
                    if len(cands) != 1:
                        unmatched_services += 1
                        continue
                    k = (cands[0]["recipe_spell_id"], "trainer", npc)
                    tr = trainer_windows.setdefault(k, {"n": 0, "cost": 0})
                    tr["n"] += 1
                    tr["cost"] = max(tr["cost"], s.get("cost") or 0)
                    agg.seen(("recipe_sources",) + k, build)
            elif t == "sighting":
                k = (ev["npc_id"], ev.get("spawn") or f"{bid}:{n}")
                levels.setdefault(k, (ev["level"], build))

    out: dict[str, list[dict]] = {t: [] for t in TABLE_PK}

    # loot
    kills: Counter = Counter()
    drops: dict[tuple, dict] = {}
    for (hk, hid, _), w in windows.items():
        kills[(hk, hid)] += 1
        for b in w["builds"]:
            agg.seen(("loot_holder", hk, hid), b)
        for item, count in w["items"].items():
            d = drops.setdefault((hk, hid, item), {"drops": 0, "min": count, "max": count})
            d["drops"] += 1
            d["min"], d["max"] = min(d["min"], count), max(d["max"], count)
            for b in w["builds"]:
                agg.seen(("loot", hk, hid, item), b)
    for (hk, hid, item), d in drops.items():
        out["loot"].append({
            "holder_kind": hk, "holder_id": hid, "item_id": item,
            "drops": d["drops"], "kills": kills[(hk, hid)], "count_min": d["min"], "count_max": d["max"],
            **agg.prov(("loot", hk, hid, item)),
        })

    # spawns: cluster spawn GUIDs into grid cells
    cells: Counter = Counter()
    for (kind, hid, _), (m, zone, x, y, build) in spawn_points.items():
        cx, cy = round(round(x / SPAWN_GRID) * SPAWN_GRID, 1), round(round(y / SPAWN_GRID) * SPAWN_GRID, 1)
        k = (kind, hid, m, zone, cx, cy)
        cells[k] += 1
        agg.seen(("spawns",) + k, build)
    by_pk: dict[tuple, dict] = {}
    for (kind, hid, m, zone, cx, cy), n in sorted(cells.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        pk = (kind, hid, m, cx, cy)
        if pk in by_pk:  # same rounded x/y on two zones of one map: the primary key cannot tell them apart
            print(f"  warning: spawn cell {pk} seen under two zones; counts merged", file=sys.stderr)
            by_pk[pk]["n"] += n
            continue
        coords = {"map_id": m, "x": cx, "y": cy}
        if zone:
            coords["zone_id"] = zone
        by_pk[pk] = {"holder_kind": kind, "holder_id": hid, "coords": coords, "n": n,
                     **agg.prov(("spawns", kind, hid, m, zone, cx, cy))}
    out["spawns"] = list(by_pk.values())

    # vendors
    for (npc, item), v in vendor_items.items():
        row = {"npc_id": npc, "item_id": item, "n": v["n"], "windows": vendor_windows[npc]}
        if v["price"]:
            row["price"] = v["price"]
        if v["stock"]:
            row["limited_stock"] = v["stock"]
        if v["costs"]:
            row["extended_cost"] = json.loads(sorted(v["costs"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0])
        row.update(agg.prov(("vendors", npc, item)))
        out["vendors"].append(row)

    # quest givers
    for k, n in givers.items():
        q, role, kind, hid = k
        row = {"quest_id": q, "role": role, "holder_kind": kind, "holder_id": hid}
        if k in giver_pos:
            row["coords"] = giver_pos[k]
        row["n"] = n
        row.update(agg.prov(("quest_givers",) + k))
        out["quest_givers"].append(row)

    # recipe sources
    for k, tr in trainer_windows.items():
        sid, kind, npc = k
        row = {"recipe_spell_id": sid, "source_kind": kind, "source_id": npc}
        if tr["cost"]:
            row["cost"] = tr["cost"]
        row["n"] = tr["n"]
        row.update(agg.prov(("recipe_sources",) + k))
        out["recipe_sources"].append(row)

    # levels
    lv: Counter = Counter()
    for (npc, _), (level, build) in levels.items():
        lv[(npc, level)] += 1
        agg.seen(("npc_levels", npc, level), build)
    for (npc, level), n in lv.items():
        out["npc_levels"].append({"npc_id": npc, "level": level, "n": n, **agg.prov(("npc_levels", npc, level))})

    out["_stats"] = [{"loot_windows": len(windows), "unmatched_trainer_services": unmatched_services}]
    out["_observed_holders"] = [{"loot_kills": dict((f"{k[0]}:{k[1]}", v) for k, v in kills.items()),
                                 "vendor_windows": dict(vendor_windows)}]
    return out


# ───────────────────────────── quality report ─────────────────────────────

PSEUDONYM_KEY: bytes | None = None


def pseudonym(handle: str) -> str:
    data = handle.encode("utf-8")
    digest = hmac.new(PSEUDONYM_KEY, data, hashlib.sha256) if PSEUDONYM_KEY else hashlib.sha256(data)
    return "c-" + digest.hexdigest()[:6]


def quality_report(batches: list[dict], spells: dict[int, str], z_flag: float = 4.0, min_kills: int = 20) -> str:
    """Markdown for the reconcile PR. Per contributor, loot is counted on that contributor's
    own windows (one per spawn), which is what the comparison needs."""
    skin = {sid for sid, n in spells.items() if n == "Skinning"}
    pick = {sid for sid, n in spells.items() if n == "Pick Pocket"}
    per_c: dict[str, dict] = defaultdict(lambda: {"batches": 0, "events": 0, "windows": {}, "bad_t": 0})
    now = time.time()
    for b in batches:
        c = per_c[b["contributor"]]
        c["batches"] += 1
        c["events"] += len(b["events"])
        for n, ev in enumerate(b["events"]):
            t = ev.get("t") or 0
            if t < 1735689600 or t > now + 86400:  # before 2025-01-01, or in the future
                c["bad_t"] += 1
            if ev["type"] != "loot":
                continue
            src = ev.get("source") or {}
            kind = src.get("kind")
            if kind not in ("npc", "object", "item") or src.get("id") is None:
                continue
            hk = ("skinning" if ev.get("spell_id") in skin else "pickpocket" if ev.get("spell_id") in pick else "npc") if kind == "npc" else kind
            key = (hk, src["id"], src.get("spawn") or f"{b['batch_id']}:{n}")
            items = c["windows"].setdefault(key, set())
            items.update(it["item_id"] for it in ev.get("items", []))

    # (holder) -> contributor -> kills ; (holder, item) -> contributor -> drops
    kills: dict[tuple, Counter] = defaultdict(Counter)
    drops: dict[tuple, Counter] = defaultdict(Counter)
    for h, c in per_c.items():
        for (hk, hid, _), items in c["windows"].items():
            kills[(hk, hid)][h] += 1
            for item in items:
                drops[(hk, hid, item)][h] += 1

    total_windows = sum(len(c["windows"]) for c in per_c.values())
    lines = ["## Data quality", ""]
    lines.append(f"{len(per_c)} contributors, {len(batches)} batches, {total_windows} loot windows. "
                 "Contributors are shown as pseudonyms; maintainers can map them with the infra repo.")
    lines += ["", "| contributor | batches | events | loot windows | share | bad timestamps |", "|---|---|---|---|---|---|"]
    for h, c in sorted(per_c.items(), key=lambda kv: -len(kv[1]["windows"])):
        share = len(c["windows"]) / total_windows if total_windows else 0
        lines.append(f"| {pseudonym(h)} | {c['batches']} | {c['events']} | {len(c['windows'])} | {share:.0%} | {c['bad_t']} |")

    single = sum(1 for cs in drops.values() if len(cs) == 1)
    lines += ["", f"Loot rows backed by a single contributor: {single} of {len(drops)}."]
    if len(per_c) >= 3:
        top_h, top = max(per_c.items(), key=lambda kv: len(kv[1]["windows"]))
        if total_windows and len(top["windows"]) / total_windows > 0.6:
            lines.append(f"**{pseudonym(top_h)} supplies {len(top['windows']) / total_windows:.0%} of all loot windows.**")

    # Each contributor's rate is compared with the median rate across contributors, so one
    # inflated contributor cannot make the honest ones look deviant. With only two
    # contributors the data cannot say which one is off; that is reported as a disagreement.
    flags, pairs = [], []
    for (hk, hid, item), cs in drops.items():
        enough = {h: k for h, k in kills[(hk, hid)].items() if k >= min_kills}
        rates = {h: cs.get(h, 0) / k for h, k in enough.items()}
        if len(rates) >= 3:
            ordered = sorted(rates.values())
            mid = len(ordered) // 2
            ref = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
            k_max = max(enough.values())
            ref = min(max(ref, 1 / (2 * k_max)), 1 - 1 / (2 * k_max))  # keep the variance away from zero
            for h, k_c in enough.items():
                z = (rates[h] - ref) / math.sqrt(ref * (1 - ref) / k_c)
                if abs(z) >= z_flag:
                    flags.append((abs(z), h, hk, hid, item, cs.get(h, 0), k_c, ref, z))
        elif len(rates) == 2:
            (h1, k1), (h2, k2) = enough.items()
            d1, d2 = cs.get(h1, 0), cs.get(h2, 0)
            p = (d1 + d2) / (k1 + k2)
            if 0 < p < 1:
                z = (d1 / k1 - d2 / k2) / math.sqrt(p * (1 - p) * (1 / k1 + 1 / k2))
                if abs(z) >= z_flag:
                    pairs.append((abs(z), h1, d1, k1, h2, d2, k2, hk, hid, item))
    lines += ["", f"Drop counts far from the median contributor's (|z| >= {z_flag:g}, at least {min_kills} windows each, "
                  f"three or more contributors): {len(flags)}"]
    if flags:
        lines += ["", "| contributor | holder | item | theirs | median rate | z |", "|---|---|---|---|---|---|"]
        for _, h, hk, hid, item, d_c, k_c, ref, z in sorted(flags, reverse=True)[:20]:
            lines.append(f"| {pseudonym(h)} | {hk} {hid} | {item} | {d_c}/{k_c} | {ref:.1%} | {z:+.1f} |")
    if pairs:
        lines += ["", f"Two contributors disagree and there is no third to tell which is off: {len(pairs)}", "",
                  "| holder | item | one | other | z |", "|---|---|---|---|---|"]
        for _, h1, d1, k1, h2, d2, k2, hk, hid, item in sorted(pairs, reverse=True)[:20]:
            lines.append(f"| {hk} {hid} | {item} | {pseudonym(h1)} {d1}/{k1} | {pseudonym(h2)} {d2}/{k2} | {_:.1f} |")
    if any(c["bad_t"] for c in per_c.values()):
        lines += ["", "Some events have timestamps before 2025 or in the future (see the table); check those contributors' clocks or batches."]
    lines += ["", "To exclude a contributor or batch: `contributors.py exclude` in the infra repo; the next run drops all of their batches."]
    return "\n".join(lines) + "\n"


# ───────────────────────────── merge with seeded rows ─────────────────────────────

def merge_seeded(table: str, observed: list[dict], existing: list[dict], holders: dict, dispute_loot: int, dispute_vendor: int) -> tuple[list[dict], Counter]:
    stats: Counter = Counter()
    keep = []
    observed_keys = {tuple(get(r, p) for p in TABLE_PK[table]) for r in observed}
    for r in existing:
        if r.get("source", {}).get("kind") == "observation":
            continue  # regenerated from batches
        key = tuple(get(r, p) for p in TABLE_PK[table])
        if r.get("source", {}).get("kind") == "emu_seed" and table in ("loot", "vendors"):
            if key in observed_keys:
                stats["superseded"] += 1
                continue
            if table == "loot":
                seen_windows = holders["loot_kills"].get(f"{r['holder_kind']}:{r['holder_id']}", 0)
                limit = dispute_loot
            else:
                seen_windows = holders["vendor_windows"].get(r["npc_id"], 0)
                limit = dispute_vendor
            if seen_windows >= limit and r.get("confidence") != "disputed":
                r = {**r, "confidence": "disputed"}
                stats["disputed"] += 1
        keep.append(r)
    return keep + observed, stats


def sort_key(table: str):
    def k(r: dict):
        return tuple("" if get(r, p) is None else get(r, p) for p in TABLE_PK[table]) + (build_key(r["build_last_seen"]),)
    return k


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batches", help="directory of batch JSON files from tools/uploader.py")
    ap.add_argument("--keys", help="directory of contributor key files ({contributor, key})")
    ap.add_argument("--trust-unknown", action="store_true", help="accept batches from contributors without a key")
    ap.add_argument("--out", default="data/world")
    ap.add_argument("--client", default="data/client", help="client tables used for spell and recipe lookups")
    ap.add_argument("--wago-cache", default=".cache/wago", help="where tools/extract_client.py caches DB2 CSVs (for UiMapAssignment)")
    ap.add_argument("--dispute-loot", type=int, default=200, help="loot windows without the item before an emu_seed row is disputed")
    ap.add_argument("--dispute-vendor", type=int, default=5, help="merchant windows without the item before an emu_seed row is disputed")
    ap.add_argument("--exclude", help="JSON file of excluded contributors and batches")
    ap.add_argument("--report", help="write a markdown data-quality report here")
    ap.add_argument("--pseudonym-key", help="file with the secret for report pseudonyms (from the private bucket)")
    args = ap.parse_args()

    builds = {b["build"] for b in json.loads((REPO / "builds.json").read_text(encoding="utf-8"))["builds"]}
    global PSEUDONYM_KEY
    if args.pseudonym_key and Path(args.pseudonym_key).exists():
        PSEUDONYM_KEY = Path(args.pseudonym_key).read_bytes().strip()
    exclusions = load_exclusions(args.exclude)
    batches = load_batches(Path(args.batches), load_keys(args.keys), args.trust_unknown, builds, exclusions)
    client = Path(args.client) if Path(args.client).is_absolute() else REPO / args.client
    spells = {r["spell_id"]: r["name"] for r in read_jsonl(client / "spells.jsonl")}
    recipes = read_jsonl(client / "recipes.jsonl")
    wago = Path(args.wago_cache) if Path(args.wago_cache).is_absolute() else REPO / args.wago_cache
    zones = load_uimap_zones({b["build"] for b in batches}, wago)
    missing = sorted({b["build"] for b in batches} - set(zones))
    if missing:
        print(f"  note: no UiMapAssignment cached for {', '.join(missing)}; fishing loot skipped and spawns lack zone_id there "
              f"(fetch with tools/extract_client.py --build <build>)", file=sys.stderr)

    result = reconcile(batches, spells, recipes, zones)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        excluded = sum(1 for _ in exclusions["contributors"]) + sum(1 for _ in exclusions["batches"])
        Path(args.report).write_text(quality_report(batches, spells)
                                     + (f"\n{excluded} exclusion entries applied.\n" if excluded else ""), encoding="utf-8")
    stats, holders = result.pop("_stats")[0], result.pop("_observed_holders")[0]
    out = Path(args.out) if Path(args.out).is_absolute() else REPO / args.out
    print(f"{len(batches)} batches, {stats['loot_windows']} loot windows, "
          f"{stats['unmatched_trainer_services']} trainer services not matched to a recipe")
    for table in TABLE_PK:
        path = out / f"{table}.jsonl"
        existing = read_jsonl(path)
        rows, mstats = merge_seeded(table, result[table], existing, holders, args.dispute_loot, args.dispute_vendor)
        if not rows and not path.exists():
            continue
        rows.sort(key=sort_key(table))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        extra = "".join(f", {v} emu_seed rows {k}" for k, v in sorted(mstats.items()))
        rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
        print(f"  {rel}: {len(result[table])} observed rows, {len(rows)} total{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
