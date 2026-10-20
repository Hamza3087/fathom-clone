"""Sounding API. Binds 127.0.0.1; Apache (port 3000) and the SSH tunnel are the ways in."""
import logging, os, re, time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from . import db, auth, jobs, migrate
from .routes import account, org, meetings, collab, intel, public, api_v1

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

OPEN = ("/api/auth/login", "/api/auth/demo", "/api/auth/invite/", "/api/public/", "/api/health")


@app.middleware("http")
async def identify(req: Request, call_next):
    path = req.url.path
    req.state.user = None
    if path.startswith("/api/v1/"):
        req.state.user = auth.user_from_api_key(req.headers.get("x-api-key", ""))
        if not req.state.user:
            return JSONResponse({"detail": "Send a valid API key in the X-Api-Key header."}, status_code=401)
    elif path.startswith("/api/"):
        req.state.user = auth.user_from_session(req.cookies.get(auth.COOKIE))
        if not req.state.user and not path.startswith(OPEN):
            return JSONResponse({"detail": "Log in to continue."}, status_code=401)
        if auth.is_demo(req.state.user) and req.method != "GET" and DEMO_BLOCKED.match(path):
            return JSONResponse({"detail": "The demo workspace can't change this. Everything else works, so try it on your own copy."},
                                status_code=403)
    return await call_next(req)


# Shared demo account: people, access, credentials, outbound webhooks and the org name stay fixed.
DEMO_BLOCKED = re.compile(r"^/api/(org/members|org/invites|org/settings|me$|me/api-keys|me/webhooks)")


@app.get("/api/health")
def health():
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:8011/v1/models", timeout=3); q = True
    except Exception:
        q = False
    return dict(ok=True, qwen=q, queued=db.one("SELECT COUNT(*) AS n FROM jobs WHERE state IN ('queued','running')")["n"])


for r in (account, org, meetings, collab, intel, public, api_v1):
    app.include_router(r.router)

db.init()
migrate.run()
jobs.start()

WEB = os.path.join(db.ROOT, "web")
if os.path.isdir(WEB):  # the built UI, after the API routes
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
