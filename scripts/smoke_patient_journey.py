import json, urllib.request, urllib.error, sys
BASE = "http://127.0.0.1:10101"

def call(method, path, token=None, body=None, raw=False):
    req = urllib.request.Request(BASE+path, method=method)
    req.add_header("Content-Type", "application/json")
    if token: req.add_header("Authorization", "Bearer "+token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=120) as r:
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload or b"null"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")

def ok(label, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — '+str(extra)) if extra else ''}")
    return cond

results = []
print("STEP 1  patient identity (mock ABHA)")
s, otp = call("POST", "/mock-idp/abha/request-otp", body={"abha_address":"demo@abdm"})
results.append(ok("request-otp", s==200, f"otpSentTo={otp.get('otpSentTo')}"))
s, tok = call("POST", "/mock-idp/abha/verify-otp", body={"abha_address":"demo@abdm","otp":"123456"})
results.append(ok("verify-otp", s==200, f"abhaRef={tok.get('abhaRef')}"))
PT = tok["access_token"]

print("STEP 2  existing longitudinal record loads")
s, me = call("GET", "/api/v1/patients/me", PT)
results.append(ok("/patients/me", s==200 and me.get("known") is True,
                  f"{me.get('displayName')} counts={me.get('counts')}"))
PATIENT = me.get("patientRef")

print("STEP 3  new consultation")
s, sess = call("POST","/api/v1/sessions", PT,
               {"language":"en","consentScopes":["history","voice","documents"],"audioExplained":True})
results.append(ok("create session (consent gate)", s==201, sess.get("sessionRef")))
SESSION = sess["sessionRef"]

print("STEP 4  answer questions")
VOICE = []
answered = 0
for i in range(60):
    s, step = call("GET", f"/api/v1/sessions/{SESSION}/dialogue/next", PT)
    if s != 200 or step.get("complete") or not step.get("question"): break
    q = step["question"]
    # Options win whenever the question has them, whatever its declared kind: the ontology
    # offers option lists on free-text questions too (the lexicon maps phrases onto them).
    if q["kind"] == "multi_choice" and q["options"]:
        val = [q["options"][0]["value"]]
    elif q["options"]:
        val = q["options"][0]["value"]
    elif q["kind"] == "scale" and q.get("scale"):
        val = q["scale"]["max"]
    else:
        val = "burning pain in my stomach after eating"
    s, step = call("POST", f"/api/v1/sessions/{SESSION}/dialogue/answer", PT,
                   {"turnId":q["turnId"],"questionId":q["questionId"],"value":val,"modality":"touch"})
    if s != 200: print("     answer failed", s, step); break
    answered += 1
    if answered == 3:
        # A voice turn mid-interview, with NO confidence score — the honest Indic-locale case.
        s2, nx = call("GET", f"/api/v1/sessions/{SESSION}/dialogue/next", PT)
        vq = (nx or {}).get("question")
        if vq:
            s2, v = call("POST", f"/api/v1/sessions/{SESSION}/dialogue/answer/voice", PT,
                         {"turnId":vq["turnId"],"questionId":vq["questionId"],
                          "transcript":"it burns after I eat","confidence":None,"bargeIn":False})
            vo = (v or {}).get("voice", {})
            VOICE.append((s2==200, vo))
    if step.get("complete"): break
results.append(ok("walked the interview", answered > 5, f"{answered} questions answered"))

print("STEP 5  voice turn (client transcript + confidence policy)")
if VOICE:
    good, vo = VOICE[0]
    t = vo.get("transcript", {})
    results.append(ok("voice turn, confidence left UNMEASURED", good and t.get("confidenceStatus")=="unavailable",
                      f"status={t.get('confidenceStatus')} confidence={t.get('confidence')} degraded={vo.get('degradedToTouch')}"))
else:
    results.append(ok("voice turn", False, "never reached"))

print("STEP 6  document upload -> OCR pipeline")
import mimetypes, uuid, os
path = "data/fixtures/documents/prescription.pdf"
boundary = uuid.uuid4().hex
body = b"".join([
    f"--{boundary}\r\n".encode(),
    b'Content-Disposition: form-data; name="file"; filename="prescription.pdf"\r\n',
    b"Content-Type: application/pdf\r\n\r\n",
    open(path,"rb").read(), b"\r\n", f"--{boundary}--\r\n".encode()])
req = urllib.request.Request(BASE+f"/api/v1/sessions/{SESSION}/documents", method="POST", data=body)
req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
req.add_header("Authorization","Bearer "+PT)
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        up = json.loads(r.read())
    results.append(ok("upload + OCR", True,
        f"backend={up.get('backend')} facts={up.get('factsRecorded')} needsCheck={up.get('lowConfidenceCount')}"))
except urllib.error.HTTPError as e:
    results.append(ok("upload + OCR", False, e.read()[:200]))

print("STEP 7  patient read-back")
s, rev = call("GET", f"/api/v1/sessions/{SESSION}/dialogue/review", PT)
results.append(ok("patient sees own answers", s==200 and len(rev.get("answers",[]))>0,
                  f"{len(rev.get('answers',[]))} answers"))

json.dump({"session":SESSION,"patient":PATIENT,"patientToken":PT}, open("/tmp/mk_state.json","w"))
print()
print(f"{sum(results)}/{len(results)} patient-side steps passed")
sys.exit(0 if all(results) else 1)
