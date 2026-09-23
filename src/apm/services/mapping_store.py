"""Serve the already-verified mapping, instead of recomputing it.

The mapping run resolved every package: it fetched Debian metadata, contacted
each candidate upstream and proved ancestry with merge-base. That work is on disk
in out/upstream-mapping.csv. Repeating it because someone picked a package from a
dropdown costs seconds at best and minutes at worst, and returns the same answer.

So the dashboard reads this. Live resolution stays available behind an explicit
refresh, for a package whose mapping is stale or absent.

Two files are combined deliberately:

  config/packages.yaml   repository URLs, branches and pinned commits, at full
                         fidelity - the CSV truncates SHAs to 12 characters for
                         reading, which is not enough to fetch by.
  the mapping CSV        status, upstream, evidence and the proven counts.
"""

from __future__ import annotations

import csv
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .. import links
from ..config import ROOT, load_packages
from ..domain.enums import (
    PackageCategory, ResolutionMethod, ResolutionMode, ResolutionStatus,
)
from ..domain.models import (
    PackageSource, Repository, ResolutionEvidence, UpstreamCandidate,
    UpstreamResolution,
)
from .adapters import rejected_candidates

log = logging.getLogger(__name__)

DEFAULT_PATH = ROOT / "out" / "upstream-mapping.csv"


class MappingStore:
    """Read-only view of the generated mapping, reloaded when the file changes."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or DEFAULT_PATH)
        self._lock = threading.Lock()
        self._rows: Dict[Tuple[str, str], UpstreamResolution] = {}
        self._mtime: Optional[float] = None
        self._packages: Dict[str, object] = {}

    # -- loading -----------------------------------------------------------

    @property
    def available(self) -> bool:
        return self.path.exists()

    def _ensure_loaded(self) -> None:
        if not self.path.exists():
            return
        mtime = self.path.stat().st_mtime
        if self._mtime == mtime and self._rows:
            return
        with self._lock:
            if self._mtime == mtime and self._rows:
                return
            self._packages = {p.name: p for p in load_packages()}
            rows: Dict[Tuple[str, str], UpstreamResolution] = {}
            with self.path.open(encoding="utf-8") as handle:
                for record in csv.DictReader(handle):
                    resolution = self._to_resolution(record)
                    rows[(resolution.package, resolution.debian_release)] = resolution
            self._rows = rows
            self._mtime = mtime
            log.info("loaded %d mapping rows from %s", len(rows), self.path)

    def reload(self) -> None:
        self._mtime = None
        self._ensure_loaded()

    # -- reading -----------------------------------------------------------

    def get(self, package: str, release: str) -> Optional[UpstreamResolution]:
        self._ensure_loaded()
        found = self._rows.get((package, release))
        return found.model_copy(deep=True) if found else None

    def all(self) -> List[UpstreamResolution]:
        self._ensure_loaded()
        return [r.model_copy(deep=True) for r in self._rows.values()]

    def describe(self) -> dict:
        self._ensure_loaded()
        from collections import Counter

        statuses = Counter(r.status.value for r in self._rows.values())
        return {
            "available": self.available,
            "path": str(self.path),
            "rows": len(self._rows),
            "generated_at": (
                datetime.fromtimestamp(self._mtime, tz=timezone.utc).isoformat()
                if self._mtime else None
            ),
            "statuses": dict(statuses),
        }

    # -- conversion --------------------------------------------------------

    def _to_resolution(self, record: dict) -> UpstreamResolution:
        package_name = record["Package"].strip()
        release = record["Debian Release"].strip()
        catalogue = self._packages.get(package_name)

        # Full-fidelity values come from the catalogue; the CSV shortens SHAs.
        arcos_repository = getattr(catalogue, "arcos_repository", "") or ""
        pinned = (getattr(catalogue, "commits", {}) or {}).get(release)
        arcos_commit = pinned or (record.get("ARCoS Commit") or "").strip() or None

        debian = None
        source_package = (record.get("Debian Source Package") or "").strip()
        if source_package:
            debian = PackageSource(
                source_package=source_package,
                debian_release=release,
                version=(record.get("Debian Version") or "").strip(),
                suite=release,
                vcs_git=_or_none(record.get("Vcs-Git (packaging)")),
                vcs_branch=_or_none(record.get("Vcs-Git Branch")),
                vcs_browser=None,
                homepage=_or_none(record.get("Homepage")),
                web_url=_or_none(record.get("Debian Link"))
                or links.debian_source_web(release, source_package),
            )

        upstream_url = _or_none(record.get("Upstream Repository"))
        upstream_ref = _or_none(record.get("Upstream Ref"))
        upstream_repo = None
        if upstream_url:
            upstream_repo = Repository(
                url=upstream_url,
                web_url=_or_none(record.get("Upstream Link"))
                or links.upstream_web(upstream_url, upstream_ref),
                is_packaging=(record.get("Origin Kind") or "").strip()
                == "debian_packaging",
            )

        evidence = [
            ResolutionEvidence(kind=_evidence_kind(line), detail=line,
                               url=_first_url(line))
            for line in (record.get("Evidence") or "").split("\n")
            if line.strip()
        ]
        notes = [
            line.strip() for line in (record.get("Notes") or "").split("\n")
            if line.strip()
        ]

        candidates = []
        if upstream_url:
            candidates.append(
                UpstreamCandidate(
                    repository=upstream_url, ref=upstream_ref,
                    source=_method(record.get("Resolution Method")),
                    accepted=True,
                    shares_history=bool(_or_none(record.get("Merge Base"))),
                )
            )
        # The repositories that were found and turned down belong here too:
        # without them the panel reads as though only one was ever considered.
        candidates += rejected_candidates([e.detail for e in evidence])

        return UpstreamResolution(
            package=package_name,
            debian_release=release,
            status=_status(record.get("Status")),
            mode=(
                ResolutionMode.MANUAL
                if (record.get("Resolution Mode") or "").strip().lower() == "manual"
                else ResolutionMode.AUTO
            ),
            method=_method(record.get("Resolution Method")),
            category=_category(record.get("Category")),
            confidence=(record.get("Confidence") or "none").strip(),
            arcos_repository=arcos_repository,
            github_repository=(record.get("ARCoS Repository") or "").strip(),
            arcos_branch=(record.get("ARCoS Branch") or "").strip(),
            arcos_commit=arcos_commit,
            arcos_path=(record.get("ARCoS Path") or "").strip()
            or getattr(catalogue, "submodule_path", "") or "",
            arcos_release=(record.get("ARCoS Release") or "").strip(),
            debian=debian,
            upstream_repository=upstream_repo,
            upstream_ref=upstream_ref,
            upstream_branch=_or_none(record.get("Upstream Branch")),
            upstream_tag=_or_none(record.get("Upstream Tag")),
            upstream_commit=_or_none(record.get("Upstream Commit")),
            origin_kind=_or_none(record.get("Origin Kind")),
            merge_base=_or_none(record.get("Merge Base")),
            behind=_as_int(record.get("Commits Behind")),
            arcos_only=_as_int(record.get("ARCoS-only Commits")),
            reason=_or_none(record.get("Reason")) or (notes[0] if notes else None),
            evidence_source=(record.get("Evidence Source") or "").strip(),
            evidence_url=(record.get("Evidence URL") or "").strip(),
            verification=(record.get("Verification") or "").strip(),
            evidence=evidence,
            candidates=candidates,
            notes=notes,
            resolved_at=None,
        )


def _or_none(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text or None


def _as_int(value: Optional[str]) -> Optional[int]:
    text = (value or "").strip()
    return int(text) if text.isdigit() else None


def _status(value: Optional[str]) -> ResolutionStatus:
    try:
        return ResolutionStatus((value or "").strip())
    except ValueError:
        return ResolutionStatus.FAILED


def _method(value: Optional[str]) -> ResolutionMethod:
    try:
        return ResolutionMethod((value or "").strip())
    except ValueError:
        return ResolutionMethod.UNRESOLVED


def _category(value: Optional[str]) -> Optional[PackageCategory]:
    try:
        return PackageCategory((value or "").strip())
    except ValueError:
        return None


def _evidence_kind(line: str) -> str:
    lowered = line.lower()
    for prefix, kind in (
        ("curated", "curated"), ("ancestry", "ancestry"), ("git ", "git"),
        ("kernel series", "kernel"), ("arcos ships", "git"),
    ):
        if lowered.startswith(prefix):
            return kind
    if "upstream/metadata" in lowered:
        return "dep12"
    if "watch" in lowered:
        return "watch"
    if "homepage" in lowered:
        return "homepage"
    if lowered.startswith("vcs-git"):
        return "packaging"
    return "note"


def _first_url(line: str) -> Optional[str]:
    for token in line.replace(",", " ").split():
        if token.startswith(("http://", "https://")):
            return token.rstrip(".;)")
    return None
