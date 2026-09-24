"""The vocabulary of the system.

Every state a package, commit or operation can be in is named here once.
"""

from __future__ import annotations

from enum import Enum


class ResolutionStatus(str, Enum):
    """How completely a package's upstream is known.

    VERIFIED is the requirement's RESOLVED: the name is kept because it says
    something stronger and true - the upstream was not merely selected, it was
    contacted, its ref resolved to a commit, and where possible its shared
    history with the fork was proven.
    """

    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_UPSTREAM = "NO_UPSTREAM"
    FAILED = "FAILED"

    @property
    def is_actionable(self) -> bool:
        """Whether a comparison against this resolution would mean anything."""
        return self in (ResolutionStatus.VERIFIED, ResolutionStatus.PARTIAL)


class ResolutionMode(str, Enum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class ResolutionMethod(str, Enum):
    """How the upstream was arrived at, in descending order of authority."""

    ANCESTRY = "ancestry"
    CURATED = "curated"
    KERNEL_SERIES = "kernel_series"
    DEP12 = "dep12"
    WATCH_FORGE = "watch_forge"
    HOMEPAGE_FORGE = "homepage_forge"
    MANUAL = "manual"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class PackageCategory(str, Enum):
    """What kind of thing a package is - separate from whether we resolved it.

    A vendor blob with no upstream is a finished answer, not a failure.
    """

    DEBIAN_UPSTREAM = "debian_upstream"
    DEBIAN_NO_UPSTREAM = "debian_no_upstream"
    THIRD_PARTY = "third_party"
    ARRCUS_NATIVE = "arrcus_native"
    VENDOR = "vendor"


class CommitClass(str, Enum):
    """Which side of the comparison a commit belongs to.

    The distinction between MISSING_UPSTREAM and ARCOS_ONLY is the whole point of
    the comparison: an ARCoS-specific commit is not an upstream patch waiting to
    be pulled, and counting it as one inflates the backlog with work that is
    already done.
    """

    MISSING_UPSTREAM = "MISSING_UPSTREAM"
    ARCOS_ONLY = "ARCOS_ONLY"
    ALREADY_BACKPORTED = "ALREADY_BACKPORTED"


class Criticality(str, Enum):
    """Evidence-based only. UNKNOWN is the honest default, not a gap."""

    CRITICAL = "CRITICAL"
    STABLE_RELEVANT = "STABLE_RELEVANT"
    NORMAL = "NORMAL"
    UNKNOWN = "UNKNOWN"


class PreviewOutcome(str, Enum):
    CLEAN = "CLEAN"
    CONFLICT = "CONFLICT"
    EMPTY = "EMPTY"
    FAILED = "FAILED"


class UpstreamMdOutcome(str, Enum):
    """What generating debian/upstream.md would do to what is already there."""

    CREATED = "CREATED"
    UPDATED = "UPDATED"
    NO_CHANGE = "NO_CHANGE"
    CONFLICT = "CONFLICT"


class PublishStatus(str, Enum):
    """What happened when one package's debian/upstream.md was proposed.

    Anything that stops for a human - a hand-written file, a branch someone else
    owns, a closed PR, a file that no longer matches the plan - is its own
    status, so a batch summary says what needs looking at rather than "failed".
    """

    DRY_RUN_OK = "DRY_RUN_OK"
    PR_OPENED = "PR_OPENED"
    PR_EXISTS = "PR_EXISTS"
    PR_MERGED = "PR_MERGED"
    PR_CLOSED = "PR_CLOSED"
    NO_CHANGE = "NO_CHANGE"
    SKIPPED_NO_UPSTREAM = "SKIPPED_NO_UPSTREAM"
    SKIPPED_NEEDS_REVIEW = "SKIPPED_NEEDS_REVIEW"
    SKIPPED_EXCLUDED = "SKIPPED_EXCLUDED"
    CONFLICT = "CONFLICT"
    DRIFT = "DRIFT"
    BRANCH_EXISTS = "BRANCH_EXISTS"
    BASE_UNREADABLE = "BASE_UNREADABLE"
    PUSH_FAILED = "PUSH_FAILED"
    PR_FAILED = "PR_FAILED"
    ERROR = "ERROR"

    @property
    def is_done(self) -> bool:
        """A PR is open or merged; re-running should not touch it again."""
        return self in (
            PublishStatus.PR_OPENED, PublishStatus.PR_EXISTS,
            PublishStatus.PR_MERGED,
        )

    @property
    def is_retryable(self) -> bool:
        return self in (
            PublishStatus.PUSH_FAILED, PublishStatus.PR_FAILED, PublishStatus.ERROR,
        )

    @property
    def is_failure(self) -> bool:
        return self.is_retryable


class ErrorCode(str, Enum):
    """Failure states the UI is expected to render differently."""

    AUTH_REQUIRED = "AUTH_REQUIRED"
    TIMEOUT = "TIMEOUT"
    REPOSITORY_UNAVAILABLE = "REPOSITORY_UNAVAILABLE"
    BRANCH_NOT_FOUND = "BRANCH_NOT_FOUND"
    CONFLICT = "CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    INVALID_REQUEST = "INVALID_REQUEST"
    NO_COMMON_ANCESTOR = "NO_COMMON_ANCESTOR"
    UPSTREAM_NOT_RESOLVED = "UPSTREAM_NOT_RESOLVED"
    GIT_ERROR = "GIT_ERROR"
    INTERNAL = "INTERNAL"
