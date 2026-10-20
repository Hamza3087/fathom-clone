"""Organization admin: members, invites, teams, meeting types, templates, scorecards, dictionary."""
import secrets
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from .. import db, auth, jobs, ai

router = APIRouter()


def admin(req):
    u = auth.current_user(req); auth.require_admin(u); return u


def own(table, oid, u):
    r = db.one(f"SELECT * FROM {table} WHERE id=? AND org_id=?", oid, u["org_id"])
    if not r:
        raise HTTPException(404, "Not found.")
    return r


# ---------- members ----------
@router.get("/api/org/members")
def members(req: Request):
    u = auth.current_user(req)
    users = db.many("SELECT id, name, email, role, active, created FROM users WHERE org_id=? ORDER BY name", u["org_id"])
    tm = db.many("SELECT tm.user_id, tm.team_id, tm.lead, t.name FROM team_members tm JOIN teams t ON t.id=tm.team_id WHERE t.org_id=?",
                 u["org_id"])
    for x in users:
        x["teams"] = [dict(id=t["team_id"], name=t["name"], lead=bool(t["lead"])) for t in tm if t["user_id"] == x["id"]]
        # The demo workspace is open to anyone, so it never shows real people's email addresses.
        if auth.is_demo(u) and not x["email"].lower().endswith("@sounding.demo"):
            x["email"] = "hidden in the demo"
    return users


class MemberPatch(BaseModel):
    role: str | None = None
    active: bool | None = None


@router.patch("/api/org/members/{uid}")
def patch_member(uid: str, body: MemberPatch, req: Request):
    u = admin(req)
    target = db.one("SELECT * FROM users WHERE id=? AND org_id=?", uid, u["org_id"])
    if not target:
        raise HTTPException(404, "No such member.")
    vals = {}
    if body.role in ("admin", "member"):
        vals["role"] = body.role
    if body.active is not None:
        vals["active"] = int(body.active)
    if uid == u["id"] and (vals.get("role") == "member" or vals.get("active") == 0):
        raise HTTPException(400, "You can't remove your own admin access. Ask another admin.")
    db.update("users", "id", uid, **vals)
    if vals.get("active") == 0:
        db.run("DELETE FROM sessions WHERE user_id=?", uid)
    return {"ok": True}


class InviteIn(BaseModel):
    email: str | None = None
    role: str = "member"
    team_id: str | None = None


@router.post("/api/org/invites")
def create_invite(body: InviteIn, req: Request):
    u = admin(req)
    if body.team_id:
        own("teams", body.team_id, u)
    tok = secrets.token_urlsafe(24)
    db.insert("invites", token=tok, org_id=u["org_id"], email=(body.email or "").strip() or None,
              role=body.role if body.role in ("admin", "member") else "member", team_id=body.team_id, created_by=u["id"], created=db.now())
    return dict(token=tok, link=f"/#/invite/{tok}")


@router.get("/api/org/invites")
def list_invites(req: Request):
    u = admin(req)
    return db.many("SELECT token, email, role, team_id, created FROM invites WHERE org_id=? AND used_by IS NULL ORDER BY created DESC",
                   u["org_id"])


@router.delete("/api/org/invites/{tok}")
def delete_invite(tok: str, req: Request):
    u = admin(req)
    db.run("DELETE FROM invites WHERE token=? AND org_id=?", tok, u["org_id"])
    return {"ok": True}


# ---------- teams ----------
class TeamIn(BaseModel):
    name: str


@router.get("/api/org/teams")
def teams(req: Request):
    u = auth.current_user(req)
    ts = db.many("SELECT * FROM teams WHERE org_id=? ORDER BY name", u["org_id"])
    for t in ts:
        t["members"] = db.many("SELECT u.id, u.name, tm.lead FROM team_members tm JOIN users u ON u.id=tm.user_id WHERE tm.team_id=?", t["id"])
    return ts


@router.post("/api/org/teams")
def create_team(body: TeamIn, req: Request):
    u = admin(req)
    tid = db.new_id()
    db.insert("teams", id=tid, org_id=u["org_id"], name=body.name.strip()[:60] or "New team", created=db.now())
    return {"id": tid}


@router.patch("/api/org/teams/{tid}")
def rename_team(tid: str, body: TeamIn, req: Request):
    u = admin(req); own("teams", tid, u)
    db.update("teams", "id", tid, name=body.name.strip()[:60])
    return {"ok": True}


@router.delete("/api/org/teams/{tid}")
def delete_team(tid: str, req: Request):
    u = admin(req); own("teams", tid, u)
    db.run("UPDATE meetings SET visibility='private', team_id=NULL WHERE team_id=?", tid)
    db.run("DELETE FROM team_members WHERE team_id=?", tid); db.run("DELETE FROM teams WHERE id=?", tid)
    return {"ok": True}


class TeamMember(BaseModel):
    user_id: str
    lead: bool = False


@router.post("/api/org/teams/{tid}/members")
def add_member(tid: str, body: TeamMember, req: Request):
    u = admin(req); own("teams", tid, u)
    if not db.one("SELECT 1 AS x FROM users WHERE id=? AND org_id=?", body.user_id, u["org_id"]):
        raise HTTPException(404, "No such member.")
    db.run("INSERT OR REPLACE INTO team_members (team_id, user_id, lead) VALUES (?,?,?)", tid, body.user_id, int(body.lead))
    return {"ok": True}


@router.delete("/api/org/teams/{tid}/members/{uid}")
def remove_member(tid: str, uid: str, req: Request):
    u = admin(req); own("teams", tid, u)
    db.run("DELETE FROM team_members WHERE team_id=? AND user_id=?", tid, uid)
    return {"ok": True}


# ---------- settings ----------
class OrgPatch(BaseModel):
    name: str | None = None
    dictionary: list[str] | None = None


@router.get("/api/org/settings")
def get_settings(req: Request):
    u = auth.current_user(req)
    o = db.one("SELECT * FROM orgs WHERE id=?", u["org_id"])
    return dict(name=o["name"], dictionary=(o["settings"] or {}).get("dictionary", []))


@router.patch("/api/org/settings")
def patch_settings(body: OrgPatch, req: Request):
    u = admin(req)
    o = db.one("SELECT * FROM orgs WHERE id=?", u["org_id"])
    s = o["settings"] or {}
    if body.dictionary is not None:
        terms = [t.strip() for t in body.dictionary if t.strip()]
        if len(terms) > 50 or any(len(t) > 100 or len(t.split()) > 6 for t in terms):
            raise HTTPException(400, "Use up to 50 terms, each at most 6 words and 100 characters.")
        s["dictionary"] = terms
    db.update("orgs", "id", u["org_id"], settings=s, **({"name": body.name.strip()[:80]} if body.name else {}))
    return get_settings(req)


# ---------- templates & meeting types ----------
@router.get("/api/templates")
def templates(req: Request):
    u = auth.current_user(req)
    out = [dict(key=k, name=v["name"], sections=v["sections"], builtin=True) for k, v in ai.TEMPLATES.items()]
    for t in db.many("SELECT * FROM templates WHERE org_id=? ORDER BY name", u["org_id"]):
        out.append(dict(key=f"custom:{t['id']}", id=t["id"], name=t["name"], base=t["base"], instructions=t["instructions"],
                        sections=ai.TEMPLATES.get(t["base"], ai.TEMPLATES["general"])["sections"], builtin=False))
    return out


class TemplateIn(BaseModel):
    name: str
    base: str = "general"
    instructions: str


@router.post("/api/org/templates")
def create_template(body: TemplateIn, req: Request):
    u = admin(req)
    tid = db.new_id()
    db.insert("templates", id=tid, org_id=u["org_id"], name=body.name.strip()[:60], base=body.base if body.base in ai.TEMPLATES else "general",
              instructions=body.instructions.strip()[:1500], created=db.now())
    return {"id": tid}


@router.patch("/api/org/templates/{tid}")
def patch_template(tid: str, body: TemplateIn, req: Request):
    u = admin(req); own("templates", tid, u)
    db.update("templates", "id", tid, name=body.name.strip()[:60], base=body.base if body.base in ai.TEMPLATES else "general",
              instructions=body.instructions.strip()[:1500])
    return {"ok": True}


@router.delete("/api/org/templates/{tid}")
def delete_template(tid: str, req: Request):
    u = admin(req); own("templates", tid, u)
    db.run("DELETE FROM templates WHERE id=?", tid)
    return {"ok": True}


class TypeIn(BaseModel):
    name: str
    description: str
    template: str = "general"


@router.get("/api/org/meeting-types")
def meeting_types(req: Request):
    u = auth.current_user(req)
    return db.many("SELECT * FROM meeting_types WHERE org_id=? ORDER BY name", u["org_id"])


@router.post("/api/org/meeting-types")
def create_type(body: TypeIn, req: Request):
    u = admin(req)
    mid = db.new_id()
    db.insert("meeting_types", id=mid, org_id=u["org_id"], name=body.name.strip()[:60], description=body.description.strip()[:500],
              template=body.template, created=db.now())
    return {"id": mid}


@router.patch("/api/org/meeting-types/{tid}")
def patch_type(tid: str, body: TypeIn, req: Request):
    u = admin(req); own("meeting_types", tid, u)
    db.update("meeting_types", "id", tid, name=body.name.strip()[:60], description=body.description.strip()[:500], template=body.template)
    return {"ok": True}


@router.delete("/api/org/meeting-types/{tid}")
def delete_type(tid: str, req: Request):
    u = admin(req); own("meeting_types", tid, u)
    db.run("UPDATE meetings SET meeting_type_id=NULL WHERE meeting_type_id=?", tid)
    for sc in db.many("SELECT id FROM scorecards WHERE meeting_type_id=?", tid):
        db.run("DELETE FROM scorecard_results WHERE scorecard_id=?", sc["id"])
    db.run("DELETE FROM scorecards WHERE meeting_type_id=?", tid); db.run("DELETE FROM meeting_types WHERE id=?", tid)
    return {"ok": True}


# ---------- scorecards ----------
class ScorecardIn(BaseModel):
    name: str
    meeting_type_id: str
    criteria: list[dict]


def clean_criteria(c):
    out = [dict(name=str(x.get("name", "")).strip()[:80], description=str(x.get("description", "")).strip()[:400]) for x in c]
    out = [x for x in out if x["name"]]
    if not 1 <= len(out) <= 12:
        raise HTTPException(400, "A scorecard needs between 1 and 12 criteria.")
    return out


@router.get("/api/org/scorecards")
def scorecards(req: Request):
    u = auth.current_user(req)
    return db.many("SELECT * FROM scorecards WHERE org_id=? ORDER BY name", u["org_id"])


@router.post("/api/org/scorecards")
def create_scorecard(body: ScorecardIn, req: Request):
    u = admin(req); own("meeting_types", body.meeting_type_id, u)
    sid = db.new_id()
    db.insert("scorecards", id=sid, org_id=u["org_id"], meeting_type_id=body.meeting_type_id, name=body.name.strip()[:60],
              criteria=clean_criteria(body.criteria), created=db.now())
    jobs.enqueue("scorecard_backfill", sid)
    return {"id": sid}


@router.patch("/api/org/scorecards/{sid}")
def patch_scorecard(sid: str, body: ScorecardIn, req: Request):
    u = admin(req); own("scorecards", sid, u); own("meeting_types", body.meeting_type_id, u)
    db.update("scorecards", "id", sid, name=body.name.strip()[:60], meeting_type_id=body.meeting_type_id,
              criteria=clean_criteria(body.criteria))
    db.run("DELETE FROM scorecard_results WHERE scorecard_id=?", sid)
    jobs.enqueue("scorecard_backfill", sid)
    return {"ok": True}


@router.delete("/api/org/scorecards/{sid}")
def delete_scorecard(sid: str, req: Request):
    u = admin(req); own("scorecards", sid, u)
    db.run("DELETE FROM scorecard_results WHERE scorecard_id=?", sid); db.run("DELETE FROM scorecards WHERE id=?", sid)
    return {"ok": True}
