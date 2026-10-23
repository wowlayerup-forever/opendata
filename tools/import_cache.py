#!/usr/bin/env python3
"""Import the client's WDB cache into data/client/{quests,npcs,objects}.jsonl.

On the modern engine quest templates and most creature templates are not in DB2. The server
sends them when a player meets them and the client caches them verbatim in
<WoW>/<product dir>/Cache/WDB/<locale>/{questcache,creaturecache,gameobjectcache}.wdb. This
reads those files. The GameObjects DB2 only ships decorative objects; every chest, herb and
ore node in the dataset comes from gameobjectcache.wdb.
Coverage is only what the owner's characters have seen; that is expected.

Usage:
  python tools/import_cache.py --wdb "/Applications/World of Warcraft/_classic_beta_/Cache/WDB/enUS"
  python tools/import_cache.py --wdb <dir> --build 1.60.1.70205    # must match the file headers

The build is the one in the cache file header (the client that wrote the cache), which can
be older than the installed client if the cache was not refreshed. It must be in builds.json.

Imports accumulate: rows already in the output from earlier imports (other characters,
other accounts) are kept, a row seen again unchanged in a newer build gets its
build_last_seen extended, and a changed row is added alongside the old one. Rows the
extractor wrote from DB2 (source.kind datamine) are never touched; a creature that has a
DB2 row is not imported from the cache.

Record layouts, worked out on 1.60.1.70205 and checked on every record (a record that does
not decode to exactly its stored length is skipped and reported, never guessed at):

  questcache.wdb, per record (little endian):
    @0 quest id   @8 quest level   @16 min level   @24 QuestSortID   @28 QuestInfoID
    @36 next quest   @40 XP difficulty   @44 XP multiplier (float)   @48 reward money   @100 flags
    @116 + 16k (k<4)  reward item, amount     @180 + 12k (k<6)  choice item, amount, display
    @300 + 16k (k<5)  reward faction id, value index, override, cap
    @436 objective count   @440 allowable races (u64, all bits = all races)
    @488 objectives, each: id u32, type u32, storage index u8, object id i32, amount i32,
         24 more bytes, description length u8, one flag byte, description
    then string lengths, MSB-first bits, flushed: title 9, objectives 12, description 12,
         area description 9, portrait giver text 10, name 8, turn-in text 10, name 8,
         completion log 11; then the strings in that order.

  creaturecache.wdb, per record:
    MSB-first bits: title length 11, title-alt 11, cursor name 6, one flag, then 4 x (name,
    name-alt) lengths of 11 bits each; flushed (15 bytes). Lengths include the NUL.
    names, then: @+12 creature type u8, @+17 classification u8, @+26 display count,
    @+34 + 12k display id, then 48 fixed bytes (quest item count at +8 of those),
    the title, title-alt and cursor-name strings, then 4 bytes per quest item.

  gameobjectcache.wdb, per record:
    @0 type u32, @4 display id u32, then 7 NUL-terminated strings (name, 3 alternate names,
    icon, cast bar caption, one unknown), 35 x i32 data, size float, quest item count u8,
    4 bytes per quest item, 8 more bytes. For chests and nodes (type 3) and goobers (10)
    data[0] is the Lock id, resolved through Lock, LockType and SkillLine from the wago
    cache (the cache build's tables if extracted, otherwise the newest extracted build).

Quest XP and reputation are difficulty indexes in the cache. They are resolved through the
QuestXP and QuestFactionReward DB2 tables from the wago cache (the cache build's if
extracted, otherwise the newest extracted build): rewards.xp = QuestXP[quest level]
.Difficulty_<index>, as shipped, with the cache's XP multiplier kept beside it rather than
applied; a reputation value index n > 0 reads QuestFactionReward row 1, n < 0 row 2 at |n|.
A faction with a non-zero override is left out (what the override means is not in the
client data) and counted.

Not carried by the cache and left absent: NPC levels, faction and npc flags (unit fields the
client only sees on a spawned unit).
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parent.parent
QUEST_MAGIC = b"TSQW"
CREATURE_MAGIC = b"BOMW"
GAMEOBJECT_MAGIC = b"BOGW"
QUEST_STRING_BITS = [9, 12, 12, 9, 10, 8, 10, 8, 11]
OBJ_FIXED = 41  # bytes before the description length byte
OBJ_MONSTER, OBJ_ITEM = 0, 1
PROVENANCE = ("layer", "source", "build_first_seen", "build_last_seen", "confidence")


class LayoutError(ValueError):
    pass


# ───────────────────────────── reading ─────────────────────────────

def read_wdb(path: Path, magic: bytes) -> tuple[str, int, list[tuple[int, bytes]]]:
    """Return (locale, build number, [(record id, record bytes)])."""
    d = path.read_bytes()
    if len(d) < 24 or d[:4] != magic:
        raise SystemExit(f"{path}: not a {magic[::-1].decode()} cache file")
    build, = struct.unpack_from("<I", d, 4)
    locale = d[8:12][::-1].decode("ascii", "replace")
    recs, p = [], 24
    while p + 8 <= len(d):
        rid, size = struct.unpack_from("<II", d, p)
        if rid == 0 and size == 0:
            break
        if p + 8 + size > len(d):
            raise SystemExit(f"{path}: record {rid} at {p} runs past the end of the file")
        recs.append((rid, d[p + 8:p + 8 + size]))
        p += 8 + size
    return locale, build, recs


def bits(b: bytes, pos: int, width: int) -> int:
    v = 0
    for k in range(width):
        byte = b[(pos + k) // 8]
        v = (v << 1) | ((byte >> (7 - (pos + k) % 8)) & 1)
    return v


def i32(b: bytes, o: int) -> int:
    return struct.unpack_from("<i", b, o)[0]


def text(raw: bytes) -> str:
    return raw.rstrip(b"\0").decode("utf-8")


# ───────────────────────────── quests ─────────────────────────────

def parse_quest(rid: int, r: bytes, rewards_tables=None, stats=None) -> dict:
    if len(r) < 488 or i32(r, 0) != rid:
        raise LayoutError("quest id at @0 does not match the record id")
    n_obj = i32(r, 436)
    if not 0 <= n_obj <= 20:
        raise LayoutError(f"implausible objective count {n_obj}")
    o = 488
    npcs, items = [], []
    for _ in range(n_obj):
        if o + OBJ_FIXED + 2 > len(r):
            raise LayoutError("objective runs past the record")
        otype, object_id, amount = i32(r, o + 4), i32(r, o + 9), i32(r, o + 13)
        desc_len = r[o + OBJ_FIXED]
        o += OBJ_FIXED + 2 + desc_len
        if object_id > 0 and amount > 0:
            if otype == OBJ_MONSTER:
                npcs.append({"npc_id": object_id, "count": amount})
            elif otype == OBJ_ITEM:
                items.append({"item_id": object_id, "count": amount})
    if o + 12 > len(r):
        raise LayoutError("string lengths run past the record")
    lens, pos = [], 0
    for w in QUEST_STRING_BITS:
        lens.append(bits(r, o * 8 + pos, w))
        pos += w
    s = o + (pos + 7) // 8
    if s + sum(lens) != len(r):
        raise LayoutError(f"strings end at {s + sum(lens)}, record is {len(r)} bytes")
    strings = []
    for n in lens:
        strings.append(r[s:s + n].decode("utf-8"))
        s += n
    title, objectives, description = strings[0], strings[1], strings[2]
    if not title:
        raise LayoutError("empty title")

    row: dict = {
        "quest_id": rid,
        "name": title,
        "quest_level": i32(r, 8),
        "min_level": i32(r, 16),
        "type": i32(r, 28),
        "flags": i32(r, 100),
    }
    races, = struct.unpack_from("<q", r, 440)
    row["race_mask"] = 0 if races == -1 else races
    if i32(r, 24):
        row["zone_or_sort"] = i32(r, 24)
    if i32(r, 36) > 0:
        row["next_quest_id"] = i32(r, 36)
    if objectives:
        row["objectives"] = objectives
    if description:
        row["description"] = description
    if items:
        row["objective_items"] = items
    if npcs:
        row["objective_npcs"] = npcs

    rewards: dict = {}
    if i32(r, 48):
        rewards["money"] = i32(r, 48)
    reward_items = [{"item_id": i32(r, 116 + 16 * k), "count": i32(r, 120 + 16 * k)} for k in range(4)]
    choice = [{"item_id": i32(r, 180 + 12 * k), "count": i32(r, 184 + 12 * k)} for k in range(6)]
    reward_items = [x for x in reward_items if x["item_id"] > 0 and x["count"] > 0]
    choice = [x for x in choice if x["item_id"] > 0 and x["count"] > 0]
    if reward_items:
        rewards["items"] = reward_items
    if choice:
        rewards["choice_items"] = choice
    if rewards_tables:
        quest_xp, faction_reward = rewards_tables
        xp_row = quest_xp.get(row["quest_level"])
        xp_index = i32(r, 40)
        if xp_row and 0 < xp_index <= 9:
            xp = int(xp_row.get(f"Difficulty_{xp_index}") or 0)
            if xp > 0:
                rewards["xp"] = xp
                mult = round(struct.unpack_from("<f", r, 44)[0], 4)
                if mult != 1.0:
                    rewards["xp_multiplier"] = mult
        rep = []
        for k in range(5):
            faction, index, override = i32(r, 300 + 16 * k), i32(r, 304 + 16 * k), i32(r, 308 + 16 * k)
            if faction <= 0 or index == 0:
                continue
            if override:
                if stats is not None:
                    stats["reputation overrides left out"] = stats.get("reputation overrides left out", 0) + 1
                continue
            table_row = faction_reward.get(1 if index > 0 else 2)
            if table_row and abs(index) <= 9:
                value = int(table_row.get(f"Difficulty_{abs(index)}") or 0)
                if value:
                    rep.append({"faction_id": faction, "value": value})
        if rep:
            rewards["reputation"] = rep
    row["rewards"] = rewards
    return row


# ───────────────────────────── creatures ─────────────────────────────

def parse_creature(rid: int, r: bytes) -> dict:
    if len(r) < 15:
        raise LayoutError("record shorter than its bit header")
    t, ta, cn = bits(r, 0, 11), bits(r, 11, 11), bits(r, 22, 6)
    name_lens = [bits(r, 29 + 11 * k, 11) for k in range(8)]
    o = 15 + sum(name_lens)
    if not name_lens[0]:
        raise LayoutError("no name")
    if o + 34 > len(r):
        raise LayoutError("fixed fields run past the record")
    n_disp = i32(r, o + 26)
    if not 0 <= n_disp <= 8:
        raise LayoutError(f"implausible display count {n_disp}")
    q = o + 34 + 12 * n_disp
    if q + 48 > len(r):
        raise LayoutError("fixed fields run past the record")
    n_quest_items = i32(r, q + 8)
    if not 0 <= n_quest_items <= 32 or q + 48 + t + ta + cn + 4 * n_quest_items != len(r):
        raise LayoutError("fields do not add up to the record length")

    name = text(r[15:15 + name_lens[0]])
    row: dict = {"npc_id": rid, "name": name}
    if t:
        row["subname"] = text(r[q + 48:q + 48 + t])
    row["creature_type"] = r[o + 12]
    row["classification"] = r[o + 17]
    dids = [i32(r, o + 34 + 12 * k) for k in range(n_disp)]
    dids = [x for x in dids if x > 0]
    if dids:
        row["display_ids"] = dids
    return row


# ───────────────────────────── game objects ─────────────────────────────

def parse_gameobject(rid: int, r: bytes, resolve_lock=None) -> dict:
    if len(r) < 8 + 7:
        raise LayoutError("record shorter than its header")
    obj_type, display = struct.unpack_from("<II", r, 0)
    o, strings = 8, []
    for _ in range(7):
        end = r.index(b"\0", o)
        strings.append(r[o:end].decode("utf-8"))
        o = end + 1
    if o + 35 * 4 + 4 + 1 > len(r):
        raise LayoutError("data block runs past the record")
    data = struct.unpack_from("<35i", r, o)
    o += 35 * 4 + 4
    n_quest_items = r[o]
    if o + 1 + 4 * n_quest_items + 8 != len(r):
        raise LayoutError("fields do not add up to the record length")
    if not strings[0]:
        raise LayoutError("no name")
    row: dict = {"object_id": rid, "name": strings[0], "object_type": obj_type}
    if display:
        row["display_id"] = display
    if obj_type in (3, 10) and data[0] > 0 and resolve_lock:
        row.update(resolve_lock(data[0]))
    return row


def tables_build(build: str, registry: dict, need: tuple[str, ...], what: str) -> str | None:
    """The build whose DB2 tables to use: the cache's own if extracted, else the newest one."""
    cache = REPO / ".cache" / "wago"
    candidates = [build] + sorted((b["build"] for b in registry["builds"] if b["build"] != build),
                                  key=build_key, reverse=True)
    for b in candidates:
        if all((cache / b / f"{t}.csv").exists() for t in need):
            if b != build:
                print(f"  {what} resolved with {b}'s {'/'.join(need)} ({build} not extracted)")
            return b
    print(f"  no {'/'.join(need)} in .cache/wago; {what} left out (run tools/extract_client.py for some build first)")
    return None


def lock_resolver_for(build: str, registry: dict):
    from extract_client import lock_resolver  # noqa: E402  (same directory)
    b = tables_build(build, registry, ("Lock", "LockType", "SkillLine"), "locks")
    return lock_resolver(REPO / ".cache" / "wago", b) if b else None


def quest_reward_tables_for(build: str, registry: dict):
    from extract_client import by_id, read  # noqa: E402
    b = tables_build(build, registry, ("QuestXP", "QuestFactionReward"), "quest XP and reputation")
    if not b:
        return None
    cache = REPO / ".cache" / "wago"
    return by_id(read(cache, b, "QuestXP")), by_id(read(cache, b, "QuestFactionReward"))


# ───────────────────────────── merge + write ─────────────────────────────

def build_key(build: str) -> tuple[int, ...]:
    return tuple(int(x) for x in build.split("."))


def content(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in PROVENANCE}


def merge(existing: list[dict], new: list[dict], key: str) -> tuple[list[dict], dict[str, int]]:
    """Fold cache rows from one import into the rows already on disk.

    Rows from other source kinds pass through untouched. For our own kind, an unchanged row
    widens its build range, a changed row is added next to the old one (or replaces it if it
    is for the same build_last_seen)."""
    stats = {"added": 0, "extended": 0, "changed": 0, "unchanged": 0}
    by_id: dict[int, list[dict]] = {}
    out_other = []
    for r in existing:
        if r.get("source", {}).get("kind") == "client_cache":
            by_id.setdefault(r[key], []).append(r)
        else:
            out_other.append(r)
    for r in new:
        rows = by_id.setdefault(r[key], [])
        b = r["build_last_seen"]
        same = next((x for x in rows if content(x) == content(r)), None)
        if same is not None:
            first = min(same["build_first_seen"], b, key=build_key)
            last = max(same["build_last_seen"], b, key=build_key)
            if (first, last) == (same["build_first_seen"], same["build_last_seen"]):
                stats["unchanged"] += 1
            else:
                same["build_first_seen"], same["build_last_seen"] = first, last
                stats["extended"] += 1
            continue
        clash = next((x for x in rows if x["build_last_seen"] == b), None)
        if clash is not None:
            rows[rows.index(clash)] = r
            stats["changed"] += 1
        else:
            rows.append(r)
            stats["added" if len(rows) == 1 else "changed"] += 1
    merged = out_other + [r for rows in by_id.values() for r in rows]
    merged.sort(key=lambda r: (r[key], build_key(r["build_last_seen"])))
    return merged, stats


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def prov(ref: str, build: str) -> dict:
    return {
        "layer": "client",
        "source": {"kind": "client_cache", "ref": ref},
        "build_first_seen": build,
        "build_last_seen": build,
        "confidence": "exact",
    }


# ───────────────────────────── main ─────────────────────────────

def find_wdb_dir(p: Path) -> Path:
    if (p / "questcache.wdb").exists() or (p / "creaturecache.wdb").exists():
        return p
    if (p / "enUS").is_dir():
        return p / "enUS"
    raise SystemExit(f"{p}: no questcache.wdb or creaturecache.wdb here or in {p / 'enUS'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wdb", required=True, help="Cache/WDB/enUS directory (or Cache/WDB)")
    ap.add_argument("--build", help="expected full build string; must match the cache headers")
    ap.add_argument("--out", default="data/client")
    args = ap.parse_args()

    wdb = find_wdb_dir(Path(args.wdb).expanduser())
    out = Path(args.out) if Path(args.out).is_absolute() else REPO / args.out
    registry = json.loads((REPO / "builds.json").read_text(encoding="utf-8"))
    known = {b["build"] for b in registry["builds"]}

    for fname, magic, table, key, parse in (
        ("questcache.wdb", QUEST_MAGIC, "quests", "quest_id", parse_quest),
        ("creaturecache.wdb", CREATURE_MAGIC, "npcs", "npc_id", parse_creature),
        ("gameobjectcache.wdb", GAMEOBJECT_MAGIC, "objects", "object_id", parse_gameobject),
    ):
        path = wdb / fname
        if not path.exists():
            print(f"{fname}: not present, skipped")
            continue
        locale, build_no, recs = read_wdb(path, magic)
        if locale != "enUS":
            raise SystemExit(f"{path}: locale {locale}; names in this dataset are enUS, import the enUS cache")
        matches = sorted(b for b in known if b.endswith(f".{build_no}"))
        if args.build:
            if not args.build.endswith(f".{build_no}"):
                raise SystemExit(f"{path}: written by build {build_no}, not {args.build}")
            build = args.build
        elif len(matches) == 1:
            build = matches[0]
        else:
            raise SystemExit(f"{path}: written by build {build_no}, which is not in builds.json; register it (or pass --build)")
        if build not in known:
            raise SystemExit(f"{build} is not in builds.json; register it first")

        notes: dict[str, int] = {}
        if parse is parse_gameobject:
            resolve = lock_resolver_for(build, registry)
            parse = lambda rid, r, _res=resolve: parse_gameobject(rid, r, _res)  # noqa: E731
        elif parse is parse_quest:
            tables = quest_reward_tables_for(build, registry)
            parse = lambda rid, r, _t=tables: parse_quest(rid, r, _t, notes)  # noqa: E731
        dest = out / f"{table}.jsonl"
        existing = read_jsonl(dest)
        datamined = {r[key] for r in existing if r.get("source", {}).get("kind") == "datamine"}
        rows, skipped, shadowed = [], [], 0
        for rid, r in recs:
            if rid in datamined:
                shadowed += 1
                continue
            try:
                row = parse(rid, r)
            except (LayoutError, UnicodeDecodeError, struct.error, IndexError) as e:
                skipped.append(f"{rid} ({e})")
                continue
            row.update(prov(fname, build))
            rows.append(row)
        merged, stats = merge(existing, rows, key)
        write_jsonl(dest, merged)
        rel = dest.relative_to(REPO) if dest.is_relative_to(REPO) else dest
        print(f"{fname} (build {build}, {len(recs)} records): {len(rows)} decoded -> {rel} "
              f"({', '.join(f'{v} {k}' for k, v in stats.items())})")
        if shadowed:
            print(f"  {shadowed} already have a DB2 row, left as is")
        for what, n in sorted(notes.items()):
            print(f"  {n} {what}")
        if skipped:
            print(f"  {len(skipped)} skipped, layout did not check out:")
            for s in skipped:
                print(f"    {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
