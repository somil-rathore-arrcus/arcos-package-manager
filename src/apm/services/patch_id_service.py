"""Recognise a commit that is already present under a different SHA.

A backport is the same change committed again: cherry-picked, rebased, or applied
by hand. Its SHA is necessarily different, so comparing SHAs reports it as
missing and invites someone to apply it a second time.

`git patch-id --stable` hashes the diff itself, ignoring commit metadata, context
line numbers and whitespace-only churn, so the same change hashes the same however
it arrived. That is the signal used here - not subject-line matching, which breaks
on reworded backports and produces false matches between unrelated commits that
happen to share a subject.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from ..gitio.workspace import GitWorkspace

log = logging.getLogger(__name__)


@dataclass
class PatchEquivalence:
    """Which upstream commits already exist on the ARCoS side."""

    upstream_patch_ids: Dict[str, str] = field(default_factory=dict)
    arcos_patch_ids: Dict[str, str] = field(default_factory=dict)
    # upstream sha -> the ARCoS sha carrying the same change
    equivalent: Dict[str, str] = field(default_factory=dict)
    available: bool = True
    reason: Optional[str] = None

    def is_backported(self, sha: str) -> bool:
        return sha in self.equivalent


class PatchIdService:
    def __init__(self, limit: int = 2000, timeout: int = 180,
                 max_total_commits: int = 5000) -> None:
        # Computing patch ids means materialising diffs, so very long ranges are
        # capped. The cap is reported rather than silently applied.
        self.limit = limit
        self.timeout = timeout
        # Beyond this the diffs are not worth the wait; say so instead of
        # spending minutes to find a handful of backports.
        self.max_total_commits = max_total_commits

    def compare(self, workspace: GitWorkspace, merge_base: str,
                upstream_head: str, arcos_head: str,
                total_commits: Optional[int] = None,
                expected_upstream: Optional[int] = None,
                expected_arcos: Optional[int] = None) -> PatchEquivalence:
        result = PatchEquivalence()
        if not merge_base:
            result.available = False
            result.reason = "no common ancestor, so patch equivalence is undefined"
            return result

        if total_commits and total_commits > self.max_total_commits:
            result.available = False
            result.reason = (
                f"{total_commits} commits is beyond the {self.max_total_commits} "
                f"limit for diffing"
            )
            return result

        try:
            result.upstream_patch_ids = workspace.patch_ids(
                f"{merge_base}..{upstream_head}", limit=self.limit,
                timeout=self.timeout,
            )
            result.arcos_patch_ids = workspace.patch_ids(
                f"{merge_base}..{arcos_head}", limit=self.limit,
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            log.warning("patch-id comparison failed: %s", exc)
            result.available = False
            result.reason = f"patch-id could not be computed: {exc}"
            return result

        # A side that has commits but produced no patch id at all did not
        # "find no backports" - it failed to look. Saying so is the difference
        # between a measured zero and an unmeasured one.
        for side, ids, expected in (
            ("upstream", result.upstream_patch_ids, expected_upstream),
            ("ARCoS", result.arcos_patch_ids, expected_arcos),
        ):
            if expected and not ids:
                result.available = False
                result.reason = (
                    f"patch-id produced nothing for the {side} side although it "
                    f"has {expected} commits; the diffs could not be read"
                )
                log.warning("%s", result.reason)
                return result

        if not result.arcos_patch_ids:
            # Nothing on the ARCoS side to have backported into.
            return result

        # Invert the ARCoS side once, then a single lookup per upstream commit.
        by_patch_id: Dict[str, str] = {}
        for sha, patch_id in result.arcos_patch_ids.items():
            by_patch_id.setdefault(patch_id, sha)

        for sha, patch_id in result.upstream_patch_ids.items():
            match = by_patch_id.get(patch_id)
            if match:
                result.equivalent[sha] = match

        log.info(
            "patch-id: %d upstream, %d ARCoS, %d already present",
            len(result.upstream_patch_ids), len(result.arcos_patch_ids),
            len(result.equivalent),
        )
        return result
