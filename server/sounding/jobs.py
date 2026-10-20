"""Background work: one worker thread so the GPU and Qwen see one job at a time."""
import hashlib, hmac, ipaddress, json, logging, os, re, socket, threading, time, traceback, urllib.parse, urllib.request
from . import db, media, ai

log = logging.getLogger("sounding")
_wake = threading.Event()


def enqueue(kind, ref, **args):
    db.insert("jobs", kind=kind, ref=ref, args=args, state="queued", created=db.now())
    _wake.set()


def queue_position(mid):
    rows = db.many("SELECT ref FROM jobs WHERE state IN ('queued','running') AND kind='process' ORDER BY id")
    refs = [r["ref"] for r in rows]
    return refs.index(mid) if mid in refs else None


def notify(user_id, kind, body, meeting_id=None, link=None):
    db.insert("notifications", id=db.new_id(12), user_id=user_id, kind=kind, meeting_id=meeting_id, body=body, link=link,
              created=db.now(), read=0)


def mdir(mid):
    return os.path.join(db.MEDIA, mid)


def set_stage(mid, stage, **kw):
    db.update("meetings", "id", mid, stage=stage, updated=db.now(), **kw)


def org_settings(org_id):
    return (db.one("SELECT settings FROM orgs WHERE id=?", org_id) or {}).get("settings") or {}


# ---------- steps ----------
def write_notes(m, template_key=None):
    """Chooses the template (explicit > meeting type > owner's default > general) and writes notes."""
    owner = db.one("SELECT * FROM users WHERE id=?", m["owner_id"])
    custom = None
    key = template_key or m.get("template")
    if not key and m.get("meeting_type_id"):
        mt = db.one("SELECT template FROM meeting_types WHERE id=?", m["meeting_type_id"])
        key = mt and mt["template"]
    key = key or (owner["settings"] or {}).get("default_template") or "general"
    if key.startswith("custom:"):
        custom = db.one("SELECT * FROM templates WHERE id=? AND org_id=?", key[7:], m["org_id"])
        if not custom:
            key, custom = "general", None
    spec = ai.template_spec(key, custom)
    extra = (owner["settings"] or {}).get("summary_instructions", "")
    dictionary = org_settings(m["org_id"]).get("dictionary", [])
    n = ai.clean_notes(ai.notes(m["transcript"], m["names"], spec, extra, dictionary), m["speakers"], m["duration"], spec)
    return key, spec, n


def run_trackers(m, trackers=None):
    trackers = trackers if trackers is not None else db.many("SELECT * FROM trackers WHERE org_id=?", m["org_id"])
    if not trackers or not m["transcript"]:
        return
    ids = [t["id"] for t in trackers]
    db.run(f"DELETE FROM tracker_hits WHERE meeting_id=? AND tracker_id IN ({','.join('?' * len(ids))})", m["id"], *ids)
    hits = []
    for t in trackers:
        if t["kind"] == "keyword":
            pats = [re.compile(r"\b" + re.escape(w) + r"\b", re.I) for w in t["terms"].get("keywords", []) if w.strip()]
            for tr in m["transcript"]:
                if any(p.search(tr["x"]) for p in pats):
                    hits.append((t["id"], tr["t"], tr["x"][:300]))
    hits += ai.find_tracker_hits(m["transcript"], [t for t in trackers if t["kind"] == "ai"], m["duration"])
    seen = set()
    for tid, t, q in hits:
        if (tid, t) in seen: continue
        seen.add((tid, t))
        db.run("INSERT OR IGNORE INTO tracker_hits (tracker_id, meeting_id, t, quote) VALUES (?,?,?,?)", tid, m["id"], t, q)
    for tid in {h[0] for h in hits}:
        tr = next(t for t in trackers if t["id"] == tid)
        for s in db.many("SELECT user_id FROM tracker_subs WHERE tracker_id=?", tid):
            notify(s["user_id"], "tracker", f"“{tr['name']}” came up in {m['title']}", m["id"], f"#/meeting/{m['id']}?tab=trackers")


def run_scorecards(m):
    if not m.get("meeting_type_id") or not m["transcript"]:
        return
    rep_label = next((lbl for lbl, uid in (m["speaker_users"] or {}).items() if uid == m["owner_id"]), None)
    rep_text = rep_label and (f"{rep_label} ({m['names'][rep_label]})" if m["names"].get(rep_label) else rep_label)
    for sc in db.many("SELECT * FROM scorecards WHERE meeting_type_id=?", m["meeting_type_id"]):
        total, details = ai.score(m["transcript"], m["names"], sc["criteria"], rep_text, m["duration"])
        db.run("INSERT OR REPLACE INTO scorecard_results (scorecard_id, meeting_id, user_id, score, details, created) VALUES (?,?,?,?,?,?)",
               sc["id"], m["id"], m["owner_id"], total, json.dumps(details), db.now())


def share_with_attendees(m):
    for email in m["attendees"]:
        u = db.one("SELECT id FROM users WHERE email=? AND org_id=? AND active=1", email, m["org_id"])
        if u and u["id"] != m["owner_id"]:
            db.run("INSERT OR IGNORE INTO meeting_shares (meeting_id, user_id) VALUES (?,?)", m["id"], u["id"])
            notify(u["id"], "shared", f"Notes are ready for {m['title']}, which you attended", m["id"], f"#/meeting/{m['id']}")


def process(mid):
    m = db.one("SELECT * FROM meetings WHERE id=?", mid)
    if not m: return
    work = mdir(mid)
    set_stage(mid, "converting")
    dur, has_video = media.prepare(f"{work}/{m['source']}", work)
    owner = db.one("SELECT email FROM users WHERE id=?", m["owner_id"])
    if owner and owner["email"].lower() == "demo@sounding.demo" and dur > 75 * 60:
        raise ValueError("Demo uploads can be up to 75 minutes long.")
    set_stage(mid, "speakers", duration=dur, has_video=int(has_video))
    # Speakers and transcription run in one child process (see media.run_speech); the stage shown is "speakers" until it returns.
    segs, words, diarizer, speech_t = media.run_speech(f"{work}/audio16k.wav", dur, work)
    set_stage(mid, "transcribing")
    turns = media.apply_dictionary(media.to_turns(words, segs), org_settings(m["org_id"]).get("dictionary", []))
    speakers = sorted({t["s"] for t in turns}, key=lambda s: int(s.split()[-1]))
    set_stage(mid, "summarizing", transcript=turns, speakers=speakers, metrics=media.metrics(turns, dur)); t2 = time.time()
    m = db.one("SELECT * FROM meetings WHERE id=?", mid)
    if turns and not m["meeting_type_id"]:
        types = db.many("SELECT * FROM meeting_types WHERE org_id=?", m["org_id"])
        mt = ai.classify(turns, types)
        if mt:
            db.update("meetings", "id", mid, meeting_type_id=mt); m["meeting_type_id"] = mt
    if turns:
        key, spec, n = write_notes(m)
    else:
        key, n = "general", dict(title="No speech found", overview="", sections=[], actions=[], names={})
    title = m["title"] if m["title_set"] else n["title"]
    names = {**n["names"], **(m["names"] or {})}
    set_stage(mid, "finishing", title=title, names=names, template=key,
              summary=dict(template=key, overview=n["overview"], sections=n["sections"]), actions=n["actions"])
    m = db.one("SELECT * FROM meetings WHERE id=?", mid)
    t3 = time.time()
    run_trackers(m); run_scorecards(m); share_with_attendees(m)
    set_stage(mid, "done", timings=dict(diarizer=diarizer, **speech_t, summarize=round(t3 - t2, 1),
                                        extras=round(time.time() - t3, 1)))
    os.remove(f"{work}/audio16k.wav")
    notify(m["owner_id"], "ready", f"Notes are ready for {title}", mid, f"#/meeting/{mid}")
    deliver_webhooks(db.one("SELECT * FROM meetings WHERE id=?", mid))


def resummarize(mid, template):
    m = db.one("SELECT * FROM meetings WHERE id=?", mid)
    key, spec, n = write_notes(m, template)
    # Keep action items people already assigned or ticked when the text still matches.
    old = {a["text"]: a for a in m["actions"]}
    acts = [{**a, **{k: old[a["text"]][k] for k in ("assignee", "done")}} if a["text"] in old else a for a in n["actions"]]
    db.update("meetings", "id", mid, template=key, summary=dict(template=key, overview=n["overview"], sections=n["sections"]),
              actions=acts, translations={}, updated=db.now())


# ---------- webhooks ----------
def safe_url(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        for fam, *_, sa in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80)):
            ip = ipaddress.ip_address(sa[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return False
    except socket.gaierror:
        return False
    return True


def meeting_payload(m, include=("transcript", "summary", "action_items")):
    names = m.get("names") or {}
    out = dict(id=m["id"], title=m["title"], date=m["date"], duration_seconds=m["duration"], url=f"/#/meeting/{m['id']}",
               attendees=m["attendees"], speakers=[dict(label=s, name=names.get(s)) for s in m["speakers"]])
    if "summary" in include and m.get("summary"):
        out["summary"] = m["summary"]
    if "action_items" in include:
        out["action_items"] = [dict(text=a["text"], done=a["done"], timestamp_seconds=a["t"]) for a in m["actions"]]
    if "transcript" in include:
        out["transcript"] = [dict(speaker=names.get(t["s"], t["s"]), start=t["t"], end=t["end"], text=t["x"]) for t in m["transcript"]]
    return out


def send_webhook(hook, payload):
    body = json.dumps(payload).encode()
    if not safe_url(hook["url"]):
        return "blocked: private or invalid address"
    sig = hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(hook["url"], body, {"Content-Type": "application/json", "X-Sounding-Signature": f"sha256={sig}",
                                                     "User-Agent": "Sounding-Webhooks/1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return str(r.status)
    except Exception as e:
        return f"error: {type(e).__name__}"


def deliver_webhooks(m):
    # Webhooks fire for meetings the hook owner can see: their own, and ones shared with them.
    from .auth import can_view
    for hook in db.many("SELECT * FROM webhooks"):
        u = db.one("SELECT * FROM users WHERE id=? AND active=1", hook["user_id"])
        if u and can_view(u, m):
            status = send_webhook(hook, dict(event="meeting.ready", meeting=meeting_payload(m, hook["include"])))
            db.update("webhooks", "id", hook["id"], last_status=f"{time.strftime('%Y-%m-%d %H:%M')} {status}")


# ---------- worker ----------
def run_job(j):
    a = j["args"]
    if j["kind"] == "process":
        try:
            process(j["ref"])
        except Exception as e:
            set_stage(j["ref"], "failed", error=f"{type(e).__name__}: {e}"[:300]); raise
    elif j["kind"] == "resummarize":
        resummarize(j["ref"], a.get("template"))
    elif j["kind"] == "tracker_backfill":
        t = db.one("SELECT * FROM trackers WHERE id=?", j["ref"])
        if t:
            for m in db.many("SELECT * FROM meetings WHERE org_id=? AND stage='done'", t["org_id"]):
                run_trackers(m, [t])
    elif j["kind"] == "rescore":
        m = db.one("SELECT * FROM meetings WHERE id=?", j["ref"])
        if m: run_scorecards(m)
    elif j["kind"] == "scorecard_backfill":
        sc = db.one("SELECT * FROM scorecards WHERE id=?", j["ref"])
        if sc:
            for m in db.many("SELECT * FROM meetings WHERE meeting_type_id=? AND stage='done'", sc["meeting_type_id"]):
                run_scorecards(m)


def worker():
    db.run("UPDATE jobs SET state='queued' WHERE state='running'")
    while True:
        j = db.one("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1")
        if not j:
            _wake.wait(5); _wake.clear(); continue
        db.update("jobs", "id", j["id"], state="running")
        try:
            run_job(j); db.update("jobs", "id", j["id"], state="done")
        except Exception as e:
            log.error("job %s %s failed:\n%s", j["kind"], j["ref"], traceback.format_exc())
            db.update("jobs", "id", j["id"], state="failed", error=f"{type(e).__name__}: {e}"[:300])


def start():
    threading.Thread(target=worker, daemon=True, name="jobs").start()
