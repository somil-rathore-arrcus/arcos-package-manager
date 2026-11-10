"""Propose debian/upstream.md to each ARCoS package: branch, commit, push, link.

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
  pull requests       every run stops at the pushed branch and hands back
                      its Compare Link; a person opens the pull request from
                      it. Nothing here can open one: a request for anything
                      but a push is refused before work starts, and the GitHub
                      client only reads. With a token set it makes at most one
                      read per package - the lookup for an earlier pull
                      request - and leaves a merged, closed or open one alone.
                      A package the ledger records with a pull request,
                      including one a reviewer closed, is never pushed
  duplicates          a package whose repository and target branch already
                      have another release's branch is never given a second
                      one: it is COVERED when that branch holds the identical
                      file, and refused for a person otherwise
  where               a push runs only where the workspaces allow it
                      (APM_ALLOW_PUSH, set on the one machine pushes run from)

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
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..domain.enums import (
    ErrorCode, PublishStatus, ResolutionStatus, UpstreamMdOutcome,
)
from ..domain.models import PublishResult, UpstreamResolution
from .github_service import (
    build_upstream_md_pr_body, pull_request_link,
)
from .upstream_md_service import UPSTREAM_MD_PATH, classify, review_dir

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
# debian-upstream-tracking-<release>: the second release's branch in a
# repository whose debian-upstream-tracking already carries the first release's
# proposal. git cannot hold debian-upstream-tracking/<release> beside it.
_TRACKING_RELEASE = re.compile(rf"^{re.escape(TRACKING_BRANCH)}-[a-z][a-z0-9]{{2,}}$")

# Characters and sequences git refuses in a ref name, plus whitespace.
_BAD_REF = re.compile(r"\.\.|[\s~^:?*\[\\]|@\{|//|/\.|\.lock(/|$)|^/|/$|\.$")

_STATUS_SKIP = {
    ResolutionStatus.NO_UPSTREAM: PublishStatus.SKIPPED_NO_UPSTREAM,
    ResolutionStatus.NEEDS_REVIEW: PublishStatus.SKIPPED_NEEDS_REVIEW,
}


NO_PULL_REQUESTS = (
    "This application never opens a pull request. It pushes the branch and "
    "returns its Compare Link; a person opens the pull request from there."
)
PUSH_DISABLED = (
    "Pushing is disabled on this machine (APM_ALLOW_PUSH is not set). Branches "
    "are pushed only from the machine that holds the ARCoS git access."
)


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


def template_for(release: str, template: str,
                 per_release: Optional[Dict[str, str]] = None) -> str:
    """The release's own template from settings.yaml, else the shared one."""
    return (per_release or {}).get(release) or template


def in_namespace(branch: str) -> bool:
    """Whether `branch` is a name this workflow may create."""
    return bool(branch) and (
        branch == TRACKING_BRANCH
        or bool(_TRACKING_RELEASE.match(branch))
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
            f"Branch {branch!r} is not {TRACKING_BRANCH}, "
            f"{TRACKING_BRANCH}-<release> or under {BRANCH_PREFIX}; this "
            f"workflow only creates branches there.",
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
    `branch` on this repository, or None. A COVERED record names a branch it
    did not push, so it is never the owner."""
    for other in others:
        if (other.package, other.release) == (target.package, target.release):
            continue
        if other.status is PublishStatus.COVERED:
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

    The committed bytes are the reviewed file under out/upstream-md-<release>/,
    and they must still be what the plan hashed and what the mapping renders
    today; anything else is DRIFT and goes back to a person rather than being
    quietly regenerated. The CLI and the dashboard both publish from here.
    """
    plan_path = Path(plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    release = plan.get("release", "")
    exclude = exclude or {}
    md_dir = review_dir(plan_path.parent, release)

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
                 defer: Sequence[str] = (),
                 release_templates: Optional[Dict[str, str]] = None) -> None:
        self.workspaces = workspaces
        self.github = github
        self.author_name = author_name
        self.author_email = author_email
        self.branch_template = branch_template
        self.release_templates = dict(release_templates or {})
        self.exclude = dict(exclude or {})
        self.defer = list(defer)

    # -- run-level checks --------------------------------------------------

    def preflight(self, push_only: bool = True) -> None:
        """What must hold before the first push, checked up front: that this is
        a push and nothing more, and that this machine is the one pushes run
        from. Nothing has been pushed when it refuses."""
        if not push_only:
            raise PublishError(ErrorCode.PULL_REQUESTS_DISABLED, NO_PULL_REQUESTS)
        if not getattr(self.workspaces, "allow_push", True):
            raise PublishError(ErrorCode.PUSH_DISABLED, PUSH_DISABLED)

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
        return branch_for(target.package, target.release, template_for(
            target.release, self.branch_template, self.release_templates))

    def covering_record(self, target: PublishTarget,
                        prior: Optional[PublishResult] = None,
                        others: Sequence[PublishResult] = (),
                        ) -> Optional[PublishResult]:
        """Another release's record of a branch proposing to this package's
        repository and target branch, while this one has no branch of its own.

        Read from the records alone, so a run can count its pushes before it
        starts: a package with such a record is never pushed. It is COVERED when
        that branch holds the identical file, and refused for a person when it
        does not - one target branch takes one debian/upstream.md. A merged one
        is not counted: the target then has the file, and NO_CHANGE says so.
        """
        if prior is not None and prior.branch and (
                prior.pushed or prior.pull_request is not None):
            return None
        for other in others:
            if other.release == target.release or other.status in (
                    PublishStatus.COVERED, PublishStatus.PR_MERGED):
                continue
            if not (other.branch and other.commit and (
                    other.pushed or other.pull_request is not None)):
                continue
            if other.repository.lower() == target.slug.lower() and \
                    other.base_branch == target.base_branch:
                return other
        return None

    def would_push(self, queue: Sequence[Tuple[PublishTarget, Optional[PublishResult]]],
                   others: Sequence[PublishResult] = (),
                   ) -> List[Tuple[PublishTarget, Optional[PublishResult]]]:
        """The entries of `queue` a run may push: every one no other release's
        branch covers. What a confirmation counts."""
        return [(t, p) for t, p in queue
                if self.covering_record(t, p, others) is None]

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

    def publish(self, target: PublishTarget, *, apply: bool,
                title_prefix: str = "",
                prior: Optional[PublishResult] = None,
                push_only: bool = True,
                others: Sequence[PublishResult] = ()) -> PublishResult:
        """`others` are the ledger records of every other package and release,
        so a branch one of them pushed is named as theirs, not as a stranger's,
        and one that already proposes this file is not proposed again.

        The branch is never the caller's choice: it is the one this package's
        record names, else the one settings.yaml's template gives. A push is
        all an apply does; asking for more (push_only=False) is refused.
        """
        if apply:
            self.preflight(push_only=push_only)
        branch = self.branch_of(target, prior)
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
        if prior is not None and (
                prior.status.is_done or prior.status is PublishStatus.PR_CLOSED):
            # A pull request exists, or a reviewer closed one. A link would only
            # duplicate the first; the second was a person's decision, and
            # without a token this could not even see it. The record stands.
            return prior.model_copy(deep=True)

        workspace = self.workspaces.scratch(
            f"publish-{target.release}-{target.package}"
        )
        try:
            workspace.destroy()
            workspace.ensure()
            done = self._prepare_and_push(
                workspace, target, result, apply=apply, title_prefix=title_prefix,
                prior=prior, others=others,
            )
        finally:
            workspace.destroy()
        if not done:
            if result.status is PublishStatus.DRY_RUN_OK:
                # The link the push would give, so the dry run shows it too.
                self._attach_link(target, result)
            return result
        return self._branch_pushed(target, result)

    def _prepare_and_push(self, workspace, target: PublishTarget,
                          result: PublishResult, *, apply: bool,
                          title_prefix: str,
                          prior: Optional[PublishResult],
                          others: Sequence[PublishResult] = ()) -> bool:
        """Steps up to and including the push. True when the branch is there
        to hand out: pushed now, or by an earlier run of this tool."""
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
                # Pushed by an earlier run of this tool; hand out its link again.
                result.commit = existing_branch
                result.pushed = True
                result.outcome = prior.outcome
                result.title = prior.title or commit_title(
                    target.package, prior.outcome, title_prefix
                )
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
                    )
                    return False
                return not self._settled_by_earlier_pr(target, result, prior=prior)
            result.status = PublishStatus.BRANCH_EXISTS
            result.commit = existing_branch
            covering = self.covering_record(target, prior, others)
            if covering is not None and covering.branch == branch:
                # Another release's proposal under a name both releases share:
                # the same file there covers this package.
                self._covered(workspace, target, result, covering)
                return False
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

        covering = self.covering_record(target, prior, others)
        if covering is not None:
            self._covered(workspace, target, result, covering)
            return False

        if self._settled_by_earlier_pr(target, result, prior=prior):
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
        reviewed this time. Asked whenever a pushed branch is resumed: it
        waits for a person, often across a regenerated plan.
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

    def _covered(self, workspace, target: PublishTarget, result: PublishResult,
                 covering: PublishResult) -> None:
        """Settle `result` against another release's branch for the same
        repository and target branch (see covering_record). Never pushes.

        COVERED only when that branch is still at its recorded commit and holds
        exactly this file; anything else is for a person, because a second
        branch would put a second proposal in front of the same target.
        """
        url, other = target.repository_url, covering.branch
        whose = f"{other} ({covering.release}'s proposal for {target.base_branch})"
        result.status = PublishStatus.BRANCH_EXISTS
        if covering.status is PublishStatus.PR_CLOSED:
            result.error = (f"the pull request from {whose} was closed without "
                            f"merging; proposing the file again is a decision "
                            f"for a person")
            return
        tip = workspace.remote_ref(url, f"refs/heads/{other}")
        if tip is None or tip != covering.commit:
            where = "is gone" if tip is None else f"is at {tip[:12]}"
            result.error = (f"{whose} {where}, not at the recorded "
                            f"{covering.commit[:12]}; what {target.release} "
                            f"proposes is a decision for a person")
            return
        head = workspace.fetch_side("covering", url, f"refs/heads/{other}", depth=1)
        if head != tip:
            result.error = f"{other} moved from {tip[:12]} to {head[:12]} during the run"
            return
        if workspace.read_file(UPSTREAM_MD_PATH, rev=tip) != target.content:
            result.error = (f"{whose} holds a different {UPSTREAM_MD_PATH}; one "
                            f"target branch takes one file, so which one is a "
                            f"decision for a person")
            return
        result.status = PublishStatus.COVERED
        result.covered_by = covering.release
        result.branch = other
        result.commit = tip
        result.error = None

    def _settled_by_earlier_pr(self, target: PublishTarget,
                               result: PublishResult,
                               prior: Optional[PublishResult] = None) -> bool:
        """True when a pull request for this branch already decided it.

        A merged one is done. A closed one was turned down by a reviewer, and
        proposing it again is a decision for a person, not for a re-run. An
        open one settles it too: handing out its link again would only invite a
        second pull request. Without a token nothing can be looked up, and this
        is False.

        GitHub finds pull requests by head-branch name. Where the name is shared
        by the whole repository, one found may be another release's or another
        package's, so it counts only if its head is the commit this package's
        record holds.
        """
        if not self.github.has_token:
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
        """Stop at the pushed branch, with the link to open the PR.

        No pull request exists, so this is BRANCH_PUSHED rather than done; a
        person opens it from the link, and a later run finds it by its branch.
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

    # -- many packages -----------------------------------------------------

    def select(self, targets: Sequence[PublishTarget], history=None, *,
               retry_failed: bool = False, recheck: bool = False,
               limit: Optional[int] = None, push_only: bool = True,
               ) -> Tuple[List[Tuple[PublishTarget, Optional[PublishResult]]],
                          List[PublishResult]]:
        """Which targets to process, and the earlier results kept for the rest.

        A run also keeps what it can do nothing more for: a branch it already
        pushed (BRANCH_PUSHED), whose link the links file carries, a package
        another release's branch covers (COVERED), and a pull request a
        reviewer closed (PR_CLOSED). Queuing them would make --limit take the
        same packages every time and --confirm-count count them. --recheck
        queues everything, as it always has. (`push_only` is accepted for the
        callers that still pass it; every run is push-only.)
        """
        settled_for_push_only = (PublishStatus.BRANCH_PUSHED,
                                 PublishStatus.COVERED, PublishStatus.PR_CLOSED)
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
                    prior.status.is_done or prior.status in settled_for_push_only):
                kept.append(prior)
                continue
            queue.append((target, prior))
        if limit is not None:
            queue = queue[:max(limit, 0)]
        return queue, kept

    def publish_many(self, queue: Sequence[Tuple[PublishTarget, Optional[PublishResult]]],
                     *, apply: bool, ledger=None,
                     confirm_count: Optional[int] = None,
                     stop_after_failures: int = 3,
                     title_prefix: str = "",
                     progress: Optional[Callable[[int, int, str], None]] = None,
                     push_only: bool = True,
                     others: Sequence[PublishResult] = (),
                     ) -> List[PublishResult]:
        """Process `queue` in order; one package's failure never stops the rest,
        but `stop_after_failures` consecutive ones do - that is a systemic
        problem (auth, the bridge), not a package.

        An apply pushes and opens no pull request; the confirmation counts the
        packages it may push, which leaves out those another release covers."""
        if apply:
            self.preflight(push_only=push_only)
            pushes = len(self.would_push(queue, others))
            if confirm_count != pushes:
                raise PublishError(
                    ErrorCode.INVALID_REQUEST,
                    f"This run would push {pushes} package(s); confirm with "
                    f"--confirm-count {pushes}. Nothing was pushed.",
                )

        results: List[PublishResult] = []
        consecutive = 0
        for index, (target, prior) in enumerate(queue, 1):
            if progress:
                progress(index, len(queue), target.package)
            try:
                result = self.publish(
                    target, apply=apply, title_prefix=title_prefix,
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
