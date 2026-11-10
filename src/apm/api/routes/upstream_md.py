"""Generating debian/upstream.md, and - only on request - pushing it.

No pull request is opened here or anywhere else in this application: a push
returns the branch's Compare Link, and a person opens the pull request.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, Request

from ...config import ROOT
from ...domain.enums import ErrorCode, ResolutionStatus
from ...domain.models import PublishResult, UpstreamMdDocument
from ...services.publish_ledger import PublishLedger, all_records
from ...publish_upstream_md import refresh_records
from ...services.upstream_md_publisher import (
    NO_PULL_REQUESTS, PublishError, load_targets,
)
from ..deps import container, require_write_access
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
def generate(request: UpstreamMdRequest, http: Request, app=Depends(container)):
    """Render the file and report what writing it would do. Writes nothing remote."""
    if request.write_local:
        require_write_access(http, app)
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
        # A draft, never a reviewed file: the reviewed ones are only what
        # generate-upstream-md writes to out/upstream-md-<release>/.
        target = OUT_DIR / "upstream-md-drafts" / f"{request.package}__{request.release}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document.content, encoding="utf-8")
        document.local_path = str(target)
    return document


@router.post("/upstream-md/pr", response_model=PublishResult)
def push_branch(request: UpstreamMdPrRequest, http: Request,
                app=Depends(container)):
    """Push one package's reviewed debian/upstream.md to its branch: the same
    reviewed plan, publisher, branch names and records as
    `apm publish-upstream-md --apply`, one package at a time.

    The repository, the target branch and the bytes come from the reviewed plan
    (out/upstream-md-plan-<release>.json and its files), the branch name from
    settings.yaml and the records; the request names only the package and
    release. No pull request is opened: the result carries the Compare Link a
    person opens one from. Requires confirm=true unless it is a dry run."""
    if not request.push_only:
        raise PublishError(ErrorCode.PULL_REQUESTS_DISABLED, NO_PULL_REQUESTS)
    if not request.dry_run:
        require_write_access(http, app)
        if not request.confirm:
            raise PublishError(
                ErrorCode.INVALID_REQUEST,
                "Pushing needs confirm=true. Generating the file never "
                "pushes it on its own.",
            )
        app.publisher.preflight()

    targets, settled = reviewed_targets(app, request.release)
    target = next((t for t in targets if t.package == request.package), None)
    if target is None:
        refused = next((s for s in settled if s.package == request.package), None)
        if refused is None:
            raise PublishError(
                ErrorCode.INVALID_REQUEST,
                f"{request.package} is not in the reviewed {request.release} "
                f"plan, so it cannot be published. Generate and review it "
                f"first: apm generate-upstream-md --release {request.release}.",
            )
        # Not eligible as reviewed (excluded, not verified, or the file is no
        # longer what was reviewed): said, never pushed, nothing recorded.
        return refused

    ledger = PublishLedger.for_release(OUT_DIR, request.release)
    result = app.publisher.publish(
        target, apply=not request.dry_run, prior=ledger.get(request.package),
        others=all_records(OUT_DIR),
    )
    if not request.dry_run:
        ledger.record(result)
        refresh_records(OUT_DIR, request.release, ledger, targets)
    return result


def reviewed_targets(app, release: str):
    """The release's reviewed plan as the CLI reads it: eligible targets, and
    the packages it settles without publishing."""
    plan = OUT_DIR / f"upstream-md-plan-{release}.json"
    if not plan.exists():
        raise PublishError(
            ErrorCode.NOT_FOUND,
            f"There is no reviewed plan for {release} ({plan.name}). Generate "
            f"and review it first: apm generate-upstream-md --release {release}.",
        )
    return load_targets(plan, app.mapping, app.upstream_md, app.publisher.exclude)


@router.get("/upstream-md/prs", response_model=List[PublishResult])
def list_pull_requests(release: str = "bookworm"):
    """What proposing debian/upstream.md has done so far. Reads the ledger only."""
    return PublishLedger.for_release(OUT_DIR, release).entries()
