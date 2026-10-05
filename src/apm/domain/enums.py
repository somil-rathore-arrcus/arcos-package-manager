"""The vocabulary of the system.

Every state a package, commit or operation can be in is named here once.
"""

from __future__ import annotations

from enum import Enum


class ResolutionStatus(str, Enum):
    """How completely a package's upstream is known.

    VERIFIED      the canonical upstream is identified AND the relationship is
                  proven: shared git history, or a content match a person
                  approved. A repository that merely answers ls-remote is not
                  enough (see VerificationLevel).
    NEEDS_REVIEW  a candidate exists, but proof is missing or contradictory -
                  no shared history, a curated conflict, an unprobed ref.
    NO_UPSTREAM   there is evidence the package has no external upstream.
    FAILED        the resolver could not finish: a probe, fetch or network
                  error. Never a finding about the package.

    PARTIAL is kept for compatibility with older mappings; nothing sets it.
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


class VerificationLevel(str, Enum):
    """How strongly the ARCoS-upstream relationship has been proven.

    Separate from status and from confidence. A repository answering ls-remote
    proves the repository exists, not that ARCoS came from it - so REF_EXISTS
    alone never makes a resolution VERIFIED.
    """

    NONE = "NONE"
    # The repository was contacted and the ref resolved to a commit.
    REF_EXISTS = "REF_EXISTS"
    # git merge-base found a common ancestor with the ARCoS commit.
    SHARED_HISTORY = "SHARED_HISTORY"
    # No shared history, but a person approved a content match against a
    # release tag. Any count derived from it is synthesized, and says so.
    CONTENT_MATCH_APPROVED = "CONTENT_MATCH_APPROVED"

    @property
    def proves_relationship(self) -> bool:
        return self in (
            VerificationLevel.SHARED_HISTORY,
            VerificationLevel.CONTENT_MATCH_APPROVED,
        )


class ReviewReason(str, Enum):
    """Why a resolution is not VERIFIED, as a code a filter can match on.

    The prose reason stays; this is what makes "every package blocked by a
    curated conflict" a query rather than a reading exercise.
    """

    NO_CANDIDATE = "NO_CANDIDATE"
    NO_SHARED_HISTORY = "NO_SHARED_HISTORY"
    CURATED_CONFLICT = "CURATED_CONFLICT"
    INVALID_REF = "INVALID_REF"
    ANCESTRY_NOT_PROBED = "ANCESTRY_NOT_PROBED"
    CONTENT_MATCH_UNAPPROVED = "CONTENT_MATCH_UNAPPROVED"
    # Errors: the resolver could not finish. These make a row FAILED, never a
    # finding about the package.
    PROBE_FAILED = "PROBE_FAILED"
    NETWORK_ERROR = "NETWORK_ERROR"
    ARCOS_UNREACHABLE = "ARCOS_UNREACHABLE"

    @property
    def is_error(self) -> bool:
        return self in (
            ReviewReason.PROBE_FAILED, ReviewReason.NETWORK_ERROR,
            ReviewReason.ARCOS_UNREACHABLE,
        )


class RefStrategy(str, Enum):
    """Why this upstream ref was chosen, in descending order of preference.

    A remote's default branch is its development tip, which is the wrong thing
    to compare a Bookworm package against. It is only ever the explicit last
    resort, and labelled as one.
    """

    EXACT_TAG = "exact_tag"
    MAINTENANCE_BRANCH = "maintenance_branch"
    KERNEL_SERIES = "kernel_series"
    PACKAGING_TAG = "packaging_tag"
    PACKAGING_BRANCH = "packaging_branch"
    CURATED = "curated"
    MANUAL = "manual"
    PACKAGING_FALLBACK = "packaging_fallback"
    DEFAULT_BRANCH = "default_branch"

    @property
    def is_fallback(self) -> bool:
        return self in (
            RefStrategy.PACKAGING_FALLBACK, RefStrategy.DEFAULT_BRANCH,
        )


class Presence(str, Enum):
    """Whether an upstream commit's change is already in the ARCoS tree.

    A differing patch-id does not prove a change is missing - an adapted
    backport has a different diff by definition - and a weak match does not
    prove it is present. UNKNOWN is the honest answer between the two.
    """

    DEFINITELY_PRESENT = "DEFINITELY_PRESENT"
    PROBABLY_PRESENT = "PROBABLY_PRESENT"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


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
    PROBE_FAILED = "PROBE_FAILED"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_REF = "INVALID_REF"
    # The selection was made against a comparison the branches have since
    # moved past; applying it would apply something nobody reviewed.
    STALE_SELECTION = "STALE_SELECTION"
    BASE_MOVED = "BASE_MOVED"
    INTERNAL = "INTERNAL"
