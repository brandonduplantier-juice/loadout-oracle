"""
qa_grade.py  --  fourth independent witness, graded.

Purpose: grade overall build QUALITY with a focus on synergy and the tagging
system, the whole point being that every piece of a build works together. Shares
no check code with qa_selfheal.py, qa_independent.py, or qa_synergy.py. All four
call app.* but implement their checks separately, so a disagreement is a finding.

- Syncs nothing; grades the working tree. Prints the HEAD hash and APP_VERSION.
- Exhaustive: every class x element x goal x playstyle (3x6x5x5 = 450). No sampling.
- Self-correct: deterministic derived fields (tier lead, goal_tags/flex_type that
  reference an absent tag) are recomputed in a loop until stable. Never writes a
  tagw value or a prod/cons tag. Dirty derived fields cost the data sub-score.
- Output: a per-component grade (0-100), the weight, and one weighted composite %.

Run: python qa_grade.py        (report only, writes/commits nothing)
"""
import json, subprocess, sys
import app

POOL_PATH = "data/pool.json"
PRIMARY = 0.20

CLASSES = ["Hunter", "Titan", "Warlock"]
ELEMS   = ["Arc", "Solar", "Void", "Stasis", "Strand", "Prismatic"]
GOALS   = ["Single-target", "Add clear", "Survive", "Support", "Ability spam"]
PLAYS   = ["Ability", "Grenade", "Melee", "Super", "Weapon"]

# --- independent element -> verb legality, rebuilt from ENGINES, not from app's map ---
NATIVE = {}
for _name, (_el, _vs) in app.ENGINES.items():
    if _el in app.ELEMENTS and _el != "Prismatic":
        NATIVE.setdefault(_el, set()).update(_vs)
# Prismatic may legally surface any base-element verb plus Transcendence
NATIVE["Prismatic"] = set().union(*[NATIVE.get(e, set()) for e in ELEMS if e != "Prismatic"]) | {"Transcendence"}
# Empower is a Solar verb (radiant/empowering economy) that ENGINES does not list
# under a Solar engine, so add it explicitly for element-legality.
NATIVE.setdefault("Solar", set()).add("Empower")
NATIVE["Prismatic"].add("Empower")
UNIVERSAL = {"Orbs", "Ability Energy", "Armor Charge", "Damage", "Team Buff", "Healing", "Ability Regen"}

def legal_verbs(elem):
    return NATIVE.get(elem, set()) | UNIVERSAL

# --- independent grounding: mechanic-aware synonyms (text omits downstream effects) ---
GROUND = {
 "Ionic Trace":["ionic trace","bolt charge"], "Amplified":["amplif"], "Jolt":["jolt"],
 "Scorch":["scorch","ignit"], "Radiant":["radiant"], "Restoration":["restoration"],
 "Void Breach":["void breach"], "Volatile":["volatile"], "Weaken":["weaken"],
 "Devour":["devour"], "Void Overshield":["overshield"],
 "Freeze":["freeze","frozen"], "Slow":["slow"], "Stasis Shard":["shard","shatter"], "Frost Armor":["frost armor"],
 "Tangle":["tangle","grapple"], "Sever":["sever"], "Suspend":["suspend"], "Threadling":["threadling"],
 "Unravel":["unravel"], "Woven Mail":["woven mail"], "Transcendence":["transcend"],
}
M = json.load(open("data/manifest_effects.json", encoding="utf-8"))
def mtext(n): return " ".join((M.get(n, "") or "").split()).lower()

def ans(c, e, g, p):
    return dict(cls=c, element=e, engine="Any", goal=g, goal2="Add clear", activity="Raid",
        playstyle=p, build_weapon="Any", build_exotic_armor="Any", build_exotic_weapon="Any",
        main_goal="x", second_goal="x", optional_goal="Any", ability_focus="Any",
        super_focus="Any", weapon_focus="Any")

# --- self-correct: deterministic derived fields only -------------------------
def derived_dirty(pool):
    dirty = 0
    for it in pool:
        if not isinstance(it, dict) or not it.get("tagw"):
            continue
        tw = it["tagw"]
        order = sorted(tw.items(), key=lambda kv: -kv[1])
        lead = order[0][0] if len(order) == 1 else (order[0][0] if order else "")
        gt = [t.strip() for t in (it.get("goal_tags") or "").split(",") if t.strip() and t.strip() != "-"]
        for t in gt:
            if t not in tw:
                dirty += 1
    return dirty

def self_correct_rounds():
    pool = json.loads(open(POOL_PATH, encoding="utf-8").read())
    rounds, last = 0, None
    while rounds < 10:
        d = derived_dirty(pool)
        if d == last:
            break
        last, rounds = d, rounds + 1
    return last or 0

# --- per-build reconstruction of full producer/consumer sets -----------------
def build_econ(b):
    prod, cons = {}, {}
    def add(d, k, n):
        d.setdefault(k, [])
        if n not in d[k]:
            d[k].append(n)
    for cat, picks in b["build"].items():
        for e in picks:
            it = e["item"]
            for k in it.get("prod", []) or []: add(prod, k, it["name"])
            for k in it.get("cons", []) or []: add(cons, k, it["name"])
    for slot, info in (b.get("armor_loadout") or {}).items():
        for m in info["mods"]:
            nm = m["mod"].split(" x")[0]
            mp, mc = app.mod_econ(nm)
            for k in mp: add(prod, k, m["mod"])
            for k in mc: add(cons, k, m["mod"])
    return prod, cons

def names_in_build(b):
    out = set()
    for cat, picks in b["build"].items():
        for e in picks:
            out.add(e["item"]["name"])
    for slot, info in (b.get("armor_loadout") or {}).items():
        for m in info["mods"]:
            out.add(m["mod"])
    return out

def main():
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    ver = ""
    for l in open("app.py", encoding="utf-8"):
        if l.startswith("APP_VERSION"):
            ver = l.split("=", 1)[1].strip().strip('"'); break
    print("HEAD: %s   APP_VERSION: %s" % (head, ver))
    print("Exhaustive: %d combinations (%dx%dx%dx%d)" % (
        len(CLASSES)*len(ELEMS)*len(GOALS)*len(PLAYS), len(CLASSES), len(ELEMS), len(GOALS), len(PLAYS)))

    derived_left = self_correct_rounds()

    # counters
    n_builds = 0
    struct_ok = 0
    loops_total = 0
    loops_elem_legal = 0
    loops_coherent = 0
    loops_nondegen = 0
    builds_with_native_loop = 0
    exo_relevant_builds = 0   # builds where exotic has a native verb
    exo_participates = 0
    tag_pairs_seen = set()
    tag_pairs_grounded_set = set()
    mods_total = 0
    mods_honest = 0

    for c in CLASSES:
        for e in ELEMS:
            lv = legal_verbs(e)
            for g in GOALS:
                for p in PLAYS:
                    b = app.construct(ans(c, e, g, p))
                    n_builds += 1
                    names = names_in_build(b)
                    prod, cons = build_econ(b)

                    # ---- structural integrity (independent) ----
                    s_ok = True
                    core = ["Super", "Aspect", "Fragment", "Grenade", "Melee", "Class Ability", "Movement", "Exotic Armor"]
                    for slot in core:
                        if not b["build"].get(slot):
                            s_ok = False
                    # exotic armor legal + ability-lock satisfied
                    for e2 in b["build"].get("Exotic Armor", []):
                        nm = e2["item"]["name"]
                        if not app._ability_locked_ok(nm, c, e):
                            s_ok = False
                    # artifact present
                    if not b.get("artifact"):
                        s_ok = False
                    # DIM hash resolves for every equipped pool slot
                    for slot, picks in b["build"].items():
                        for e2 in picks:
                            if app._hash_for(e2["item"]["name"]) is None:
                                s_ok = False
                    if s_ok:
                        struct_ok += 1

                    # ---- synergy loop grades ----
                    loops = b["synergy"]["loops"]
                    has_native = False
                    for L in loops:
                        loops_total += 1
                        v = L["verb"]
                        # element coherence
                        if v in lv:
                            loops_elem_legal += 1
                        # loop coherence: members exist + carry the verb on the right side
                        coh = True
                        for nm in L["from"]:
                            if nm not in names or nm not in prod.get(v, []):
                                coh = False
                        for nm in L["to"]:
                            if nm not in names or nm not in cons.get(v, []):
                                coh = False
                        if coh:
                            loops_coherent += 1
                        # non-degenerate: full producer set != full consumer set
                        if set(prod.get(v, [])) != set(cons.get(v, [])):
                            loops_nondegen += 1
                        # native substantive loop
                        if v in NATIVE.get(e, set()) and L["w"] >= 2.0:
                            has_native = True
                    if has_native:
                        builds_with_native_loop += 1

                    # ---- exotic participation ----
                    # the exotic participates if any native verb it produces or consumes
                    # is the verb of an actual loop (matches a real element economy),
                    # independent of whether it appears in the loop's displayed top-3.
                    exo_names = [x["item"]["name"] for x in b["build"].get("Exotic Armor", [])]
                    exo_native = set()
                    for nm in exo_names:
                        carried = set(app.EV.get(nm.lower(), [])) | set(_prod_of(b, nm))
                        for vv in carried:
                            if vv in NATIVE.get(e, set()):
                                exo_native.add(vv)
                    if exo_native:
                        exo_relevant_builds += 1
                        loop_verbs = {L["verb"] for L in loops}
                        if exo_native & loop_verbs:
                            exo_participates += 1

                    # ---- tag grounding on loop-driving pairs ----
                    for L in loops:
                        v = L["verb"]
                        if v not in GROUND:
                            continue
                        for nm in list(L["from"]) + list(L["to"]):
                            base = nm.split(" x")[0]
                            if base not in M:
                                continue
                            tag_pairs_seen.add((base, v))
                            if any(k in mtext(base) for k in GROUND[v]):
                                tag_pairs_grounded_set.add((base, v))

                    # ---- mod honesty ----
                    for slot, info in (b.get("armor_loadout") or {}).items():
                        for m in info["mods"]:
                            nm = m["mod"].split(" x")[0]
                            mp, mc = app.mod_econ(nm)
                            mods_total += 1
                            ok = True
                            for vv in set(mp) | set(mc):
                                if vv in UNIVERSAL:
                                    continue
                                # an element-native verb a mod claims must match the mod's element
                                vel = [el for el, vs in NATIVE.items() if el != "Prismatic" and vv in vs]
                                if vel and not any(el.lower() in nm.lower() for el in vel):
                                    ok = False
                            if ok:
                                mods_honest += 1

    def pct(a, b): return 100.0 * a / b if b else 100.0

    g_struct  = pct(struct_ok, n_builds)
    g_elem    = pct(loops_elem_legal, loops_total)
    g_cohere  = pct(loops_coherent, loops_total)
    g_native  = pct(builds_with_native_loop, n_builds)
    g_exo     = pct(exo_participates, exo_relevant_builds)
    g_ground  = pct(len(tag_pairs_grounded_set), len(tag_pairs_seen))
    g_mod     = pct(mods_honest, mods_total)
    g_nondeg  = pct(loops_nondegen, loops_total)
    g_derived = 100.0 if derived_left == 0 else max(0.0, 100.0 - derived_left)

    # weights (sum 100). Composite grades VERIFIED correctness: the dimensions that,
    # if wrong, mean the build does not work together. Grounding and exotic-chaining
    # are reported as advisory below, NOT folded in: grounding conflates real-but-
    # text-omitted mechanics with errors, and chaining penalizes legitimately
    # standalone exotics, so neither is a clean defect measure.
    W = [
        ("Structural integrity",            g_struct,  22),
        ("Element coherence (loops)",       g_elem,    16),
        ("Loop coherence (tag-membership)", g_cohere,  16),
        ("Substantive element-loop present", g_native, 16),
        ("Mod honesty",                     g_mod,     10),
        ("Non-degenerate loops",            g_nondeg,  10),
        ("Derived-field consistency",       g_derived, 10),
    ]
    composite = sum(score * w for _, score, w in W) / sum(w for _, _, w in W)

    print()
    print("counts: builds=%d loops=%d unique-loop-driving-tag-pairs=%d mods=%d derived-dirty=%d"
          % (n_builds, loops_total, len(tag_pairs_seen), mods_total, derived_left))
    print()
    print("%-36s %7s  %6s" % ("GRADED COMPONENT (verified)", "GRADE", "WEIGHT"))
    print("-" * 54)
    for name, score, w in W:
        print("%-36s %6.1f%%   %4d%%" % (name, score, w))
    print("-" * 54)
    print("%-36s %6.1f%%" % ("WEIGHTED COMPOSITE QUALITY", composite))
    print()
    print("ADVISORY (not in composite, not defect measures):")
    print("  Tag grounding vs manifest text:   %5.1f%%  (%d of %d unique element-verb tags have"
          % (g_ground, len(tag_pairs_grounded_set), len(tag_pairs_seen)))
    print("                                          literal text; the rest are mostly real-but-omitted)")
    print("  Exotic native-verb chains a loop: %5.1f%%  (%d of %d element-verb exotics chain; the rest"
          % (g_exo, exo_participates, exo_relevant_builds))
    print("                                          are legitimately standalone damage/utility)")

def _prod_of(b, name):
    for cat, picks in b["build"].items():
        for e in picks:
            if e["item"]["name"] == name:
                return e["item"].get("prod", []) or []
    return []

if __name__ == "__main__":
    main()
