"""Request and response bodies.

Responses are the domain models themselves, so the API cannot drift from what the
services actually produce. Only the request shapes live here.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import ErrorCode


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    detail: Optional[str] = None


class ResolveRequest(BaseModel):
    package: str
    release: str
    arcos_branch: Optional[str] = None
    refresh: bool = False


class ManualUpstreamRequest(BaseModel):
    package: str
    release: str
    repository: str = Field(description="Upstream git repository URL")
    ref: str = Field(description="A branch or tag in that repository")
    arcos_branch: Optional[str] = None


class ComparisonRequest(BaseModel):
    package: str
    release: str
    arcos_branch: Optional[str] = None
    upstream_repository: Optional[str] = Field(
        None, description="Set together with upstream_ref to compare a manual upstream."
    )
    upstream_ref: Optional[str] = None
    refresh: bool = False


class PatchRequest(BaseModel):
    package: str
    release: str
    arcos_branch: str
    upstream_repository: str
    upstream_ref: str
    shas: List[str] = Field(default_factory=list)
    comparison_arcos_commit: Optional[str] = Field(
        None, description="The ARCoS commit the comparison was computed against."
    )
    comparison_upstream_commit: Optional[str] = None
    approved_shas: List[str] = Field(
        default_factory=list,
        description="Commits deliberately selected from outside the current "
                    "missing set.",
    )


class CherryPickRequest(PatchRequest):
    branch_name: Optional[str] = None
    push: bool = Field(
        False, description="Push the new branch. Never happens without this."
    )
    expected_base_sha: Optional[str] = Field(
        None,
        description="base_sha from the preview. Required: the cherry-pick is "
                    "refused if the target branch has moved since.",
    )


class ContentBaseApprovalRequest(BaseModel):
    package: str
    release: str
    tag: Optional[str] = Field(None, description="Default: the best content match.")
    verified_by: str = Field(description="The person accountable for the decision.")
    note: Optional[str] = None
    confirm: bool = Field(False, description="Must be true.")


class UpstreamMdRequest(BaseModel):
    package: str
    release: str
    arcos_branch: Optional[str] = None
    write_local: bool = Field(
        False, description="Also write the file under the local output directory."
    )


class UpstreamMdPrRequest(BaseModel):
    """Push one package's reviewed debian/upstream.md to its branch.

    Only the package and release are the caller's: the repository, the target
    branch and the content come from the reviewed plan, and the branch name
    from settings.yaml and the publishing records. A request naming a branch
    or a target is refused (422), never followed.
    """

    model_config = ConfigDict(extra="forbid")

    package: str
    release: str
    dry_run: bool = Field(
        False,
        description=(
            "Build and check the exact commit, then stop: nothing is pushed."
        ),
    )
    confirm: bool = Field(
        False,
        description=(
            "Must be true unless dry_run. Pushing is an explicit act, never a "
            "side effect of generating the file."
        ),
    )
    push_only: bool = Field(
        True,
        description=(
            "Always true: the branch is pushed and the result carries its "
            "Compare Link (compare_url) and pr_create_url; a person opens the "
            "pull request. false is refused - this application never opens one."
        ),
    )


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    capabilities: dict
