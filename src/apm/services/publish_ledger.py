"""What proposing debian/upstream.md has actually done, per package.

The plan (upstream-md-plan-<release>.json) describes work; this records it. One
entry per package, keyed by name within a release, holding the branch, commit
and pull request that exist because of this tool - so a re-run resumes rather
than repeats, and a failure in one package is written down before the next one
starts.

Rewritten atomically after every package: a run that dies half way leaves a
ledger that is correct for every package it reached.

The record of a branch a push-only run pushed is never replaced by a result
that only says what a later run did (see supersedes): that branch can wait days
for a person, and losing its commit would make the tool's own branch look like
someone else's. Every other record is replaced by the next result, as it
always was.

A push-only run's entries are BRANCH_PUSHED. The JSON keeps the full link to
open each pull request (pr_create_url); the Markdown table shows the page
without the pre-filled title and description, which belong in the links file
rather than in a table cell.
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
from .github_service import bare_pull_request_link

log = logging.getLogger(__name__)


# A pull request exists for the branch: opened or found by this tool, merged, or
# closed by a reviewer.
_PULL_REQUEST_RECORDS = (
    PublishStatus.PR_OPENED, PublishStatus.PR_EXISTS, PublishStatus.PR_MERGED,
    PublishStatus.PR_CLOSED,
)


def supersedes(result: PublishResult,
               previous: Optional[PublishResult]) -> bool:
    """Whether `result` may replace `previous` as a package's record.

    Only a BRANCH_PUSHED record with its commit is guarded. It gives way to a
    pull request answer, to a result that itself carries a pushed commit, or to
    NO_CHANGE - the branch is gone and the target already has the file.
    Anything else (DRIFT from an upstream move, an exclusion, an error, a
    package no longer eligible) describes one run, not what exists, so the
    record stands and a later run still recognises the branch as its own
    instead of reporting it as BRANCH_EXISTS.

    Every other record - a pull request's or PR_FAILED's included - gives way
    to the next result, as it did before push-only existed: a run that never
    meets a BRANCH_PUSHED record writes exactly what it always wrote.
    """
    if previous is None or previous.status is not PublishStatus.BRANCH_PUSHED:
        return True
    if not (previous.pushed and previous.commit):
        return True
    return (
        result.status in _PULL_REQUEST_RECORDS
        or (result.pushed and bool(result.commit))
        or result.status is PublishStatus.NO_CHANGE
    )


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

    def record(self, result: PublishResult) -> bool:
        """Write `result` down, unless the existing record outranks it (see
        supersedes). Returns whether it was written."""
        return self.record_all([result]) == 1

    def record_all(self, results: Iterable[PublishResult]) -> int:
        """record() for many, saved once. Returns how many were written."""
        now = datetime.now(timezone.utc).replace(microsecond=0)
        written = 0
        for result in results:
            previous = self._entries.get(result.package)
            if not supersedes(result, previous):
                log.info("%s: keeping the recorded %s over this run's %s",
                         result.package, previous.status.value,
                         result.status.value)
                continue
            result.updated_at = now
            self._entries[result.package] = result
            written += 1
        self.save()
        return written

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
            f"# debian/upstream.md publishing records - {self.release}",
            "",
            "No pull request is opened by this tool; each is opened by a person "
            "from its Compare Link.",
            "",
            "| Package | Status | Branch | Commit | Compare Link | PR URL | Error |",
            "|---|---|---|---|---|---|---|",
        ]
        for e in self.entries():
            if e.pull_request and e.pull_request.url:
                url = e.pull_request.url
            elif e.status is PublishStatus.BRANCH_PUSHED:
                url = bare_pull_request_link(e.pr_create_url)
            else:
                url = ""
            error = (e.error or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {e.package} | {e.status.value} | {e.branch} | "
                f"{(e.commit or '')[:12]} | {e.compare_url or ''} | {url} | {error} |"
            )
        return "\n".join(lines) + "\n"


def all_records(out_dir: Path) -> List[PublishResult]:
    """Every release's recorded results (never a dry run's), so a branch one
    release or package pushed is recognised as the tool's own from another."""
    prefix = "upstream-md-pr-results-"
    records: List[PublishResult] = []
    for path in sorted(Path(out_dir).glob(f"{prefix}*.json")):
        release = path.stem[len(prefix):]
        records.extend(PublishLedger.for_release(out_dir, release).entries())
    return records


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
