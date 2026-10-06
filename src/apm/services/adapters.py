"""Translate the resolver's internal records into the shared domain model.

The mapping pipeline predates the domain model and is deliberately left alone -
it is verified, and its output is checked by invariants. This is the one place
that knows both shapes, so neither leaks into the other.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .. import links
from ..domain.enums import (
    PackageCategory, ResolutionMethod, ResolutionMode, ResolutionStatus,
    ReviewReason, VerificationLevel,
)
from ..domain.models import (
    Package as DomainPackage, PackageSource, Repository, ResolutionEvidence,
    UpstreamCandidate, UpstreamResolution,
)
from ..models import Resolution, Status


def to_domain_package(package, category: Optional[str] = None) -> DomainPackage:
    return DomainPackage(
        name=package.name,
        arcos_repository=package.arcos_repository,
        github_repository=package.github_repository,
        submodule_path=package.submodule_path,
        releases=list(package.releases),
        branches=dict(package.branches),
        pinned_commits=dict(package.commits or {}),
        category=PackageCategory(category) if category else None,
    )


_STATUS = {
    Status.VERIFIED: ResolutionStatus.VERIFIED,
    Status.NEEDS_REVIEW: ResolutionStatus.NEEDS_REVIEW,
    Status.NO_UPSTREAM: ResolutionStatus.NO_UPSTREAM,
    Status.FAILED: ResolutionStatus.FAILED,
    Status.UNRESOLVED: ResolutionStatus.FAILED,
}


def to_domain_resolution(
    resolution: Resolution,
    mode: ResolutionMode = ResolutionMode.AUTO,
    packaging_hosts: Optional[set] = None,
) -> UpstreamResolution:
    debian = None
    if resolution.debian:
        source = resolution.debian
        debian = PackageSource(
            source_package=source.package,
            debian_release=resolution.release,
            version=source.version,
            suite=source.suite,
            directory=source.directory,
            vcs_git=source.vcs_git,
            vcs_branch=source.vcs_branch,
            vcs_browser=source.vcs_browser,
            homepage=source.homepage,
            web_url=links.debian_source_web(resolution.release, source.package),
        )

    upstream_repo = None
    if resolution.upstream and resolution.upstream.repository:
        url = resolution.upstream.repository
        upstream_repo = Repository(
            url=url,
            web_url=links.upstream_web(
                url, resolution.upstream.ref if resolution.upstream else None
            ),
            is_packaging=bool(
                packaging_hosts
                and any(host in url for host in packaging_hosts)
            ),
        )

    return UpstreamResolution(
        package=resolution.package,
        debian_release=resolution.release,
        status=_STATUS.get(resolution.status, ResolutionStatus.FAILED),
        mode=mode,
        method=_method(resolution.method.value),
        category=PackageCategory(resolution.category.value),
        confidence=resolution.confidence.value,
        arcos_repository=resolution.arcos_repository,
        github_repository=resolution.github_repository,
        arcos_branch=resolution.arcos_branch,
        arcos_commit=resolution.arcos_commit,
        arcos_path=resolution.arcos_path,
        arcos_release=resolution.arcos_release,
        debian=debian,
        upstream_repository=upstream_repo,
        upstream_ref=resolution.upstream.ref if resolution.upstream else None,
        upstream_branch=resolution.upstream_branch or None,
        upstream_tag=resolution.upstream_tag or None,
        upstream_commit=resolution.resolved_sha,
        origin_kind=resolution.origin_kind,
        merge_base=resolution.merge_base,
        merge_bases=list(resolution.merge_bases or (
            [resolution.merge_base] if resolution.merge_base else [])),
        behind=resolution.behind,
        arcos_only=resolution.arcos_only,
        counts_basis=resolution.counts_basis if resolution.behind is not None
        or resolution.arcos_only is not None else "",
        upstream_commit_date=resolution.upstream_commit_date,
        verification_level=VerificationLevel(resolution.verification_level),
        review_reasons=[ReviewReason(r) for r in resolution.review_reasons],
        warnings=list(resolution.warnings),
        ref_selection=resolution.ref_selection,
        curated=resolution.curated,
        content_match=resolution.content_match,
        debian_patches=resolution.debian_patches,
        reason=resolution.reason or None,
        evidence_source=resolution.evidence_source,
        evidence_url=resolution.evidence_url,
        verification=resolution.verification,
        evidence=[_evidence(line) for line in resolution.evidence],
        candidates=_candidates(resolution),
        notes=list(resolution.notes),
        resolved_at=datetime.now(timezone.utc),
    )


def _method(value: str) -> ResolutionMethod:
    try:
        return ResolutionMethod(value)
    except ValueError:
        return ResolutionMethod.UNRESOLVED


def _evidence(line: str) -> ResolutionEvidence:
    lowered = line.lower()
    if lowered.startswith("curated"):
        kind = "curated"
    elif lowered.startswith("ancestry"):
        kind = "ancestry"
    elif lowered.startswith("git "):
        kind = "git"
    elif "upstream/metadata" in lowered or lowered.startswith("dep-12"):
        kind = "dep12"
    elif "watch" in lowered:
        kind = "watch"
    elif "homepage" in lowered:
        kind = "homepage"
    elif lowered.startswith("vcs-git"):
        kind = "packaging"
    else:
        kind = "note"
    url = None
    for token in line.replace(",", " ").split():
        if token.startswith("http://") or token.startswith("https://"):
            url = token.rstrip(".;")
            break
    return ResolutionEvidence(kind=kind, detail=line, url=url)


def rejected_candidates(evidence_lines) -> list:
    """The repositories that were looked at and turned down, from the evidence.

    Shared with the mapping store: a resolution served from the cached mapping
    used to show only the winner, which read as "nothing else was considered"
    when in fact the packaging repository had been found, probed and rejected.
    """
    candidates = []
    seen = set()
    for line in evidence_lines or []:
        lowered = line.lower()
        if not (
            "rejected" in lowered or "is not" in lowered or "not upstream" in lowered
        ):
            continue
        url = next((t for t in line.split() if t.startswith("http")), None)
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append(
            UpstreamCandidate(
                repository=url,
                source=ResolutionMethod.UNRESOLVED,
                accepted=False,
                rejected_reason=line,
            )
        )
    return candidates


def _candidates(resolution: Resolution) -> list:
    """What was considered, so a review can see the rejected options.

    The probe's own list when there is one - every ref it measured, with its
    counts - and otherwise a reconstruction from the evidence.
    """
    if resolution.candidates:
        probed = {c.repository for c in resolution.candidates}
        # A repository the probe already measured is not listed a second time
        # from the evidence text.
        return list(resolution.candidates) + [
            c for c in rejected_candidates(resolution.evidence)
            if c.repository not in probed
        ]
    candidates = []
    if resolution.upstream and resolution.upstream.repository:
        candidates.append(
            UpstreamCandidate(
                repository=resolution.upstream.repository,
                ref=resolution.upstream.ref,
                source=_method(resolution.method.value),
                accepted=True,
                shares_history=resolution.merge_base is not None,
            )
        )
    return candidates + rejected_candidates(resolution.evidence)
