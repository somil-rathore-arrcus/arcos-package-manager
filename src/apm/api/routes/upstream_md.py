"""Generating debian/upstream.md, and - only on request - proposing it."""

from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends

from ...config import ROOT
from ...domain.enums import ErrorCode, ResolutionStatus
from ...domain.models import PublishResult, UpstreamMdDocument
from ...services.publish_ledger import PublishLedger
from ...services.upstream_md_publisher import (
    PublishError, target_from_resolution,
)
from ..deps import container
from ..schemas import UpstreamMdPrRequest, UpstreamMdRequest

router = APIRouter(tags=["upstream.md"])

OUT_DIR = Path(ROOT) / "out"


def _existing(app, resolution):
    """The file already in the fork, so generation compares rather than clobbers.

    Shared with the batch generator: one way of reading it, so the single-package
    path and the whole-release path cannot reach different conclusions about
    whether a file is there.
    """
    content, _unknown = app.metadata_batch.existing_content(resolution)
    return content


@router.post("/upstream-md/generate", response_model=UpstreamMdDocument)
def generate(request: UpstreamMdRequest, app=Depends(container)):
    """Render the file and report what writing it would do. Writes nothing remote."""
    resolution = app.upstream.resolve(
        request.package, request.release, request.arcos_branch
    )
    if resolution.status is ResolutionStatus.NO_UPSTREAM:
        raise PublishError(
            ErrorCode.INVALID_REQUEST,
            f"{request.package} has no upstream, so there is nothing to record.",
        )
    document = app.upstream_md.generate(resolution, existing=_existing(app, resolution))

    if request.write_local:
        target = OUT_DIR / "upstream-md" / f"{request.package}__{request.release}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document.content, encoding="utf-8")
        document.local_path = str(target)
    return document


@router.post("/upstream-md/pr", response_model=PublishResult)
def open_pull_request(request: UpstreamMdPrRequest, app=Depends(container)):
    """Commit the file on a new branch and open a PR, through the same publisher
    the CLI uses. Requires explicit confirmation unless it is a dry run."""
    if not request.confirm and not request.dry_run:
        raise PublishError(
            ErrorCode.INVALID_REQUEST,
            "Opening a pull request needs confirm=true. Generating the file "
            "never proposes it on its own.",
        )

    resolution = app.upstream.resolve(
        request.package, request.release, request.arcos_branch
    )
    if resolution.status is not ResolutionStatus.VERIFIED:
        raise PublishError(
            ErrorCode.INVALID_REQUEST,
            f"{request.package} is {resolution.status.value}; only a VERIFIED "
            f"upstream is proposed.",
        )

    target = target_from_resolution(
        resolution, app.upstream_md.render(resolution)
    )
    if not request.dry_run:
        # The same pre-flight as the CLI: a token that cannot open the PR is
        # found out before anything is pushed.
        app.publisher.preflight([target.slug])
    ledger = PublishLedger.for_release(OUT_DIR, request.release)
    result = app.publisher.publish(
        target, apply=not request.dry_run, draft=request.draft,
        branch=request.branch_name or None, prior=ledger.get(request.package),
    )
    if not request.dry_run:
        ledger.record(result)
    return result


@router.get("/upstream-md/prs", response_model=List[PublishResult])
def list_pull_requests(release: str = "bookworm"):
    """What proposing debian/upstream.md has done so far. Reads the ledger only."""
    return PublishLedger.for_release(OUT_DIR, release).entries()
