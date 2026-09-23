"""Domain entities.

Pydantic models, so FastAPI serialises them directly and the frontend's types are
generated from the same schema. Business rules that belong to an entity live on
the entity, not in a route handler or a React component.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .enums import (
    CommitClass,
    Criticality,
    PackageCategory,
    PreviewOutcome,
    ResolutionMethod,
    ResolutionMode,
    ResolutionStatus,
    UpstreamMdOutcome,
)


class DebianRelease(BaseModel):
    id: str
    name: str
    suite: str
    version: str


class Repository(BaseModel):
    """A git repository, however it is spelled."""

    url: str
    host: Optional[str] = None
    owner: Optional[str] = None
    name: Optional[str] = None
    web_url: Optional[str] = None
    is_packaging: bool = Field(
        False,
        description=(
            "True when this is a Debian packaging repository rather than the "
            "project's own. Pulling from one brings packaging changes, from the "
            "other it brings upstream code."
        ),
    )


class Branch(BaseModel):
    name: str
    sha: Optional[str] = None
    is_default: bool = False
    is_configured: bool = Field(
        False, description="Named by the release manifest for this package."
    )
    web_url: Optional[str] = None


class Package(BaseModel):
    """An ARCoS package, as the release manifest defines it."""

    name: str
    arcos_repository: str
    github_repository: str
    submodule_path: str = ""
    releases: List[str] = Field(default_factory=list)
    branches: Dict[str, str] = Field(default_factory=dict)
    pinned_commits: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Commit each release's manifest pins. Authoritative: the branch "
            "field in .gitmodules can be stale."
        ),
    )
    category: Optional[PackageCategory] = None

    def ships_in(self, release: str) -> bool:
        return release in self.releases

    def branch_for(self, release: str) -> Optional[str]:
        return self.branches.get(release)


class PackageSource(BaseModel):
    """What Debian knows about the package for one release."""

    source_package: str
    debian_release: str
    version: str
    suite: str
    directory: str = ""
    vcs_git: Optional[str] = None
    vcs_branch: Optional[str] = Field(
        None,
        description=(
            "The branch Vcs-Git names inline ('<url> -b debian/master'). It is "
            "a branch of the PACKAGING repository, never of the upstream."
        ),
    )
    vcs_browser: Optional[str] = None
    homepage: Optional[str] = None
    dep12_repository: Optional[str] = None
    watch_urls: List[str] = Field(default_factory=list)
    web_url: Optional[str] = None


class ResolutionEvidence(BaseModel):
    """One recorded reason, so a mapping can be argued with rather than trusted."""

    kind: str = Field(description="dep12 | watch | homepage | git | ancestry | curated")
    detail: str
    url: Optional[str] = None


class UpstreamCandidate(BaseModel):
    """A repository considered, and what became of it."""

    repository: str
    ref: Optional[str] = None
    source: ResolutionMethod
    accepted: bool = False
    shares_history: Optional[bool] = None
    rejected_reason: Optional[str] = None


class UpstreamResolution(BaseModel):
    """Everything known about where one package for one release comes from."""

    package: str
    debian_release: str
    status: ResolutionStatus
    mode: ResolutionMode = ResolutionMode.AUTO
    method: ResolutionMethod = ResolutionMethod.UNRESOLVED
    category: Optional[PackageCategory] = None
    confidence: str = "none"

    arcos_repository: str = ""
    github_repository: str = ""
    arcos_branch: str = ""
    arcos_commit: Optional[str] = None
    arcos_path: str = Field(
        "", description="Where the package sits in the release manifest."
    )
    arcos_release: str = Field(
        "", description="The manifest branch this package list came from."
    )

    debian: Optional[PackageSource] = None
    upstream_repository: Optional[Repository] = None
    upstream_ref: Optional[str] = None
    # The same ref, split by what it is. A tag is a fixed point and a branch
    # keeps moving, so a report that blurs them answers the wrong question.
    upstream_branch: Optional[str] = None
    upstream_tag: Optional[str] = None
    upstream_commit: Optional[str] = None
    origin_kind: Optional[str] = Field(
        None, description="project | debian_packaging"
    )

    merge_base: Optional[str] = None
    behind: Optional[int] = None
    arcos_only: Optional[int] = None

    reason: Optional[str] = None
    evidence_source: str = Field(
        "", description="What was read to reach this answer."
    )
    evidence_url: str = Field("", description="Where that can be read again.")
    verification: str = Field(
        "", description="How the answer was checked, in words."
    )
    evidence: List[ResolutionEvidence] = Field(default_factory=list)
    candidates: List[UpstreamCandidate] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    resolved_at: Optional[datetime] = None

    @property
    def comparable(self) -> bool:
        """Whether a git comparison against this resolution is meaningful."""
        return bool(
            self.status.is_actionable
            and self.upstream_repository
            and self.upstream_ref
        )


class ReportFile(BaseModel):
    """A generated report on disk, offered for download."""

    name: str
    size_bytes: int
    modified_at: float
    release: str = ""
    download_url: str


class CriticalityAssessment(BaseModel):
    """Why a commit was called critical. No evidence means UNKNOWN, not NORMAL."""

    level: Criticality = Criticality.UNKNOWN
    evidence: List[str] = Field(default_factory=list)
    cve_ids: List[str] = Field(default_factory=list)
    fixes: List[str] = Field(default_factory=list)
    cc_stable: bool = False


class PatchInfo(BaseModel):
    """Patch identity, which is what tells a backport from a missing commit."""

    patch_id: Optional[str] = None
    equivalent_sha: Optional[str] = Field(
        None, description="The ARCoS commit carrying the same change."
    )
    equivalent_subject: Optional[str] = None


class CommitInfo(BaseModel):
    sha: str
    short_sha: str
    subject: str
    author_name: str = ""
    author_email: str = ""
    authored_at: Optional[datetime] = None
    body: str = ""
    classification: CommitClass = CommitClass.MISSING_UPSTREAM
    criticality: CriticalityAssessment = Field(
        default_factory=CriticalityAssessment
    )
    patch: PatchInfo = Field(default_factory=PatchInfo)
    web_url: Optional[str] = None


class ComparisonSummary(BaseModel):
    merge_base: Optional[str] = None
    has_common_ancestor: bool = False
    missing_upstream: int = 0
    arcos_only: int = 0
    already_backported: int = 0
    critical: int = 0
    stable_relevant: int = 0
    # Commits carrying a Fixes: trailer. Counted separately so the four levels
    # add up to missing_upstream - leaving NORMAL out of the summary hid 16 of
    # iputils' 142 missing commits from every tile and every filter.
    normal: int = 0
    unknown_criticality: int = 0
    truncated: bool = False


class ComparisonResult(BaseModel):
    package: str
    debian_release: str
    arcos_repository: str
    arcos_branch: str
    arcos_commit: Optional[str] = None
    upstream_repository: str
    upstream_ref: str
    upstream_commit: Optional[str] = None

    summary: ComparisonSummary = Field(default_factory=ComparisonSummary)
    missing_upstream: List[CommitInfo] = Field(default_factory=list)
    arcos_only: List[CommitInfo] = Field(default_factory=list)
    already_backported: List[CommitInfo] = Field(default_factory=list)

    computed_at: Optional[datetime] = None
    warnings: List[str] = Field(default_factory=list)


class PatchSelection(BaseModel):
    """Commits a user chose, in the order they must be applied.

    Cherry-pick order is oldest first: applying a later commit before the one it
    builds on conflicts for no reason.
    """

    package: str
    debian_release: str
    arcos_branch: str
    upstream_repository: str
    upstream_ref: str
    shas: List[str] = Field(default_factory=list)


class FileChange(BaseModel):
    path: str
    status: str = ""
    insertions: int = 0
    deletions: int = 0


class CherryPickPreview(BaseModel):
    """What applying the selection would do, worked out without touching it."""

    outcome: PreviewOutcome
    package: str
    arcos_branch: str
    applied: List[str] = Field(default_factory=list)
    failed_sha: Optional[str] = None
    conflicts: List[str] = Field(default_factory=list)
    files_changed: List[FileChange] = Field(default_factory=list)
    order: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    message: Optional[str] = None
    workspace_removed: bool = True


class CherryPickResult(BaseModel):
    outcome: PreviewOutcome
    package: str
    base_branch: str
    new_branch: str
    applied: List[str] = Field(default_factory=list)
    head_sha: Optional[str] = None
    pushed: bool = False
    conflicts: List[str] = Field(default_factory=list)
    failed_sha: Optional[str] = None
    message: Optional[str] = None


class PullRequest(BaseModel):
    number: Optional[int] = None
    url: Optional[str] = None
    state: Optional[str] = None
    title: str = ""
    body: str = ""
    base: str = ""
    head: str = ""
    draft: bool = False
    already_existed: bool = False


class UpstreamMdDocument(BaseModel):
    """debian/upstream.md, generated from a verified resolution."""

    package: str
    debian_release: str
    path: str = "debian/upstream.md"
    content: str = ""
    outcome: UpstreamMdOutcome = UpstreamMdOutcome.CREATED
    existing_content: Optional[str] = None
    diff: Optional[str] = None
    local_path: Optional[str] = None
