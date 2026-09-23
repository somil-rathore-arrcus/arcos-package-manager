"""Domain model shared by the CLI, the API, the report writer and the frontend.

Everything the system knows how to say is expressed here. Services return these
objects, FastAPI serialises them directly, and the frontend's TypeScript types
are generated from the same schema, so there is one vocabulary rather than three
that drift.
"""

from .enums import (  # noqa: F401
    Criticality,
    CommitClass,
    ErrorCode,
    PackageCategory,
    PreviewOutcome,
    ResolutionMethod,
    ResolutionMode,
    ResolutionStatus,
    UpstreamMdOutcome,
)
from .models import (  # noqa: F401
    Branch,
    CherryPickPreview,
    CherryPickResult,
    CommitInfo,
    ComparisonResult,
    ComparisonSummary,
    CriticalityAssessment,
    DebianRelease,
    FileChange,
    Package,
    PackageSource,
    PatchInfo,
    PatchSelection,
    PullRequest,
    Repository,
    ResolutionEvidence,
    UpstreamCandidate,
    UpstreamMdDocument,
    UpstreamResolution,
)
