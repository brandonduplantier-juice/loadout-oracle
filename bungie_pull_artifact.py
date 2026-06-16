"""
bungie_pull_artifact.py

Dumps the current seasonal artifact's real structure from the Bungie manifest so
the app's artifact logic can be wired to live data instead of community guesses.

For the named artifact (default "Implement of Curiosity") it writes
data/artifact_raw.json with, for each tier the manifest defines, the tier title,
its unlock requirement, and every mod in it (name, energy or point cost if any,
and description). It also prints a readable summary so you can confirm the tier
layout (the 2 / 3 / 2 slot structure) before we encode the selection rules.

USAGE (run with internet, from the repo root or the data folder):
    Windows:   set BUNGIE_API_KEY=your_key_here
    mac/linux: export BUNGIE_API_KEY=your_key_here
    pip install requests
    python bungie_pull_artifact.py
    (optional) python bungie_pull_artifact.py "Exact Artifact Name"
Then paste the printed summary back and we map it into the app.
"""
import json
import os
import re
import sys

import requests

API_KEY = os.environ.get("BUNGIE_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY}
BASE = "https://www.bungie.net"
HERE = os.path.dirname(os.path.abspath(__file__))
WANT = sys.argv[1] if len(sys.argv) > 1 else "Implement of Curiosity"


def data_dir():
    for c in (os.path.join(HERE, "data"), HERE):
        if os.path.isdir(c) and os.path.exists(os.path.join(c, "mods_stats.json")):
            return c
    return os.path.join(HERE, "data")


def norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def get_json(u):
    r = requests.get(u, headers=HEADERS, timeout=300)
    r.raise_for_status()
    return r.json()


def main():
    if not API_KEY:
        raise SystemExit("Set BUNGIE_API_KEY in your environment first.")

    print("Fetching manifest...")
    comp = get_json(BASE + "/Platform/Destiny2/Manifest/")
    paths = comp["Response"]["jsonWorldComponentContentPaths"]["en"]
    artifacts = get_json(BASE + paths["DestinyArtifactDefinition"])
    items = get_json(BASE + paths["DestinyInventoryItemDefinition"])
    perks = get_json(BASE + paths["DestinySandboxPerkDefinition"])
    print("  artifacts:", len(artifacts), " items:", len(items), " perks:", len(perks))

    def item_name(h):
        it = items.get(str(h))
        return ((it or {}).get("displayProperties") or {}).get("name", "") if it else ""

    def item_desc(h):
        it = items.get(str(h)) or {}
        d = (it.get("displayProperties") or {}).get("description", "")
        if d:
            return d
        # artifact perk text usually lives on the sandbox perk, not the item
        parts = []
        for p in it.get("perks", []):
            pk = perks.get(str(p.get("perkHash")))
            pd = ((pk or {}).get("displayProperties") or {}).get("description", "")
            if pd:
                parts.append(pd)
        return " ".join(parts)

    def energy(h):
        it = items.get(str(h)) or {}
        return ((it.get("plug") or {}).get("energyCost") or {}).get("energyCost")

    # find the wanted artifact, else list what is available
    chosen = None
    names = []
    for h, art in artifacts.items():
        nm = (art.get("displayProperties") or {}).get("name", "")
        names.append(nm)
        if norm(nm) == norm(WANT):
            chosen = (h, art)
    if not chosen:
        print("Could not find artifact named:", WANT)
        print("Available artifacts:", ", ".join(n for n in names if n))
        raise SystemExit(1)

    h, art = chosen
    out = {"name": (art.get("displayProperties") or {}).get("name", ""),
           "hash": int(h), "tiers": []}
    print("\nArtifact:", out["name"], "(hash", h, ")")
    for ti, tier in enumerate(art.get("tiers", [])):
        title = tier.get("displayTitle", "") or str(ti + 1)
        req = tier.get("progressRequirement", tier.get("pointCost", ""))
        mods = []
        for it in tier.get("items", []):
            ih = it.get("itemHash")
            mods.append({"name": item_name(ih), "cost": energy(ih),
                         "desc": item_desc(ih), "hash": ih})
        out["tiers"].append({"index": ti, "title": title, "unlock_requirement": req,
                             "mods": mods})
        print("\n  Tier %s (unlock req %s) -- %d mods:" % (title, req, len(mods)))
        for m in mods:
            d = (m["desc"][:80] + "...") if m["desc"] and len(m["desc"]) > 80 else m["desc"]
            print("     %-30s %s" % (m["name"], d))

    path = os.path.join(data_dir(), "artifact_raw.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nWrote", path, "-- paste the summary above back and we map the 2/3/2 tiers.")


if __name__ == "__main__":
    main()
