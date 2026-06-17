"""
build_quality.py  --  optimality PROFILER, not a coherence checker.

The other four witnesses prove a build is legal and internally consistent. They do
NOT tell you whether the engine picked strong pieces. This tool estimates that, with
two honest limits stated up front:

  1. Parts A and B are tag-derived. They measure whether a build is DEEP, FOCUSED,
     and uses HIGH-FIT pieces versus shallow, scattered, low-fit. That correlates
     with quality but is computed from the same tags the engine builds from, so it
     cannot certify a build is the meta choice. Treat low scores as a worklist of
     builds to inspect, not as proof of a defect.

  2. Part C (benchmark) is the only part that measures against EXTERNAL truth, real
     community builds. Its value equals the quality of build_benchmark.json, which
     YOU own. The seed shipped here is tiny and marked; verify and expand it. Do not
     trust the benchmark number until the seed is yours.

Run: python build_quality.py            (report only, commits nothing)
"""
import json, subprocess, os
import app

CLASSES = ["Hunter", "Titan", "Warlock"]
ELEMS   = ["Arc", "Solar", "Void", "Stasis", "Strand", "Prismatic"]
GOALS   = ["Single-target", "Add clear", "Survive", "Support", "Ability spam"]
PLAYS   = ["Ability", "Grenade", "Melee", "Super", "Weapon"]

GOAL_VERBS = {
    "Single-target": ["Damage"],
    "Add clear":     ["Add Clear", "Crowd Control"],
    "Survive":       ["Survivability", "Healing"],
    "Support":       ["Team Buff", "Healing"],
    "Ability spam":  ["Ability Regen"],
}

NATIVE = {}
for _n, (_el, _vs) in app.ENGINES.items():
    if _el in app.ELEMENTS and _el != "Prismatic":
        NATIVE.setdefault(_el, set()).update(_vs)
NATIVE["Prismatic"] = set().union(*[NATIVE.get(e, set()) for e in ELEMS if e != "Prismatic"]) | {"Transcendence"}
NATIVE.setdefault("Solar", set()).add("Empower")
NATIVE["Prismatic"].add("Empower")

POOL = [it for it in json.load(open("data/pool.json", encoding="utf-8")) if isinstance(it, dict)]
ARM = [it for it in POOL if it.get("category") == "Exotic Armor" and it.get("tagw")]

def ans(c, e, g, p):
    return dict(cls=c, element=e, engine="Any", goal=g, goal2="Add clear", activity="Raid",
        playstyle=p, build_weapon="Any", build_exotic_armor="Any", build_exotic_weapon="Any",
        main_goal="x", second_goal="x", optional_goal="Any", ability_focus="Any",
        super_focus="Any", weapon_focus="Any")

def build_econ(b):
    prod, cons = {}, {}
    def add(d, k, n):
        d.setdefault(k, [])
        if n not in d[k]: d[k].append(n)
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

def legal_exotics(cls, elem):
    out = []
    for it in ARM:
        if it.get("class") not in (cls, "Any"):
            continue
        if it.get("element") not in (elem, "Any") and not (elem != "Prismatic" and it.get("element") == elem):
            continue
        if not app._ability_locked_ok(it["name"], cls, elem):
            continue
        out.append(it)
    return out

def profile_build(c, e, g, p):
    b = app.construct(ans(c, e, g, p))
    prod, cons = build_econ(b)
    loops = b["synergy"]["loops"]
    loop_verbs = {L["verb"] for L in loops}
    gverbs = GOAL_VERBS[g]

    # 1) core depth: strongest element-native loop's min(producers, consumers)
    native_loops = [L for L in loops if L["verb"] in NATIVE.get(e, set())]
    depth = 0
    if native_loops:
        core = max(native_loops, key=lambda L: L["w"])
        v = core["verb"]
        depth = min(len(prod.get(v, [])), len(cons.get(v, [])))
    depth_score = min(depth, 3) / 3.0

    # 2) exotic reinforcement: exotic's strongest native verb is in a loop that has a
    #    non-exotic contributor (aspects/fragments/mods actually feed it)
    exo = [x["item"] for x in b["build"].get("Exotic Armor", [])]
    exo_names = {it["name"] for it in exo}
    exo_native = set()
    for it in exo:
        carried = set(app.EV.get(it["name"].lower(), [])) | set(it.get("prod", []) or [])
        exo_native |= {v for v in carried if v in NATIVE.get(e, set())}
    if not exo_native:
        reinforce_score = None  # exotic is element-agnostic, not applicable
    else:
        reinforce_score = 0.0
        for L in loops:
            if L["verb"] in exo_native:
                members = set(L["from"]) | set(L["to"])
                if members - exo_names:
                    reinforce_score = 1.0
                    break

    # 3) goal concentration: share of pick tag-weight sitting on the goal verbs
    tot = 0.0; on_goal = 0.0
    for cat, picks in b["build"].items():
        for ee in picks:
            tw = ee["item"].get("tagw") or {}
            tot += sum(tw.values())
            on_goal += sum(tw.get(gv, 0.0) for gv in gverbs)
    conc = (on_goal / tot) if tot else 0.0
    conc_score = min(conc / 0.40, 1.0)   # >=40% of weight on goal verbs reads as fully committed

    # 4) non-participating aspect/fragment picks (a signal, not a verdict: some passives
    #    are legitimately off-loop)
    af = []
    for cat in ("Aspect", "Fragment"):
        for ee in b["build"].get(cat, []):
            af.append(ee["item"])
    nonpart = 0
    for it in af:
        verbs = set(it.get("prod", []) or []) | set(it.get("cons", []) or [])
        if not (verbs & loop_verbs):
            nonpart += 1
    waste_score = 1.0 - (nonpart / len(af)) if af else 1.0

    # 5) exotic goal-fit vs the best LEGAL alternative (flags second-rate exotic picks).
    #    ties do not count against it; only a real gap below the max does.
    chosen_fit = max((it.get("tagw", {}).get(gv, 0.0) for it in exo for gv in gverbs), default=0.0)
    alt = legal_exotics(c, e)
    best_fit = max((it["tagw"].get(gv, 0.0) for it in alt for gv in gverbs), default=0.0)
    fit_score = (chosen_fit / best_fit) if best_fit > 0 else 1.0
    fit_gap = best_fit - chosen_fit

    # composite optimality proxy (reinforcement excluded when not applicable)
    parts = [("depth", depth_score, 0.25), ("concentration", conc_score, 0.20),
             ("low-waste", waste_score, 0.20), ("goal-fit", fit_score, 0.25)]
    if reinforce_score is not None:
        parts.append(("reinforcement", reinforce_score, 0.10))
    wsum = sum(w for _, _, w in parts)
    score = sum(s * w for _, s, w in parts) / wsum

    return dict(key=(c, e, g, p), score=score, depth=depth, conc=conc,
                nonpart=nonpart, af=len(af), fit_score=fit_score, fit_gap=fit_gap,
                reinforce=reinforce_score,
                exotic=(exo[0]["name"] if exo else None),
                comps={k: s for k, s, _ in parts})

def run_profile():
    rows = [profile_build(c, e, g, p) for c in CLASSES for e in ELEMS for g in GOALS for p in PLAYS]
    n = len(rows)
    mean = sum(r["score"] for r in rows) / n
    def avg(f): return sum(f(r) for r in rows) / n
    strong = sum(1 for r in rows if r["score"] >= 0.80)
    medium = sum(1 for r in rows if 0.60 <= r["score"] < 0.80)
    weak   = sum(1 for r in rows if r["score"] < 0.60)

    print("=== PART A: build-strength profile (optimality proxy, tag-derived) ===")
    print("builds: %d   mean strength: %.1f%%" % (n, 100 * mean))
    print("  strong (>=80%%): %d    medium (60-80%%): %d    weak (<60%%): %d" % (strong, medium, weak))
    print("  avg core-loop depth:         %.2f producers/consumers" % avg(lambda r: r["depth"]))
    print("  avg goal concentration:      %.1f%% of pick weight on goal verbs" % (100 * avg(lambda r: r["conc"])))
    print("  avg non-participating picks: %.2f of %.1f aspects+fragments per build"
          % (avg(lambda r: r["nonpart"]), avg(lambda r: r["af"])))
    print("  avg exotic goal-fit ratio:   %.1f%% of the best legal alternative" % (100 * avg(lambda r: r["fit_score"])))
    rein = [r for r in rows if r["reinforce"] is not None]
    if rein:
        print("  exotic reinforced (when applicable): %.1f%% of %d builds with an element-locked exotic"
              % (100 * sum(r["reinforce"] for r in rein) / len(rein), len(rein)))

    print()
    print("=== PART B: weakest builds to inspect (lowest strength first) ===")
    for r in sorted(rows, key=lambda r: r["score"])[:15]:
        c, e, g, p = r["key"]
        gap = (", exotic goal-fit gap %.2f" % r["fit_gap"]) if r["fit_gap"] >= 0.05 else ""
        print("  %4.0f%%  %-7s %-9s %-13s %-7s exotic=%-20s depth=%d nonpart=%d%s"
              % (100 * r["score"], c, e, g, p, str(r["exotic"])[:20], r["depth"], r["nonpart"], gap))
    # exotic goal-fit gaps worth a look (engine chose a notably lower-fit exotic)
    gaps = [r for r in rows if r["fit_gap"] >= 0.10]
    print()
    print("  exotic goal-fit gaps >= 0.10 (engine passed a higher-fit legal exotic): %d" % len(gaps))
    seen = set()
    for r in sorted(gaps, key=lambda r: -r["fit_gap"]):
        c, e, g, p = r["key"]
        sig = (c, e, g, r["exotic"])
        if sig in seen: continue
        seen.add(sig)
        print("    %-7s %-9s %-13s chose %-20s (fit gap %.2f)" % (c, e, g, str(r["exotic"])[:20], r["fit_gap"]))
        if len(seen) >= 12: break

def run_benchmark():
    print()
    print("=== PART C: external benchmark (community builds) ===")
    path = "build_benchmark.json"
    if not os.path.exists(path):
        print("  build_benchmark.json not found. This is the ONLY part that measures the")
        print("  engine against real builds. Create it (format below) and OWN the data.")
        print('  [{"label","inputs":{cls,element,goal,playstyle},')
        print('    "accept_exotic":[...],"avoid_exotic":[...],"expect_verbs":[...],"confidence":"high|med"}]')
        return
    bench = json.load(open(path, encoding="utf-8"))
    hits = 0; checked = 0
    print("  NOTE: results are only as trustworthy as this seed. Verify and expand it.")
    for entry in bench:
        i = entry["inputs"]
        b = app.construct(ans(i["cls"], i["element"], i["goal"], i["playstyle"]))
        exo = [x["item"]["name"] for x in b["build"].get("Exotic Armor", [])]
        chosen = exo[0] if exo else None
        accept = set(entry.get("accept_exotic", []))
        avoid = set(entry.get("avoid_exotic", []))
        ok = (chosen in accept) if accept else (chosen not in avoid)
        checked += 1; hits += 1 if ok else 0
        loop_verbs = {L["verb"] for L in b["synergy"]["loops"]}
        verbs_ok = all(v in loop_verbs for v in entry.get("expect_verbs", []))
        print("  [%s] %-28s chose %-22s exotic_ok=%s verbs_ok=%s (conf=%s)"
              % ("PASS" if ok and verbs_ok else "MISS", entry["label"][:28], str(chosen)[:22],
                 ok, verbs_ok, entry.get("confidence", "?")))
    if checked:
        print("  benchmark exotic-match: %.0f%% (%d/%d)  -- seed-limited, not a meta verdict"
              % (100 * hits / checked, hits, checked))

def main():
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    ver = ""
    for l in open("app.py", encoding="utf-8"):
        if l.startswith("APP_VERSION"):
            ver = l.split("=", 1)[1].strip().strip('"'); break
    print("HEAD: %s   APP_VERSION: %s" % (head, ver))
    print("Exhaustive: 450 combinations\n")
    run_profile()
    run_benchmark()
    print()
    print("READ THIS: Parts A and B are optimality PROXIES from the engine's own tags.")
    print("They flag shallow/scattered/low-fit builds for review. They cannot certify a")
    print("build is meta-optimal. Only Part C does that, and only with a seed you trust.")

if __name__ == "__main__":
    main()
