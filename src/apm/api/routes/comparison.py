"""Git comparison and the commits it produces."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...domain.models import ComparisonResult, Repository
from ..deps import container
from ..schemas import ComparisonRequest

router = APIRouter(tags=["comparison"])


def _resolution(app, request: ComparisonRequest):
    """Resolve automatically, unless the caller supplied an upstream."""
    if request.upstream_repository and request.upstream_ref:
        return app.upstream.resolve_manual(
            request.package, request.release, request.upstream_repository,
            request.upstream_ref, request.arcos_branch,
        )
    return app.upstream.resolve(
        request.package, request.release, request.arcos_branch, request.refresh
    )


@router.post("/comparison", response_model=ComparisonResult)
def compare(request: ComparisonRequest, app=Depends(container)):
    resolution = _resolution(app, request)
    return app.comparison.compare(
        resolution, request.arcos_branch, refresh=request.refresh
    )


@router.get("/comparison/{package}", response_model=ComparisonResult)
def compare_get(
    package: str,
    release: str = Query(description="Debian release"),
    arcos_branch: str = Query(None),
    refresh: bool = Query(False),
    app=Depends(container),
):
    request = ComparisonRequest(
        package=package, release=release, arcos_branch=arcos_branch,
        refresh=refresh,
    )
    return compare(request, app)


@router.get("/commits/{package}")
def commits(
    package: str,
    release: str = Query(description="Debian release"),
    arcos_branch: str = Query(None),
    kind: str = Query(
        "missing",
        description="missing | arcos_only | already_backported | all",
    ),
    app=Depends(container),
):
    """The commit lists on their own, for a table that does not need the summary."""
    result = compare(
        ComparisonRequest(
            package=package, release=release, arcos_branch=arcos_branch
        ),
        app,
    )
    groups = {
        "missing": result.missing_upstream,
        "arcos_only": result.arcos_only,
        "already_backported": result.already_backported,
    }
    if kind == "all":
        return groups
    return groups.get(kind, result.missing_upstream)
