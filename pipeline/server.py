"""Sounding pipeline API. Audio -> Sortformer (speakers) + Parakeet (words) -> Qwen (notes, Q&A).
Binds 127.0.0.1 only; the UI reaches it through an SSH tunnel."""
import os, re, json, time, uuid, queue, shutil, threading, subprocess, urllib.request, logging, warnings
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import hmac, hashlib, base64
import uvicorn

warnings.filterwarnings("ignore")
S = "/root/sounding"; MODELS = S + "/models"; STORE = S + "/meetings"
QWEN = "http://127.0.0.1:8011/v1/chat/completions"
MAX_UPLOAD = 1024 ** 3
os.makedirs(STORE, exist_ok=True)
log = logging.getLogger("sounding"); logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# ---------- storage ----------
lock = threading.Lock()
def mdir(mid): return os.path.join(STORE, mid)
def load(mid):
    p = os.path.join(mdir(mid), "meeting.json")
    if not os.path.exists(p): raise HTTPException(404, "No meeting with that id")
    return json.load(open(p))
def save(m):
    with lock:
        p = os.path.join(mdir(m["id"]), "meeting.json")
        json.dump(m, open(p + ".tmp", "w")); os.replace(p + ".tmp", p)
def update(mid, **kw):
    m = load(mid); m.update(kw); save(m); return m

# ---------- models (loaded once, on first job) ----------
_models = {}
def models():
    if not _models:
        import torch
        from nemo.collections.asr.models import SortformerEncLabelModel, ASRModel
        logging.disable(logging.WARNING)
        d = SortformerEncLabelModel.restore_from(MODELS + "/diar_streaming_sortformer_4spk-v2.nemo", map_location="cuda").eval()
        sm = d.sortformer_modules  # model card "high latency" streaming config
        sm.chunk_len, sm.chunk_right_context, sm.fifo_len, sm.spkcache_update_period, sm.spkcache_len = 340, 40, 40, 300, 188
        a = ASRModel.restore_from(MODELS + "/parakeet-tdt-0.6b-v2.nemo", map_location="cuda").eval()
        a.change_attention_model("rel_pos_local_attn", [256, 256]); a.change_subsampling_conv_chunking_factor(1)
        logging.disable(logging.NOTSET)
        _models.update(diar=d, asr=a, torch=torch)
    return _models

# ---------- pipeline ----------
def ffmpeg(*args): subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)
def duration(p): return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]))

def diarize(wav):
    m = models(); d, torch = m["diar"], m["torch"]
    try:
        out = d.diarize(audio=wav, batch_size=1)[0]
    except torch.OutOfMemoryError:
        # Long recordings can outgrow the GPU memory Qwen leaves free; the CPU is slower but fine.
        torch.cuda.empty_cache(); log.info("diarization out of GPU memory, retrying on CPU")
        d.to("cpu")
        try: out = d.diarize(audio=wav, batch_size=1)[0]
        finally: d.to("cuda"); torch.cuda.empty_cache()
    segs = []
    for s in out:
        a, b, spk = s.split(); segs.append((float(a), float(b), spk))
    return sorted(segs)

def cut_points(segs, dur, target=150.0):
    """Split long audio for ASR at silences between speaker segments, about every 2.5 minutes,
    so each chunk fits in the GPU memory Qwen leaves free."""
    gaps, end = [], 0.0
    for a, b, _ in segs:
        if a > end + 0.3: gaps.append((end + a) / 2)
        end = max(end, b)
    cuts, pos = [0.0], 0.0
    while dur - pos > target * 1.3:
        want = pos + target
        near = [g for g in gaps if pos + 30 < g < pos + target * 1.25]
        pos = min(near, key=lambda g: abs(g - want)) if near else want
        cuts.append(pos)
    return cuts + [dur]

def asr_span(wav, a, b, work, depth=0):
    m = models(); torch = m["torch"]
    part = f"{work}/part.wav"; ffmpeg("-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", wav, "-ac", "1", "-ar", "16000", part)
    try:
        with torch.autocast("cuda", dtype=torch.bfloat16):
            o = m["asr"].transcribe([part], timestamps=True, batch_size=1, verbose=False)[0]
        return [(w["word"], w["start"] + a, w["end"] + a) for w in o.timestamp["word"]]
    except torch.OutOfMemoryError:
        torch.cuda.empty_cache()
        if depth >= 3 or b - a < 20: raise
        mid = (a + b) / 2  # halve the span and try again
        return asr_span(wav, a, mid, work, depth + 1) + asr_span(wav, mid, b, work, depth + 1)
    finally:
        if os.path.exists(part): os.remove(part)
        torch.cuda.empty_cache()

def transcribe(wav, segs, dur, work):
    cuts = cut_points(segs, dur); words = []
    for a, b in zip(cuts, cuts[1:]): words += asr_span(wav, a, b, work)
    return words

def to_turns(words, segs):
    names = {}
    turns = []
    last = "speaker_0"  # raw diarizer label of the previous word
    for w, a, b in words:
        best, ov = None, -1e9
        for s0, s1, spk in segs:
            if s1 < a - 1 or s0 > b + 1: continue
            o = min(b, s1) - max(a, s0)
            if o > ov: best, ov = spk, o
        last = best or last  # words outside every segment stay with the previous speaker
        spk = names.setdefault(last, f"Speaker {len(names) + 1}")
        if turns and turns[-1]["s"] == spk and a - turns[-1]["end"] < 1.5:
            turns[-1]["x"] += " " + w; turns[-1]["end"] = round(b, 2)
        else:
            turns.append(dict(t=round(a, 2), end=round(b, 2), s=spk, x=w))
    return turns

def clock(t): return f"{int(t) // 60}:{int(t) % 60:02d}"
def transcript_text(turns): return "\n".join(f"[{clock(t['t'])}] {t['s']}: {t['x']}" for t in turns)

def qwen(system, user, max_tokens=1500, as_json=True):
    body = dict(model="sounding-qwen", temperature=0.2, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                chat_template_kwargs={"enable_thinking": False})
    if as_json: body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(QWEN, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=600))["choices"][0]["message"]["content"]

NOTES = ("You write meeting notes from a speaker-labelled transcript produced by speech recognition, so expect some wrong words. "
         "Return only JSON with keys: title (under 8 words), overview (2-3 plain sentences), points (3-6 key points as strings), "
         "decisions (strings, may be empty), actions (objects with text, owner, at where owner is a speaker label exactly as written "
         "and at is the [m:ss] stamp of the line where it was agreed, copied from the transcript), names (object mapping a speaker label to a person's name or role, "
         "only when the transcript makes it clear). Do not invent facts.")
MAX_CHARS = 90000  # roughly 24k tokens, leaves room in the 32k context

def notes(turns):
    tx = transcript_text(turns)
    if len(tx) <= MAX_CHARS:
        return json.loads(qwen(NOTES, tx))
    parts, cur = [], ""
    for line in tx.splitlines(True):
        if len(cur) + len(line) > MAX_CHARS: parts.append(cur); cur = ""
        cur += line
    parts.append(cur)
    partial = [qwen(NOTES, f"Part {i + 1} of {len(parts)}:\n{p}") for i, p in enumerate(parts)]
    return json.loads(qwen(NOTES + " You are given notes for consecutive parts of one meeting; merge them into one set of notes.",
                           "\n\n".join(partial)))

def stamp(v, dur):
    m = re.fullmatch(r"\[?(\d+):(\d{2})\]?", str(v or "").strip())
    t = int(m[1]) * 60 + int(m[2]) if m else None
    return float(t) if t is not None and t <= dur else None

def clean_notes(n, speakers, dur):
    n = n or {}
    acts = []
    for i, a in enumerate(n.get("actions") or []):
        if isinstance(a, dict) and a.get("text"):
            acts.append(dict(id=f"a{i}", text=str(a["text"]), owner=a.get("owner") if a.get("owner") in speakers else None,
                             t=stamp(a.get("at"), dur), done=False))
    names = {k: str(v) for k, v in (n.get("names") or {}).items() if k in speakers and v}
    return dict(title=str(n.get("title") or "Untitled meeting"), overview=str(n.get("overview") or ""),
                points=[str(p) for p in n.get("points") or []], decisions=[str(p) for p in n.get("decisions") or []],
                actions=acts, names=names)

def run(mid):
    work = mdir(mid); src = load(mid)["source"]
    try:
        update(mid, stage="converting")
        wav = f"{work}/audio16k.wav"
        ffmpeg("-i", f"{work}/{src}", "-ac", "1", "-ar", "16000", wav)
        ffmpeg("-i", wav, "-c:a", "libmp3lame", "-b:a", "48k", f"{work}/audio.mp3")
        dur = duration(wav); update(mid, duration=dur, stage="speakers")
        t0 = time.time(); segs = diarize(wav); models()["torch"].cuda.empty_cache()
        update(mid, stage="transcribing"); t1 = time.time()
        words = transcribe(wav, segs, dur, work)
        turns = to_turns(words, segs)
        speakers = sorted({t["s"] for t in turns}, key=lambda s: int(s.split()[-1]))
        update(mid, transcript=turns, speakers=speakers, stage="summarizing"); t2 = time.time()
        n = clean_notes(notes(turns), speakers, dur) if turns else clean_notes({"title": "No speech found"}, [], dur)
        m = load(mid)
        if not m.get("title_set"): m["title"] = n.pop("title")
        else: n.pop("title")
        m["names"] = {**n.pop("names"), **m.get("names", {})}
        m.update(summary=dict(overview=n["overview"], points=n["points"], decisions=n["decisions"]), actions=n["actions"],
                 stage="done", timings=dict(speakers=round(t1 - t0, 1), transcribe=round(t2 - t1, 1), summarize=round(time.time() - t2, 1)))
        save(m); os.remove(wav)
        log.info("done %s in %.0fs", mid, time.time() - t0)
    except Exception as e:
        log.exception("failed %s", mid)
        update(mid, stage="failed", error=f"{type(e).__name__}: {e}"[:300])

jobs = queue.Queue()
def worker():
    while True:
        mid = jobs.get()
        try: run(mid)
        finally: jobs.task_done()
threading.Thread(target=worker, daemon=True).start()

# ---------- login ----------
# One shared password from the root-only env file. Requests that arrive through Apache (they carry
# X-Forwarded-For) must log in; direct 127.0.0.1 calls (SSH tunnel, health checks) are trusted.
PASSWORD = os.environ.get("SOUNDING_PASSWORD", "")
SECRET = os.environ.get("SOUNDING_SECRET", "").encode()
COOKIE, SESSION_DAYS = "sounding_session", 14
attempts = {}

def sign(exp): return hmac.new(SECRET, str(exp).encode(), hashlib.sha256).hexdigest()
def make_session():
    exp = int(time.time()) + SESSION_DAYS * 86400
    return base64.urlsafe_b64encode(f"{exp}.{sign(exp)}".encode()).decode()
def valid_session(v):
    try:
        exp, sig = base64.urlsafe_b64decode(v.encode()).decode().split(".")
        return int(exp) > time.time() and hmac.compare_digest(sig, sign(int(exp)))
    except Exception:
        return False

LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sounding</title><link href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;600;700&display=swap" rel="stylesheet">
<style>:root{--paper:#f4f6f5;--panel:#fff;--ink:#0e2a30;--ink2:#4a6168;--line:#dbe3e1;--teal:#0f6b73}
@media (prefers-color-scheme:dark){:root{--paper:#0b1a1e;--panel:#12262b;--ink:#e6f0ee;--ink2:#93aaa9;--line:#234047;--teal:#57c2c9}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--paper);color:var(--ink);font:15px/1.5 'Schibsted Grotesk',system-ui,sans-serif;padding:16px}
form{width:100%;max-width:340px;display:flex;flex-direction:column;gap:12px}h1{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}
p{margin:0;color:var(--ink2)}input,button{font:inherit;padding:11px 14px;border-radius:999px}input{border:1px solid var(--line);background:var(--panel);color:var(--ink)}
button{border:0;background:var(--teal);color:var(--paper);font-weight:600;cursor:pointer}.err{color:#b3261e}:focus-visible{outline:2px solid var(--teal);outline-offset:2px}</style></head>
<body><form method="post" action="/login"><h1>Sounding</h1><p>Enter the team password to see your meetings.</p>__ERR__
<label for="pw" style="position:absolute;left:-9999px">Password</label><input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
<button type="submit">Log in</button></form></body></html>"""

def from_proxy(req): return "x-forwarded-for" in req.headers
def client_ip(req): return req.headers.get("x-forwarded-for", "").split(",")[-1].strip() or req.client.host

# ---------- API ----------
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

@app.middleware("http")
async def require_login(req: Request, call_next):
    if not from_proxy(req) or req.url.path in ("/login", "/logout") or valid_session(req.cookies.get(COOKIE, "")):
        return await call_next(req)
    if req.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Log in to continue."}, status_code=401)
    return RedirectResponse("/login", status_code=303)

@app.get("/login")
def login_page(): return HTMLResponse(LOGIN_PAGE.replace("__ERR__", ""))

@app.post("/login")
async def login(req: Request, password: str = Form("")):
    ip, now = client_ip(req), time.time()
    recent = [t for t in attempts.get(ip, []) if now - t < 600]
    if len(recent) >= 10:
        return HTMLResponse(LOGIN_PAGE.replace("__ERR__", '<p class="err">Too many attempts. Try again in 10 minutes.</p>'), status_code=429)
    if not PASSWORD or not SECRET or not hmac.compare_digest(password.encode(), PASSWORD.encode()):
        attempts[ip] = recent + [now]
        return HTMLResponse(LOGIN_PAGE.replace("__ERR__", '<p class="err">That password is wrong.</p>'), status_code=401)
    attempts.pop(ip, None)
    r = RedirectResponse("/", status_code=303)
    # Secure-only when the proxy says the browser connection is HTTPS; the plain-HTTP port needs a normal cookie.
    https = req.headers.get("x-forwarded-proto") == "https"
    r.set_cookie(COOKIE, make_session(), max_age=SESSION_DAYS * 86400, httponly=True, secure=https, samesite="lax")
    return r

@app.get("/logout")
def logout():
    r = RedirectResponse("/login", status_code=303); r.delete_cookie(COOKIE); return r
LIST_KEYS = ("id", "title", "date", "duration", "stage", "error", "speakers", "names", "scope")

@app.get("/api/health")
def health():
    try: urllib.request.urlopen("http://127.0.0.1:8011/v1/models", timeout=3); q = True
    except Exception: q = False
    return dict(ok=True, qwen=q, models_loaded=bool(_models), queued=jobs.qsize())

@app.get("/api/meetings")
def list_meetings():
    out = []
    for mid in os.listdir(STORE):
        try: m = load(mid)
        except HTTPException: continue
        row = {k: m.get(k) for k in LIST_KEYS}
        row["open_actions"] = sum(1 for a in m.get("actions", []) if not a["done"])
        row["search"] = " ".join(t["x"] for t in m.get("transcript", []))
        out.append(row)
    return sorted(out, key=lambda r: r["date"], reverse=True)

@app.post("/api/meetings")
async def upload(file: UploadFile = File(...), title: str = Form("")):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".flac", ".aac", ".mov", ".mkv"}:
        raise HTTPException(400, "Upload an audio or video file: wav, mp3, m4a, mp4, webm, ogg, flac, aac, mov or mkv.")
    mid = uuid.uuid4().hex[:10]; os.makedirs(mdir(mid))
    size = 0
    with open(f"{mdir(mid)}/source{ext}", "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                f.close(); shutil.rmtree(mdir(mid)); raise HTTPException(413, "Files over 1 GB aren't supported.")
            f.write(chunk)
    title = title.strip()
    save(dict(id=mid, title=title or os.path.splitext(file.filename)[0], title_set=bool(title), source=f"source{ext}",
              date=time.strftime("%Y-%m-%dT%H:%M:%S"), stage="queued", scope="mine", names={}, speakers=[],
              transcript=[], actions=[], summary=None))
    jobs.put(mid)
    return load(mid)

@app.get("/api/meetings/{mid}")
def get_meeting(mid: str): return load(mid)

@app.get("/api/meetings/{mid}/audio")
def audio(mid: str):
    p = f"{mdir(load(mid)['id'])}/audio.mp3"
    if not os.path.exists(p): raise HTTPException(404, "Audio is still being prepared")
    return FileResponse(p, media_type="audio/mpeg")  # Starlette handles Range requests for seeking

@app.patch("/api/meetings/{mid}")
async def patch(mid: str, req: Request):
    body = await req.json(); m = load(mid)
    if "title" in body: m["title"] = str(body["title"])[:200]; m["title_set"] = True
    if "names" in body: m["names"] = {k: str(v)[:80] for k, v in body["names"].items() if k in m["speakers"] and str(v).strip()}
    if "done" in body:
        for a in m["actions"]:
            if a["id"] in body["done"]: a["done"] = bool(body["done"][a["id"]])
    save(m); return m

@app.delete("/api/meetings/{mid}")
def delete(mid: str):
    load(mid); shutil.rmtree(mdir(mid)); return {"deleted": mid}

@app.post("/api/meetings/{mid}/ask")
async def ask(mid: str, req: Request):
    q = str((await req.json()).get("question", "")).strip()[:500]
    m = load(mid)
    if not q: raise HTTPException(400, "Type a question first.")
    if m["stage"] != "done": raise HTTPException(409, "This meeting is still processing.")
    names = m.get("names", {})
    tx = transcript_text([{**t, "s": f"{t['s']} ({names[t['s']]})" if t["s"] in names else t["s"]} for t in m["transcript"]])[:MAX_CHARS]
    raw = qwen("Answer questions about this meeting transcript. Be brief and plain. After each claim cite the moment as [m:ss]. "
               "If the transcript doesn't answer the question, say so. Reply as JSON {\"answer\": string}.",
               f"{tx}\n\nQuestion: {q}", max_tokens=500)
    try: ans = json.loads(raw)["answer"]
    except Exception: ans = raw
    return {"answer": ans}

if os.path.isdir(S + "/web"):  # the built UI (npm run build), after the API routes
    app.mount("/", StaticFiles(directory=S + "/web", html=True), name="web")

if __name__ == "__main__":
    for mid in os.listdir(STORE):  # resume anything interrupted by a restart
        try:
            if load(mid)["stage"] not in ("done", "failed"): jobs.put(mid)
        except Exception: pass
    uvicorn.run(app, host="127.0.0.1", port=8020, log_level="warning")
