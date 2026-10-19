"""Where a branch this tool pushed stands: on the fork, in a pull request, in CI.

Shared by both publishing workflows - the debian/upstream.md branch and a
patch branch - because the question is the same once the branch is pushed.

Two sources, each used only for what it can actually prove:

  git refs    GitHub publishes every pull request's head as refs/pull/<n>/head.
              Listing them over the SSH access the tool already has shows
              whether a pull request was opened from the branch's commit, and
              its number - with no API credential at all. Refs say nothing
              about a pull request's state.
  GitHub API  With a token: the pull request's state, and the checks. Without
              one the checks are reported UNAVAILABLE, with the reason.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from ..domain.enums import CiState, ErrorCode
from ..domain.models import CiStatus, DetectedPullRequest, PublishStatusReport
from .github_service import GitHubError, repo_slug

log = logging.getLogger(__name__)

# Branches this tool creates: debian/upstream.md proposals and patch series.
_OWN_BRANCH = re.compile(r"^(upstream-metadata|upstream)/[A-Za-z0-9._/-]+$")
_PULL_HEAD = re.compile(r"^refs/pull/(\d+)/head$")


class PublishStatusError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


def validate_own_branch(branch: str) -> None:
    """Only branches this tool creates are looked up, so the endpoint cannot be
    used to probe arbitrary refs."""
    if not _OWN_BRANCH.match(branch or "") or ".." in branch:
        raise PublishStatusError(
            ErrorCode.INVALID_REQUEST,
            f"{branch!r} is not a branch this tool creates (upstream-metadata/... "
            f"or upstream/...).",
        )


class PublishStatusService:
    def __init__(self, transports, github, web_url: str = "https://github.com",
                 timeout: int = 60) -> None:
        self.transports = transports
        self.github = github
        self.web_url = web_url.rstrip("/")
        self.timeout = timeout

    def status(self, *, package: str, repository_url: str, branch: str,
               base: Optional[str] = None) -> PublishStatusReport:
        validate_own_branch(branch)
        slug = repo_slug(repository_url)
        if not slug:
            raise PublishStatusError(
                ErrorCode.INVALID_REQUEST,
                f"{repository_url} is not a GitHub repository.",
            )
        report = PublishStatusReport(
            package=package, repository=slug, head_branch=branch,
            base_branch=base or "", api_access=bool(self.github.token),
            ci=CiStatus(state=CiState.NONE),
        )

        heads, pulls = self._refs(repository_url, branch)
        report.head_sha = heads
        report.branch_exists = heads is not None
        if heads is None:
            report.ci = CiStatus(
                state=CiState.NONE,
                reason=f"{branch} is not on {slug}, so there is nothing to check.",
            )
            return report

        report.pull_requests = [
            DetectedPullRequest(number=n, url=f"{self.web_url}/{slug}/pull/{n}")
            for n, sha in sorted(pulls) if sha == heads
        ]
        if self.github.token and base:
            try:
                report.pull_request = self.github.find_pull_request(
                    slug, branch, base, state="all")
            except GitHubError as exc:
                log.info("pull request lookup for %s failed: %s", branch, exc)
        report.ci = self.github.ci_status(slug, heads)
        return report

    def _refs(self, url: str, branch: str):
        """(the branch's commit or None, [(pr number, head commit)])."""
        result = self.transports.for_url(url).run(
            ["ls-remote", url, f"refs/heads/{branch}", "refs/pull/*/head"],
            timeout=self.timeout,
        )
        if not result.ok:
            raise PublishStatusError(
                ErrorCode.TIMEOUT if "timed out" in result.stderr
                else ErrorCode.REPOSITORY_UNAVAILABLE,
                f"Could not list the refs of {repo_slug(url) or url}.",
                result.stderr.strip()[:400],
            )
        head, pulls = None, []
        for line in result.stdout.splitlines():
            sha, _, ref = line.strip().partition("\t")
            if ref == f"refs/heads/{branch}":
                head = sha
                continue
            match = _PULL_HEAD.match(ref)
            if match:
                pulls.append((int(match.group(1)), sha))
        return head, pulls
