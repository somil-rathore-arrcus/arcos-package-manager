"""Generate debian/upstream.md for a whole release, and plan the PRs.

Two things happen here, and they are kept apart on purpose:

  generation   renders the file for every package from the SAME verified
               resolution the mapping and the dashboard use, writes it under
               out/upstream-md/<package>/debian/upstream.md, and says what
               committing it would do to what is already in the fork.

  planning     records the branch, base branch, commit message and diff that a
               later push would use.

Nothing in this module writes to a remote. It reads each fork to compare, and
that is the only network effect it has: the plan is a description of work that
has not been done.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..domain.enums import ResolutionStatus, UpstreamMdOutcome
from ..domain.models import UpstreamResolution
from .upstream_md_publisher import (
    BRANCH_TEMPLATE, branch_for, commit_title, sha256_text,
)
from .upstream_md_service import UPSTREAM_MD_PATH

log = logging.getLogger(__name__)

# One branch per package and release, named after what it carries rather than
# when it ran, so re-running the plan proposes the same branch instead of a
# second one. Shared with the publisher, so the plan names what it will push.
COMMIT_MESSAGE = commit_title("<package>", None)


@dataclass
class PlanEntry:
    package: str
    release: str
    status: str
    outcome: str
    arcos_repository: str
    arcos_branch: str
    branch: str = ""
    commit_message: str = ""
    path: str = UPSTREAM_MD_PATH
    local_path: str = ""
    diff: str = ""
    upstream_repository: str = ""
    upstream_ref: str = ""
    reason: str = ""
    # True when the fork could not be read, so CREATED here means "nothing was
    # found", not "nothing is there".
    existing_unknown: bool = False
    # The exact bytes that were reviewed. The publisher refuses to commit a
    # file that no longer hashes to this.
    sha256: str = ""
    bytes: int = 0

    def as_dict(self) -> dict:
        return {
            "package": self.package,
            "release": self.release,
            "status": self.status,
            "outcome": self.outcome,
            "arcos_repository": self.arcos_repository,
            "base_branch": self.arcos_branch,
            "branch": self.branch,
            "commit_message": self.commit_message,
            "path": self.path,
            "local_path": self.local_path,
            "upstream_repository": self.upstream_repository,
            "upstream_ref": self.upstream_ref,
            "reason": self.reason,
            "existing_unknown": self.existing_unknown,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "diff": self.diff,
        }


@dataclass
class BatchResult:
    release: str
    entries: List[PlanEntry] = field(default_factory=list)
    skipped: List[dict] = field(default_factory=list)

    @property
    def outcomes(self) -> dict:
        counts: dict = {}
        for entry in self.entries:
            counts[entry.outcome] = counts.get(entry.outcome, 0) + 1
        return counts


class MetadataBatchService:
    """Render, compare and plan debian/upstream.md across a release."""

    def __init__(self, upstream_md, workspaces=None,
                 branch_template: str = BRANCH_TEMPLATE,
                 commit_message: str = COMMIT_MESSAGE) -> None:
        self.upstream_md = upstream_md
        self.workspaces = workspaces
        self.branch_template = branch_template
        self.commit_message = commit_message

    # -- reading what is already there -------------------------------------

    def existing_content(self, resolution: UpstreamResolution):
        """The file currently in the fork, or (None, True) if it can't be read.

        The distinction matters: a package whose fork could not be reached has
        an UNKNOWN existing file, and calling that CREATED would claim a file is
        absent on the strength of a failed connection.
        """
        if self.workspaces is None:
            return None, True
        workspace = None
        try:
            workspace = self.workspaces.scratch(
                f"upstreammd-{resolution.package}-{resolution.debian_release}"
            )
            workspace.destroy()
            workspace.ensure()
            # One file at one commit: history is not needed, and on the linux
            # fork fetching it is most of the run.
            head = workspace.fetch_side(
                "arcos", resolution.arcos_repository,
                resolution.arcos_commit or resolution.arcos_branch, depth=1,
            )
            return workspace.read_file(UPSTREAM_MD_PATH, rev=head), False
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            log.warning(
                "could not read %s from %s: %s",
                UPSTREAM_MD_PATH, resolution.arcos_repository, exc,
            )
            return None, True
        finally:
            if workspace is not None:
                workspace.destroy()

    # -- the batch ---------------------------------------------------------

    def run(self, resolutions: List[UpstreamResolution], release: str,
            out_dir: Path, include_needs_review: bool = False,
            read_remote: bool = True, progress=None) -> BatchResult:
        result = BatchResult(release=release)
        wanted = [r for r in resolutions if r.debian_release == release]
        total = len(wanted)

        for index, resolution in enumerate(sorted(wanted, key=lambda r: r.package), 1):
            if progress:
                progress(index, total, resolution.package)

            if resolution.status is ResolutionStatus.VERIFIED:
                pass
            elif (resolution.status is ResolutionStatus.NEEDS_REVIEW
                  and include_needs_review):
                pass
            else:
                # NO_UPSTREAM is a finished answer, not a gap - and a file that
                # recorded an upstream for it would be fiction.
                result.skipped.append({
                    "package": resolution.package,
                    "status": resolution.status.value,
                    "reason": resolution.reason or "",
                })
                continue

            existing, unknown = (
                self.existing_content(resolution) if read_remote else (None, True)
            )
            document = self.upstream_md.generate(resolution, existing=existing)

            target = (
                Path(out_dir) / resolution.package / "debian" / "upstream.md"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(document.content, encoding="utf-8")

            outcome = document.outcome or UpstreamMdOutcome.CREATED
            result.entries.append(PlanEntry(
                package=resolution.package,
                release=release,
                status=resolution.status.value,
                outcome=outcome.value,
                arcos_repository=resolution.github_repository
                or resolution.arcos_repository,
                arcos_branch=resolution.arcos_branch,
                branch=branch_for(resolution.package, release,
                                  self.branch_template),
                commit_message=commit_title(resolution.package, outcome),
                local_path=str(target),
                diff=document.diff or "",
                upstream_repository=(
                    resolution.upstream_repository.url
                    if resolution.upstream_repository else ""
                ),
                upstream_ref=resolution.upstream_ref or "",
                reason=resolution.reason or "",
                existing_unknown=unknown,
                sha256=sha256_text(document.content),
                bytes=len(document.content.encode("utf-8")),
            ))
        return result

    # -- the plan ----------------------------------------------------------

    def write_plan(self, result: BatchResult, out_dir: Path) -> dict:
        """Write the branch/commit plan. Describes work; performs none of it."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"upstream-md-plan-{result.release}.json"
        md_path = out_dir / f"upstream-md-plan-{result.release}.md"

        payload = {
            "release": result.release,
            "path": UPSTREAM_MD_PATH,
            "commit_message": self.commit_message,
            "branch_template": self.branch_template,
            "pushed": False,
            "pull_requests_created": False,
            "note": (
                "A description of work that has NOT been done. No branch was "
                "created, nothing was pushed and no pull request was opened. "
                "What apm publish-upstream-md does is recorded in "
                f"upstream-md-pr-results-{result.release}.json."
            ),
            "outcomes": result.outcomes,
            "entries": [e.as_dict() for e in result.entries],
            "skipped": result.skipped,
        }
        json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

        lines = [
            f"# debian/upstream.md plan - {result.release}",
            "",
            "Nothing here has been done. No branch exists, nothing was pushed "
            "and no pull request was opened.",
            "",
            f"- File: `{UPSTREAM_MD_PATH}`",
            f"- Commit message: `{self.commit_message}`",
            f"- Branch: `{branch_for('<package>', result.release, self.branch_template)}`",
            "",
            "| Package | ARCoS repository | Base branch | Branch | Outcome | Upstream |",
            "|---|---|---|---|---|---|",
        ]
        for entry in sorted(result.entries, key=lambda e: e.package):
            lines.append(
                f"| {entry.package} | {entry.arcos_repository} | "
                f"{entry.arcos_branch} | {entry.branch} | {entry.outcome} | "
                f"{entry.upstream_repository} @ {entry.upstream_ref} |"
            )
        if result.skipped:
            lines += [
                "",
                "## Not generated",
                "",
                "| Package | Status | Reason |",
                "|---|---|---|",
            ]
            for item in sorted(result.skipped, key=lambda s: s["package"]):
                lines.append(
                    f"| {item['package']} | {item['status']} | {item['reason']} |"
                )
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        log.info("wrote %s and %s", json_path, md_path)
        return {"json": json_path, "markdown": md_path}
