"""Search, account-wide Ask, companies, trackers and coaching."""
import re, time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from .. import db, auth, jobs, ai
from .meetings import CONSUMER, LIST_COLS, list_row, lookup, org_domains

router = APIRouter()


def visible(u, extra="", *args, cols="m.*"):
    cond, a = auth.visible_sql(u)
    return db.many(f"SELECT {cols} FROM meetings m WHERE {cond} {extra}", *a, *args)


# ---------- search ----------
@router.get("/api/search")
def search(req: Request, q: str):
    u = auth.current_user(req)
    q = q.strip()
    if len(q) < 2:
        return []
    words = [w for w in re.findall(r"\w+", q.lower()) if len(w) > 1]
    phrase = q.lower()
    out = []
    for m in visible(u, "AND m.stage='done' ORDER BY m.date DESC"):
        names = m.get("names") or {}
        hits = []
        for t in m["transcript"]:
            low = t["x"].lower()
            s = (3 if phrase in low else 0) + sum(1 for w in words if w in low)
            if s:
                hits.append((s, t))
        in_title = phrase in m["title"].lower()
        s_txt = " ".join([(m.get("summary") or {}).get("overview", "")] +
                         [b["text"] for sec in (m.get("summary") or {}).get("sections", []) for b in sec["bullets"]]).lower()
        in_summary = phrase in s_txt or (words and all(w in s_txt for w in words))
        if not (hits or in_title or in_summary):
            continue
        hits.sort(key=lambda h: (-h[0], h[1]["t"]))
        out.append(dict(id=m["id"], title=m["title"], date=m["date"], score=(10 if in_title else 0) + (5 if in_summary else 0) + sum(h[0] for h in hits[:5]),
                        moments=[dict(t=h[1]["t"], speaker=names.get(h[1]["s"], h[1]["s"]), text=h[1]["x"][:260]) for h in hits[:3]]))
    return sorted(out, key=lambda r: -r["score"])[:50]


# ---------- account-wide Ask ----------
class AskIn(BaseModel):
    question: str
    scope: str = "all"  # mine | team | all
    folder_id: str | None = None
    company: str | None = None


@router.post("/api/ask")
def ask_all(body: AskIn, req: Request):
    u = auth.current_user(req)
    q = body.question.strip()[:500]
    if not q:
        raise HTTPException(400, "Type a question first.")
    extra, args = "AND m.stage='done'", []
    if body.scope == "mine":
        extra += " AND m.owner_id=?"; args.append(u["id"])
    elif body.scope == "team":
        extra += " AND m.owner_id<>?"; args.append(u["id"])
    if body.folder_id:
        f = db.one("SELECT * FROM folders WHERE id=? AND org_id=?", body.folder_id, u["org_id"])
        if not f or not (f["shared"] or f["owner_id"] == u["id"] or u["role"] == "admin"):
            raise HTTPException(404, "No such folder.")
        extra += " AND m.id IN (SELECT meeting_id FROM folder_items WHERE folder_id=?)"; args.append(body.folder_id)
    if body.company:
        extra += " AND m.attendees LIKE ?"; args.append(f"%@{body.company}%")
    ms = visible(u, extra + " ORDER BY m.date DESC LIMIT 300", *args)
    if not ms:
        return dict(answer="There are no processed meetings in that scope yet.", refs={})
    users = {r["id"]: r["name"] for r in db.many("SELECT id, name FROM users WHERE org_id=?", u["org_id"])}
    for m in ms:
        m["owner_name"] = users.get(m["owner_id"], "")
    answer, refs = ai.ask_all(ms, q, u)
    return dict(answer=answer, refs=refs, searched=len(ms))


# ---------- companies ----------
@router.get("/api/companies")
def companies(req: Request):
    u = auth.current_user(req)
    own = org_domains(u["org_id"])
    by = {}
    for m in visible(u, "ORDER BY m.date DESC", cols="m.id, m.date, m.attendees"):
        for a in m["attendees"]:
            d = a.split("@")[-1].lower()
            if "@" not in a or d in own or d in CONSUMER:
                continue
            c = by.setdefault(d, dict(domain=d, meetings=set(), people=set(), last=m["date"]))
            c["meetings"].add(m["id"]); c["people"].add(a)
            c["last"] = max(c["last"], m["date"])
    return sorted(({**c, "meetings": len(c["meetings"]), "people": sorted(c["people"])} for c in by.values()), key=lambda c: c["last"], reverse=True)


@router.get("/api/companies/{domain}")
def company(domain: str, req: Request):
    u = auth.current_user(req)
    d = domain.lower()
    rows = [m for m in visible(u, "AND m.attendees LIKE ? ORDER BY m.date DESC", f"%@{d}%", cols=LIST_COLS)
            if any(a.endswith("@" + d) for a in m["attendees"])]
    users, teams, types = lookup(u["org_id"]); own = org_domains(u["org_id"])
    people = {}
    for m in rows:
        for a in m["attendees"]:
            if a.endswith("@" + d):
                people.setdefault(a, 0); people[a] += 1
    return dict(domain=d, meetings=[list_row(m, users, teams, types, own, u) for m in rows],
                people=[dict(email=k, meetings=v) for k, v in sorted(people.items(), key=lambda x: -x[1])])


# ---------- trackers ----------
class TrackerIn(BaseModel):
    name: str
    kind: str  # keyword | ai
    keywords: list[str] = []
    description: str = ""


def tracker_terms(body):
    if body.kind == "keyword":
        kws = [k.strip()[:60] for k in body.keywords if k.strip()][:25]
        if not kws:
            raise HTTPException(400, "Add at least one keyword.")
        return dict(keywords=kws)
    if body.kind == "ai":
        if len(body.description.strip()) < 10:
            raise HTTPException(400, "Describe the moments to find in a sentence or two.")
        return dict(description=body.description.strip()[:600])
    raise HTTPException(400, "Choose keyword or AI.")


@router.get("/api/trackers")
def list_trackers(req: Request):
    u = auth.current_user(req)
    cond, args = auth.visible_sql(u)
    ts = db.many("SELECT t.*, u.name AS owner_name FROM trackers t JOIN users u ON u.id=t.owner_id WHERE t.org_id=? ORDER BY t.name", u["org_id"])
    subs = {r["tracker_id"] for r in db.many("SELECT tracker_id FROM tracker_subs WHERE user_id=?", u["id"])}
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))
    for t in ts:
        r = db.one(f"SELECT COUNT(*) AS hits, COUNT(DISTINCT h.meeting_id) AS meetings FROM tracker_hits h JOIN meetings m ON m.id=h.meeting_id "
                   f"WHERE h.tracker_id=? AND m.date>=? AND {cond}", t["id"], since, *args)
        t.update(hits_30d=r["hits"], meetings_30d=r["meetings"], subscribed=t["id"] in subs,
                 can_edit=t["owner_id"] == u["id"] or u["role"] == "admin",
                 pending=bool(db.one("SELECT 1 AS x FROM jobs WHERE kind='tracker_backfill' AND ref=? AND state IN ('queued','running')", t["id"])))
    return ts


@router.post("/api/trackers")
def create_tracker(body: TrackerIn, req: Request):
    u = auth.current_user(req)
    tid = db.new_id()
    db.insert("trackers", id=tid, org_id=u["org_id"], owner_id=u["id"], name=body.name.strip()[:60] or "Tracker", kind=body.kind,
              terms=tracker_terms(body), created=db.now())
    db.run("INSERT INTO tracker_subs (tracker_id, user_id) VALUES (?,?)", tid, u["id"])
    jobs.enqueue("tracker_backfill", tid)
    return {"id": tid}


def tracker_edit(u, tid):
    t = db.one("SELECT * FROM trackers WHERE id=? AND org_id=?", tid, u["org_id"])
    if not t:
        raise HTTPException(404, "No such tracker.")
    if t["owner_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "Only whoever made the tracker or an admin can change it.")
    return t


@router.patch("/api/trackers/{tid}")
def patch_tracker(tid: str, body: TrackerIn, req: Request):
    u = auth.current_user(req); tracker_edit(u, tid)
    db.update("trackers", "id", tid, name=body.name.strip()[:60], kind=body.kind, terms=tracker_terms(body))
    jobs.enqueue("tracker_backfill", tid)
    return {"ok": True}


@router.delete("/api/trackers/{tid}")
def delete_tracker(tid: str, req: Request):
    u = auth.current_user(req); tracker_edit(u, tid)
    for t in ("tracker_hits", "tracker_subs"):
        db.run(f"DELETE FROM {t} WHERE tracker_id=?", tid)
    db.run("DELETE FROM trackers WHERE id=?", tid)
    return {"ok": True}


@router.post("/api/trackers/{tid}/subscribe")
def subscribe(tid: str, req: Request):
    u = auth.current_user(req)
    if not db.one("SELECT 1 AS x FROM trackers WHERE id=? AND org_id=?", tid, u["org_id"]):
        raise HTTPException(404, "No such tracker.")
    db.run("INSERT OR IGNORE INTO tracker_subs (tracker_id, user_id) VALUES (?,?)", tid, u["id"])
    return {"ok": True}


@router.delete("/api/trackers/{tid}/subscribe")
def unsubscribe(tid: str, req: Request):
    u = auth.current_user(req)
    db.run("DELETE FROM tracker_subs WHERE tracker_id=? AND user_id=?", tid, u["id"])
    return {"ok": True}


@router.get("/api/trackers/{tid}/hits")
def tracker_hits(tid: str, req: Request, days: int = 90):
    u = auth.current_user(req)
    if not db.one("SELECT 1 AS x FROM trackers WHERE id=? AND org_id=?", tid, u["org_id"]):
        raise HTTPException(404, "No such tracker.")
    cond, args = auth.visible_sql(u)
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    return db.many(f"SELECT h.t, h.quote, m.id AS meeting_id, m.title, m.date FROM tracker_hits h JOIN meetings m ON m.id=h.meeting_id "
                   f"WHERE h.tracker_id=? AND m.date>=? AND {cond} ORDER BY m.date DESC, h.t", tid, since, *args)


# ---------- coaching ----------
def coachable_users(u, team_id=""):
    """Admins see everyone, team leads see their teams, members see themselves."""
    people = db.many("SELECT id, name FROM users WHERE org_id=? AND active=1", u["org_id"])
    if u["role"] == "admin":
        allowed = {p["id"] for p in people}
    else:
        leads = auth.lead_team_ids(u["id"])
        allowed = {u["id"]} | {r["user_id"] for t in leads for r in db.many("SELECT user_id FROM team_members WHERE team_id=?", t)}
    if team_id:
        allowed &= {r["user_id"] for r in db.many("SELECT user_id FROM team_members WHERE team_id=?", team_id)}
    return [p for p in people if p["id"] in allowed]


@router.get("/api/coaching")
def coaching(req: Request, days: int = 30, team_id: str = ""):
    u = auth.current_user(req)
    people = coachable_users(u, team_id)
    ids = {p["id"] for p in people}
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    stats = {p["id"]: dict(user=p, meetings=0, talk_pct=[], wpm=[], questions=[], longest=[], monologues=0, talk_min=0.0) for p in people}
    # Metrics count only where a speaker label has been linked to a teammate.
    for m in db.many("SELECT id, speaker_users, metrics FROM meetings WHERE org_id=? AND stage='done' AND date>=?", u["org_id"], since):
        per = (m["metrics"] or {}).get("per_speaker", {})
        for label, uid in (m["speaker_users"] or {}).items():
            if uid in ids and label in per:
                s, p = stats[uid], per[label]
                s["meetings"] += 1; s["talk_pct"].append(p["talk_pct"]); s["wpm"].append(p["wpm"]); s["questions"].append(p["questions"])
                s["longest"].append(p["longest"]); s["monologues"] += p["monologues"]; s["talk_min"] += p["talk"] / 60
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    rows = [dict(user=s["user"], meetings=s["meetings"], talk_pct=avg(s["talk_pct"]), wpm=avg(s["wpm"]), questions_per_call=avg(s["questions"]),
                 longest_monologue=avg(s["longest"]), monologues=s["monologues"], talk_minutes=round(s["talk_min"], 1)) for s in stats.values()]
    return dict(days=days, people=sorted(rows, key=lambda r: (-r["meetings"], r["user"]["name"])))


@router.get("/api/coaching/scorecards")
def coaching_scorecards(req: Request, days: int = 90):
    u = auth.current_user(req)
    ids = {p["id"] for p in coachable_users(u)}
    names = {p["id"]: p["name"] for p in db.many("SELECT id, name FROM users WHERE org_id=?", u["org_id"])}
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    out = []
    for sc in db.many("SELECT s.*, t.name AS type_name FROM scorecards s JOIN meeting_types t ON t.id=s.meeting_type_id WHERE s.org_id=?", u["org_id"]):
        res = db.many("SELECT r.*, m.title, m.date FROM scorecard_results r JOIN meetings m ON m.id=r.meeting_id "
                      "WHERE r.scorecard_id=? AND m.date>=? ORDER BY m.date DESC", sc["id"], since)
        res = [r for r in res if r["user_id"] in ids]
        per = {}
        for r in res:
            per.setdefault(r["user_id"], []).append(r["score"])
        ranking = sorted(({"user": dict(id=k, name=names.get(k, "Former member")), "avg": round(sum(v) / len(v)), "calls": len(v)}
                          for k, v in per.items()), key=lambda x: -x["avg"])
        cut = max(1, len(ranking) // 4) if ranking else 0
        crit_avg = {}
        for r in res:
            for c in r["details"].get("criteria", []):
                crit_avg.setdefault(c["name"], []).append(c["score"])
        out.append(dict(id=sc["id"], name=sc["name"], meeting_type=sc["type_name"], criteria=sc["criteria"],
                        average=round(sum(r["score"] for r in res) / len(res)) if res else None,
                        criteria_avg={k: round(sum(v) / len(v), 1) for k, v in crit_avg.items()},
                        top=ranking[:cut], lower=ranking[cut:],
                        recent=[dict(meeting_id=r["meeting_id"], title=r["title"], date=r["date"], score=r["score"],
                                     user=names.get(r["user_id"])) for r in res[:20]]))
    return out
