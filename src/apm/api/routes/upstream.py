"""Upstream resolution: automatic, manual, and the evidence for either."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query

from ...domain.enums import ErrorCode
from ...domain.models import ResolutionEvidence, UpstreamResolution
from ...services.upstream_service import UpstreamServiceError
from ..deps import container
from ..schemas import (
    ContentBaseApprovalRequest, ManualUpstreamRequest, ResolveRequest,
)

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


@router.post("/upstream/content-base/approve")
def approve_content_base(request: ContentBaseApprovalRequest,
                         app=Depends(container)):
    """Record a person's approval of a content-matched base tag.

    For a fork that shares no git history. The approval names who decided, and
    takes effect when the package is next resolved; nothing is re-resolved or
    compared here.
    """
    if not request.confirm:
        raise UpstreamServiceError(
            ErrorCode.INVALID_REQUEST, "Approving a content base needs confirm=true.")
    resolution = app.upstream.resolve(request.package, request.release)
    match = resolution.content_match
    if match is None or not match.candidates:
        raise UpstreamServiceError(
            ErrorCode.INVALID_REQUEST,
            f"No content match is recorded for {request.package}; it needs "
            f"NO_SHARED_HISTORY and a resolution run first.")
    tag = request.tag or match.base_tag
    candidate = next((c for c in match.candidates if c.tag == tag), None)
    if candidate is None:
        raise UpstreamServiceError(
            ErrorCode.INVALID_REQUEST, f"{tag} was not among the tags compared.")
    listing = app.verifier.list_refs(resolution.upstream_repository.url)
    sha = listing.tags.get(tag) if listing.ok else None
    if not sha:
        raise UpstreamServiceError(
            ErrorCode.INVALID_REF, f"Tag {tag} could not be resolved upstream.",
            listing.error or "")
    return app.approvals.approve(request.package, request.release, {
        "repository": resolution.upstream_repository.url, "base_tag": tag,
        "base_sha": sha, "method": candidate.method, "score": candidate.score,
        "files_compared": candidate.files_compared,
        "files_differing": candidate.files_differing,
        "arcos_tree": candidate.arcos_tree, "arcos_commit": resolution.arcos_commit,
        "verified_by": request.verified_by, "note": request.note or "",
    })
