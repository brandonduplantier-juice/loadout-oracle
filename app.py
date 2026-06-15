"""
Destiny 2 Build Creator - Flask app.

Three-step wizard:
  1. Class, preferred element, main/secondary/optional goal, activity,
     build-around weapon, build-around exotic armor, build-around exotic weapon.
  2. Ability focus, super focus, weapon focus.
  3. Synergy preferences (engine/keyword, damage profile, survivability,
     team role, playstyle).
Then ranks the curated build library by a synergy score.

Scoring model is the one validated against 20000+ simulated builds: class and
the three build-around picks are HARD filters; everything else soft-scores and
nothing is excluded by the soft layer (closest builds always rank to the top).
"""
import json
import os
import re

from flask import (
    Flask, render_template, request, redirect, url_for, session
)

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE, "data", "builds.json"), encoding="utf-8") as f:
    BUILDS = json.load(f)
with open(os.path.join(BASE, "data", "options.json"), encoding="utf-8") as f:
    OPTIONS = json.load(f)
with open(os.path.join(BASE, "data", "icons.json"), encoding="utf-8") as f:
    ICONS = json.load(f)
with open(os.path.join(BASE, "data", "pool.json"), encoding="utf-8") as f:
    POOL = json.load(f)
with open(os.path.join(BASE, "data", "weapons.json"), encoding="utf-8") as f:
    WEAPON_ELEM = json.load(f)
with open(os.path.join(BASE, "data", "weapons_tree.json"), encoding="utf-8") as f:
    WEAPON_TREE = json.load(f)
with open(os.path.join(BASE, "data", "artifacts_all.json"), encoding="utf-8") as f:
    ARTIFACTS = json.load(f)["artifacts"]
with open(os.path.join(BASE, "data", "mods_stats.json"), encoding="utf-8") as f:
    _ms = json.load(f)
    ARMOR_MODS = _ms["armor_mods"]
    STATS = _ms["stats"]
try:
    with open(os.path.join(BASE, "data", "community_priors.json"), encoding="utf-8") as f:
        COMMUNITY_PRIORS = json.load(f)
except FileNotFoundError:
    COMMUNITY_PRIORS = {"item_pop": {}, "class_elem": {}, "n_builds": 0}
with open(os.path.join(BASE, "data", "gear_sets.json"), encoding="utf-8") as f:
    GEAR_SETS = json.load(f)

ICON_BASE = "https://www.bungie.net"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "loadout-oracle-local-key")


def _norm(s):
    import re
    s = str(s or "").strip().lower()
    for a in ["\u2019", "\u2018", "\u02bc", "`"]:
        s = s.replace(a, "'")
    return re.sub(r"\s+", " ", s)


def icon_url(name):
    """Best-effort icon for a component name; strips notes and alternatives."""
    if not name:
        return ""
    n = str(name).split("(")[0].split(" or ")[0].split("/")[0].strip()
    p = ICONS.get(_norm(n))
    return (ICON_BASE + p) if p else ""


def split_items(field):
    """A comma-joined field -> list of (name, icon_url)."""
    out = []
    for piece in str(field or "").split(","):
        piece = piece.strip()
        if piece:
            out.append((piece, icon_url(piece)))
    return out


app.jinja_env.globals["icon_url"] = icon_url
app.jinja_env.globals["split_items"] = split_items
app.jinja_env.globals["ICON_BASE"] = ICON_BASE


# ---- pool-based constructor ----
GOALMAP = {
    "Max Damage": "Damage", "Add Clear": "Add Clear",
    "High Survivability": "Survivability", "Ability Spam": "Ability Regen",
    "Healing": "Healing", "Team Buff": "Team Buff", "Utility": "Utility",
    "Solo": "Survivability",
}
SLOTS = [
    ("Super", 1), ("Aspect", 2), ("Fragment", 4), ("Grenade", 1),
    ("Melee", 1), ("Class Ability", 1), ("Movement", 1),
    ("Exotic Armor", 1), ("Exotic Weapon", 1),
]
CLASSES = ["Hunter", "Titan", "Warlock"]
POOL_ELEMENTS = ["Arc", "Solar", "Void", "Stasis", "Strand", "Prismatic"]

# activity -> extra weighted tags (emphasis the activity rewards)
ACTIVITY_TAGS = {
    "Conquests (GM)": [("Survivability", 2), ("Add Clear", 1)],
    "Raid": [("Damage", 2), ("Team Buff", 1)],
    "Pantheon": [("Damage", 2), ("Team Buff", 1)],
    "Dungeon": [("Survivability", 1), ("Damage", 1)],
    "Onslaught": [("Add Clear", 2), ("Survivability", 1)],
    "Contest of Elders": [("Add Clear", 1), ("Survivability", 1)],
    "The Coil": [("Add Clear", 1), ("Survivability", 1)],
    "Gambit": [("Add Clear", 1), ("Damage", 1)],
    "Fireteam Ops": [("Add Clear", 1)],
    "General PvE": [("Add Clear", 1)],
    "Crucible (PvP)": [("Survivability", 1), ("Mobility", 1)],
    "Trials of Osiris": [("Survivability", 1), ("Mobility", 1)],
    "Iron Banner": [("Survivability", 1), ("Mobility", 1)],
}
# activities that read as endgame for matching the curated builds
ACTIVITY_ENDGAME = {
    "Conquests (GM)", "Raid", "Pantheon", "Dungeon",
    "Contest of Elders", "Trials of Osiris",
}


def weapon_element(name):
    if not name or name == "Any":
        return ""
    return WEAPON_ELEM.get(str(name).strip(), "")


def _taglist(s):
    return [t.strip() for t in str(s or "").split(",") if t.strip() and t.strip() != "-"]


def goal_weights(a):
    w = {}

    def add(tag, val):
        if tag:
            w[tag] = w.get(tag, 0) + val

    for key, val in (("main_goal", 3), ("second_goal", 2), ("optional_goal", 1)):
        g = a.get(key, "Any")
        if g and g != "Any":
            add(GOALMAP.get(g, g), val)
    surv = a.get("survivability", "Any")
    if surv == "High":
        add("Survivability", 2)
    elif surv == "Med":
        add("Survivability", 1)
    for tag, val in {
        "Boss DPS": [("Damage", 2)], "Add Clear": [("Add Clear", 2)],
        "Support": [("Team Buff", 1), ("Healing", 1)], "Sustained": [("Damage", 1)],
    }.get(a.get("damage_profile", "Any"), []):
        add(tag, val)
    for tag, val in {
        "Support": [("Team Buff", 1)], "DPS": [("Damage", 1)], "Solo": [("Survivability", 1)],
    }.get(a.get("team_role", "Any"), []):
        add(tag, val)
    for tag, val in ACTIVITY_TAGS.get(a.get("activity", "Any"), []):
        add(tag, val)
    return w


def item_score(item, w):
    # weighted tier signal: item's tag distribution dotted with the build's goal
    # weights, so a 70/30 Damage-Health item scores differently from pure Damage.
    tw = item.get("tagw") or {}
    s = 0.0
    for tag, val in w.items():
        s += val * tw.get(tag, 0.0) * 3.0
    # legacy binary term keeps behaviour stable when tagw is thin
    tg = _taglist(item["goal_tags"])
    fx = _taglist(item["flex_type"])
    for tag, val in w.items():
        if tag in tg:
            s += val * 0.25
        elif tag in fx:
            s += val * 0.12
    # human element: items that show up in scraped meta builds get a small nudge
    pop = COMMUNITY_PRIORS["item_pop"].get(_norm(item["name"]), 0)
    if pop:
        s += min(pop, 3) * 0.5
    return s


def gated(cat, cls, elem):
    return [p for p in POOL if p["category"] == cat
            and p["class"] in ("Any", cls) and p["element"] in ("Any", elem)]


def find_pool_item(cat, name):
    n = _norm(str(name).split("(")[0].split(" or ")[0].split("/")[0])
    for p in POOL:
        if p["category"] == cat and _norm(p["name"]) == n:
            return p
    return None


def _name_cores(name):
    """Candidate sub-names from a messy curated label, for loose matching."""
    n = _norm(name)
    cores = {n, re.sub(r"\(.*?\)", "", n).strip()}
    for m in re.findall(r"\((.*?)\)", n):
        cores.add(m.strip())
    for sep in ("/", " or ", " + ", ":"):
        for part in n.split(sep):
            cores.add(re.sub(r"\(.*?\)", "", part).strip())
    return {c for c in cores if len(c) >= 5}


def loose_pool_item(cat, name):
    """Resolve a curated name to a pool item when an exact match fails, by
    finding the category item whose name contains a distinctive core word."""
    exact = find_pool_item(cat, name)
    if exact:
        return exact
    cores = _name_cores(name)
    best = None
    for p in POOL:
        if p["category"] != cat:
            continue
        pn = _norm(p["name"])
        if any(core in pn for core in cores):
            if best is None or len(pn) < len(_norm(best["name"])):
                best = p
    return best


# weapon name -> type (Auto Rifle, Hand Cannon, ...) from the weapons tree
WEAPON_TYPE = {}
for _ammo, _types in WEAPON_TREE.items():
    for _wtype, _list in _types.items():
        for _entry in _list:
            WEAPON_TYPE[_norm(_entry[0])] = _wtype


def assemble(cls, elem, a, w):
    build = {}
    total = 0.0
    chosen_aspects = []
    for cat, need in SLOTS:
        n = need
        if cat == "Fragment" and chosen_aspects:
            # fragment count = sum of the chosen aspects' fragment slots
            n = max(1, min(5, sum(int(x.get("frag_slots", 2) or 2)
                                  for x in chosen_aspects)))
        ranked = sorted(gated(cat, cls, elem),
                        key=lambda x: (-item_score(x, w), x["name"]))
        picks = ranked[:n]
        if cat == "Exotic Armor" and a.get("build_exotic_armor", "Any") not in ("Any", None):
            fi = find_pool_item("Exotic Armor", a["build_exotic_armor"])
            if fi and fi["class"] in ("Any", cls):
                picks = [fi]
        if cat == "Exotic Weapon" and a.get("build_exotic_weapon", "Any") not in ("Any", None):
            fi = find_pool_item("Exotic Weapon", a["build_exotic_weapon"])
            if fi:
                picks = [fi]
        scored = [{"item": p, "score": round(item_score(p, w), 1)} for p in picks]
        build[cat] = scored
        if cat == "Aspect":
            chosen_aspects = [x["item"] for x in scored]
        total += sum(x["score"] for x in scored)
    return build, round(total, 1)


def mod_econ(name):
    """What a given armor mod produces and consumes, for loop-aware selection
    and synergy detection."""
    p, c = set(), set()
    if "Siphon" in name:
        p.add("Orbs")
    if "Surge" in name:
        p.add("Empower")
        c.add("Armor Charge")
    if name in ("Recuperation", "Better Already"):
        c.add("Orbs")
    if name in ("Absolution", "Innervation", "Invigoration"):
        c.add("Orbs")
    if "Charge Up" in name:
        p.add("Armor Charge")
    if name in ("Bomber", "Outreach", "Distribution"):
        p.add("Ability Energy")
    if "Kickstart" in name:
        p.add("Ability Energy")
        c.add("Armor Charge")
    if name == "Heavy Handed":
        p.add("Orbs")
    if name in ("Powerful Attraction", "Reaper"):
        c.add("Orbs")
    return p, c


LOOP_WEIGHT = {"Orbs": 3.0, "Ability Energy": 2.5, "Empower": 2.0, "Transcendence": 2.0,
               "Healing": 1.5, "Crowd Control": 1.2, "Damage": 1.0, "Armor Charge": 1.5}


def compute_synergy(build, mods_loadout):
    """Detect closed producer to consumer loops across the assembled build and
    its mod set. This is the 'everything feeding into things' score."""
    prod, cons = {}, {}

    def addp(d, k, name):
        d.setdefault(k, [])
        if name not in d[k]:
            d[k].append(name)

    for cat, picks in build.items():
        for e in picks:
            it = e["item"]
            for k in it.get("prod", []):
                addp(prod, k, it["name"])
            for k in it.get("cons", []):
                addp(cons, k, it["name"])
    for slot, info in (mods_loadout or {}).items():
        for m in info["mods"]:
            nm = m["mod"].split(" x")[0]
            mp, mc = mod_econ(nm)
            for k in mp:
                addp(prod, k, m["mod"])
            for k in mc:
                addp(cons, k, m["mod"])

    loops, score = [], 0.0
    for k in set(list(prod) + list(cons)):
        P = list(prod.get(k, []))
        C = list(cons.get(k, []))
        cross = [n for n in P if n not in C] or [n for n in C if n not in P] or (len(P) > 1)
        if P and C and cross:
            strength = min(len(P), len(C))
            contrib = strength * LOOP_WEIGHT.get(k, 1.0)
            score += contrib
            loops.append({"verb": k, "from": P[:3], "to": C[:3], "w": round(contrib, 1)})
    loops.sort(key=lambda l: -l["w"])
    return {"loops": loops[:6], "score": round(score, 1)}


def classify_community(cls, elem, build):
    """Place the build against how the scraped meta builds are distributed."""
    pri = COMMUNITY_PRIORS
    share = pri["class_elem"].get(cls + "|" + elem, 0)
    dims = {}
    for cat in ("Super", "Aspect", "Grenade", "Melee"):
        for e in build.get(cat, []):
            for t, v in (e["item"].get("tagw") or {}).items():
                dims[t] = dims.get(t, 0) + v
    top = max(dims, key=dims.get) if dims else "Damage"
    arche = {
        "Damage": "weapon and super damage", "Add Clear": "ad-clear and orbs",
        "Ability Regen": "ability spam", "Survivability": "survivability and uptime",
        "Healing": "sustain and support", "Crowd Control": "lockdown and control",
        "Team Buff": "team support", "Utility": "utility", "Mobility": "mobility",
    }.get(top, top)
    note = "This leans into " + arche + "."
    if share:
        note += (" " + cls + " " + elem + " appears in about "
                 + str(int(share * 100)) + "% of the scraped meta builds.")
    return {"archetype": arche, "share": share, "note": note}


def construct(a):
    w = goal_weights(a)
    classes = [a["cls"]] if a.get("cls", "Any") != "Any" else list(CLASSES)
    fa = a.get("build_exotic_armor", "Any")
    if fa not in ("Any", None):
        fi = find_pool_item("Exotic Armor", fa)
        if fi and fi["class"] in CLASSES:
            classes = [fi["class"]]
    elements = [a["element"]] if a.get("element", "Any") != "Any" else list(POOL_ELEMENTS)
    # an explicit weapon pick is a strong element signal: constrain to its
    # element (Prismatic stays allowed since it runs any damage type)
    weap_elem = weapon_element(a.get("build_weapon", "Any"))
    if weap_elem and a.get("element", "Any") == "Any":
        elements = [weap_elem, "Prismatic"]
    best = None
    for c in classes:
        for e in elements:
            b, total = assemble(c, e, a, w)
            bonus = 0.5 if (weap_elem and e == weap_elem) else 0
            if best is None or (total + bonus) > best["rank"]:
                best = {"cls": c, "elem": e, "build": b, "total": total,
                        "rank": total + bonus}
    best["auto_class"] = a.get("cls", "Any") == "Any"
    best["auto_elem"] = a.get("element", "Any") == "Any"
    best["weap_elem"] = weap_elem
    best["slots"] = [s for s, _ in SLOTS]
    best["slots_view"] = [(s, best["build"][s]) for s in best["slots"]]
    best["has_goals"] = bool(w)
    best["armor_mods"] = recommend_armor_mods(best["elem"], a)
    best["weapon_mods"] = recommend_weapon_mods(a)
    best["artifact"] = recommend_artifact(best["elem"], a)
    best["gear_set"] = recommend_gear_set(best["elem"], a)
    best["armor_loadout"] = recommend_armor_loadout(best["elem"], a, best["build"], best["artifact"])
    best["stat_priority"] = stat_priority(a, best["elem"])
    best["weapon_synergy"] = recommend_weapon_synergy(best["elem"], a, best["build"], best["artifact"])
    best["dim_search"] = dim_search_for(best["build"])
    best["synergy"] = compute_synergy(best["build"], best["armor_loadout"])
    best["community"] = classify_community(best["cls"], best["elem"], best["build"])
    return best


# exotic armor available per class, for the class-filtered dropdown
EXOTIC_ARMOR_BY_CLASS = {c: sorted(
    p["name"] for p in POOL if p["category"] == "Exotic Armor" and p["class"] == c
) for c in CLASSES}


def recommend_armor_mods(elem, a):
    """Element economy mods are always correct; arms/class vary by focus."""
    e = elem if elem in ("Arc", "Solar", "Void", "Stasis", "Strand") else None
    mods = []
    if e:
        mods.append(("Helmet", e + " Siphon, Heavy Ammo Finder"))
        mods.append(("Legs", e + " Surge x3"))
        mods.append(("Chest", e + " Resistance"))
    else:
        mods.append(("Helmet", "Harmonic Siphon, Heavy Ammo Finder"))
        mods.append(("Legs", "Harmonic Surge x3"))
        mods.append(("Chest", "Concussive Dampener"))
    af = focus_weight(a.get("ability_focus", "Any"))
    wf = focus_weight(a.get("weapon_focus", "Any"))
    if af and af >= wf:
        mods.append(("Arms", "Grenade Kickstart, Melee Kickstart"))
        mods.append(("Class item", "Bomber, Outreach, Distribution"))
    elif wf:
        mods.append(("Arms", "Loader and Dexterity for your weapon type"))
        mods.append(("Class item", "Powerful Attraction, Reaper"))
    else:
        mods.append(("Arms", "Impact Induction, Momentum Transfer"))
        mods.append(("Class item", "Powerful Attraction, Time Dilation"))
    return mods


def recommend_weapon_mods(a):
    return ["Backup Mag", "Counterbalance Stock or Freehand Grip", "Targeting Adjuster"]


def _build_tags(a):
    """Translate the build's goals and focus into mod-matching tags."""
    tags = set()
    gmap = {
        "Max Damage": ["damage"], "Damage": ["damage"],
        "Add Clear": ["orb", "ammo"], "Crowd Control": ["orb"],
        "High Survivability": ["survivability"], "Survivability": ["survivability"],
        "Healing": ["healing", "survivability"], "Tank": ["survivability"],
        "Ability Spam": ["ability_regen"], "Ability Uptime": ["ability_regen"],
        "Ability Regen": ["ability_regen"], "Grenade": ["grenade", "ability_regen"],
        "Melee": ["melee", "ability_regen"], "Team Buff": ["orb", "ability_regen"],
        "Mobility": ["weapon_handling"], "Utility": ["utility", "orb"],
    }
    for k in ("main_goal", "second_goal", "optional_goal"):
        for t in gmap.get(a.get(k, ""), []):
            tags.add(t)
    if a.get("ability_focus") == "High":
        tags.add("ability_regen")
    if a.get("super_focus") == "High":
        tags.update(["super", "orb"])
    if a.get("weapon_focus") == "High":
        tags.update(["damage", "weapon_handling"])
    if not tags:
        tags.update(["orb", "ability_regen", "survivability", "damage"])
    return tags


DIM2MOD = {
    "Damage": ["damage"], "Add Clear": ["orb", "ammo"], "Ability Regen": ["ability_regen"],
    "Survivability": ["survivability"], "Healing": ["healing", "survivability"],
    "Crowd Control": ["orb"], "Team Buff": [], "Utility": [], "Mobility": [],
}

# exotics whose mechanic is specific enough to force a matching mod into a slot
EXOTIC_OVERRIDES = {
    # melee-damage exotics -> Heavy Handed (orbs on melee) and a melee regen mod
    "Wormgod Caress": {"Arms": ["Heavy Handed", "Melee Kickstart"],
                       "note": "Wormgod stacks melee damage, so keep meleeing"},
    "Synthoceps": {"Arms": ["Heavy Handed"],
                   "note": "Synthoceps empowers melee and super when surrounded"},
    "Liar's Handshake": {"Arms": ["Heavy Handed", "Momentum Transfer"],
                         "note": "Liar's Handshake rewards the melee counter-punch loop"},
    "Karnstein Armlets": {"Arms": ["Heavy Handed"],
                          "note": "Karnstein melee kills heal you"},
    "Necrotic Grip": {"Arms": ["Heavy Handed"],
                      "note": "Necrotic spreads poison from melee"},
    "Caliban's Hand": {"Arms": ["Heavy Handed"],
                       "note": "Caliban ignites from melee"},
    # grenade exotics -> grenade regen mods
    "Contraverse Hold": {"Arms": ["Grenade Kickstart", "Impact Induction"],
                         "note": "Contraverse refunds grenade energy on hits"},
    "Sunbracers": {"Arms": ["Grenade Kickstart", "Impact Induction"],
                   "note": "Sunbracers turn a melee kill into grenade spam"},
    "Nothing Manacles": {"Arms": ["Grenade Kickstart"],
                         "note": "Nothing Manacles track scatter grenades"},
    "Verity's Brow": {"Arms": ["Grenade Kickstart"],
                      "note": "Verity's Brow charges grenades from ability kills"},
    # super exotics -> Hands-On on the helmet
    "Celestial Nighthawk": {"Helmet": ["Hands-On"],
                            "note": "Celestial turns Golden Gun into one big shot"},
    "Cuirass of the Falling Star": {"Helmet": ["Hands-On"],
                                    "note": "Cuirass massively boosts Thundercrash"},
    "Star-Eater Scales": {"Helmet": ["Hands-On"],
                          "note": "Star-Eater overcharges your super from orbs"},
    # three-ability empower loop
    "Heart of Inmost Light": {"Arms": ["Grenade Kickstart", "Melee Kickstart"],
                              "Class Item": ["Bomber", "Outreach"],
                              "note": "Inmost Light empowers the other two abilities each cast"},
}


def _slot_note(elem, names):
    bits = []
    for raw in names:
        n = raw.split(" x")[0]
        if "Siphon" in n:
            bits.append("makes orbs from your " + elem + " kills")
        elif "Surge" in n:
            bits.append("boosts your " + elem + " weapon damage")
        elif "Resistance" in n and "Sniper" not in n:
            bits.append("cuts incoming " + elem + " damage")
        elif n in ("Recuperation", "Better Already"):
            bits.append("heals you from the orbs the build makes")
        elif n in ("Absolution", "Innervation", "Invigoration"):
            bits.append("turns those orbs into ability energy")
        elif "Kickstart" in n:
            bits.append("refunds grenade and melee energy")
        elif n == "Heavy Handed":
            bits.append("your melee kills make orbs")
        elif n in ("Bomber", "Outreach", "Distribution"):
            bits.append("class ability feeds ability regen")
        elif "Loader" in n or "Targeting" in n or "Scavenger" in n:
            bits.append("supports your weapon damage phase")
    out = []
    for b in bits:
        if b not in out:
            out.append(b)
    return "; ".join(out[:2])


def recommend_armor_loadout(elem, a, build=None, artifact=None):
    """Build-aware mod selection. Reads the assembled build's fragments, aspects,
    exotics, super, grenade and melee (their produced and consumed verbs and
    dominant tags), the artifact, and weapon focus, then picks mods that close
    the build's loops. Distinct mods only; element anchors stack; sits under 10."""
    pref = {}
    for t in _build_tags(a):
        pref[t] = pref.get(t, 0) + 1.0
    build_prod, build_cons = set(), set()
    if build:
        for cat in ("Fragment", "Aspect", "Exotic Armor", "Super", "Grenade", "Melee"):
            for e in build.get(cat, []):
                it = e["item"]
                build_prod |= set(it.get("prod", []))
                build_cons |= set(it.get("cons", []))
                for dim, w in (it.get("tagw") or {}).items():
                    if w >= 0.18:
                        for mt in DIM2MOD.get(dim, []):
                            pref[mt] = pref.get(mt, 0) + min(w, 1.0)
    # the artifact reinforces its element economy; most builds make orbs
    if artifact:
        build_prod.add("Orbs")
    exo_name = ""
    if build and build.get("Exotic Armor"):
        exo_name = build["Exotic Armor"][0]["item"].get("name", "")
    forced = EXOTIC_OVERRIDES.get(exo_name, {})
    el = "Harmonic" if elem in ("Prismatic", "Any", "") else elem
    out = {}
    for slot, mods in ARMOR_MODS.items():
        cands = [m for m in mods
                 if not (m["mod"] == "Harmonic Siphon" and el != "Harmonic")]

        def score(m):
            name = m["mod"].replace("<Element>", el).replace("<Weapon>", "Primary")
            s = sum(pref.get(t, 0) for t in m["tags"]) + (3 if m["elem"] else 0)
            mp, mc = mod_econ(name)
            # loop closing: mod consumes what the build produces, or vice versa
            s += 2.0 * len(mc & build_prod) + 1.5 * len(mp & build_cons)
            return s

        def relevant(m):
            name = m["mod"].replace("<Element>", el).replace("<Weapon>", "Primary")
            mp, mc = mod_econ(name)
            return (m["elem"] or bool(set(m["tags"]) & set(pref))
                    or bool((mc & build_prod) or (mp & build_cons)))

        def cap(m):
            if m["elem"]:
                return {"Legs": 3, "Helmet": 2, "Chest": 2}.get(slot, 1)
            return 1
        ranked = sorted(cands, key=lambda m: (-score(m), m["cost"]))
        budget = 10
        counts = {}
        # pre-seed mods forced by a specific exotic, before the greedy fill
        for fname in forced.get(slot, []):
            m = next((x for x in cands
                      if x["mod"].replace("<Element>", el).replace("<Weapon>", "Primary") == fname),
                     None)
            if m and m["cost"] <= budget:
                counts.setdefault(fname, [m["cost"], m["desc"], 0])[2] += 1
                budget -= m["cost"]
        progress = True
        while budget > 0 and progress:
            progress = False
            for m in ranked:
                if not relevant(m):
                    continue
                name = m["mod"].replace("<Element>", el).replace("<Weapon>", "Primary")
                c = counts.setdefault(name, [m["cost"], m["desc"], 0])
                if c[2] >= cap(m) or m["cost"] > budget:
                    continue
                c[2] += 1
                budget -= m["cost"]
                progress = True
                break
        modlist = [{"mod": n + (" x" + str(v[2]) if v[2] > 1 else ""),
                    "cost": v[0], "desc": v[1]}
                   for n, v in counts.items() if v[2] > 0]
        note = _slot_note(elem, [m["mod"] for m in modlist])
        if forced.get(slot) and forced.get("note"):
            note = forced["note"] + (("; " + note) if note else "")
        out[slot] = {"mods": modlist, "used": 10 - budget, "note": note}
    return out


def stat_priority(a, elem):
    """Order the six Armor 3.0 stats by what this build leans on."""
    goals = [a.get("main_goal"), a.get("second_goal"), a.get("optional_goal")]
    if a.get("super_focus") == "High" or "Max Damage" in goals:
        pri = "Super"
    elif "Grenade" in goals or "Ability Spam" in goals or a.get("ability_focus") == "High":
        pri = "Grenade"
    elif "Melee" in goals:
        pri = "Melee"
    else:
        pri = "Health"
    order = ["Super", "Grenade", "Melee", "Class", "Weapons", "Health"]
    if pri in order:
        order.remove(pri)
        order.insert(0, pri)
    order.remove("Health")
    order.insert(0 if pri == "Health" else 1, "Health")
    return [{"stat": s, "desc": STATS[s]} for s in order]


def dim_search_for(build):
    names = []
    for slot in ("Exotic Weapon", "Exotic Armor"):
        for e in build.get(slot, []):
            nm = (e.get("item") or {}).get("name", "")
            if nm and nm.lower() != "none":
                names.append(nm)
    return " or ".join('name:"' + n + '"' for n in names)


RECOIL_TYPES = {"Auto Rifle", "Submachine Gun", "Pulse Rifle", "Machine Gun"}
HIPFIRE_TYPES = {"Sidearm", "Submachine Gun", "Shotgun"}
AIRBORNE_TYPES = {"Hand Cannon", "Pulse Rifle", "Scout Rifle"}


def recommend_weapon_synergy(elem, a, build=None, artifact=None):
    """Build-aware gun mods. Ties the weapon mod picks to the build-around weapon
    type, the exotic weapon, the damage profile and goals, and the element the
    rest of the build (fragments, Surge, artifact) is amplifying."""
    we = "your subclass element" if elem in ("Prismatic", "Any", "") else elem
    goals = (a.get("main_goal"), a.get("second_goal"), a.get("optional_goal"))
    dps = ("Max Damage" in goals or "Damage" in goals
           or a.get("damage_profile") in ("DPS", "Burst", "Sustained")
           or a.get("team_role") == "DPS"
           or a.get("activity") in ACTIVITY_ENDGAME)
    addclear = "Add Clear" in goals or "Crowd Control" in goals
    bw = a.get("build_weapon", "Any")
    wtype = WEAPON_TYPE.get(_norm(bw)) if bw not in ("Any", None, "") else None
    has_exotic_wpn = bool(build and build.get("Exotic Weapon")
                          and build["Exotic Weapon"][0]["item"].get("name")
                          and "choice" not in build["Exotic Weapon"][0]["item"]["name"].lower())
    # build produces empowering buffs (radiant/surge/volatile/jolt)?
    empower = False
    if build:
        for cat in ("Fragment", "Aspect", "Super", "Melee"):
            for e in build.get(cat, []):
                if "Empower" in e["item"].get("prod", []):
                    empower = True

    mods = []
    # 1) damage spec mod, keyed to what you are shooting
    if dps:
        mods.append({"mod": "Boss Spec", "why": "more damage to bosses and vehicles in your damage phase"})
    elif addclear:
        mods.append({"mod": "Minor Spec", "why": "more damage to red-bar adds for clearing"})
    else:
        mods.append({"mod": "Major Spec", "why": "more damage to the majors you fight most"})
    # 2) uptime mod, stronger case when a DPS exotic anchors the build
    if dps or has_exotic_wpn:
        mods.append({"mod": "Backup Mag", "why": "more rounds before reloading mid damage phase"})
    # 3) handling mod from the actual weapon type
    if wtype in RECOIL_TYPES:
        mods.append({"mod": "Counterbalance Stock", "why": "steadies the recoil on your " + wtype.lower()})
    elif wtype in HIPFIRE_TYPES:
        mods.append({"mod": "Freehand Grip", "why": "improves hip-fire on your " + wtype.lower()})
    elif a.get("weapon_focus") == "High":
        mods.append({"mod": "Targeting Adjuster", "why": "tighter aim assist since weapons are your focus"})
    elif not dps:
        mods.append({"mod": "Counterbalance Stock", "why": "general recoil control"})

    primary = ("A " + we + " Primary so its kills feed your "
               + ("" if we == "your subclass element" else we + " ")
               + "Siphon orbs and stack Surge.")
    heavy = ("Your exotic as the damage weapon, fed by Backup Mag."
             if has_exotic_wpn else
             "A Special or Heavy in your damage element for the damage phase.")
    note = "Champion counters are intrinsic to weapon frames now, so no mod slots go to them."
    if empower:
        note += (" Your build makes empowering buffs, so fire your damage weapon while they are up "
                 "and match its element to your Surge.")
    return {"primary": primary, "heavy": heavy, "mods": mods, "note": note}


# perks worth taking regardless of element (broad utility / champion / economy)
NEUTRAL_GOOD = {
    "Elemental Siphon", "Press The Advantage", "Counter Energy", "Expert Handling",
    "Armorsmith", "Dreadful Finisher", "Sniper's Meditation", "Fierce Proxemics",
    "Precision Equity", "Diviner's Discount", "Overcharged Armory", "Squad Goals",
    "Solo Operative", "Vanguard Surplus Discounts",
}


def _artifact_match(p, elem):
    e = p.get("element", "")
    if elem == "Prismatic":
        return e not in ("", "Kinetic")
    return e == elem


def recommend_artifact(elem, a):
    if not ARTIFACTS:
        return None
    ranked = sorted(
        ARTIFACTS,
        key=lambda art: -sum(1 for p in art["perks"] if _artifact_match(p, elem)),
    )
    art = ranked[0]
    matched = [p for p in art["perks"] if _artifact_match(p, elem)]
    neutral = [p for p in art["perks"] if p["perk"] in NEUTRAL_GOOD]
    picks, seen = [], set()
    for p in matched + neutral + art["perks"]:
        if p["perk"] not in seen:
            seen.add(p["perk"])
            picks.append(p)
        if len(picks) >= 7:
            break
    return {
        "name": art["name"], "source": art["source"], "perks": picks,
        "alts": [x["name"] for x in ranked[1:3]
                 if sum(1 for p in x["perks"] if _artifact_match(p, elem)) > 0],
    }


def _set_bonus(bonuses, count):
    for b in bonuses:
        if int(b.get("count", 0)) == count:
            return b
    return bonuses[0] if bonuses else None


def recommend_gear_set(elem, a):
    if not GEAR_SETS:
        return None
    signal = set()
    if elem and elem != "Prismatic":
        signal.add(elem.lower())
    goalwords = {
        "Max Damage": ["damage", "final blow"],
        "High Survivability": ["heal", "shield", "health", "resist"],
        "Add Clear": ["orb", "combatant", "ammo brick"],
        "Healing": ["heal", "cure", "restoration"],
        "Ability Spam": ["grenade", "melee", "ability", "energy"],
        "Team Buff": ["fireteam", "ally", "allies"],
        "Solo": ["heal", "shield", "resist"],
    }
    for k in ("main_goal", "second_goal", "optional_goal"):
        for wd in goalwords.get(a.get(k, "Any"), []):
            signal.add(wd)
    ranked = []
    for sname, bonuses in GEAR_SETS.items():
        text = " ".join((b["perk"] + " " + b["desc"]).lower() for b in bonuses)
        sc = sum(1 for wd in signal if wd in text)
        ranked.append((sc, sname, bonuses))
    ranked.sort(key=lambda x: -x[0])
    if not ranked or ranked[0][0] == 0:
        return None
    best = ranked[0]
    four = _set_bonus(best[2], 4)
    out = {"name": best[1],
           "four": {"perk": four["perk"], "desc": four["desc"]} if four else None,
           "two_two": []}
    # a valid alternative: two different sets at 2 pieces each
    for sc, sname, bonuses in ranked[:2]:
        two = _set_bonus(bonuses, 2)
        if two:
            out["two_two"].append({"name": sname, "perk": two["perk"], "desc": two["desc"]})
    return out

ELEMENTS = {"Arc", "Solar", "Void", "Stasis", "Strand", "Prismatic"}
SURV_RANK = {"Low": 1, "Med": 2, "High": 3}

# weights, mirrored from the workbook Build Creator
W_ELEMENT = 3
W_MAIN = 4
W_SECOND = 3
W_OPTIONAL = 1
W_ACTIVITY = 2
W_ENGINE = 6
W_DAMAGE = 3
W_SURV = 2
W_ROLE = 2
W_PLAY = 2


def contains(needle, haystack):
    if not needle or needle == "Any":
        return False
    return str(needle).lower() in str(haystack or "").lower()


def focus_weight(rank):
    """Any -> 0, else High/Medium/Low map to 3/2/1."""
    return {"High": 3, "Medium": 2, "Low": 1}.get(rank, 0)


def passes_hard_filters(b, a):
    # Only class is a hard filter now. Build-around picks soft-boost instead,
    # so the curated list stays populated even for pool-only exotics.
    if a.get("cls") and a["cls"] != "Any" and b["class"] != a["cls"]:
        return False
    return True


def score(b, a):
    s = 0
    reasons = []
    if a.get("element") and a["element"] != "Any" and b["element"] == a["element"]:
        s += W_ELEMENT
        reasons.append((W_ELEMENT, a["element"] + " element"))
    if contains(a.get("main_goal"), b.get("goals")):
        s += W_MAIN
        reasons.append((W_MAIN, a["main_goal"]))
    if contains(a.get("second_goal"), b.get("goals")):
        s += W_SECOND
        reasons.append((W_SECOND, a["second_goal"]))
    if contains(a.get("optional_goal"), b.get("goals")):
        s += W_OPTIONAL
        reasons.append((W_OPTIONAL, a["optional_goal"]))
    act = a.get("activity", "Any")
    if act and act != "Any":
        endgame = act in ACTIVITY_ENDGAME
        bact = str(b.get("activity") or "")
        if (endgame and "Endgame" in bact) or (not endgame and "General" in bact):
            s += W_ACTIVITY
            reasons.append((W_ACTIVITY, act))
    if contains(a.get("engine"), b.get("keyword_engine")):
        s += W_ENGINE
        reasons.append((W_ENGINE, a["engine"]))
    if a.get("damage_profile") and a["damage_profile"] != "Any" \
            and b.get("damage_profile") == a["damage_profile"]:
        s += W_DAMAGE
        reasons.append((W_DAMAGE, a["damage_profile"]))
    surv = a.get("survivability")
    if surv and surv != "Any":
        try:
            if int(b.get("survivability") or 0) >= SURV_RANK[surv]:
                s += W_SURV
                reasons.append((W_SURV, surv + " survivability"))
        except (TypeError, ValueError, KeyError):
            pass
    if contains(a.get("team_role"), b.get("team_role")):
        s += W_ROLE
        reasons.append((W_ROLE, a["team_role"] + " role"))
    if contains(a.get("playstyle"), b.get("playstyle")):
        s += W_PLAY
        reasons.append((W_PLAY, a["playstyle"]))
    wa = focus_weight(a.get("ability_focus"))
    ws = focus_weight(a.get("super_focus"))
    ww = focus_weight(a.get("weapon_focus"))
    if wa or ws or ww:
        fb = round((
            int(b.get("ability_focus") or 0) * wa
            + int(b.get("super_focus") or 0) * ws
            + int(b.get("weapon_focus") or 0) * ww
        ) / 2)
        if fb:
            s += fb
            reasons.append((fb, "focus match"))
    ba = a.get("build_exotic_armor", "Any")
    if ba and ba != "Any" and contains(ba.split("(")[0].split(":")[0].strip(), b.get("exotic_armor")):
        s += 8
        reasons.append((8, "exotic armor"))
    be = a.get("build_exotic_weapon", "Any")
    if be and be != "Any" and contains(be.split("(")[0].split("/")[0].strip(), b.get("exotic_weapon")):
        s += 8
        reasons.append((8, "exotic weapon"))
    bw = a.get("build_weapon", "Any")
    if bw and bw != "Any":
        if contains(bw, b.get("legendary_weapons")):
            s += 5
            reasons.append((5, "weapon"))
        we = weapon_element(bw)
        if we and b.get("element") == we:
            s += 2
            reasons.append((2, we + " weapon"))
    reasons.sort(reverse=True)
    return s, reasons


def theme(a):
    e = (a or {}).get("element", "Any")
    return e.lower() if e in ELEMENTS else "default"


@app.route("/")
def index():
    session.clear()
    return render_template("step1.html", o=OPTIONS, a={}, theme="default",
                           weapon_tree=WEAPON_TREE,
                           exotic_armor_by_class=EXOTIC_ARMOR_BY_CLASS)


@app.route("/step1", methods=["POST"])
def step1():
    f = request.form
    session["answers"] = {
        "cls": f.get("cls", "Any"),
        "element": f.get("element", "Any"),
        "main_goal": f.get("main_goal", "Any"),
        "second_goal": f.get("second_goal", "Any"),
        "optional_goal": f.get("optional_goal", "Any"),
        "activity": f.get("activity", "Any"),
        "build_weapon": f.get("build_weapon", "Any").strip() or "Any",
        "build_exotic_armor": f.get("build_exotic_armor", "Any"),
        "build_exotic_weapon": f.get("build_exotic_weapon", "Any"),
    }
    return redirect(url_for("focus"))


@app.route("/focus", methods=["GET", "POST"])
def focus():
    a = session.get("answers", {})
    if request.method == "POST":
        f = request.form
        a["ability_focus"] = f.get("ability_focus", "Any")
        a["super_focus"] = f.get("super_focus", "Any")
        a["weapon_focus"] = f.get("weapon_focus", "Any")
        session["answers"] = a
        return redirect(url_for("synergy"))
    return render_template("step2.html", o=OPTIONS, a=a, theme=theme(a))


@app.route("/synergy", methods=["GET", "POST"])
def synergy():
    a = session.get("answers", {})
    if request.method == "POST":
        f = request.form
        a["engine"] = f.get("engine", "Any")
        a["damage_profile"] = f.get("damage_profile", "Any")
        a["survivability"] = f.get("survivability", "Any")
        a["team_role"] = f.get("team_role", "Any")
        a["playstyle"] = f.get("playstyle", "Any")
        session["answers"] = a
        return redirect(url_for("results"))
    return render_template("step3.html", o=OPTIONS, a=a, theme=theme(a))


CUR_SLOTS = [
    ("Super", "Super", "super"),
    ("Class ability", "Class Ability", "class_ability"),
    ("Aspects", "Aspect", "aspects"),
    ("Fragments", "Fragment", "fragments"),
    ("Grenade", "Grenade", "grenade"),
    ("Melee", "Melee", "melee"),
    ("Exotic armor", "Exotic Armor", "exotic_armor"),
    ("Exotic weapon", "Exotic Weapon", "exotic_weapon"),
    ("Legendaries", "Weapon", "legendary_weapons"),
]


def _stub_item(nm, cat, elem):
    return {"name": nm, "category": cat, "element": elem, "icon": "",
            "tier": "", "origin": None, "prod": [], "cons": []}


def _resolve_items(cat, field, elem):
    out = []
    for nm, _ic in split_items(field):
        p = loose_pool_item(cat, nm)
        out.append({"item": p or _stub_item(nm, cat, elem), "score": 0})
    return out


def _curated_answers(b):
    goals = [g.strip() for g in str(b.get("goals") or "").split(",") if g.strip()]
    fmap = {3: "High", 2: "Medium", 1: "Low"}

    def f(k):
        try:
            return fmap.get(int(b.get(k) or 0), "Any")
        except (TypeError, ValueError):
            return "Any"
    return {
        "cls": b["class"], "element": b["element"],
        "main_goal": goals[0] if goals else "Any",
        "second_goal": goals[1] if len(goals) > 1 else "Any",
        "optional_goal": goals[2] if len(goals) > 2 else "Any",
        "ability_focus": f("ability_focus"), "super_focus": f("super_focus"),
        "weapon_focus": f("weapon_focus"),
        "damage_profile": b.get("damage_profile", "Any"),
        "team_role": b.get("team_role", "Any"),
        "activity": b.get("activity", "Any"),
    }


def enrich_curated(b):
    """Resolve a curated build's items against the enriched pool and regenerate
    the structured mod/artifact/gear/synergy sections so curated cards match the
    generated loadout's format and detail."""
    elem = b["element"]
    a = _curated_answers(b)
    build = {cat: _resolve_items(cat, b.get(field), elem)
             for _lab, cat, field in CUR_SLOTS}
    slots_view = [(lab, build[cat]) for lab, cat, _field in CUR_SLOTS]
    art = recommend_artifact(elem, a)
    armor = recommend_armor_loadout(elem, a, build, art)
    return {
        "elem": elem, "cls": b["class"], "slots_view": slots_view,
        "armor_loadout": armor,
        "artifact": art,
        "gear_set": recommend_gear_set(elem, a),
        "stat_priority": stat_priority(a, elem),
        "weapon_synergy": recommend_weapon_synergy(elem, a, build, art),
        "synergy": compute_synergy(build, armor),
        "community": classify_community(b["class"], elem, build),
    }


@app.route("/results")
def results():
    a = session.get("answers", {})
    if not a:
        return redirect(url_for("index"))
    pool = [b for b in BUILDS if passes_hard_filters(b, a)]
    ranked = []
    for b in pool:
        s, reasons = score(b, a)
        ranked.append({"build": b, "score": s, "reasons": reasons,
                       "gen": enrich_curated(b)})
    ranked.sort(key=lambda x: -x["score"])
    top = ranked[0]["score"] if ranked else 0
    gen = construct(a)
    return render_template(
        "results.html", ranked=ranked, a=a, theme=theme(a), top=top, gen=gen
    )


@app.route("/back/<step>")
def back(step):
    return redirect(url_for(step))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=True)
