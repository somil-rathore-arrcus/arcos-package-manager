"""The FastAPI application.

Routes validate input and call a service. No git command, GitHub call or
resolution rule lives in this layer.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from . import errors
from .routes import (
    catalog, comparison, health, patches, reports, upstream, upstream_md,
)

log = logging.getLogger(__name__)

API_PREFIX = "/api"


def create_app() -> FastAPI:
    app = FastAPI(
        title="ARCoS Package Manager",
        version=__version__,
        description=(
            "Compare ARCoS package forks against their verified upstreams and "
            "propose the patches worth pulling."
        ),
    )

    origins = [
        o.strip() for o in os.environ.get("APM_CORS_ORIGINS", "").split(",")
        if o.strip()
    ]
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_credentials=True,
            allow_methods=["*"], allow_headers=["*"],
        )

    for module in (health, catalog, upstream, comparison, patches, upstream_md,
                   reports):
        app.include_router(module.router, prefix=API_PREFIX)

    @app.get("/health", tags=["health"])
    def liveness():
        """Liveness for container health checks.

        Answers without touching git, GitHub, the mapping or the disk, so a slow
        remote can never make a working process look unhealthy. /api/health is
        the detailed report of what this deployment can do.
        """
        return {"status": "ok", "version": __version__}

    errors.install(app)
    # Last, so every /api route is matched before the dashboard's catch-all.
    _serve_frontend(app)
    return app


def _serve_frontend(app: FastAPI) -> None:
    """Serve the built dashboard from the same process and port as the API.

    One uvicorn process serves frontend/dist (or APM_FRONTEND_DIST) at / and
    the API at /api, so the browser sees one origin and no web server or second
    port is needed - in the Docker image and in the development fallback alike.
    Absent a build, nothing is mounted and the API runs alone.
    """
    from pathlib import Path

    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from ..config import ROOT

    configured = os.environ.get("APM_FRONTEND_DIST", "").strip()
    dist = Path(configured).expanduser() if configured else ROOT / "frontend" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        log.info("no frontend build at %s; serving the API only", dist)
        return
    root = dist.resolve()
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def dashboard(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail={
                "code": "NOT_FOUND", "message": f"No API route /{path}."})
        candidate = (root / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        # Client-side routes all load the single-page app.
        return FileResponse(index)

    log.info("serving the dashboard from %s", dist)


app = create_app()
