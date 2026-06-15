"""
bungie_pull_mod_costs.py

Pulls the REAL energy cost of each armor mod from the Bungie manifest and writes
data/mod_costs.json  ->  { "<mods_stats mod key>": <energy cost int>, ... }

Why this exists: DIM enforces a mod's manifest energy cost against the armor's
energy budget (10 for most pieces, 11 for Tier 4/5 under Armor 3.0). If the app's
stored costs are below the live values, the solver packs more mods into a slot
than will physically fit and DIM rejects the loadout with "none of the allowed
items could accommodate these mods". Reading costs straight from the manifest
keeps the app aligned with DIM through any future Bungie rebalance: just re-run
this puller and redeploy.

The app (app.py) loads data/mod_costs.json at startup and overrides the static
costs in mods_stats.json with these values, falling back to the static value for
any mod this puller could not resolve.

USAGE (run on a machine with internet, from the repo root or the data folder):
    Windows:  set BUNGIE_API_KEY=your_key_here
    mac/linux: export BUNGIE_API_KEY=your_key_here
    pip install requests
    python bungie_pull_mod_costs.py
Then commit data/mod_costs.json and redeploy.
"""
import json
import os
import re

import requests

API_KEY = os.environ.get("BUNGIE_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY}
BASE = "https://www.bungie.net"
HERE = os.path.dirname(os.path.abspath(__file__))


def data_path(name):
    """Find a data file whether the script runs from the repo root or data/."""
    for c in (os.path.join(HERE, name), os.path.join(HERE, "data", name)):
        if os.path.exists(c):
            return c
    return os.path.join(HERE, "data", name)


def norm(s):
    s = str(s or "").strip().lower()
    for a in ["\u2019", "\u2018", "\u02bc", "`"]:
        s = s.replace(a, "'")
    return re.sub(r"\s+", " ", s)


# Concrete manifest names used to resolve the app's templated and element mod
# keys. Element and weapon variants of a mod share one energy cost, and the
# Harmonic (subclass-matching) variant is the cheapest, so it is listed first as
# the value the app should prefer. Each key maps to a list of candidate manifest
# names; the first that resolves wins. Keyed by the exact string in mods_stats.json.
ALIAS = {
    "<Element> Siphon": ["Solar Siphon", "Arc Siphon", "Void Siphon"],
    "<Element> Surge": ["Solar Weapon Surge", "Arc Weapon Surge", "Void Weapon Surge",
                         "Solar Surge", "Kinetic Weapon Surge"],
    "<Element> Resistance": ["Solar Resistance", "Arc Resistance", "Void Resistance"],
    "<Weapon> Targeting": ["Harmonic Targeting", "Arc Targeting", "Solar Targeting"],
    "<Weapon> Loader": ["Harmonic Loader", "Arc Loader", "Solar Loader"],
    "<Weapon> Unflinching": ["Unflinching Harmonic Aim", "Unflinching Arc Aim",
                             "Unflinching Solar Aim"],
    "Charge Up": ["Charged Up", "Charge Up"],
    "Heavy Ammo Scavenger": ["Heavy Ammo Scavenger", "Harmonic Scavenger"],
}


def get_json(u):
    r = requests.get(u, headers=HEADERS, timeout=300)
    r.raise_for_status()
    return r.json()


def main():
    if not API_KEY:
        raise SystemExit("Set BUNGIE_API_KEY in your environment first.")

    refs = json.load(open(data_path("dim_refs.json"), encoding="utf-8"))
    mod_hashes = refs.get("armor_mod_hashes", {})
    ms = json.load(open(data_path("mods_stats.json"), encoding="utf-8"))
    armor_mods = ms["armor_mods"]

    keys = []
    for slot_mods in armor_mods.values():
        for m in slot_mods:
            keys.append(m["mod"])

    print("Fetching manifest...")
    comp = get_json(BASE + "/Platform/Destiny2/Manifest/")
    paths = comp["Response"]["jsonWorldComponentContentPaths"]["en"]
    items = get_json(BASE + paths["DestinyInventoryItemDefinition"])
    print("  items:", len(items))

    def energy_of(it):
        return ((it.get("plug") or {}).get("energyCost") or {}).get("energyCost")

    cost_by_hash = {}
    cost_by_name = {}
    v2_copies = {}  # (category, display name) -> [costs across non-redacted copies]
    for h, it in items.items():
        ec = energy_of(it)
        if ec is None or it.get("redacted"):
            continue
        cost_by_hash[int(h)] = ec
        nm = norm((it.get("displayProperties") or {}).get("name", ""))
        if not nm:
            continue
        if nm not in cost_by_name:
            cost_by_name[nm] = ec
        cat = (it.get("plug") or {}).get("plugCategoryIdentifier", "")
        if cat.startswith("enhancements.v2"):
            disp = (it.get("displayProperties") or {}).get("name", "")
            if disp:
                v2_copies.setdefault((cat, disp), []).append(ec)

    out = {}
    unresolved = []
    for key in keys:
        cost = None
        if key in mod_hashes and int(mod_hashes[key]) in cost_by_hash:
            cost = cost_by_hash[int(mod_hashes[key])]
        if cost is None and key in ALIAS:
            for cand in ALIAS[key]:
                cost = cost_by_name.get(norm(cand))
                if cost is not None:
                    break
        if cost is None:
            cost = cost_by_name.get(norm(key))
        if cost is None:
            unresolved.append(key)
        else:
            out[key] = cost

    data_dir = os.path.dirname(data_path("mods_stats.json"))
    out_path = os.path.join(data_dir, "mod_costs.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # Full official list of armor mods (the v2 slot categories only, so no Ghost or
    # dead seasonal mods) with energy cost. The manifest keeps duplicate copies of a
    # mod, so for each name we trust the cost from our known hash when we have one,
    # otherwise the modal (most common) cost across non-redacted copies, which drops
    # the stray low-cost duplicate that made an earlier pass read everything as 1.
    refs_hash_by_name = {norm(k): int(v) for k, v in mod_hashes.items()}
    full = {}
    for (cat, disp), costs in v2_copies.items():
        nm = norm(disp)
        if nm in refs_hash_by_name and refs_hash_by_name[nm] in cost_by_hash:
            c = cost_by_hash[refs_hash_by_name[nm]]
        else:
            c = max(set(costs), key=costs.count)
        full.setdefault(cat, {})[disp] = c

    full_path = os.path.join(data_dir, "all_mod_costs.json")
    full_sorted = {cat: dict(sorted(mods.items())) for cat, mods in sorted(full.items())}
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(full_sorted, f, ensure_ascii=False, indent=1)
    total = sum(len(m) for m in full.values())

    print("resolved", len(out), "of", len(keys), "mod costs")
    if unresolved:
        print("unresolved (app keeps static fallback):", ", ".join(unresolved))
    print("Wrote", out_path)
    print("Wrote", full_path, "with", total, "armor mods across", len(full), "categories")
    for k in keys:
        if k in out:
            print("  %-26s %s" % (k, out[k]))


if __name__ == "__main__":
    main()
