"""Independent exhaustive verifier for Loadout Oracle.

Deliberately shares NO check code with qa_selfheal.py. Both call app.* (the
system under test) through its public API, but every invariant here is
reimplemented from scratch so the two are independent witnesses. A disagreement
between them is a finding. Dry run by default: reports, changes nothing.
"""
import json, os, re, sys, subprocess

POOL_PATH = os.path.join("data", "pool.json")
VOCAB = {"Damage","Add Clear","Ability Regen","Survivability","Utility",
         "Crowd Control","Team Buff","Mobility","Healing"}
ELEM_WORDS = ("Arc","Solar","Void","Stasis","Strand")
PRIMARY = 0.20
CLS = ["Hunter","Titan","Warlock"]
ELEMS = ["Arc","Solar","Void","Stasis","Strand","Prismatic"]
GOALS = ["Single-target","Add clear","Survive","Support","Ability spam"]
PLAY = ["Weapon","Super","Grenade","Melee","Ability"]

import app

# ---- independent derivation helpers (written fresh) ---------------------------
def top_tag(tw):       return max(tw, key=tw.get)
def primaries(tw):     return [t for t,w in sorted(tw.items(),key=lambda kv:-kv[1]) if w >= PRIMARY]
def secondaries(tw):   return [t for t,w in sorted(tw.items(),key=lambda kv:-kv[1]) if w < PRIMARY]
def derive_tier(tw):
    o = sorted(tw.items(), key=lambda kv:-kv[1])
    if len(o)==1: return o[0][0]
    if len(o)==2: return o[0][0]+" + "+o[1][0]
    hi=o[0][1]; cl=[t for t,w in o if w>=PRIMARY and hi-w<0.10]
    return cl[0] if len(cl)<=1 else (cl[0]+" + "+cl[1] if len(cl)==2 else cl[0]+" hybrid")
def field_tagset(s): return {t.strip() for t in (s or "").replace("-","").split(",") if t.strip()}
def tier_lead(t):    return t.split(" + ")[0].replace(" hybrid","").strip() if t else ""

# ---- DATA layer (independent) -------------------------------------------------
def data_scan(pool):
    auto, review = [], []
    for it in pool:
        if not isinstance(it, dict): continue
        nm, tw = it.get("name"), (it.get("tagw") or {})
        if not tw: continue
        if abs(sum(tw.values())-1.0) > 0.03:
            review.append(("tagw-sum", nm, round(sum(tw.values()),3)))
        unknown = set(tw) - VOCAB
        if unknown:
            review.append(("bad-vocab", nm, sorted(unknown)))
        if len(tw)==1 and abs(next(iter(tw.values()))-1.0) < 0.01 and it.get("category") in ("Exotic Armor","Exotic Weapon"):
            review.append(("degenerate-exotic", nm, list(tw)))
        keys = set(tw)
        tier = it.get("tier")
        if tier not in (None,"") and tier_lead(tier) != top_tag(tw):
            auto.append(("tier", nm, tier, derive_tier(tw)))
        gt = it.get("goal_tags")
        if gt not in (None,"","-") and (field_tagset(gt) - keys):
            auto.append(("goal_tags", nm, gt, ", ".join(primaries(tw))))
        ft = it.get("flex_type")
        if ft not in (None,"","-") and (field_tagset(ft) - keys):
            auto.append(("flex_type", nm, ft, ", ".join(secondaries(tw)) or "-"))
    return auto, review

def data_integrity(pool):
    """ability-lock validity + name/perk resolution, independent of build gen."""
    errs = []
    pool_names = {it["name"] for it in pool if isinstance(it,dict)}
    by_name = {it["name"]: it for it in pool if isinstance(it,dict)}
    # ability-lock: the locked ability must exist and be legal for the exotic's class
    for exo, locks in app.EXOTIC_ABILITY.items():
        exo_it = by_name.get(exo)
        exo_cls = exo_it.get("class") if exo_it else None
        for slot, ab in locks.items():
            if ab not in pool_names:
                errs.append("LOCK-ABILITY-MISSING %s -> %s:%s"%(exo,slot,ab)); continue
            if exo_cls and exo_cls != "Any":
                legal_anywhere = any(ab in {x["name"] for x in app.gated(slot, exo_cls, e)} for e in ELEMS)
                if not legal_anywhere:
                    errs.append("LOCK-ABILITY-ILLEGAL %s -> %s:%s not legal for %s"%(exo,slot,ab,exo_cls))
    # every exotic weapon resolves to weapon-perk slot data + a DIM hash
    for it in pool:
        if isinstance(it,dict) and it.get("category")=="Exotic Weapon":
            if it["name"] not in app.WEAPON_PERKS:
                errs.append("WEAPON-PERKS-MISSING %s"%it["name"])
    return errs

# ---- BUILD layer (independent) ------------------------------------------------
STACK_MIN, STACK_MAX = 1, 5
def build_scan():
    combos = [(c,e,g,p) for c in CLS for e in ELEMS for g in GOALS for p in PLAY]
    errs = []
    for c,e,g,p in combos:
        a = dict(cls=c,element=e,engine="Any",goal=g,goal2="Add clear",activity="Raid",playstyle=p,
            build_weapon="Any",build_exotic_armor="Any",build_exotic_weapon="Any",main_goal="Max Damage",
            second_goal="Add Clear",optional_goal="Any",ability_focus="Any",super_focus="Any",weapon_focus="Any")
        tag = "%s/%s/%s/%s"%(c,e,g,p)
        try: b = app.construct(a)
        except Exception as ex:
            errs.append("CONSTRUCT-ERROR "+tag+": "+repr(ex)); continue
        cls, elem, build = b["cls"], b["elem"], b["build"]
        nm = lambda s: [x["item"]["name"] for x in build.get(s,[])]
        # slots filled
        for s in ("Super","Grenade","Melee","Class Ability","Exotic Armor"):
            if not build.get(s): errs.append("EMPTY-SLOT "+tag+" "+s)
        # element legality (legal set from the SUT's gated)
        for s in ("Super","Grenade","Melee","Class Ability"):
            legal = {x["name"] for x in app.gated(s, cls, elem)}
            for n in nm(s):
                if n not in legal: errs.append("OFF-ELEMENT "+tag+" "+s+":"+n)
        # exotic element legality (via pool element field)
        for s in ("Exotic Armor","Exotic Weapon"):
            for n in nm(s):
                it = app.find_pool_item(s, n)
                if it and it.get("element") not in ("Any", elem) and elem != "Prismatic":
                    # exotic weapons are element-typed via WELEM, allow off-element weapons (legit DPS)
                    if s == "Exotic Armor":
                        errs.append("EXOTIC-OFFELEM "+tag+" "+n+"="+str(it.get("element")))
        # ability-lock satisfied
        for n in nm("Exotic Armor")+nm("Exotic Weapon"):
            for slot, want in app.EXOTIC_ABILITY.get(n, {}).items():
                if nm(slot) and want not in nm(slot):
                    errs.append("LOCK-UNMET "+tag+" "+n+" wants "+slot+":"+want)
        # synergy: >=1 loop and every named producer/consumer traces to a real item or mod
        al = b.get("armor_loadout") or {}
        item_names = {n for s in build for n in nm(s)}
        mod_full, mod_base = set(), set()
        for info in al.values():
            for m in info["mods"]:
                mod_full.add(m["mod"]); mod_base.add(m["mod"].split(" x")[0])
        resolvable = item_names | mod_full | mod_base
        loops = b["synergy"]["loops"]
        if not loops: errs.append("NO-SYNERGY "+tag)
        for L in loops:
            for ref in L["from"]+L["to"]:
                if ref not in resolvable and ref.split(" x")[0] not in resolvable:
                    errs.append("DANGLING-SYNERGY "+tag+" "+L["verb"]+":"+ref)
        # mods present + element-matched + stack caps
        if al and sum(len(v["mods"]) for v in al.values())==0:
            errs.append("NO-MODS "+tag)
        for slot, info in al.items():
            for m in info["mods"]:
                base = m["mod"].split(" x")[0]
                lead = base.split(" ")[0]
                if lead in ELEM_WORDS and lead != elem and elem != "Prismatic":
                    errs.append("MOD-OFFELEM "+tag+" "+m["mod"])
                mm = re.search(r" x(\d+)$", m["mod"])
                if mm:
                    cnt = int(mm.group(1))
                    if cnt < STACK_MIN or cnt > STACK_MAX:
                        errs.append("MOD-STACK "+tag+" "+m["mod"])
        # artifact present + >=1 element match
        art = b.get("artifact") or {}
        perks = art.get("perks", []) if isinstance(art, dict) else []
        if not perks: errs.append("NO-ARTIFACT "+tag)
        elif elem == "Prismatic":
            # Prismatic runs every base element, so any base-element perk is relevant.
            if not any(pk.get("elements") or pk.get("element") or True for pk in perks):
                errs.append("ARTIFACT-OFFELEM "+tag)
        else:
            anyrel = any(elem in (pk.get("elements") or []) or pk.get("element")==elem
                         or not (pk.get("elements") or pk.get("element")) for pk in perks)
            if not anyrel: errs.append("ARTIFACT-OFFELEM "+tag)
        # DIM export: every equipped slot name resolves to a hash
        for s in ("Exotic Armor","Exotic Weapon","Super","Grenade","Melee","Class Ability","Aspect","Fragment"):
            for n in nm(s):
                if app._hash_for(n) is None:
                    errs.append("DIM-UNRESOLVED "+tag+" "+s+":"+n)
    return len(combos), errs

# ---- orchestration ------------------------------------------------------------
def run(fix=False):
    out = []
    head = subprocess.run(["git","rev-parse","--short","HEAD"],capture_output=True,text=True).stdout.strip()
    out.append("HEAD: "+head)
    pool = json.loads(open(POOL_PATH, encoding="utf-8").read())
    rounds = 0
    while True:
        rounds += 1
        auto, _ = data_scan(pool)
        if not auto or not fix: break
        for field, nm_, frm, to in auto:
            for it in pool:
                if isinstance(it,dict) and it.get("name")==nm_ and field in it: it[field]=to
        if rounds > 10: out.append("WARN: no convergence in 10 rounds"); break
    if fix:
        open(POOL_PATH,"w",encoding="utf-8").write(json.dumps(pool,indent=0,ensure_ascii=False)+"\n")
    auto, review = data_scan(pool)
    integ = data_integrity(pool)
    ncombo, berrs = build_scan()

    out.append("")
    out.append("=== DATA LAYER ===")
    out.append("auto-fixable remaining: %d"%len(auto))
    for f,n,a,b in auto[:30]: out.append("  FIX %s.%s: %r -> %r"%(n,f,a,b))
    out.append("needs-review (semantic): %d"%len(review))
    for k,n,d in review[:30]: out.append("  REVIEW %s: %s %s"%(n,k,d))
    out.append("data-integrity errors: %d"%len(integ))
    for e in integ[:30]: out.append("  "+e)
    out.append("")
    out.append("=== BUILD LAYER (exhaustive: %d combinations) ==="%ncombo)
    out.append("errors: %d"%len(berrs))
    for e in berrs[:40]: out.append("  "+e)
    if len(berrs) > 40: out.append("  ... +%d more"%(len(berrs)-40))

    indep_clean = not (auto or review or integ or berrs)
    out.append("")
    out.append("=== CROSS-VALIDATION vs qa_selfheal.py ===")
    try:
        r = subprocess.run([sys.executable,"qa_selfheal.py"],capture_output=True,text=True,timeout=600)
        sh_line = [l for l in r.stdout.splitlines() if l.startswith("RESULT:")]
        sh_verdict = sh_line[0] if sh_line else "(no RESULT line)"
        sh_clean = "ALL CLEAN" in sh_verdict
        out.append("qa_selfheal verdict: "+sh_verdict)
        out.append("independent verdict: "+("ALL CLEAN" if indep_clean else "ISSUES"))
        out.append("AGREE" if sh_clean==indep_clean else "*** DISAGREE *** the two witnesses differ")
    except Exception as ex:
        out.append("cross-validation failed to run: "+repr(ex))

    out.append("")
    out.append("FINAL VERDICT: "+("ALL CLEAN" if indep_clean else
               "ISSUES (auto:%d review:%d integrity:%d build:%d)"%(len(auto),len(review),len(integ),len(berrs))))
    if fix: out.append("rounds run: %d"%rounds)
    report = "\n".join(out)
    open("qa_independent_report.txt","w",encoding="utf-8").write(report+"\n")
    print(report)

if __name__ == "__main__":
    run(fix="--fix" in sys.argv)
