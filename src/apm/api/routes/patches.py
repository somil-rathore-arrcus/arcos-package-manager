"""Preview and cherry-pick, and the link a pull request is opened from.

Nothing in this module happens implicitly. Preview never writes, cherry-pick only
pushes when asked, and a pull request is never opened here: a person opens it
from the pushed branch's link.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...domain.models import (
    CherryPickPreview, CherryPickResult, PatchSelection, PullRequest, Repository,
    UpstreamResolution,
)
from ...services.github_service import (
    GitHubError, build_patch_pr_body, patch_pr_title, pull_request_link,
    repo_slug,
)
from ...services.patch_service import PatchError
from ...domain.enums import ErrorCode, PreviewOutcome, ResolutionStatus
from ..deps import container, require_write_access
from ..schemas import CherryPickRequest, PatchRequest

router = APIRouter(tags=["patches"])


def _selection(request: PatchRequest) -> PatchSelection:
    return PatchSelection(
        package=request.package,
        debian_release=request.release,
        arcos_branch=request.arcos_branch,
        upstream_repository=request.upstream_repository,
        upstream_ref=request.upstream_ref,
        shas=request.shas,
        comparison_arcos_commit=request.comparison_arcos_commit,
        comparison_upstream_commit=request.comparison_upstream_commit,
        expected_base_sha=getattr(request, "expected_base_sha", None),
        approved_shas=request.approved_shas,
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
def cherry_pick(request: CherryPickRequest, http: Request,
                app=Depends(container)):
    """Apply the selection to a NEW branch. The target branch is never written to.

    Only after a preview: the request carries the preview's base_sha, and the
    service refuses if the target branch has moved since, or if any selected
    commit is no longer in the missing set.
    """
    if request.push:
        require_write_access(http, app)
    if not request.expected_base_sha:
        raise PatchError(
            ErrorCode.INVALID_REQUEST,
            "Run a preview first and pass its base_sha as expected_base_sha; a "
            "selection is never applied against a branch nobody previewed.",
        )
    package = app.catalog.package(request.package)
    repository = package.arcos_repository if package else ""
    result = app.patches.cherry_pick(
        _selection(request), repository,
        branch_name=request.branch_name,
        base_ref=request.arcos_branch,
        push=request.push,
    )
    if result.outcome is PreviewOutcome.CLEAN:
        _describe_pull_request(app, request, result, repository)
    return result


def _describe_pull_request(app, request: CherryPickRequest,
                           result: CherryPickResult, repository: str) -> None:
    """The title and description a pull request for this branch carries, and -
    once it is pushed - the link that opens one without a token.

    Built here, where the validation actually ran, so the description reports
    what happened. A resolution that cannot be read now must not turn a branch
    that was pushed into an error, so the request's own upstream stands in.
    """
    try:
        resolution = app.upstream.resolve(
            request.package, request.release, request.arcos_branch)
    except Exception:  # noqa: BLE001 - the push already happened
        # FAILED, not VERIFIED: the description must not claim a verification
        # this request could not repeat.
        resolution = UpstreamResolution(
            package=request.package, debian_release=request.release,
            status=ResolutionStatus.FAILED,
            upstream_repository=Repository(url=request.upstream_repository),
            upstream_ref=request.upstream_ref,
        )
    result.pr_title = patch_pr_title(
        request.package, result.base_branch, len(result.applied))
    result.pr_body = build_patch_pr_body(resolution, result)
    slug = repo_slug(repository)
    if result.pushed and slug:
        result.pr_create_url, _ = pull_request_link(
            slug, result.base_branch, result.new_branch, result.pr_title,
            result.pr_body,
        )


@router.post("/pull-requests")
def create_pull_request():
    """Refused, always: this application never opens a pull request. A pushed
    branch comes back with its link (pr_create_url), and a person opens the
    pull request from there."""
    raise GitHubError(
        ErrorCode.PULL_REQUESTS_DISABLED,
        "This application never opens a pull request. Open it on GitHub from "
        "the branch's link instead.",
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
