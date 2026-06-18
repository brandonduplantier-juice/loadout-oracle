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
import base64
import json
import os
import re
import time
import uuid
from collections import deque
from urllib import request as urlreq, error as urlerr, parse as urlparse

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
# Authoritative armor mod energy costs from the Bungie manifest, written by
# bungie_pull_mod_costs.py into data/mod_costs.json. DIM enforces the manifest
# energy cost against each piece's budget (10 for most armor, 11 for Tier 4/5),
# so using these exact values keeps generated loadouts within the real budget and
# importable into DIM. Falls back to the static costs above for any mod the puller
# could not resolve, or if the file is absent. Re-run the puller after any Bungie
# mod rebalance to stay aligned.
try:
    with open(os.path.join(BASE, "data", "mod_costs.json"), encoding="utf-8") as f:
        MOD_COSTS = json.load(f)
    for _slot_mods in ARMOR_MODS.values():
        for _m in _slot_mods:
            if _m["mod"] in MOD_COSTS:
                _m["cost"] = MOD_COSTS[_m["mod"]]
except FileNotFoundError:
    MOD_COSTS = {}
try:
    with open(os.path.join(BASE, "data", "community_priors.json"), encoding="utf-8") as f:
        COMMUNITY_PRIORS = json.load(f)
except FileNotFoundError:
    COMMUNITY_PRIORS = {"item_pop": {}, "class_elem": {}, "n_builds": 0}
with open(os.path.join(BASE, "data", "gear_sets.json"), encoding="utf-8") as f:
    GEAR_SETS = json.load(f)

# Weapon recommendation data. weapon_perks.json: name -> {hash, slot, ammo, element,
# perks}. weapon_perk_tags.json: perk -> [build-page modifiers]. Both optional; if
# absent the weapon recommender simply returns nothing and the app is unchanged.
try:
    with open(os.path.join(BASE, "data", "weapon_perks.json"), encoding="utf-8") as f:
        WEAPON_PERKS = json.load(f)
    for _wnm, _wv in WEAPON_PERKS.items():
        _wv["name"] = _wnm
except FileNotFoundError:
    WEAPON_PERKS = {}

# Hardening: every pool Exotic Weapon must resolve to a WEAPON_PERKS entry, because
# recommend_weapons reads the exotic's equipment slot from there to skip that slot when
# filling legendaries. A name that does not resolve gives a blank slot and a silently
# double-filled slot in the build. Fail loud at load instead of minting a bad loadout.
# Set LO_WEAPON_CHECK=warn to downgrade to a logged warning (keeps the app serving).
if WEAPON_PERKS:
    _unmatched = sorted(p["name"] for p in POOL
                        if p.get("category") == "Exotic Weapon" and p["name"] not in WEAPON_PERKS)
    if _unmatched:
        _msg = ("weapon_perks.json has no slot data for these pool Exotic Weapons, so the "
                "weapon recommender cannot avoid their slot: " + ", ".join(_unmatched)
                + ". Re-run the exotic-weapon manifest pull or align the names.")
        if os.environ.get("LO_WEAPON_CHECK", "strict").lower() == "warn":
            print("WARNING: " + _msg)
        else:
            raise RuntimeError(_msg)
try:
    with open(os.path.join(BASE, "data", "weapon_perk_tags.json"), encoding="utf-8") as f:
        WEAPON_PERK_TAGS = {k: v for k, v in json.load(f).items() if k != "_comment"}
except FileNotFoundError:
    WEAPON_PERK_TAGS = {}

# Complete modifier -> internal tag map. Mirrors DIM2MOD but fills the entries
# DIM2MOD leaves empty (Utility, Team Buff, Mobility) so every tagged perk scores.
PERK_MOD2TAG = {
    "Damage": ["damage"], "Add Clear": ["orb", "ammo"], "Ability Regen": ["ability_regen"],
    "Survivability": ["survivability"], "Healing": ["healing", "survivability"],
    "Crowd Control": ["orb", "utility"], "Team Buff": ["orb"], "Utility": ["utility"],
    "Mobility": ["weapon_handling"],
}

# Prismatic exotic class items (Essentialism / Stoicism / Solipsism).
# Static file is the verified Final Shape launch set with synergy tags.
# If the dim_refs puller has written the live-manifest set, fold in any
# spirits the manifest lists that the static file is missing, so the app
# stays current the moment Brandon runs the puller. Tags persist by name.
with open(os.path.join(BASE, "data", "exotic_class_items.json"), encoding="utf-8") as f:
    EXOTIC_CLASS_ITEMS = json.load(f)
try:
    with open(os.path.join(BASE, "data", "dim_refs.json"), encoding="utf-8") as f:
        DIM_REFS = json.load(f)
except FileNotFoundError:
    DIM_REFS = {}
SUBCLASS_REFS = DIM_REFS.get("subclasses", {})
STAT_HASHES = DIM_REFS.get("stat_hashes", {})
ARMOR_MOD_HASHES = DIM_REFS.get("armor_mod_hashes", {})
FRAG_SLOTS = DIM_REFS.get("aspect_frag_slots", {})  # aspect name -> fragment count
try:
    with open(os.path.join(BASE, "data", "pool_hashes.json"), encoding="utf-8") as f:
        POOL_HASHES = json.load(f)  # item name -> manifest inventory item hash
except FileNotFoundError:
    POOL_HASHES = {}
DIM_API_KEY = os.environ.get("DIM_API_KEY", "")
# Single-account Bungie OAuth, so the server can mint authenticated DIM shares.
BUNGIE_API_KEY = os.environ.get("BUNGIE_API_KEY", "")
BUNGIE_OAUTH_CLIENT_ID = os.environ.get("BUNGIE_OAUTH_CLIENT_ID", "")
BUNGIE_OAUTH_CLIENT_SECRET = os.environ.get("BUNGIE_OAUTH_CLIENT_SECRET", "")
BUNGIE_REFRESH_TOKEN = os.environ.get("BUNGIE_REFRESH_TOKEN", "")
BUNGIE_MEMBERSHIP_ID = os.environ.get("BUNGIE_MEMBERSHIP_ID", "")
DIM_AUTH_READY = all([DIM_API_KEY, BUNGIE_API_KEY, BUNGIE_OAUTH_CLIENT_ID,
                      BUNGIE_OAUTH_CLIENT_SECRET, BUNGIE_REFRESH_TOKEN,
                      BUNGIE_MEMBERSHIP_ID])
# fold any live-manifest exotic class item spirits into the static set
for _cls, _live in (DIM_REFS.get("exotic_class_items") or {}).items():
    if _cls not in EXOTIC_CLASS_ITEMS:
        continue
    _base = EXOTIC_CLASS_ITEMS[_cls]
    for _col in ("col1", "col2"):
        have = {s["spirit"] for s in _base.get(_col, [])}
        for _s in (_live.get(_col) or []):
            nm = _s.get("spirit") or _s.get("name")
            if nm and nm not in have:
                _base.setdefault(_col, []).append({
                    "spirit": nm, "source": _s.get("source", ""),
                    "effect": _s.get("effect", ""), "tags": _s.get("tags", [])})

ICON_BASE = "https://www.bungie.net"

app = Flask(__name__)
import monitoring
monitoring.install(app)
app.secret_key = os.environ.get("SECRET_KEY", "loadout-oracle-local-key")

# Build version, shown in the footer. Bump APP_VERSION on each meaningful change.
APP_VERSION = "0.9.31"
BUILD_DATE = "2026-06-15"


@app.context_processor
def inject_version():
    return {"app_version": APP_VERSION, "build_date": BUILD_DATE}


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
    # boss-DPS shaping: the flat Damage tag cannot separate single-target burst from
    # add-clear, so credit a super's curated single-target and Weaken-debuff ratings.
    # SUPER_DPS only holds supers, so this is a no-op for every other slot.
    if w.get("Single-target") or w.get("Debuff"):
        d = SUPER_DPS.get(item["name"])
        if d:
            s += w.get("Single-target", 0) * d.get("st", 0) * 3.0
            s += w.get("Debuff", 0) * d.get("debuff", 0) * 3.0
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


SYN_KW = 1.2      # score per producer/consumer keyword the exotic shares with the build
SYN_ELEM = 2.0    # exotic weapon element matches the build element


def _build_econ(build):
    """Keywords produced and consumed by the slots assembled so far."""
    produced, consumed = set(), set()
    for _cat, picks in build.items():
        for e in picks:
            it = e["item"]
            produced.update(it.get("prod", []))
            consumed.update(it.get("cons", []))
    return produced, consumed


def exotic_synergy_score(item, w, produced, consumed, elem, a):
    """Score an exotic by how well it works with the rest of the build, not in
    isolation: closed producer/consumer loops, element match, then goal fit."""
    s = item_score(item, w)
    for k in item.get("prod", []):
        if k in consumed:
            s += SYN_KW
    for k in item.get("cons", []):
        if k in produced:
            s += SYN_KW
    we = WEAPON_ELEM.get(item["name"], "")
    if we:
        if we == elem:
            s += SYN_ELEM
        elif elem == "Prismatic":
            s += SYN_ELEM * 0.5
    tagw = item.get("tagw", {})
    if a.get("damage_profile") in ("Boss DPS", "Sustained") and tagw.get("Damage"):
        s += tagw["Damage"] * 2
    if a.get("playstyle") == "Weapon" and tagw.get("Damage"):
        s += tagw["Damage"]
    return s


def assemble(cls, elem, a, w):
    build = {}
    total = 0.0
    chosen_aspects = []
    for cat, need in SLOTS:
        n = need
        if cat == "Fragment" and chosen_aspects:
            # fragment count = sum of the chosen aspects' fragment slots,
            # using exact manifest counts (aspect name -> slots) when available
            n = max(1, min(5, sum(
                int(FRAG_SLOTS.get(x["name"], x.get("frag_slots", 2)) or 2)
                for x in chosen_aspects)))
        if cat in ("Exotic Weapon", "Exotic Armor"):
            # exotic slots are assembled last, so score them for synergy with
            # everything already chosen instead of in isolation
            produced, consumed = _build_econ(build)
            ranked = sorted(gated(cat, cls, elem), key=lambda x: (
                -exotic_synergy_score(x, w, produced, consumed, elem, a), x["name"]))
        else:
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
        c.add("Armor Charge")  # Surge spends Armor Charge for weapon damage; not the Empower buff
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


VERB_INFO = {
    "Orbs": "Orbs of Power. Picking one up returns ability energy and triggers your Armor Charge mods.",
    "Ability Energy": "Direct grenade, melee, or class ability energy, so your abilities come back faster.",
    "Empower": "A damage buff to your weapons and abilities, such as from an Empowering Rift.",
    "Armor Charge": "Stacks stored on your armor that power Surge, Kickstart, and other charged mods.",
    "Transcendence": "The Prismatic meter. Filling both halves grants a buffed state with a unique grenade.",
    "Damage": "A flat increase to the damage this build deals.",
    "Team Buff": "An effect that helps your whole fireteam, not just you.",
    "Jolt": "Jolted targets chain lightning to nearby enemies when hit. Strong add clear and Arc energy.",
    "Ionic Trace": "A streak of Arc energy that races to you and refunds ability energy.",
    "Amplified": "An Arc buff granting faster movement, reload, and handling after rapid Arc kills.",
    "Devour": "Kills restore full health and refill your grenade. A self-sustaining survival loop.",
    "Void Breach": "A pickup from Void ability kills that grants ability energy.",
    "Volatile": "Volatile targets explode in a Void blast when hit, spreading to nearby enemies.",
    "Weaken": "Weakened targets take more damage from every source. A team-wide damage debuff.",
    "Void Overshield": "A protective Void shield layered on top of your health.",
    "Scorch": "Stacking Solar burn. At 100 stacks the target ignites for a large explosion.",
    "Radiant": "A Solar buff that raises your weapon damage and pierces some enemy shields.",
    "Restoration": "Heals you over time and keeps most incoming hits from stopping the heal.",
    "Healing": "Restores your health, through cure or restoration.",
    "Freeze": "Locks a target in place, unable to act, until the freeze is broken.",
    "Slow": "Hampers a target's movement, aim, and abilities. Enough stacks will freeze it.",
    "Stasis Shard": "A shard from shattering frozen targets. Collecting it grants Frost Armor and energy.",
    "Frost Armor": "A stacking Stasis buff that adds damage resistance.",
    "Woven Mail": "A Strand buff that gives strong damage resistance.",
    "Threadling": "A small Strand creature that seeks and attacks nearby enemies. Add clear.",
    "Suspend": "Lifts and holds a target helpless in the air. Strong crowd control.",
    "Tangle": "A Strand knot from kills. Shoot or throw it to burst and spread Strand effects.",
    "Unravel": "Unraveled targets fire seeking threads at nearby enemies when hit. Add clear.",
    "Sever": "Cuts a target's outgoing damage so it hits you and allies for less.",
    "Crowd Control": "Effects that lock down or disable enemies, like freeze, suspend, or blind.",
}


_VERB_PLAIN = {
    "Orbs": "Orbs of Power", "Ability Energy": "ability energy",
    "Armor Charge": "Armor Charge for your damage mods",
    "Empower": "a weapon and ability damage buff", "Transcendence": "Prismatic Transcendence",
    "Damage": "extra damage", "Jolt": "Jolt that chains lightning",
    "Ionic Trace": "Ionic Traces that refund ability energy",
    "Amplified": "Amplified for faster handling", "Devour": "Devour that heals you on kills",
    "Scorch": "Scorch that builds toward Ignitions", "Ignition": "Ignitions",
    "Restoration": "Restoration healing over time", "Radiant": "Radiant, a weapon damage buff",
    "Volatile": "Volatile rounds", "Weaken": "Weaken on enemies", "Invisibility": "invisibility",
    "Overshield": "an overshield", "Woven Mail": "Woven Mail resistance",
    "Frost Armor": "Frost Armor resistance", "Freeze": "Freeze", "Slow": "Slow",
    "Stasis Shard": "Stasis shards", "Tangle": "Tangles you can shoot", "Unravel": "Unravel",
    "Suspend": "Suspend", "Cure": "healing", "Healing": "healing",
    "Void Breach": "Void Breaches that refund ability energy", "Bolt Charge": "Bolt Charge",
    "Team Buff": "a buff for your whole fireteam",
}


def _narr_names(lst):
    seen = []
    for x in lst:
        c = x.split(" x")[0]
        if c.startswith("Harmonic "):
            c = c[9:]
        if c not in seen:
            seen.append(c)
    seen = seen[:2]
    if not seen:
        return "your gear"
    return seen[0] if len(seen) == 1 else (seen[0] + " and " + seen[1])


def synergy_narrative(build, loops):
    """Plain-English summary of how the build sustains itself, read off its top loops.
    Names the producers, the effect they create, and what spends it, in one sentence."""
    loops = loops or []
    if not loops:
        return ""
    exo = ""
    if build.get("Exotic Armor"):
        exo = build["Exotic Armor"][0]["item"].get("name", "")
    lead = ("Built around " + exo + ", this loadout") if exo else "This loadout"
    lead += " sustains itself: "
    clauses = [_narr_names(L["from"]) + " make " + _VERB_PLAIN.get(L["verb"], L["verb"])
               + ", feeding " + _narr_names(L["to"]) for L in loops[:3]]
    return lead + "; ".join(clauses) + "."


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
        cross = [n for n in P if n not in C] or [n for n in C if n not in P]
        if P and C and cross:
            strength = min(len(P), len(C))
            contrib = strength * LOOP_WEIGHT.get(k, 1.0)
            score += contrib
            loops.append({"verb": k, "desc": VERB_INFO.get(k, ""),
                          "from": P[:3], "to": C[:3], "w": round(contrib, 1)})
    loops.sort(key=lambda l: -l["w"])
    top = loops[:6]
    return {"loops": top, "score": round(score, 1),
            "plain": synergy_narrative(build, top)}


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


def _perk_internal_tags(perk):
    out = set()
    for mod in WEAPON_PERK_TAGS.get(perk, []):
        out.update(PERK_MOD2TAG.get(mod, []))
    return out


def _weapon_score(w, pref, elem, elem_bonus):
    provided, driving = set(), []
    for p in w.get("perks", []):
        ti = _perk_internal_tags(p)
        if ti:
            provided |= ti
            driving.append(p)
    s = sum(pref.get(t, 0) for t in provided)
    if w.get("element") and w["element"] == elem:
        s += elem_bonus
    s += 0.1 * len(driving)
    return s, driving


def recommend_weapons(elem, a, build):
    """Pick a legendary for each weapon slot the exotic does not occupy. Kinetic
    and Energy use the build's goal tags and a strong element match; Power leans
    damage since the heavy is the build's DPS weapon. Returns {slot: {...}} keyed
    by Kinetic / Energy / Power, advisory: DIM equips whatever copy the player
    owns of the chosen hash, perks are the matched traits to chase."""
    if not WEAPON_PERKS:
        return {}
    pref = {t: 1.0 for t in _build_tags(a)}
    dmg = dict(pref)
    dmg["damage"] = dmg.get("damage", 0) + 3.0
    exo_slot = ""
    for nm in _slot_names(build.get("Exotic Weapon")):
        w = WEAPON_PERKS.get(nm)
        if w:
            exo_slot = w.get("slot", "")
            break
    out = {}
    for slot in ("Kinetic", "Energy", "Power"):
        if slot == exo_slot:
            continue
        pref_s = dmg if slot == "Power" else pref
        eb = 1.5 if slot == "Power" else 3.0
        pool = [w for w in WEAPON_PERKS.values()
                if w.get("slot") == slot and w.get("perks")]
        if not pool:
            continue
        # The build drives one element's Surge and Siphon, so the Kinetic and Energy
        # picks should match it when a matching weapon exists in that slot. Power stays
        # damage-first. Prismatic runs all elements, so it is exempt. Falls back to the
        # full pool when no element-matched weapon exists in the slot (e.g. an Arc build
        # has no Arc weapon in the Kinetic slot).
        if slot != "Power" and elem not in ("Prismatic", "Any", ""):
            matched = [w for w in pool if w.get("element") == elem]
            if matched:
                pool = matched
        best = max(pool, key=lambda w: _weapon_score(w, pref_s, elem, eb)[0])
        _, driving = _weapon_score(best, pref_s, elem, eb)
        out[slot] = {"name": best["name"], "hash": best["hash"],
                     "element": best.get("element", ""), "ammo": best.get("ammo", ""),
                     "type": best.get("type", ""), "perks": driving[:4]}
    return out


def construct(a):
    a = _normalize_answers(a)
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
    best["stat_priority"] = stat_priority(a, best["elem"], best["build"])
    best["weapon_synergy"] = recommend_weapon_synergy(best["elem"], a, best["build"], best["artifact"])
    best["dim_search"] = dim_search_for(best["build"])
    best["synergy"] = compute_synergy(best["build"], best["armor_loadout"])
    best["community"] = classify_community(best["cls"], best["elem"], best["build"])
    best["exotic_class_item"] = recommend_exotic_class_item(
        best["cls"], a, best["build"], best["elem"])
    best["weapon_recs"] = recommend_weapons(best["elem"], a, best["build"])
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
    the build's loops. Fills at most the 3 piece-specific slots, synergy-driven;
    never assigns the general stat slot (left open for the user to set); element
    anchors may stack across those 3 slots; stays under the 10 energy budget."""
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
    # Surge has no Harmonic version, so it takes the subclass element directly. On
    # Prismatic there is no single element, so anchor the Surge to the build's damage
    # weapon (the exotic weapon) so the mod resolves and transfers to DIM.
    surge_el = elem if elem not in ("Prismatic", "Any", "") else ""
    if not surge_el and build:
        for _ewn in _slot_names(build.get("Exotic Weapon")):
            _we = (WEAPON_PERKS.get(_ewn) or {}).get("element", "")
            if _we in ("Arc", "Solar", "Void", "Stasis", "Strand"):
                surge_el = _we
            break

    def eff_name(m):
        # The build assumes weapons match the subclass, so for any mod that has a
        # Harmonic version we use it: same effect, cheaper energy.
        if m.get("harmonic"):
            return m["harmonic_mod"]
        nm = m["mod"]
        if "<Element>" in nm:
            nm = ((surge_el + " ") if surge_el else "") + nm.replace("<Element> ", "")
        return nm.replace("<Weapon>", "Primary")

    def eff_cost(m):
        return m["harmonic_cost"] if m.get("harmonic") else m["cost"]

    out = {}
    for slot, mods in ARMOR_MODS.items():
        cands = list(mods)

        def score(m):
            name = eff_name(m)
            s = sum(pref.get(t, 0) for t in m["tags"]) + (3 if m["elem"] else 0)
            mp, mc = mod_econ(name)
            # loop closing: mod consumes what the build produces, or vice versa
            s += 2.0 * len(mc & build_prod) + 1.5 * len(mp & build_cons)
            return s

        def relevant(m):
            name = eff_name(m)
            # never offer an element mod we cannot resolve to a real hash (e.g. a
            # Surge with no element on Prismatic); it would silently drop from DIM.
            if m["elem"] and name not in ARMOR_MOD_HASHES:
                return False
            mp, mc = mod_econ(name)
            return (m["elem"] or bool(set(m["tags"]) & set(pref))
                    or bool((mc & build_prod) or (mp & build_cons)))

        ranked = sorted(cands, key=lambda m: (-score(m), eff_cost(m)))
        budget = 10
        # Each armor piece has 1 general (stat) slot + 3 piece-specific slots. We never
        # assign the general slot: it only adjusts stats (mobility, grenade, super, etc.)
        # and the user sets it themselves. We fill at most the 3 specific slots, driven by
        # synergy, and leave any leftover energy free for the user's own general mod. Each
        # mod copy occupies one slot, so a stacked mod (Surge x3) uses three of the three.
        SPECIFIC_SLOTS = 3
        # a non-anchor, non-loop mod must match the goal at least this strongly (sum of
        # build-tag preference) to earn a slot, otherwise the slot is left open.
        MOD_KEEP_FLOOR = 2.5
        counts = {}  # name -> [cost, desc, count, harmonic, base_cost]
        def slots_used():
            return sum(c[2] for c in counts.values())
        # pre-seed mods forced by a specific exotic, before the greedy fill
        for fname in forced.get(slot, []):
            m = next((x for x in cands if eff_name(x) == fname), None)
            if m and eff_cost(m) <= budget and slots_used() < SPECIFIC_SLOTS:
                counts.setdefault(fname, [eff_cost(m), m["desc"], 0,
                                          m.get("harmonic", False), m["cost"]])[2] += 1
                budget -= eff_cost(m)
        progress = True
        while budget > 0 and slots_used() < SPECIFIC_SLOTS and progress:
            progress = False
            for m in ranked:
                if not relevant(m):
                    continue
                name = eff_name(m)
                # earn the slot, do not just fill it: keep a mod only if it anchors the
                # element (Surge/Siphon/Resistance), closes one of the build's loops, or
                # strongly matches the goal. Otherwise leave the slot open for the user.
                mp_g, mc_g = mod_econ(name)
                closes = bool((mc_g & build_prod) or (mp_g & build_cons))
                if not (m["elem"] or closes
                        or sum(pref.get(t, 0) for t in m["tags"]) >= MOD_KEEP_FLOOR):
                    continue
                c = counts.setdefault(name, [eff_cost(m), m["desc"], 0,
                                             m.get("harmonic", False), m["cost"]])
                if c[2] >= m.get("max_copies", 1) or eff_cost(m) > budget:
                    continue
                c[2] += 1
                budget -= eff_cost(m)
                progress = True
                break
        modlist = [{"mod": n + (" x" + str(v[2]) if v[2] > 1 else ""),
                    "cost": v[0], "desc": v[1], "harmonic": v[3], "base_cost": v[4]}
                   for n, v in counts.items() if v[2] > 0]
        note = _slot_note(elem, [m["mod"] for m in modlist])
        if any(m["harmonic"] for m in modlist):
            note = ((note + "; ") if note else "") + (
                "Assumes " + elem + " armor: the harmonic discount on these mods only "
                "applies on element-matched armor, so off-element pieces will not fit them all.")
        if forced.get(slot) and forced.get("note"):
            note = forced["note"] + (("; " + note) if note else "")
        out[slot] = {"mods": modlist, "used": 10 - budget, "note": note}
    return out


# Per-super damage character. "st" = single-target boss-burst rating, "debuff" = applies a
# Weaken-style multiplier. The flat "Damage" tag could not separate a roaming add-clear super
# (Silkstrike) from a boss-burst super (Golden Gun) or value a Weaken tether, so on a Boss DPS
# goal the add-clear super won. item_score reads these at scoring time (kept out of pool tagw so
# the tag-distribution invariant holds), and stat_priority uses st to tell whether the super
# itself is the damage source. Curated, scale 0..0.5.
SUPER_DPS = {
    "Golden Gun: Deadshot": {"st": 0.50},
    "Golden Gun: Marksman": {"st": 0.50},
    "Blade Barrage": {"st": 0.40},
    "Nova Bomb: Cataclysm": {"st": 0.42},
    "Nova Bomb: Vortex": {"st": 0.30},
    "Thundercrash": {"st": 0.48},
    "Needlestorm": {"st": 0.45},
    "Chaos Reach": {"st": 0.40},
    "Gathering Storm": {"st": 0.35},
    "Storm's Edge": {"st": 0.30},
    "Silence and Squall": {"st": 0.32},
    "Daybreak": {"st": 0.15},
    "Fists of Havoc": {"st": 0.20},
    "Bladefury": {"st": 0.22},
    "Stormtrance": {"st": 0.18},
    "Burning Maul": {"st": 0.22},
    "Hammer of Sol": {"st": 0.25},
    "Sentinel Shield": {"st": 0.15},
    "Glacial Quake": {"st": 0.12},
    "Arc Staff": {"st": 0.15},
    "Silkstrike": {"st": 0.15},
    "Spectral Blades": {"st": 0.20},
    "Nova Warp": {"st": 0.18},
    "Winter's Wrath": {"st": 0.12},
    "Song of Flame": {"st": 0.22},
    "Well of Radiance": {"st": 0.10},
    "Ward of Dawn": {"st": 0.12},
    "Shadowshot: Deadfall": {"st": 0.10, "debuff": 0.50},
    "Shadowshot: Moebius Quiver": {"st": 0.38, "debuff": 0.38},
    "Twilight Arsenal": {"st": 0.38, "debuff": 0.40},
}


def stat_priority(a, elem, build=None):
    """Order the six Armor 3.0 stats by what this build actually leans on.
    A damage goal does not automatically mean Super. If the equipped super is not a
    boss-damage super, the damage is coming from weapons, so Weapons leads. The Weapons
    stat now governs weapon damage, reload, and reserves, so a weapon-DPS build must not
    bury it."""
    goals = [a.get("main_goal"), a.get("second_goal"), a.get("optional_goal")]
    play = a.get("playstyle")
    surv = a.get("survivability") == "High" or "High Survivability" in goals
    dmg_goal = "Max Damage" in goals or a.get("damage_profile") == "Boss DPS"
    super_name = None
    if build and build.get("Super"):
        try:
            super_name = build["Super"][0]["item"]["name"]
        except Exception:
            super_name = None
    super_is_damage = SUPER_DPS.get(super_name, {}).get("st", 0) >= 0.35
    super_focused = a.get("super_focus") == "High" or play == "Super"

    if super_focused or (dmg_goal and super_is_damage):
        front = ["Super", "Weapons"]          # super burst plus weapon sustain
    elif dmg_goal or play == "Weapon" or a.get("weapon_focus") == "High":
        front = ["Weapons", "Health"] if surv else ["Weapons", "Super"]
    elif "Grenade" in goals or "Ability Spam" in goals or a.get("ability_focus") == "High":
        front = ["Grenade"]
    elif "Melee" in goals:
        front = ["Melee"]
    else:
        front = ["Health"]

    seq = list(front)
    if surv and "Health" not in seq:
        seq.append("Health")
    for s in ["Super", "Grenade", "Melee", "Class", "Weapons", "Health"]:
        if s not in seq:
            seq.append(s)
    return [{"stat": s, "desc": STATS[s]} for s in seq]


ECI_NEED_WEIGHTS = {
    "super":         {"super": 3, "weapon": 1, "regen": 1},
    "grenade":       {"grenade": 3, "regen": 2, "weapon": 1, "ability": 1},
    "melee":         {"melee": 3, "add-clear": 1, "regen": 1},
    "weapon":        {"weapon": 3, "add-clear": 1, "super": 1},
    "survivability": {"survivability": 3, "healing": 2, "class": 1},
    "ability":       {"regen": 3, "ability": 2, "grenade": 1, "melee": 1, "class": 1},
}
# meta staples get a small versatility nudge so ties resolve toward known picks
ECI_STAPLE = {"Spirit of Inmost Light": 0.5, "Spirit of the Ophidian": 0.2,
              "Spirit of the Star-Eater": 1.5, "Spirit of Synthoceps": 0.2}


def _eci_need(a):
    play = a.get("playstyle")
    goals = [a.get("main_goal"), a.get("second_goal"), a.get("optional_goal")]
    if a.get("super_focus") == "High" or play == "Super" or "Max Damage" in goals:
        return "super"
    if play == "Melee":
        return "melee"
    if play == "Grenade":
        return "grenade"
    if play == "Weapon" or a.get("weapon_focus") == "High":
        return "weapon"
    if "High Survivability" in goals or "Healing" in goals:
        return "survivability"
    return "ability"


def _eci_pick(spirits, need):
    w = ECI_NEED_WEIGHTS.get(need, ECI_NEED_WEIGHTS["ability"])
    best, bestscore = None, -1.0
    for s in spirits:
        sc = sum(w.get(t, 0) for t in s.get("tags", [])) + ECI_STAPLE.get(s["spirit"], 0)
        if sc > bestscore:
            best, bestscore = s, sc
    return best


def recommend_exotic_class_item(cls, a, build, elem):
    """Prismatic-only: pick a col1 + col2 spirit combo matched to build focus."""
    if elem != "Prismatic" or cls not in EXOTIC_CLASS_ITEMS:
        return None
    item = EXOTIC_CLASS_ITEMS[cls]
    need = _eci_need(a)
    why = {
        "super": "leans into Super and boss damage",
        "grenade": "feeds grenade-centric ability spam",
        "melee": "supports a melee-forward loop",
        "weapon": "favors weapon damage and uptime",
        "survivability": "prioritizes staying alive",
        "ability": "keeps your full ability kit cycling",
    }.get(need, "rounds out the build")
    note = ("It takes your single exotic-armor slot, so it competes with a "
            "dedicated exotic, it does not stack with one. The item is always "
            + item["name"] + ", but its two Spirit perks are a random roll, so "
            "target this pairing by farming Dual Destiny and attuning.")
    chosen = a.get("exotic_armor")
    if chosen and chosen not in ("Any", "", None):
        note = ("You picked " + str(chosen) + " as your exotic. A class item is "
                "only worth it if a roll clearly beats that for this build. " + note)
    return {"name": item["name"], "slot": item["slot"], "need": need, "why": why,
            "col1": _eci_pick(item["col1"], need),
            "col2": _eci_pick(item["col2"], need),
            "all_col1": item["col1"], "all_col2": item["col2"], "note": note}


def dim_search_for(build):
    names = []
    for slot in ("Exotic Weapon", "Exotic Armor"):
        for e in build.get(slot, []):
            nm = (e.get("item") or {}).get("name", "")
            if nm and nm.lower() != "none":
                names.append(nm)
    return " or ".join('name:"' + n + '"' for n in names)


# ---- DIM loadout share document builder ----
DESTINY_CLASS = {"Titan": 0, "Hunter": 1, "Warlock": 2}
_POOL_HASHES_NORM = {_norm(k): v for k, v in POOL_HASHES.items()}
DIM_MOVEMENT_CAT = 457473665  # class-ability/movement socket category in 3.0 subclasses


def _hash_for(name):
    """Resolve an item name to a manifest hash, apostrophe and case tolerant."""
    if not name:
        return None
    h = POOL_HASHES.get(name)
    if h is not None:
        return h
    return _POOL_HASHES_NORM.get(_norm(name))


def _slot_names(slotval):
    out = []
    if isinstance(slotval, list):
        for x in slotval:
            nm = (x.get("item") or {}).get("name") if isinstance(x, dict) else None
            if nm and nm.lower() != "none":
                out.append(nm)
    elif isinstance(slotval, str):
        out = [p.strip() for p in slotval.split(",") if p.strip()]
    return out


def _subclass_overrides(sc, build):
    """Map build ability/aspect/fragment picks to subclass socket indices.

    Indices are derived from the subclass's real socket_categories so Prismatic
    (fragments at 9+) and base subclasses (fragments at 7+) both place correctly.
    """
    cats = {int(k): v for k, v in (sc.get("socket_categories") or {}).items()}
    if not cats:
        return {}
    aspect_cat = cats.get(5)
    frag_cat = cats[max(cats)]  # highest socket index is always a fragment slot
    overrides = {}

    def place(idx, slotkey):
        nm = _slot_names(build.get(slotkey))
        if nm:
            h = _hash_for(nm[0])
            if h:
                overrides[idx] = int(h)

    place(0, "Super")
    place(1, "Class Ability")
    place(2, "Movement")
    place(3, "Melee")
    place(4, "Grenade")
    for idx, nm in zip(sorted(i for i, c in cats.items() if c == aspect_cat),
                       _slot_names(build.get("Aspect"))):
        h = _hash_for(nm)
        if h:
            overrides[idx] = int(h)
    for idx, nm in zip(sorted(i for i, c in cats.items() if c == frag_cat),
                       _slot_names(build.get("Fragment"))):
        h = _hash_for(nm)
        if h:
            overrides[idx] = int(h)
    return overrides


def _mod_hashes(gen):
    """Manifest hashes for the build's armor mods, one entry per copy.

    Source is the budgeted armor_loadout only. armor_mods is a second, legacy
    recommender; feeding both put the union of two mod sets into each slot and
    pushed past the 10 energy budget, which is why the DIM optimizer returned no
    builds. Stacked mods (x2, x3) are emitted once per copy because DIM's mods
    list represents each copy as a repeated hash; collapsing them dropped copies.
    """
    out = []
    for _slot, info in (gen.get("armor_loadout") or {}).items():
        for m in (info.get("mods") or []):
            raw = str((m.get("mod") if isinstance(m, dict) else m) or "").strip()
            mt = re.search(r"x(\d+)\s*$", raw)
            count = int(mt.group(1)) if mt else 1
            nm = re.sub(r"\s*x\d+\s*$", "", raw)
            h = ARMOR_MOD_HASHES.get(nm)
            if h:
                out.extend([int(h)] * count)
    return out


def build_dim_loadout(gen):
    """Assemble a DIM loadout-share document from a generated build."""
    cls, elem, build = gen["cls"], gen["elem"], gen["build"]
    equipped = []
    sc = SUBCLASS_REFS.get(cls + "|" + elem)
    if sc and sc.get("hash"):
        item = {"hash": int(sc["hash"])}
        ov = _subclass_overrides(sc, build)
        if ov:
            item["socketOverrides"] = ov
        equipped.append(item)

    params = {"assumeArmorMasterwork": 3}  # All (legendary + exotic)
    exo = _slot_names(build.get("Exotic Armor"))
    if exo:
        h = _hash_for(exo[0])
        if h:
            params["exoticArmorHash"] = int(h)
    # weapons: equip the build's exotic weapon by hash so DIM actually loads it.
    # Armor is left to the optimizer via exoticArmorHash and statConstraints, but a
    # weapon is a specific item, so it must be in equipped or the slot stays empty.
    for nm in _slot_names(build.get("Exotic Weapon")):
        h = _hash_for(nm)
        if h:
            equipped.append({"hash": int(h)})
    # legendary picks from the weapon recommender fill the other two slots
    for _slot, _rec in (gen.get("weapon_recs") or {}).items():
        _rh = _rec.get("hash")
        if _rh:
            equipped.append({"hash": int(_rh)})
    scs = []
    for s in gen.get("stat_priority", []):
        sh = STAT_HASHES.get(s["stat"])
        if sh:
            scs.append({"statHash": int(sh)})
    if scs:
        params["statConstraints"] = scs
    mods = _mod_hashes(gen)
    if mods:
        params["mods"] = mods

    label = (gen.get("community") or {}).get("label")
    name = "Loadout Oracle - " + (label or (cls + " " + elem))
    return {
        "id": str(uuid.uuid4()),
        "name": name[:120],
        "classType": DESTINY_CLASS.get(cls, 3),
        "equipped": equipped,
        "unequipped": [],
        "parameters": params,
        "notes": "Generated by Loadout Oracle (loadout-oracle.onrender.com)",
    }


_DIM_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_TOKENS = {"bungie": None, "bungie_exp": 0.0, "dim": None, "dim_exp": 0.0,
           "destiny_mid": None, "refresh": None}


def _bungie_access_token():
    """Refresh the Bungie access token, cached. Bungie rotates the refresh token
    on each call, so we keep the freshest one for this process's lifetime and fall
    back to the env-provided token on a cold start."""
    now = time.time()
    if _TOKENS["bungie"] and now < _TOKENS["bungie_exp"] - 60:
        return _TOKENS["bungie"]
    refresh = _TOKENS.get("refresh") or BUNGIE_REFRESH_TOKEN
    data = urlparse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    }).encode("utf-8")
    basic = base64.b64encode(
        (BUNGIE_OAUTH_CLIENT_ID + ":" + BUNGIE_OAUTH_CLIENT_SECRET).encode("utf-8")
    ).decode("ascii")
    req = urlreq.Request(
        "https://www.bungie.net/Platform/App/OAuth/Token/", data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Authorization": "Basic " + basic, "X-API-Key": BUNGIE_API_KEY,
                 "User-Agent": _DIM_UA})
    with urlreq.urlopen(req, timeout=12) as r:
        tok = json.loads(r.read().decode("utf-8"))
    _TOKENS["bungie"] = tok["access_token"]
    _TOKENS["bungie_exp"] = now + int(tok.get("expires_in", 3600))
    if tok.get("refresh_token"):
        _TOKENS["refresh"] = tok["refresh_token"]
    return _TOKENS["bungie"]


def _dim_auth_token():
    """Exchange the Bungie access token for a DIM bearer token, cached."""
    now = time.time()
    if _TOKENS["dim"] and now < _TOKENS["dim_exp"] - 60:
        return _TOKENS["dim"]
    bt = _bungie_access_token()
    body = json.dumps({"bungieAccessToken": bt,
                       "membershipId": BUNGIE_MEMBERSHIP_ID}).encode("utf-8")
    req = urlreq.Request(
        "https://api.destinyitemmanager.com/auth/token", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": DIM_API_KEY,
                 "Accept": "application/json", "User-Agent": _DIM_UA})
    with urlreq.urlopen(req, timeout=12) as r:
        tok = json.loads(r.read().decode("utf-8"))
    _TOKENS["dim"] = tok["accessToken"]
    _TOKENS["dim_exp"] = now + int(tok.get("expiresInSeconds", 3600))
    return _TOKENS["dim"]


def _dim_post_once(payload, bearer):
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": DIM_API_KEY,
        "X-DIM-Version": "loadout-oracle-" + APP_VERSION,
        "User-Agent": _DIM_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    req = urlreq.Request("https://api.destinyitemmanager.com/loadout_share",
                         data=body, method="POST", headers=headers)
    with urlreq.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def _destiny_membership_id():
    """Look up the user's primary Destiny platform membership id, cached.
    The DIM share endpoint requires this (not the Bungie.net id)."""
    if _TOKENS.get("destiny_mid"):
        return _TOKENS["destiny_mid"]
    at = _bungie_access_token()
    req = urlreq.Request(
        "https://www.bungie.net/Platform/User/GetMembershipsForCurrentUser/",
        headers={"X-API-Key": BUNGIE_API_KEY, "Authorization": "Bearer " + at,
                 "User-Agent": _DIM_UA, "Accept": "application/json"})
    with urlreq.urlopen(req, timeout=12) as r:
        data = json.loads(r.read().decode("utf-8"))
    resp = data.get("Response", {})
    mid = resp.get("primaryMembershipId")
    if not mid:
        dm = resp.get("destinyMemberships") or []
        if dm:
            mid = dm[0].get("membershipId")
    if not mid:
        raise RuntimeError("no Destiny membership found for account")
    _TOKENS["destiny_mid"] = mid
    return mid


def post_dim_share(loadout):
    """Create a DIM loadout share, returning (url, error). Mints an authenticated
    share under the single configured account. Degrades gracefully, logs to stdout."""
    if not DIM_AUTH_READY:
        return None, "auth_not_configured"
    try:
        bearer = _dim_auth_token()
        platform_mid = _destiny_membership_id()
    except urlerr.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            detail = ""
        print("[dim_share] auth HTTPError", e.code, detail)
        monitoring.alert("dim_auth_http_" + str(e.code),
                         "DIM share auth failed: HTTP %s %s" % (e.code, detail[:150]))
        return None, "auth_http_" + str(e.code) + (": " + detail if detail else "")
    except Exception as e:
        print("[dim_share] auth error", e)
        monitoring.alert("dim_auth_error", "DIM share auth failed: %s" % str(e)[:150])
        return None, "auth_error: " + str(e)[:150]
    payload = {"loadout": loadout, "platformMembershipId": platform_mid}
    try:
        data = _dim_post_once(payload, bearer)
    except urlerr.HTTPError as e:
        try:
            detail = e.read().decode("utf-8")[:400]
        except Exception:
            detail = ""
        err = "http_" + str(e.code) + (": " + detail if detail else "")
        print("[dim_share] HTTPError", err)
        return None, err
    except Exception as e:
        err = type(e).__name__ + ": " + str(e)[:200]
        print("[dim_share] error", err)
        return None, err
    url = data.get("shareUrl") or data.get("url")
    if not url:
        sid = data.get("shareId") or data.get("id")
        url = "https://dim.gg/" + str(sid) if sid else None
    if url:
        print("[dim_share] ok", url)
        return url, None
    err = "no_url_in_response: " + json.dumps(data)[:300]
    print("[dim_share]", err)
    return None, err


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

    if we == "your subclass element":
        primary = ("A Primary in any of your Prismatic damage types so its kills "
                   "feed your Siphon orbs and stack Surge.")
    else:
        primary = (("An " if we[:1] in "AEIOU" else "A ") + we
                   + " Primary so its kills feed your " + we
                   + " Siphon orbs and stack Surge.")
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
    wtype = a.get("weapon_type", "")
    goals = (a.get("main_goal"), a.get("second_goal"), a.get("optional_goal"))
    tier_weight = {3: 1.0, 2: 0.9, 1: 0.8}

    def pscore(p):
        s = 0.0
        els = p.get("elements", []) or ([p["element"]] if p.get("element") else [])
        if elem == "Prismatic":
            s += 1.5 if els else 0.0
        elif elem in els:
            s += 3.0
        if wtype and wtype in p.get("weapons", []):
            s += 3.0
        t = p.get("type", "")
        if t == "economy":
            s += 1.5
        elif t == "survivability" and "Survivability" in goals:
            s += 1.0
        elif t == "utility":
            s += 0.5
        return s

    def artifact_fit(art):
        total = 0.0
        for p in art.get("perks", []):
            if p.get("champion"):
                continue
            total += pscore(p) * tier_weight.get(p.get("tier"), 0.9)
        for e in (art.get("elements") or []):
            if e == elem or (elem == "Prismatic" and e):
                total += 1.0
        if wtype and wtype in (art.get("weapons") or []):
            total += 1.0
        return total

    ranking = sorted(ARTIFACTS, key=lambda art: (-artifact_fit(art), art["name"]))
    art = ranking[0]
    eligible = [p for p in art["perks"] if not p.get("champion")]
    ranked = sorted(eligible, key=lambda p: (-pscore(p), p.get("tier", 9), p["perk"]))
    picks = [p for p in ranked if pscore(p) > 0][:8] or ranked[:6]
    alts = [other["name"] for other in ranking[1:3] if artifact_fit(other) > 0]
    return {
        "name": art["name"], "source": art.get("source", ""),
        "perks": picks, "alts": alts,
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
        "goal": f.get("goal", "Any"),
        "goal2": f.get("goal2", "Any"),
        "activity": f.get("activity", "Any"),
        "build_weapon": f.get("build_weapon", "Any").strip() or "Any",
        "build_exotic_armor": f.get("build_exotic_armor", "Any"),
        "build_exotic_weapon": f.get("build_exotic_weapon", "Any"),
    }
    return redirect(url_for("synergy"))


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


def engines_for_element(elem):
    """The keyword engines legal for a chosen subclass element (plus Any).
    Prismatic gets only Transcendence; Any element shows them all."""
    allowed = ["Any"]
    for name, (eelem, _verbs) in ENGINES.items():
        if elem in ("Any", eelem):
            allowed.append(name)
    return allowed


@app.route("/synergy", methods=["GET", "POST"])
def synergy():
    a = session.get("answers", {})
    if request.method == "POST":
        f = request.form
        a["engine"] = f.get("engine", "Any")
        a["playstyle"] = f.get("playstyle", "Any")
        session["answers"] = a
        return redirect(url_for("results"))
    engines = engines_for_element(a.get("element", "Any"))
    return render_template("step3.html", o=OPTIONS, a=a, theme=theme(a),
                           engines=engines)


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
    a = _normalize_answers(a)
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
        "stat_priority": stat_priority(a, elem, build),
        "weapon_synergy": recommend_weapon_synergy(elem, a, build, art),
        "synergy": compute_synergy(build, armor),
        "community": classify_community(b["class"], elem, build),
    }


@app.route("/results")
def results():
    a = session.get("answers", {})
    if not a:
        return redirect(url_for("index"))
    a = _normalize_answers(a)
    pool = [b for b in BUILDS if passes_hard_filters(b, a)]
    ranked = []
    for b in pool:
        s, reasons = score(b, a)
        ranked.append({"build": b, "score": s, "reasons": reasons,
                       "gen": enrich_curated(b)})
    ranked.sort(key=lambda x: -x["score"])
    top = ranked[0]["score"] if ranked else 0
    gen = construct(a)
    monitoring.check_build(a, gen)
    return render_template(
        "results.html", ranked=ranked, a=a, theme=theme(a), top=top, gen=gen,
        dim_enabled=DIM_AUTH_READY
    )


# In-memory rate limiting for the public DIM share endpoint, which performs an
# authenticated action under the single configured account. The service runs a
# single worker, so a process-local limiter is consistent. State resets on
# restart, which is acceptable for casual abuse prevention.
_RATE_IP = {}            # ip -> deque[timestamps]
_RATE_GLOBAL = deque()   # timestamps across all callers
_RATE_IP_MAX = 12        # shares per IP
_RATE_IP_WINDOW = 3600   # over this many seconds
_RATE_GLOBAL_MAX = 60    # shares total
_RATE_GLOBAL_WINDOW = 600


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "?"


def _rate_ok(ip):
    now = time.time()
    g = _RATE_GLOBAL
    while g and now - g[0] > _RATE_GLOBAL_WINDOW:
        g.popleft()
    if len(g) >= _RATE_GLOBAL_MAX:
        return False
    dq = _RATE_IP.get(ip)
    if dq is None:
        dq = deque()
        _RATE_IP[ip] = dq
    while dq and now - dq[0] > _RATE_IP_WINDOW:
        dq.popleft()
    if len(dq) >= _RATE_IP_MAX:
        return False
    dq.append(now)
    g.append(now)
    if len(_RATE_IP) > 2000:  # bound memory: drop emptied buckets
        for k in [k for k, v in _RATE_IP.items() if not v]:
            _RATE_IP.pop(k, None)
    return True


@app.route("/dim_share", methods=["POST"])
def dim_share():
    a = session.get("answers", {})
    if not a:
        return {"ok": False, "reason": "no_build"}, 400
    if not DIM_API_KEY:
        return {"ok": False, "reason": "no_key"}
    if not _rate_ok(_client_ip()):
        return {"ok": False, "reason": "rate_limited"}, 429
    url, err = post_dim_share(build_dim_loadout(construct(a)))
    if url:
        return {"ok": True, "url": url}
    return {"ok": False, "reason": err or "unknown"}


@app.route("/report_issue", methods=["POST"])
def report_issue():
    """User-submitted build issue report: emails the flagged parts, a note, and an
    optional screenshot to the maintainer. Reuses the alert Gmail credentials."""
    if not _rate_ok(_client_ip()):
        return {"ok": False, "reason": "rate_limited"}, 429
    data = request.get_json(force=True, silent=True) or {}
    note = (str(data.get("note") or "")).strip()[:2000]
    flagged = data.get("flagged") or []
    if isinstance(flagged, list):
        flagged = ", ".join(str(x) for x in flagged[:40])[:1000]
    else:
        flagged = str(flagged)[:1000]
    page = (str(data.get("url") or ""))[:300]
    png = None
    shot = data.get("screenshot") or ""
    if isinstance(shot, str) and shot.startswith("data:image/png;base64,"):
        try:
            raw = base64.b64decode(shot.split(",", 1)[1])
            png = raw if 0 < len(raw) <= 6_000_000 else None
        except Exception:
            png = None
    a = session.get("answers", {}) or {}
    ctx = ", ".join("%s=%s" % (k, a.get(k)) for k in
                    ("cls", "element", "engine", "main_goal", "second_goal",
                     "optional_goal", "playstyle", "activity") if a.get(k))
    body = ("User-reported build issue.\n\n"
            "Flagged parts: %s\n\n"
            "Note:\n%s\n\n"
            "Build inputs: %s\n"
            "Page: %s\n"
            "Screenshot attached: %s\n"
            % (flagged or "(none)", note or "(none)", ctx or "(none)", page,
               "yes" if png else "no"))
    ok = monitoring.send_report("Build issue report", body, png)
    return {"ok": bool(ok)} if ok else {"ok": False, "reason": "send_failed"}


@app.route("/back/<step>")
def back(step):
    return redirect(url_for(step))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=True)


# ============ PORTED SYNERGY ENGINE (was synergy_full prototype) ============

ENGINES = {
    'Jolt':('Arc',{'Jolt','Ionic Trace','Amplified'}),'Amplified':('Arc',{'Amplified','Ionic Trace','Jolt'}),
    'Bolt Charge':('Arc',{'Jolt','Amplified','Ionic Trace'}),'Devour':('Void',{'Devour','Void Breach','Healing'}),
    'Volatile':('Void',{'Volatile','Void Breach','Weaken'}),'Invisibility':('Void',{'Void Overshield','Volatile','Weaken'}),
    'Radiant':('Solar',{'Radiant','Restoration','Scorch'}),'Ignition':('Solar',{'Scorch','Radiant'}),
    'Frost Armor':('Stasis',{'Frost Armor','Stasis Shard','Slow'}),'Shatter':('Stasis',{'Freeze','Stasis Shard','Slow'}),
    'Woven Mail':('Strand',{'Woven Mail','Tangle','Sever'}),'Threadlings':('Strand',{'Threadling','Tangle','Unravel'}),
    'Suspend':('Strand',{'Suspend','Tangle','Unravel'}),'Transcendence':('Prismatic',{'Transcendence','Empower','Ability Energy'}),
}
DAMAGE_AMP = {'Radiant','Weaken','Empower','Volatile'}   # single-target stacks damage buffs
EXOTIC_VERBS = {
    "Fallen Sunstar":["Ionic Trace","Amplified"],"Crown of Tempests":["Jolt","Ionic Trace"],"Geomag Stabilizers":["Ionic Trace","Amplified"],
    "Getaway Artist":["Amplified","Jolt","Ionic Trace"],"Ballidorse Wrathweavers":["Frost Armor","Stasis Shard"],
    "Rime-Coat Raiment":["Frost Armor","Stasis Shard","Slow"],"Osmiomancy Gloves":["Slow","Freeze","Stasis Shard"],
    "Contraverse Hold":["Devour","Void Breach"],"Secant Filaments":["Devour","Void Overshield"],"Nothing Manacles":["Void Breach"],
    "Briarbinds":["Void Breach"],"Sunbracers":["Scorch"],"Starfire Protocol":["Scorch","Ability Energy"],"Dawn Chorus":["Scorch","Radiant"],
    "Speaker's Sight":["Radiant","Healing"],"Swarmers":["Threadling","Tangle","Unravel"],"Mothkeeper's Wraps":["Woven Mail"],
    "Mataiodoxía":["Transcendence"],"Gyrfalcon's Hauberk":["Volatile","Void Breach"],"Omnioculus":["Weaken"],
    "Cyrtarachne's Facade":["Woven Mail"],"Mask of Fealty":["Sever","Tangle"],"Renewal Grasps":["Slow","Frost Armor"],"Mask of Bakris":["Slow"],
    "Liar's Handshake":["Amplified"],"Raiden Flux":["Amplified"],"Celestial Nighthawk":["Damage"],"Star-Eater Scales":["Damage"],"Foetracer":["Weaken"],
    "Pyrogale Gauntlets":["Scorch"],"Hallowfire Heart":["Scorch","Ability Energy"],"Khepri's Horn":["Scorch"],
    "Cadmus Ridge Lancecap":["Stasis Shard","Frost Armor"],"Hoarfrost-Z":["Stasis Shard","Frost Armor"],"Icefall Mantle":["Frost Armor"],
    "Abeyant Leap":["Suspend","Sever","Woven Mail"],"Point-Contact Cannon Brace":["Jolt"],"An Insurmountable Skullfort":["Amplified"],
    "Eternal Warrior":["Amplified"],"Doom Fang Pauldron":["Void Overshield","Volatile"],"Helm of Saint-14":["Void Overshield"],"Ursa Furiosa":["Void Overshield"],
    "Trinity Ghoul":["Jolt"],"Coldheart":["Ionic Trace"],"Riskrunner":["Amplified","Jolt"],"Centrifuse":["Jolt","Amplified"],
    "Delicate Tomb":["Ionic Trace","Amplified"],"Sunshot":["Scorch"],"Polaris Lance":["Scorch"],"Skyburner's Oath":["Scorch"],
    "Dragon's Breath":["Scorch"],"Ticuu's Divination":["Scorch","Radiant"],"Graviton Lance":["Weaken"],
    "Collective Obligation":["Volatile","Weaken","Devour"],"Tractor Cannon":["Weaken"],"Ruinous Effigy":["Volatile","Weaken"],
    "Conditional Finality":["Freeze","Scorch"],"Verglas Curve":["Stasis Shard","Freeze"],"Cryosthesia 77K":["Freeze"],
    "Wicked Implement":["Slow","Freeze","Stasis Shard"],"Ager's Scepter":["Slow","Stasis Shard"],"Salvation's Grip":["Stasis Shard","Freeze"],
    "Quicksilver Storm":["Tangle","Unravel"],"Final Warning":["Tangle"],"Wish-Keeper":["Suspend","Tangle"],"Euphony":["Tangle","Threadling"],
}
DPS_GUNS = {"One Thousand Voices","Whisper of the Worm","Izanagi's Burden","Still Hunt","The Lament","Sleeper Simulant","D.A.R.C.I.",
    "Leviathan's Breath","Deathbringer","Gjallarhorn","Two-Tailed Fox","Microcosm","Grand Overture","Eyes of Tomorrow","Cloudstrike","Xenophage"}

VERB_PATTERNS=[('Jolt',r'jolt'),('Volatile',r'volatile'),('Scorch',r'scorch|ignit'),('Slow',r'\bslow'),('Freeze',r'frozen|freez'),
 ('Weaken',r'weaken'),('Sever',r'sever'),('Suspend',r'suspend'),('Unravel',r'unravel'),('Threadling',r'threadling'),('Tangle',r'tangle'),
 ('Devour',r'devour'),('Frost Armor',r'frost armor'),('Radiant',r'radiant'),('Amplified',r'amplif'),('Woven Mail',r'woven mail'),
 ('Void Overshield',r'overshield'),('Restoration',r'restoration|\bcure'),('Void Breach',r'void breach'),('Stasis Shard',r'stasis shard'),
 ('Ionic Trace',r'ionic trace'),('Orbs',r'orb of power|\borbs?\b'),('Armor Charge',r'armor charge'),
 ('Ability Energy',r'ability energy|melee energy|class ability energy|grenade energy|energy to your'),('Healing',r'\bheal|health'),
 ('Transcendence',r'transcend'),('Empower',r'empower')]
CONS_HINTS=['while you have','while you are','while ','picking up','collecting','consume','against','affected by','debuffed','shattering']
def derive_econ(desc):
    d=' '+(desc or '').lower()+' ';prod,cons=set(),set()
    for verb,pat in VERB_PATTERNS:
        m=re.search(pat,d)
        if not m: continue
        pre=d[max(0,m.start()-32):m.start()]
        (cons if any(h in pre for h in CONS_HINTS) else prod).add(verb)
    return prod,cons
for art in ARTIFACTS:
    for p in art.get('perks',[]):
        pr,co=derive_econ(p.get('desc',''));p['prod'],p['cons']=sorted(pr),sorted(co)

LOOP_WEIGHT={"Orbs":2.0,"Armor Charge":1.5,"Empower":0.4,"Healing":0.6,"Ability Energy":0.8,"Damage":0.8,"Transcendence":2.5,
 "Devour":2.2,"Void Breach":2.0,"Volatile":1.8,"Weaken":1.2,"Void Overshield":1.4,"Jolt":1.8,"Ionic Trace":1.6,"Amplified":1.2,
 "Frost Armor":1.8,"Stasis Shard":1.5,"Slow":1.0,"Freeze":1.6,"Scorch":1.6,"Radiant":1.4,"Restoration":1.2,"Woven Mail":1.6,
 "Tangle":1.3,"Threadling":1.6,"Unravel":1.3,"Sever":1.0,"Suspend":1.3,"Crowd Control":1.0,"Team Buff":0.8}

GOALW={'Single-target':[('Damage',1.0),('Single-target',1.5),('Debuff',1.0)],'Add clear':[('Add Clear',1.0)],'Survive':[('Survivability',1.0)],
 'Support':[('Team Buff',0.6),('Healing',0.6),('Debuff',0.8)],'Ability spam':[('Ability Regen',1.0)]}
def goal_weights2(a):
    w={}
    for k,m in (('goal',3),('goal2',2)):
        for tag,v in GOALW.get(a.get(k,'Any'),[]): w[tag]=w.get(tag,0)+v*m
    for tag,v in ACTIVITY_TAGS.get(a.get('activity','Any'),[]): w[tag]=w.get(tag,0)+v*0.5  # activity is a secondary nudge, not a rival to the explicit goal
    return w

def engine_contribution(it,verbs,produced,consumed):
    ip,ic=set(it.get('prod',[]) or [])&verbs,set(it.get('cons',[]) or [])&verbs
    return 2.0*(len(ip&(consumed-produced))+len(ic&(produced-consumed)))+0.6*len(ip|ic)

_man=json.load(open(os.path.join(BASE, "data", "exotic_verbs.json"), encoding="utf-8"))
try: FRAG_SLOTS=json.load(open(os.path.join(BASE, "data", "aspect_frag_slots.json"), encoding="utf-8"))
except Exception: pass
# Curated fragment-slot corrections, verified in game. The puller assigns native=min and
# prism=max across an aspect's plug variants, assuming Prismatic grants at least as many
# slots as the base subclass. Edge of Fate broke that: these four keep 3 slots on their base
# subclass but were cut to 2 on Prismatic, so the manifest inverts or over-reports them.
# Verified counts win and survive re-pulls.
_FRAG_OVERRIDE = {
    "Ascension": {"native": 3, "prism": 2},
    "Consecration": {"native": 3, "prism": 2},
    "Threaded Specter": {"native": 3, "prism": 2},
    "Winter's Shroud": {"native": 3, "prism": 2},
    "Gunpowder Gamble": {"native": 2, "prism": 3},
    "Weaver's Call": {"native": 2, "prism": 3},
}
for _k, _v in _FRAG_OVERRIDE.items():
    FRAG_SLOTS[_k] = _v
# Thruster, Acrobat's Dodge, Phoenix Dive (class abilities) and Blink (movement) are each
# exclusive to one base subclass AND legal on Prismatic (per Bungie / Destinypedia). The flat
# element tag cannot say "Arc OR Prismatic", so gate them explicitly instead of mis-tagging.
_DUAL={"Acrobat's Dodge":("Hunter",("Arc",)),"Thruster":("Titan",("Arc",)),"Phoenix Dive":("Warlock",("Solar",))}
def _dual_state(name,cls,elem):
    # True = must be available here, False = must be excluded here, None = not a dual ability
    if name=='Blink':
        return (cls=='Hunter' and elem in ('Arc','Prismatic')) or (cls=='Warlock' and elem in ('Void','Prismatic'))
    if name in _DUAL:
        c,bases=_DUAL[name]; return cls==c and (elem in bases or elem=='Prismatic')
    return None
_DUAL_ITEMS=[it for it in POOL if it['name'] in (set(_DUAL)|{'Blink'})]
_orig_gated=gated
def gated2(cat,cls,elem):
    out=[it for it in _orig_gated(cat,cls,elem) if _dual_state(it['name'],cls,elem) is not False]
    have={it['name'] for it in out}
    for it in _DUAL_ITEMS:
        nm=it['name']
        if it.get('category')==cat and nm not in have and _dual_state(nm,cls,elem) is True and it.get('class') in (cls,'Any'):
            out.append(it); have.add(nm)
    return out
gated=gated2
EV={name.lower():list(info['prod']) for name,info in _man.items() if info.get('prod')}
# curated EV corrections for puller contamination: it derives verbs from all socket
# text (ornaments, shaders), so cosmetic flavor leaks in as spurious verbs. The Stag's
# 'Scorch' is shader text, not its perk, and wrongly trips the element-lock penalty on
# non-Solar survive builds. Superseded once the intrinsic-only puller is re-pulled.
_EV_DROP={'the stag':{'Scorch'}}
for _k,_d in _EV_DROP.items():
    if _k in EV: EV[_k]=[v for v in EV[_k] if v not in _d]
# small hand-fix for exotics the text heuristic missed or mistagged (engine-critical only)
OVERRIDE={
 # Arc
 "Fallen Sunstar":["Ionic Trace","Amplified"],"Crown of Tempests":["Jolt","Ionic Trace"],"Getaway Artist":["Amplified","Jolt"],
 "Geomag Stabilizers":["Ionic Trace","Amplified"],"Point-Contact Cannon Brace":["Jolt"],"An Insurmountable Skullfort":["Amplified"],
 "Eternal Warrior":["Amplified"],"Liar's Handshake":["Amplified"],"Trinity Ghoul":["Jolt"],"Coldheart":["Ionic Trace"],
 "Riskrunner":["Amplified","Jolt"],"Centrifuse":["Jolt","Amplified"],"Delicate Tomb":["Ionic Trace","Amplified"],
 # Void
 "Contraverse Hold":["Devour","Void Breach"],"Secant Filaments":["Devour","Void Overshield"],"Nothing Manacles":["Void Breach"],
 "Gyrfalcon's Hauberk":["Volatile","Void Breach"],"Doom Fang Pauldron":["Void Overshield","Volatile"],"Astrocyte Verse":["Volatile"],
 "Collective Obligation":["Volatile","Weaken"],"Tractor Cannon":["Weaken"],"Graviton Lance":["Weaken"],
 # Solar
 "Pyrogale Gauntlets":["Scorch"],"Sunbracers":["Scorch"],"Starfire Protocol":["Scorch","Ability Energy"],"Dawn Chorus":["Scorch","Radiant"],
 "Hallowfire Heart":["Scorch"],"Speaker's Sight":["Radiant","Healing"],"Celestial Nighthawk":["Damage"],"Sunshot":["Scorch"],
 "Polaris Lance":["Scorch"],"Ticuu's Divination":["Scorch","Radiant"],
 # Stasis
 "Ballidorse Wrathweavers":["Frost Armor","Stasis Shard"],"Rime-coat Raiment":["Frost Armor","Stasis Shard"],
 "Cadmus Ridge Lancecap":["Stasis Shard","Frost Armor"],"Renewal Grasps":["Slow","Frost Armor"],"Mask of Bakris":["Slow"],
 "Osmiomancy Gloves":["Slow","Freeze","Stasis Shard"],"Wicked Implement":["Slow","Freeze","Stasis Shard"],
 "Verglas Curve":["Stasis Shard","Freeze"],"Conditional Finality":["Freeze","Scorch"],
 # Strand
 "Swarmers":["Threadling","Tangle","Unravel"],"Cyrtarachne's Facade":["Woven Mail"],"Abeyant Leap":["Suspend","Sever","Woven Mail"],
 "Mask of Fealty":["Sever","Tangle"],"Mothkeeper's Wraps":["Woven Mail"],"Quicksilver Storm":["Tangle","Unravel"],
 "Euphony":["Tangle","Threadling"],"Final Warning":["Tangle"],
 # Prismatic
 "Mataiodoxía":["Transcendence"],
}
for name,vl in OVERRIDE.items(): EV[name.lower()]=list(vl)
WELEM={name.lower():(info.get('element') or '') for name,info in _man.items() if info.get('slot')=='weapon'}
# exotics whose effect is dead without a specific slot pick; force that pick when the exotic is equipped
EXOTIC_FORCE={
 "Boots of the Assembler":{"Class Ability":"Healing Rift"},
 "Speaker's Sight":{"Class Ability":"Healing Rift"},
}
try:
    EXOTIC_ABILITY = json.load(open(os.path.join(BASE, "data", "exotic_abilities.json"), encoding="utf-8"))
except Exception:
    EXOTIC_ABILITY = {}
try:
    monitoring.startup_data_check(POOL, EXOTIC_ABILITY)
except Exception:
    pass


def _ability_locked_ok(name, cls, elem):
    """An ability-locked exotic is only valid when its signature ability is legal on
    this subclass and element. A Tripmine exotic is dead on Stasis, so drop it there."""
    links = EXOTIC_ABILITY.get(name)
    if not links:
        return True
    for slot, abil in links.items():
        if abil not in {x["name"] for x in gated(slot, cls, elem)}:
            return False
    return True

def assemble2(cls,elem,a,w):
    engine=a.get('engine','Any');base_verbs=set(ENGINES.get(engine,('',set()))[1]);verbs=set(base_verbs)
    # Map each element-coded verb to its element, to detect an exotic whose signature
    # is locked to a different subclass (Crown of Tempests' Arc regen is dead on Strand).
    VERB_ELEM={}
    for _e,_vs in ENGINES.values():
        if _e in ELEMENTS and _e!='Prismatic':
            for _v in _vs: VERB_ELEM[_v]=_e
    # verbs whose value does not depend on the build element; an exotic carrying one of
    # these still works off-element, so it is exempt from the element-lock penalty below.
    EXO_AGNOSTIC={'Orbs','Ability Energy','Armor Charge','Damage','Team Buff','Healing'}
    single='Single-target' in (a.get('goal'),a.get('goal2'))
    if single: verbs|=DAMAGE_AMP
    produced,consumed=set(),set();build={};total=0.0;chosen_aspects=[]
    def evget(name): return set(EV.get(name.lower(),[]))
    def fold(it): produced.update(it.get('prod',[]) or []);consumed.update(it.get('cons',[]) or [])
    _goal=a.get('goal','')
    def goal_fit(it):
        # small nudge so the keystone exotic serves the primary goal; never overrides
        # the +4 per engine-verb term, so engine keystones are unaffected.
        tw=it.get('tagw') or {}
        if not tw: return 0.0
        dom=max(tw,key=tw.get)
        if _goal=='Single-target': return 0.3 if dom=='Damage' else (-0.3 if dom in ('Survivability','Healing','Team Buff') else 0.0)
        if _goal in ('Survive','Support'): return 0.3 if dom in ('Survivability','Healing','Team Buff') else (-0.3 if dom=='Damage' else 0.0)
        if _goal=='Add clear': return 0.3 if dom in ('Add Clear','Crowd Control') else 0.0
        if _goal=='Ability spam': return 0.3 if dom=='Ability Regen' else 0.0
        return 0.0
    def pick_exotic(cat,pin):
        cands=[c for c in gated(cat,cls,elem) if _ability_locked_ok(c['name'],cls,elem)];chosen=None
        fitv=verbs if cat=='Exotic Weapon' else base_verbs   # armor stays on engine; weapon may chase damage amp
        if pin not in ('Any','',None):
            fi=find_pool_item(cat,pin)
            if fi and fi.get('class') in ('Any',cls): chosen=fi
        if chosen is None and cands:
            def sc(it):
                if cat=='Exotic Weapon':
                    wel=WELEM.get(it['name'].lower(),'')
                    on=(elem=='Prismatic') or (wel==elem)
                    ev=(evget(it['name'])&fitv) if on else set()
                else:
                    ev=evget(it['name'])&fitv
                s=4.0*len(ev)+0.4*item_score(it,w)
                if cat=='Exotic Armor':
                    s+=goal_fit(it)
                    # an exotic whose only engine verbs are locked to another element
                    # cannot fire here, so its ability-regen tagw is misleading: penalize
                    exo_v=evget(it['name']); exo_el={VERB_ELEM[v] for v in exo_v if v in VERB_ELEM}
                    # Non-Prismatic: the lock must match the build element. Generic
                    # Prismatic (no engine pinned): the run element is uncommitted, so a
                    # single-element-locked exotic is a gamble; disfavor it in favor of
                    # an element-agnostic one. A pinned engine opts back in. Exotics that
                    # also carry a universal verb (Healing, Team Buff, Orbs, etc.) are
                    # exempt: that value works on any subclass, so the penalty would wrongly
                    # bury real support/healing exotics (Speaker's Sight, Apotheosis Veil).
                    if exo_el and not (exo_v & EXO_AGNOSTIC) and elem not in exo_el and (elem!='Prismatic' or not base_verbs):
                        s-=8.0
                if cat=='Exotic Weapon' and single: s+=(50.0 if it['name'] in DPS_GUNS else 0.0)+2.0*item_score(it,{'Damage':3})
                if cat=='Exotic Weapon' and w.get('Debuff') and 'Weaken' in evget(it['name']):
                    # An intrinsic weapon Weaken (Tractor, Divinity, Graviton, etc.) is a team-wide
                    # damage multiplier that fires on any subclass, so credit it ungated by element
                    # and scaled by the build's Debuff weight. Kept under the DPS_GUNS bonus so a
                    # single-target goal still leads with the damage gun; this surfaces the debuff
                    # weapon on support and enabler builds where it is the right call.
                    s += w["Debuff"] * 3.0
                return s+1e-6*len(it['name'])
            chosen=max(cands,key=sc)
        if chosen is None: return None
        it=dict(chosen);it['prod']=sorted(set(it.get('prod',[]) or [])|evget(chosen['name']));return it
    ea=pick_exotic('Exotic Armor',a.get('build_exotic_armor','Any'));ew=pick_exotic('Exotic Weapon',a.get('build_exotic_weapon','Any'))
    if ea: fold(ea)
    if ew: fold(ew)
    for cat,need in SLOTS:
        if cat=='Exotic Armor': picks=[ea] if ea else []
        elif cat=='Exotic Weapon': picks=[ew] if ew else []
        else:
            n=need
            if cat=='Fragment' and chosen_aspects:
                def _frag_slots(it):
                    # fragment count is element-dependent: an aspect grants a different
                    # number of fragment slots on Prismatic than on its native subclass
                    # (Hellion is 2 on Solar, 3 on Prismatic). data may be the per-element
                    # form {native,prism} or the legacy flat int; handle both.
                    v=FRAG_SLOTS.get(it['name'])
                    if isinstance(v,dict):
                        key='prism' if elem=='Prismatic' else 'native'
                        return int(v.get(key) or v.get('native') or v.get('prism') or it.get('frag_slots',2) or 2)
                    if v is not None: return int(v or 2)
                    return int(it.get('frag_slots',2) or 2)
                n=max(1,sum(_frag_slots(x) for x in chosen_aspects))
            cands=gated(cat,cls,elem);picks=[]
            forced=None
            for exo in (ea,ew):
                if exo and exo['name'] in EXOTIC_FORCE and cat in EXOTIC_FORCE[exo['name']]:
                    forced=EXOTIC_FORCE[exo['name']][cat]
                if exo and exo['name'] in EXOTIC_ABILITY and cat in EXOTIC_ABILITY[exo['name']]:
                    forced=EXOTIC_ABILITY[exo['name']][cat]
            if forced:
                fi=next((c for c in cands if c['name']==forced),None)
                if fi: picks.append(fi);cands.remove(fi);fold(fi)
            for _ in range(n-len(picks)):
                if not cands: break
                b=max(cands,key=lambda it:item_score(it,w)+engine_contribution(it,verbs,produced,consumed)+1e-6*len(it['name']))
                picks.append(b);cands.remove(b);fold(b)
        scored=[{'item':p,'score':round(item_score(p,w),1)} for p in picks]
        build[cat]=scored
        if cat=='Aspect': chosen_aspects=[x['item'] for x in scored]
        total+=sum(x['score'] for x in scored)
    return build,round(total,1)

def recommend_artifact2(elem,a):
    if not ARTIFACTS: return None
    engine=a.get('engine','Any');verbs=set(ENGINES.get(engine,('',set()))[1])
    if 'Single-target' in (a.get('goal'),a.get('goal2')): verbs|=DAMAGE_AMP
    def pem(p):
        els=p.get('elements') or ([p['element']] if p.get('element') else [])
        return 1 if (elem=='Prismatic' and els) or elem in els else 0
    best,bs=None,-1
    for art in ARTIFACTS:
        perks=[p for p in art['perks'] if not p.get('champion')]
        em=sum(pem(p) for p in perks);vs=sum(len((set(p.get('prod',[]))|set(p.get('cons',[])))&verbs) for p in perks)
        sc=3.0*em+1.0*vs+2.0*(elem in (art.get('elements') or []))
        if sc>bs: best,bs=art,sc
    perks=[p for p in best['perks'] if not p.get('champion')]
    ranked=sorted(perks,key=lambda p:(-(len((set(p.get('prod',[]))|set(p.get('cons',[])))&verbs)),-pem(p),p.get('tier',9),p['perk']))
    return {'name':best['name'],'source':best.get('source',''),'perks':ranked[:8],'alts':[]}

_orig_gear=recommend_gear_set
def recommend_gear_set2(elem,a):
    GS=GEAR_SETS
    if not GS: return None
    engine=a.get('engine','Any');verbs=set(ENGINES.get(engine,('',set()))[1])
    if 'Single-target' in (a.get('goal'),a.get('goal2')): verbs|=DAMAGE_AMP
    el=(elem or '').lower()
    goalwords={'Survive':['heal','shield','resist','health'],'Support':['ally','allies','fireteam','heal'],
               'Add clear':['orb','combatant','ammo'],'Single-target':['damage','boss','final blow'],
               'Ability spam':['grenade','melee','ability','energy']}
    sig=set()
    for k in ('goal','goal2'):
        for wd in goalwords.get(a.get(k,''),[]): sig.add(wd)
    ranked=[]
    for sname,bonuses in GS.items():
        text=' '.join((b['perk']+' '+b['desc']).lower() for b in bonuses)
        pr,co=derive_econ(text)
        vscore=len((set(pr)|set(co))&verbs)
        escore=1 if (el and el!='prismatic' and el in text) else 0
        gscore=sum(1 for wd in sig if wd in text)
        ranked.append((3*vscore+3*escore+gscore,sname,bonuses))
    ranked.sort(key=lambda x:-x[0])
    if not ranked or ranked[0][0]==0: return _orig_gear(elem,a)
    best=ranked[0];four=_set_bonus(best[2],4)
    out={'name':best[1],'four':{'perk':four['perk'],'desc':four['desc']} if four else None,'two_two':[]}
    for sc,sname,bonuses in ranked[:2]:
        two=_set_bonus(bonuses,2)
        if two: out['two_two'].append({'name':sname,'perk':two['perk'],'desc':two['desc']})
    return out
goal_weights=goal_weights2;assemble=assemble2;recommend_artifact=recommend_artifact2;recommend_gear_set=recommend_gear_set2

# ---- synergy engine: answer normalization (bridges wizard vocab to the engine) ----
G2OLD = {'Single-target': 'Max Damage', 'Add clear': 'Add Clear', 'Survive': 'High Survivability',
         'Support': 'Team Buff', 'Ability spam': 'Ability Spam', 'Any': 'Any'}
DPROF = {'Single-target': 'Boss DPS', 'Add clear': 'Add Clear', 'Survive': 'Support',
         'Support': 'Support', 'Ability spam': 'Sustained', 'Any': 'Any'}
OLD2NEW = {'Max Damage': 'Single-target', 'Boss DPS': 'Single-target', 'Add Clear': 'Add clear',
           'Crowd Control': 'Add clear', 'High Survivability': 'Survive', 'Solo': 'Survive',
           'Healing': 'Support', 'Team Buff': 'Support', 'Ability Spam': 'Ability spam',
           'Utility': 'Any', 'Any': 'Any'}
NEW_GOALS = {'Single-target', 'Add clear', 'Survive', 'Support', 'Ability spam'}


def _to_new_goal(v):
    return v if v in NEW_GOALS else OLD2NEW.get(v, 'Any')


def _normalize_answers(a):
    """Fill the new keys the engine reads (goal, goal2) from whatever goal vocab the
    wizard supplied, and back-fill the old-vocab keys the un-ported helpers read, without
    clobbering an explicit pick the user already made."""
    a = dict(a)
    if a.get('goal') not in NEW_GOALS:
        a['goal'] = _to_new_goal(a.get('main_goal', 'Any'))
    if a.get('goal2') not in NEW_GOALS:
        a['goal2'] = _to_new_goal(a.get('second_goal', 'Any'))
    g, g2 = a['goal'], a['goal2']
    a.setdefault('engine', 'Any')
    a.setdefault('playstyle', 'Any')
    if a.get('main_goal', 'Any') in ('Any', None):
        a['main_goal'] = G2OLD.get(g, 'Any')
    if a.get('second_goal', 'Any') in ('Any', None):
        a['second_goal'] = G2OLD.get(g2, 'Any')
    a.setdefault('optional_goal', 'Any')
    if a.get('damage_profile', 'Any') in ('Any', None):
        a['damage_profile'] = DPROF.get(g, 'Any')
    if a.get('survivability', 'Any') in ('Any', None):
        a['survivability'] = 'High' if 'Survive' in (g, g2) else 'Any'
    if a.get('team_role', 'Any') in ('Any', None):
        a['team_role'] = 'Support' if 'Support' in (g, g2) else ('DPS' if 'Single-target' in (g, g2) else 'Flex')
    ps = a.get('playstyle', 'Any')
    if a.get('ability_focus', 'Any') in ('Any', None):
        a['ability_focus'] = 'High' if ps in ('Ability', 'Grenade', 'Melee') else 'Any'
    if a.get('super_focus', 'Any') in ('Any', None):
        a['super_focus'] = 'High' if ps == 'Super' else 'Any'
    if a.get('weapon_focus', 'Any') in ('Any', None):
        a['weapon_focus'] = 'High' if ps == 'Weapon' else 'Any'
    return a
