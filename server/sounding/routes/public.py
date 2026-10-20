"""Share links: anyone-with-the-link or org-only views of a meeting or a single clip."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from .. import db
from .collab import clip_file
from .meetings import media_file

router = APIRouter()


def resolve(token, req):
    link = db.one("SELECT * FROM share_links WHERE token=? AND revoked=0", token)
    if not link:
        raise HTTPException(404, "This link was turned off or never existed.")
    m = db.one("SELECT * FROM meetings WHERE id=?", link["meeting_id"])
    if not m or m["stage"] != "done":
        raise HTTPException(404, "This recording isn't available.")
    if link["access"] == "org":
        u = getattr(req.state, "user", None)
        if not u or u["org_id"] != m["org_id"]:
            raise HTTPException(401, "Log in with your work account to view this link.")
    h = link["highlight_id"] and db.one("SELECT * FROM highlights WHERE id=?", link["highlight_id"])
    if link["highlight_id"] and not h:
        raise HTTPException(404, "This clip was deleted.")
    return link, m, h


@router.get("/api/public/{token}")
def view(token: str, req: Request):
    link, m, h = resolve(token, req)
    owner = db.one("SELECT name FROM users WHERE id=?", m["owner_id"])
    org = db.one("SELECT name FROM orgs WHERE id=?", m["org_id"])
    base = dict(title=m["title"], date=m["date"], owner=owner and owner["name"], org=org["name"], has_video=bool(m["has_video"]),
                speakers=m["speakers"], names=m["names"])
    if h:
        tr = [t for t in m["transcript"] if t["end"] > h["start"] and t["t"] < h["end"]]
        # Times are relative to the clip, which is all the viewer can play.
        tr = [{**t, "t": max(0, round(t["t"] - h["start"], 2)), "end": round(t["end"] - h["start"], 2)} for t in tr]
        return dict(kind="clip", clip=dict(title=h["title"], note=h["note"], duration=round(h["end"] - h["start"], 2)), transcript=tr, **base)
    return dict(kind="meeting", duration=m["duration"], summary=m["summary"],
                actions=[dict(text=a["text"], done=a["done"], t=a["t"]) for a in m["actions"]], transcript=m["transcript"], **base)


@router.get("/api/public/{token}/media")
def public_media(token: str, req: Request, kind: str = "audio"):
    link, m, h = resolve(token, req)
    p = clip_file(h, kind) if h else media_file(m["id"], kind)
    return FileResponse(p, media_type="video/mp4" if p.endswith(".mp4") else "audio/mpeg")
