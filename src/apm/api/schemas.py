"""Request and response bodies.

Responses are the domain models themselves, so the API cannot drift from what the
services actually produce. Only the request shapes live here.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

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


class CherryPickRequest(PatchRequest):
    branch_name: Optional[str] = None
    push: bool = Field(
        False, description="Push the new branch. Never happens without this."
    )


class PullRequestRequest(BaseModel):
    package: str
    release: str
    arcos_branch: str
    head_branch: str
    title: Optional[str] = None
    body: Optional[str] = None
    draft: bool = False
    applied: List[str] = Field(default_factory=list)


class UpstreamMdRequest(BaseModel):
    package: str
    release: str
    arcos_branch: Optional[str] = None
    write_local: bool = Field(
        False, description="Also write the file under the local output directory."
    )


class UpstreamMdPrRequest(BaseModel):
    package: str
    release: str
    arcos_branch: Optional[str] = None
    branch_name: Optional[str] = Field(
        None,
        description="Must be under upstream-metadata/ and not the target branch.",
    )
    draft: bool = False
    dry_run: bool = Field(
        False,
        description=(
            "Build and check the exact commit, then stop: nothing is pushed and "
            "no pull request is opened."
        ),
    )
    confirm: bool = Field(
        False,
        description=(
            "Must be true. Opening a pull request is an explicit act, never a "
            "side effect of generating the file."
        ),
    )


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    capabilities: dict
