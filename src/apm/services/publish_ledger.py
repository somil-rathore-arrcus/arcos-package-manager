"""What proposing debian/upstream.md has actually done, per package.

The plan (upstream-md-plan-<release>.json) describes work; this records it. One
entry per package, keyed by name within a release, holding the branch, commit
and pull request that exist because of this tool - so a re-run resumes rather
than repeats, and a failure in one package is written down before the next one
starts.

Rewritten atomically after every package: a run that dies half way leaves a
ledger that is correct for every package it reached.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..domain.enums import PublishStatus
from ..domain.models import PublishResult

log = logging.getLogger(__name__)


def ledger_paths(out_dir: Path, release: str, dry_run: bool = False):
    """The JSON and Markdown files for a release. A dry run gets its own pair so
    it can never overwrite the record of a real one."""
    stem = f"upstream-md-pr-{'dryrun' if dry_run else 'results'}-{release}"
    out_dir = Path(out_dir)
    return out_dir / f"{stem}.json", out_dir / f"{stem}.md"


class PublishLedger:
    def __init__(self, json_path: Path, md_path: Optional[Path] = None,
                 release: str = "") -> None:
        self.json_path = Path(json_path)
        self.md_path = Path(md_path) if md_path else self.json_path.with_suffix(".md")
        self.release = release
        self._entries: Dict[str, PublishResult] = {}
        self._load()

    @classmethod
    def for_release(cls, out_dir: Path, release: str,
                    dry_run: bool = False) -> "PublishLedger":
        json_path, md_path = ledger_paths(out_dir, release, dry_run)
        return cls(json_path, md_path, release)

    # -- reading -----------------------------------------------------------

    def _load(self) -> None:
        if not self.json_path.exists():
            return
        try:
            payload = json.loads(self.json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A ledger that cannot be read must not be silently replaced: it is
            # the only record of which branches and PRs exist.
            raise RuntimeError(
                f"{self.json_path} exists but cannot be read ({exc}). Move it "
                f"aside deliberately before running again."
            ) from exc
        self.release = self.release or payload.get("release", "")
        for item in payload.get("entries", []):
            result = PublishResult.model_validate(item)
            self._entries[result.package] = result

    def get(self, package: str) -> Optional[PublishResult]:
        return self._entries.get(package)

    def entries(self) -> List[PublishResult]:
        return [self._entries[k] for k in sorted(self._entries)]

    # -- writing -----------------------------------------------------------

    def record(self, result: PublishResult) -> None:
        result.updated_at = datetime.now(timezone.utc).replace(microsecond=0)
        self._entries[result.package] = result
        self.save()

    def record_all(self, results: Iterable[PublishResult]) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for result in results:
            result.updated_at = now
            self._entries[result.package] = result
        self.save()

    def save(self) -> None:
        counts: Dict[str, int] = {}
        for entry in self._entries.values():
            counts[entry.status.value] = counts.get(entry.status.value, 0) + 1
        payload = {
            "release": self.release,
            "counts": dict(sorted(counts.items())),
            "entries": [
                e.model_dump(mode="json", exclude={"diff"}) for e in self.entries()
            ],
        }
        _atomic_write(self.json_path, json.dumps(payload, indent=2) + "\n")
        _atomic_write(self.md_path, self.render_markdown())

    def render_markdown(self) -> str:
        lines = [
            f"# debian/upstream.md pull requests - {self.release}",
            "",
            "| Package | Status | Branch | Commit | PR URL | Error |",
            "|---|---|---|---|---|---|",
        ]
        for e in self.entries():
            url = e.pull_request.url if e.pull_request and e.pull_request.url else ""
            error = (e.error or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {e.package} | {e.status.value} | {e.branch} | "
                f"{(e.commit or '')[:12]} | {url} | {error} |"
            )
        return "\n".join(lines) + "\n"


def summary_counts(results: Iterable[PublishResult]) -> Dict[PublishStatus, int]:
    counts: Dict[PublishStatus, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
