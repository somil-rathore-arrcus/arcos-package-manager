"""Security evidence from outside the commit message.

A commit message that names a CVE is one signal. Others are stronger and do not
depend on how a commit was written:

  debian     Debian's own patches and changelog for the shipped version: a
             quilt patch carrying a CVE, and the upstream commit it came from
  osv        OSV.dev: vulnerabilities affecting the upstream commit ARCoS is
             based on, with the upstream commits that fix them

Each provider returns findings; the comparison then marks any missing upstream
commit that a finding names as a fix CRITICAL, with the source recorded. A
provider that could not answer says so - "unavailable" is never reported as "no
vulnerabilities" - and the absence of any finding leaves a commit UNKNOWN, not
safe.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import httpx

from ..domain.enums import Presence
from ..domain.models import DebianPatchReport, SecurityFinding

log = logging.getLogger(__name__)


@dataclass
class SecurityContext:
    package: str
    release: str
    upstream_repository: str
    # Upstream commits ARCoS is based on (merge bases, or the approved base).
    base_commits: List[str] = field(default_factory=list)
    missing: Sequence[str] = ()
    present: Sequence[str] = ()
    debian_patches: Optional[DebianPatchReport] = None
    # Whether ARCoS reaches a commit; None when it cannot be determined.
    contains: Optional[Callable[[str], Optional[bool]]] = None
    # Whether the upstream ref reaches a commit; None when it cannot be told. A
    # long comparison lists only its most recent missing commits, so a fix ARCoS
    # lacks can be missing without being in `missing`.
    upstream_contains: Optional[Callable[[str], Optional[bool]]] = None


@dataclass
class ProviderAnswer:
    name: str
    findings: List[SecurityFinding] = field(default_factory=list)
    available: bool = True
    detail: str = ""

    @property
    def status_line(self) -> str:
        if not self.available:
            return f"{self.name}: unavailable ({self.detail})"
        return f"{self.name}: {len(self.findings)} finding(s)" + (
            f" ({self.detail})" if self.detail else "")


def _status(fixes: Sequence[str], context: SecurityContext) -> str:
    if not fixes:
        return "unknown"
    states = []
    for fix in fixes:
        fix = fix.lower()
        if len(fix) < 7:
            states.append("unknown")
        elif any(m.startswith(fix) for m in context.missing):
            states.append("missing")
        elif any(p.startswith(fix) for p in context.present):
            states.append("present")
        elif context.contains is not None:
            reached = context.contains(fix)
            if reached is None:
                states.append("unknown")
            elif reached:
                states.append("present")
            elif context.upstream_contains is None:
                states.append("not_in_range")
            else:
                upstream = context.upstream_contains(fix)
                states.append("missing" if upstream else
                              "not_in_range" if upstream is False else "unknown")
        else:
            states.append("unknown")
    for state in ("missing", "unknown", "not_in_range", "present"):
        if state in states:
            return state
    return "unknown"


class DebianSecurityProvider:
    """Findings from Debian's patches and changelog for the shipped version."""

    name = "debian"

    def answer(self, context: SecurityContext) -> ProviderAnswer:
        report = context.debian_patches
        out = ProviderAnswer(self.name)
        if report is None or not report.available:
            out.available = False
            out.detail = (report.reason if report else "no Debian source") or "unavailable"
            return out
        seen = set()
        for patch in report.patches:
            for cve in patch.cve_ids:
                fixes = [patch.upstream_commit] if patch.upstream_commit else []
                status = _status(fixes, context) if fixes else {
                    Presence.DEFINITELY_PRESENT: "present",
                    Presence.PROBABLY_PRESENT: "present",
                    Presence.MISSING: "missing",
                }.get(patch.presence, "unknown")
                out.findings.append(SecurityFinding(
                    identifier=cve, sources=["debian-patch"],
                    summary=f"Debian patch {patch.name}: {patch.subject}".strip(),
                    fix_commits=fixes, status=status,
                    url=f"https://security-tracker.debian.org/tracker/{cve}",
                ))
                seen.add(cve)
        for upload in report.uploads:
            for cve in upload.cve_ids:
                if cve in seen:
                    continue
                seen.add(cve)
                out.findings.append(SecurityFinding(
                    identifier=cve, sources=["debian-changelog"],
                    summary=f"named in the Debian {upload.version} upload "
                            f"({upload.distribution})",
                    status="unknown",
                    url=f"https://security-tracker.debian.org/tracker/{cve}",
                ))
        return out


# A fixing commit's web page: GitHub's /commit/<sha>, GitLab's /-/commit/<sha>
# and cgit's /commit/?id=<sha>, with the repository before it.
_COMMIT_URL = re.compile(
    r"^(?P<repo>\S+?)(?:/-)?/commit/(?:\?id=)?(?P<sha>[0-9a-fA-F]{7,40})(?![0-9a-fA-F])"
)


def _osv_fixes(vuln: dict, repo: str, context: SecurityContext) -> List[str]:
    """The commits OSV says fix `vuln` in this upstream.

    A range or FIX reference for another repository - a mirror, or mainline
    under a stable tree - counts only when git shows the upstream ref has that
    commit. Otherwise it fixes some other project and says nothing here.
    """
    candidates = []
    for affected in vuln.get("affected", []) or []:
        for rng in affected.get("ranges", []) or []:
            if rng.get("type") != "GIT":
                continue
            same = not repo or _normalise_repo(rng.get("repo", "")) == repo
            candidates += [(e["fixed"], same) for e in rng.get("events", []) or []
                           if e.get("fixed")]
    for ref in vuln.get("references", []) or []:
        if ref.get("type") != "FIX":
            continue
        match = _COMMIT_URL.search(ref.get("url", ""))
        if match:
            same = not repo or _normalise_repo(match.group("repo")) == repo
            candidates.append((match.group("sha"), same))
    fixes = []
    for sha, same in candidates:
        if same or (context.upstream_contains is not None
                    and context.upstream_contains(sha.lower())):
            fixes.append(sha)
    return list(dict.fromkeys(fixes))


class OsvSecurityProvider:
    """Vulnerabilities OSV.dev knows affect the upstream base commit."""

    name = "osv"

    def __init__(self, api_url: str = "https://api.osv.dev/v1",
                 cache_dir: Optional[Path] = None, ttl: int = 86400,
                 timeout: int = 20, max_vulns: int = 100, max_pages: int = 10,
                 client: Optional[httpx.Client] = None) -> None:
        self.api_url = api_url.rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.ttl = ttl
        self.timeout = timeout
        self.max_vulns = max_vulns
        self.max_pages = max_pages
        self._client = client

    def _post(self, path: str, body: dict) -> dict:
        key = hashlib.sha256(
            (path + json.dumps(body, sort_keys=True)).encode()).hexdigest()[:32]
        cached = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cached and cached.exists() and time.time() - cached.stat().st_mtime < self.ttl:
            return json.loads(cached.read_text())
        url = f"{self.api_url}{path}"
        if self._client is not None:
            response = self._client.post(url, json=body)
        else:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=body)
        response.raise_for_status()
        payload = response.json()
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(payload))
        return payload

    def answer(self, context: SecurityContext) -> ProviderAnswer:
        out = ProviderAnswer(self.name)
        if not context.base_commits:
            out.available = False
            out.detail = "no upstream base commit to ask about"
            return out
        repo = _normalise_repo(context.upstream_repository)
        vulns: Dict[str, dict] = {}
        unread = False
        try:
            for commit in context.base_commits[:3]:
                unread = self._query(commit, vulns) or unread
        except (httpx.HTTPError, ValueError) as exc:
            out.available = False
            out.detail = f"{type(exc).__name__}: {exc}"[:200]
            return out
        listed = list(vulns.items())
        for vuln_id, vuln in listed[: self.max_vulns]:
            fixes = _osv_fixes(vuln, repo, context)
            aliases = [a for a in vuln.get("aliases", []) or [] if a != vuln_id]
            out.findings.append(SecurityFinding(
                identifier=vuln_id, aliases=aliases, sources=["osv"],
                summary=(vuln.get("summary") or vuln.get("details") or "")[:300],
                fix_commits=fixes, status=_status(fixes, context),
                url=f"https://osv.dev/vulnerability/{vuln_id}",
            ))
        notes = [f"queried {min(len(context.base_commits), 3)} base commit(s)"]
        if len(listed) > self.max_vulns:
            notes.append(f"showing {self.max_vulns} of {len(listed)} "
                         f"vulnerabilities")
        if unread:
            notes.append(f"OSV had more than the {self.max_pages} page(s) read "
                         f"per commit; the rest are not shown")
        out.detail = "; ".join(notes)
        return out

    def _query(self, commit: str, vulns: Dict[str, dict]) -> bool:
        """OSV's answer for `commit`, page by page, into `vulns`. True when
        OSV still had more after max_pages - a partial answer must say so."""
        body = {"commit": commit}
        for _ in range(self.max_pages):
            payload = self._post("/query", body)
            for vuln in payload.get("vulns", []) or []:
                vulns.setdefault(vuln.get("id", ""), vuln)
            token = payload.get("next_page_token")
            if not token:
                return False
            body = {"commit": commit, "page_token": token}
        return True


class SecurityService:
    def __init__(self, providers: Sequence = ()) -> None:
        self.providers = list(providers)

    def assess(self, context: SecurityContext) -> List[ProviderAnswer]:
        answers = []
        for provider in self.providers:
            try:
                answers.append(provider.answer(context))
            except Exception as exc:  # noqa: BLE001 - one source must not sink the rest
                log.warning("security provider %s failed: %s", provider.name, exc)
                answers.append(ProviderAnswer(provider.name, available=False,
                                              detail=str(exc)[:200]))
        return answers


def _normalise_repo(url: str) -> str:
    text = (url or "").strip().lower()
    text = re.sub(r"^(git\+)?(https?|ssh|git)://(git@)?", "", text)
    text = text.replace("git@github.com:", "github.com/")
    return text[:-4] if text.endswith(".git") else text.rstrip("/")
