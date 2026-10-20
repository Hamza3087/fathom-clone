"""Accounts, sessions, API keys and who-can-see-what."""
import hashlib, hmac, secrets, time
from fastapi import HTTPException, Request
from . import db

SESSION_DAYS = 14
COOKIE = "sounding_session"


# ---------- passwords ----------
def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw, stored):
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(got.hex(), h)
    except Exception:
        return False


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


# ---------- sessions ----------
def create_session(user_id):
    tok = secrets.token_urlsafe(32)
    db.insert("sessions", token_hash=sha(tok), user_id=user_id, expires=time.time() + SESSION_DAYS * 86400)
    return tok


def end_session(tok):
    if tok:
        db.run("DELETE FROM sessions WHERE token_hash=?", sha(tok))


def user_from_session(tok):
    if not tok:
        return None
    s = db.one("SELECT * FROM sessions WHERE token_hash=?", sha(tok))
    if not s or s["expires"] < time.time():
        return None
    u = db.one("SELECT * FROM users WHERE id=? AND active=1", s["user_id"])
    return u


def user_from_api_key(key):
    if not key:
        return None
    k = db.one("SELECT * FROM api_keys WHERE key_hash=?", sha(key))
    if not k:
        return None
    db.update("api_keys", "id", k["id"], last_used=time.time())
    return db.one("SELECT * FROM users WHERE id=? AND active=1", k["user_id"])


def current_user(req: Request):
    u = getattr(req.state, "user", None)
    if not u:
        raise HTTPException(401, "Log in to continue.")
    return u


def require_admin(u):
    if u["role"] != "admin":
        raise HTTPException(403, "Only organization admins can do that.")


DEMO_EMAIL = "demo@sounding.demo"


def is_demo(u):
    return bool(u) and u["email"].lower() == DEMO_EMAIL


def public_user(u):
    return {**{k: u[k] for k in ("id", "email", "name", "role")}, "demo": is_demo(u)} if u else None


# ---------- access ----------
def team_ids(user_id):
    return [r["team_id"] for r in db.many("SELECT team_id FROM team_members WHERE user_id=?", user_id)]


def lead_team_ids(user_id):
    return [r["team_id"] for r in db.many("SELECT team_id FROM team_members WHERE user_id=? AND lead=1", user_id)]


def visible_sql(u, alias="m"):
    """SQL condition + args for meetings this user may view."""
    if u["role"] == "admin":
        return f"{alias}.org_id=?", [u["org_id"]]
    teams = team_ids(u["id"])
    tq = ",".join("?" * len(teams)) or "NULL"
    cond = (f"{alias}.org_id=? AND ({alias}.owner_id=? OR {alias}.visibility='org'"
            f" OR ({alias}.visibility='team' AND {alias}.team_id IN ({tq}))"
            f" OR EXISTS (SELECT 1 FROM meeting_shares s WHERE s.meeting_id={alias}.id AND s.user_id=?)"
            f" OR EXISTS (SELECT 1 FROM folder_items fi JOIN folders f ON f.id=fi.folder_id"
            f"   WHERE fi.meeting_id={alias}.id AND f.shared=1 AND f.org_id={alias}.org_id))")
    return cond, [u["org_id"], u["id"], *teams, u["id"]]


def can_view(u, m):
    if not m or m["org_id"] != u["org_id"]:
        return False
    cond, args = visible_sql(u)
    return db.one(f"SELECT 1 AS ok FROM meetings m WHERE m.id=? AND {cond}", m["id"], *args) is not None


def can_edit(u, m):
    return m and m["org_id"] == u["org_id"] and (m["owner_id"] == u["id"] or u["role"] == "admin")


def meeting_for(u, mid, edit=False):
    m = db.one("SELECT * FROM meetings WHERE id=?", mid)
    if not m or not can_view(u, m):
        raise HTTPException(404, "No meeting with that id, or you don't have access to it.")
    if edit and not can_edit(u, m):
        raise HTTPException(403, "Only the meeting owner or an admin can change this meeting.")
    return m
