
"""bungie_pull_exotic_verbs.py v2 - gathers all socket text (weapons hide mechanics there), saves desc."""

import json, os, re, requests

API_KEY = os.environ.get("BUNGIE_API_KEY", "")

HEADERS = {"X-API-Key": API_KEY}

BASE = "https://www.bungie.net"

HERE = os.path.dirname(os.path.abspath(__file__))

DAMAGE_ENUM = {1: "Kinetic", 2: "Arc", 3: "Solar", 4: "Void", 6: "Stasis", 7: "Strand"}

VERB_PATTERNS = [("Jolt", r"jolt|chain lightning"), ("Volatile", r"volatile"),

 ("Scorch", r"scorch|ignit|ablaze"), ("Slow", r"\bslow|duskfield"), ("Freeze", r"frozen|freez|stasis crystal"),

 ("Weaken", r"weaken|suppress"), ("Sever", r"sever"), ("Suspend", r"suspend"), ("Unravel", r"unravel"),

 ("Threadling", r"threadling"), ("Tangle", r"tangle"), ("Devour", r"devour"), ("Frost Armor", r"frost armor"),

 ("Radiant", r"radiant"), ("Amplified", r"amplif"), ("Woven Mail", r"woven mail"), ("Void Overshield", r"overshield"),

 ("Restoration", r"restoration|\bcure"), ("Void Breach", r"void breach"),

 ("Stasis Shard", r"stasis shard|stasis crystal"), ("Ionic Trace", r"ionic trace"),

 ("Orbs", r"orb of power"), ("Armor Charge", r"armor charge"),

 ("Ability Energy", r"ability energy|melee energy|class ability energy|grenade energy|energy to your"),

 ("Healing", r"\bcure\b|restore.*health"), ("Transcendence", r"transcend"), ("Empower", r"empower"),

 ("Bolt Charge", r"bolt charge")]

CONS_HINTS = ["while you have", "while you are", "while ", "picking up", "collecting", "consume",

 "against", "affected by", "debuffed", "shattering"]

def derive_econ(text):

    d = " " + (text or "").lower() + " "; prod, cons = set(), set()

    for verb, pat in VERB_PATTERNS:

        m = re.search(pat, d)

        if not m:

            continue

        pre = d[max(0, m.start() - 32):m.start()]

        (cons if any(h in pre for h in CONS_HINTS) else prod).add(verb)

    return sorted(prod), sorted(cons)

def get(url):

    r = requests.get(url, headers=HEADERS, timeout=120); r.raise_for_status(); return r.json()

def main():

    if not API_KEY:

        raise SystemExit("set BUNGIE_API_KEY first")

    man = get(BASE + "/Platform/Destiny2/Manifest/")["Response"]

    path = man["jsonWorldComponentContentPaths"]["en"]["DestinyInventoryItemDefinition"]

    print("downloading item definitions ...")

    by_hash = {int(h): v for h, v in get(BASE + path).items()}

    out = {}

    for it in by_hash.values():

        if (it.get("inventory") or {}).get("tierTypeName") != "Exotic":

            continue

        itype = it.get("itemType")

        if itype not in (2, 3):

            continue

        dp = it.get("displayProperties") or {}

        name = (dp.get("name") or "").strip()

        if not name or it.get("redacted"):

            continue

        texts = [dp.get("description", "")]

        for s in ((it.get("sockets") or {}).get("socketEntries") or []):

            for h in ([s.get("singleInitialItemHash")] +

                      [rp.get("plugItemHash") for rp in (s.get("reusablePlugItems") or [])]):

                pl = by_hash.get(h) if h else None

                if pl:

                    texts.append((pl.get("displayProperties") or {}).get("description", ""))

        desc = " ".join(t for t in texts if t)

        prod, cons = derive_econ(name + " " + desc)

        out[name] = {"prod": prod, "cons": cons,

                     "element": DAMAGE_ENUM.get(it.get("defaultDamageType") or 0, ""),

                     "slot": "weapon" if itype == 3 else "armor", "desc": desc[:400]}

    with open(os.path.join(HERE, "data", "exotic_verbs.json"), "w", encoding="utf-8") as f:

        json.dump(out, f, ensure_ascii=False, indent=1)

    tagged = sum(1 for v in out.values() if v["prod"] or v["cons"])

    print("wrote %d exotics, %d with verbs, to data/exotic_verbs.json" % (len(out), tagged))

if __name__ == "__main__":

    main()

