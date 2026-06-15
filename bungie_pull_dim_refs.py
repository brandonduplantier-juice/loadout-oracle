"""
bungie_pull_dim_refs.py

Second puller for the one-click DIM import. pool_hashes.json already maps the
subclass PLUGS (super, aspects, fragments, grenade, melee) and exotics to
manifest hashes. A valid DIM loadout also needs:

  1) subclass item hashes per class+element, plus the socket layout
     (which socket index holds super / class / movement / melee / grenade /
     aspects / fragments) so we can build socketOverrides.
  2) armor mod name -> hash (Siphon, Surge, Resistance, Recuperation, ...).
  3) the six Armor 3.0 stat name -> stat hash (for statConstraints).

Output: dim_refs.json

USAGE:
    pip install requests
    set BUNGIE_API_KEY=<your Bungie.net API key>
    python bungie_pull_dim_refs.py

Run it from the repo root or beside pool.json / mods_stats.json. The API key is
read from the BUNGIE_API_KEY environment variable; nothing is hardcoded.
"""
import json
import os
import re

import requests

API_KEY = os.environ.get("BUNGIE_API_KEY", "PUT_YOUR_BUNGIE_API_KEY_HERE")
HEADERS = {"X-API-Key": API_KEY}
BASE = "https://www.bungie.net"
HERE = os.path.dirname(os.path.abspath(__file__))

DAMAGE = {1: "Kinetic", 2: "Arc", 3: "Solar", 4: "Void", 6: "Stasis", 7: "Strand"}
CLASS = {0: "Titan", 1: "Hunter", 2: "Warlock"}
SUBCLASS_ITEM_TYPE = 16  # DestinyItemType.Subclass
CLASS_ITEM_BUCKET = 1585787867  # inventory bucket for the class-item slot

# Subclass display name -> element. damageType is unreliable on subclass items
# (often 0), so resolve by name. Covers all current 3.0 subclasses.
SUBCLASS_ELEMENT = {
    "sunbreaker": "Solar", "striker": "Arc", "sentinel": "Void",
    "behemoth": "Stasis", "berserker": "Strand",
    "gunslinger": "Solar", "arcstrider": "Arc", "nightstalker": "Void",
    "revenant": "Stasis", "threadrunner": "Strand",
    "dawnblade": "Solar", "stormcaller": "Arc", "voidwalker": "Void",
    "shadebinder": "Stasis", "broodweaver": "Strand",
}

# the six Armor 3.0 stats by display name
STAT_NAMES = ["Weapons", "Health", "Class", "Grenade", "Super", "Melee"]


def norm(s):
    s = str(s or "").strip().lower()
    for a in ["\u2019", "\u2018", "\u02bc", "`"]:
        s = s.replace(a, "'")
    return re.sub(r"\s+", " ", s)


def get_json(u):
    r = requests.get(u, headers=HEADERS, timeout=300)
    r.raise_for_status()
    return r.json()


def find_data(*names):
    for n in names:
        for c in (os.path.join(HERE, n), os.path.join(HERE, "data", n)):
            if os.path.exists(c):
                return c
    return None


def armor_mod_names():
    """Concrete mod display names we want hashes for, expanded from the app's
    mods_stats.json templates (and a sensible default set as a fallback)."""
    names = set()
    p = find_data("mods_stats.json")
    elements = ["Arc", "Solar", "Void", "Stasis", "Strand", "Harmonic"]
    if p:
        ms = json.load(open(p, encoding="utf-8"))
        for _slot, mods in ms.get("armor_mods", {}).items():
            for m in mods:
                base = m["mod"].replace("<Weapon>", "Primary")
                if "<Element>" in base:
                    for el in elements:
                        names.add(base.replace("<Element>", el))
                else:
                    names.add(base)
    names.update({
        "Recuperation", "Better Already", "Absolution", "Innervation",
        "Invigoration", "Bomber", "Outreach", "Distribution",
        "Powerful Attraction", "Reaper", "Time Dilation", "Concussive Dampener",
        "Sniper Damage Resistance", "Heavy Handed", "Momentum Transfer",
        "Impact Induction", "Grenade Kickstart", "Melee Kickstart",
    })
    return {norm(n): n for n in names}


def main():
    if API_KEY == "PUT_YOUR_BUNGIE_API_KEY_HERE":
        raise SystemExit("Set API_KEY first (env BUNGIE_API_KEY), rotate the old one.")

    print("Fetching manifest...")
    comp = get_json(BASE + "/Platform/Destiny2/Manifest/")
    paths = comp["Response"]["jsonWorldComponentContentPaths"]["en"]
    items = get_json(BASE + paths["DestinyInventoryItemDefinition"])
    stats = get_json(BASE + paths["DestinyStatDefinition"])
    print("  items:", len(items), "stats:", len(stats))

    # ---- subclasses ----
    subclasses = {}
    for h, it in items.items():
        if it.get("itemType") != SUBCLASS_ITEM_TYPE:
            continue
        if it.get("redacted"):
            continue
        nm = (it.get("displayProperties") or {}).get("name", "")
        cls = CLASS.get(it.get("classType"))
        if not cls:
            continue
        nl = norm(nm)
        if "prismatic" in nl:
            elem = "Prismatic"
        else:
            elem = SUBCLASS_ELEMENT.get(nl) or DAMAGE.get(it.get("damageType"))
            if not elem:
                elem = next((e for e in ["Arc", "Solar", "Void", "Stasis", "Strand"]
                             if e.lower() in nl), None)
        if not elem:
            continue
        # socket layout: list each socket index with its category, if available
        socket_idx = {}
        socks = (it.get("sockets") or {})
        cats = socks.get("socketCategories") or []
        for c in cats:
            for i in c.get("socketIndexes", []):
                socket_idx[i] = c.get("socketCategoryHash")
        key = cls + "|" + elem
        # prefer the definition that actually has sockets (skip stub duplicates)
        if key in subclasses and not socket_idx:
            continue
        subclasses[key] = {
            "hash": int(h), "name": nm,
            "socket_categories": socket_idx,
        }

    # ---- armor mods ----
    want_mods = armor_mod_names()
    mod_hashes = {}
    # pass 1: targeted display names (authoritative)
    for h, it in items.items():
        if it.get("redacted"):
            continue
        nm = norm((it.get("displayProperties") or {}).get("name", ""))
        if nm in want_mods and want_mods[nm] not in mod_hashes:
            mod_hashes[want_mods[nm]] = int(h)
    # pass 2: sweep every armor mod plug by display name, fill anything missing
    # (this catches element Siphon/Surge/Resistance and ammo finder/scavenger)
    for h, it in items.items():
        if it.get("itemType") != 19 or it.get("redacted"):  # 19 = Mod
            continue
        pci = ((it.get("plug") or {}).get("plugCategoryIdentifier") or "")
        if not pci.startswith("enhancements"):
            continue
        nm = (it.get("displayProperties") or {}).get("name", "")
        if nm and nm not in mod_hashes:
            mod_hashes[nm] = int(h)

    # ---- stats ----
    stat_hashes = {}
    for h, st in stats.items():
        nm = (st.get("displayProperties") or {}).get("name", "")
        if nm in STAT_NAMES and nm not in stat_hashes:
            stat_hashes[nm] = int(h)

    # ---- aspect fragment slots ----
    # An Aspect's number of Fragment slots is carried in its investmentStats as
    # an energy-capacity value (1-3). We resolve stat names to find it.
    cap_stat_hashes = {h for h, st in stats.items()
                       if "energy capacity" in
                       ((st.get("displayProperties") or {}).get("name", "").lower())}
    aspect_frag_slots = {}
    for h, it in items.items():
        pc = ((it.get("plug") or {}).get("plugCategoryIdentifier") or "")
        if "aspects" not in pc:
            continue
        nm = (it.get("displayProperties") or {}).get("name", "")
        cap = None
        for s in it.get("investmentStats", []):
            if str(s.get("statTypeHash")) in cap_stat_hashes:
                cap = s.get("value")
        if cap is None:
            # fallback: aspects expose capacity via energyCapacity on some dumps
            cap = (it.get("plug") or {}).get("energyCapacity")
        if nm and cap is not None:
            aspect_frag_slots[nm] = int(cap)

    # ---- exotic class items (Prismatic only) and their two perk columns ----
    plugsets = get_json(BASE + paths["DestinyPlugSetDefinition"])

    def plug_spirits(plugset_hash):
        ps = plugsets.get(str(plugset_hash)) or {}
        out = []
        for ip in ps.get("reusablePlugItems", []):
            d = items.get(str(ip.get("plugItemHash")))
            if not d:
                continue
            dp = d.get("displayProperties") or {}
            n = dp.get("name", "")
            if n and n.lower().startswith("spirit of"):
                out.append({"spirit": n, "source": "",
                            "effect": (dp.get("description") or "").strip(),
                            "tags": []})
        return out

    SLOT_BY_CLASS = {"Hunter": "Cloak", "Titan": "Mark", "Warlock": "Band"}
    exotic_class_items = {}
    for h, it in items.items():
        if it.get("itemType") != 2:  # Armor
            continue
        if (it.get("inventory") or {}).get("tierType") != 6:  # Exotic
            continue
        # class-item slot: match the bucket rather than itemSubType (which is 30,
        # not 28; 28 is ChestArmor). Bucket is the reliable signal.
        if (it.get("inventory") or {}).get("bucketTypeHash") != CLASS_ITEM_BUCKET:
            continue
        cls = CLASS.get(it.get("classType"))
        nm = (it.get("displayProperties") or {}).get("name", "")
        if not cls or not nm:
            continue
        cols = []
        for sock in (it.get("sockets") or {}).get("socketEntries", []):
            # exotic class items roll random perks, so options live in the
            # randomized plug set; fall back to reusable for safety.
            psh = sock.get("randomizedPlugSetHash") or sock.get("reusablePlugSetHash")
            if psh:
                spirits = plug_spirits(psh)
                if spirits:
                    cols.append(spirits)
        if cols:
            entry = {"name": nm, "slot": SLOT_BY_CLASS.get(cls, ""), "hash": int(h)}
            if len(cols) >= 1:
                entry["col1"] = cols[0]
            if len(cols) >= 2:
                entry["col2"] = cols[1]
            exotic_class_items[cls] = entry

    if not exotic_class_items:
        # Exotic class item spirit perks are not reliably exposed in the
        # manifest's standard plug sockets, so the app ships a verified static
        # dataset for these instead. This puller leaves the field empty.
        pass

    out = {
        "subclasses": subclasses,
        "armor_mod_hashes": mod_hashes,
        "stat_hashes": stat_hashes,
        "aspect_frag_slots": aspect_frag_slots,
        "exotic_class_items": exotic_class_items,
        "socket_layout_note": (
            "Standard 3.0 subclass socket indices: 0 super, 1 class ability, "
            "2 movement, 3 melee, 4 grenade, 5-6 aspects, 7-10 fragments. "
            "socket_categories above is the raw category-hash per index for "
            "verification."),
    }
    with open(os.path.join(HERE, "dim_refs.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("subclasses:", len(subclasses))
    print("armor mods matched:", len(mod_hashes), "of", len(want_mods))
    print("stats matched:", len(stat_hashes), "of", len(STAT_NAMES), stat_hashes)
    print("aspect frag slots:", len(aspect_frag_slots))
    print("exotic class items:", {k: v["name"] for k, v in exotic_class_items.items()})
    print("Wrote dim_refs.json")


if __name__ == "__main__":
    main()
