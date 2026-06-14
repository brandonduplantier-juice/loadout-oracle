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
    return best

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
    """Any -> 0, else 4 - rank so rank 1 weighs most."""
    if rank in (None, "", "Any"):
        return 0
    try:
        return 4 - int(rank)
    except (TypeError, ValueError):
        return 0


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
                           weapon_tree=WEAPON_TREE)


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
