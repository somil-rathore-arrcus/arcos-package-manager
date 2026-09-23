"""Releases, packages and branches - everything the selectors need.

All of it is discovered. Nothing here knows how many packages there are, and
nothing assumes a branch name.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...domain.models import Branch, DebianRelease, Package
from ..deps import container

router = APIRouter(tags=["catalog"])


@router.get("/releases", response_model=List[DebianRelease])
def releases(app=Depends(container)):
    return app.catalog.releases()


@router.get("/packages", response_model=List[Package])
def packages(
    release: Optional[str] = Query(
        None, description="Only packages this release's manifest contains."
    ),
    app=Depends(container),
):
    return app.catalog.packages(release)


@router.get("/packages/{package}", response_model=Package)
def package(package: str, app=Depends(container)):
    found = app.catalog.package(package)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"No package '{package}'."},
        )
    return found


@router.get("/branches", response_model=List[Branch])
def branches(
    package: str = Query(description="Package name"),
    release: str = Query(description="Debian release"),
    app=Depends(container),
):
    """The ARCoS fork's real branches, with the shipped one flagged."""
    return app.catalog.branches(package, release)
