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
    tg = _taglist(item["goal_tags"])
    fx = _taglist(item["flex_type"])
    s = 0.0
    for tag, val in w.items():
        if tag in tg:
            s += val
        elif tag in fx:
            s += val * 0.5
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


def assemble(cls, elem, a, w):
    build = {}
    total = 0.0
    for cat, need in SLOTS:
        ranked = sorted(gated(cat, cls, elem),
                        key=lambda x: (-item_score(x, w), x["name"]))
        picks = ranked[:need]
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
        total += sum(x["score"] for x in scored)
    return build, round(total, 1)


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
    best["has_goals"] = bool(w)
    best["armor_mods"] = recommend_armor_mods(best["elem"], a)
    best["weapon_mods"] = recommend_weapon_mods(a)
    best["artifact"] = recommend_artifact(best["elem"], a)
    best["gear_set"] = recommend_gear_set(best["elem"], a)
    best["armor_loadout"] = recommend_armor_loadout(best["elem"], a)
    best["stat_priority"] = stat_priority(a, best["elem"])
    best["weapon_synergy"] = recommend_weapon_synergy(best["elem"], a)
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


def recommend_armor_loadout(elem, a):
    """Fill every armor piece with a full mod set (~10 energy) matched to the build."""
    tags = _build_tags(a)
    el = "Harmonic" if elem in ("Prismatic", "Any", "") else elem
    out = {}
    for slot, mods in ARMOR_MODS.items():
        cands = [m for m in mods
                 if not (m["mod"] == "Harmonic Siphon" and el != "Harmonic")]

        def score(m):
            return len(set(m["tags"]) & tags) + (3 if m["elem"] else 0)
        ranked = sorted(cands, key=lambda m: -score(m))
        budget, chosen = 10, []
        for m in ranked:
            if score(m) <= 0 and chosen:
                continue
            name = m["mod"].replace("<Element>", el).replace("<Weapon>", "Primary")
            # element anchor mods (Siphon/Surge/Resistance) can stack
            copies = 2 if (m["elem"] and slot in ("Legs", "Helmet")) else 1
            for _ in range(copies):
                if budget - m["cost"] < 0 or len(chosen) >= 4:
                    break
                chosen.append({"mod": name, "cost": m["cost"], "desc": m["desc"]})
                budget -= m["cost"]
            if len(chosen) >= 4 or budget <= 1:
                break
        out[slot] = {"mods": chosen, "used": 10 - budget}
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


def recommend_weapon_synergy(elem, a):
    we = "your subclass element" if elem in ("Prismatic", "Any", "") else elem
    goals = (a.get("main_goal"), a.get("second_goal"), a.get("optional_goal"))
    dmg = "Max Damage" in goals or a.get("weapon_focus") == "High"
    spec = "Boss Spec" if dmg else "Minor Spec"
    return {
        "primary": "A " + we + " Primary to feed your Siphon orb generation and Surge stacks.",
        "heavy": "A Special or Heavy weapon for your damage phase.",
        "mods": [spec + " (damage)", "Backup Mag (uptime)",
                 "Counterbalance or Freehand Grip (stability/handling)"],
        "note": "Champion counters are intrinsic to weapon frames now, so no artifact slots are spent on them.",
    }


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
        "unlock_note": ("Equip this one artifact, then spend points (earned from XP) to "
                        "unlock perks in the order listed. You can hold up to about 12 at "
                        "once and refund freely, so prioritize the element-matched perks first."),
        "alts": [x["name"] for x in ranked[1:3]
                 if sum(1 for p in x["perks"] if _artifact_match(p, elem)) > 0],
    }


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
    best = None
    for sname, bonuses in GEAR_SETS.items():
        text = " ".join((b["perk"] + " " + b["desc"]).lower() for b in bonuses)
        sc = sum(1 for wd in signal if wd in text)
        if best is None or sc > best[0]:
            best = (sc, sname, bonuses)
    if not best or best[0] == 0:
        return None
    return {"name": best[1], "bonuses": best[2]}

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


@app.route("/results")
def results():
    a = session.get("answers", {})
    if not a:
        return redirect(url_for("index"))
    pool = [b for b in BUILDS if passes_hard_filters(b, a)]
    ranked = []
    for b in pool:
        s, reasons = score(b, a)
        ranked.append({"build": b, "score": s, "reasons": reasons})
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
