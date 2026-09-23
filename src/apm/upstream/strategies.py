"""The ordered chain that decides where a package's upstream is.

Each strategy either produces a candidate with the evidence for it, or declines
and says why. The first candidate wins, but every rejection is kept, because
"salsa was found and rejected as packaging" is the answer to the question people
actually ask when a package comes back unresolved.

Vcs-Git is never a candidate. It is the Debian PACKAGING repository - the
history of the debian/ directory - not the project's own code. Treating it as
upstream is the specific error that made lldpd and net-snmp look resolved while
pointing at the wrong tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import Confidence, DebianSource, Method, Upstream
from .forge import ForgeRepo, is_packaging_host, to_repo
from .kernel import series_for_version


@dataclass
class Candidate:
    upstream: Upstream
    method: Method
    confidence: Confidence
    evidence: str
    # What was read to produce this candidate, and where that can be read
    # again. Reported as its own column so a reviewer can go and check.
    source: str = ""
    source_url: str = ""


@dataclass
class ChainResult:
    candidate: Optional[Candidate]
    evidence: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    # True when a curated entry states there is provably nothing to track, as
    # opposed to nothing having been found.
    no_upstream: bool = False


_CONFIDENCE = {
    "high": Confidence.HIGH,
    "medium": Confidence.MEDIUM,
    "low": Confidence.LOW,
}


def resolve(
    package_name: str,
    release: str,
    source: Optional[DebianSource],
    artifacts,
    settings,
    override: Optional[dict] = None,
) -> ChainResult:
    result = ChainResult(candidate=None)
    forge_hosts = settings.forge_hosts
    packaging_hosts = settings.packaging_hosts

    # 1. Curated. A human verified this; nothing downstream may override it.
    if override:
        if override.get("no_upstream"):
            reason = override.get("reason", "recorded as having no upstream")
            result.evidence.append(f"curated: no upstream exists - {reason}")
            result.no_upstream = True
            return result
        repo = override.get("repository")
        ref = override.get("ref") or override.get("branch")
        if repo:
            reason = override.get("reason", "hand-verified mapping")
            confidence = _CONFIDENCE.get(
                str(override.get("confidence", "high")).lower(), Confidence.HIGH
            )
            result.candidate = Candidate(
                Upstream(repository=repo, ref=ref),
                Method.CURATED,
                confidence,
                f"curated: {reason}",
                source="config/overrides.yaml (hand-verified)",
                source_url=repo,
            )
            result.evidence.append(f"curated override -> {repo} @ {ref or 'HEAD'}")
            return result

    # 2. The kernel, by stable series derived from the Debian version.
    kernel = settings.kernel
    if package_name == kernel.get("package") and source is not None:
        series = series_for_version(source.version)
        if series:
            expected = (kernel.get("expected_series") or {}).get(release)
            note = f"derived from Debian {source.version}"
            if expected and expected != series:
                note += f"; WARNING expected {expected} for {release}"
            result.candidate = Candidate(
                Upstream(repository=kernel["upstream_repository"], ref=series),
                Method.KERNEL_SERIES,
                Confidence.HIGH if series == expected else Confidence.MEDIUM,
                f"kernel series {series} {note}",
                source=f"Debian linux version {source.version}",
                source_url=kernel["upstream_repository"],
            )
            result.evidence.append(result.candidate.evidence)
            return result

    if source is None:
        result.evidence.append("no Debian source package for this release")
        return result

    # Record the packaging repo for reference, and make explicit that it is not
    # a candidate.
    if source.vcs_git:
        if is_packaging_host(source.vcs_git, packaging_hosts):
            result.rejected.append(
                f"Vcs-Git {source.vcs_git} is Debian packaging, not upstream"
            )
        else:
            result.rejected.append(f"Vcs-Git {source.vcs_git} recorded, not upstream")

    # 3. DEP-12 Repository: the one field Debian defines for true upstream.
    dep12_repo = artifacts.dep12_repository if artifacts else None
    if dep12_repo:
        repo = to_repo(dep12_repo, forge_hosts)
        if repo and not is_packaging_host(repo.url, packaging_hosts):
            result.candidate = Candidate(
                Upstream(repository=repo.url),
                Method.DEP12,
                Confidence.HIGH,
                f"debian/upstream/metadata Repository: {dep12_repo}",
                source="debian/upstream/metadata (DEP-12) Repository:",
                source_url=dep12_repo,
            )
            result.evidence.append(result.candidate.evidence)
            return result
        result.rejected.append(
            f"DEP-12 Repository {dep12_repo} is not a usable upstream forge URL"
        )

    # 4. debian/watch, but only when it names a forge. A watch line pointing at
    #    a tarball directory says where releases are published, not where the
    #    code lives, and cannot be cloned.
    from ..debian.dep12 import watch_urls

    for url in watch_urls(artifacts.watch if artifacts else None):
        repo = to_repo(url, forge_hosts)
        if repo and not is_packaging_host(repo.url, packaging_hosts):
            result.candidate = Candidate(
                Upstream(repository=repo.url),
                Method.WATCH_FORGE,
                Confidence.MEDIUM,
                f"debian/watch points at {url}",
                source="debian/watch",
                source_url=url,
            )
            result.evidence.append(result.candidate.evidence)
            return result
        result.rejected.append(f"debian/watch {url} is not a git forge URL")

    # 5. Homepage, last and least. Often right, sometimes a project's blog, so it
    #    is reported as medium confidence for review, never as settled fact.
    if source.homepage:
        repo = to_repo(source.homepage, forge_hosts)
        if repo and not is_packaging_host(repo.url, packaging_hosts):
            result.candidate = Candidate(
                Upstream(repository=repo.url),
                Method.HOMEPAGE_FORGE,
                Confidence.MEDIUM,
                f"Homepage: {source.homepage}",
                source="Debian Sources Homepage:",
                source_url=source.homepage,
            )
            result.evidence.append(result.candidate.evidence)
            return result
        result.rejected.append(
            f"Homepage {source.homepage} is not a git forge URL"
        )

    result.evidence.append("no DEP-12, watch or homepage yielded a git forge")
    return result
