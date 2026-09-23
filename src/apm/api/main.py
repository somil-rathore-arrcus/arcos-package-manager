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

    errors.install(app)
    return app


app = create_app()
