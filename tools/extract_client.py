#!/usr/bin/env python3
"""Extract data/client/ tables for one WoW: Forever client build from wago.tools.

wago.tools publishes every build's DB2 tables as CSV:
  GET https://wago.tools/api/builds                      -> {product: [{version, created_at, ...}]}
  GET https://wago.tools/db2/<Table>/csv?build=<version> -> CSV, arrays flattened to Name_0..Name_n

Forever beta builds (1.60.1.x) are published under the product `wow_classic_beta`
(2026-09, from recordings; re-check with --list-builds since the launch product may differ).
As of 2026-10-22 wago also lists a `wowf` product (1.60.1.70245, 1.60.1.70532); likely the live one.

Usage:
  python tools/extract_client.py --list-builds
  python tools/extract_client.py --build 1.60.1.70374 [--product wow_classic_beta]
  python tools/extract_client.py --build 1.60.1.70374 --offline      # transform from cache only

Tables are cached under .cache/wago/<build>/<Table>.csv and never re-downloaded.
Output: data/client/{items,spells,recipes,npcs,objects}.jsonl, sorted by primary key.

What is NOT produced here and why:
  - client/quests: quest names, text and rewards are not in DB2 on the modern engine; the
    server sends them and the client caches them in Cache/WDB/<locale>/questcache.wdb.
    A separate importer (source.kind: client_cache) will handle that file.
  - NPC levels: same, creaturecache.wdb.
  - Item stats, damage and armor are COMPUTED from budget tables (RandPropPoints, ItemDamage*,
    ItemArmor*) with the formulas the client uses. The raw budget values are kept next to the
    computed numbers (`stat_budget`, `damage.table`, `armor_source`) so anyone can recompute.
    Spot-check against in-game tooltips before treating the computed numbers as exact.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WAGO = "https://wago.tools"
USER_AGENT = "wowlayerup-forever/opendata extractor (+https://github.com/wowlayerup-forever/opendata)"

TABLES = [
    "Item", "ItemSparse", "ItemEffect", "ItemXItemEffect", "ItemSet",
    "RandPropPoints", "ItemArmorQuality", "ItemArmorTotal", "ItemArmorShield", "ArmorLocation",
    "ItemDamageOneHand", "ItemDamageOneHandCaster", "ItemDamageTwoHand", "ItemDamageTwoHandCaster",
    "ItemDamageRanged", "ItemDamageThrown", "ItemDamageAmmo",
    "SpellName", "Spell", "SpellLevels", "SpellMisc", "SpellCastTimes", "SpellEffect", "SpellReagents",
    "SpellTotems", "SkillLineAbility", "SkillLine",
    "Creature", "GameObjects", "Lock",
]

# ItemStatType -> name. Unknown types are kept as stat_<n>.
STAT_NAMES = {
    0: "mana", 1: "health", 3: "agility", 4: "strength", 5: "intellect", 6: "spirit", 7: "stamina",
    12: "defense_rating", 13: "dodge_rating", 14: "parry_rating", 15: "block_rating",
    16: "hit_melee_rating", 17: "hit_ranged_rating", 18: "hit_spell_rating",
    19: "crit_melee_rating", 20: "crit_ranged_rating", 21: "crit_spell_rating",
    28: "haste_melee_rating", 29: "haste_ranged_rating", 30: "haste_spell_rating",
    31: "hit_rating", 32: "crit_rating", 35: "resilience_rating", 36: "haste_rating",
    37: "expertise_rating", 38: "attack_power", 39: "ranged_attack_power",
    41: "spell_healing", 42: "spell_damage", 43: "mana_regen", 44: "armor_penetration_rating",
    45: "spell_power", 46: "health_regen", 47: "spell_penetration", 48: "block_value",
    49: "mastery_rating", 50: "bonus_armor", 51: "fire_resistance", 52: "frost_resistance",
    53: "holy_resistance", 54: "shadow_resistance", 55: "nature_resistance", 56: "arcane_resistance",
    57: "pvp_power", 71: "agility_strength_intellect", 72: "agility_strength", 73: "agility_intellect",
    74: "strength_intellect",
}

# ItemEffect.TriggerType -> trigger name in schema
TRIGGER = {0: "use", 1: "equip", 2: "chance_on_hit", 4: "soulstone", 6: "learn"}

# Inventory type -> RandPropPoints column bucket (0..4). From the client's stat-budget rules.
INV_BUCKET = {
    1: 0, 5: 0, 7: 0, 20: 0, 17: 0,            # head, chest, legs, robe, two-hand
    3: 1, 6: 1, 8: 1, 10: 1, 12: 1,             # shoulder, waist, feet, hands, trinket
    2: 2, 9: 2, 11: 2, 14: 2, 16: 2, 23: 2,     # neck, wrist, finger, shield, cloak, holdable
    13: 3, 21: 3, 22: 3, 15: 3, 25: 3, 26: 3,   # one-hand, main hand, off-hand weapon, ranged, thrown, ranged right (wand)
    28: 4,                                      # relic
}
TWO_HAND_SUBCLASSES = {1, 5, 6, 8, 10, 20}  # axe2, mace2, polearm, sword2, staff, fishing pole
MATERIAL_COL = {7: "Cloth", 8: "Leather", 5: "Mail", 6: "Plate"}
INV_ARMORLOC = {1: 1, 3: 3, 5: 5, 20: 5, 6: 6, 7: 7, 8: 8, 9: 9, 10: 10, 16: 16}  # ArmorLocation keyed by inventory type


# ───────────────────────────── download ─────────────────────────────

def http_get(url: str, dest: Path, attempts: int = 5) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    delay = 2.0
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                tmp.rename(dest)
                return
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                raise SystemExit(f"{url}: HTTP {e.code} (table not published for this build?)")
            if e.code in (429,) or e.code >= 500:
                ra = e.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else delay
                print(f"  {e.code} on {url}, retrying in {wait:.0f}s", file=sys.stderr)
                time.sleep(min(wait, 120))
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  {e} on {url}, retrying in {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    raise SystemExit(f"gave up on {url}")


def list_builds(cache: Path) -> dict:
    dest = cache / "builds.json"
    http_get(f"{WAGO}/api/builds", dest)
    return json.loads(dest.read_text(encoding="utf-8"))


def fetch_tables(build: str, cache: Path, offline: bool) -> None:
    for t in TABLES:
        dest = cache / build / f"{t}.csv"
        if dest.exists():
            continue
        if offline:
            raise SystemExit(f"--offline but {dest} is missing")
        print(f"  fetching {t}")
        http_get(f"{WAGO}/db2/{t}/csv?{urllib.parse.urlencode({'build': build})}", dest)
        time.sleep(0.5)  # be polite; wago.tools is community-run


# ───────────────────────────── csv helpers ─────────────────────────────

def read(cache: Path, build: str, table: str) -> list[dict[str, str]]:
    with open(cache / build / f"{table}.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def i(v: str | None, default: int = 0) -> int:
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return int(float(v))


def fl(v: str | None, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    return float(v)


def arr(row: dict, name: str, n: int, conv=i) -> list:
    return [conv(row.get(f"{name}_{k}")) for k in range(n)]


def by_id(rows: list[dict], key: str = "ID") -> dict[int, dict]:
    return {i(r[key]): r for r in rows}


def group(rows: list[dict], key: str) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        out[i(r[key])].append(r)
    return out


def prov(table: str, build: str) -> dict:
    return {
        "layer": "client",
        "source": {"kind": "datamine", "ref": table},
        "build_first_seen": build,
        "build_last_seen": build,
        "confidence": "exact",
    }


def write_jsonl(path: Path, rows: list[dict], key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: r[key])
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
    print(f"  wrote {rel}: {len(rows)} rows")


# ───────────────────────────── transforms ─────────────────────────────

def quality_bucket(q: int) -> str:
    return "Epic" if q >= 4 else "Superior" if q == 3 else "Good"


def compute_stats(sparse: dict, inv: int, ilvl: int, quality: int, randprop: dict[int, dict]) -> tuple[dict, dict]:
    """Return (computed stats, raw budget). value = StatPercentEditor * RandPropPoints bucket / 10000."""
    stats: dict[str, int] = {}
    budget: dict[str, int] = {}
    bucket = INV_BUCKET.get(inv)
    rp = randprop.get(ilvl)
    types = arr(sparse, "StatModifier_bonusStat", 10)
    editors = arr(sparse, "StatPercentEditor", 10)
    for st, ed in zip(types, editors):
        if st <= 0 and ed == 0:
            continue
        if st < 0:
            continue
        name = STAT_NAMES.get(st, f"stat_{st}")
        budget[name] = ed
        if rp is None or bucket is None:
            continue
        points = i(rp.get(f"{quality_bucket(quality)}_{bucket}"))
        value = int(math.floor(ed * points / 10000.0 + 0.5))
        if value:
            stats[name] = stats.get(name, 0) + value
    return stats, budget


def damage_table(item_class: int, subclass: int, inv: int, flags1: int) -> str | None:
    if item_class == 6:  # projectile
        return "ItemDamageAmmo"
    if item_class != 2:
        return None
    caster = bool(flags1 & 0x200)
    if subclass in (2, 3, 18):  # bow, gun, crossbow
        return "ItemDamageRanged"
    if subclass == 16:
        return "ItemDamageThrown"
    if subclass == 19:  # wand
        return "ItemDamageRanged"
    if subclass in TWO_HAND_SUBCLASSES or inv == 17:
        return "ItemDamageTwoHandCaster" if caster else "ItemDamageTwoHand"
    return "ItemDamageOneHandCaster" if caster else "ItemDamageOneHand"


def compute_damage(sparse: dict, item: dict, ilvl: int, quality: int, dmg_tables: dict[str, dict[int, dict]]) -> dict | None:
    table = damage_table(i(item["ClassID"]), i(item["SubclassID"]), i(item["InventoryType"]), i(sparse.get("Flags_1")))
    if table is None:
        return None
    row = dmg_tables[table].get(ilvl)
    delay = i(sparse.get("ItemDelay"))
    if row is None or delay <= 0:
        return None
    dps = fl(row.get(f"Quality_{quality}"))
    variance = fl(sparse.get("DmgVariance"))
    avg = dps * delay / 1000.0
    return {
        "min": int(math.floor(avg * (1 - variance / 2) + 0.5)),
        "max": int(math.floor(avg * (1 + variance / 2) + 0.5)),
        "speed_ms": delay,
        "school": i(sparse.get("DamageType")),
        "table": table,
    }


def compute_armor(sparse: dict, item: dict, ilvl: int, quality: int, armor: dict) -> tuple[int | None, str | None]:
    item_class, subclass, inv = i(item["ClassID"]), i(item["SubclassID"]), i(item["InventoryType"])
    if item_class != 4:
        return None, None
    qmod_row = armor["ItemArmorQuality"].get(ilvl)
    if qmod_row is None:
        return None, None
    qmod = fl(qmod_row.get(f"Qualitymod_{quality}"))
    if inv == 14:  # shield
        row = armor["ItemArmorShield"].get(ilvl)
        if row is None:
            return None, None
        return int(math.floor(fl(row.get(f"Quality_{quality}")) + 0.5)), "ItemArmorShield"
    material = MATERIAL_COL.get(i(item.get("Material")))
    loc = armor["ArmorLocation"].get(INV_ARMORLOC.get(inv, -1))
    total = armor["ItemArmorTotal"].get(ilvl)
    if not (material and loc and total):
        return None, None
    mod = fl(loc.get(f"{material.replace('Mail', 'Chain')}modifier"))
    base = fl(total.get(material))
    return int(math.floor(qmod * base * mod + 0.5)), "ItemArmorTotal"


def extract_items(cache: Path, build: str) -> list[dict]:
    items = by_id(read(cache, build, "Item"))
    sparse = by_id(read(cache, build, "ItemSparse"))
    randprop = by_id(read(cache, build, "RandPropPoints"))
    effects = by_id(read(cache, build, "ItemEffect"))
    x_effects = group(read(cache, build, "ItemXItemEffect"), "ItemID")
    dmg_tables = {t: by_id(read(cache, build, t)) for t in (
        "ItemDamageOneHand", "ItemDamageOneHandCaster", "ItemDamageTwoHand", "ItemDamageTwoHandCaster",
        "ItemDamageRanged", "ItemDamageThrown", "ItemDamageAmmo")}
    armor = {t: by_id(read(cache, build, t)) for t in ("ItemArmorQuality", "ItemArmorTotal", "ItemArmorShield", "ArmorLocation")}
    spell_ids = {i(r["ID"]) for r in read(cache, build, "SpellName") if r.get("Name_lang")}

    out = []
    dangling_spells = 0
    # Item rows with no ItemSparse row: the client gets name and stats from hotfixes or the
    # server, so all we can state is what Item has. Emitted as stubs so references resolve.
    for item_id, it in items.items():
        if item_id in sparse:
            continue
        row = {
            "item_id": item_id,
            "sparse": False,
            "item_class": i(it.get("ClassID")),
            "item_subclass": i(it.get("SubclassID")),
            "inventory_type": i(it.get("InventoryType")),
        }
        if i(it.get("IconFileDataID")):
            row["icon_file_data_id"] = i(it["IconFileDataID"])
        row.update(prov("Item", build))
        out.append(row)
    for item_id, sp in sparse.items():
        it = items.get(item_id)
        if it is None:
            continue
        name = sp.get("Display_lang", "")
        if not name:
            continue
        ilvl = i(sp.get("ItemLevel"))
        quality = i(sp.get("OverallQualityID"))
        inv = i(it.get("InventoryType"))
        stats, budget = compute_stats(sp, inv, ilvl, quality, randprop)
        row = {
            "item_id": item_id,
            "name": name,
            "quality": quality,
            "item_level": ilvl,
            "required_level": i(sp.get("RequiredLevel")),
            "item_class": i(it.get("ClassID")),
            "item_subclass": i(it.get("SubclassID")),
            "inventory_type": inv,
            "binding": i(sp.get("Bonding")),
            "stackable": max(1, i(sp.get("Stackable"), 1)),
            "stats": stats,
        }
        if budget:
            row["stat_budget"] = budget
        armor_v, armor_src = compute_armor(sp, it, ilvl, quality, armor)
        if armor_v:
            row["armor"] = armor_v
            row["armor_source"] = armor_src
        dmg = compute_damage(sp, it, ilvl, quality, dmg_tables)
        if dmg:
            row["damage"] = dmg
        spells = []
        for x in x_effects.get(item_id, []):
            eff = effects.get(i(x["ItemEffectID"]))
            if eff and i(eff.get("SpellID")) > 0:
                if i(eff["SpellID"]) not in spell_ids:
                    dangling_spells += 1  # effect points at a spell this build does not ship; inert
                    continue
                spells.append({"spell_id": i(eff["SpellID"]), "trigger": TRIGGER.get(i(eff.get("TriggerType")), "use")})
        if spells:
            row["spells"] = spells
        for src, dst in (("AllowableClass", "allowable_class_mask"), ("AllowableRace_0", "allowable_race_mask"),
                         ("RequiredSkill", "required_skill"), ("RequiredSkillRank", "required_skill_rank"),
                         ("SellPrice", "sell_price"), ("BuyPrice", "buy_price"), ("ItemSet", "set_id")):
            v = i(sp.get(src))
            if v and not (dst.endswith("_mask") and v == -1):
                row[dst] = v
        if i(it.get("IconFileDataID")):
            row["icon_file_data_id"] = i(it["IconFileDataID"])
        if sp.get("Description_lang"):
            row["description"] = sp["Description_lang"]
        row.update(prov("ItemSparse", build))
        out.append(row)
    stubs = sum(1 for r in out if r.get("sparse") is False)
    print(f"  items: {stubs} stubs without ItemSparse, {dangling_spells} effects dropped for missing spells")
    return out


def extract_spells_and_recipes(cache: Path, build: str) -> tuple[list[dict], list[dict]]:
    names = by_id(read(cache, build, "SpellName"))
    spell = by_id(read(cache, build, "Spell"))
    levels = {i(r["SpellID"]): r for r in read(cache, build, "SpellLevels") if i(r.get("DifficultyID")) == 0}
    misc = {i(r["SpellID"]): r for r in read(cache, build, "SpellMisc") if i(r.get("DifficultyID")) == 0}
    cast_times = by_id(read(cache, build, "SpellCastTimes"))
    effects = group(read(cache, build, "SpellEffect"), "SpellID")
    reagents = {i(r["SpellID"]): r for r in read(cache, build, "SpellReagents")}
    totems = {i(r["SpellID"]): r for r in read(cache, build, "SpellTotems")}
    sla = read(cache, build, "SkillLineAbility")
    skill_of_spell = {i(r["Spell"]): i(r["SkillLine"]) for r in sla}
    item_effects = by_id(read(cache, build, "ItemEffect"))
    x_effects = read(cache, build, "ItemXItemEffect")

    # recipe item -> recipe spell: item 'learn' effect -> spell with effect 36 (learn spell) -> EffectTriggerSpell
    taught_by: dict[int, int] = {}
    for x in x_effects:
        eff = item_effects.get(i(x["ItemEffectID"]))
        if not eff or i(eff.get("TriggerType")) != 6:
            continue
        for e in effects.get(i(eff["SpellID"]), []):
            if i(e.get("Effect")) == 36 and i(e.get("EffectTriggerSpell")) > 0:
                taught_by.setdefault(i(e["EffectTriggerSpell"]), i(x["ItemID"]))

    spells_out = []
    for sid, nm in names.items():
        name = nm.get("Name_lang", "")
        if not name:
            continue
        row = {"spell_id": sid, "name": name}
        sp = spell.get(sid, {})
        if sp.get("NameSubtext_lang"):
            row["rank"] = sp["NameSubtext_lang"]
        if sp.get("Description_lang"):
            row["description"] = sp["Description_lang"]
        m = misc.get(sid)
        if m:
            row["school"] = i(m.get("SchoolMask"))
        lv = levels.get(sid)
        if lv:
            row["spell_level"] = i(lv.get("SpellLevel"))
        if sid in skill_of_spell:
            row["skill_id"] = skill_of_spell[sid]
        row.update(prov("SpellName", build))
        spells_out.append(row)

    # A recipe spell can sit under several SkillLines (shared tailoring/leatherworking recipes) or
    # appear twice under the same one. One row per spell; the lowest SkillLineAbility row wins.
    sla_by_spell: dict[int, list[dict]] = defaultdict(list)
    for r in sorted(sla, key=lambda r: i(r["ID"])):
        sla_by_spell[i(r["Spell"])].append(r)

    recipes_out = []
    for sid, sla_rows in sla_by_spell.items():
        r = sla_rows[0]
        create = next((e for e in effects.get(sid, []) if i(e.get("Effect")) == 24 and i(e.get("EffectItemType")) > 0), None)
        if create is None:
            continue
        rg = reagents.get(sid)
        reag = []
        if rg:
            for k in range(8):
                it, ct = i(rg.get(f"Reagent_{k}")), i(rg.get(f"ReagentCount_{k}"))
                if it > 0 and ct > 0:
                    reag.append({"item_id": it, "count": ct})
        if not reag:
            continue  # not a crafting recipe (e.g. class summon creating an item)
        count = max(1, int(round(fl(create.get("EffectBasePointsF"), 1.0))))
        row = {
            "recipe_spell_id": sid,
            "name": names.get(sid, {}).get("Name_lang", f"spell {sid}"),
            "skill_id": i(r["SkillLine"]),
            "skill_required": i(r.get("MinSkillLineRank")),
            "skill_green": i(r.get("TrivialSkillLineRankLow")),
            "skill_gray": i(r.get("TrivialSkillLineRankHigh")),
            "creates": {"item_id": i(create["EffectItemType"]), "count": count},
            "reagents": reag,
        }
        skills = sorted({i(x["SkillLine"]) for x in sla_rows})
        if len(skills) > 1:
            row["skill_ids"] = skills
        tt = totems.get(sid)
        if tt:
            tools = [i(tt.get(f"Totem_{k}")) for k in range(2) if i(tt.get(f"Totem_{k}")) > 0]
            if tools:
                row["tool_item_ids"] = tools
        if sid in taught_by:
            row["taught_by_item_id"] = taught_by[sid]
        m = misc.get(sid)
        if m and i(m.get("CastingTimeIndex")) in cast_times:
            row["cast_ms"] = i(cast_times[i(m["CastingTimeIndex"])].get("Base"))
        row.update(prov("SkillLineAbility", build))
        recipes_out.append(row)
    return spells_out, recipes_out


def extract_npcs(cache: Path, build: str) -> list[dict]:
    out = []
    for r in read(cache, build, "Creature"):
        name = r.get("Name_lang", "")
        if not name:
            continue
        row = {"npc_id": i(r["ID"]), "name": name}
        if r.get("Title_lang"):
            row["subname"] = r["Title_lang"]
        row["creature_type"] = i(r.get("CreatureType"))
        row["classification"] = i(r.get("Classification"))
        dids = [d for d in arr(r, "DisplayID", 4) if d > 0]
        if dids:
            row["display_ids"] = dids
        row.update(prov("Creature", build))
        out.append(row)
    return out


def extract_objects(cache: Path, build: str) -> list[dict]:
    locks = by_id(read(cache, build, "Lock"))
    out = []
    for r in read(cache, build, "GameObjects"):
        name = r.get("Name_lang", "")
        if not name:
            continue
        row = {"object_id": i(r["ID"]), "name": name, "object_type": i(r.get("TypeID"))}
        if i(r.get("DisplayID")):
            row["display_id"] = i(r["DisplayID"])
        # chests (3) and goobers keep a lock id in PropValue_0; lock type 2 = skill (herbalism 182, mining 186, lockpicking 633)
        if row["object_type"] in (3, 10):
            lock = locks.get(i(r.get("PropValue_0")))
            if lock:
                for k in range(8):
                    if i(lock.get(f"Type_{k}")) == 2 and i(lock.get(f"Index_{k}")) > 0:
                        row["required_skill_id"] = i(lock[f"Index_{k}"])
                        row["required_skill_rank"] = i(lock.get(f"Skill_{k}"))
                        break
        row.update(prov("GameObjects", build))
        out.append(row)
    return out


# ───────────────────────────── main ─────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="full version string, e.g. 1.60.1.70374")
    ap.add_argument("--product", default=None, help="wago product the build is listed under (informational)")
    ap.add_argument("--list-builds", action="store_true", help="print products with 1.x builds and exit")
    ap.add_argument("--cache", default=".cache/wago")
    ap.add_argument("--out", default="data/client")
    ap.add_argument("--offline", action="store_true", help="never download; fail if a table is missing from cache")
    ap.add_argument("--only", nargs="*", choices=["items", "spells", "recipes", "npcs", "objects"])
    ap.add_argument("--register", action="store_true", help="add the build to builds.json (status from --product, date from wago's listing) if missing")
    args = ap.parse_args()

    cache = Path(args.cache).resolve() if Path(args.cache).is_absolute() else (REPO / args.cache).resolve()
    if args.list_builds:
        builds = list_builds(cache)
        for product, entries in builds.items():
            ones = [e for e in entries if e.get("version", "").startswith("1.")]
            if ones:
                print(product)
                for e in ones[:5]:
                    print(f"  {e['version']}  {e.get('created_at', '')}")
        return 0
    if not args.build:
        ap.error("--build is required (or --list-builds)")

    registry_path = REPO / "builds.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if args.build not in {b["build"] for b in registry["builds"]}:
        if not args.register:
            print(f"note: {args.build} is not in builds.json yet; add it (or pass --register) before committing the output", file=sys.stderr)
        else:
            created = None
            product = args.product
            if not args.offline:
                for prod, entries in list_builds(cache).items():
                    for e in entries:
                        if e.get("version") == args.build:
                            created, product = e.get("created_at", "")[:10] or None, product or prod
            registry["builds"].append({
                "build": args.build, "product": product or "unknown",
                "status": "beta" if (product or "").endswith("beta") else "live",
                "released": created, "notes": "registered by tools/extract_client.py --register; fill in interface number",
            })
            registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
            print(f"registered {args.build} in builds.json")

    print(f"build {args.build}: tables cached under {cache}")
    fetch_tables(args.build, cache, args.offline)
    out = Path(args.out).resolve() if Path(args.out).is_absolute() else REPO / args.out
    want = set(args.only or ["items", "spells", "recipes", "npcs", "objects"])

    print("transforming")
    if "items" in want:
        write_jsonl(out / "items.jsonl", extract_items(cache, args.build), "item_id")
    if want & {"spells", "recipes"}:
        spells, recipes = extract_spells_and_recipes(cache, args.build)
        if "spells" in want:
            write_jsonl(out / "spells.jsonl", spells, "spell_id")
        if "recipes" in want:
            write_jsonl(out / "recipes.jsonl", recipes, "recipe_spell_id")
    if "npcs" in want:
        write_jsonl(out / "npcs.jsonl", extract_npcs(cache, args.build), "npc_id")
    if "objects" in want:
        write_jsonl(out / "objects.jsonl", extract_objects(cache, args.build), "object_id")
    print("done. Run tools/validate.py next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
