"""Decide how important a commit is, from evidence only.

A commit is called critical because it says so - a CVE identifier, a stable
backport request, a Fixes: trailer, a named advisory. It is never called critical
because the subject line sounds alarming, because "fix" and "crash" appear in
ordinary development commits constantly and a heuristic on them would flood the
critical list with noise and bury the commits that matter.

Where there is no evidence the answer is UNKNOWN, which is different from NORMAL:
UNKNOWN means nobody has established anything, NORMAL means the commit carries
routine-change evidence. Neither claims the commit is safe to skip.
"""

from __future__ import annotations

import re
from typing import Iterable, List

from ..domain.enums import Criticality
from ..domain.models import CriticalityAssessment

# CVE-YYYY-NNNN..NNNNNNN
CVE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)

# "Cc: stable@vger.kernel.org", "cc: stable@kernel.org # 6.1"
CC_STABLE = re.compile(r"^\s*cc:\s*.*stable@", re.IGNORECASE | re.MULTILINE)

# "Fixes: abc1234 ("subject")" - a trailer, not the word "fixes" in prose.
FIXES = re.compile(r"^\s*Fixes:\s*([0-9a-f]{7,40})\b", re.IGNORECASE | re.MULTILINE)

# Distribution and project advisory identifiers.
ADVISORY = re.compile(
    r"\b(?:DSA-\d{3,5}-\d+|DLA-\d{3,5}-\d+|USN-\d{3,5}-\d+|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}|RHSA-\d{4}:\d+)\b",
    re.IGNORECASE,
)

# An explicit security statement, not a guess from vocabulary.
SECURITY_TRAILER = re.compile(
    r"^\s*(?:security(?:-fix)?|vulnerability):\s*\S", re.IGNORECASE | re.MULTILINE
)


class CriticalityService:
    """Classify a commit message. Pure and deterministic."""

    def assess(self, subject: str, body: str = "") -> CriticalityAssessment:
        text = f"{subject}\n{body}"
        assessment = CriticalityAssessment()

        cves = _unique(m.group(0).upper() for m in CVE.finditer(text))
        if cves:
            assessment.cve_ids = cves
            assessment.evidence.append(
                "references " + ", ".join(cves)
            )

        advisories = _unique(m.group(0).upper() for m in ADVISORY.finditer(text))
        if advisories:
            assessment.evidence.append(
                "security advisory " + ", ".join(advisories)
            )

        if SECURITY_TRAILER.search(text):
            assessment.evidence.append("carries an explicit security trailer")

        if CC_STABLE.search(text):
            assessment.cc_stable = True
            assessment.evidence.append(
                "Cc: stable - upstream asked for this to be backported"
            )

        fixes = _unique(m.group(1).lower() for m in FIXES.finditer(text))
        if fixes:
            assessment.fixes = fixes
            assessment.evidence.append(
                "Fixes: " + ", ".join(f[:12] for f in fixes)
            )

        assessment.level = self._level(assessment)
        return assessment

    @staticmethod
    def _level(assessment: CriticalityAssessment) -> Criticality:
        # A named vulnerability or advisory is the strongest evidence there is.
        if assessment.cve_ids or any(
            e.startswith("security advisory") or "security trailer" in e
            for e in assessment.evidence
        ):
            return Criticality.CRITICAL
        # Upstream explicitly nominating a commit for stable branches is a
        # direct statement that it belongs in a maintenance release.
        if assessment.cc_stable:
            return Criticality.STABLE_RELEVANT
        # A Fixes: trailer documents that this repairs a specific earlier commit:
        # real evidence of a defect, but not of a security impact.
        if assessment.fixes:
            return Criticality.NORMAL
        return Criticality.UNKNOWN


def _unique(values: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
