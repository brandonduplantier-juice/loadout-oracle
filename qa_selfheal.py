"""Loadout Oracle QA self-heal loop.

  python qa_selfheal.py          # dry run: report all issues, change nothing
  python qa_selfheal.py --fix    # heal the auto-fixable layer in a loop until
                                 # stable, rewrite pool.json, then report the rest

AUTO-FIXABLE (deterministic, genuine breakage only):
  - tier whose LEAD tag is not the top tagw tag       -> rebuild tier from tagw
  - goal_tags / flex_type that REFERENCE a tag absent  -> rebuild from tagw
    from tagw (a stale leftover from a weight change)
  Convention drift (a 0.18 tag in goal_tags vs the 0.20 cutoff) and the literal
  "-" empty-placeholder are NOT errors and are left untouched.

NEEDS-REVIEW (semantic, never auto-written): tagw sum off, bad vocab, a single
  1.0 exotic stub, an off-element ability in a generated build, a broken
  ability-lock. These need judgment, so they are reported, not corrected.
"""
import json, os, sys

POOL = os.path.join("data", "pool.json")
VOCAB = {"Damage","Add Clear","Ability Regen","Survivability","Utility",
         "Crowd Control","Team Buff","Mobility","Healing"}
PRIMARY = 0.20

def split(tw):
    o = sorted(tw.items(), key=lambda kv: -kv[1])
    return ", ".join(t for t,w in o if w>=PRIMARY), ", ".join(t for t,w in o if w<PRIMARY)

def tier_of(tw):
    o = sorted(tw.items(), key=lambda kv: -kv[1])
    if not o: return ""
    if len(o)==1: return o[0][0]
    if len(o)==2: return "%s + %s"%(o[0][0],o[1][0])
    t1=o[0][1]; prim=[t for t,w in o if w>=PRIMARY and (t1-w)<0.10]
    if len(prim)<=1: return o[0][0]
    if len(prim)==2: return "%s + %s"%(prim[0],prim[1])
    return "%s hybrid"%prim[0]

def _tagset(s):
    return {t.strip() for t in (s or "").replace("-","").split(",") if t.strip()}

def _lead(tier):
    # the lead tag is everything before " + " or " hybrid"
    return tier.split(" + ")[0].replace(" hybrid","").strip() if tier else ""

# ---- data-layer checks --------------------------------------------------------
def check_data(pool):
    auto, review = [], []
    for it in pool:
        if not isinstance(it, dict): continue
        nm = it.get("name"); tw = it.get("tagw") or {}
        if not tw: continue
        keys = set(tw)
        top = max(tw, key=tw.get)
        # ---- semantic (review only) ----
        if abs(sum(tw.values())-1.0) > 0.03:
            review.append({"name":nm,"kind":"tagw-sum","detail":round(sum(tw.values()),3)})
        if keys - VOCAB:
            review.append({"name":nm,"kind":"bad-vocab","detail":sorted(keys-VOCAB)})
        if len(tw)==1 and abs(list(tw.values())[0]-1.0)<0.01 and it.get("category") in ("Exotic Armor","Exotic Weapon"):
            review.append({"name":nm,"kind":"degenerate-exotic","detail":list(tw)})
        # ---- auto-fixable (genuine breakage only) ----
        tier = it.get("tier")
        if tier not in (None,"") and _lead(tier) != top:
            auto.append({"name":nm,"field":"tier","from":tier,"to":tier_of(tw)})
        gt = it.get("goal_tags")
        if gt not in (None,"","-") and (_tagset(gt) - keys):
            auto.append({"name":nm,"field":"goal_tags","from":gt,"to":split(tw)[0],
                         "why":"references "+",".join(sorted(_tagset(gt)-keys))})
        ft = it.get("flex_type")
        if ft not in (None,"","-") and (_tagset(ft) - keys):
            auto.append({"name":nm,"field":"flex_type","from":ft,"to":(split(tw)[1] or "-"),
                         "why":"references "+",".join(sorted(_tagset(ft)-keys))})
    return auto, review

def apply_autofix(pool):
    n=0
    for it in pool:
        if not isinstance(it, dict): continue
        tw = it.get("tagw") or {}
        if not tw: continue
        keys=set(tw); top=max(tw,key=tw.get)
        if it.get("tier") not in (None,"") and _lead(it["tier"])!=top:
            it["tier"]=tier_of(tw); n+=1
        if it.get("goal_tags") not in (None,"","-") and (_tagset(it["goal_tags"])-keys):
            it["goal_tags"]=split(tw)[0]; n+=1
        if it.get("flex_type") not in (None,"","-") and (_tagset(it["flex_type"])-keys):
            it["flex_type"]=split(tw)[1] or "-"; n+=1
    return n

# ---- build-layer checks (semantic; report only) -------------------------------
def check_builds():
    import app, random
    CLS=["Hunter","Titan","Warlock"]; ELEMS=["Arc","Solar","Void","Stasis","Strand","Prismatic"]
    GOALS=["Single-target","Add clear","Survive","Support","Ability spam"]; PLAY=["Weapon","Super","Grenade","Melee","Ability"]
    combos=[(c,e,g,p) for c in CLS for e in ELEMS for g in GOALS for p in PLAY]
    random.seed(1); random.shuffle(combos); combos=combos[:60]
    errs=[]; n=0
    for c,e,g,p in combos:
        n+=1
        a=dict(cls=c,element=e,engine="Any",goal=g,goal2="Add clear",activity="Raid",playstyle=p,
            build_weapon="Any",build_exotic_armor="Any",build_exotic_weapon="Any",main_goal="Max Damage",
            second_goal="Add Clear",optional_goal="Any",ability_focus="Any",super_focus="Any",weapon_focus="Any")
        try: b=app.construct(a)
        except Exception as ex:
            errs.append("CONSTRUCT-ERROR %s %s %s/%s: %r"%(c,e,g,p,ex)); continue
        cls,elem=b["cls"],b["elem"]; build=b["build"]
        def names(s): return [x["item"]["name"] for x in build.get(s,[])]
        for s in ["Super","Grenade","Melee","Class Ability","Exotic Armor"]:
            if not build.get(s): errs.append("EMPTY-SLOT %s %s %s"%(cls,elem,s))
        for s in ["Super","Grenade","Melee"]:
            lg={x["name"] for x in app.gated(s,cls,elem)}
            for nm in names(s):
                if nm not in lg: errs.append("OFF-ELEMENT %s %s %s:%s"%(cls,elem,s,nm))
        for nm in names("Exotic Armor")+names("Exotic Weapon"):
            if nm in app.EXOTIC_ABILITY:
                for slot,want in app.EXOTIC_ABILITY[nm].items():
                    if names(slot) and want not in names(slot):
                        errs.append("LOCK-MISMATCH %s wants %s:%s"%(nm,slot,want))
        if not b["synergy"]["loops"]:
            errs.append("NO-SYNERGY %s %s %s/%s"%(cls,elem,g,p))
    return n, errs

# ---- the loop -----------------------------------------------------------------
def main():
    fix = "--fix" in sys.argv
    pool = json.loads(open(POOL, encoding="utf-8").read())
    rounds = 0
    while True:
        rounds += 1
        auto, _ = check_data(pool)
        if not auto or not fix: break
        apply_autofix(pool)
        if rounds > 10:
            print("WARN: did not converge in 10 rounds"); break
    if fix:
        open(POOL,"w",encoding="utf-8").write(json.dumps(pool,indent=0,ensure_ascii=False)+"\n")
    auto, review = check_data(pool)
    nbuilds, berrs = check_builds()

    print("=== DATA LAYER ===")
    print("auto-fixable remaining: %d %s" % (len(auto), "(run --fix to heal)" if (auto and not fix) else ""))
    for a in auto[:25]: print("  FIX %s.%s: %r -> %r  %s" % (a["name"],a["field"],a["from"],a["to"],a.get("why","")))
    print("needs-review (semantic, not auto-fixed): %d" % len(review))
    for r in review[:25]: print("  REVIEW %s: %s %s" % (r["name"],r["kind"],r["detail"]))
    print()
    print("=== BUILD LAYER (%d generated) ===" % nbuilds)
    print("errors: %d" % len(berrs))
    for e in berrs[:25]: print("  %s" % e)
    print()
    ok = (not auto) and (not review) and (not berrs)
    print("RESULT:", "ALL CLEAN" if ok else "review:%d auto:%d build:%d" % (len(review),len(auto),len(berrs)))
    if fix: print("rounds run: %d" % rounds)

if __name__ == "__main__":
    main()
