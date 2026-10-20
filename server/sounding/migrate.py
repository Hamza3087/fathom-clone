"""First start: create the organization and admin account, and import meetings from the single-user version."""
import glob, json, logging, os
from . import db, auth, media, jobs

log = logging.getLogger("sounding")


def run():
    if db.one("SELECT id FROM orgs LIMIT 1"):
        return
    email = os.environ.get("SOUNDING_ADMIN_EMAIL", "").strip()
    pw = os.environ.get("SOUNDING_PASSWORD", "")
    if not email or not pw:
        log.error("Set SOUNDING_ADMIN_EMAIL and SOUNDING_PASSWORD in the env file to create the first admin.")
        return
    oid, uid = db.new_id(), db.new_id()
    db.insert("orgs", id=oid, name=os.environ.get("SOUNDING_ORG_NAME", "My organization"), settings={}, created=db.now())
    db.insert("users", id=uid, org_id=oid, email=email, name=os.environ.get("SOUNDING_ADMIN_NAME", email.split("@")[0]),
              pw_hash=auth.hash_pw(pw), role="admin", active=1, settings={}, created=db.now())
    for f in glob.glob(os.path.join(db.MEDIA, "*", "meeting.json")):
        m = json.load(open(f))
        if m.get("stage") != "done":
            continue
        s = m.get("summary") or {}
        sections = [dict(title="Key takeaways", bullets=[dict(text=p, t=None) for p in s.get("points", [])]),
                    dict(title="Topics discussed", bullets=[]),
                    dict(title="Decisions", bullets=[dict(text=p, t=None) for p in s.get("decisions", [])]),
                    dict(title="Next steps", bullets=[])]
        acts = [dict(id=a["id"], text=a["text"], speaker=a.get("owner"), assignee=None, t=a.get("t"), done=a.get("done", False))
                for a in m.get("actions", [])]
        db.insert("meetings", id=m["id"], org_id=oid, owner_id=uid, title=m["title"], title_set=int(m.get("title_set", False)),
                  date=m["date"], duration=m.get("duration"), stage="done", source=m.get("source"), has_video=0, visibility="org",
                  template="general", speakers=m.get("speakers", []), names=m.get("names", {}), transcript=m.get("transcript", []),
                  summary=dict(template="general", overview=s.get("overview", ""), sections=sections), actions=acts,
                  metrics=media.metrics(m.get("transcript", []), m.get("duration") or 0), timings=m.get("timings"),
                  created=db.now(), updated=db.now())
        os.rename(f, f + ".imported")
        jobs.enqueue("resummarize", m["id"], template="general")  # old notes had no timestamps
        log.info("imported meeting %s", m["id"])
