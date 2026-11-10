"""GitHub, read-only: links a person opens, and lookups of what exists.

This application never creates, opens, submits or merges a pull request. It
pushes a branch and hands back the page a person opens the pull request from
(pull_request_link, the Compare Link). The client below makes GET requests only,
whatever token is configured - there is no method that writes to GitHub, and a
test reads this file to keep it that way.

Reading private repositories works over SSH without a token; only the REST API
needs one, for looking up a pull request or its checks.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import quote, urlencode

import httpx

from ..domain.enums import CiState, ErrorCode, UpstreamMdOutcome
from ..domain.models import (
    CheckRun, CherryPickResult, CiStatus, ComparisonResult, PullRequest,
    UpstreamResolution, ValidationReport,
)
from .upstream_md_service import (
    BRANCH_LABEL, SOURCE_LABEL, UPSTREAM_MD_PATH, UpstreamMdService,
    branch_field, review_flags, source_field,
)

log = logging.getLogger(__name__)

_REPO = re.compile(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", re.IGNORECASE)

# GitHub answers a request line much past 8 KB with an error page instead of the
# form, so a body that would take a link past this is left out of it. Whoever
# opens the link pastes the body from the links file instead.
PR_LINK_MAX_LENGTH = 8000


class GitHubError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


def repo_slug(url: str) -> Optional[str]:
    """owner/name from any spelling of a GitHub URL."""
    match = _REPO.search(url or "")
    return f"{match.group(1)}/{match.group(2)}" if match else None


def pull_request_link(slug: str, base: str, head_branch: str, title: str = "",
                      body: str = "", web_url: str = "https://github.com",
                      max_length: int = PR_LINK_MAX_LENGTH) -> Tuple[str, bool]:
    """GitHub's "open a pull request" page for a pushed branch, pre-filled.

    Needs no token: the link only fills in the form, and the person who opens
    it creates the pull request with their own access. Returns the link and
    whether the body is in it. A body too long for a URL is left out whole
    (False) rather than cut short, so a description is never half there.
    """
    # "/" stays literal in branch names: that is how GitHub spells them in a
    # compare URL, and how verify_pr_ledger.py expects to find them.
    compare = (f"{web_url.rstrip('/')}/{slug}/compare/"
               f"{quote(base, safe='/')}...{quote(head_branch, safe='/')}")
    params = [("quick_pull", "1")]
    if title:
        params.append(("title", title))
    without_body = f"{compare}?{urlencode(params, quote_via=quote)}"
    if not body:
        return without_body, True
    link = f"{compare}?{urlencode(params + [('body', body)], quote_via=quote)}"
    if len(link) > max_length:
        return without_body, False
    return link, True


def bare_pull_request_link(link: Optional[str]) -> str:
    """`link` without its title and body: short enough for a table, and still
    the page the pull request is opened from."""
    if not link:
        return ""
    return link.split("?", 1)[0] + "?quick_pull=1"


class GitHubService:
    def __init__(self, token: str = "", api_url: str = "https://api.github.com",
                 timeout: int = 30, client: Optional[httpx.Client] = None) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @property
    def has_token(self) -> bool:
        """Whether the REST API can be read: pull request and check lookups.
        Never a licence to write - nothing here writes."""
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
        if method != "GET":
            # Nothing in this application writes to GitHub.
            raise GitHubError(
                ErrorCode.PULL_REQUESTS_DISABLED,
                f"Refusing {method} {path}: this application only reads GitHub.",
            )
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
                "A GitHub token is required to read this from the GitHub API. "
                "Set APM_GITHUB_TOKEN (see .env.example).",
            )

    # -- reads -------------------------------------------------------------

    def get_repository(self, slug: str) -> dict:
        """The repository as the token sees it (apm doctor's access check)."""
        self._require_token()
        response = self._request("GET", f"/repos/{slug}")
        if response.status_code == 200:
            return response.json()
        if _rate_limited(response):
            raise _rate_limit_error(response)
        sso = response.headers.get("X-GitHub-SSO", "")
        if response.status_code == 401:
            raise GitHubError(
                ErrorCode.AUTH_REQUIRED,
                "GitHub rejected the token. Check it is valid and not expired.",
            )
        if response.status_code == 403 and sso:
            raise GitHubError(
                ErrorCode.AUTH_REQUIRED,
                f"The token is not SSO-authorised for {slug.split('/')[0]}. "
                f"Authorise it for the organisation, then retry.",
                sso,
            )
        if response.status_code in (403, 404):
            raise GitHubError(
                ErrorCode.NOT_FOUND if response.status_code == 404
                else ErrorCode.AUTH_REQUIRED,
                f"The token cannot see {slug}. For a private repository this "
                f"usually means the token's repository access or scope, not the "
                f"spelling.",
                _message(response),
            )
        raise GitHubError(
            ErrorCode.INTERNAL, f"GitHub: {_message(response)}",
            response.text[:400],
        )

    def find_pull_request(self, slug: str, head_branch: str, base: str,
                          state: str = "open") -> Optional[PullRequest]:
        """A PR for this branch, so retrying does not duplicate it.

        With state="all" a closed or merged PR is returned too: re-proposing
        something a reviewer already closed is a decision for a person.
        """
        owner = slug.split("/")[0]
        response = self._request(
            "GET", f"/repos/{slug}/pulls",
            params={"head": f"{owner}:{head_branch}", "base": base, "state": state},
        )
        if response.status_code == 200:
            items = response.json()
            return _to_pull_request(items[0], already_existed=True) if items else None
        # "Could not ask" is not "there is none": a caller deciding whether a
        # merged or closed pull request already settled a branch must not push
        # past one it could not see.
        _raise_for(response, slug)

    def get_pull_request(self, slug: str, number: int) -> PullRequest:
        self._require_token()
        response = self._request("GET", f"/repos/{slug}/pulls/{number}")
        if response.status_code == 200:
            return _to_pull_request(response.json())
        if response.status_code == 404:
            raise GitHubError(
                ErrorCode.NOT_FOUND, f"No pull request {slug}#{number}."
            )
        _raise_for(response, slug)

    def ci_status(self, slug: str, sha: str) -> CiStatus:
        """The checks GitHub reports for `sha`: check runs and commit statuses.

        Without API access, or when GitHub cannot be asked, this is UNAVAILABLE
        with the reason - never a pass. A commit nothing has reported on yet is
        NONE, which is also not a pass.
        """
        if not self.token:
            return CiStatus(
                state=CiState.UNAVAILABLE, sha=sha,
                reason="Check results are only available through the GitHub "
                       "API, and no API credential is configured "
                       "(APM_GITHUB_TOKEN is empty). Git over SSH cannot read "
                       "them.",
            )
        try:
            runs, run_items = self._all_pages(
                f"/repos/{slug}/commits/{sha}/check-runs", "check_runs")
            statuses, status_items = self._all_pages(
                f"/repos/{slug}/commits/{sha}/status", "statuses")
        except GitHubError as exc:
            return CiStatus(state=CiState.UNAVAILABLE, sha=sha,
                            reason=f"GitHub could not be reached: {exc}")
        for response in (runs, statuses):
            if _rate_limited(response):
                return CiStatus(
                    state=CiState.UNAVAILABLE, sha=sha,
                    reason=f"GitHub's API rate limit was reached "
                           f"({_message(response)}); checks were not read.",
                )
            if response.status_code in (401, 403, 404):
                return CiStatus(
                    state=CiState.UNAVAILABLE, sha=sha,
                    reason=f"The API credential cannot read checks on {slug} "
                           f"(HTTP {response.status_code}: {_message(response)}).",
                )
            if response.status_code >= 400:
                return CiStatus(
                    state=CiState.UNAVAILABLE, sha=sha,
                    reason=f"GitHub answered HTTP {response.status_code}: "
                           f"{_message(response)}",
                )

        if run_items is None or status_items is None:
            # More checks than were read: a failure among the rest must not be
            # summarised as a pass.
            return CiStatus(
                state=CiState.UNAVAILABLE, sha=sha,
                reason=f"More than {_MAX_PAGES * 100} checks are reported on "
                       f"{sha[:12]}; they were not all read.",
            )
        checks = [
            CheckRun(
                name=run.get("name", ""), status=run.get("status", ""),
                conclusion=run.get("conclusion"),
                url=run.get("html_url") or run.get("details_url"),
                summary=((run.get("output") or {}).get("title") or ""),
            )
            for run in run_items
        ]
        for status in status_items:
            state = status.get("state", "")
            checks.append(CheckRun(
                name=status.get("context", ""),
                status="completed" if state in ("success", "failure", "error")
                else "pending",
                conclusion=state if state in ("success", "failure", "error") else None,
                url=status.get("target_url"),
                summary=status.get("description") or "",
            ))
        return CiStatus(state=_ci_state(checks), sha=sha, checks=checks)

    def _all_pages(self, path: str, key: str):
        """(the last response, every item under `key`) - or (response, None)
        when GitHub reports more than _MAX_PAGES pages. Stops at an error
        response, which the caller reports."""
        items: list = []
        for page in range(1, _MAX_PAGES + 1):
            response = self._request("GET", path,
                                     params={"per_page": 100, "page": page})
            if response.status_code != 200:
                return response, []
            payload = response.json()
            batch = payload.get(key, []) or []
            items += batch
            total = payload.get("total_count")
            if not batch or total is None or len(items) >= total:
                return response, items
        return response, None


def build_patch_pr_body(resolution: UpstreamResolution,
                        result: CherryPickResult,
                        comparison: Optional[ComparisonResult] = None,
                        commits: Optional[List] = None,
                        validation: Optional[ValidationReport] = None) -> str:
    """Describe the change so a reviewer can judge it without rerunning the tool.

    ARCoS CI rejects a PR whose body lacks "Problem Description :", "Root
    Cause :" and "Fix Details :", each followed by text, so they come first.
    Validation is reported as it happened: when nothing was built or tested,
    the body says so.
    """
    count = len(result.applied)
    upstream_url = (resolution.upstream_repository.url
                    if resolution.upstream_repository else "")
    merge_base = (comparison.summary.merge_base
                  if comparison and comparison.summary.merge_base
                  else resolution.merge_base or "")[:12]
    base_sha = (result.base_sha or "")[:12]
    validation = validation or result.validation
    lines = [
        "Problem Description :",
        f"The ARCoS fork of {resolution.package} ({result.base_branch}) is "
        f"missing {count} upstream commit(s) from "
        f"{upstream_url or 'its upstream'} ({resolution.upstream_ref}) that were "
        f"selected for backport.",
        "",
        "Root Cause :",
        "The fork diverged from upstream"
        + (f" at {merge_base}" if merge_base else "")
        + "; these upstream changes were made after that point and have not "
        "been brought across.",
        "",
        "Fix Details :",
        f"Cherry-picks the {count} selected upstream commit(s) with "
        f"`git cherry-pick -x`, oldest first, onto a branch created from "
        f"`{result.base_branch}`" + (f" at {base_sha}" if base_sha else "")
        + ". The target branch was not modified, and only the selected commits "
        "are included.",
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
    described = {c.sha: c for c in result.commits}
    upstream_slug = repo_slug(upstream_url)
    for sha in result.applied:
        ref = (f"[`{sha[:12]}`](https://github.com/{upstream_slug}/commit/{sha})"
               if upstream_slug else f"`{sha}`")
        commit = selected.get(sha)
        if commit is None:
            info = described.get(sha)
            subject = f" {info.subject}" if info and info.subject else ""
            lines.append(f"- {ref}{subject} — criticality not assessed here")
            continue
        level = commit.criticality.level.value
        evidence = "; ".join(commit.criticality.evidence)
        entry = f"- {ref} {commit.subject} — **{level}**"
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

    lines += ["", "## Validation", ""]
    if validation is None or not validation.configured:
        lines.append("No validation command is configured, so nothing was built "
                     "or tested by the tool. Review and test before merging.")
    else:
        for run in validation.runs:
            outcome = ("timed out" if run.timed_out else
                       f"exit {run.exit_code}" if run.exit_code is not None
                       else "did not report an exit status")
            lines.append(f"- `{run.command}` — {outcome} ({run.duration_seconds}s)")
        lines.append("")
        lines.append(validation.message)

    lines += [
        "", "---",
        "", "Prepared by the ARCoS Package Manager. Commits were applied with "
        "`git cherry-pick -x` onto a branch created from the target; the target "
        "branch was not modified.",
    ]
    return "\n".join(lines)


def patch_pr_title(package: str, base_branch: str, count: int) -> str:
    return f"{package}: pull {count} upstream commit(s) into {base_branch}"


def build_upstream_md_pr_body(resolution: UpstreamResolution,
                              base_sha: Optional[str] = None,
                              content: Optional[str] = None,
                              outcome: Optional[UpstreamMdOutcome] = None) -> str:
    """The PR description for a debian/upstream.md proposal.

    ARCoS CI (.github/workflows/description.yml) rejects a PR whose body lacks
    "Problem Description :", "Root Cause :" and "Fix Details :", each with some
    text after it - so those three come first, spelled exactly that way.

    The file holds two values; the evidence behind them, and what a reviewer
    should check before merging, are written here instead. `content` is the
    file as committed, rendered from the resolution when not given.
    """
    if content is None:
        content = UpstreamMdService().render(resolution)
    upstream = resolution.upstream_repository
    ref = resolution.upstream_branch or resolution.upstream_tag or \
        resolution.upstream_ref or "N/A"
    commit = (resolution.upstream_commit or "")[:12]
    base = (base_sha or resolution.arcos_commit or "")[:12]
    verb = "Updates" if outcome is UpstreamMdOutcome.UPDATED else "Adds"
    lines = [
        "Problem Description :",
        f"The ARCoS fork of {resolution.package} does not record which upstream "
        f"project and branch it tracks, so comparing it with upstream or "
        f"rebasing it depends on knowledge kept outside the repository.",
        "",
        "Root Cause :",
        "The verified upstream mapping existed only in the ARCoS Package Manager "
        f"output (upstream-mapping-{resolution.debian_release}), not in the "
        "package repository itself.",
        "",
        "Fix Details :",
        f"{verb} {UPSTREAM_MD_PATH}, which records the upstream source and the "
        "current upstream branch, taken from the verified mapping. No other "
        "file changes; debian/rules, debian/control and the build are untouched.",
        "",
        "```text",
        content.rstrip("\n"),
        "```",
        "",
        "| | |",
        "|---|---|",
        f"| {SOURCE_LABEL} | {source_field(resolution)} |",
        f"| {BRANCH_LABEL} | {branch_field(resolution).value} |",
        f"| ARCoS commit | {base or 'N/A'} ({resolution.arcos_branch or 'N/A'}) |",
        f"| Debian release | {resolution.debian_release} |",
        f"| Upstream repository | {upstream.url if upstream else 'N/A'} |",
        f"| Upstream ref | `{ref}`" + (f" @ {commit}" if commit else "") + " |",
        f"| Status / method | {resolution.status.value} / "
        f"{resolution.method.value} ({resolution.confidence}) |",
        f"| Verification level | {resolution.verification_level.value} |",
        f"| Origin kind | {resolution.origin_kind or 'N/A'} |",
    ]
    selection = resolution.ref_selection
    if selection is not None:
        lines.append(
            f"| Ref selection | {selection.strategy.value}: {selection.reason} |"
        )
        if selection.base_tag:
            lines.append(f"| Upstream base tag | `{selection.base_tag}` |")
    if resolution.merge_base:
        lines.append(
            f"| Merge base | {', '.join(m[:12] for m in resolution.merge_bases) or resolution.merge_base[:12]} |"
        )
    if resolution.behind is not None:
        lines.append(
            f"| Raw commit counts | {resolution.behind} upstream commit(s) not "
            f"in ARCoS, "
            f"{resolution.arcos_only if resolution.arcos_only is not None else '?'}"
            f" ARCoS commit(s) not upstream - graph counts, backports not "
            f"excluded, not a list of missing fixes |"
        )
    snapshot = resolution.comparison
    if snapshot is not None:
        lines.append(
            f"| Backlog (comparison) | {snapshot.relevant_upstream} relevant; "
            f"{snapshot.definitely_present + snapshot.probably_present} already "
            f"present; {snapshot.missing} missing; {snapshot.unknown_presence} "
            f"unknown; {snapshot.critical_missing} critical/security missing |"
        )
    # The mapping's own warnings are one of the flags, so they are listed here
    # once rather than in a section of their own as well.
    flags = review_flags(resolution)
    if flags:
        lines += ["", "Review notes :", ""]
        lines += [f"- `{flag.code}`: {flag.detail}" for flag in flags]
    lines += ["", "Generated by the ARCoS Package Manager."]
    return "\n".join(lines)


_FAILED = {"failure", "error", "cancelled", "timed_out", "action_required",
           "startup_failure"}
_PASSED = {"success", "neutral", "skipped"}


def _ci_state(checks: List[CheckRun]) -> CiState:
    """Failure if anything failed, pending while anything runs, success only
    when every check finished and passed; NONE when nothing reported."""
    if not checks:
        return CiState.NONE
    if any((c.conclusion or "") in _FAILED for c in checks):
        return CiState.FAILURE
    if any(c.status != "completed" for c in checks):
        return CiState.PENDING
    if all((c.conclusion or "") in _PASSED for c in checks):
        return CiState.SUCCESS
    return CiState.PENDING


def _to_pull_request(payload: dict, already_existed: bool = False) -> PullRequest:
    return PullRequest(
        number=payload.get("number"),
        url=payload.get("html_url"),
        # GitHub reports a merged PR as "closed"; they mean opposite things to
        # anyone deciding whether to propose it again.
        state="merged" if payload.get("merged_at") else payload.get("state"),
        title=payload.get("title", ""),
        body=payload.get("body") or "",
        base=(payload.get("base") or {}).get("ref", ""),
        head=(payload.get("head") or {}).get("ref", ""),
        head_sha=(payload.get("head") or {}).get("sha"),
        draft=bool(payload.get("draft")),
        already_existed=already_existed,
    )


_MAX_PAGES = 10


def _rate_limited(response: httpx.Response) -> bool:
    """GitHub's quota answer: 429, or 403 with none of the quota left."""
    if response.status_code == 429:
        return True
    return response.status_code == 403 and (
        response.headers.get("X-RateLimit-Remaining") == "0"
        or "rate limit" in _message(response).lower()
    )


def _rate_limit_error(response: httpx.Response) -> GitHubError:
    when = ""
    reset = response.headers.get("X-RateLimit-Reset", "")
    if reset.isdigit():
        from datetime import datetime, timezone
        when = " until " + datetime.fromtimestamp(
            int(reset), tz=timezone.utc).strftime("%H:%M UTC")
    elif response.headers.get("Retry-After", "").isdigit():
        when = f" for {response.headers['Retry-After']}s"
    return GitHubError(
        ErrorCode.RATE_LIMITED,
        f"GitHub's API rate limit is reached{when}. Nothing was changed; "
        f"retry then.",
        _message(response),
    )


def _raise_for(response: httpx.Response, slug: str) -> None:
    """Raise the GitHubError a failed read means: the quota, the token, or a
    repository the token cannot see."""
    if _rate_limited(response):
        raise _rate_limit_error(response)
    if response.status_code == 401:
        raise GitHubError(
            ErrorCode.AUTH_REQUIRED,
            "GitHub rejected the token. Check it is valid and not expired.",
        )
    if response.status_code in (403, 404):
        raise GitHubError(
            ErrorCode.NOT_FOUND if response.status_code == 404
            else ErrorCode.AUTH_REQUIRED,
            f"The token cannot see {slug}. For a private repository this "
            f"usually means the token's repository access or scope, not the "
            f"spelling.",
            _message(response),
        )
    raise GitHubError(
        ErrorCode.INTERNAL, f"GitHub: {_message(response)}", response.text[:400],
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
