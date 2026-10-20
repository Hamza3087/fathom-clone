#!/usr/bin/env python3
"""Seeds the demo workspace with real recordings through the public API, so the data goes through the same
pipeline a user's upload would. Runs on the server (reads admin credentials from the env file).

Recordings (all real, openly licensed):
  ICSI Meeting Corpus (CC BY 4.0) - hour-long research meetings with 8-9 people
  AMI Meeting Corpus (CC BY 4.0)  - 4-person product design meetings, far-field mic

Usage: python3 seed_demo.py setup     # org config + uploads
       python3 seed_demo.py enrich    # after processing: highlights, comments, folders, playlists, speaker links
"""
import json, os, secrets, subprocess, sys, time, urllib.request, http.cookiejar

B = "http://127.0.0.1:8020/api"
ROOT = "/root/sounding"
env = {}
for line in open(f"{ROOT}/sounding.env"):
    if "=" in line:
        k, v = line.strip().split("=", 1); env[k] = v.strip('"')

jar = http.cookiejar.CookieJar()
o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, body=None):
    r = urllib.request.Request(B + path, json.dumps(body).encode() if body is not None else None,
                               {"Content-Type": "application/json"}, method=method)
    with o.open(r, timeout=600) as resp:
        return json.loads(resp.read() or b"{}")


def login():
    call("POST", "/auth/login", {"email": env["SOUNDING_ADMIN_EMAIL"], "password": env["SOUNDING_PASSWORD"]})


def by_name(items, name, key="name"):
    return next((x for x in items if x[key] == name), None)


def ensure_demo_user():
    sys.path.insert(0, f"{ROOT}/app")
    from sounding import db, auth
    if not db.one("SELECT id FROM users WHERE email=?", auth.DEMO_EMAIL):
        admin = db.one("SELECT * FROM users WHERE email=?", env["SOUNDING_ADMIN_EMAIL"])
        db.insert("users", id=db.new_id(), org_id=admin["org_id"], email=auth.DEMO_EMAIL, name="Demo reviewer",
                  pw_hash=auth.hash_pw(secrets.token_urlsafe(32)), role="admin", active=1, settings={}, created=db.now())
    return db.one("SELECT id FROM users WHERE email=?", auth.DEMO_EMAIL)["id"]


RECORDINGS = [
    # file, title (blank = let Sounding name it), days ago, hour, attendees, visibility, team, meeting type
    ("data/icsi/Bmr007.wav", "", 2, 14, ["alex@icsi.example", "morgan@icsi.example", "sam@icsi.example", "jo@icsi.example"], "org", None, "Research sync"),
    ("data/icsi/Bmr019.wav", "", 6, 11, ["alex@icsi.example", "morgan@icsi.example", "riley@icsi.example"], "org", None, "Research sync"),
    ("data/icsi/Bmr024.wav", "", 9, 15, ["morgan@icsi.example", "sam@icsi.example"], "team", "Research", "Research sync"),
    ("wav/ES2004b.wav", "", 4, 10, ["pm@realreaction.example", "ui@realreaction.example", "marketing@realreaction.example"], "org", None, "Design review"),
    ("wav/TS3003b.wav", "", 7, 16, ["pm@realreaction.example", "design@realreaction.example"], "team", "Product design", "Design review"),
    ("wav/EN2002b.wav", "", 12, 9, ["pm@realreaction.example"], "org", None, ""),
]


def setup():
    login()
    ensure_demo_user()
    teams = call("GET", "/org/teams")
    for name in ("Product design", "Research"):
        if not by_name(teams, name):
            call("POST", "/org/teams", {"name": name})
    teams = call("GET", "/org/teams")
    members = call("GET", "/org/members")
    for m in members:
        for t in teams:
            call("POST", f"/org/teams/{t['id']}/members", {"user_id": m["id"], "lead": m["email"] == env["SOUNDING_ADMIN_EMAIL"]})
    types = call("GET", "/org/meeting-types")
    wanted = {
        "Design review": ("Product design meetings for the remote control project: features, materials, user interface, costs.", "project"),
        "Research sync": ("Research group meetings about speech recognition experiments, data collection, results and papers.", "project"),
        "Project kickoff": ("The first meeting of a new project: introductions, goals, roles and the plan.", "general"),
    }
    for name, (desc, tpl) in wanted.items():
        t = by_name(types, name)
        body = {"name": name, "description": desc, "template": tpl}
        call("PATCH", f"/org/meeting-types/{t['id']}", body) if t else call("POST", "/org/meeting-types", body)
    types = {t["name"]: t["id"] for t in call("GET", "/org/meeting-types")}
    cards = call("GET", "/org/scorecards")
    if not by_name(cards, "Research sync health"):
        call("POST", "/org/scorecards", {"name": "Research sync health", "meeting_type_id": types["Research sync"], "criteria": [
            {"name": "Clear agenda", "description": "The meeting states what it will cover near the start."},
            {"name": "Decisions recorded", "description": "Decisions and owners are stated out loud, not left implicit."},
            {"name": "Everyone heard", "description": "Quieter participants are invited in; no single voice dominates."}]})
    trackers = call("GET", "/trackers")
    for t in [dict(name="Deadlines and dates", kind="ai", description="Any deadline, due date, schedule or timing commitment for work or deliverables."),
              dict(name="Data and recordings", kind="keyword", keywords=["data", "recording", "corpus", "transcripts"]),
              dict(name="Pricing", kind="keyword", keywords=["price", "euro", "cost", "budget"])]:
        if not by_name(trackers, t["name"]):
            call("POST", "/trackers", t)
    existing = {m["title"] for m in call("GET", "/meetings")}
    team_ids = {t["name"]: t["id"] for t in teams}
    cookie = next(c.value for c in jar if c.name == "sounding_session")
    for path, title, ago, hour, attendees, vis, team, mtype in RECORDINGS:
        stamp = time.strftime("%Y-%m-%dT", time.localtime(time.time() - ago * 86400)) + f"{hour:02d}:00"
        marker = os.path.basename(path)
        if any(marker.split(".")[0] in t for t in existing):
            continue
        # curl streams the file; the session cookie goes in a header file so it never shows in ps
        hdr = f"{ROOT}/.seed-cookie"
        with open(hdr, "w") as f: f.write(f"Cookie: sounding_session={cookie}\n")
        os.chmod(hdr, 0o600)
        out = subprocess.check_output(["curl", "-s", "-H", f"@{hdr}", "-F", f"file=@{ROOT}/{path}", "-F", f"title={title}",
                                       "-F", f"date={stamp}", "-F", f"visibility={vis}", "-F", f"team_id={team_ids.get(team, '') if team else ''}",
                                       "-F", f"attendees={', '.join(attendees)}", "-F", f"meeting_type_id={types.get(mtype, '')}",
                                       f"{B}/meetings"])
        os.remove(hdr)
        print("uploaded", path, json.loads(out))


def enrich():
    login()
    ms = [m for m in call("GET", "/meetings") if m["stage"] == "done"]
    folders = call("GET", "/folders")
    f_design = by_name(folders, "Remote control project") or call("POST", "/folders", {"name": "Remote control project", "shared": True})
    f_research = by_name(folders, "Speech research") or call("POST", "/folders", {"name": "Speech research", "shared": True})
    pl = by_name(call("GET", "/playlists"), "Decisions worth rewatching") or call("POST", "/playlists", {"name": "Decisions worth rewatching", "shared": True})
    for m in ms:
        full = call("GET", f"/meetings/{m['id']}")
        is_icsi = (full["meeting_type"] or {}).get("name") == "Research sync"
        call("POST", f"/folders/{(f_research if is_icsi else f_design)['id']}/items", {"meeting_id": m["id"]})
        if call("GET", f"/meetings/{m['id']}/highlights"):
            continue
        # Clip the moment behind the first decision (or first takeaway) and add it to the playlist.
        bullets = [b for s in (full["summary"] or {}).get("sections", []) for b in s["bullets"] if b.get("t") is not None]
        dec = next((b for s in full["summary"]["sections"] if s["title"] in ("Decisions", "Key takeaways") for b in s["bullets"] if b.get("t") is not None), None)
        if dec:
            h = call("POST", f"/meetings/{m['id']}/highlights", {"start": max(0, dec["t"] - 15), "end": dec["t"] + 25, "title": dec["text"][:110]})
            call("POST", f"/playlists/{pl['id']}/items", {"highlight_id": h["id"]})
        if len(bullets) > 2:
            b = bullets[2]
            call("POST", f"/meetings/{m['id']}/comments", {"body": "Worth a second listen before we plan next week.", "t": b["t"], "mentions": []})
        print("enriched", full["title"])


if __name__ == "__main__":
    {"setup": setup, "enrich": enrich}[sys.argv[1]]()
