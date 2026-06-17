"""bungie_pull_aspect_frags.py - complete aspect fragment-slot table from the manifest."""
import json, os, requests
from collections import Counter
API_KEY = os.environ.get("BUNGIE_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY}
BASE = "https://www.bungie.net"
HERE = os.path.dirname(os.path.abspath(__file__))
def get(u):
    r = requests.get(u, headers=HEADERS, timeout=120); r.raise_for_status(); return r.json()
def main():
    if not API_KEY:
        raise SystemExit("set BUNGIE_API_KEY first")
    man = get(BASE + "/Platform/Destiny2/Manifest/")["Response"]
    path = man["jsonWorldComponentContentPaths"]["en"]["DestinyInventoryItemDefinition"]
    print("downloading ...")
    items = get(BASE + path)
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
        # only real fragment-slot counts; ignore stale or zero plug entries
        if not (1 <= cap <= 3):
            continue
        caps.setdefault(nm, set()).add(cap)
    # an aspect has a separate plug per subclass it lives on (native element and,
    # if applicable, Prismatic), each with its own capacity. Prismatic is never lower
    # than native, so min is the native count and max is the Prismatic count. aspects
    # on a single subclass collapse to native == prism.
    out = {nm: {"native": min(cs), "prism": max(cs)} for nm, cs in caps.items()}
    with open(os.path.join(HERE, "data", "aspect_frag_slots.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("wrote %d aspects" % len(out), "| distribution:", dict(Counter(out.values())))
if __name__ == "__main__":
    main()
