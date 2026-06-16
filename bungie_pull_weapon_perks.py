"""
bungie_pull_weapon_perks.py  (v5)

Pulls each weapon's manifest hash, equip slot, element, and full perk pool and
writes data/weapon_perks.json:

  { "<weapons_tree name>": {
        "hash": <int>, "ammo": "Primary", "slot": "Energy",
        "type": "Auto Rifle", "element": "Void", "perks": ["...", ...] }, ... }

v5: added "slot" (Kinetic / Energy / Power) from the weapon's inventory bucket, so
the recommender can fill the two legendary slots the exotic does not occupy
without a DIM slot collision. Ammo alone is not enough: Primary and Special
weapons live in either Kinetic or Energy.

v4: element from the manifest damage type, not the stale tree. Kinetic maps to "".
v3: dropped the currentlyCanRoll filter (end-of-life manifest flags everything not
rollable); perks are the full union of plugs from randomizedPlugSetHash sockets.

USAGE (machine with internet, from the repo root or data/):
    set BUNGIE_API_KEY=your_key_here      (Windows)
    export BUNGIE_API_KEY=your_key_here   (mac/linux)
    pip install requests
    python bungie_pull_weapon_perks.py
Then commit data/weapon_perks.json and redeploy.
"""
import json
import os
import re

import requests

API_KEY = os.environ.get("BUNGIE_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY}
BASE = "https://www.bungie.net"
HERE = os.path.dirname(os.path.abspath(__file__))

WEAPON_ITEM_TYPE = 3
DAMAGE_ENUM = {1: "Kinetic", 2: "Arc", 3: "Solar", 4: "Void", 6: "Stasis",
               7: "Strand"}
# weapon equip buckets -> short slot name
SLOT_BY_BUCKET = {1498876634: "Kinetic", 2465295065: "Energy", 953998645: "Power"}


def data_path(name):
    for c in (os.path.join(HERE, name), os.path.join(HERE, "data", name)):
        if os.path.exists(c):
            return c
    return os.path.join(HERE, "data", name)


def norm(s):
    s = str(s or "").strip().lower()
    for a in ["\u2019", "\u2018", "\u02bc", "`"]:
        s = s.replace(a, "'")
    return re.sub(r"\s+", " ", s)


def get_json(u):
    r = requests.get(u, headers=HEADERS, timeout=300)
    r.raise_for_status()
    return r.json()


def main():
    if not API_KEY:
        raise SystemExit("Set BUNGIE_API_KEY in your environment first.")

    tree = json.load(open(data_path("weapons_tree.json"), encoding="utf-8"))
    meta = {}
    for ammo, types in tree.items():
        for wtype, entries in types.items():
            for entry in entries:
                if not entry:
                    continue
                nm = entry[0]
                el = entry[1] if len(entry) > 1 else ""
                meta[norm(nm)] = {"name": nm, "ammo": ammo, "type": wtype,
                                  "element": el}

    print("Fetching manifest...")
    comp = get_json(BASE + "/Platform/Destiny2/Manifest/")
    paths = comp["Response"]["jsonWorldComponentContentPaths"]["en"]
    items = get_json(BASE + paths["DestinyInventoryItemDefinition"])
    plugsets = get_json(BASE + paths["DestinyPlugSetDefinition"])
    dmgdefs = get_json(BASE + paths["DestinyDamageTypeDefinition"])
    print("  items:", len(items), " plugsets:", len(plugsets),
          " damage types:", len(dmgdefs))

    dmg_name = {int(h): (c.get("displayProperties") or {}).get("name", "")
                for h, c in dmgdefs.items()}

    def weapon_element(it):
        dh = it.get("defaultDamageTypeHash")
        if not dh:
            dhs = it.get("damageTypeHashes") or []
            dh = dhs[0] if dhs else None
        name = dmg_name.get(int(dh), "") if dh else ""
        if not name:
            name = DAMAGE_ENUM.get(it.get("defaultDamageType"), "")
        return "" if name == "Kinetic" else name

    def weapon_slot(it):
        bh = (it.get("inventory") or {}).get("bucketTypeHash")
        return SLOT_BY_BUCKET.get(int(bh), "") if bh else ""

    def perk_names_from_plugset(ps_hash):
        ps = plugsets.get(str(ps_hash))
        if not ps:
            return []
        out = []
        for pi in ps.get("reusablePlugItems") or []:
            pit = items.get(str(pi.get("plugItemHash")))
            if not pit:
                continue
            pn = (pit.get("displayProperties") or {}).get("name")
            if pn:
                out.append(pn)
        return out

    def rollable_perks(it):
        entries = (it.get("sockets") or {}).get("socketEntries") or []
        names, seen = [], set()
        for e in entries:
            ps_hash = e.get("randomizedPlugSetHash")
            if not ps_hash:
                continue
            for pn in perk_names_from_plugset(ps_hash):
                if pn not in seen:
                    seen.add(pn)
                    names.append(pn)
        return names

    cand = {}
    for h, it in items.items():
        if it.get("itemType") != WEAPON_ITEM_TYPE:
            continue
        nm = norm((it.get("displayProperties") or {}).get("name"))
        if nm not in meta:
            continue
        cand.setdefault(nm, []).append(
            (int(h), rollable_perks(it), weapon_element(it), weapon_slot(it)))

    out, missing = {}, []
    for nm, info in meta.items():
        cs = cand.get(nm) or []
        if not cs:
            missing.append(info["name"])
            continue
        best = max(cs, key=lambda x: len(x[1]))
        out[info["name"]] = {"hash": best[0], "ammo": info["ammo"],
                             "slot": best[3], "type": info["type"],
                             "element": best[2], "perks": best[1]}

    data_dir = os.path.dirname(data_path("weapons_tree.json"))
    out_path = os.path.join(data_dir, "weapon_perks.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(out.items())), f, ensure_ascii=False, indent=1)

    with_perks = sum(1 for v in out.values() if v["perks"])
    from collections import Counter
    slots = Counter(v["slot"] or "(none)" for v in out.values())
    print("\nresolved", len(out), "of", len(meta), "weapons")
    print("  with a non-empty perk pool:", with_perks)
    print("  slot distribution:", dict(slots))
    for nm in ["Anonymous Autumn", "Gnawing Hunger", "Apex Predator", "Funnelweb"]:
        if nm in out:
            v = out[nm]
            print("  check:", nm, "->", v["element"] or "(kinetic)", "/", v["slot"])
    print("Wrote", out_path)


if __name__ == "__main__":
    main()
