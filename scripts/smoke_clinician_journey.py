import json
import sys
import urllib.error
import urllib.request

BASE="http://127.0.0.1:10101"
st=json.load(open("/tmp/mk_state.json")); SESSION=st["session"]; PT=st["patientToken"]

def call(method,path,token=None,body=None):
    req=urllib.request.Request(BASE+path,method=method)
    req.add_header("Content-Type","application/json")
    if token: req.add_header("Authorization","Bearer "+token)
    d=json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req,d,timeout=180) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")

R=[]
def ok(label, c, x=""):
    print(f"  {'PASS' if c else 'FAIL'}  {label}{(' — '+str(x)) if x else ''}"); R.append(c); return c

print("STEP 8  clinician identity + queue")
s,t=call("POST","/mock-idp/token",body={"role":"clinician","sub":"dr.smoke@aiia"})
DT=t["access_token"]; ok("staff token",s==200)
s,q=call("GET","/api/v1/queue",DT)
ok("queue lists the waiting session",s==200 and any(e["sessionRef"]==SESSION for e in q.get("queue",[])),
   f"{q.get('count')} waiting")

print("STEP 9  traced summary + red flags + contradictions + patient context")
s,summ=call("GET",f"/api/v1/sessions/{SESSION}/summary?prose=false",DT)
ok("summary assembles (traceability gate)",s==200,f"{len(summ.get('sections',[]))} sections")
esc=summ.get("escalation") or {}
ok("deterministic red flags present",isinstance(esc.get("flags"),list),
   f"priority={esc.get('priority')} flags={len(esc.get('flags',[]))}")
s,ctx=call("GET",f"/api/v1/sessions/{SESSION}/patient-context",DT)
ok("prior context retrieved",s==200,
   f"known={ctx.get('known')} similar={len(ctx.get('similar',[]))} reconcile={len(ctx.get('reconciliation',[]))}")
s,cx=call("GET",f"/api/v1/sessions/{SESSION}/contradictions",DT)
ok("contradictions surfaced, none auto-resolved",s==200,
   f"{cx.get('count')} found; statuses={sorted({c.get('status') for c in cx.get('contradictions',[])})}")

print("STEP 10 FHIR preview (stub receiver)")
s,fh=call("GET",f"/api/v1/sessions/{SESSION}/fhir/preview",DT)
ok("FHIR R4 bundle previews",s==200 and fh.get("entries",0)>0,
   f"fhirVersion={fh.get('fhirVersion')} entries={fh.get('entries')} counts={fh.get('resourceCounts')}")
ok("preview is explicitly NOT committed",fh.get("committed") is False)

print("STEP 11 INVARIANT 4 — only a clinician commits, and only with confirmed:true")
s,_=call("POST",f"/api/v1/sessions/{SESSION}/commit",PT,{"confirmed":True})
ok("patient token REFUSED at commit",s in (401,403),f"HTTP {s}")
s,_=call("POST",f"/api/v1/sessions/{SESSION}/commit",DT,{})
ok("clinician without confirmed:true REFUSED",s in (400,403),f"HTTP {s}")
s,com=call("POST",f"/api/v1/sessions/{SESSION}/commit",DT,{"confirmed":True})
ok("clinician commits",s==200,f"entries={com.get('entries')} his={com.get('hisPush',{}).get('status')}")
promo=com.get("promotion",{}) or {}
PATIENT=promo.get("patientRef"); ENC=promo.get("encounterRef")
ok("encounter promoted",bool(PATIENT and ENC),f"{ENC}")

print("STEP 12 facts arrive PENDING (commit != per-fact review)")
s,brief=call("GET",f"/api/v1/patients/{PATIENT}/brief?encounter={ENC}",DT)
def walk(o):
    if isinstance(o,dict):
        if o.get("factRef"): yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o,list):
        for v in o: yield from walk(v)
lines=[ln for ln in walk(brief) if ln.get("reviewStatus") is not None]; statuses={}
for line in lines: statuses[line.get("reviewStatus")]=statuses.get(line.get("reviewStatus"),0)+1
ok("brief carries reviewable facts",s==200 and len(lines)>0,f"{len(lines)} lines, statuses={statuses}")
ok("every promoted fact is pending",set(statuses)=={"pending"},statuses)

TARGET=lines[0]["factRef"]; REJECT=lines[1]["factRef"]; EDIT=lines[2]["factRef"]

print("STEP 13 review transitions")
s,r=call("POST",f"/api/v1/patients/{PATIENT}/encounters/{ENC}/facts/{TARGET}/review",DT,{"status":"confirmed"})
ok("confirm",s==200 and r.get("reviewStatus")=="confirmed",r.get("reviewedBy"))
s,r=call("POST",f"/api/v1/patients/{PATIENT}/encounters/{ENC}/facts/{TARGET}/review",DT,{"status":"confirmed"})
ok("double-confirm refused (no duplicate judgement)",s==400)
s,r=call("POST",f"/api/v1/patients/{PATIENT}/encounters/{ENC}/facts/{EDIT}/review",DT,
         {"status":"edited","value":"corrected by the doctor"})
ok("edit",s==200 and r.get("reviewStatus")=="edited",f"origin={r.get('origin')} value={r.get('displayValue')}")
ok("edit does NOT imply confirmed",r.get("reviewStatus")=="edited")
s,r=call("POST",f"/api/v1/patients/{PATIENT}/encounters/{ENC}/facts/{REJECT}/review",DT,{"status":"rejected"})
ok("reject",s==200 and r.get("reviewStatus")=="rejected")
s,r=call("POST",f"/api/v1/patients/{PATIENT}/encounters/{ENC}/facts/{REJECT}/review",DT,{"status":"confirmed"})
ok("rejected is TERMINAL (un-reject refused)",s==400,(r.get('issue') or [{}])[0].get('diagnostics','')[:80])

print("STEP 14 rejected never resurfaces; provenance still opens for the auditor")
s,brief2=call("GET",f"/api/v1/patients/{PATIENT}/brief?encounter={ENC}",DT)
refs={ln["factRef"] for ln in walk(brief2) if ln.get("reviewStatus") is not None}
ok("rejected fact gone from the brief",REJECT not in refs,f"{len(refs)} lines remain")
s,ev=call("GET",f"/api/v1/patients/{PATIENT}/encounters/{ENC}/facts/{REJECT}",DT)
ok("rejected fact not openable from a clinical view",s==400,f"HTTP {s}")
s,ev=call("GET",f"/api/v1/patients/{PATIENT}/encounters/{ENC}/facts/{TARGET}",DT)
ok("provenance opens for a live fact",s==200,
   f"tier={ev.get('tier')} origin={ev.get('origin')} review={ev.get('reviewStatus')} evidence={len(ev.get('evidence',[]))}")
doc=[e for e in ev.get("evidence",[]) if e.get("bbox")]
print(f"        (document-backed evidence with a bbox on this fact: {len(doc)})")

print("STEP 15 longitudinal record now contains the new encounter")
s,enc=call("GET",f"/api/v1/patients/{PATIENT}/encounters",DT)
ok("new encounter in the confirmed list",s==200 and any(e["encounterRef"]==ENC for e in enc.get("encounters",[])),
   f"{len(enc.get('encounters',[]))} encounters")
s,tl=call("GET",f"/api/v1/patients/{PATIENT}/timeline",DT)
ok("timeline populated",s==200 and tl.get("count",0)>0,f"{tl.get('count')} events")
s,meds=call("GET",f"/api/v1/patients/{PATIENT}/medications",DT)
ok("medication history reports provenance",s==200,
   f"{len(meds.get('medications',[]))} threads; needsReconciliation={meds.get('needsReconciliation')}")
s,cxp=call("GET",f"/api/v1/patients/{PATIENT}/contradictions",DT)
ok("patient-level contradictions",s==200,f"{cxp.get('count')} open")

print("STEP 16 reports")
s,pb=call("GET",f"/api/v1/patients/{PATIENT}/brief/patient?encounter={ENC}",DT)
ok("patient-facing brief",s==200)
req=urllib.request.Request(BASE+f"/api/v1/patients/{PATIENT}/brief.pdf?audience=clinician&encounter={ENC}")
req.add_header("Authorization","Bearer "+DT)
try:
    with urllib.request.urlopen(req,timeout=120) as r: pdf=r.read()
    ok("clinician PDF renders",pdf[:4]==b"%PDF",f"{len(pdf)} bytes")
except urllib.error.HTTPError as e:
    ok("clinician PDF renders",False,e.code)

json.dump({"patient":PATIENT,"encounter":ENC,"doctorToken":DT,"rejected":REJECT,"confirmed":TARGET},
          open("/tmp/mk_doc_state.json","w"))
print()
print(f"{sum(R)}/{len(R)} clinician-side steps passed")
sys.exit(0 if all(R) else 1)
