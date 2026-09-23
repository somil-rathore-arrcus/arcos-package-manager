"""Generating debian/upstream.md, and - only on request - proposing it."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from ...config import ROOT
from ...domain.enums import ErrorCode, PreviewOutcome, UpstreamMdOutcome
from ...domain.models import PullRequest, UpstreamMdDocument
from ...services.github_service import (
    GitHubError, build_upstream_md_pr_body, repo_slug,
)
from ...services.upstream_md_service import UPSTREAM_MD_PATH
from ..deps import container
from ..schemas import UpstreamMdPrRequest, UpstreamMdRequest

router = APIRouter(tags=["upstream.md"])


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
    document = app.upstream_md.generate(resolution, existing=_existing(app, resolution))

    if request.write_local:
        target = Path(ROOT) / "out" / "upstream-md" / \
            f"{request.package}__{request.release}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document.content, encoding="utf-8")
        document.local_path = str(target)
    return document


@router.post("/upstream-md/pr", response_model=PullRequest)
def open_pull_request(request: UpstreamMdPrRequest, app=Depends(container)):
    """Commit the file on a new branch and open a PR. Requires explicit confirmation."""
    if not request.confirm:
        raise GitHubError(
            ErrorCode.INVALID_REQUEST,
            "Opening a pull request needs confirm=true. Generating the file "
            "never proposes it on its own.",
        )

    resolution = app.upstream.resolve(
        request.package, request.release, request.arcos_branch
    )
    document = app.upstream_md.generate(resolution, existing=_existing(app, resolution))

    if document.outcome is UpstreamMdOutcome.NO_CHANGE:
        raise GitHubError(
            ErrorCode.INVALID_REQUEST,
            f"{UPSTREAM_MD_PATH} is already up to date for "
            f"{request.package}; there is nothing to propose.",
        )
    if document.outcome is UpstreamMdOutcome.CONFLICT:
        raise GitHubError(
            ErrorCode.CONFLICT,
            f"{UPSTREAM_MD_PATH} in {request.package} was not written by this "
            f"tool and may contain information it does not know. Review it "
            f"before replacing it.",
        )

    slug = repo_slug(resolution.arcos_repository)
    if not slug:
        raise GitHubError(
            ErrorCode.INVALID_REQUEST, "Not a GitHub repository."
        )

    branch = request.branch_name or f"docs/upstream-md/{request.release}"
    base = resolution.arcos_branch

    workspace = app.workspaces.scratch(f"md-pr-{request.package}")
    try:
        workspace.destroy()
        workspace.ensure()
        head = workspace.fetch_side("arcos", resolution.arcos_repository, base)
        workspace.checkout_new_branch(branch, head)
        workspace.write_file(UPSTREAM_MD_PATH, document.content)
        sha = workspace.commit_all(
            f"{request.package}: record verified upstream in {UPSTREAM_MD_PATH}"
        )
        if sha is None:
            raise GitHubError(
                ErrorCode.INVALID_REQUEST, "Nothing changed; no commit was made."
            )
        push = workspace.push(resolution.arcos_repository, branch)
        if not push.ok:
            raise GitHubError(
                ErrorCode.AUTH_REQUIRED
                if "permission" in push.stderr.lower() else ErrorCode.GIT_ERROR,
                f"Could not push {branch}.", push.stderr.strip()[:400],
            )
    finally:
        workspace.destroy()

    return app.github.create_pull_request(
        slug, branch, base,
        f"{request.package}: record verified upstream in debian/upstream.md",
        build_upstream_md_pr_body(resolution),
        draft=request.draft,
    )
