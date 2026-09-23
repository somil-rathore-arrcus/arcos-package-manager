"""Preview, cherry-pick and pull requests.

Nothing in this module happens implicitly. Preview never writes, cherry-pick only
pushes when asked, and a pull request is a separate call again.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...domain.models import (
    CherryPickPreview, CherryPickResult, PatchSelection, PullRequest,
)
from ...services.github_service import (
    GitHubError, build_patch_pr_body, repo_slug,
)
from ...domain.enums import ErrorCode
from ..deps import container
from ..schemas import CherryPickRequest, PatchRequest, PullRequestRequest

router = APIRouter(tags=["patches"])


def _selection(request: PatchRequest) -> PatchSelection:
    return PatchSelection(
        package=request.package,
        debian_release=request.release,
        arcos_branch=request.arcos_branch,
        upstream_repository=request.upstream_repository,
        upstream_ref=request.upstream_ref,
        shas=request.shas,
    )


@router.post("/patches/preview", response_model=CherryPickPreview)
def preview(request: PatchRequest, app=Depends(container)):
    """Try the selection in a throwaway workspace. Writes nothing."""
    package = app.catalog.package(request.package)
    repository = package.arcos_repository if package else ""
    return app.patches.preview(
        _selection(request), repository, base_ref=request.arcos_branch
    )


@router.post("/patches/cherry-pick", response_model=CherryPickResult)
def cherry_pick(request: CherryPickRequest, app=Depends(container)):
    """Apply the selection to a NEW branch. The target branch is never written to."""
    package = app.catalog.package(request.package)
    repository = package.arcos_repository if package else ""
    return app.patches.cherry_pick(
        _selection(request), repository,
        branch_name=request.branch_name,
        base_ref=request.arcos_branch,
        push=request.push,
    )


@router.post("/pull-requests", response_model=PullRequest)
def create_pull_request(request: PullRequestRequest, app=Depends(container)):
    resolution = app.upstream.resolve(
        request.package, request.release, request.arcos_branch
    )
    slug = repo_slug(resolution.arcos_repository)
    if not slug:
        raise GitHubError(
            ErrorCode.INVALID_REQUEST,
            f"{resolution.arcos_repository} is not a GitHub repository, so a "
            f"pull request cannot be opened for it.",
        )

    from ...domain.models import CherryPickResult as Result
    from ...domain.enums import PreviewOutcome

    result = Result(
        outcome=PreviewOutcome.CLEAN, package=request.package,
        base_branch=request.arcos_branch, new_branch=request.head_branch,
        applied=request.applied, pushed=True,
    )
    title = request.title or (
        f"{request.package}: pull {len(request.applied)} upstream commit(s) "
        f"into {request.arcos_branch}"
    )
    body = request.body or build_patch_pr_body(resolution, result)
    return app.github.create_pull_request(
        slug, request.head_branch, request.arcos_branch, title, body,
        draft=request.draft,
    )


@router.get("/pull-requests/{number}", response_model=PullRequest)
def get_pull_request(number: int, package: str, release: str,
                     app=Depends(container)):
    resolution = app.upstream.resolve(package, release)
    slug = repo_slug(resolution.arcos_repository)
    if not slug:
        raise GitHubError(
            ErrorCode.INVALID_REQUEST, "Not a GitHub repository."
        )
    return app.github.get_pull_request(slug, number)
