"""Public REST API, authenticated with X-Api-Key. Returns only meetings the key's owner can see."""
import time
from fastapi import APIRouter, HTTPException, Request
from .. import db, auth, jobs

router = APIRouter()
_calls = {}


def limited(req):
    u = auth.current_user(req)
    now = time.time()
    recent = [t for t in _calls.get(u["id"], []) if now - t < 60]
    if len(recent) >= 60:
        raise HTTPException(429, "Rate limit: 60 requests per minute per user.")
    _calls[u["id"]] = recent + [now]
    return u


@router.get("/api/v1/meetings")
def meetings(req: Request, include_transcript: bool = False, include_summary: bool = True, include_action_items: bool = True,
             created_after: str = "", cursor: str = "", limit: int = 20):
    u = limited(req)
    cond, args = auth.visible_sql(u)
    extra, more = "AND m.stage='done'", []
    if created_after:
        extra += " AND m.date>=?"; more.append(created_after)
    if cursor:
        extra += " AND m.date<?"; more.append(cursor)
    limit = max(1, min(limit, 100))
    rows = db.many(f"SELECT * FROM meetings m WHERE {cond} {extra} ORDER BY m.date DESC LIMIT ?", *args, *more, limit + 1)
    inc = [k for k, on in (("transcript", include_transcript), ("summary", include_summary), ("action_items", include_action_items)) if on]
    items = [jobs.meeting_payload(m, inc) for m in rows[:limit]]
    return dict(items=items, next_cursor=rows[limit - 1]["date"] if len(rows) > limit else None)


@router.get("/api/v1/meetings/{mid}")
def meeting(mid: str, req: Request):
    u = limited(req)
    return jobs.meeting_payload(auth.meeting_for(u, mid))


@router.get("/api/v1/teams")
def teams(req: Request):
    u = limited(req)
    return db.many("SELECT id, name, created FROM teams WHERE org_id=? ORDER BY name", u["org_id"])


@router.get("/api/v1/team_members")
def team_members(req: Request, team_id: str = ""):
    u = limited(req)
    if team_id:
        return db.many("SELECT u.id, u.name, u.email, tm.lead FROM team_members tm JOIN users u ON u.id=tm.user_id JOIN teams t ON t.id=tm.team_id "
                       "WHERE t.org_id=? AND t.id=?", u["org_id"], team_id)
    return db.many("SELECT id, name, email, role FROM users WHERE org_id=? AND active=1 ORDER BY name", u["org_id"])
