"""Meetings: upload, list, read, edit, media, downloads and the per-meeting AI actions."""
import os, re, shutil, time
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from .. import db, auth, jobs, ai, media

router = APIRouter()
MAX_UPLOAD = 2 * 1024 ** 3
DEMO_MAX_BYTES, DEMO_DAILY, DEMO_MAX_MINUTES = 300 * 1024 ** 2, 6, 75
EXTS = {".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".flac", ".aac", ".mov", ".mkv", ".opus"}
CONSUMER = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "icloud.com", "me.com",
            "aol.com", "proton.me", "protonmail.com", "gmx.com", "yandex.com", "mail.com"}


def org_domains(org_id):
    return {r["email"].split("@")[-1].lower() for r in db.many("SELECT email FROM users WHERE org_id=?", org_id)}


def companies_of(m, own):
    return sorted({a.split("@")[-1].lower() for a in m["attendees"] if "@" in a} - own - CONSUMER)


def display(m, label):
    return (m.get("names") or {}).get(label) or label


def lookup(org_id):
    users = {r["id"]: r["name"] for r in db.many("SELECT id, name FROM users WHERE org_id=?", org_id)}
    teams = {r["id"]: r["name"] for r in db.many("SELECT id, name FROM teams WHERE org_id=?", org_id)}
    types = {r["id"]: r["name"] for r in db.many("SELECT id, name FROM meeting_types WHERE org_id=?", org_id)}
    return users, teams, types


def list_row(m, users, teams, types, own, u):
    s = m.get("summary") or {}
    return dict(id=m["id"], title=m["title"], date=m["date"], duration=m["duration"], stage=m["stage"], error=m["error"],
                owner=dict(id=m["owner_id"], name=users.get(m["owner_id"], "Former member")), mine=m["owner_id"] == u["id"],
                visibility=m["visibility"], team=m["team_id"] and dict(id=m["team_id"], name=teams.get(m["team_id"])),
                meeting_type=m["meeting_type_id"] and dict(id=m["meeting_type_id"], name=types.get(m["meeting_type_id"])),
                speakers=[display(m, x) for x in m["speakers"]], has_video=bool(m["has_video"]),
                overview=(s.get("overview") or "")[:280], companies=companies_of(m, own),
                open_actions=sum(1 for a in m["actions"] if not a["done"]),
                queue=jobs.queue_position(m["id"]) if m["stage"] == "queued" else None)


LIST_COLS = "m.id, m.org_id, m.owner_id, m.title, m.date, m.duration, m.stage, m.error, m.visibility, m.team_id, m.meeting_type_id, " \
            "m.speakers, m.names, m.has_video, m.summary, m.attendees, m.actions"


@router.get("/api/meetings")
def list_meetings(req: Request, scope: str = "all", team_id: str = "", type_id: str = "", company: str = "", limit: int = 200):
    u = auth.current_user(req)
    cond, args = auth.visible_sql(u)
    if scope == "mine":
        cond += " AND m.owner_id=?"; args.append(u["id"])
    elif scope == "shared":
        cond += " AND m.owner_id<>? AND EXISTS (SELECT 1 FROM meeting_shares s WHERE s.meeting_id=m.id AND s.user_id=?)"; args += [u["id"], u["id"]]
    elif scope == "team":
        cond += " AND m.visibility IN ('team','org')"
    if team_id:
        cond += " AND m.team_id=?"; args.append(team_id)
    if type_id:
        cond += " AND m.meeting_type_id=?"; args.append(type_id)
    if company:
        cond += " AND m.attendees LIKE ?"; args.append(f"%@{company}%")
    rows = db.many(f"SELECT {LIST_COLS} FROM meetings m WHERE {cond} ORDER BY m.date DESC LIMIT ?", *args, min(limit, 500))
    users, teams, types = lookup(u["org_id"]); own = org_domains(u["org_id"])
    return [list_row(m, users, teams, types, own, u) for m in rows]


@router.post("/api/meetings")
async def upload(req: Request, file: UploadFile = File(...), title: str = Form(""), date: str = Form(""), visibility: str = Form("private"),
                 team_id: str = Form(""), attendees: str = Form(""), meeting_type_id: str = Form(""), template: str = Form("")):
    u = auth.current_user(req)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in EXTS:
        raise HTTPException(400, "Upload an audio or video file: " + ", ".join(sorted(e[1:] for e in EXTS)) + ".")
    limit = MAX_UPLOAD
    if auth.is_demo(u):
        seeded = set(open(DEMO_SNAPSHOT).read().split()) if os.path.exists(DEMO_SNAPSHOT) else set()
        recent = sum(1 for r in db.many("SELECT id FROM meetings WHERE owner_id=? AND created>?", u["id"], time.time() - 86400)
                     if r["id"] not in seeded)  # the seeded sample meetings don't count against the limit
        if recent >= DEMO_DAILY:
            raise HTTPException(429, f"The demo workspace takes {DEMO_DAILY} uploads a day. Try again tomorrow.")
        limit = DEMO_MAX_BYTES
    vis, team = check_visibility(u, visibility, team_id)
    mt = meeting_type_id if meeting_type_id and db.one("SELECT 1 AS x FROM meeting_types WHERE id=? AND org_id=?", meeting_type_id, u["org_id"]) else None
    mid = db.new_id(); work = jobs.mdir(mid); os.makedirs(work)
    size = 0
    with open(f"{work}/source{ext}", "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                f.close(); shutil.rmtree(work)
                raise HTTPException(413, "Files over 2 GB aren't supported." if limit == MAX_UPLOAD else "Demo uploads can be up to 300 MB.")
            f.write(chunk)
    title = title.strip()
    when = date.strip() if re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?", date.strip()) else time.strftime("%Y-%m-%dT%H:%M:%S")
    db.insert("meetings", id=mid, org_id=u["org_id"], owner_id=u["id"], title=title or os.path.splitext(file.filename)[0][:120],
              title_set=int(bool(title)), date=when, stage="queued", source=f"source{ext}", visibility=vis, team_id=team,
              meeting_type_id=mt, template=template or None, attendees=parse_emails(attendees), created=db.now(), updated=db.now())
    jobs.enqueue("process", mid)
    return {"id": mid}


def parse_emails(s):
    return sorted({e.strip().lower() for e in re.split(r"[,;\s]+", s or "") if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e.strip().lower())})


def check_visibility(u, vis, team_id):
    if vis not in ("private", "team", "org"):
        vis = "private"
    if vis == "team":
        if not team_id or (team_id not in auth.team_ids(u["id"]) and u["role"] != "admin"):
            raise HTTPException(400, "Pick one of your teams to share with.")
        if not db.one("SELECT 1 AS x FROM teams WHERE id=? AND org_id=?", team_id, u["org_id"]):
            raise HTTPException(404, "No such team.")
        return vis, team_id
    return vis, None


@router.get("/api/meetings/{mid}")
def get_meeting(mid: str, req: Request):
    u = auth.current_user(req)
    m = auth.meeting_for(u, mid)
    users, teams, types = lookup(u["org_id"])
    m.pop("source", None)
    m["owner"] = dict(id=m["owner_id"], name=users.get(m["owner_id"], "Former member"))
    m["team"] = m["team_id"] and dict(id=m["team_id"], name=teams.get(m["team_id"]))
    m["meeting_type"] = m["meeting_type_id"] and dict(id=m["meeting_type_id"], name=types.get(m["meeting_type_id"]))
    m["can_edit"] = auth.can_edit(u, m)
    m["companies"] = companies_of(m, org_domains(u["org_id"]))
    m["queue"] = jobs.queue_position(mid) if m["stage"] == "queued" else None
    m["pending"] = [j["kind"] for j in db.many("SELECT kind FROM jobs WHERE ref=? AND state IN ('queued','running')", mid)]
    m["folders"] = [r["folder_id"] for r in db.many("SELECT folder_id FROM folder_items WHERE meeting_id=?", mid)]
    m["shares"] = db.many("SELECT u.id, u.name, u.email FROM meeting_shares s JOIN users u ON u.id=s.user_id WHERE s.meeting_id=?", mid)
    m["scorecards"] = db.many("SELECT r.*, s.name FROM scorecard_results r JOIN scorecards s ON s.id=r.scorecard_id WHERE r.meeting_id=?", mid)
    m["tracker_hits"] = db.many("SELECT h.tracker_id, h.t, h.quote, t.name FROM tracker_hits h JOIN trackers t ON t.id=h.tracker_id "
                                "WHERE h.meeting_id=? ORDER BY h.t", mid)
    return m


class MeetingPatch(BaseModel):
    title: str | None = None
    date: str | None = None
    visibility: str | None = None
    team_id: str | None = None
    attendees: list[str] | None = None
    names: dict | None = None
    speaker_users: dict | None = None
    meeting_type_id: str | None = None
    actions: list[dict] | None = None  # [{id, done?, assignee?}]


@router.patch("/api/meetings/{mid}")
def patch_meeting(mid: str, body: MeetingPatch, req: Request):
    u = auth.current_user(req)
    m = auth.meeting_for(u, mid)
    editor = auth.can_edit(u, m)
    vals = {}
    edits = body.model_dump(exclude_none=True)
    if set(edits) - {"actions"} and not editor:
        raise HTTPException(403, "Only the meeting owner or an admin can change this meeting.")
    if "title" in edits and body.title.strip():
        vals.update(title=body.title.strip()[:200], title_set=1)
    if "date" in edits and re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?", body.date):
        vals["date"] = body.date
    if "visibility" in edits:
        vals["visibility"], vals["team_id"] = check_visibility(u, body.visibility, body.team_id)
    if "attendees" in edits:
        vals["attendees"] = parse_emails(" ".join(body.attendees))
    if "names" in edits:
        vals["names"] = {k: str(v).strip()[:80] for k, v in body.names.items() if k in m["speakers"] and str(v or "").strip()}
    if "speaker_users" in edits:
        valid = {r["id"] for r in db.many("SELECT id FROM users WHERE org_id=?", u["org_id"])}
        vals["speaker_users"] = {k: v for k, v in body.speaker_users.items() if k in m["speakers"] and v in valid}
        people = {r["id"]: r["name"] for r in db.many("SELECT id, name FROM users WHERE org_id=?", u["org_id"])}
        # A speaker linked to a teammate takes their name unless someone typed a different one.
        linked = {k: people[v] for k, v in vals["speaker_users"].items() if k not in (body.names or {})}
        vals["names"] = {**(vals.get("names") or m["names"]), **linked}  # a name typed in the same edit wins
    if "meeting_type_id" in edits:
        mt = body.meeting_type_id or None
        if mt and not db.one("SELECT 1 AS x FROM meeting_types WHERE id=? AND org_id=?", mt, u["org_id"]):
            raise HTTPException(404, "No such meeting type.")
        vals["meeting_type_id"] = mt
    if "actions" in edits:
        acts = {a["id"]: a for a in m["actions"]}
        valid = {r["id"] for r in db.many("SELECT id FROM users WHERE org_id=?", u["org_id"])}
        for ch in body.actions:
            a = acts.get(ch.get("id"))
            if not a: continue
            if "done" in ch: a["done"] = bool(ch["done"])
            if editor and "text" in ch and str(ch["text"]).strip(): a["text"] = str(ch["text"]).strip()[:500]
            if editor and "assignee" in ch:
                new = ch["assignee"] if ch["assignee"] in valid else None
                if new and new != a.get("assignee") and new != u["id"]:
                    jobs.notify(new, "assigned", f"{u['name']} assigned you: {a['text']}", mid, f"#/meeting/{mid}?tab=actions")
                a["assignee"] = new
        if editor and any(ch.get("new") for ch in body.actions):
            for ch in body.actions:
                if ch.get("new") and str(ch.get("text", "")).strip():
                    nid = f"a{len(acts) + 1}_{db.new_id(4)}"
                    acts[nid] = dict(id=nid, text=str(ch["text"]).strip()[:500], speaker=None, assignee=ch.get("assignee") if ch.get("assignee") in valid else None,
                                     t=ch.get("t"), done=False)
        vals["actions"] = list(acts.values())
    db.update("meetings", "id", mid, updated=db.now(), **vals)
    if "meeting_type_id" in vals and vals["meeting_type_id"] != m["meeting_type_id"]:
        db.run("DELETE FROM scorecard_results WHERE meeting_id=?", mid)  # old type's scorecards no longer apply
        if vals["meeting_type_id"]:
            jobs.enqueue("rescore", mid)
    elif "speaker_users" in vals and m["meeting_type_id"]:
        jobs.enqueue("rescore", mid)
    return get_meeting(mid, req)


DEMO_SNAPSHOT = os.path.join(db.ROOT, "demo-snapshot", "meetings.txt")


def demo_owns(u, m):
    """The demo account owns the seeded meetings so they show under My calls, but can't delete or trim them."""
    if not auth.is_demo(u):
        return
    seeded = set(open(DEMO_SNAPSHOT).read().split()) if os.path.exists(DEMO_SNAPSHOT) else set()
    if m["owner_id"] != u["id"] or m["id"] in seeded:
        raise HTTPException(403, "The demo workspace can't delete or trim the sample meetings. Upload your own recording to try it.")


@router.delete("/api/meetings/{mid}")
def delete_meeting(mid: str, req: Request):
    u = auth.current_user(req)
    demo_owns(u, auth.meeting_for(u, mid, edit=True))
    for t in ("meeting_shares", "share_links", "folder_items", "comments", "tracker_hits", "scorecard_results"):
        db.run(f"DELETE FROM {t} WHERE meeting_id=?", mid)
    hl = [r["id"] for r in db.many("SELECT id FROM highlights WHERE meeting_id=?", mid)]
    for h in hl:
        db.run("DELETE FROM playlist_items WHERE highlight_id=?", h)
    db.run("DELETE FROM highlights WHERE meeting_id=?", mid)
    db.run("DELETE FROM notifications WHERE meeting_id=?", mid)
    db.run("DELETE FROM jobs WHERE ref=? AND state='queued'", mid)
    db.run("DELETE FROM meetings WHERE id=?", mid)
    shutil.rmtree(jobs.mdir(mid), ignore_errors=True)
    return {"deleted": mid}


def media_file(mid, kind):
    work = jobs.mdir(mid)
    p = f"{work}/video.mp4" if kind == "video" else f"{work}/audio.mp3"
    if not os.path.exists(p):
        raise HTTPException(404, "The recording is still being prepared.")
    return p


@router.get("/api/meetings/{mid}/media")
def get_media(mid: str, req: Request, kind: str = "audio"):
    u = auth.current_user(req); auth.meeting_for(u, mid)
    p = media_file(mid, kind)
    return FileResponse(p, media_type="video/mp4" if p.endswith(".mp4") else "audio/mpeg")


def srt_time(t, sep=","):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d}{sep}{ms % 1000:03d}"


def notes_markdown(m):
    s = m.get("summary") or {}
    out = [f"# {m['title']}", f"{m['date'][:16].replace('T', ' ')}", "", s.get("overview", ""), ""]
    for sec in s.get("sections", []):
        if sec["bullets"]:
            out += [f"## {sec['title']}"] + [f"- {b['text']}" + (f" ({ai.clock(b['t'])})" if b.get("t") is not None else "") for b in sec["bullets"]] + [""]
    if m["actions"]:
        out += ["## Action items"] + [f"- [{'x' if a['done'] else ' '}] {a['text']}" for a in m["actions"]]
    return "\n".join(out)


@router.get("/api/meetings/{mid}/download")
def download(mid: str, req: Request, format: str = "txt"):
    u = auth.current_user(req); m = auth.meeting_for(u, mid)
    safe = re.sub(r"[^A-Za-z0-9 _-]+", "", m["title"]).strip()[:60] or "meeting"
    if format in ("mp3", "mp4"):
        return FileResponse(media_file(mid, "video" if format == "mp4" else "audio"), filename=f"{safe}.{format}")
    names = m.get("names") or {}
    tr = m["transcript"]
    if format == "txt":
        body = "\n".join(f"[{ai.clock(t['t'])}] {names.get(t['s'], t['s'])}: {t['x']}" for t in tr)
    elif format in ("srt", "vtt"):
        sep = "," if format == "srt" else "."
        cues = [f"{i + 1}\n{srt_time(t['t'], sep)} --> {srt_time(t['end'], sep)}\n{names.get(t['s'], t['s'])}: {t['x']}\n" for i, t in enumerate(tr)]
        body = ("WEBVTT\n\n" if format == "vtt" else "") + "\n".join(cues)
    elif format == "md":
        body = notes_markdown(m)
    else:
        raise HTTPException(400, "Choose mp3, mp4, txt, srt, vtt or md.")
    return PlainTextResponse(body, headers={"Content-Disposition": f'attachment; filename="{safe}.{format}"'})


class TemplatePick(BaseModel):
    template: str


@router.post("/api/meetings/{mid}/resummarize")
def resummarize(mid: str, body: TemplatePick, req: Request):
    u = auth.current_user(req); m = auth.meeting_for(u, mid, edit=True)
    if m["stage"] != "done":
        raise HTTPException(409, "This meeting is still processing.")
    if body.template not in ai.TEMPLATES and not (body.template.startswith("custom:") and
                                                  db.one("SELECT 1 AS x FROM templates WHERE id=? AND org_id=?", body.template[7:], u["org_id"])):
        raise HTTPException(404, "No such template.")
    jobs.enqueue("resummarize", mid, template=body.template)
    return {"queued": True}


class Lang(BaseModel):
    language: str


@router.post("/api/meetings/{mid}/translate")
def translate(mid: str, body: Lang, req: Request):
    u = auth.current_user(req); m = auth.meeting_for(u, mid)
    if body.language not in ai.LANGS:
        raise HTTPException(400, "Choose one of: " + ", ".join(ai.LANGS))
    tr = m["translations"] or {}
    key = f"{m['template']}:{body.language}"
    if key not in tr:
        tr[key] = ai.translate(m["summary"] or {}, m["actions"], body.language)
        db.update("meetings", "id", mid, translations=tr)
    return tr[key]


@router.post("/api/meetings/{mid}/followup")
def followup(mid: str, req: Request):
    u = auth.current_user(req); m = auth.meeting_for(u, mid)
    org = db.one("SELECT name FROM orgs WHERE id=?", u["org_id"])
    r = ai.followup(m, u, org["name"])
    return dict(subject=str(r.get("subject", f"Follow-up: {m['title']}")), body=str(r.get("body", "")))


class Question(BaseModel):
    question: str
    scope: str = "all"


@router.post("/api/meetings/{mid}/ask")
def ask(mid: str, body: Question, req: Request):
    u = auth.current_user(req); m = auth.meeting_for(u, mid)
    q = body.question.strip()[:500]
    if not q:
        raise HTTPException(400, "Type a question first.")
    if m["stage"] != "done":
        raise HTTPException(409, "This meeting is still processing.")
    return {"answer": ai.ask_meeting(m, q, u)}


class TrimIn(BaseModel):
    mode: str  # range | before | after
    start: float = 0
    end: float = 0


def shift(t, a, b):
    """New time for t after removing [a, b]; None if t was inside the cut."""
    if t is None: return None
    if t < a: return t
    if t < b: return None
    return round(t - (b - a), 2)


@router.post("/api/meetings/{mid}/trim")
def trim(mid: str, body: TrimIn, req: Request):
    u = auth.current_user(req); m = auth.meeting_for(u, mid, edit=True)
    demo_owns(u, m)
    if m["stage"] != "done":
        raise HTTPException(409, "This meeting is still processing.")
    dur = m["duration"]
    a, b = {"before": (0, body.start), "after": (body.start, dur)}.get(body.mode, (body.start, body.end))
    a, b = max(0.0, a), min(dur, b)
    if b - a < 1 or b - a >= dur - 1:
        raise HTTPException(400, "Pick a section of at least one second that leaves some of the recording.")
    new_dur = media.trim(jobs.mdir(mid), m["has_video"], a, b)
    tr = []
    for t in m["transcript"]:
        s, e = shift(t["t"], a, b), shift(t["end"], a, b)
        if s is not None and e is not None:
            tr.append({**t, "t": s, "end": e})
    summ = m["summary"] or {}
    for sec in summ.get("sections", []):
        for bl in sec["bullets"]:
            bl["t"] = shift(bl.get("t"), a, b)
    acts = [{**x, "t": shift(x.get("t"), a, b)} for x in m["actions"]]
    for h in db.many("SELECT * FROM highlights WHERE meeting_id=?", mid):
        s, e = shift(h["start"], a, b), shift(h["end"], a, b)
        if s is None or e is None:
            db.run("DELETE FROM playlist_items WHERE highlight_id=?", h["id"]); db.run("DELETE FROM highlights WHERE id=?", h["id"])
        else:
            db.update("highlights", "id", h["id"], start=s, end=e)
        for f in ("clip.mp3", "clip.mp4"):
            p = f"{jobs.mdir(mid)}/{h['id']}.{f}"
            if os.path.exists(p): os.remove(p)
    for c in db.many("SELECT id, t FROM comments WHERE meeting_id=? AND t IS NOT NULL", mid):
        db.update("comments", "id", c["id"], t=shift(c["t"], a, b))
    for h in db.many("SELECT * FROM tracker_hits WHERE meeting_id=?", mid):
        nt = shift(h["t"], a, b)
        db.run("DELETE FROM tracker_hits WHERE tracker_id=? AND meeting_id=? AND t=?", h["tracker_id"], mid, h["t"])
        if nt is not None:
            db.run("INSERT OR IGNORE INTO tracker_hits VALUES (?,?,?,?)", h["tracker_id"], mid, nt, h["quote"])
    db.update("meetings", "id", mid, duration=new_dur, transcript=tr, summary=summ, actions=acts, translations={},
              metrics=media.metrics(tr, new_dur), updated=db.now())
    return get_meeting(mid, req)


@router.post("/api/meetings/{mid}/rescore")
def rescore(mid: str, req: Request):
    u = auth.current_user(req); auth.meeting_for(u, mid, edit=True)
    jobs.enqueue("rescore", mid)
    return {"queued": True}


# ---------- sharing ----------
class ShareIn(BaseModel):
    user_ids: list[str]


@router.post("/api/meetings/{mid}/shares")
def add_shares(mid: str, body: ShareIn, req: Request):
    u = auth.current_user(req); m = auth.meeting_for(u, mid, edit=True)
    for uid in body.user_ids:
        if uid != m["owner_id"] and db.one("SELECT 1 AS x FROM users WHERE id=? AND org_id=?", uid, u["org_id"]):
            if db.run("INSERT OR IGNORE INTO meeting_shares (meeting_id, user_id) VALUES (?,?)", mid, uid):
                jobs.notify(uid, "shared", f"{u['name']} shared {m['title']} with you", mid, f"#/meeting/{mid}")
    return get_meeting(mid, req)["shares"]


@router.delete("/api/meetings/{mid}/shares/{uid}")
def remove_share(mid: str, uid: str, req: Request):
    u = auth.current_user(req); auth.meeting_for(u, mid, edit=True)
    db.run("DELETE FROM meeting_shares WHERE meeting_id=? AND user_id=?", mid, uid)
    return {"ok": True}


class LinkIn(BaseModel):
    access: str = "public"  # public | org
    highlight_id: str | None = None


@router.get("/api/meetings/{mid}/links")
def list_links(mid: str, req: Request):
    u = auth.current_user(req); m = auth.meeting_for(u, mid)
    # Editors see every link on the meeting so they can turn any of them off; others see their own.
    if auth.can_edit(u, m):
        return db.many("SELECT l.token, l.access, l.highlight_id, l.created, u.name AS created_by_name FROM share_links l "
                       "JOIN users u ON u.id=l.created_by WHERE l.meeting_id=? AND l.revoked=0", mid)
    return db.many("SELECT token, access, highlight_id, created FROM share_links WHERE meeting_id=? AND revoked=0 AND created_by=?", mid, u["id"])


@router.post("/api/meetings/{mid}/links")
def create_link(mid: str, body: LinkIn, req: Request):
    u = auth.current_user(req); m = auth.meeting_for(u, mid)
    if body.highlight_id:
        if not db.one("SELECT 1 AS x FROM highlights WHERE id=? AND meeting_id=?", body.highlight_id, mid):
            raise HTTPException(404, "No such clip.")
    elif not auth.can_edit(u, m):
        raise HTTPException(403, "Only the owner or an admin can share the whole recording by link. You can share a clip instead.")
    import secrets
    tok = secrets.token_urlsafe(18)
    db.insert("share_links", token=tok, meeting_id=mid, highlight_id=body.highlight_id, access=body.access if body.access in ("public", "org") else "org",
              created_by=u["id"], created=db.now(), revoked=0)
    return dict(token=tok, link=f"/#/s/{tok}")


@router.delete("/api/links/{tok}")
def revoke_link(tok: str, req: Request):
    u = auth.current_user(req)
    link = db.one("SELECT * FROM share_links WHERE token=?", tok)
    if not link:
        raise HTTPException(404, "No such link.")
    m = db.one("SELECT * FROM meetings WHERE id=?", link["meeting_id"])
    if link["created_by"] != u["id"] and not auth.can_edit(u, m):
        raise HTTPException(403, "Only whoever made the link, the owner or an admin can turn it off.")
    db.update("share_links", "token", tok, revoked=1)
    return {"ok": True}
