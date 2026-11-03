"""Propose debian/upstream.md to each ARCoS package: branch, commit, push, PR.

This is the only code that writes to an ARCoS repository on behalf of the
upstream.md workflow, and it is built so that the things it must never do are
not merely avoided but unreachable:

  the target branch   no local branch is ever created and the only push is
                      <sha>:refs/heads/upstream-metadata/..., validated against
                      the base name before anything runs
  force               there is no force option anywhere in this path
  other files         the commit is the base tree plus one index entry, and
                      the changed-path list is checked to be exactly that entry
  hand-written files  an existing file that is neither one this tool wrote
                      (an older version's marker, or the two-field format)
                      is a CONFLICT and stops the package
  unverified mappings only VERIFIED packages get past eligibility
  merging             there is no call that merges anything
  push-only           a push-only run stops at the pushed branch and hands
                      back the link a person opens the pull request from. It
                      never opens a pull request and needs no token. Only
                      without a token does it make no API call at all; with
                      one set it makes at most one read per package - the
                      lookup for an earlier pull request an apply makes - and
                      leaves a merged, closed or open one alone. A package
                      the ledger records with a pull request, including one
                      a reviewer closed, is never pushed by it

Every package ends in a PublishResult, recorded before the next one starts; a
failure in one package is written down and the batch moves on.

Reads (tip, existing file, remote branch) all happen before the first write, and
a dry run stops after building - and checking - the exact commit that would be
pushed, so what a dry run reports is what an apply does.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..domain.enums import (
    ErrorCode, PublishStatus, ResolutionStatus, UpstreamMdOutcome,
)
from ..domain.models import PublishResult, UpstreamResolution
from .github_service import (
    GitHubError, build_upstream_md_pr_body, pull_request_link,
)
from .upstream_md_service import UPSTREAM_MD_PATH, classify

log = logging.getLogger(__name__)

BRANCH_PREFIX = "upstream-metadata/"
# Per release and package: trixie can follow without colliding, and two packages
# built from one repository (ONL-standalone and ONL-xc) cannot share a branch.
BRANCH_TEMPLATE = BRANCH_PREFIX + "{release}/{package}"
# The name the ARCoS maintainers asked for: one branch per repository. It names
# neither the release nor the package, so a repository holds one such proposal
# at a time; the publisher refuses a second rather than replace the first.
TRACKING_BRANCH = "debian-upstream-tracking"

# Every name this workflow may push to, whatever settings.yaml says, so a typo
# there cannot point a push at a release branch: anything under
# upstream-metadata/, and debian-upstream-tracking itself or anything under it.
_NAMESPACES = (BRANCH_PREFIX, TRACKING_BRANCH + "/")

# Characters and sequences git refuses in a ref name, plus whitespace.
_BAD_REF = re.compile(r"\.\.|[\s~^:?*\[\\]|@\{|//|/\.|\.lock(/|$)|^/|/$|\.$")

_STATUS_SKIP = {
    ResolutionStatus.NO_UPSTREAM: PublishStatus.SKIPPED_NO_UPSTREAM,
    ResolutionStatus.NEEDS_REVIEW: PublishStatus.SKIPPED_NEEDS_REVIEW,
}

# Records of a branch that waits for a person rather than for the next run:
# pushed by a push-only run, or found by _pushed_file_problem holding a file
# other than the reviewed one (only that check writes a DRIFT record with the
# branch's commit). The plan can be regenerated while such a branch waits, so
# resuming it - or any branch, in a push-only run - first re-reads the pushed
# file. Resuming any other record behaves as it always has.
_WAITING_RECORDS = (PublishStatus.BRANCH_PUSHED, PublishStatus.DRIFT)


class PublishError(RuntimeError):
    """A run-level refusal: nothing has been pushed when this is raised."""

    def __init__(self, code: ErrorCode, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


# -- naming -------------------------------------------------------------------

def branch_for(package: str, release: str,
               template: str = BRANCH_TEMPLATE) -> str:
    return template.format(package=package, release=release)


def in_namespace(branch: str) -> bool:
    """Whether `branch` is a name this workflow may create."""
    return bool(branch) and (
        branch == TRACKING_BRANCH
        or any(branch.startswith(ns) and len(branch) > len(ns)
               for ns in _NAMESPACES)
    )


def names_proposal(branch: str, package: str, release: str) -> bool:
    """Whether the branch name alone identifies this release's package.

    upstream-metadata/bookworm/mstpd does; debian-upstream-tracking is shared by
    every release and package of the repository, so a pull request found by
    that name proves nothing about which proposal it was.
    """
    parts = branch.split("/")
    return package in parts and release in parts


def validate_branch(branch: str, base: str) -> None:
    """Refuse any branch this workflow must not write to. Raises PublishError."""
    if not in_namespace(branch):
        raise PublishError(
            ErrorCode.INVALID_REQUEST,
            f"Branch {branch!r} is not {TRACKING_BRANCH} or under "
            f"{BRANCH_PREFIX}; this workflow only creates branches there.",
        )
    if branch == base:
        raise PublishError(
            ErrorCode.INVALID_REQUEST,
            f"Branch {branch!r} is the target branch. The target is never "
            f"written to directly.",
        )
    if _BAD_REF.search(branch):
        raise PublishError(
            ErrorCode.INVALID_REQUEST, f"{branch!r} is not a valid branch name."
        )


def validate_template(template: str) -> str:
    """The branch_template from settings.yaml, checked once at start-up so a bad
    one stops the service instead of the first push."""
    try:
        sample = branch_for("package", "release", template)
    except (KeyError, IndexError, ValueError) as exc:
        raise PublishError(
            ErrorCode.INVALID_REQUEST,
            f"branch_template {template!r} is not valid ({exc!r}); the only "
            f"placeholders are {{release}} and {{package}}.",
        ) from exc
    validate_branch(sample, base="")
    return template


def commit_title(package: str, outcome: Optional[UpstreamMdOutcome],
                 prefix: str = "") -> str:
    verb = "update" if outcome is UpstreamMdOutcome.UPDATED else "add"
    return f"{prefix}{package}: {verb} {UPSTREAM_MD_PATH} with verified upstream"


def commit_message(title: str) -> str:
    return (
        f"{title}\n\n"
        f"Generated by the ARCoS Package Manager from the verified upstream "
        f"mapping. Only {UPSTREAM_MD_PATH} changes.\n"
    )


def lint_content(content: str) -> Optional[str]:
    """Why `content` would fail `git diff --check`, or None when it is clean."""
    if not content.strip():
        return "the file is empty"
    if "\r" in content:
        return "the file has carriage returns"
    if not content.endswith("\n") or content.endswith("\n\n"):
        return "the file must end with exactly one newline"
    for number, line in enumerate(content.split("\n"), 1):
        if line != line.rstrip():
            return f"line {number} has trailing whitespace"
    return None


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _owner(others: Sequence[PublishResult], target: "PublishTarget",
           branch: str, sha: str) -> Optional[PublishResult]:
    """The other package or release whose record says it pushed `sha` to
    `branch` on this repository, or None."""
    for other in others:
        if (other.package, other.release) == (target.package, target.release):
            continue
        if other.branch == branch and other.commit == sha and \
                other.repository.lower() == target.slug.lower():
            return other
    return None


# -- what to publish ----------------------------------------------------------

@dataclass
class PublishTarget:
    package: str
    release: str
    status: ResolutionStatus
    repository_url: str
    slug: str
    base_branch: str
    content: str
    pinned_commit: Optional[str] = None
    resolution: Optional[UpstreamResolution] = None


def load_targets(plan_path: Path, mapping, renderer,
                 exclude: Optional[Dict[str, str]] = None,
                 ) -> Tuple[List[PublishTarget], List[PublishResult]]:
    """Read the reviewed plan into publishable targets and settled results.

    The committed bytes are the reviewed file under out/upstream-md/, and they
    must still be what the plan hashed and what the mapping renders today;
    anything else is DRIFT and goes back to a person rather than being quietly
    regenerated.
    """
    plan_path = Path(plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    release = plan.get("release", "")
    exclude = exclude or {}
    md_dir = plan_path.parent / "upstream-md"

    targets: List[PublishTarget] = []
    settled: List[PublishResult] = []

    def settle(package, status, error="", **extra):
        settled.append(PublishResult(
            package=package, release=release, status=status, error=error or None,
            **extra,
        ))

    for item in plan.get("skipped", []):
        try:
            status = ResolutionStatus(item.get("status", ""))
        except ValueError:
            status = ResolutionStatus.NEEDS_REVIEW
        settle(
            item["package"],
            _STATUS_SKIP.get(status, PublishStatus.SKIPPED_NEEDS_REVIEW),
            item.get("reason", ""),
        )

    for entry in plan.get("entries", []):
        package = entry["package"]
        slug = entry.get("arcos_repository", "")
        base = entry.get("base_branch", "")
        common = dict(repository=slug, base_branch=base)
        if package in exclude:
            settle(package, PublishStatus.SKIPPED_EXCLUDED, exclude[package], **common)
            continue
        try:
            status = ResolutionStatus(entry.get("status", ""))
        except ValueError:
            status = ResolutionStatus.NEEDS_REVIEW
        if status is not ResolutionStatus.VERIFIED:
            settle(package, _STATUS_SKIP.get(status, PublishStatus.SKIPPED_NEEDS_REVIEW),
                   entry.get("reason", "") or f"status {status.value}", **common)
            continue

        path = md_dir / package / "debian" / "upstream.md"
        if not path.exists() and entry.get("local_path"):
            path = Path(entry["local_path"])
        if not path.exists():
            settle(package, PublishStatus.DRIFT,
                   f"the reviewed file {path} is missing; regenerate", **common)
            continue
        content = path.read_bytes().decode("utf-8")

        expected = entry.get("sha256")
        if not expected:
            settle(package, PublishStatus.ERROR,
                   "the plan predates content hashes; run generate-upstream-md "
                   "again and review it", **common)
            continue
        if sha256_text(content) != expected:
            settle(package, PublishStatus.DRIFT,
                   f"{path} changed after the plan was written", **common)
            continue

        resolution = mapping.get(package, release) if mapping else None
        if resolution is None:
            settle(package, PublishStatus.ERROR,
                   f"no mapping row for {package}/{release}", **common)
            continue
        if resolution.status is not ResolutionStatus.VERIFIED:
            settle(package,
                   _STATUS_SKIP.get(resolution.status,
                                    PublishStatus.SKIPPED_NEEDS_REVIEW),
                   f"the mapping now says {resolution.status.value}", **common)
            continue
        if not resolution.verification_level.proves_relationship:
            settle(package, PublishStatus.SKIPPED_NEEDS_REVIEW,
                   f"verification level {resolution.verification_level.value} "
                   f"does not prove the relationship (a mapping written before "
                   f"verification levels existed?); regenerate it", **common)
            continue
        if renderer.render(resolution) != content:
            settle(package, PublishStatus.DRIFT,
                   "the mapping has changed since the file was generated; "
                   "regenerate and review", **common)
            continue

        targets.append(PublishTarget(
            package=package, release=release, status=status,
            repository_url=resolution.arcos_repository
            or f"ssh://git@github.com/{slug}.git",
            slug=slug, base_branch=base or resolution.arcos_branch,
            content=content, pinned_commit=resolution.arcos_commit,
            resolution=resolution,
        ))
    return targets, settled


def pull_request_body(target: PublishTarget, result: PublishResult) -> str:
    """The description of this package's pull request.

    One function for the API call and the push-only link alike, so a pull
    request opened from the link says exactly what one the tool opened would.
    """
    if target.resolution is None:
        return result.title
    return build_upstream_md_pr_body(
        target.resolution, result.base_sha, content=target.content,
        outcome=result.outcome,
    )


def target_from_resolution(resolution: UpstreamResolution,
                           content: str) -> PublishTarget:
    """A target for one package resolved live, as the API does."""
    from .github_service import repo_slug

    slug = resolution.github_repository or repo_slug(resolution.arcos_repository) or ""
    return PublishTarget(
        package=resolution.package, release=resolution.debian_release,
        status=resolution.status, repository_url=resolution.arcos_repository,
        slug=slug, base_branch=resolution.arcos_branch, content=content,
        pinned_commit=resolution.arcos_commit, resolution=resolution,
    )


# -- the publisher ------------------------------------------------------------

class UpstreamMdPublisher:
    def __init__(self, workspaces, github, author_name: str, author_email: str,
                 branch_template: str = BRANCH_TEMPLATE,
                 exclude: Optional[Dict[str, str]] = None,
                 defer: Sequence[str] = ()) -> None:
        self.workspaces = workspaces
        self.github = github
        self.author_name = author_name
        self.author_email = author_email
        self.branch_template = branch_template
        self.exclude = dict(exclude or {})
        self.defer = list(defer)

    # -- run-level checks --------------------------------------------------

    def preflight(self, slugs: Iterable[str], push_only: bool = False) -> None:
        """Everything that must hold before the first push, checked up front.

        A token that cannot open pull requests would otherwise be discovered
        after a branch had been pushed, leaving it behind with no PR. A
        push-only run opens no pull request, so neither the token nor the API's
        view of each repository matters to it; the push itself is the check.
        """
        if push_only:
            return
        if not self.github.can_create_pull_requests:
            raise PublishError(
                ErrorCode.AUTH_REQUIRED,
                "APM_GITHUB_TOKEN is not set, so no pull request could be "
                "opened. Nothing was pushed.",
            )
        for slug in sorted(set(s for s in slugs if s)):
            try:
                self.github.get_repository(slug)
            except GitHubError as exc:
                raise PublishError(
                    exc.code, f"Pre-flight failed for {slug}: {exc} Nothing was "
                    f"pushed.", exc.detail,
                ) from exc

    def ordered(self, targets: Sequence[PublishTarget]) -> List[PublishTarget]:
        """Plan order, with the large repositories last."""
        rank = {name: i for i, name in enumerate(self.defer)}
        return sorted(
            targets, key=lambda t: (t.package in rank, rank.get(t.package, 0),
                                    t.package),
        )

    def branch_of(self, target: PublishTarget,
                  prior: Optional[PublishResult] = None) -> str:
        """The branch this package's proposal lives on.

        A branch this tool already pushed, or opened a pull request from, keeps
        its name whatever the template says now: changing the template must not
        leave a second branch carrying the same file beside the first.
        """
        if prior is not None and prior.branch and (
                prior.pushed or prior.pull_request is not None):
            return prior.branch
        return branch_for(target.package, target.release, self.branch_template)

    def refuse_shared_branches(
            self, queue: Sequence[Tuple[PublishTarget, Optional[PublishResult]]],
    ) -> Tuple[List[Tuple[PublishTarget, Optional[PublishResult]]],
               List[PublishResult]]:
        """Split off every package whose branch an earlier one in `queue` uses.

        Under a name shared by the whole repository (debian-upstream-tracking),
        two packages built from one repository would otherwise both be proposed
        on it, and the second would find the first's branch. The first in plan
        order keeps it; the rest are refused here, before any confirmation
        count, so a dry run reports what an apply would do.
        """
        kept, refused = [], []
        claimed: Dict[Tuple[str, str], PublishTarget] = {}
        for target, prior in queue:
            branch = self.branch_of(target, prior)
            first = claimed.setdefault((target.slug.lower(), branch), target)
            if first is target:
                kept.append((target, prior))
                continue
            refused.append(PublishResult(
                package=target.package, release=target.release,
                status=PublishStatus.BRANCH_EXISTS, repository=target.slug,
                base_branch=target.base_branch, branch=branch,
                pinned_commit=target.pinned_commit,
                error=(f"{branch} on {target.slug} is this run's branch for "
                       f"{first.package} ({first.release}); a branch name the "
                       f"whole repository shares carries one proposal at a time"),
            ))
        return kept, refused

    # -- one package -------------------------------------------------------

    def publish(self, target: PublishTarget, *, apply: bool, draft: bool = False,
                title_prefix: str = "", branch: Optional[str] = None,
                prior: Optional[PublishResult] = None,
                push_only: bool = False,
                others: Sequence[PublishResult] = ()) -> PublishResult:
        """`others` are the ledger records of every other package and release,
        so a branch one of them pushed is named as theirs, not as a stranger's.
        """
        branch = branch or self.branch_of(target, prior)
        result = PublishResult(
            package=target.package, release=target.release,
            status=PublishStatus.ERROR, repository=target.slug,
            base_branch=target.base_branch, branch=branch,
            pinned_commit=target.pinned_commit,
        )

        if target.package in self.exclude:
            result.status = PublishStatus.SKIPPED_EXCLUDED
            result.error = self.exclude[target.package]
            return result
        if target.status is not ResolutionStatus.VERIFIED:
            result.status = _STATUS_SKIP.get(
                target.status, PublishStatus.SKIPPED_NEEDS_REVIEW
            )
            result.error = f"mapping status is {target.status.value}"
            return result
        if target.resolution is not None and \
                not target.resolution.verification_level.proves_relationship:
            result.status = PublishStatus.SKIPPED_NEEDS_REVIEW
            result.error = (
                f"verification level "
                f"{target.resolution.verification_level.value} does not prove "
                f"the relationship"
            )
            return result

        validate_branch(branch, target.base_branch)
        if not target.slug or not target.repository_url or not target.base_branch:
            result.error = "the repository or target branch is unknown"
            return result
        problem = lint_content(target.content)
        if problem:
            result.error = f"refusing to commit: {problem}"
            return result
        if apply and not push_only and not self.github.can_create_pull_requests:
            raise PublishError(
                ErrorCode.AUTH_REQUIRED,
                "APM_GITHUB_TOKEN is not set, so no pull request could be "
                "opened. Nothing was pushed.",
            )
        if push_only and prior is not None and (
                prior.status.is_done or prior.status is PublishStatus.PR_CLOSED):
            # A pull request exists, or a reviewer closed one. A link would only
            # duplicate the first; the second was a person's decision, and
            # without a token push-only could not even see it. The record stands.
            return prior.model_copy(deep=True)

        workspace = self.workspaces.scratch(
            f"publish-{target.release}-{target.package}"
        )
        try:
            workspace.destroy()
            workspace.ensure()
            done = self._prepare_and_push(
                workspace, target, result, apply=apply, title_prefix=title_prefix,
                prior=prior, push_only=push_only, others=others,
            )
        finally:
            workspace.destroy()
        if not done:
            if push_only and result.status is PublishStatus.DRY_RUN_OK:
                # The link the push would give, so the dry run shows it too.
                self._attach_link(target, result)
            return result
        if push_only:
            return self._branch_pushed(target, result)
        return self._open_pull_request(target, result, draft=draft)

    def _prepare_and_push(self, workspace, target: PublishTarget,
                          result: PublishResult, *, apply: bool,
                          title_prefix: str,
                          prior: Optional[PublishResult],
                          push_only: bool = False,
                          others: Sequence[PublishResult] = ()) -> bool:
        """Steps up to and including the push. True when a PR should follow."""
        url, base, branch = target.repository_url, target.base_branch, result.branch

        tip = workspace.remote_ref(url, f"refs/heads/{base}")
        if tip is None:
            result.error = f"{target.slug} has no branch {base}"
            return False
        result.base_sha = tip
        result.base_moved = bool(
            target.pinned_commit and not tip.startswith(target.pinned_commit)
        )

        moved = self._upstream_moved(workspace, target)
        if moved:
            result.status = PublishStatus.DRIFT
            result.error = moved
            return False

        existing_branch = workspace.remote_ref(url, f"refs/heads/{branch}")
        if existing_branch is not None:
            if prior and prior.commit and prior.commit == existing_branch:
                # Pushed by an earlier run of this tool; carry on to the PR.
                result.commit = existing_branch
                result.pushed = True
                result.outcome = prior.outcome
                result.title = prior.title or commit_title(
                    target.package, prior.outcome, title_prefix
                )
                stale = None
                if push_only or prior.status in _WAITING_RECORDS:
                    stale = self._pushed_file_problem(
                        workspace, target, branch, existing_branch
                    )
                if stale:
                    result.status = PublishStatus.DRIFT
                    result.error = stale
                    return False
                if not apply:
                    result.status = PublishStatus.DRY_RUN_OK
                    result.error = (
                        "branch already pushed; an apply would give its link again"
                        if push_only else
                        "branch already pushed; an apply would open the PR"
                    )
                    return False
                return not self._settled_by_earlier_pr(
                    target, result, push_only=push_only, prior=prior
                )
            result.status = PublishStatus.BRANCH_EXISTS
            result.commit = existing_branch
            owner = _owner(others, target, branch, existing_branch)
            if owner is not None:
                result.error = (
                    f"{branch} on {target.slug} holds this tool's proposal for "
                    f"{owner.package} ({owner.release}) at "
                    f"{existing_branch[:12]}; a branch name the whole repository "
                    f"shares carries one proposal at a time, so this one waits "
                    f"until that pull request is merged or the branch deleted"
                )
            else:
                result.error = (
                    f"{branch} already exists at {existing_branch[:12]} and this "
                    f"tool has no record of creating it"
                )
            return False

        head = workspace.fetch_side("arcos", url, base, depth=1)
        if head != tip:
            result.error = f"{base} moved from {tip[:12]} to {head[:12]} during the run"
            return False

        existing: Optional[str] = None
        if workspace.path_exists(head, UPSTREAM_MD_PATH):
            existing = workspace.read_file(UPSTREAM_MD_PATH, rev=head)
            if existing is None:
                result.status = PublishStatus.BASE_UNREADABLE
                result.error = f"{UPSTREAM_MD_PATH} exists at {head[:12]} but could not be read"
                return False

        outcome, diff = classify(existing, target.content)
        result.outcome = outcome
        result.diff = diff or None
        if outcome is UpstreamMdOutcome.NO_CHANGE:
            result.status = PublishStatus.NO_CHANGE
            return False
        if outcome is UpstreamMdOutcome.CONFLICT:
            result.status = PublishStatus.CONFLICT
            result.error = (
                f"{UPSTREAM_MD_PATH} at {head[:12]} was not written by this tool; "
                f"review it before replacing it"
            )
            return False

        if self._settled_by_earlier_pr(target, result, push_only=push_only,
                                       prior=prior):
            return False

        result.title = commit_title(target.package, outcome, title_prefix)
        commit = workspace.commit_file_on(
            head, UPSTREAM_MD_PATH, target.content, commit_message(result.title),
            self.author_name, self.author_email,
        )
        result.commit = commit

        changed = workspace.changed_paths(head, commit)
        if changed != [UPSTREAM_MD_PATH]:
            result.error = f"the commit would change {changed}, not only {UPSTREAM_MD_PATH}"
            return False
        whitespace = workspace.diff_check(head, commit)
        if whitespace:
            result.error = f"git diff --check failed: {whitespace[:300]}"
            return False
        if workspace.rev_parse(f"{commit}^") != head:
            result.error = "the commit's parent is not the target branch tip"
            return False

        if not apply:
            result.status = PublishStatus.DRY_RUN_OK
            return False

        push = workspace.push_commit(url, commit, branch)
        if not push.ok:
            result.status = PublishStatus.PUSH_FAILED
            result.error = (push.stderr.strip() or "push rejected")[:400]
            return False
        if workspace.remote_ref(url, f"refs/heads/{branch}") != commit:
            result.status = PublishStatus.PUSH_FAILED
            result.error = f"after the push {branch} does not point at {commit[:12]}"
            return False
        result.pushed = True
        return True

    def _upstream_moved(self, workspace, target: PublishTarget) -> Optional[str]:
        """Why the proposal would describe a stale upstream, or None.

        The file names only the repository and the branch, which a new upstream
        commit does not change. The pull request body states more: "this
        upstream ref, at this commit", the merge base and every count beside
        it. If the ref has moved since the mapping was made, that evidence
        describes a commit that is no longer the tip, so the package goes back
        for re-resolution instead of being proposed on it. A branch a push-only
        run pushed keeps its ledger record meanwhile (see
        publish_ledger.supersedes).
        """
        resolution = target.resolution
        if resolution is None or not resolution.upstream_repository:
            return None
        recorded = resolution.upstream_commit or ""
        ref = resolution.upstream_ref or ""
        if not recorded or not ref:
            return "the mapping records no upstream commit to check against"
        url = resolution.upstream_repository.url
        if resolution.upstream_tag:
            names = [f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}"]
        else:
            names = [f"refs/heads/{ref}"]
        current = None
        for name in names:
            current = workspace.remote_ref(url, name)
            if current:
                break
        if current is None:
            return f"upstream {ref} no longer exists on {url}; regenerate"
        if not (current.startswith(recorded) or recorded.startswith(current)):
            return (
                f"upstream {ref} moved from {recorded[:12]} to {current[:12]} "
                f"since the mapping was made; re-run resolve-release and "
                f"generate-upstream-md, then review"
            )
        return None

    def _pushed_file_problem(self, workspace, target: PublishTarget,
                             branch: str, sha: str) -> Optional[str]:
        """Why this tool's pushed branch cannot be proposed as it is, or None.

        A branch pushed before the plan changed carries the old file, and this
        tool never overwrites a branch; proposing it would propose bytes nobody
        reviewed this time. Asked only by push-only runs and for the records in
        _WAITING_RECORDS, whose branches wait for a person, often across a
        regenerated plan.
        """
        head = workspace.fetch_side("arcos", target.repository_url,
                                    f"refs/heads/{branch}", depth=1)
        if head != sha:
            return f"{branch} moved from {sha[:12]} to {head[:12]} during the run"
        if workspace.read_file(UPSTREAM_MD_PATH, rev=sha) == target.content:
            return None
        return (
            f"{branch} (pushed by this tool at {sha[:12]}) holds a different "
            f"{UPSTREAM_MD_PATH} from the reviewed one; delete that branch on "
            f"the fork - closing any pull request opened from it - then run "
            f"again"
        )

    def _settled_by_earlier_pr(self, target: PublishTarget,
                               result: PublishResult,
                               push_only: bool = False,
                               prior: Optional[PublishResult] = None) -> bool:
        """True when a pull request for this branch already decided it.

        A merged one is done. A closed one was turned down by a reviewer, and
        proposing it again is a decision for a person, not for a re-run. An
        open one settles only a push-only run, which would otherwise hand out a
        link to open it a second time; an apply goes on to find it as
        PR_EXISTS. Without a token nothing can be looked up, and this is False.

        GitHub finds pull requests by head-branch name. Where the name is shared
        by the whole repository, one found may be another release's or another
        package's, so it counts only if its head is the commit this package's
        record holds.
        """
        if not self.github.can_create_pull_requests:
            return False
        earlier = self.github.find_pull_request(
            target.slug, result.branch, target.base_branch, state="all"
        )
        if earlier is None:
            return False
        if not names_proposal(result.branch, target.package, target.release) \
                and not (prior is not None and prior.commit
                         and earlier.head_sha == prior.commit):
            return False
        if earlier.state == "open":
            if not push_only:
                return False
            result.pull_request = earlier
            result.status = PublishStatus.PR_EXISTS
            result.error = None
            return True
        result.pull_request = earlier
        if earlier.state == "merged":
            result.status = PublishStatus.PR_MERGED
            result.error = None
        else:
            result.status = PublishStatus.PR_CLOSED
            result.error = (
                f"a pull request for {result.branch} was closed without merging "
                f"(#{earlier.number}); re-proposing it is a decision for a person"
            )
        return True

    def _branch_pushed(self, target: PublishTarget,
                       result: PublishResult) -> PublishResult:
        """Push-only: stop at the pushed branch, with the link to open the PR.

        No pull request exists yet, so this is BRANCH_PUSHED rather than done: a
        later run with a token resumes from the ledger and opens it without
        pushing again.
        """
        self._attach_link(target, result)
        result.status = PublishStatus.BRANCH_PUSHED
        result.error = None
        return result

    def _attach_link(self, target: PublishTarget, result: PublishResult) -> None:
        result.pr_create_url, complete = pull_request_link(
            target.slug, target.base_branch, result.branch, result.title,
            pull_request_body(target, result),
        )
        if not complete:
            log.info("%s: the pull request body is too long for the link; it "
                     "is left out of it", target.package)

    def _open_pull_request(self, target: PublishTarget, result: PublishResult,
                           *, draft: bool) -> PublishResult:
        try:
            pr = self.github.create_pull_request(
                target.slug, result.branch, target.base_branch, result.title,
                pull_request_body(target, result), draft=draft,
            )
        except GitHubError as exc:
            result.status = PublishStatus.PR_FAILED
            result.error = f"{exc} {exc.detail}".strip()[:400]
            # The branch is pushed; a person can still open the pull request.
            self._attach_link(target, result)
            return result
        result.pull_request = pr
        result.error = None
        result.status = (
            PublishStatus.PR_EXISTS if pr.already_existed else PublishStatus.PR_OPENED
        )
        return result

    # -- many packages -----------------------------------------------------

    def select(self, targets: Sequence[PublishTarget], history=None, *,
               retry_failed: bool = False, recheck: bool = False,
               limit: Optional[int] = None, push_only: bool = False,
               ) -> Tuple[List[Tuple[PublishTarget, Optional[PublishResult]]],
                          List[PublishResult]]:
        """Which targets to process, and the earlier results kept for the rest.

        A push-only run also keeps what it can do nothing more for: a branch it
        already pushed (BRANCH_PUSHED), whose link the links file carries, and
        a pull request a reviewer closed (PR_CLOSED). Queuing them would make
        --limit take the same packages every time and --confirm-count count
        them. A run with a token still queues BRANCH_PUSHED - to open its pull
        request. --recheck queues everything, as it always has.
        """
        settled_for_push_only = (PublishStatus.BRANCH_PUSHED,
                                 PublishStatus.PR_CLOSED)
        queue, kept = [], []
        for target in self.ordered(targets):
            prior = history.get(target.package) if history else None
            if retry_failed:
                if prior and prior.status.is_retryable:
                    queue.append((target, prior))
                elif prior:
                    kept.append(prior)
                continue
            if prior and not recheck and (
                    prior.status.is_done
                    or (push_only and prior.status in settled_for_push_only)):
                kept.append(prior)
                continue
            queue.append((target, prior))
        if limit is not None:
            queue = queue[:max(limit, 0)]
        return queue, kept

    def publish_many(self, queue: Sequence[Tuple[PublishTarget, Optional[PublishResult]]],
                     *, apply: bool, ledger=None,
                     confirm_count: Optional[int] = None,
                     stop_after_failures: int = 3, draft: bool = False,
                     title_prefix: str = "",
                     progress: Optional[Callable[[int, int, str], None]] = None,
                     push_only: bool = False,
                     others: Sequence[PublishResult] = (),
                     ) -> List[PublishResult]:
        """Process `queue` in order; one package's failure never stops the rest,
        but `stop_after_failures` consecutive ones do - that is a systemic
        problem (auth, the bridge), not a package.

        `push_only` pushes but opens no pull request. It is a real push all the
        same, so it needs the same confirmation as any other apply."""
        if apply:
            if confirm_count != len(queue):
                raise PublishError(
                    ErrorCode.INVALID_REQUEST,
                    f"This run would push {len(queue)} package(s); confirm with "
                    f"--confirm-count {len(queue)}. Nothing was pushed.",
                )
            self.preflight((t.slug for t, _ in queue), push_only=push_only)

        results: List[PublishResult] = []
        consecutive = 0
        for index, (target, prior) in enumerate(queue, 1):
            if progress:
                progress(index, len(queue), target.package)
            try:
                result = self.publish(
                    target, apply=apply, draft=draft, title_prefix=title_prefix,
                    prior=prior, push_only=push_only, others=others,
                )
            except PublishError:
                raise
            except Exception as exc:  # noqa: BLE001 - recorded, never fatal
                log.exception("publishing %s failed", target.package)
                result = PublishResult(
                    package=target.package, release=target.release,
                    status=PublishStatus.ERROR, repository=target.slug,
                    base_branch=target.base_branch,
                    branch=self.branch_of(target, prior),
                    pinned_commit=target.pinned_commit,
                    error=f"{type(exc).__name__}: {exc} "
                          f"{getattr(exc, 'stderr', '')}".strip()[:400],
                )
            if ledger is not None:
                ledger.record(result)
            results.append(result)

            consecutive = consecutive + 1 if result.status.is_failure else 0
            if stop_after_failures and consecutive >= stop_after_failures:
                log.error(
                    "%d consecutive failures; stopping before %d remaining "
                    "package(s)", consecutive, len(queue) - index,
                )
                break
        return results
