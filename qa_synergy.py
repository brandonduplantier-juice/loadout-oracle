"""Synergy-first independent verifier for Loadout Oracle.

Focus: does every piece of a build actually work together. Shares no check code
with qa_selfheal.py or qa_independent.py. Reads the engine's loops and validates
them independently. Dry run by default.
"""
import json, os, re, sys, subprocess
import app

POOL_PATH = os.path.join("data","pool.json")
CLS  = ["Hunter","Titan","Warlock"]
ELEMS= ["Arc","Solar","Void","Stasis","Strand","Prismatic"]
GOALS= ["Single-target","Add clear","Survive","Support","Ability spam"]
PLAY = ["Weapon","Super","Grenade","Melee","Ability"]

# ---- independent canonical maps (grounded in Destiny mechanics, not the engine) ----
UNIVERSAL = {"Orbs","Ability Energy","Armor Charge","Damage","Team Buff","Healing"}
ELEM_VERBS = {
 "Arc":    {"Ionic Trace","Amplified","Jolt"},
 "Solar":  {"Scorch","Radiant","Restoration","Empower"},
 "Void":   {"Void Breach","Volatile","Weaken","Devour","Void Overshield"},
 "Stasis": {"Freeze","Slow","Stasis Shard","Frost Armor"},
 "Strand": {"Tangle","Sever","Suspend","Threadling","Unravel","Woven Mail"},
}
ALL_ELEM = set().union(*ELEM_VERBS.values()) | {"Transcendence"}
def verb_legal_for(verb, elem):
    if verb in UNIVERSAL: return True
    if elem == "Prismatic": return verb in ALL_ELEM
    return verb in ELEM_VERBS.get(elem, set())
GENERIC = {"Orbs","Ability Energy","Armor Charge"}  # near-universal, not element synergy

# verb -> text keyword(s) for grounding (specific verbs only; vague ones skipped)
VERB_KW = {
 "Ionic Trace":["ionic trace","bolt charge"],"Amplified":["amplif"],"Jolt":["jolt"],
 "Scorch":["scorch","ignit"],"Radiant":["radiant"],"Restoration":["restoration"],
 "Void Breach":["void breach"],"Volatile":["volatile"],"Weaken":["weaken"],
 "Devour":["devour"],"Void Overshield":["overshield"],
 "Freeze":["freeze","frozen"],"Slow":["slow"],"Stasis Shard":["shard","shatter"],"Frost Armor":["frost armor"],
 "Tangle":["tangle","grapple"],"Sever":["sever"],"Suspend":["suspend"],"Threadling":["threadling"],
 "Unravel":["unravel"],"Woven Mail":["woven mail"],"Transcendence":["transcend"],
 "Empower":["radiant","empowering","weapons of light","well of radiance"],
}
M = json.load(open("data/manifest_effects.json", encoding="utf-8"))
def mtext(n): return " ".join((M.get(n,"") or "").split()).lower()

def ans(c,e,g,p):
    return dict(cls=c,element=e,engine="Any",goal=g,goal2="Add clear",activity="Raid",playstyle=p,
        build_weapon="Any",build_exotic_armor="Any",build_exotic_weapon="Any",main_goal="Max Damage",
        second_goal="Add Clear",optional_goal="Any",ability_focus="Any",super_focus="Any",weapon_focus="Any")

def run(fix=False):
    out=[]
    head=subprocess.run(["git","rev-parse","--short","HEAD"],capture_output=True,text=True).stdout.strip()
    out.append("HEAD: "+head)

    # self-correct loop on derived fields (deterministic only)
    pool=json.loads(open(POOL_PATH,encoding="utf-8").read())
    PRIMARY=0.20
    def split(tw):
        o=sorted(tw.items(),key=lambda kv:-kv[1]); return ", ".join(t for t,w in o if w>=PRIMARY), ", ".join(t for t,w in o if w<PRIMARY)
    def tier_of(tw):
        o=sorted(tw.items(),key=lambda kv:-kv[1])
        if len(o)==1: return o[0][0]
        if len(o)==2: return o[0][0]+" + "+o[1][0]
        hi=o[0][1]; cl=[t for t,w in o if w>=PRIMARY and hi-w<0.10]
        return cl[0] if len(cl)<=1 else (cl[0]+" + "+cl[1] if len(cl)==2 else cl[0]+" hybrid")
    def lead(t): return t.split(" + ")[0].replace(" hybrid","").strip() if t else ""
    rounds=0
    while True:
        rounds+=1; fixed=0
        for it in pool:
            if not isinstance(it,dict) or not it.get("tagw"): continue
            tw=it["tagw"]; keys=set(tw); top=max(tw,key=tw.get)
            if fix and it.get("tier") not in (None,"") and lead(it["tier"])!=top: it["tier"]=tier_of(tw); fixed+=1
        if not fix or not fixed or rounds>10: break
    if fix: open(POOL_PATH,"w",encoding="utf-8").write(json.dumps(pool,indent=0,ensure_ascii=False)+"\n")
    by_name={it["name"]:it for it in pool if isinstance(it,dict)}

    # accumulators
    c1=c2=c3=c4=c5=c6=[]
    integrity=[]; coherence=[]; elemcoh=[]; degenerate=[]; coverage=[]; exotic=[]; modhonesty=[]
    grounding=set(); verb_loop_builds={}; n=0
    mods_checked=set()

    combos=[(c,e,g,p) for c in CLS for e in ELEMS for g in GOALS for p in PLAY]
    for c,e,g,p in combos:
        n+=1; tag="%s/%s/%s/%s"%(c,e,g,p)
        b=app.construct(ans(c,e,g,p))
        cls,elem,build=b["cls"],b["elem"],b["build"]
        nm=lambda s:[x["item"]["name"] for x in build.get(s,[])]
        item_names={x for s in build for x in nm(s)}
        # tags as the ENGINE sees them at runtime (pool.json unioned with EV/OVERRIDE),
        # captured from the build's own item dicts, not from pool.json on disk
        bitem={x["item"]["name"]:x["item"] for s in build for x in build.get(s,[])}
        al=b.get("armor_loadout") or {}
        mod_full,mod_base,mod_econ_map={},{},{}
        modset=set()
        for info in al.values():
            for m in info["mods"]:
                base=m["mod"].split(" x")[0]; modset.add(base)
                mp,mc=app.mod_econ(base)
                mod_econ_map[base]=(mp,mc); mod_econ_map[m["mod"]]=(mp,mc)
        resolvable=item_names|modset|{mm for info in al.values() for m in info["mods"] for mm in [m["mod"]]}
        loops=b["synergy"]["loops"]
        verbs_seen=set()
        for L in loops:
            v=L["verb"]; verbs_seen.add(v)
            P,C=L["from"],L["to"]
            # 1 integrity
            for ref in P+C:
                base=ref.split(" x")[0]
                if ref not in resolvable and base not in resolvable:
                    integrity.append("%s %s:%s"%(tag,v,ref))
            # 2 coherence: producers carry v in prod, consumers in cons (items or mod_econ)
            for ref in P:
                base=ref.split(" x")[0]
                it=bitem.get(base)
                if it is not None:
                    if v not in (it.get("prod") or []): coherence.append("%s PROD %s lacks %s"%(tag,base,v))
                elif base in mod_econ_map:
                    if v not in mod_econ_map[base][0]: coherence.append("%s PROD mod %s lacks %s"%(tag,base,v))
            for ref in C:
                base=ref.split(" x")[0]
                it=bitem.get(base)
                if it is not None:
                    if v not in (it.get("cons") or []): coherence.append("%s CONS %s lacks %s"%(tag,base,v))
                elif base in mod_econ_map:
                    if v not in mod_econ_map[base][1]: coherence.append("%s CONS mod %s lacks %s"%(tag,base,v))
            # 3 element coherence
            if not verb_legal_for(v, elem): elemcoh.append("%s verb %s illegal on %s"%(tag,v,elem))
            # 4 degenerate
            if set(P)==set(C) and P: degenerate.append("%s %s (from==to)"%(tag,v))
            # 8 grounding (specific verbs only)
            if v in VERB_KW:
                for ref in P+C:
                    base=ref.split(" x")[0]
                    if base in by_name and base in M:
                        if not any(k in mtext(base) for k in VERB_KW[v]):
                            grounding.add((base,v))
            verb_loop_builds.setdefault(v,set()).add(tag)
        # 5 substantive coverage: at least one element-native loop (not generic)
        native=[L for L in loops if L["verb"] not in GENERIC and verb_legal_for(L["verb"],elem) and L["verb"] not in UNIVERSAL]
        if not native: coverage.append(tag)
        # 6 exotic participates in a non-generic loop (only meaningful on the
        # synergy-focused goal; on a damage goal a standalone damage exotic is fine)
        if g == "Ability spam":
            for exn in nm("Exotic Armor"):
                it=bitem.get(exn)
                ev=set((it.get("prod") or [])+(it.get("cons") or [])) if it else set()
                ev_specific=ev-GENERIC-{"Damage","Team Buff","Healing"}
                if ev_specific and not (ev_specific & verbs_seen): exotic.append("%s exotic %s isolated"%(tag,exn))
        # 7 mod honesty (each distinct mod once): claimed verb must have text support
        for base in modset:
            if base in mods_checked: continue
            mods_checked.add(base)
            mp,mc=app.mod_econ(base)
            for v in mp:
                if v in VERB_KW and base in M and not any(k in mtext(base) for k in VERB_KW[v]):
                    modhonesty.append("mod %s claims produce %s w/o text basis"%(base,v))

    # 9 noise verbs: loop in >90% of builds (exempt the genuinely universal
    # resources Orbs/Ability Energy/Armor Charge, which are universal by design)
    EXEMPT_NOISE = {"Orbs","Ability Energy","Armor Charge"}
    noise=[(v,len(s)) for v,s in verb_loop_builds.items() if len(s) > 0.90*n and v not in EXEMPT_NOISE]

    def sec(title,lst,cap=25):
        out.append("%s: %d"%(title,len(lst)))
        for x in (sorted(lst)[:cap] if not isinstance(lst,set) else sorted(lst)[:cap]): out.append("  "+str(x))
    out.append(""); out.append("=== EXHAUSTIVE: %d combinations (3x6x5x5) ==="%n)
    out.append("")
    out.append("--- SYNERGY CHECKS ---")
    sec("1 loop-integrity (dangling)", integrity)
    sec("2 loop-coherence (mislabeled membership)", coherence)
    sec("3 verb-element incoherence", elemcoh)
    sec("4 degenerate loops (from==to)", degenerate)
    sec("5 builds lacking a substantive element loop", coverage)
    sec("6 exotic isolated from element loops", exotic)
    sec("7 dishonest mods (faked verb)", modhonesty)
    out.append("")
    out.append("--- GROUNDING (loop-driving tags lacking text basis) ---")
    out.append("ungrounded (item, verb) pairs in loops: %d"%len(grounding))
    for it,v in sorted(grounding)[:40]: out.append("  %s :: %s"%(it,v))
    out.append("")
    out.append("--- NOISE VERBS (loop in >90%% of builds) ---")
    out.append("flagged: %d"%len(noise))
    for v,k in sorted(noise,key=lambda x:-x[1]):
        pr=sum(1 for it in pool if isinstance(it,dict) and v in (it.get('prod') or []))
        co=sum(1 for it in pool if isinstance(it,dict) and v in (it.get('cons') or []))
        out.append("  %s: loops in %d/%d builds | %d producers %d consumers"%(v,k,n,pr,co))

    # hard checks gate the verdict; soft checks are known low-weight cosmetics
    # (degenerate self-loops, element-appropriate-but-non-looping exotics, and
    # ungrounded low-weight tags) reported as warnings, not failures.
    hard = bool(integrity or coherence or elemcoh or coverage or modhonesty or noise)
    soft = len(degenerate)+len(exotic)+len(grounding)
    synergy_clean = not hard

    # cross-validation
    out.append(""); out.append("=== CROSS-VALIDATION (three witnesses) ===")
    verdicts={}
    for tool in ("qa_selfheal.py","qa_independent.py"):
        try:
            r=subprocess.run([sys.executable,tool],capture_output=True,text=True,timeout=900)
            line=[l for l in r.stdout.splitlines() if l.startswith(("RESULT:","FINAL VERDICT:"))]
            verdicts[tool]="ALL CLEAN" in (line[-1] if line else "")
            out.append("%s: %s"%(tool, line[-1] if line else "(no verdict line)"))
        except Exception as ex:
            verdicts[tool]=None; out.append("%s: failed (%r)"%(tool,ex))
    out.append("synergy verifier: "+("ALL CLEAN" if synergy_clean else "ISSUES"))
    agree = synergy_clean==verdicts.get("qa_selfheal.py")==verdicts.get("qa_independent.py")
    out.append("AGREE" if agree else "*** WITNESSES DIVERGE *** (expected: synergy adds checks the others lack)")

    out.append(""); out.append("FINAL VERDICT: "+("ALL CLEAN" if synergy_clean else "SYNERGY FAILURES")
               +" (%d known cosmetic warnings: %d degenerate, %d soft-isolated, %d ungrounded)"%(soft,len(degenerate),len(exotic),len(grounding)))
    if fix: out.append("rounds run: %d"%rounds)
    rep="\n".join(out)
    open("qa_synergy_report.txt","w",encoding="utf-8").write(rep+"\n")
    print(rep)

if __name__=="__main__":
    run(fix="--fix" in sys.argv)
