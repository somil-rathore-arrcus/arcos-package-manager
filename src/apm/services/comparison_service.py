"""Work out how far an ARCoS fork trails its upstream, from git history.

The question is "which upstream commits are not in this fork", and only the
commit graph answers it. Version strings do not: a Debian version says what the
packaging claims, not what the branch contains, and two forks at the same version
can differ by hundreds of commits.

The three sets are kept apart deliberately:

  missing upstream    reachable from upstream, not from ARCoS - the backlog
  ARCoS-specific      reachable from ARCoS, not from upstream - local work
  already backported  in both, under different SHAs - done, do not re-apply

Collapsing ARCoS-specific commits into the backlog would report local work as
patches waiting to be pulled.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from ..domain.enums import CommitClass, Criticality, ErrorCode
from ..domain.models import (
    CommitInfo, ComparisonResult, ComparisonSummary, PatchInfo, UpstreamResolution,
)
from ..gitio.workspace import (
    ARCOS_REMOTE, UPSTREAM_REMOTE, GitWorkspace, GitWorkspaceError, LogEntry,
)
from .criticality_service import CriticalityService
from .patch_id_service import PatchIdService

log = logging.getLogger(__name__)


class ComparisonError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class ComparisonService:
    def __init__(self, workspaces, criticality: Optional[CriticalityService] = None,
                 patch_ids: Optional[PatchIdService] = None,
                 max_commits: int = 500) -> None:
        self.workspaces = workspaces
        self.criticality = criticality or CriticalityService()
        self.patch_ids = patch_ids or PatchIdService()
        self.max_commits = max_commits

    def compare(self, resolution: UpstreamResolution,
                arcos_branch: Optional[str] = None,
                refresh: bool = False) -> ComparisonResult:
        if not resolution.comparable:
            raise ComparisonError(
                ErrorCode.UPSTREAM_NOT_RESOLVED,
                f"{resolution.package} has no verified upstream to compare "
                f"against (status {resolution.status.value}).",
            )

        branch = arcos_branch or resolution.arcos_branch
        upstream_url = resolution.upstream_repository.url
        upstream_ref = resolution.upstream_ref

        workspace = self.workspaces.for_package(
            resolution.package, resolution.debian_release
        )
        if refresh:
            workspace.destroy()
        workspace.ensure()

        # The manifest pins the commit that actually ships; the branch tip may
        # have moved past it. Compare what ships.
        arcos_ref = resolution.arcos_commit or branch
        try:
            arcos_head = workspace.fetch_side(
                ARCOS_REMOTE, resolution.arcos_repository, arcos_ref
            )
        except GitWorkspaceError as exc:
            raise ComparisonError(
                ErrorCode.TIMEOUT if exc.timed_out
                else ErrorCode.REPOSITORY_UNAVAILABLE,
                f"Timed out reading {resolution.arcos_repository}."
                if exc.timed_out else
                f"Could not read {resolution.arcos_repository} at {arcos_ref}.",
                exc.stderr,
            ) from exc

        try:
            upstream_head = workspace.fetch_side(
                UPSTREAM_REMOTE, upstream_url, upstream_ref
            )
        except GitWorkspaceError as exc:
            raise ComparisonError(
                ErrorCode.TIMEOUT if exc.timed_out else ErrorCode.BRANCH_NOT_FOUND,
                f"Timed out fetching {upstream_url}." if exc.timed_out else
                f"Could not read {upstream_ref} from {upstream_url}.",
                exc.stderr,
            ) from exc

        merge_base = workspace.merge_base(arcos_head, upstream_head)
        warnings: List[str] = []
        if not merge_base:
            # Real and worth saying plainly: the fork was imported, not forked,
            # so there is no commit-level relationship to measure.
            raise ComparisonError(
                ErrorCode.NO_COMMON_ANCESTOR,
                f"{resolution.package} shares no history with {upstream_url}. "
                f"It was imported rather than forked, so a commit comparison "
                f"would be meaningless.",
            )

        total_missing = workspace.count(
            f"{merge_base}..{upstream_head}", no_merges=True
        )
        total_arcos_only = workspace.count(
            f"{merge_base}..{arcos_head}", no_merges=True
        )

        truncated = total_missing > self.max_commits
        if truncated:
            warnings.append(
                f"{total_missing} upstream commits are missing; showing the "
                f"{self.max_commits} most recent."
            )

        upstream_entries = workspace.log(
            f"{merge_base}..{upstream_head}",
            limit=self.max_commits if truncated else None,
        )
        arcos_entries = workspace.log(
            f"{merge_base}..{arcos_head}",
            limit=self.max_commits,
        )

        # Blobs, but only if they will be used.
        #
        # Walking the commit graph needs no file contents, so both sides are
        # fetched blobless - fast, and tiny for a large repository. patch-id is
        # the opposite: it materialises every diff, and in a partial clone each
        # one triggers a lazy fetch back to the server. Measured on iputils, a
        # 4 MB workspace spent over four minutes in `git index-pack --promisor`
        # re-downloading blobs, and the request never returned.
        #
        # So the blobs are backfilled in one pass (`fetch --refetch`, because a
        # plain re-fetch of an unmoved ref downloads nothing), once per head
        # commit, and only when the range is small enough for backport detection
        # to be worth it. A kernel-sized comparison skips both the download and
        # the diffing, and says so.
        total_commits = total_missing + total_arcos_only
        if total_commits <= self.patch_ids.max_total_commits:
            for remote, url, ref, head in (
                (ARCOS_REMOTE, resolution.arcos_repository, arcos_ref, arcos_head),
                (UPSTREAM_REMOTE, upstream_url, upstream_ref, upstream_head),
            ):
                backfill = workspace.backfill_blobs(remote, url, ref, head)
                if backfill is not None and not backfill.ok:
                    warnings.append(
                        f"Could not fetch file contents for {url}; backport "
                        f"detection may be slow or unavailable."
                    )

        equivalence = self.patch_ids.compare(
            workspace, merge_base, upstream_head, arcos_head,
            total_commits=total_commits,
            expected_upstream=total_missing,
            expected_arcos=total_arcos_only,
        )
        if not equivalence.available and equivalence.reason:
            warnings.append(
                f"Backport detection unavailable: {equivalence.reason}. "
                f"Commits already applied under a different SHA may appear as "
                f"missing."
            )

        arcos_subjects = {e.sha: e.subject for e in arcos_entries}

        missing: List[CommitInfo] = []
        backported: List[CommitInfo] = []
        for entry in upstream_entries:
            commit = self._commit(entry, resolution, CommitClass.MISSING_UPSTREAM)
            commit.patch.patch_id = equivalence.upstream_patch_ids.get(entry.sha)
            twin = equivalence.equivalent.get(entry.sha)
            if twin:
                commit.classification = CommitClass.ALREADY_BACKPORTED
                commit.patch.equivalent_sha = twin
                commit.patch.equivalent_subject = arcos_subjects.get(twin)
                backported.append(commit)
            else:
                missing.append(commit)

        already_there = set(equivalence.equivalent.values())
        arcos_only = []
        for entry in arcos_entries:
            if entry.sha in already_there:
                continue
            commit = self._commit(entry, resolution, CommitClass.ARCOS_ONLY)
            commit.patch.patch_id = equivalence.arcos_patch_ids.get(entry.sha)
            arcos_only.append(commit)

        summary = ComparisonSummary(
            merge_base=merge_base,
            has_common_ancestor=True,
            missing_upstream=len(missing),
            arcos_only=len(arcos_only),
            already_backported=len(backported),
            critical=sum(
                1 for c in missing if c.criticality.level is Criticality.CRITICAL
            ),
            stable_relevant=sum(
                1 for c in missing
                if c.criticality.level is Criticality.STABLE_RELEVANT
            ),
            normal=sum(
                1 for c in missing if c.criticality.level is Criticality.NORMAL
            ),
            unknown_criticality=sum(
                1 for c in missing if c.criticality.level is Criticality.UNKNOWN
            ),
            truncated=truncated,
        )
        if truncated:
            summary.missing_upstream = total_missing - len(backported)
        if total_arcos_only > len(arcos_only):
            warnings.append(
                f"{total_arcos_only} ARCoS-specific commits; showing "
                f"{len(arcos_only)}."
            )

        return ComparisonResult(
            package=resolution.package,
            debian_release=resolution.debian_release,
            arcos_repository=resolution.arcos_repository,
            arcos_branch=branch,
            arcos_commit=arcos_head,
            upstream_repository=upstream_url,
            upstream_ref=upstream_ref,
            upstream_commit=upstream_head,
            summary=summary,
            missing_upstream=missing,
            arcos_only=arcos_only,
            already_backported=backported,
            computed_at=datetime.now(timezone.utc),
            warnings=warnings,
        )

    def _commit(self, entry: LogEntry, resolution: UpstreamResolution,
                classification: CommitClass) -> CommitInfo:
        return CommitInfo(
            sha=entry.sha,
            short_sha=entry.sha[:12],
            subject=entry.subject,
            author_name=entry.author_name,
            author_email=entry.author_email,
            authored_at=_parse_date(entry.authored_at),
            body=entry.body,
            classification=classification,
            criticality=self.criticality.assess(entry.subject, entry.body),
            patch=PatchInfo(),
            web_url=_commit_url(
                resolution.arcos_repository
                if classification is CommitClass.ARCOS_ONLY
                else (
                    resolution.upstream_repository.url
                    if resolution.upstream_repository else None
                ),
                entry.sha,
            ),
        )


def _parse_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _commit_url(repository: Optional[str], sha: str) -> Optional[str]:
    if not repository:
        return None
    url = repository
    for prefix in ("ssh://git@", "git@", "git://"):
        if url.startswith(prefix):
            url = "https://" + url[len(prefix):].replace(":", "/", 1)
            break
    url = url[:-4] if url.endswith(".git") else url
    if "github.com" in url:
        return f"{url}/commit/{sha}"
    if "gitlab" in url or "salsa.debian.org" in url:
        return f"{url}/-/commit/{sha}"
    if "git.kernel.org" in repository:
        return f"{repository}/commit/?id={sha}"
    return None
