"""Upstream resolution: automatic, manual, and the evidence for either."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query

from ...domain.models import ResolutionEvidence, UpstreamResolution
from ..deps import container
from ..schemas import ManualUpstreamRequest, ResolveRequest

router = APIRouter(tags=["upstream"])


@router.get("/upstream/{package}", response_model=UpstreamResolution)
def get_upstream(
    package: str,
    release: str = Query(description="Debian release"),
    arcos_branch: str = Query(None),
    refresh: bool = Query(False),
    app=Depends(container),
):
    return app.upstream.resolve(package, release, arcos_branch, refresh)


@router.post("/upstream/resolve", response_model=UpstreamResolution)
def resolve(request: ResolveRequest, app=Depends(container)):
    return app.upstream.resolve(
        request.package, request.release, request.arcos_branch, request.refresh
    )


@router.post("/upstream/verify", response_model=UpstreamResolution)
def verify_manual(request: ManualUpstreamRequest, app=Depends(container)):
    """Check a user-supplied upstream. Manual does not mean unchecked."""
    return app.upstream.resolve_manual(
        request.package, request.release, request.repository, request.ref,
        request.arcos_branch,
    )


@router.get(
    "/upstream/{package}/evidence", response_model=List[ResolutionEvidence]
)
def evidence(
    package: str,
    release: str = Query(description="Debian release"),
    app=Depends(container),
):
    return app.upstream.resolve(package, release).evidence
