"""Readiness, and an honest statement of what this deployment can do."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ... import __version__
from ..deps import container
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(app=Depends(container)):
    return HealthResponse(version=__version__, capabilities=app.capabilities())


@router.get("/health/workspace")
def workspace(app=Depends(container)):
    """Check the git host is reachable and its workspace writable.

    Worth having separately: a workspace that cannot be created fails every
    comparison later, and the cause is much clearer here.
    """
    return app.workspaces.probe()
