"""FastAPI 入口：uvicorn c2b.api.main:app"""
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import __version__
from ..db import get_session, init_db, recover_stale_label_jobs
from .routes import router


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    init_db()
    # The deployed API runs one process with daemon-thread workers. A process
    # restart destroys those workers, so clear their visible job locks before
    # accepting requests; otherwise a project can remain "running" forever.
    recover_stale_label_jobs(force=True)
    yield


app = FastAPI(title="Code2Business API", version=__version__, lifespan=_lifespan)
if os.environ.get("C2B_DESKTOP_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
    # The Desktop WebView is a different origin from the loopback API.  Keep
    # this exception local to the packaged app instead of relaxing CORS for
    # normal browser/server deployments.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            # Tauri development serves the same desktop WebView from Vite.
            # These loopback-only origins are not enabled in Web/API mode.
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(router)


@app.get("/healthz")
def healthz(session: Session = Depends(get_session)):
    """Unprotected liveness/readiness probe that also verifies the database."""
    session.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__}


@app.middleware("http")
async def _optional_api_token(request, call_next):
    """Protect remote deployments when ``C2B_API_TOKEN`` is configured.

    Local-only deployments remain zero-configuration.  A reverse proxy can
    inject ``X-C2B-Token`` or clients can use a standard Bearer token.
    """
    expected = os.environ.get("C2B_API_TOKEN", "")
    if expected and request.url.path.startswith("/api") and request.method != "OPTIONS":
        authorization = request.headers.get("authorization", "")
        supplied = request.headers.get("x-c2b-token", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not supplied or not secrets.compare_digest(supplied, expected):
            return JSONResponse(
                status_code=401,
                content={"detail": "缺少或无效的 API 访问令牌"},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)
