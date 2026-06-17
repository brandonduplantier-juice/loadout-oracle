"""Build data/exotic_abilities.json: exotic -> {slot: signature ability}.

v2: exotic effect text lives in the INTRINSIC SOCKET plug, not the item description
or perks (those are empty/flavor for exotics). This resolves the intrinsic plug and
filters the "No mod currently selected" placeholder. It MERGES with the existing
hand-verified file, never dropping a verified entry.

Conservative: links a slot only when exactly ONE ability of that slot is named.
Needs env BUNGIE_API_KEY. Run from repo root:
  python exotic_link2.py                  # refresh exotic_effects.json cache + print
                                          # discovered links. Does NOT touch
                                          # exotic_abilities.json (safe for a cache refresh).
  python exotic_link2.py --write-abilities  # also merge + write exotic_abilities.json,
                                          # then review the printed additions before committing.
"""
import json, os, re, sys, urllib.request

POOL = os.path.join("data", "pool.json")
OUT = os.path.join("data", "exotic_abilities.json")
CACHE = os.path.join("data", "exotic_effects.json")

def norm(s): return re.sub(r"[^a-z0-9]", "", s.lower())

def _namekey(s): return s.replace("\u2019", "'").strip().lower()  # curly->straight apostrophe, case-insensitive

def _get(url, key=None):
    h = {"X-API-Key": key} if key else {}
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=600))

def effects(exotic_names):
    if os.path.exists(CACHE):
        print("using cached", CACHE)
        return json.load(open(CACHE, encoding="utf-8"))
    key = os.environ.get("BUNGIE_API_KEY")
    if not key: sys.exit("BUNGIE_API_KEY not set")
    print("downloading manifest items + perks (one-time)...")
    man = _get("https://www.bungie.net/Platform/Destiny2/Manifest/", key)
    p = man["Response"]["jsonWorldComponentContentPaths"]["en"]
    items = _get("https://www.bungie.net" + p["DestinyInventoryItemDefinition"])
    perks = _get("https://www.bungie.net" + p["DestinySandboxPerkDefinition"])
    by_name = {}
    for h, it in items.items():
        nm = ((it.get("displayProperties") or {}).get("name") or "")
        if nm: by_name.setdefault(_namekey(nm), []).append(it)
    def intrinsic_text(it):
        parts = []
        for se in ((it.get("sockets") or {}).get("socketEntries") or []):
            ph = se.get("singleInitialItemHash")
            plug = items.get(str(ph)) or items.get(ph) or {}
            pcat = ((plug.get("plug") or {}).get("plugCategoryIdentifier") or "").lower()
            tdn = (plug.get("itemTypeDisplayName") or "").lower()
            if "intrinsic" in pcat or tdn == "intrinsic":
                d = ((plug.get("displayProperties") or {}).get("description") or "").strip()
                if d and "no mod currently" not in d.lower():
                    parts.append(d)
        for pk in (it.get("perks") or []):
            pd = perks.get(str(pk.get("perkHash"))) or {}
            d = ((pd.get("displayProperties") or {}).get("description") or "").strip()
            if d and d not in parts: parts.append(d)
        return " ".join(parts).strip()
    eff = {}
    for nm in exotic_names:
        best = ""
        for it in by_name.get(_namekey(nm), []):
            t = intrinsic_text(it)
            if len(t) > len(best): best = t
        if best: eff[nm] = best
    json.dump(eff, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    print("cached %d exotic effect texts -> %s" % (len(eff), CACHE))
    return eff

GENERIC = {"grenade", "super", "melee"}
def core(name): return re.sub(r"\b(grenade|super)\b", "", name, flags=re.I).strip()

def main():
    pool = [it for it in json.load(open(POOL, encoding="utf-8")) if isinstance(it, dict)]
    exotics = [it for it in pool if it.get("category") in ("Exotic Armor", "Exotic Weapon")]
    eff = effects([e["name"] for e in exotics])
    abilities = {s: [(it["name"], it.get("class", "Any")) for it in pool if it.get("category") == s]
                 for s in ("Grenade", "Melee", "Super")}
    existing = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    discovered, no_text = {}, 0
    for ex in exotics:
        nm = ex["name"]; txt = eff.get(nm)
        if not txt: no_text += 1; continue
        n = " " + norm(txt) + " "; links = {}
        for slot, abil in abilities.items():
            hits = sorted({a for a, ac in abil if ac in ("Any", ex.get("class", "Any")) and
                           (norm(a) in n or (len(norm(core(a))) >= 6 and norm(core(a)) not in GENERIC and norm(core(a)) in n))})
            if len(hits) == 1: links[slot] = hits[0]
        if links: discovered[nm] = links
    merged = dict(discovered); merged.update(existing)   # verified seed always wins
    new = {k: v for k, v in discovered.items() if k not in existing}
    write_abilities = "--write-abilities" in sys.argv
    if write_abilities:
        json.dump(merged, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("exotics with text: %d | no text: %d" % (len(exotics) - no_text, no_text))
    if write_abilities:
        print("verified (kept): %d | newly discovered: %d | total written: %d -> %s"
              % (len(existing), len(new), len(merged), OUT))
    else:
        print("cache refreshed; %s NOT modified (pass --write-abilities to regenerate it)" % OUT)
    print("--- discovered links (review before any commit) ---")
    for k, v in sorted(new.items()): print("  %-30s %s" % (k, v))

if __name__ == "__main__":
    main()
