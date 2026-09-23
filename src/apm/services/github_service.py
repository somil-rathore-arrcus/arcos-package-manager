"""Opening pull requests, and nothing else automatically.

Every method here is called only from an explicit user action. There is no code
path that opens a pull request as a side effect of resolving, comparing or
generating - a PR is visible to a whole team, and the tool never creates one on
its own initiative.

Reading private repositories works over SSH without a token; only the REST API
needs one, so the absence of a token degrades to "cannot open PRs" rather than
breaking the application.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

import httpx

from ..domain.enums import ErrorCode
from ..domain.models import (
    CherryPickResult, ComparisonResult, PullRequest, UpstreamResolution,
)

log = logging.getLogger(__name__)

_REPO = re.compile(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", re.IGNORECASE)


class GitHubError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


def repo_slug(url: str) -> Optional[str]:
    """owner/name from any spelling of a GitHub URL."""
    match = _REPO.search(url or "")
    return f"{match.group(1)}/{match.group(2)}" if match else None


class GitHubService:
    def __init__(self, token: str = "", api_url: str = "https://api.github.com",
                 timeout: int = 30, client: Optional[httpx.Client] = None) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @property
    def can_create_pull_requests(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.api_url}{path}"
        try:
            if self._client is not None:
                return self._client.request(
                    method, url, headers=self._headers(), **kwargs
                )
            with httpx.Client(timeout=self.timeout) as client:
                return client.request(
                    method, url, headers=self._headers(), **kwargs
                )
        except httpx.HTTPError as exc:
            raise GitHubError(
                ErrorCode.REPOSITORY_UNAVAILABLE,
                f"GitHub request failed: {exc}",
            ) from exc

    def _require_token(self) -> None:
        if not self.token:
            raise GitHubError(
                ErrorCode.AUTH_REQUIRED,
                "A GitHub token is required to open pull requests. Set "
                "APM_GITHUB_TOKEN (see .env.example).",
            )

    # -- reads -------------------------------------------------------------

    def find_pull_request(self, slug: str, head_branch: str,
                          base: str) -> Optional[PullRequest]:
        """An already-open PR for this branch, so retrying does not duplicate it."""
        owner = slug.split("/")[0]
        response = self._request(
            "GET", f"/repos/{slug}/pulls",
            params={"head": f"{owner}:{head_branch}", "base": base, "state": "open"},
        )
        if response.status_code != 200:
            return None
        items = response.json()
        if not items:
            return None
        return _to_pull_request(items[0], already_existed=True)

    def get_pull_request(self, slug: str, number: int) -> PullRequest:
        response = self._request("GET", f"/repos/{slug}/pulls/{number}")
        if response.status_code == 404:
            raise GitHubError(
                ErrorCode.NOT_FOUND, f"No pull request {slug}#{number}."
            )
        if response.status_code >= 400:
            raise GitHubError(
                ErrorCode.INTERNAL, _message(response), response.text[:400]
            )
        return _to_pull_request(response.json())

    # -- writes ------------------------------------------------------------

    def create_pull_request(self, slug: str, head_branch: str, base: str,
                            title: str, body: str,
                            draft: bool = False) -> PullRequest:
        """Open a PR, or return the one that is already open for this branch."""
        self._require_token()

        existing = self.find_pull_request(slug, head_branch, base)
        if existing:
            log.info("pull request already open for %s: %s", head_branch,
                     existing.url)
            return existing

        response = self._request(
            "POST", f"/repos/{slug}/pulls",
            json={
                "title": title, "body": body, "head": head_branch,
                "base": base, "draft": draft,
            },
        )
        if response.status_code == 401:
            raise GitHubError(
                ErrorCode.AUTH_REQUIRED,
                "GitHub rejected the token. Check it is valid and, if the "
                "organisation uses SAML, that it is SSO-authorised.",
            )
        if response.status_code == 403:
            raise GitHubError(
                ErrorCode.AUTH_REQUIRED,
                f"The token is not permitted to open pull requests on {slug}.",
                _message(response),
            )
        if response.status_code == 404:
            raise GitHubError(
                ErrorCode.NOT_FOUND,
                f"{slug} was not found. For a private repository this is what "
                f"an unauthorised token looks like, so check the token's scope "
                f"before the spelling.",
            )
        if response.status_code >= 400:
            raise GitHubError(
                ErrorCode.INTERNAL,
                f"GitHub refused the pull request: {_message(response)}",
                response.text[:400],
            )
        return _to_pull_request(response.json())


def build_patch_pr_body(resolution: UpstreamResolution,
                        result: CherryPickResult,
                        comparison: Optional[ComparisonResult] = None,
                        commits: Optional[List] = None) -> str:
    """Describe the change so a reviewer can judge it without rerunning the tool."""
    lines = [
        f"Pulls {len(result.applied)} upstream commit(s) into "
        f"`{result.base_branch}`.",
        "",
        "| | |",
        "|---|---|",
        f"| Package | `{resolution.package}` |",
        f"| Debian release | {resolution.debian_release} |",
        f"| Target branch | `{result.base_branch}` |",
        f"| Upstream repository | {resolution.upstream_repository.url if resolution.upstream_repository else 'N/A'} |",
        f"| Upstream ref | `{resolution.upstream_ref}` |",
        f"| Resolution | {resolution.status.value} via {resolution.method.value} |",
    ]
    if comparison and comparison.summary.merge_base:
        lines.append(f"| Merge base | `{comparison.summary.merge_base[:12]}` |")
    lines += ["", "## Commits", ""]

    selected = {c.sha: c for c in (commits or [])}
    for sha in result.applied:
        commit = selected.get(sha)
        if commit is None:
            lines.append(f"- `{sha[:12]}`")
            continue
        level = commit.criticality.level.value
        evidence = "; ".join(commit.criticality.evidence)
        entry = f"- `{sha[:12]}` {commit.subject} — **{level}**"
        if evidence:
            entry += f" ({evidence})"
        lines.append(entry)

    critical = [
        c for c in (commits or [])
        if c.sha in set(result.applied) and c.criticality.level.value == "CRITICAL"
    ]
    if critical:
        lines += [
            "", f"{len(critical)} of these carry security evidence (CVE or "
            f"advisory).",
        ]

    lines += [
        "", "---",
        "", "Prepared by the ARCoS Package Manager. Commits were applied with "
        "`git cherry-pick -x` onto a branch created from the target; the target "
        "branch was not modified.",
    ]
    return "\n".join(lines)


def build_upstream_md_pr_body(resolution: UpstreamResolution) -> str:
    upstream = resolution.upstream_repository
    return "\n".join([
        f"Records the verified upstream for `{resolution.package}` in "
        f"`debian/upstream.md`.",
        "",
        "| | |",
        "|---|---|",
        f"| Debian release | {resolution.debian_release} |",
        f"| Upstream repository | {upstream.url if upstream else 'N/A'} |",
        f"| Upstream ref | `{resolution.upstream_ref}` |",
        f"| Status | {resolution.status.value} |",
        f"| Method | {resolution.method.value} |",
        f"| Origin kind | {resolution.origin_kind or 'N/A'} |",
        "",
        "The file is generated from the resolution the tool verified, so the "
        "committed record and the dashboard cannot drift apart.",
    ])


def _to_pull_request(payload: dict, already_existed: bool = False) -> PullRequest:
    return PullRequest(
        number=payload.get("number"),
        url=payload.get("html_url"),
        state=payload.get("state"),
        title=payload.get("title", ""),
        body=payload.get("body") or "",
        base=(payload.get("base") or {}).get("ref", ""),
        head=(payload.get("head") or {}).get("ref", ""),
        draft=bool(payload.get("draft")),
        already_existed=already_existed,
    )


def _message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    parts = [payload.get("message", f"HTTP {response.status_code}")]
    for error in payload.get("errors", []) or []:
        if isinstance(error, dict) and error.get("message"):
            parts.append(error["message"])
    return " - ".join(parts)
