"""SQLite storage. One short-lived connection per call; WAL lets the worker write while requests read."""
import json, os, sqlite3, time, secrets

ROOT = os.environ.get("SOUNDING_ROOT", "/root/sounding")
DB_PATH = os.path.join(ROOT, "sounding.db")
MEDIA = os.path.join(ROOT, "meetings")

SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (id TEXT PRIMARY KEY, name TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES orgs(id), email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL, pw_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member', active INTEGER NOT NULL DEFAULT 1,
  settings TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, org_id TEXT NOT NULL, name TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS team_members (team_id TEXT NOT NULL, user_id TEXT NOT NULL, lead INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (team_id, user_id));
CREATE TABLE IF NOT EXISTS invites (token TEXT PRIMARY KEY, org_id TEXT NOT NULL, email TEXT, role TEXT NOT NULL DEFAULT 'member',
  team_id TEXT, created_by TEXT NOT NULL, created REAL NOT NULL, used_by TEXT);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);

CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY, org_id TEXT NOT NULL, owner_id TEXT NOT NULL, title TEXT NOT NULL, title_set INTEGER NOT NULL DEFAULT 0,
  date TEXT NOT NULL, duration REAL, stage TEXT NOT NULL, error TEXT, source TEXT, has_video INTEGER NOT NULL DEFAULT 0,
  visibility TEXT NOT NULL DEFAULT 'private', team_id TEXT, meeting_type_id TEXT, template TEXT,
  attendees TEXT NOT NULL DEFAULT '[]', speakers TEXT NOT NULL DEFAULT '[]', names TEXT NOT NULL DEFAULT '{}',
  speaker_users TEXT NOT NULL DEFAULT '{}', transcript TEXT NOT NULL DEFAULT '[]', summary TEXT, translations TEXT NOT NULL DEFAULT '{}',
  actions TEXT NOT NULL DEFAULT '[]', metrics TEXT, timings TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS meetings_org ON meetings(org_id, date);
CREATE TABLE IF NOT EXISTS meeting_shares (meeting_id TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY (meeting_id, user_id));
CREATE TABLE IF NOT EXISTS share_links (token TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, highlight_id TEXT, access TEXT NOT NULL,
  created_by TEXT NOT NULL, created REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS highlights (id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, user_id TEXT NOT NULL, start REAL NOT NULL,
  end REAL NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS playlists (id TEXT PRIMARY KEY, org_id TEXT NOT NULL, owner_id TEXT NOT NULL, name TEXT NOT NULL,
  shared INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS playlist_items (playlist_id TEXT NOT NULL, highlight_id TEXT NOT NULL, pos REAL NOT NULL,
  PRIMARY KEY (playlist_id, highlight_id));
CREATE TABLE IF NOT EXISTS folders (id TEXT PRIMARY KEY, org_id TEXT NOT NULL, owner_id TEXT NOT NULL, name TEXT NOT NULL,
  shared INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS folder_items (folder_id TEXT NOT NULL, meeting_id TEXT NOT NULL, added REAL NOT NULL,
  PRIMARY KEY (folder_id, meeting_id));
CREATE TABLE IF NOT EXISTS comments (id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, user_id TEXT NOT NULL, t REAL,
  body TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notifications (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, meeting_id TEXT,
  body TEXT NOT NULL, link TEXT, created REAL NOT NULL, read INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS notifications_user ON notifications(user_id, created);

CREATE TABLE IF NOT EXISTS meeting_types (id TEXT PRIMARY KEY, org_id TEXT NOT NULL, name TEXT NOT NULL,
  description TEXT NOT NULL, template TEXT NOT NULL DEFAULT 'general', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS templates (id TEXT PRIMARY KEY, org_id TEXT NOT NULL, name TEXT NOT NULL, base TEXT NOT NULL,
  instructions TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS trackers (id TEXT PRIMARY KEY, org_id TEXT NOT NULL, owner_id TEXT NOT NULL, name TEXT NOT NULL,
  kind TEXT NOT NULL, terms TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tracker_hits (tracker_id TEXT NOT NULL, meeting_id TEXT NOT NULL, t REAL NOT NULL, quote TEXT NOT NULL,
  PRIMARY KEY (tracker_id, meeting_id, t));
CREATE TABLE IF NOT EXISTS tracker_subs (tracker_id TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY (tracker_id, user_id));
CREATE TABLE IF NOT EXISTS scorecards (id TEXT PRIMARY KEY, org_id TEXT NOT NULL, meeting_type_id TEXT NOT NULL, name TEXT NOT NULL,
  criteria TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scorecard_results (scorecard_id TEXT NOT NULL, meeting_id TEXT NOT NULL, user_id TEXT,
  score REAL NOT NULL, details TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY (scorecard_id, meeting_id));

CREATE TABLE IF NOT EXISTS api_keys (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
  prefix TEXT NOT NULL, created REAL NOT NULL, last_used REAL);
CREATE TABLE IF NOT EXISTS webhooks (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, url TEXT NOT NULL, secret TEXT NOT NULL,
  include TEXT NOT NULL, created REAL NOT NULL, last_status TEXT);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, ref TEXT NOT NULL, args TEXT NOT NULL DEFAULT '{}',
  state TEXT NOT NULL DEFAULT 'queued', error TEXT, created REAL NOT NULL);
"""

JSON_COLS = {"settings", "attendees", "speakers", "names", "speaker_users", "transcript", "summary", "translations",
             "actions", "metrics", "timings", "terms", "criteria", "details", "include", "args"}


def connect():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init():
    os.makedirs(MEDIA, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)


def row(r):
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if isinstance(d[k], str):
            d[k] = json.loads(d[k])
    return d


def one(sql, *args):
    with connect() as c:
        return row(c.execute(sql, args).fetchone())


def many(sql, *args):
    with connect() as c:
        return [row(r) for r in c.execute(sql, args).fetchall()]


def run(sql, *args):
    with connect() as c:
        return c.execute(sql, args).rowcount


def enc(v):
    return json.dumps(v) if isinstance(v, (dict, list)) else v


def insert(table, **vals):
    keys = list(vals)
    with connect() as c:
        c.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})", [enc(vals[k]) for k in keys])
    return vals


def update(table, key, key_val, **vals):
    if not vals:
        return 0
    sets = ",".join(f"{k}=?" for k in vals)
    with connect() as c:
        return c.execute(f"UPDATE {table} SET {sets} WHERE {key}=?", [enc(v) for v in vals.values()] + [key_val]).rowcount


def new_id(n=10):
    return secrets.token_hex(n // 2 + 1)[:n]


def now():
    return time.time()
