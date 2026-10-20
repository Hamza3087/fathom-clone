"""Highlights & clips, comments, folders, playlists, notifications."""
import os
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from .. import db, auth, jobs, media

router = APIRouter()


# ---------- highlights / clips ----------
def clip_file(h, kind="audio"):
    """Cuts a clip once and caches it, so a shared clip never exposes the rest of the recording."""
    work = jobs.mdir(h["meeting_id"])
    m = db.one("SELECT has_video FROM meetings WHERE id=?", h["meeting_id"])
    video = kind == "video" and m and m["has_video"]
    out = f"{work}/{h['id']}.clip.{'mp4' if video else 'mp3'}"
    if not os.path.exists(out):
        src = f"{work}/{'video.mp4' if video else 'audio.mp3'}"
        if not os.path.exists(src):
            raise HTTPException(404, "The recording is still being prepared.")
        args = ["-ss", f"{h['start']:.2f}", "-to", f"{h['end']:.2f}", "-i", src]
        if video:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-movflags", "+faststart", out]
        else:
            args += ["-c:a", "libmp3lame", "-b:a", "48k", out]
        media.ffmpeg(*args)
    return out


def highlight_rows(where, *args):
    return db.many(f"SELECT h.*, u.name AS user_name, m.title AS meeting_title, m.has_video FROM highlights h "
                   f"JOIN users u ON u.id=h.user_id JOIN meetings m ON m.id=h.meeting_id WHERE {where}", *args)


class HighlightIn(BaseModel):
    start: float
    end: float
    title: str = ""
    note: str = ""


@router.get("/api/meetings/{mid}/highlights")
def list_highlights(mid: str, req: Request):
    u = auth.current_user(req); auth.meeting_for(u, mid)
    return highlight_rows("h.meeting_id=? ORDER BY h.start", mid)


@router.post("/api/meetings/{mid}/highlights")
def create_highlight(mid: str, body: HighlightIn, req: Request):
    u = auth.current_user(req); m = auth.meeting_for(u, mid)
    a, b = max(0.0, body.start), min(m["duration"] or body.end, body.end)
    if b - a < 1:
        raise HTTPException(400, "A clip has to be at least one second long.")
    if b - a > 1800:
        raise HTTPException(400, "Clips can be at most 30 minutes long.")
    hid = db.new_id()
    title = body.title.strip()[:120]
    if not title:
        words = " ".join(t["x"] for t in m["transcript"] if t["end"] > a and t["t"] < b).split()
        title = " ".join(words[:10]) + ("…" if len(words) > 10 else "") or "Highlight"
    db.insert("highlights", id=hid, meeting_id=mid, user_id=u["id"], start=round(a, 2), end=round(b, 2), title=title,
              note=body.note.strip()[:1000], created=db.now())
    return highlight_rows("h.id=?", hid)[0]


def highlight_for(u, hid, edit=False):
    h = db.one("SELECT * FROM highlights WHERE id=?", hid)
    if not h:
        raise HTTPException(404, "No such clip.")
    m = auth.meeting_for(u, h["meeting_id"])
    if edit and h["user_id"] != u["id"] and not auth.can_edit(u, m):
        raise HTTPException(403, "Only whoever made the clip, the meeting owner or an admin can change it.")
    return h


class HighlightPatch(BaseModel):
    start: float | None = None
    end: float | None = None
    title: str | None = None
    note: str | None = None


@router.patch("/api/highlights/{hid}")
def patch_highlight(hid: str, body: HighlightPatch, req: Request):
    u = auth.current_user(req); h = highlight_for(u, hid, edit=True)
    vals = {}
    if body.title is not None: vals["title"] = body.title.strip()[:120] or h["title"]
    if body.note is not None: vals["note"] = body.note.strip()[:1000]
    start = h["start"] if body.start is None else body.start
    end = h["end"] if body.end is None else body.end
    if (start, end) != (h["start"], h["end"]) and end - start >= 1:
        body.start, body.end = start, end
        vals.update(start=round(body.start, 2), end=round(body.end, 2))
        for f in ("clip.mp3", "clip.mp4"):
            p = f"{jobs.mdir(h['meeting_id'])}/{hid}.{f}"
            if os.path.exists(p): os.remove(p)
    db.update("highlights", "id", hid, **vals)
    return highlight_rows("h.id=?", hid)[0]


@router.delete("/api/highlights/{hid}")
def delete_highlight(hid: str, req: Request):
    u = auth.current_user(req); h = highlight_for(u, hid, edit=True)
    db.run("DELETE FROM playlist_items WHERE highlight_id=?", hid); db.run("DELETE FROM share_links WHERE highlight_id=?", hid)
    db.run("DELETE FROM highlights WHERE id=?", hid)
    for f in ("clip.mp3", "clip.mp4"):
        p = f"{jobs.mdir(h['meeting_id'])}/{hid}.{f}"
        if os.path.exists(p): os.remove(p)
    return {"ok": True}


def playlist_can_see(u, pid):
    p = db.one("SELECT * FROM playlists WHERE id=? AND org_id=?", pid, u["org_id"])
    return p if p and (p["shared"] or p["owner_id"] == u["id"] or u["role"] == "admin") else None


@router.get("/api/highlights/{hid}/clip")
def get_clip(hid: str, req: Request, kind: str = "audio"):
    u = auth.current_user(req)
    h = db.one("SELECT * FROM highlights WHERE id=?", hid)
    if not h:
        raise HTTPException(404, "No such clip.")
    m = db.one("SELECT * FROM meetings WHERE id=?", h["meeting_id"])
    # Clips are visible to anyone who can see the meeting, or who can see a playlist the clip is in.
    in_playlist = any(playlist_can_see(u, r["playlist_id"]) for r in db.many("SELECT playlist_id FROM playlist_items WHERE highlight_id=?", hid))
    if not (auth.can_view(u, m) or in_playlist):
        raise HTTPException(404, "No such clip.")
    p = clip_file(h, kind)
    return FileResponse(p, media_type="video/mp4" if p.endswith(".mp4") else "audio/mpeg")


# ---------- comments ----------
class CommentIn(BaseModel):
    body: str
    t: float | None = None
    mentions: list[str] = []


@router.get("/api/meetings/{mid}/comments")
def list_comments(mid: str, req: Request):
    u = auth.current_user(req); auth.meeting_for(u, mid)
    return db.many("SELECT c.*, u.name AS user_name FROM comments c JOIN users u ON u.id=c.user_id WHERE c.meeting_id=? ORDER BY c.created", mid)


@router.post("/api/meetings/{mid}/comments")
def add_comment(mid: str, body: CommentIn, req: Request):
    u = auth.current_user(req); m = auth.meeting_for(u, mid)
    text = body.body.strip()[:4000]
    if not text:
        raise HTTPException(400, "Write something first.")
    cid = db.new_id()
    db.insert("comments", id=cid, meeting_id=mid, user_id=u["id"], t=body.t, body=text, created=db.now())
    notified = set()
    for uid in body.mentions:
        target = db.one("SELECT * FROM users WHERE id=? AND org_id=? AND active=1", uid, u["org_id"])
        if target and uid != u["id"] and uid not in notified:
            notified.add(uid)
            if not auth.can_view(target, m):  # mentioning someone gives them access, like Fathom
                db.run("INSERT OR IGNORE INTO meeting_shares (meeting_id, user_id) VALUES (?,?)", mid, uid)
            jobs.notify(uid, "mention", f"{u['name']} mentioned you on {m['title']}: {text[:140]}", mid, f"#/meeting/{mid}?tab=comments")
    if m["owner_id"] != u["id"] and m["owner_id"] not in notified:
        jobs.notify(m["owner_id"], "comment", f"{u['name']} commented on {m['title']}: {text[:140]}", mid, f"#/meeting/{mid}?tab=comments")
    return db.one("SELECT c.*, u.name AS user_name FROM comments c JOIN users u ON u.id=c.user_id WHERE c.id=?", cid)


@router.delete("/api/comments/{cid}")
def delete_comment(cid: str, req: Request):
    u = auth.current_user(req)
    c = db.one("SELECT * FROM comments WHERE id=?", cid)
    if not c:
        raise HTTPException(404, "No such comment.")
    m = auth.meeting_for(u, c["meeting_id"])
    if c["user_id"] != u["id"] and not auth.can_edit(u, m):
        raise HTTPException(403, "You can only delete your own comments.")
    db.run("DELETE FROM comments WHERE id=?", cid)
    return {"ok": True}


# ---------- folders ----------
class FolderIn(BaseModel):
    name: str
    shared: bool = False


def folder_for(u, fid, edit=False):
    f = db.one("SELECT * FROM folders WHERE id=? AND org_id=?", fid, u["org_id"])
    if not f or not (f["shared"] or f["owner_id"] == u["id"] or u["role"] == "admin"):
        raise HTTPException(404, "No such folder.")
    if edit and f["owner_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "Only the folder owner or an admin can change this folder.")
    return f


@router.get("/api/folders")
def list_folders(req: Request):
    u = auth.current_user(req)
    return db.many("SELECT f.*, u.name AS owner_name, (SELECT COUNT(*) FROM folder_items i WHERE i.folder_id=f.id) AS count "
                   "FROM folders f JOIN users u ON u.id=f.owner_id WHERE f.org_id=? AND (f.shared=1 OR f.owner_id=? OR ?='admin') ORDER BY f.name",
                   u["org_id"], u["id"], u["role"])


@router.post("/api/folders")
def create_folder(body: FolderIn, req: Request):
    u = auth.current_user(req)
    fid = db.new_id()
    db.insert("folders", id=fid, org_id=u["org_id"], owner_id=u["id"], name=body.name.strip()[:80] or "New folder",
              shared=int(body.shared), created=db.now())
    return {"id": fid}


@router.patch("/api/folders/{fid}")
def patch_folder(fid: str, body: FolderIn, req: Request):
    u = auth.current_user(req); folder_for(u, fid, edit=True)
    db.update("folders", "id", fid, name=body.name.strip()[:80], shared=int(body.shared))
    return {"ok": True}


@router.delete("/api/folders/{fid}")
def delete_folder(fid: str, req: Request):
    u = auth.current_user(req); folder_for(u, fid, edit=True)
    db.run("DELETE FROM folder_items WHERE folder_id=?", fid); db.run("DELETE FROM folders WHERE id=?", fid)
    return {"ok": True}


@router.get("/api/folders/{fid}")
def get_folder(fid: str, req: Request):
    u = auth.current_user(req); f = folder_for(u, fid)
    from .meetings import LIST_COLS, list_row, lookup, org_domains
    rows = db.many(f"SELECT {LIST_COLS} FROM meetings m JOIN folder_items i ON i.meeting_id=m.id WHERE i.folder_id=? ORDER BY m.date DESC", fid)
    users, teams, types = lookup(u["org_id"]); own = org_domains(u["org_id"])
    f["meetings"] = [list_row(m, users, teams, types, own, u) for m in rows]
    f["can_edit"] = f["owner_id"] == u["id"] or u["role"] == "admin"
    return f


class FolderItem(BaseModel):
    meeting_id: str


@router.post("/api/folders/{fid}/items")
def add_to_folder(fid: str, body: FolderItem, req: Request):
    u = auth.current_user(req); f = folder_for(u, fid)
    # Adding to a shared folder grants everyone in the org access, so it needs edit rights on the meeting.
    auth.meeting_for(u, body.meeting_id, edit=bool(f["shared"]))
    if f["owner_id"] != u["id"] and u["role"] != "admin" and not f["shared"]:
        raise HTTPException(403, "Only the folder owner can add to a private folder.")
    db.run("INSERT OR IGNORE INTO folder_items (folder_id, meeting_id, added) VALUES (?,?,?)", fid, body.meeting_id, db.now())
    return {"ok": True}


@router.delete("/api/folders/{fid}/items/{mid}")
def remove_from_folder(fid: str, mid: str, req: Request):
    u = auth.current_user(req); folder_for(u, fid, edit=True)
    db.run("DELETE FROM folder_items WHERE folder_id=? AND meeting_id=?", fid, mid)
    return {"ok": True}


# ---------- playlists ----------
class PlaylistIn(BaseModel):
    name: str
    shared: bool = False


@router.get("/api/playlists")
def list_playlists(req: Request):
    u = auth.current_user(req)
    return db.many("SELECT p.*, u.name AS owner_name, (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id=p.id) AS count "
                   "FROM playlists p JOIN users u ON u.id=p.owner_id WHERE p.org_id=? AND (p.shared=1 OR p.owner_id=? OR ?='admin') ORDER BY p.name",
                   u["org_id"], u["id"], u["role"])


@router.post("/api/playlists")
def create_playlist(body: PlaylistIn, req: Request):
    u = auth.current_user(req)
    pid = db.new_id()
    db.insert("playlists", id=pid, org_id=u["org_id"], owner_id=u["id"], name=body.name.strip()[:80] or "New playlist",
              shared=int(body.shared), created=db.now())
    return {"id": pid}


def playlist_edit(u, pid):
    p = playlist_can_see(u, pid)
    if not p:
        raise HTTPException(404, "No such playlist.")
    if p["owner_id"] != u["id"] and u["role"] != "admin":
        raise HTTPException(403, "Only the playlist owner or an admin can change it.")
    return p


@router.patch("/api/playlists/{pid}")
def patch_playlist(pid: str, body: PlaylistIn, req: Request):
    u = auth.current_user(req); playlist_edit(u, pid)
    db.update("playlists", "id", pid, name=body.name.strip()[:80], shared=int(body.shared))
    return {"ok": True}


@router.delete("/api/playlists/{pid}")
def delete_playlist(pid: str, req: Request):
    u = auth.current_user(req); playlist_edit(u, pid)
    db.run("DELETE FROM playlist_items WHERE playlist_id=?", pid); db.run("DELETE FROM playlists WHERE id=?", pid)
    return {"ok": True}


@router.get("/api/playlists/{pid}")
def get_playlist(pid: str, req: Request):
    u = auth.current_user(req)
    p = playlist_can_see(u, pid)
    if not p:
        raise HTTPException(404, "No such playlist.")
    p["items"] = db.many("SELECT h.*, i.pos, u.name AS user_name, m.title AS meeting_title, m.has_video FROM playlist_items i "
                         "JOIN highlights h ON h.id=i.highlight_id JOIN users u ON u.id=h.user_id JOIN meetings m ON m.id=h.meeting_id "
                         "WHERE i.playlist_id=? ORDER BY i.pos", pid)
    for it in p["items"]:
        it["can_open_meeting"] = auth.can_view(u, db.one("SELECT * FROM meetings WHERE id=?", it["meeting_id"]))
    p["can_edit"] = p["owner_id"] == u["id"] or u["role"] == "admin"
    return p


class PlaylistItem(BaseModel):
    highlight_id: str


@router.post("/api/playlists/{pid}/items")
def add_to_playlist(pid: str, body: PlaylistItem, req: Request):
    u = auth.current_user(req); playlist_edit(u, pid); highlight_for(u, body.highlight_id)
    pos = (db.one("SELECT MAX(pos) AS p FROM playlist_items WHERE playlist_id=?", pid)["p"] or 0) + 1
    db.run("INSERT OR IGNORE INTO playlist_items (playlist_id, highlight_id, pos) VALUES (?,?,?)", pid, body.highlight_id, pos)
    return {"ok": True}


@router.delete("/api/playlists/{pid}/items/{hid}")
def remove_from_playlist(pid: str, hid: str, req: Request):
    u = auth.current_user(req); playlist_edit(u, pid)
    db.run("DELETE FROM playlist_items WHERE playlist_id=? AND highlight_id=?", pid, hid)
    return {"ok": True}


class Order(BaseModel):
    order: list[str]


@router.put("/api/playlists/{pid}/order")
def reorder(pid: str, body: Order, req: Request):
    u = auth.current_user(req); playlist_edit(u, pid)
    for i, hid in enumerate(body.order):
        db.run("UPDATE playlist_items SET pos=? WHERE playlist_id=? AND highlight_id=?", i, pid, hid)
    return {"ok": True}


# ---------- notifications ----------
@router.get("/api/notifications")
def notifications(req: Request):
    u = auth.current_user(req)
    items = db.many("SELECT * FROM notifications WHERE user_id=? ORDER BY created DESC LIMIT 50", u["id"])
    unread = db.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read=0", u["id"])["n"]
    return dict(items=items, unread=unread)


class ReadIn(BaseModel):
    ids: list[str] = []
    all: bool = False


@router.post("/api/notifications/read")
def mark_read(body: ReadIn, req: Request):
    u = auth.current_user(req)
    if body.all:
        db.run("UPDATE notifications SET read=1 WHERE user_id=?", u["id"])
    for i in body.ids:
        db.run("UPDATE notifications SET read=1 WHERE id=? AND user_id=?", i, u["id"])
    return {"ok": True}
