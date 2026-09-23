"""Data model shared by discovery, resolution and reporting.

The row produced by `resolve` is the single unit everything else consumes: the
report writes it, and the later comparison/publish phases will attach to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Category(str, Enum):
    """What kind of thing a package is.

    Orthogonal to Status. A package with no upstream is not a failure - it is a
    package that legitimately has no upstream, and this says which.
    """

    DEBIAN_UPSTREAM = "debian_upstream"
    DEBIAN_NO_UPSTREAM = "debian_no_upstream"
    # Not a Debian package, but a real external open-source project ARCoS forked
    # (mstpd, rtrlib, hsflowd, zenoh, the ONL trees). These need a curated
    # mapping: Debian carries no metadata for them at all.
    THIRD_PARTY = "third_party"
    ARRCUS_NATIVE = "arrcus_native"
    VENDOR = "vendor"


class Status(str, Enum):
    """Whether the resolved upstream was confirmed to exist."""

    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNRESOLVED = "UNRESOLVED"
    NO_UPSTREAM = "NO_UPSTREAM"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class Method(str, Enum):
    """How the upstream was arrived at. Ordered by trustworthiness."""

    CURATED = "curated"
    # The origin was proven by shared git history rather than metadata.
    ANCESTRY = "ancestry"
    KERNEL_SERIES = "kernel_series"
    DEP12 = "dep12"
    WATCH_FORGE = "watch_forge"
    HOMEPAGE_FORGE = "homepage_forge"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class Package:
    """One ARCoS package, as discovered from the arrcus_rel manifest."""

    name: str
    arcos_repository: str
    github_repository: str
    submodule_path: str
    branches: dict[str, str] = field(default_factory=dict)
    # Releases whose manifest actually contains this package. Eight packages ship
    # in bookworm but not trixie.
    releases: list[str] = field(default_factory=list)
    # Commit each release's manifest pins for this package. Authoritative: the
    # branch field in .gitmodules can be stale.
    commits: dict[str, str] = field(default_factory=dict)
    debian_source_package: Optional[str] = None

    def branch_for(self, release: str) -> str:
        return self.branches.get(release) or self.branches.get("default") or "aminor"


@dataclass
class DebianSource:
    """A stanza from the Debian `Sources` index."""

    package: str
    version: str
    directory: str
    suite: str
    # The archive this stanza came from. A security-suite package has a
    # pool/updates/... Directory that exists only on the security mirror, so the
    # mirror must travel with the stanza or its tarball cannot be found.
    mirror: str = ""
    vcs_git: Optional[str] = None
    # Debian records the packaging branch inline: "<url> -b debian/master". It
    # is the branch of the PACKAGING repository, never of the upstream project,
    # and it is reported in its own column so the two cannot be confused.
    vcs_branch: Optional[str] = None
    vcs_browser: Optional[str] = None
    homepage: Optional[str] = None

    @property
    def upstream_version(self) -> str:
        """Strip the epoch and the Debian revision: 1:6.1-1 -> 6.1."""
        v = self.version.split(":", 1)[-1]
        return v.rsplit("-", 1)[0] if "-" in v else v


@dataclass
class Upstream:
    """A candidate upstream repository and the ref to track."""

    repository: str
    ref: Optional[str] = None
    is_tag: bool = False


@dataclass
class Resolution:
    """The outcome of resolving one package for one Debian release."""

    package: str
    release: str
    category: Category
    status: Status
    method: Method
    confidence: Confidence
    arcos_repository: str = ""
    github_repository: str = ""
    arcos_branch: str = ""
    arcos_commit: Optional[str] = None
    # Where the package sits in the release manifest (aminor/packages/<name> for
    # most, top level for linux and the ONL trees) and which ARCoS release that
    # manifest is. Both come from discovery; neither is assumed.
    arcos_path: str = ""
    arcos_release: str = ""
    # auto | manual. Manual resolutions come from a user-supplied upstream and
    # are verified the same way, but they are never mixed with automatic ones.
    mode: str = "auto"
    # What was read to reach this answer, and where it can be read again.
    evidence_source: str = ""
    evidence_url: str = ""
    # How the answer was checked: ls-remote alone, or shared history as well.
    verification: str = ""
    debian: Optional[DebianSource] = None
    upstream: Optional[Upstream] = None
    resolved_sha: Optional[str] = None
    # Filled in when ancestry was proven: how far the fork trails its origin.
    merge_base: Optional[str] = None
    # Whether the upstream is the project's own repository or Debian's packaging
    # repository. Pulling from one brings upstream code, from the other it brings
    # Debian packaging changes - the reviewer needs to know which.
    origin_kind: Optional[str] = None
    behind: Optional[int] = None
    arcos_only: Optional[int] = None
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def record(self, text: str) -> None:
        self.evidence.append(text)

    @property
    def reason(self) -> str:
        """Why this row is not a finished VERIFIED mapping.

        Empty for a verified row: there is nothing to explain. Every other
        status has to say something, and the report checks that it does.
        """
        if self.status is Status.VERIFIED:
            return ""
        if self.notes:
            return self.notes[0]
        # Fall back to the last thing that was tried, which is what failed.
        return self.evidence[-1] if self.evidence else ""

    @property
    def upstream_branch(self) -> str:
        if not self.upstream or not self.upstream.ref or self.upstream.is_tag:
            return ""
        return self.upstream.ref

    @property
    def upstream_tag(self) -> str:
        if not self.upstream or not self.upstream.ref or not self.upstream.is_tag:
            return ""
        return self.upstream.ref
