"""What Debian changed on top of the upstream release it ships.

Read from the package's own debian/ directory - debian/changelog,
debian/patches/series and the patches themselves - for the version the release
actually ships. It answers three questions a commit graph cannot:

  - which Debian uploads exist for this upstream version, and which were
    security uploads
  - which CVEs Debian says it fixed
  - which quilt patches Debian applies, and which of them are security fixes or
    backports of upstream commits

This is supporting evidence, kept apart from the upstream commit lists: a Debian
patch is packaging, never an upstream source commit, and is never counted as a
missing upstream commit.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from ..domain.models import DebianPatch, DebianPatchReport, DebianUpload
from ..upstream.refs import debian_upstream_version

CVE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
ADVISORY = re.compile(r"\b(?:DSA|DLA)-\d{3,5}-\d+\b", re.IGNORECASE)
_ENTRY = re.compile(
    r"^(?P<source>[a-z0-9][a-z0-9+.\-]*)\s+\((?P<version>[^)]+)\)\s+"
    r"(?P<dist>[^;]+);\s*(?P<options>.*)$",
    re.IGNORECASE,
)
_TRAILER = re.compile(r"^ -- (?P<who>.+?)\s{2}(?P<date>.+)$")
_HEADER = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9-]*):\s*(?P<value>.*)$")
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_COMMIT_URL = re.compile(r"/commit(?:s)?/(?:\?id=)?([0-9a-f]{7,40})\b|[?&]id=([0-9a-f]{7,40})\b")
_CHERRY = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,40})\)")
_DEP3_KEYS = {
    "description", "subject", "origin", "bug", "bug-debian", "bug-ubuntu",
    "forwarded", "author", "from", "applied-upstream", "last-update",
    "reviewed-by", "acked-by", "signed-off-by", "index", "date", "cve",
}


def parse_changelog(text: Optional[str], limit: int = 200) -> List[DebianUpload]:
    """debian/changelog entries, newest first."""
    entries: List[DebianUpload] = []
    if not text:
        return entries
    current: Optional[DebianUpload] = None
    body: List[str] = []
    for line in text.splitlines():
        match = _ENTRY.match(line)
        if match:
            if current is not None:
                _finish(current, body)
                entries.append(current)
                if len(entries) >= limit:
                    return entries
            urgency = re.search(r"urgency=(\S+)", match.group("options"))
            current = DebianUpload(
                version=match.group("version").strip(),
                distribution=match.group("dist").strip(),
                urgency=(urgency.group(1).rstrip(",") if urgency else ""),
            )
            body = []
            continue
        trailer = _TRAILER.match(line)
        if trailer and current is not None:
            current.date = trailer.group("date").strip()
            continue
        if current is not None:
            body.append(line)
    if current is not None:
        _finish(current, body)
        entries.append(current)
    return entries


def _finish(entry: DebianUpload, body: List[str]) -> None:
    text = "\n".join(body)
    entry.cve_ids = _unique(m.group(0).upper() for m in CVE.finditer(text))
    entry.security = bool(
        "security" in entry.distribution.lower()
        or entry.cve_ids
        or ADVISORY.search(text)
        or re.search(r"\bsecurity\b", text, re.IGNORECASE)
    )


def parse_series(text: Optional[str]) -> List[str]:
    """Patch names in apply order. Comments and quilt options are dropped."""
    names = []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        names.append(line.split()[0])
    return names


def parse_patch(name: str, text: str) -> DebianPatch:
    """A DEP-3 (or git format-patch) header, and what it says about the patch."""
    headers: Dict[str, str] = {}
    key = None
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(("---", "diff ", "Index: ", "+++ ")) and index > 0:
            break
        if line.startswith("From ") and index == 0:
            continue
        match = _HEADER.match(line)
        if match and match.group("key").lower() in _DEP3_KEYS:
            key = match.group("key").lower()
            headers.setdefault(key, match.group("value").strip())
            continue
        if key and line.startswith((" ", "\t")):
            headers[key] = (headers[key] + " " + line.strip()).strip()
            continue
        if not line.strip():
            key = None

    subject = headers.get("subject") or headers.get("description") or ""
    subject = re.sub(r"^\[PATCH[^\]]*\]\s*", "", subject).strip()
    origin = headers.get("origin")
    applied = headers.get("applied-upstream")
    everything = f"{name}\n{text or ''}"

    upstream_commit = None
    for field in (origin, applied):
        if not field:
            continue
        url = _COMMIT_URL.search(field)
        if url:
            upstream_commit = url.group(1) or url.group(2)
            break
        if re.search(r"\b(commit|upstream|backport)\b", field, re.IGNORECASE):
            sha = _SHA.search(field)
            if sha:
                upstream_commit = sha.group(0)
                break
    if upstream_commit is None:
        cherry = _CHERRY.search(text or "")
        if cherry:
            upstream_commit = cherry.group(1)

    cves = _unique(m.group(0).upper() for m in CVE.finditer(everything))
    bugs = [v for k, v in headers.items() if k.startswith("bug") and v]
    forwarded = headers.get("forwarded")
    origin_kind = (origin or "").split(",", 1)[0].strip().lower()

    if cves:
        category = "security"
    elif origin_kind in ("upstream", "backport") or applied or upstream_commit:
        category = "upstream_backport"
    elif origin_kind == "vendor" or (forwarded or "").lower().startswith("not-needed"):
        category = "debian_specific"
    else:
        category = "unknown"

    return DebianPatch(
        name=name, subject=subject[:300], origin=origin,
        upstream_commit=upstream_commit, cve_ids=cves, bugs=bugs,
        forwarded=forwarded, category=category,
    )


def build_report(source_package: str, version: str, release: str,
                 artifacts) -> DebianPatchReport:
    """The Debian patch history for the shipped version, from its debian/ tarball."""
    report = DebianPatchReport(
        source_package=source_package, version=version, release=release,
    )
    if artifacts is None or not getattr(artifacts, "fetched", False):
        report.available = False
        report.reason = (getattr(artifacts, "note", None)
                         or "the Debian source was not fetched")
        return report
    report.format = artifacts.source_format

    shipped = debian_upstream_version(version)
    uploads = parse_changelog(artifacts.changelog)
    if not uploads:
        report.reason = "no debian/changelog in the source"
    # The uploads of this upstream version: the Debian revisions ARCoS's base
    # release went through, security updates included.
    report.uploads = [
        u for u in uploads if debian_upstream_version(u.version) == shipped
    ]

    series = parse_series(artifacts.patch_series)
    patches = [parse_patch(name, artifacts.patches.get(name, ""))
               for name in series]
    # A CVE mentioned for a patch in the changelog makes it a security patch
    # even when the patch header forgot to say so.
    changelog_text = artifacts.changelog or ""
    for patch in patches:
        if patch.category == "security":
            continue
        stem = patch.name.rsplit("/", 1)[-1]
        for line in changelog_text.splitlines():
            if stem and stem in line:
                cves = _unique(m.group(0).upper() for m in CVE.finditer(line))
                if cves:
                    patch.cve_ids = _unique(patch.cve_ids + cves)
                    patch.category = "security"
    report.patches = patches
    if (artifacts.source_format or "").startswith("1.0") and not series:
        report.reason = (
            "source format 1.0: Debian's changes are one .diff.gz, not a patch "
            "series, and are not itemised here"
        )
    report.cve_ids = _unique(
        [c for u in report.uploads for c in u.cve_ids]
        + [c for p in patches for c in p.cve_ids]
    )
    return report


def summary_line(report: Optional[DebianPatchReport]) -> str:
    if report is None:
        return ""
    if not report.available:
        return f"not available: {report.reason or 'unknown'}"
    security = len(report.security_patches)
    backports = sum(1 for p in report.patches if p.category == "upstream_backport")
    return (
        f"{len(report.patches)} patch(es): {security} security, {backports} "
        f"upstream backport(s); {len(report.uploads)} upload(s) of this upstream "
        f"version; CVEs: {', '.join(report.cve_ids) or 'none referenced'}"
    )


def _unique(values: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
