"""Log in, invites, your profile, API keys and webhooks."""
import secrets, time
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from .. import db, auth, jobs

router = APIRouter()
_attempts = {}


def client_ip(req):
    return req.headers.get("x-forwarded-for", "").split(",")[-1].strip() or req.client.host


def throttle(req):
    ip, now = client_ip(req), time.time()
    recent = [t for t in _attempts.get(ip, []) if now - t < 600]
    _attempts[ip] = recent
    if len(recent) >= 10:
        raise HTTPException(429, "Too many attempts. Try again in 10 minutes.")
    return ip


def set_cookie(req, resp, tok):
    https = req.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(auth.COOKIE, tok, max_age=auth.SESSION_DAYS * 86400, httponly=True, secure=https, samesite="lax")


def me_payload(u):
    org = db.one("SELECT * FROM orgs WHERE id=?", u["org_id"])
    teams = db.many("SELECT t.id, t.name, tm.lead FROM teams t JOIN team_members tm ON tm.team_id=t.id WHERE tm.user_id=?", u["id"])
    return dict(user={**auth.public_user(u), "settings": u["settings"]}, org=dict(id=org["id"], name=org["name"]), teams=teams,
                is_lead=any(t["lead"] for t in teams))


class Login(BaseModel):
    email: str
    password: str


@router.post("/api/auth/login")
def login(body: Login, req: Request, resp: Response):
    ip = throttle(req)
    u = db.one("SELECT * FROM users WHERE email=? AND active=1", body.email.strip())
    if not u or not auth.check_pw(body.password, u["pw_hash"]):
        _attempts[ip].append(time.time())
        raise HTTPException(401, "That email and password don't match an account.")
    set_cookie(req, resp, auth.create_session(u["id"]))
    return me_payload(u)


@router.post("/api/auth/demo")
def demo_login(req: Request, resp: Response):
    """One-click entry for reviewers: signs into the shared demo account, if the server has one."""
    u = db.one("SELECT * FROM users WHERE email=? AND active=1", auth.DEMO_EMAIL)
    if not u:
        raise HTTPException(404, "There's no demo workspace on this server.")
    set_cookie(req, resp, auth.create_session(u["id"]))
    return me_payload(u)


@router.get("/api/auth/demo")
def demo_available():
    return {"available": bool(db.one("SELECT 1 AS x FROM users WHERE email=? AND active=1", auth.DEMO_EMAIL))}


@router.post("/api/auth/logout")
def logout(req: Request, resp: Response):
    auth.end_session(req.cookies.get(auth.COOKIE)); resp.delete_cookie(auth.COOKIE)
    return {"ok": True}


@router.get("/api/auth/me")
def me(req: Request):
    return me_payload(auth.current_user(req))


def open_invite(token):
    inv = db.one("SELECT * FROM invites WHERE token=? AND used_by IS NULL", token)
    if not inv or time.time() - inv["created"] > 14 * 86400:
        raise HTTPException(404, "This invite link has expired or was already used. Ask your admin for a new one.")
    return inv


@router.get("/api/auth/invite/{token}")
def invite_info(token: str):
    inv = open_invite(token)
    org = db.one("SELECT name FROM orgs WHERE id=?", inv["org_id"])
    return dict(org=org["name"], email=inv["email"])


class Accept(BaseModel):
    name: str
    email: str
    password: str


@router.post("/api/auth/invite/{token}")
def accept_invite(token: str, body: Accept, req: Request, resp: Response):
    throttle(req)
    inv = open_invite(token)
    email = (inv["email"] or body.email).strip()
    if "@" not in email:
        raise HTTPException(400, "Enter a valid email address.")
    if len(body.password) < 10:
        raise HTTPException(400, "Use a password of at least 10 characters.")
    if db.one("SELECT 1 AS x FROM users WHERE email=?", email):
        raise HTTPException(409, "An account with that email already exists. Log in instead.")
    uid = db.new_id()
    db.insert("users", id=uid, org_id=inv["org_id"], email=email, name=body.name.strip()[:80] or email.split("@")[0],
              pw_hash=auth.hash_pw(body.password), role=inv["role"], active=1, settings={}, created=db.now())
    if inv["team_id"]:
        db.run("INSERT OR IGNORE INTO team_members (team_id, user_id, lead) VALUES (?,?,0)", inv["team_id"], uid)
    db.update("invites", "token", token, used_by=uid)
    jobs.notify(inv["created_by"], "joined", f"{body.name.strip() or email} joined your organization")
    set_cookie(req, resp, auth.create_session(uid))
    return me_payload(db.one("SELECT * FROM users WHERE id=?", uid))


class Profile(BaseModel):
    name: str | None = None
    current_password: str | None = None
    new_password: str | None = None
    settings: dict | None = None


@router.patch("/api/me")
def update_me(body: Profile, req: Request):
    u = auth.current_user(req)
    vals = {}
    if body.name is not None and body.name.strip():
        vals["name"] = body.name.strip()[:80]
    if body.new_password:
        if not auth.check_pw(body.current_password or "", u["pw_hash"]):
            raise HTTPException(400, "Your current password is wrong.")
        if len(body.new_password) < 10:
            raise HTTPException(400, "Use a password of at least 10 characters.")
        vals["pw_hash"] = auth.hash_pw(body.new_password)
    if body.settings is not None:
        allowed = {k: v for k, v in body.settings.items() if k in ("default_template", "summary_instructions")}
        vals["settings"] = {**(u["settings"] or {}), **allowed}
    db.update("users", "id", u["id"], **vals)
    return me_payload(db.one("SELECT * FROM users WHERE id=?", u["id"]))


# ---------- API keys ----------
class KeyIn(BaseModel):
    name: str


@router.get("/api/me/api-keys")
def list_keys(req: Request):
    u = auth.current_user(req)
    return db.many("SELECT id, name, prefix, created, last_used FROM api_keys WHERE user_id=? ORDER BY created DESC", u["id"])


@router.post("/api/me/api-keys")
def create_key(body: KeyIn, req: Request):
    u = auth.current_user(req)
    key = "snd_" + secrets.token_urlsafe(32)
    kid = db.new_id()
    db.insert("api_keys", id=kid, user_id=u["id"], name=body.name.strip()[:60] or "API key", key_hash=auth.sha(key),
              prefix=key[:10], created=db.now())
    return dict(id=kid, key=key)  # shown once


@router.delete("/api/me/api-keys/{kid}")
def delete_key(kid: str, req: Request):
    u = auth.current_user(req)
    db.run("DELETE FROM api_keys WHERE id=? AND user_id=?", kid, u["id"])
    return {"ok": True}


# ---------- webhooks ----------
class HookIn(BaseModel):
    url: str
    include: list[str] = ["summary", "action_items", "transcript"]


@router.get("/api/me/webhooks")
def list_hooks(req: Request):
    u = auth.current_user(req)
    return db.many("SELECT id, url, include, created, last_status FROM webhooks WHERE user_id=?", u["id"])


@router.post("/api/me/webhooks")
def create_hook(body: HookIn, req: Request):
    u = auth.current_user(req)
    inc = [i for i in body.include if i in ("summary", "action_items", "transcript")]
    if not inc:
        raise HTTPException(400, "Choose at least one of summary, action items or transcript.")
    if not jobs.safe_url(body.url):
        raise HTTPException(400, "Use a public http or https URL. Private and local addresses aren't allowed.")
    hid, secret = db.new_id(), secrets.token_hex(24)
    db.insert("webhooks", id=hid, user_id=u["id"], url=body.url.strip(), secret=secret, include=inc, created=db.now())
    return dict(id=hid, secret=secret)  # shown once, used to verify X-Sounding-Signature


@router.delete("/api/me/webhooks/{hid}")
def delete_hook(hid: str, req: Request):
    u = auth.current_user(req)
    db.run("DELETE FROM webhooks WHERE id=? AND user_id=?", hid, u["id"])
    return {"ok": True}


@router.post("/api/me/webhooks/{hid}/test")
def test_hook(hid: str, req: Request):
    u = auth.current_user(req)
    hook = db.one("SELECT * FROM webhooks WHERE id=? AND user_id=?", hid, u["id"])
    if not hook:
        raise HTTPException(404, "No webhook with that id.")
    cond, args = auth.visible_sql(u)
    m = db.one(f"SELECT * FROM meetings m WHERE {cond} AND m.stage='done' ORDER BY date DESC LIMIT 1", *args)
    payload = dict(event="meeting.ready", test=True, meeting=jobs.meeting_payload(m, hook["include"]) if m else None)
    status = jobs.send_webhook(hook, payload)
    db.update("webhooks", "id", hid, last_status=f"{time.strftime('%Y-%m-%d %H:%M')} {status} (test)")
    return dict(status=status)
