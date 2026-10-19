"""Where a pushed branch stands - for both the debian/upstream.md and the patch
workflow. Reads only: git refs over SSH, and the GitHub API when configured."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from ...domain.enums import ErrorCode
from ...domain.models import PublishStatusReport
from ...services.publish_status import PublishStatusError
from ..deps import container

router = APIRouter(tags=["publish status"])


@router.get("/publish-status", response_model=PublishStatusReport)
def publish_status(package: str, branch: str, base: Optional[str] = None,
                   app=Depends(container)):
    """The branch's commit on the fork, any pull request opened from it, and
    its checks. Never writes; refuses branches this tool does not create."""
    known = app.catalog.package(package)
    if known is None or not known.arcos_repository:
        raise PublishStatusError(
            ErrorCode.NOT_FOUND, f"No ARCoS repository is known for {package!r}."
        )
    return app.publish_status.status(
        package=package, repository_url=known.arcos_repository,
        branch=branch, base=base,
    )
