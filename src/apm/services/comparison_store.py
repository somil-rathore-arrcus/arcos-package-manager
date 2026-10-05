"""The headline numbers of each comparison, keyed by the commits they used.

A comparison is expensive, and its result is worth recording - it is what lets
debian/upstream.md state a real backlog ("12 missing, 3 already present")
instead of a raw commit count. But a number is only true for the two commits it
was computed from. So a snapshot is stored with the ARCoS commit, the upstream
repository and the upstream commit, and is handed back only to a resolution that
still names exactly those three. When either side moves, the snapshot simply
stops matching, and nothing stale reaches the dashboard, the mapping, the file
or a pull request.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from ..domain.models import ComparisonResult, ComparisonSnapshot, UpstreamResolution

log = logging.getLogger(__name__)


class ComparisonStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, package: str, release: str) -> Path:
        return self.root / release / f"{package}.json"

    def save(self, result: ComparisonResult) -> Optional[Path]:
        if not (result.arcos_commit and result.upstream_commit):
            return None
        path = self._path(result.package, result.debian_release)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = result.snapshot().model_dump_json(indent=1)
        handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.replace(temp, path)
        except BaseException:
            if os.path.exists(temp):
                os.unlink(temp)
            raise
        return path

    def for_resolution(self, resolution: UpstreamResolution
                       ) -> Optional[ComparisonSnapshot]:
        """The snapshot for exactly this resolution's commits, or None."""
        path = self._path(resolution.package, resolution.debian_release)
        if not path.exists():
            return None
        try:
            snapshot = ComparisonSnapshot.model_validate(json.loads(path.read_text()))
        except (ValueError, OSError) as exc:
            log.warning("unreadable comparison snapshot %s: %s", path, exc)
            return None
        upstream = resolution.upstream_repository.url \
            if resolution.upstream_repository else None
        if not (_same(snapshot.arcos_commit, resolution.arcos_commit)
                and _same(snapshot.upstream_commit, resolution.upstream_commit)
                and snapshot.upstream_repository == upstream
                and snapshot.upstream_ref == resolution.upstream_ref):
            return None
        return snapshot


def _same(a: Optional[str], b: Optional[str]) -> bool:
    """Full SHAs, or a 12-character prefix of one against the other."""
    if not a or not b:
        return False
    short = min(len(a), len(b))
    return short >= 12 and a[:short] == b[:short]
