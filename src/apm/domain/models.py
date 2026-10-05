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
    Presence,
    PreviewOutcome,
    PublishStatus,
    RefStrategy,
    ResolutionMethod,
    ResolutionMode,
    ResolutionStatus,
    ReviewReason,
    UpstreamMdOutcome,
    VerificationLevel,
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
    """A repository and ref considered, and what became of it.

    Every ref the ancestry probe measured is kept, not just the winner, so a
    reviewer can see that `main` was 910 commits away and the release tag 0.
    """

    repository: str
    ref: Optional[str] = None
    source: ResolutionMethod
    accepted: bool = False
    shares_history: Optional[bool] = None
    rejected_reason: Optional[str] = None
    strategy: Optional[RefStrategy] = None
    kind: Optional[str] = Field(None, description="branch | tag")
    sha: Optional[str] = None
    behind: Optional[int] = Field(
        None, description="git rev-list --count --no-merges ARCOS..CANDIDATE"
    )
    arcos_only: Optional[int] = Field(
        None, description="git rev-list --count --no-merges CANDIDATE..ARCOS"
    )
    in_arcos: Optional[bool] = Field(
        None, description="The candidate commit is an ancestor of the ARCoS commit."
    )
    error: Optional[str] = None


class RefSelection(BaseModel):
    """Why the upstream ref is the one it is. Recorded as evidence, always."""

    ref: str
    kind: str = Field(description="branch | tag")
    strategy: RefStrategy
    sha: Optional[str] = None
    reason: str = ""
    debian_upstream_version: Optional[str] = Field(
        None, description="The Debian version with epoch, revision and repack "
                          "suffixes removed."
    )
    series: Optional[str] = Field(None, description="major.minor, e.g. 6.1")
    base_tag: Optional[str] = Field(
        None, description="The upstream tag for the shipped Debian version."
    )
    base_sha: Optional[str] = None
    arcos_contains_base: Optional[bool] = None
    is_fallback: bool = False


class CuratedUpstream(BaseModel):
    """What a person wrote in config/overrides.yaml, kept visible whatever the
    history turned out to say."""

    repository: Optional[str] = None
    ref: Optional[str] = None
    reason: str = ""
    conflict: Optional[str] = Field(
        None, description="Set when another repository shares history and the "
                          "curated one does not."
    )
    conflicting_repository: Optional[str] = None
    conflicting_ref: Optional[str] = None
    replacement_allowed: bool = False


class ContentMatchCandidate(BaseModel):
    tag: str
    sha: Optional[str] = None
    method: str = "tree-compare"
    arcos_tree: Optional[str] = Field(
        None, description="The ARCoS commit whose tree was compared."
    )
    files_compared: int = 0
    files_differing: int = 0
    lines_added: Optional[int] = None
    lines_removed: Optional[int] = None
    score: float = Field(0.0, description="1.0 is an identical tree.")


class ContentMatch(BaseModel):
    """The closest upstream release to an ARCoS tree that shares no history.

    A finding, not a verification: it takes a person's approval before any
    comparison is built on it, and anything computed from it says it was
    synthesized.
    """

    method: str = "tree-compare"
    arcos_tree: Optional[str] = None
    arcos_tree_label: str = ""
    base_tag: Optional[str] = None
    base_sha: Optional[str] = None
    score: Optional[float] = None
    files_compared: Optional[int] = None
    files_differing: Optional[int] = None
    lines_differing: Optional[int] = None
    candidates: List[ContentMatchCandidate] = Field(default_factory=list)
    approved: bool = False
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    approval_source: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class DebianPatch(BaseModel):
    """One quilt patch Debian applies to the shipped version."""

    name: str
    subject: str = ""
    origin: Optional[str] = None
    upstream_commit: Optional[str] = Field(
        None, description="From DEP-3 Origin:, when it names an upstream commit."
    )
    cve_ids: List[str] = Field(default_factory=list)
    bugs: List[str] = Field(default_factory=list)
    forwarded: Optional[str] = None
    category: str = Field(
        "unknown", description="security | upstream_backport | debian_specific | unknown"
    )
    presence: Optional[Presence] = Field(
        None, description="Whether the ARCoS tree already carries it; set by a comparison."
    )
    presence_evidence: List[str] = Field(default_factory=list)


class DebianUpload(BaseModel):
    version: str
    distribution: str = ""
    urgency: str = ""
    date: Optional[str] = None
    cve_ids: List[str] = Field(default_factory=list)
    security: bool = False


class DebianPatchReport(BaseModel):
    """What Debian changed on top of the upstream release it ships.

    Supporting evidence only: a Debian patch is packaging, never an upstream
    source commit, and is never counted as one.
    """

    source_package: str
    version: str
    release: str
    available: bool = True
    reason: Optional[str] = None
    format: Optional[str] = None
    uploads: List[DebianUpload] = Field(
        default_factory=list,
        description="Uploads of this upstream version, newest first.",
    )
    cve_ids: List[str] = Field(default_factory=list)
    patches: List[DebianPatch] = Field(default_factory=list)

    @property
    def security_patches(self) -> List[DebianPatch]:
        return [p for p in self.patches if p.category == "security"]


class SecurityEvidence(BaseModel):
    """One statement, from one source, that a change is security-relevant."""

    source: str = Field(
        description="commit-message | debian-patch | debian-changelog | osv"
    )
    identifier: str
    detail: str = ""
    url: Optional[str] = None
    commit: Optional[str] = None


class SecurityFinding(BaseModel):
    """A vulnerability known to external sources, and where ARCoS stands on it."""

    identifier: str
    aliases: List[str] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list)
    summary: str = ""
    fix_commits: List[str] = Field(default_factory=list)
    status: str = Field(
        "unknown",
        description="missing | present | not_in_range | unknown - of the fix "
                    "commits, relative to ARCoS",
    )
    url: Optional[str] = None


class ComparisonSnapshot(BaseModel):
    """The headline numbers of one comparison, pinned to the commits it used.

    Attached to a resolution only while both commits still match, so a number
    computed against yesterday's upstream never reaches today's report.
    """

    arcos_commit: str
    upstream_repository: str
    upstream_ref: str
    upstream_commit: str
    computed_at: Optional[datetime] = None
    base_tag: Optional[str] = None
    series: Optional[str] = None
    synthesized_ancestry: bool = False
    relevant_upstream: int = 0
    definitely_present: int = 0
    probably_present: int = 0
    missing: int = 0
    unknown_presence: int = 0
    reverted_upstream: int = 0
    critical_missing: int = 0
    stable_missing: int = 0
    arcos_only: int = 0
    backport_detection: str = ""
    truncated: bool = False


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
    merge_bases: List[str] = Field(
        default_factory=list, description="git merge-base --all"
    )
    # Raw commit-graph counts, head-based: ARCOS..UPSTREAM and UPSTREAM..ARCOS.
    # Not a backlog - backports are not excluded - and labelled as such
    # wherever they are shown.
    behind: Optional[int] = None
    arcos_only: Optional[int] = None
    counts_basis: str = ""
    upstream_commit_date: Optional[str] = None

    verification_level: VerificationLevel = VerificationLevel.NONE
    review_reasons: List[ReviewReason] = Field(default_factory=list)
    warnings: List[str] = Field(
        default_factory=list,
        description="Plausibility checks that did not block the answer but "
                    "deserve a look.",
    )
    ref_selection: Optional[RefSelection] = None
    curated: Optional[CuratedUpstream] = None
    content_match: Optional[ContentMatch] = None
    debian_patches: Optional[DebianPatchReport] = None
    comparison: Optional[ComparisonSnapshot] = Field(
        None, description="From a comparison run against exactly these commits."
    )

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
            and self.verification_level.proves_relationship
            and self.upstream_repository
            and self.upstream_ref
        )

    @property
    def synthesized_ancestry(self) -> bool:
        return self.verification_level is VerificationLevel.CONTENT_MATCH_APPROVED


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
    sources: List[str] = Field(
        default_factory=list,
        description="Which kinds of evidence contributed: commit-message, "
                    "debian-patch, osv.",
    )
    external: List[SecurityEvidence] = Field(default_factory=list)


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
    presence: Optional[Presence] = Field(
        None, description="Upstream commits only: is the change already in ARCoS?"
    )
    presence_evidence: List[str] = Field(default_factory=list)
    in_base_release: Optional[bool] = Field(
        None, description="Reachable from the upstream tag for the shipped "
                          "Debian version, i.e. part of the release itself."
    )
    reverts: Optional[str] = None
    reverted_by: Optional[str] = None
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

    # What the numbers are measured against, so none of them is a bare count.
    merge_bases: List[str] = Field(default_factory=list)
    base_tag: Optional[str] = None
    series: Optional[str] = None
    upstream_commit_date: Optional[str] = None
    counts_basis: str = ""
    packaging_commits_included: bool = False
    synthesized_ancestry: bool = False
    synthetic_base: Optional[str] = None

    # The backlog, by presence. relevant_upstream is every upstream commit in
    # the selected release/series that the ARCoS commit cannot reach; the rest
    # partition it.
    relevant_upstream: int = 0
    definitely_present: int = 0
    probably_present: int = 0
    missing: int = 0
    unknown_presence: int = 0
    reverted_upstream: int = 0
    missing_in_base_release: Optional[int] = Field(
        None, description="Missing commits that are part of the shipped release tag."
    )
    backport_detection: str = ""


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

    debian_patches: Optional[DebianPatchReport] = None
    security: List[SecurityFinding] = Field(default_factory=list)
    security_sources: List[str] = Field(
        default_factory=list,
        description="External sources consulted, and whether each answered.",
    )

    computed_at: Optional[datetime] = None
    warnings: List[str] = Field(default_factory=list)

    def snapshot(self) -> "ComparisonSnapshot":
        summary = self.summary
        return ComparisonSnapshot(
            arcos_commit=self.arcos_commit or "",
            upstream_repository=self.upstream_repository,
            upstream_ref=self.upstream_ref,
            upstream_commit=self.upstream_commit or "",
            computed_at=self.computed_at,
            base_tag=summary.base_tag,
            series=summary.series,
            synthesized_ancestry=summary.synthesized_ancestry,
            relevant_upstream=summary.relevant_upstream,
            definitely_present=summary.definitely_present,
            probably_present=summary.probably_present,
            missing=summary.missing,
            unknown_presence=summary.unknown_presence,
            reverted_upstream=summary.reverted_upstream,
            critical_missing=summary.critical,
            stable_missing=summary.stable_relevant,
            arcos_only=summary.arcos_only,
            backport_detection=summary.backport_detection,
            truncated=summary.truncated,
        )


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
    comparison_arcos_commit: Optional[str] = Field(
        None, description="The ARCoS commit the comparison was computed against."
    )
    comparison_upstream_commit: Optional[str] = None
    expected_base_sha: Optional[str] = Field(
        None, description="The target branch tip the preview ran on. A "
                          "cherry-pick is refused if the branch has moved since."
    )
    approved_shas: List[str] = Field(
        default_factory=list,
        description="Commits deliberately selected from outside the current "
                    "missing set. Still must be on the upstream ref.",
    )


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
    base_sha: Optional[str] = Field(
        None, description="The target branch tip this preview applied onto. "
                          "Pass it to cherry-pick as expected_base_sha."
    )
    upstream_sha: Optional[str] = None
    base_moved: bool = Field(
        False, description="The target branch is not the commit the "
                           "comparison was computed against."
    )
    validated: bool = Field(
        False, description="Every selected commit was re-checked against the "
                           "current target tip and upstream ref."
    )


class CherryPickResult(BaseModel):
    outcome: PreviewOutcome
    package: str
    base_branch: str
    new_branch: str
    applied: List[str] = Field(default_factory=list)
    base_sha: Optional[str] = None
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


class PublishResult(BaseModel):
    """One package's debian/upstream.md proposal: what was done and where it is.

    The same record is returned by the API, printed by the CLI and kept in the
    results ledger, so the three cannot describe a run differently.
    """

    package: str
    release: str
    status: PublishStatus
    repository: str = Field("", description="owner/name on GitHub")
    base_branch: str = ""
    branch: str = ""
    pinned_commit: Optional[str] = Field(
        None, description="The commit the release manifest pins."
    )
    base_sha: Optional[str] = Field(
        None, description="The target branch tip the commit was built on."
    )
    base_moved: bool = Field(
        False, description="The target branch has moved past the pinned commit."
    )
    outcome: Optional[UpstreamMdOutcome] = None
    commit: Optional[str] = None
    pushed: bool = False
    title: str = ""
    pull_request: Optional[PullRequest] = None
    error: Optional[str] = None
    diff: Optional[str] = None
    updated_at: Optional[datetime] = None
