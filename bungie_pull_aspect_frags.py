"""bungie_pull_aspect_frags.py - complete aspect fragment-slot table from the manifest.

Each aspect has a separate plug per subclass it lives on (its base element and, if
applicable, Prismatic), and each plug carries its own fragment-slot capacity. This reads
the plug's subclass from plugCategoryIdentifier (e.g. "hunter.prismatic.aspects") and
assigns its capacity to the native (base subclass) or Prismatic count accordingly, instead
of guessing native=min and prism=max. After Edge of Fate several aspects keep 3 slots on
their base subclass but were cut to 2 on Prismatic, which the old min/max logic inverted.

A self-check at the end compares the pulled table against in-game-verified counts and prints
any aspect that still disagrees, so those (and only those) stay on the app.py override.
"""
import json, os, requests
from collections import Counter
API_KEY = os.environ.get("BUNGIE_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY}
BASE = "https://www.bungie.net"
HERE = os.path.dirname(os.path.abspath(__file__))

# In-game-verified counts (native, prism) for cross-checking the pull. prism is meaningful
# only for Prismatic-available aspects; for base-only aspects prism equals native.
KNOWN = {
    "Ascension": (3, 2), "Consecration": (3, 2), "Threaded Specter": (3, 2),
    "Winter's Shroud": (3, 2), "Gunpowder Gamble": (2, 3), "Weaver's Call": (2, 3),
    "Lightning Surge": (3, 3), "Diamond Lance": (3, 3), "Drengr's Lash": (3, 3),
    "Unbreakable": (3, 3), "Knockout": (2, 2), "Stylish Executioner": (2, 2),
    "Bleak Watcher": (2, 2), "Feed the Void": (2, 2), "Hellion": (2, 2),
    "Shatterdive": (3, 3), "Soul Siphon": (3, 3), "On Your Mark": (3, 3),
    "Weavewalk": (3, 3), "Widow's Silk": (3, 3), "Icarus Dash": (3, 3),
    "Cryoclasm": (3, 3), "Shieldburst": (3, 3), "Bastion": (3, 3), "Frostpulse": (3, 3),
    "Child of the Old Gods": (3, 3), "On the Prowl": (3, 3), "Into the Fray": (2, 2),
    "Flechette Storm": (2, 2), "Banner of War": (2, 2),
}


def get(u):
    r = requests.get(u, headers=HEADERS, timeout=120); r.raise_for_status(); return r.json()


def build_table(items):
    """Pure transform from the manifest item map to {name: {native, prism}}. Separated out
    so the subclass-routing logic can be tested without hitting the network."""
    caps = {}
    for it in items.values():
        pc = (it.get("plug") or {}).get("plugCategoryIdentifier", "")
        if "aspect" not in it.get("itemTypeDisplayName", "").lower() and not any(t in pc for t in ("aspects", "totems")):
            continue
        nm = ((it.get("displayProperties") or {}).get("name") or "").strip()
        if not nm or it.get("redacted"):
            continue
        cap = ((it.get("plug") or {}).get("energyCapacity") or {}).get("capacityValue")
        if cap is None:
            for s in (it.get("investmentStats") or []):
                if s.get("statTypeHash") == 2223994109:
                    cap = s.get("value")
        if cap is None:
            continue
        cap = int(cap)
        if not (1 <= cap <= 3):
            continue
        slot = "prism" if "prismatic" in pc.lower() else "native"
        caps.setdefault(nm, {"native": set(), "prism": set()})[slot].add(cap)
    out = {}
    for nm, d in caps.items():
        nat = max(d["native"]) if d["native"] else None
        pri = max(d["prism"]) if d["prism"] else None
        if nat is None:
            nat = pri   # only a Prismatic plug was found
        if pri is None:
            pri = nat   # base-only aspect, no Prismatic plug
        out[nm] = {"native": nat, "prism": pri}
    return out


def validate(out):
    """Compare the pulled table against in-game-verified counts; return list of mismatches."""
    bad = []
    for nm, (kn, kp) in sorted(KNOWN.items()):
        v = out.get(nm)
        if not v:
            bad.append("%s: MISSING from pull" % nm)
        elif (v["native"], v["prism"]) != (kn, kp):
            bad.append("%s: pulled %s/%s, verified %s/%s" % (nm, v["native"], v["prism"], kn, kp))
    return bad


def main():
    if not API_KEY:
        raise SystemExit("set BUNGIE_API_KEY first")
    man = get(BASE + "/Platform/Destiny2/Manifest/")["Response"]
    path = man["jsonWorldComponentContentPaths"]["en"]["DestinyInventoryItemDefinition"]
    print("downloading ...")
    items = get(BASE + path)
    out = build_table(items)
    with open(os.path.join(HERE, "data", "aspect_frag_slots.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("wrote %d aspects" % len(out),
          "| distribution:", dict(Counter((v["native"], v["prism"]) for v in out.values())))
    bad = validate(out)
    if bad:
        print("\nVALIDATION: %d aspect(s) disagree with in-game truth. Keep these on the app.py override:" % len(bad))
        for b in bad:
            print("  " + b)
    else:
        print("\nVALIDATION: all %d known aspects match in-game truth. The app.py _FRAG_OVERRIDE is now redundant." % len(KNOWN))


if __name__ == "__main__":
    main()
