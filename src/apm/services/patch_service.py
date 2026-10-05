"""Applying selected upstream commits to an ARCoS fork.

Two operations, deliberately separate:

  preview     works out what would happen, in a throwaway workspace that is
              destroyed afterwards. Nothing is pushed and the target branch is
              never touched, so it is safe to run on anything.
  cherry_pick performs it for real, on a NEW branch created from the target.
              The target branch itself is never modified - a patch series always
              lands somewhere a human can inspect and a reviewer can reject.

A conflict stops the series at the commit that conflicted and reports it. It is
never resolved automatically: picking a side of a conflict is a decision about
the product, and guessing it silently is how wrong code reaches a release.
"""

from __future__ import annotations

import logging
import re
import shlex
from datetime import datetime, timezone
from typing import List, Optional

from ..domain.enums import ErrorCode, PreviewOutcome
from ..domain.models import (
    CherryPickPreview, CherryPickResult, FileChange, PatchSelection,
)
from ..gitio.workspace import (
    ARCOS_REMOTE, UPSTREAM_REMOTE, GitWorkspace, GitWorkspaceError,
)

log = logging.getLogger(__name__)

DEFAULT_BRANCH_TEMPLATE = "upstream/{package}/{timestamp}"
_SAFE_BRANCH = re.compile(r"[^A-Za-z0-9._/-]+")


class PatchError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class PatchService:
    def __init__(self, workspaces, branch_template: str = DEFAULT_BRANCH_TEMPLATE
                 ) -> None:
        self.workspaces = workspaces
        self.branch_template = branch_template

    # -- naming ------------------------------------------------------------

    def branch_name(self, package: str, when: Optional[datetime] = None) -> str:
        stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
        name = self.branch_template.format(package=package, timestamp=stamp)
        return _SAFE_BRANCH.sub("-", name).strip("-/")

    # -- shared setup ------------------------------------------------------

    def _prepare(self, workspace: GitWorkspace, selection: PatchSelection,
                 arcos_repository: str, base_ref: str) -> str:
        workspace.ensure()
        try:
            base = workspace.fetch_side(ARCOS_REMOTE, arcos_repository, base_ref)
        except GitWorkspaceError as exc:
            raise PatchError(
                ErrorCode.TIMEOUT if exc.timed_out else ErrorCode.BRANCH_NOT_FOUND,
                f"Timed out reading {arcos_repository}." if exc.timed_out else
                f"Could not read {base_ref} from {arcos_repository}.",
                exc.stderr,
            ) from exc
        # Full fetch: cherry-picking needs the blobs, not just the graph. A
        # failed fetch stops here - applying against a stale upstream ref is
        # applying something nobody selected.
        fetched = workspace.fetch(
            UPSTREAM_REMOTE, selection.upstream_repository,
            selection.upstream_ref, blobless=False,
        )
        if not fetched.ok:
            raise PatchError(
                ErrorCode.REPOSITORY_UNAVAILABLE,
                f"Could not read {selection.upstream_ref} from "
                f"{selection.upstream_repository}.",
                fetched.stderr.strip(),
            )
        return base

    def _validate(self, workspace: GitWorkspace, selection: PatchSelection,
                  base: str) -> List[str]:
        """Re-check the selection against the branches as they are now.

        The comparison may have been computed against the pinned commit, or an
        hour ago; the target branch may since have moved, and the upstream ref
        may have been rewritten. So every selected commit must still be in the
        missing set against the CURRENT target tip - reachable from the upstream
        ref, not already in the target - unless it was explicitly approved, and
        even then it must be on the upstream ref. Returns warnings; raises
        STALE_SELECTION for anything else.
        """
        upstream_tip = workspace.rev_parse(f"refs/apm/{UPSTREAM_REMOTE}^{{commit}}")
        current = set(workspace.rev_list(f"{upstream_tip} ^{base}"))
        approved = {s.lower() for s in selection.approved_shas}
        problems, warnings = [], []
        for sha in selection.shas:
            full = _expand(sha, current)
            if full is not None:
                continue
            try:
                resolved = workspace.rev_parse(f"{sha}^{{commit}}")
            except GitWorkspaceError:
                problems.append(f"{sha[:12]} is not a commit on "
                                f"{selection.upstream_ref}")
                continue
            if workspace.is_ancestor(resolved, base):
                problems.append(
                    f"{sha[:12]} is already in {selection.arcos_branch} "
                    f"({base[:12]}); the comparison predates it"
                )
            elif not workspace.is_ancestor(resolved, upstream_tip):
                problems.append(
                    f"{sha[:12]} is no longer on {selection.upstream_ref} "
                    f"({upstream_tip[:12]}); the upstream ref was rewritten"
                )
            elif sha.lower() in approved or resolved in approved:
                warnings.append(
                    f"{sha[:12]} is outside the current missing set and was "
                    f"applied because it was explicitly approved"
                )
            else:
                problems.append(
                    f"{sha[:12]} is not in the current missing set "
                    f"{base[:12]}..{upstream_tip[:12]}"
                )
        if problems:
            raise PatchError(
                ErrorCode.STALE_SELECTION,
                "The selection no longer matches the branches; re-run the "
                "comparison and select again. " + "; ".join(problems[:5]),
                "\n".join(problems),
            )
        if selection.comparison_arcos_commit and not base.startswith(
                selection.comparison_arcos_commit[:12]):
            warnings.append(
                f"{selection.arcos_branch} is at {base[:12]}, not "
                f"{selection.comparison_arcos_commit[:12]} where the comparison "
                f"was computed; the selection was re-validated against "
                f"{base[:12]}"
            )
        return warnings

    def _order(self, workspace: GitWorkspace, shas: List[str],
               base: str, tip: str) -> List[str]:
        """Oldest first, in topological order.

        Applying a later commit before the one it builds on conflicts for no
        reason, so the user's click order is replaced by history order.

        Topological, not by date: commits made in the same second sort
        arbitrarily by date, which silently reverses a pair of patches and
        produces a conflict that the series does not actually contain.
        """
        if len(shas) < 2:
            return list(shas)

        wanted = set(shas)
        result = workspace.shell(
            f"git rev-list --reverse --topo-order "
            f"{shlex.quote(tip)} --not {shlex.quote(base)}",
            check=True,
        )
        ordered = [
            line.strip() for line in result.stdout.splitlines()
            if line.strip() in wanted
        ]
        # Explicitly approved commits git did not place keep the caller's
        # order after the rest, rather than being dropped.
        missing = [sha for sha in shas if sha not in set(ordered)]
        return ordered + missing

    def _apply(self, workspace: GitWorkspace, shas: List[str]):
        applied: List[str] = []
        for sha in shas:
            result = workspace.cherry_pick(sha)
            if result.ok:
                applied.append(sha)
                continue
            combined = (result.stdout + result.stderr).lower()
            if "nothing to commit" in combined or "previous cherry-pick is now empty" in combined:
                # Already present. Skip it rather than failing the series.
                workspace.shell("git cherry-pick --skip", check=False)
                applied.append(sha)
                continue
            conflicts = workspace.conflicted_paths()
            workspace.cherry_pick_abort()
            return applied, sha, conflicts, result.stderr.strip()
        return applied, None, [], ""

    # -- preview -----------------------------------------------------------

    def preview(self, selection: PatchSelection, arcos_repository: str,
                base_ref: Optional[str] = None) -> CherryPickPreview:
        """Try the series in a throwaway workspace and report what happened."""
        if not selection.shas:
            return CherryPickPreview(
                outcome=PreviewOutcome.EMPTY, package=selection.package,
                arcos_branch=selection.arcos_branch,
                message="No commits were selected.",
            )

        workspace = self.workspaces.scratch(
            f"preview-{selection.package}-{selection.debian_release}"
        )
        workspace.destroy()
        try:
            base = self._prepare(
                workspace, selection, arcos_repository,
                base_ref or selection.arcos_branch,
            )
            upstream_tip = workspace.rev_parse(
                f"refs/apm/{UPSTREAM_REMOTE}^{{commit}}")
            checks = self._validate(workspace, selection, base)
            moved = bool(selection.comparison_arcos_commit) and not base.startswith(
                selection.comparison_arcos_commit[:12])
            ordered = self._order(
                workspace, selection.shas, base, f"refs/apm/{UPSTREAM_REMOTE}"
            )
            workspace.checkout_new_branch("apm-preview", base)
            applied, failed, conflicts, stderr = self._apply(workspace, ordered)
            common = dict(
                package=selection.package, arcos_branch=selection.arcos_branch,
                order=ordered, base_sha=base, upstream_sha=upstream_tip,
                base_moved=moved, validated=True,
            )

            if failed:
                return CherryPickPreview(
                    outcome=PreviewOutcome.CONFLICT, applied=applied,
                    failed_sha=failed, conflicts=conflicts,
                    message=(
                        f"{failed[:12]} does not apply cleanly onto "
                        f"{selection.arcos_branch} ({base[:12]}). "
                        f"{len(applied)} of {len(ordered)} applied before it."
                    ),
                    warnings=checks + ([stderr[:400]] if stderr else []),
                    **common,
                )

            changes = [
                FileChange(**change)
                for change in workspace.show_stat(f"{base}..HEAD")
            ]
            return CherryPickPreview(
                outcome=PreviewOutcome.CLEAN, applied=applied,
                files_changed=changes, warnings=checks,
                message=(f"All {len(applied)} commits apply cleanly onto "
                         f"{selection.arcos_branch} at {base[:12]}."),
                **common,
            )
        except PatchError:
            raise
        except GitWorkspaceError as exc:
            return CherryPickPreview(
                outcome=PreviewOutcome.FAILED, package=selection.package,
                arcos_branch=selection.arcos_branch,
                message=str(exc), warnings=[exc.stderr[:400]] if exc.stderr else [],
            )
        finally:
            # A preview leaves nothing behind, so it can be run freely.
            workspace.destroy()

    # -- the real thing ----------------------------------------------------

    def cherry_pick(self, selection: PatchSelection, arcos_repository: str,
                    branch_name: Optional[str] = None,
                    base_ref: Optional[str] = None,
                    push: bool = False,
                    push_url: Optional[str] = None) -> CherryPickResult:
        """Apply the series to a new branch. Never to the target branch."""
        if not selection.shas:
            raise PatchError(
                ErrorCode.INVALID_REQUEST, "No commits were selected."
            )

        base_branch = base_ref or selection.arcos_branch
        new_branch = branch_name or self.branch_name(selection.package)
        if new_branch == base_branch:
            raise PatchError(
                ErrorCode.INVALID_REQUEST,
                "The patch branch must differ from the target branch; this tool "
                "never commits directly to the branch being tracked.",
            )

        workspace = self.workspaces.scratch(
            f"apply-{selection.package}-{selection.debian_release}"
        )
        workspace.destroy()
        try:
            base = self._prepare(workspace, selection, arcos_repository, base_branch)
            if selection.expected_base_sha and not base.startswith(
                    selection.expected_base_sha):
                raise PatchError(
                    ErrorCode.BASE_MOVED,
                    f"{base_branch} moved from {selection.expected_base_sha[:12]} "
                    f"to {base[:12]} since the preview. Nothing was applied; "
                    f"preview again.",
                )
            self._validate(workspace, selection, base)
            ordered = self._order(
                workspace, selection.shas, base, f"refs/apm/{UPSTREAM_REMOTE}"
            )
            workspace.checkout_new_branch(new_branch, base)
            applied, failed, conflicts, stderr = self._apply(workspace, ordered)
        except BaseException:
            workspace.destroy()
            raise

        if failed:
            workspace.destroy()
            return CherryPickResult(
                outcome=PreviewOutcome.CONFLICT, package=selection.package,
                base_branch=base_branch, new_branch=new_branch, applied=applied,
                base_sha=base, failed_sha=failed, conflicts=conflicts,
                message=(
                    f"{failed[:12]} conflicted; nothing was pushed and the "
                    f"workspace was discarded."
                ),
            )

        head = workspace.rev_parse("HEAD")
        pushed = False
        if push:
            result = workspace.push(push_url or arcos_repository, new_branch)
            pushed = result.ok
            if not pushed:
                workspace.destroy()
                raise PatchError(
                    ErrorCode.AUTH_REQUIRED
                    if "permission" in result.stderr.lower()
                    or "authentication" in result.stderr.lower()
                    else ErrorCode.GIT_ERROR,
                    f"Could not push {new_branch}.",
                    result.stderr.strip()[:400],
                )

        workspace.destroy()
        return CherryPickResult(
            outcome=PreviewOutcome.CLEAN, package=selection.package,
            base_branch=base_branch, new_branch=new_branch, applied=applied,
            base_sha=base, head_sha=head, pushed=pushed,
            message=f"Applied {len(applied)} commits to {new_branch}.",
        )


def _expand(sha: str, candidates) -> Optional[str]:
    """The full SHA in `candidates` that `sha` names (full or a >=7 prefix)."""
    sha = (sha or "").lower()
    if sha in candidates:
        return sha
    if len(sha) >= 7:
        matches = [c for c in candidates if c.startswith(sha)]
        if len(matches) == 1:
            return matches[0]
    return None
