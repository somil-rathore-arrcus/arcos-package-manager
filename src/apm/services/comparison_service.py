"""Which upstream fixes an ARCoS fork is missing, from git history.

The question is not "how far is ARCoS behind upstream master". It is: for the
release series this package is based on, which upstream changes are missing,
which are already present or backported, and which of the missing ones carry
security evidence. Only the commit graph and the trees answer it; a version
string does not.

The sets, computed head-based so criss-cross history and repeated merges cannot
inflate them:

  relevant upstream   UPSTREAM ^ARCOS - reachable from the selected upstream ref
                      (the release tag or maintenance branch), not from the ARCoS
                      commit
      already present   ... whose change is in ARCoS anyway (see backport_service)
      missing           ... whose change is not
      unknown           ... where the evidence does not decide
  ARCoS-specific      ARCOS ^UPSTREAM, minus the commits that carry upstream
                      changes

merge-base is still computed - with --all - but only to prove the histories are
related. It does not define the missing set.

Debian's own patches for the shipped version are reported beside the commits,
never among them: they are packaging, not upstream source commits.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set

from ..domain.enums import CommitClass, Criticality, ErrorCode, Presence
from ..domain.models import (
    CommitInfo, ComparisonResult, ComparisonSummary, PatchInfo, SecurityEvidence,
    UpstreamResolution,
)
from ..gitio.verify import classify_git_error
from ..gitio.workspace import (
    ARCOS_REMOTE, UPSTREAM_REMOTE, GitWorkspace, GitWorkspaceError, LogEntry,
)
from .backport_service import BackportDetector
from .criticality_service import CriticalityService
from .patch_id_service import PatchIdService
from .security_service import SecurityContext, SecurityService

log = logging.getLogger(__name__)

PRESENT = (Presence.DEFINITELY_PRESENT, Presence.PROBABLY_PRESENT)


class ComparisonError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class ComparisonService:
    def __init__(self, workspaces, criticality: Optional[CriticalityService] = None,
                 patch_ids: Optional[PatchIdService] = None,
                 max_commits: int = 500,
                 backports: Optional[BackportDetector] = None,
                 security: Optional[SecurityService] = None,
                 debian_patches: Optional[Callable] = None,
                 store=None) -> None:
        self.workspaces = workspaces
        self.criticality = criticality or CriticalityService()
        self.patch_ids = patch_ids or PatchIdService()
        self.max_commits = max_commits
        self.backports = backports or BackportDetector()
        self.security = security
        # resolution -> (DebianPatchReport, {patch name: text}) or None
        self.debian_patches = debian_patches
        self.store = store

    def compare(self, resolution: UpstreamResolution,
                arcos_branch: Optional[str] = None,
                refresh: bool = False) -> ComparisonResult:
        if not resolution.comparable:
            raise ComparisonError(
                ErrorCode.UPSTREAM_NOT_RESOLVED,
                f"{resolution.package} has no verified upstream to compare "
                f"against (status {resolution.status.value}, verification "
                f"{resolution.verification_level.value}).",
                "; ".join(r.value for r in resolution.review_reasons),
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
        arcos_head = self._fetch(workspace, ARCOS_REMOTE,
                                 resolution.arcos_repository, arcos_ref)
        upstream_head = self._fetch(workspace, UPSTREAM_REMOTE, upstream_url,
                                    upstream_ref, upstream=True)

        warnings: List[str] = list(resolution.warnings)
        mapped = resolution.upstream_commit or ""
        if mapped and not (upstream_head.startswith(mapped)
                           or mapped.startswith(upstream_head)):
            warnings.append(
                f"{upstream_ref} has moved since the mapping was made "
                f"({mapped[:12]} -> {upstream_head[:12]}). This comparison uses "
                f"the current tip; regenerate the mapping before recording it."
            )

        synthesized = resolution.synthesized_ancestry
        packaging = resolution.origin_kind == "debian_packaging"
        synthetic_base = None
        try:
            if synthesized:
                match = resolution.content_match
                base = self._fetch(workspace, "upstream-base", upstream_url,
                                   f"refs/tags/{match.base_tag}", upstream=True)
                synthetic_base = match.base_tag
                upstream_range = f"{upstream_head} ^{base}"
                arcos_range = f"{arcos_head} ^{match.arcos_tree}"
                merge_bases: List[str] = []
                warnings.append(
                    f"No shared git history: ancestry is SYNTHESIZED from a "
                    f"content match approved by {match.verified_by} "
                    f"({match.base_tag}). Counts are {match.base_tag}.."
                    f"{upstream_ref} upstream, and ARCoS commits after "
                    f"{str(match.arcos_tree)[:12]}."
                )
            else:
                merge_bases = workspace.merge_bases(arcos_head, upstream_head)
                if not merge_bases:
                    # Real and worth saying plainly: the fork was imported, not
                    # forked, so there is no commit-level relationship.
                    raise ComparisonError(
                        ErrorCode.NO_COMMON_ANCESTOR,
                        f"{resolution.package} shares no history with "
                        f"{upstream_url}. It was imported rather than forked, so "
                        f"a commit comparison would be meaningless until a "
                        f"content match is approved.",
                    )
                if len(merge_bases) > 1:
                    warnings.append(
                        f"{len(merge_bases)} merge bases (criss-cross history). "
                        f"The missing set is computed from the two heads, not "
                        f"from any one merge base."
                    )
                upstream_range = f"{upstream_head} ^{arcos_head}"
                arcos_range = f"{arcos_head} ^{upstream_head}"

            total_missing = workspace.count(upstream_range, no_merges=True)
            total_arcos_only = workspace.count(arcos_range, no_merges=True)
            truncated = total_missing > self.max_commits
            if truncated:
                warnings.append(
                    f"{total_missing} upstream commits are not in ARCoS; showing "
                    f"the {self.max_commits} most recent. The rest are counted "
                    f"as UNKNOWN in the presence summary."
                )
            upstream_entries = workspace.log(
                upstream_range, limit=self.max_commits if truncated else None,
            )
            # More of the ARCoS side than is shown: cherry-pick trailers and
            # reverts are looked for in all of it that can reasonably be read.
            arcos_entries = workspace.log(arcos_range, limit=self.patch_ids.limit)
            upstream_date = workspace.commit_date(upstream_head)
        except GitWorkspaceError as exc:
            raise ComparisonError(
                ErrorCode.TIMEOUT if exc.timed_out else ErrorCode.GIT_ERROR,
                f"git failed while comparing {resolution.package}: {exc}",
                exc.stderr,
            ) from exc

        selection = resolution.ref_selection
        base_tag = selection.base_tag if selection else None
        in_base: Optional[Set[str]] = None
        if base_tag and not synthesized:
            if base_tag == upstream_ref:
                in_base = {e.sha for e in upstream_entries}
            else:
                try:
                    tag_head = self._fetch(workspace, "upstream-base", upstream_url,
                                           f"refs/tags/{base_tag}", upstream=True)
                    in_base = set(workspace.rev_list(f"{tag_head} ^{arcos_head}"))
                except (ComparisonError, GitWorkspaceError) as exc:
                    warnings.append(f"Could not read the base tag {base_tag}: {exc}")

        # Blobs, but only if they will be used.
        #
        # Walking the commit graph needs no file contents, so both sides are
        # fetched blobless. patch-id and the content checks are the opposite:
        # they read every diff, and in a partial clone each one triggers a lazy
        # fetch. So blobs are backfilled in one pass, once per head commit, and
        # only when the range is small enough for the checks to be worth it.
        total_commits = total_missing + total_arcos_only
        content_ok = total_commits <= self.patch_ids.max_total_commits
        if content_ok:
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
            workspace, upstream_range, arcos_range,
            total_commits=total_commits,
            expected_upstream=total_missing,
            expected_arcos=total_arcos_only,
        )
        if not equivalence.available and equivalence.reason:
            warnings.append(
                f"patch-id matching unavailable: {equivalence.reason}. Other "
                f"backport evidence was still used where it could be."
            )
        assessment = self.backports.assess(
            workspace, upstream_entries, arcos_entries, equivalence.equivalent,
            arcos_head, content=content_ok,
        )
        warnings += [f"Backport detection: {n}" for n in assessment.notes]
        if not content_ok:
            warnings.append(
                f"{total_commits} commits is beyond the "
                f"{self.patch_ids.max_total_commits}-commit limit for content "
                f"checks; commits without a trailer match are UNKNOWN, not "
                f"missing."
            )

        arcos_subjects = {e.sha: e.subject for e in arcos_entries}
        missing: List[CommitInfo] = []
        backported: List[CommitInfo] = []
        for entry in upstream_entries:
            commit = self._commit(entry, resolution, CommitClass.MISSING_UPSTREAM)
            commit.patch.patch_id = equivalence.upstream_patch_ids.get(entry.sha)
            verdict = assessment.results.get(entry.sha)
            if verdict is not None:
                commit.presence = verdict.presence
                commit.presence_evidence = list(verdict.evidence)
                commit.reverts = verdict.reverts
                commit.reverted_by = verdict.reverted_by
            if in_base is not None:
                commit.in_base_release = entry.sha in in_base
            if commit.presence in PRESENT:
                commit.classification = CommitClass.ALREADY_BACKPORTED
                commit.patch.equivalent_sha = verdict.arcos_sha
                commit.patch.equivalent_subject = arcos_subjects.get(verdict.arcos_sha)
                backported.append(commit)
            else:
                missing.append(commit)

        carriers = set(assessment.carriers) | set(equivalence.equivalent.values())
        arcos_only = []
        for entry in arcos_entries:
            if entry.sha in carriers:
                continue
            commit = self._commit(entry, resolution, CommitClass.ARCOS_ONLY)
            commit.patch.patch_id = equivalence.arcos_patch_ids.get(entry.sha)
            arcos_only.append(commit)
            if len(arcos_only) >= self.max_commits:
                break

        debian_report = None
        if self.debian_patches is not None:
            try:
                provided = self.debian_patches(resolution)
            except Exception as exc:  # noqa: BLE001 - evidence, not a requirement
                log.warning("Debian patches for %s: %s", resolution.package, exc)
                provided = None
                warnings.append(f"Debian patch history unavailable: {exc}")
            if provided is not None:
                debian_report, texts = provided
                if content_ok:
                    try:
                        self.backports.assess_debian_patches(
                            workspace, debian_report, texts, arcos_head,
                            assessment.results,
                        )
                    except GitWorkspaceError as exc:
                        warnings.append(f"Debian patch presence not checked: {exc}")

        findings, sources = self._security(
            resolution, workspace, merge_bases, synthesized, arcos_head,
            missing, backported, debian_report, content_ok,
        )

        reverted = {s for pair in assessment.reverted_pairs for s in pair}
        shown_unknown = sum(1 for c in missing if c.presence in (None, Presence.UNKNOWN))
        summary = ComparisonSummary(
            merge_base=merge_bases[0] if merge_bases else None,
            merge_bases=merge_bases,
            has_common_ancestor=bool(merge_bases),
            missing_upstream=len(missing),
            arcos_only=len(arcos_only),
            already_backported=len(backported),
            critical=sum(1 for c in missing
                         if c.criticality.level is Criticality.CRITICAL),
            stable_relevant=sum(1 for c in missing
                                if c.criticality.level is Criticality.STABLE_RELEVANT),
            normal=sum(1 for c in missing
                       if c.criticality.level is Criticality.NORMAL),
            unknown_criticality=sum(1 for c in missing
                                    if c.criticality.level is Criticality.UNKNOWN),
            truncated=truncated,
            base_tag=base_tag if not synthesized else synthetic_base,
            series=selection.series if selection else None,
            upstream_commit_date=upstream_date,
            counts_basis=(
                f"SYNTHESIZED from an approved content match: "
                f"{synthetic_base}..{upstream_ref} upstream"
                if synthesized else
                f"git rev-list --no-merges {arcos_head[:12]}..{upstream_head[:12]} "
                f"(upstream commits the ARCoS commit cannot reach)"
            ) + ("; the upstream is the Debian packaging repository, so these "
                 "are packaging commits" if packaging else ""),
            packaging_commits_included=packaging,
            synthesized_ancestry=synthesized,
            synthetic_base=synthetic_base,
            relevant_upstream=total_missing,
            definitely_present=sum(1 for c in backported
                                   if c.presence is Presence.DEFINITELY_PRESENT),
            probably_present=sum(1 for c in backported
                                 if c.presence is Presence.PROBABLY_PRESENT),
            missing=sum(1 for c in missing if c.presence is Presence.MISSING
                        and c.sha not in reverted),
            unknown_presence=shown_unknown + (
                total_missing - len(upstream_entries) if truncated else 0),
            reverted_upstream=sum(1 for c in missing if c.sha in reverted),
            missing_in_base_release=(
                sum(1 for c in missing if c.in_base_release)
                if in_base is not None else None
            ),
            backport_detection=", ".join(
                ["cherry-pick trailers"]
                + (["patch-id"] if equivalence.available else [])
                + (["git apply --check", "line comparison"] if content_ok else [])
                + ["revert pairing"]
            ),
        )
        if truncated:
            summary.missing_upstream = total_missing - len(backported)
        carried_here = sum(1 for e in arcos_entries if e.sha in carriers)
        if total_arcos_only - carried_here > len(arcos_only):
            warnings.append(
                f"{total_arcos_only} ARCoS-specific commits; showing "
                f"{len(arcos_only)}."
            )

        result = ComparisonResult(
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
            debian_patches=debian_report,
            security=findings,
            security_sources=sources,
            computed_at=datetime.now(timezone.utc),
            warnings=list(dict.fromkeys(warnings)),
        )
        if self.store is not None:
            try:
                self.store.save(result)
            except OSError as exc:
                log.warning("could not store the comparison snapshot: %s", exc)
        return result

    # -- helpers -------------------------------------------------------------

    def _fetch(self, workspace: GitWorkspace, name: str, url: str, ref: str,
               upstream: bool = False) -> str:
        try:
            return workspace.fetch_side(name, url, ref)
        except GitWorkspaceError as exc:
            if exc.timed_out:
                raise ComparisonError(
                    ErrorCode.TIMEOUT, f"Timed out fetching {ref} from {url}.",
                    exc.stderr,
                ) from exc
            kind = classify_git_error(exc.stderr)
            if kind == "INVALID_REF":
                code = ErrorCode.BRANCH_NOT_FOUND if upstream else ErrorCode.INVALID_REF
            elif kind == "NETWORK_ERROR":
                code = ErrorCode.REPOSITORY_UNAVAILABLE
            else:
                code = ErrorCode.GIT_ERROR if not upstream else ErrorCode.BRANCH_NOT_FOUND
            raise ComparisonError(
                code, f"Could not read {ref} from {url}.", exc.stderr,
            ) from exc

    def _security(self, resolution, workspace, merge_bases, synthesized,
                  arcos_head, missing, backported, debian_report, content_ok):
        if self.security is None:
            return [], []
        base_commits = list(merge_bases)
        if synthesized and resolution.content_match and resolution.content_match.base_sha:
            base_commits = [resolution.content_match.base_sha]

        def contains(sha: str) -> Optional[bool]:
            try:
                return workspace.is_ancestor(sha, arcos_head)
            except GitWorkspaceError:
                return None

        context = SecurityContext(
            package=resolution.package, release=resolution.debian_release,
            upstream_repository=resolution.upstream_repository.url,
            base_commits=base_commits,
            missing=[c.sha for c in missing],
            present=[c.sha for c in backported],
            debian_patches=debian_report,
            contains=contains,
        )
        answers = self.security.assess(context)
        findings = [f for a in answers for f in a.findings]
        by_sha: Dict[str, CommitInfo] = {c.sha: c for c in missing}
        for finding in findings:
            for fix in finding.fix_commits:
                if len(fix) < 7:
                    continue
                commit = next((c for s, c in by_sha.items() if s.startswith(fix.lower())),
                              None)
                if commit is None:
                    continue
                self.criticality.enrich(commit.criticality, [SecurityEvidence(
                    source=finding.sources[0] if finding.sources else "external",
                    identifier=finding.identifier,
                    detail=finding.summary[:160], url=finding.url,
                    commit=commit.sha,
                )])
        return findings, [a.status_line for a in answers]

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
