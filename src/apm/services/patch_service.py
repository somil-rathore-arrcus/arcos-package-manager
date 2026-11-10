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

A clean series is then validated: the configured commands (settings.yaml
patches.validation_commands) run in the patched tree, in the preview and again
before a push, and a failure stops the push. With none configured the result
says nothing was built or tested - never that it passed.
"""

from __future__ import annotations

import logging
import re
import shlex
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from ..domain.enums import ErrorCode, PreviewOutcome
from ..domain.models import (
    AppliedCommit, CherryPickPreview, CherryPickResult, FileChange,
    PatchSelection, ValidationReport, ValidationRun,
)
from ..gitio.workspace import (
    ARCOS_REMOTE, UPSTREAM_REMOTE, GitWorkspace, GitWorkspaceError,
)

log = logging.getLogger(__name__)

DEFAULT_BRANCH_TEMPLATE = "upstream/{package}/{timestamp}"
# Every patch branch lives here: a name a caller supplies must too, so no
# request can name a release branch, however it spells it.
PATCH_BRANCH_PREFIX = "upstream/"
_SAFE_BRANCH = re.compile(r"[^A-Za-z0-9._/-]+")
_BAD_PATCH_BRANCH = re.compile(r"\.\.|//|/\.|\.lock(/|$)|/$|\.$")


def validate_patch_branch(name: str, base: str) -> None:
    """Refuse a patch branch this tool would not create. Raises PatchError."""
    if (not name.startswith(PATCH_BRANCH_PREFIX)
            or len(name) == len(PATCH_BRANCH_PREFIX)
            or _SAFE_BRANCH.search(name) or _BAD_PATCH_BRANCH.search(name)):
        raise PatchError(
            ErrorCode.INVALID_REQUEST,
            f"{name!r} is not a patch branch this tool creates: it must be under "
            f"{PATCH_BRANCH_PREFIX} and use only letters, digits, '.', '_', '-' "
            f"and '/'.",
        )
    target = base[len("refs/heads/"):] if base.startswith("refs/heads/") else base
    if name == target:
        raise PatchError(
            ErrorCode.INVALID_REQUEST,
            "The patch branch must differ from the target branch; this tool "
            "never commits directly to the branch being tracked.",
        )
# Enough of a failing build's output to see why, without shipping megabytes.
_OUTPUT_TAIL = 4000
NOT_CONFIGURED = (
    "No validation command is configured (patches.validation_commands in "
    "config/settings.yaml), so nothing was built or tested."
)
_EXIT_MARKER = "__apm_validation_exit="


class PatchError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class PatchService:
    def __init__(self, workspaces, branch_template: str = DEFAULT_BRANCH_TEMPLATE,
                 validation_commands: Optional[List[str]] = None,
                 validation_timeout: int = 900) -> None:
        self.workspaces = workspaces
        self.branch_template = branch_template
        self.validation_commands = list(validation_commands or [])
        self.validation_timeout = validation_timeout

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

    def _run_validation(self, workspace: GitWorkspace) -> ValidationReport:
        """Run the configured commands in the patched tree; stop at the first
        failure. Commands come from settings.yaml only, never from a request."""
        if not self.validation_commands:
            return ValidationReport(configured=False, passed=None,
                                    message=NOT_CONFIGURED)
        runs: List[ValidationRun] = []
        for command in self.validation_commands:
            started = time.monotonic()
            # The real exit status, reported on a marker line: the transport
            # only says whether the command succeeded.
            result = workspace.shell(
                # The newline ends a trailing "# comment" in the command
                # before it can swallow the bracket and the marker.
                f"( {command}\n); __rc=$?; echo \"{_EXIT_MARKER}$__rc\" >&2; "
                f"exit $__rc",
                timeout=self.validation_timeout,
            )
            exit_code, stderr = _exit_code(result.stderr)
            output = (result.stdout + stderr).strip()
            timed_out = exit_code is None and "timed out after" in result.stderr
            runs.append(ValidationRun(
                command=command,
                exit_code=exit_code,
                timed_out=timed_out,
                duration_seconds=round(time.monotonic() - started, 1),
                output=output[-_OUTPUT_TAIL:],
            ))
            if not result.ok:
                why = (f"timed out after {self.validation_timeout}s"
                       if timed_out else f"exited with status {exit_code}")
                return ValidationReport(
                    configured=True, passed=False, runs=runs,
                    message=f"`{command}` {why}; nothing may be pushed until "
                            f"validation passes.",
                )
        return ValidationReport(
            configured=True, passed=True, runs=runs,
            message=f"All {len(runs)} validation command(s) passed.",
        )

    def _describe(self, workspace: GitWorkspace, shas: List[str]) -> List[AppliedCommit]:
        """Subject, author and date of each applied upstream commit, for the
        pull request and the dashboard. Best effort: a commit git cannot show
        is listed by its SHA alone."""
        if not shas:
            return []
        result = workspace.shell(
            "git show -s --format=%H%x1f%an%x1f%aI%x1f%s "
            + " ".join(shlex.quote(s) for s in shas)
        )
        found = {}
        for line in result.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                found[parts[0]] = AppliedCommit(
                    sha=parts[0], author=parts[1], date=parts[2], subject=parts[3])
        return [found.get(sha) or AppliedCommit(sha=sha) for sha in shas]

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
            f"preview-{selection.package}-{selection.debian_release}-"
            f"{uuid.uuid4().hex[:8]}"
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
                # No conflicted path means git failed for another reason - a
                # merge commit, a timeout, a missing object - not a conflict.
                return CherryPickPreview(
                    outcome=PreviewOutcome.CONFLICT if conflicts
                    else PreviewOutcome.FAILED,
                    applied=applied, failed_sha=failed, conflicts=conflicts,
                    message=(
                        f"{failed[:12]} "
                        + ("does not apply cleanly onto" if conflicts
                           else "could not be applied onto")
                        + f" {selection.arcos_branch} ({base[:12]}). "
                        f"{len(applied)} of {len(ordered)} applied before it."
                    ),
                    warnings=checks + ([_without_hints(stderr)[:400]]
                                       if _without_hints(stderr) else []),
                    **common,
                )

            changes = [
                FileChange(**change)
                for change in workspace.show_stat(f"{base}..HEAD")
            ]
            validation = self._run_validation(workspace)
            if validation.passed is False:
                return CherryPickPreview(
                    outcome=PreviewOutcome.VALIDATION_FAILED, applied=applied,
                    files_changed=changes, warnings=checks,
                    validation=validation,
                    message=(f"All {len(applied)} commits apply cleanly onto "
                             f"{selection.arcos_branch} at {base[:12]}, but "
                             f"validation failed: {validation.message}"),
                    **common,
                )
            return CherryPickPreview(
                outcome=PreviewOutcome.CLEAN, applied=applied,
                files_changed=changes, warnings=checks, validation=validation,
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
        if push and not getattr(self.workspaces, "allow_push", True):
            raise PatchError(
                ErrorCode.PUSH_DISABLED,
                "Pushing is disabled on this machine (APM_ALLOW_PUSH is not "
                "set); nothing was applied or pushed.",
            )

        base_branch = base_ref or selection.arcos_branch
        new_branch = branch_name or self.branch_name(selection.package)
        validate_patch_branch(new_branch, base_branch)

        # Its own directory: two requests for one package must not delete each
        # other's work half way, and push what the other one applied.
        workspace = self.workspaces.scratch(
            f"apply-{selection.package}-{selection.debian_release}-"
            f"{uuid.uuid4().hex[:8]}"
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
                outcome=PreviewOutcome.CONFLICT if conflicts
                else PreviewOutcome.FAILED,
                package=selection.package,
                base_branch=base_branch, new_branch=new_branch, applied=applied,
                base_sha=base, failed_sha=failed, conflicts=conflicts,
                message=(
                    f"{failed[:12]} "
                    + ("conflicted" if conflicts else
                       f"could not be applied ({_without_hints(stderr)[:200]})")
                    + "; nothing was pushed and the workspace was discarded."
                ),
            )

        try:
            # What was applied is what is pushed: a validation command that
            # commits (gbp dch --commit) must not add to the branch.
            head = workspace.rev_parse("HEAD")
            commits = self._describe(workspace, applied)
            validation = self._run_validation(workspace)
        except BaseException:
            workspace.destroy()
            raise
        if validation.passed is False:
            workspace.destroy()
            return CherryPickResult(
                outcome=PreviewOutcome.VALIDATION_FAILED, package=selection.package,
                base_branch=base_branch, new_branch=new_branch, applied=applied,
                base_sha=base, commits=commits, validation=validation,
                message=(f"Validation failed; nothing was pushed and the "
                         f"workspace was discarded. {validation.message}"),
            )

        pushed = False
        if push:
            url = push_url or arcos_repository
            try:
                existing = workspace.remote_ref(url, f"refs/heads/{new_branch}")
            except GitWorkspaceError as exc:
                workspace.destroy()
                raise PatchError(
                    ErrorCode.REPOSITORY_UNAVAILABLE,
                    f"Could not check whether {new_branch} exists; nothing was "
                    f"pushed.", exc.stderr,
                ) from exc
            if existing is not None:
                workspace.destroy()
                raise PatchError(
                    ErrorCode.CONFLICT,
                    f"{new_branch} already exists on the fork at {existing[:12]}; "
                    f"nothing was pushed. A patch branch is only ever created.",
                )
            result = workspace.push_commit(url, head, new_branch)
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
            base_sha=base, head_sha=head, pushed=pushed, commits=commits,
            validation=validation,
            message=f"Applied {len(applied)} commits to {new_branch}.",
        )


def _without_hints(stderr: str) -> str:
    """git's error without its "hint:" lines, which tell a person to resolve
    and `git cherry-pick --continue` in a workspace that no longer exists."""
    return "\n".join(
        line for line in stderr.splitlines() if not line.startswith("hint:")
    ).strip()


def _exit_code(stderr: str):
    """(exit status, stderr without the marker line); None when the command
    never reported one - it was killed or timed out."""
    code, kept = None, []
    for line in stderr.splitlines():
        if line.startswith(_EXIT_MARKER):
            value = line[len(_EXIT_MARKER):].strip()
            code = int(value) if value.isdigit() else None
        else:
            kept.append(line)
    return code, "\n".join(kept)


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
