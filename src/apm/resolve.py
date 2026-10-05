"""Resolve every package, for every release it ships in.

One Resolution per (package, release). A package absent from a release's
manifest produces no row for that release at all - eight packages ship in
bookworm but not trixie, and inventing rows for them would be fiction.

What a row claims is kept in three separate places, because they answer
different questions:

  status              VERIFIED / NEEDS_REVIEW / NO_UPSTREAM / FAILED
  verification level  how strongly the ARCoS-upstream relationship is proven
  confidence          how sure the candidate identification is

A repository that answers ls-remote is REF_EXISTS - it exists. Only shared git
history (or a content match a person approved) makes a row VERIFIED, and the ref
it is verified against is chosen for the release (see apm.upstream.refs), not
whatever the remote's default branch happens to be.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List, Optional, Sequence

from .cache import HttpCache
from .classify import classify
from .debian.dep12 import fetch_artifacts
from .debian.patches import build_report, summary_line
from .debian.sources_index import SourcesIndex, load_index
from .domain.enums import (
    RefStrategy, ResolutionMethod, ReviewReason, VerificationLevel,
)
from .domain.models import CuratedUpstream, RefSelection, UpstreamCandidate
from .gitio.ancestry import AncestryChecker, ProbeError, first_shared_repository
from .gitio.verify import Verifier
from .models import Category, Confidence, Method, Resolution, Status, Upstream
from .upstream.forge import is_packaging_host
from .upstream.refs import (
    DEVELOPMENT_BRANCHES, ProbedRef, RefCandidateSpec, debian_upstream_version,
    packaging_candidates, select_ref, series_of, upstream_candidates,
)
from .upstream.strategies import resolve as resolve_chain

log = logging.getLogger(__name__)

# Reasons that keep a row from VERIFIED even when history is shared.
BLOCKING = {
    ReviewReason.CURATED_CONFLICT.value, ReviewReason.INVALID_REF.value,
    ReviewReason.NO_SHARED_HISTORY.value, ReviewReason.ANCESTRY_NOT_PROBED.value,
    ReviewReason.CONTENT_MATCH_UNAPPROVED.value, ReviewReason.NO_CANDIDATE.value,
}

COUNTS_BASIS = (
    "raw commit-graph counts, head-based: git rev-list --count --no-merges "
    "ARCOS..UPSTREAM (upstream commits the ARCoS commit cannot reach) and "
    "UPSTREAM..ARCOS. Already-backported commits are NOT excluded"
)


class Resolver:
    def __init__(self, settings, overrides: dict, cache: HttpCache,
                 verifier: Verifier,
                 ancestry: Optional[AncestryChecker] = None,
                 content_base=None, approvals=None) -> None:
        self.settings = settings
        self.overrides = overrides or {}
        self.cache = cache
        self.verifier = verifier
        self.ancestry = ancestry
        # Optional: content-based base detection for a fork that shares no
        # history, and the approvals a person gave for one.
        self.content_base = content_base
        self.approvals = approvals
        self._indices = {}

    def index(self, release: str) -> SourcesIndex:
        if release not in self._indices:
            self._indices[release] = load_index(
                release, self.settings.archive, self.cache
            )
        return self._indices[release]

    def _override_for(self, package: str, release: str) -> dict:
        """Merge the package-wide override with any release-specific block."""
        entry = dict(self.overrides.get(package) or {})
        per_release = entry.pop("releases", {}) or {}
        entry.update(per_release.get(release) or {})
        return entry

    def debian_source(self, package_name: str, release: str):
        """The Debian source stanza for a package, honouring overrides."""
        override = self._override_for(package_name, release)
        source_name = override.get("debian_source_package", package_name)
        return self.index(release).get(source_name) if source_name else None

    def resolve_one(self, package, release: str) -> Resolution:
        override = self._override_for(package.name, release)
        branch = package.branch_for(release)
        pinned = (package.commits or {}).get(release)

        resolution = Resolution(
            package=package.name,
            release=release,
            category=Category.ARRCUS_NATIVE,
            status=Status.NEEDS_REVIEW,
            method=Method.UNRESOLVED,
            confidence=Confidence.NONE,
            arcos_repository=package.arcos_repository,
            github_repository=package.github_repository,
            arcos_branch=branch,
            arcos_path=package.submodule_path,
            # The ARCoS release is the manifest branch this package list came
            # from - aminor for bookworm - which is not always the branch the
            # package itself tracks (ONL-xc tracks aminor-xc).
            arcos_release=self.settings.manifest.get("branches", {}).get(
                release, ""
            ),
            counts_basis=COUNTS_BASIS,
        )

        # Which Debian source package to look up. Several ARCoS names differ from
        # Debian's (lttng-tools ships as ltt-control), so an override may rename
        # it, and an explicit null means "deliberately not a Debian package".
        source_name = override.get("debian_source_package", package.name)
        source = self.index(release).get(source_name) if source_name else None
        resolution.debian = source
        if source_name and source_name != package.name and source is not None:
            resolution.record(
                f"Debian source package is '{source_name}', not '{package.name}'"
            )

        artifacts = None
        if source is not None:
            artifacts = fetch_artifacts(
                source, self.settings.archive.get("mirror", ""), self.cache
            )
            if artifacts.note:
                resolution.record(f"debian artifacts: {artifacts.note}")
            resolution.debian_patches = build_report(
                source.package, source.version, release, artifacts
            )
            resolution.record(
                f"debian patches ({source.version}): "
                f"{summary_line(resolution.debian_patches)}"
            )

        chain = resolve_chain(
            package.name, release, source, artifacts, self.settings, override or None
        )
        for line in chain.evidence:
            resolution.record(line)
        for line in chain.rejected:
            resolution.record(line)

        candidate = chain.candidate
        if candidate is None:
            resolution.category = classify(
                package.name, source, has_upstream=False, settings=self.settings,
                curated=bool(override),
            )
            if chain.no_upstream:
                # Curated statement that nothing exists to track. That is a
                # finished answer, not a gap.
                resolution.category = _category_override(
                    override, resolution.category
                )
                resolution.status = Status.NO_UPSTREAM
                resolution.method = Method.CURATED
                resolution.confidence = Confidence.HIGH
                resolution.curated = CuratedUpstream(
                    reason=_one_line(override.get("reason", "no upstream exists"))
                )
                resolution.note(override.get("reason", "no upstream exists"))
                self._verify_arcos(resolution, pinned)
                return resolution
            if resolution.category not in (
                Category.DEBIAN_UPSTREAM, Category.DEBIAN_NO_UPSTREAM,
                Category.THIRD_PARTY,
            ):
                resolution.status = Status.NO_UPSTREAM
                resolution.method = Method.NOT_APPLICABLE
                resolution.note(
                    "No upstream exists for this package; it is "
                    f"{resolution.category.value.replace('_', ' ')}."
                )
                self._verify_arcos(resolution, pinned)
                return resolution
            # A package that should have an upstream but whose metadata named
            # none is work for a person, so it is NEEDS_REVIEW with the reason
            # attached rather than a silent gap.
            resolution.flag(
                ReviewReason.NO_CANDIDATE,
                "No DEP-12, debian/watch or Homepage field named a git forge "
                "for this package, so no upstream candidate could be proposed "
                "from Debian metadata. It needs a curated mapping in "
                "config/overrides.yaml.",
            )
            # Nothing in the metadata named an upstream, but the fork may still
            # descend from the Debian packaging repository - that is exactly
            # where several ARCoS forks come from. Ask the history.
            self._check_ancestry(resolution, source, artifacts, pinned, override)
            self._set_origin_kind(resolution)
            self._verify_arcos(resolution, pinned)
            self._finalise(resolution)
            return resolution

        resolution.upstream = candidate.upstream
        resolution.method = candidate.method
        resolution.confidence = candidate.confidence
        resolution.evidence_source = candidate.source
        resolution.evidence_url = candidate.source_url
        resolution.category = _category_override(
            override,
            classify(
                package.name, source, has_upstream=True, settings=self.settings,
                curated=candidate.method == Method.CURATED,
            ),
        )
        if candidate.method == Method.CURATED:
            resolution.curated = CuratedUpstream(
                repository=candidate.upstream.repository,
                ref=candidate.upstream.ref,
                reason=_one_line(override.get("reason", "")),
                replacement_allowed=self._replacement_allowed(override),
            )

        # Contacted, not assumed: an upstream nobody contacted is a guess.
        check = self.verifier.resolve_ref(
            candidate.upstream.repository, candidate.upstream.ref
        )
        if not check.reachable:
            resolution.flag(
                ReviewReason.NETWORK_ERROR,
                f"{candidate.upstream.repository} could not be reached, so the "
                f"resolution could not be completed ({check.error}). This is a "
                f"failure to look, not a finding about the package.",
            )
            resolution.record(f"git: repository unreachable ({check.error})")
            self._set_origin_kind(resolution)
            self._verify_arcos(resolution, pinned)
            self._finalise(resolution)
            return resolution
        if not check.ref_exists:
            resolution.flag(
                ReviewReason.INVALID_REF,
                f"{candidate.upstream.repository} exists but has no ref "
                f"'{candidate.upstream.ref}'.",
            )
            resolution.record(
                f"git: repository reachable but ref "
                f"'{candidate.upstream.ref}' does not exist"
            )
        else:
            resolution.verification_level = VerificationLevel.REF_EXISTS.value
            resolution.resolved_sha = check.sha
            resolution.verification = (
                f"git ls-remote resolved {candidate.upstream.repository} "
                f"{check.resolved_ref or candidate.upstream.ref} to a commit "
                f"(the repository exists; that alone is not proof of origin)"
            )
            if candidate.upstream.ref is None:
                resolution.upstream = Upstream(
                    repository=candidate.upstream.repository,
                    ref=check.resolved_ref,
                )
                resolution.record(
                    f"git: no ref configured; the upstream default branch is "
                    f"'{check.resolved_ref}' (a fallback, not a release ref)"
                )
            resolution.upstream.is_tag = check.ref_kind == "tag"
            resolution.record(
                f"git verified {candidate.upstream.repository} @ "
                f"{resolution.upstream.ref} -> {check.sha[:12]}"
            )

        self._check_ancestry(resolution, source, artifacts, pinned, override)
        self._set_origin_kind(resolution)
        self._verify_arcos(resolution, pinned)
        self._finalise(resolution)
        return resolution

    # -- status ------------------------------------------------------------

    def _finalise(self, resolution: Resolution) -> None:
        """Derive status from what was established. The one place it is set
        for a row that has an upstream to find."""
        reasons = set(resolution.review_reasons)
        if any(ReviewReason(r).is_error for r in reasons):
            resolution.status = Status.FAILED
        elif (VerificationLevel(resolution.verification_level).proves_relationship
              and not reasons & BLOCKING):
            resolution.status = Status.VERIFIED
        else:
            resolution.status = Status.NEEDS_REVIEW
        if resolution.status is not Status.VERIFIED and resolution.confidence \
                is Confidence.HIGH and not VerificationLevel(
                    resolution.verification_level).proves_relationship:
            # A curated "high" says the human was sure of the repository; it
            # does not stand in for the proof that is missing.
            resolution.confidence = Confidence.MEDIUM

    def _replacement_allowed(self, override: dict) -> bool:
        if "allow_ancestry_replacement" in (override or {}):
            return bool(override.get("allow_ancestry_replacement"))
        return bool(self.settings.ancestry.get("allow_curated_replacement", False))

    # -- origin --------------------------------------------------------------

    def _set_origin_kind(self, resolution) -> None:
        if not (resolution.upstream and resolution.upstream.repository):
            return
        resolution.origin_kind = (
            "debian_packaging"
            if is_packaging_host(
                resolution.upstream.repository, self.settings.packaging_hosts
            )
            else "project"
        )

    def _release_number(self, release: str) -> str:
        for item in self.settings.releases:
            if item.id == release:
                return str(item.version)
        return ""

    def _ancestry_candidates(self, resolution, source, artifacts,
                             override) -> List[RefCandidateSpec]:
        """Refs the fork might have come from, most release-appropriate first.

        The project's repository comes first - its release tag, its maintenance
        branch, then any configured ref - and the Debian packaging repository
        after it, because ARCoS forked the packaging repository for several
        packages and excluding it is what makes those look like they have no
        origin. A default branch is only ever a labelled fallback.
        """
        specs: List[RefCandidateSpec] = []
        upstream_version = debian_upstream_version(source.version) if source else ""
        primary = resolution.upstream.repository if resolution.upstream else None
        if primary:
            listing = self.verifier.list_refs(primary)
            if not listing.ok:
                resolution.record(
                    f"git: could not list the refs of {primary} ({listing.error}); "
                    f"only the configured ref can be probed"
                )
            if resolution.method == Method.CURATED:
                strategy = RefStrategy.CURATED
            elif resolution.method == Method.KERNEL_SERIES:
                strategy = RefStrategy.KERNEL_SERIES
            else:
                strategy = RefStrategy.DEFAULT_BRANCH
            names = [resolution.package, source.package if source else "",
                     _repo_name(primary)]
            specs += upstream_candidates(
                primary, listing, upstream_version,
                artifacts.watch if artifacts else None, names,
                resolution.upstream.ref, strategy,
                pin=bool((override or {}).get("pin_ref")),
            )
            if upstream_version and not any(
                    s.strategy is RefStrategy.EXACT_TAG for s in specs):
                resolution.record(
                    f"refs: no tag in {primary} names Debian's upstream version "
                    f"{upstream_version}"
                )

        vcs = source.vcs_git if source else None
        primary_only = set(self.settings.ancestry.get("primary_only", []))
        if vcs and vcs != primary and resolution.package not in primary_only:
            listing = self.verifier.list_refs(vcs)
            specs += packaging_candidates(
                vcs, listing, source.version, source.codename or resolution.release,
                self._release_number(resolution.release), source.vcs_branch,
                self.settings.ancestry.get("packaging_refs", []),
            )

        seen, unique = set(), []
        for spec in specs:
            if spec.key not in seen:
                seen.add(spec.key)
                unique.append(spec)
        limit = int(self.settings.ancestry.get("max_candidates", 12))
        return unique[:limit]

    def _check_ancestry(self, resolution, source, artifacts, pinned,
                        override) -> None:
        """Measure the fork against every candidate ref and let history decide."""
        if self.ancestry is None or not self.ancestry.enabled:
            resolution.flag(
                ReviewReason.ANCESTRY_NOT_PROBED,
                "The ancestry probe was not run, so the relationship between "
                "the fork and this upstream is unproven.",
            )
            return
        if resolution.package in set(self.settings.ancestry.get("skip", [])):
            resolution.flag(
                ReviewReason.ANCESTRY_NOT_PROBED,
                "The ancestry probe is skipped for this package in "
                "config/settings.yaml, so shared history was not proven.",
            )
            resolution.record("ancestry probe skipped for this package (configured)")
            return

        specs = self._ancestry_candidates(resolution, source, artifacts, override)
        if not specs:
            return

        arcos_ref = pinned or resolution.arcos_branch
        arcos_sha = pinned
        if not arcos_sha and resolution.arcos_repository:
            tip = self.verifier.branch_tip(
                resolution.arcos_repository, resolution.arcos_branch
            )
            arcos_sha = tip.sha if tip.ref_exists else None
        try:
            data = self.ancestry.check(
                resolution.package, resolution.release,
                resolution.arcos_repository, arcos_ref,
                [(s.repository, s.ref, s.sha) for s in specs],
                arcos_sha=arcos_sha,
            )
        except ProbeError as exc:
            reason = {
                "ARCOS_UNREACHABLE": ReviewReason.ARCOS_UNREACHABLE,
                "NETWORK_ERROR": ReviewReason.NETWORK_ERROR,
                "INVALID_REF": ReviewReason.ARCOS_UNREACHABLE,
            }.get(exc.kind, ReviewReason.PROBE_FAILED)
            resolution.flag(
                reason,
                f"The ancestry probe could not complete ({exc}). The relationship "
                f"is unmeasured - this is not a finding of no shared history.",
            )
            resolution.record(f"ancestry: probe failed ({exc.kind}): {exc}")
            return

        probed = _join(specs, data)
        resolution.candidates = [_candidate_model(p) for p in probed]
        errors = [p for p in probed if p.error and p.error_kind != "INVALID_REF"]
        for p in probed:
            if p.error:
                resolution.record(
                    f"ancestry: {p.spec.repository} @ {p.spec.ref}: "
                    f"{p.error_kind}: {p.error}"
                )

        winner = first_shared_repository(data)
        if winner is None:
            if errors:
                # Something could not be measured; "no shared history" would be
                # a claim the probe did not establish.
                resolution.flag(
                    ReviewReason.NETWORK_ERROR
                    if any(p.error_kind == "NETWORK_ERROR" for p in errors)
                    else ReviewReason.PROBE_FAILED,
                    f"{len(errors)} candidate ref(s) could not be measured "
                    f"({errors[0].spec.repository} @ {errors[0].spec.ref}: "
                    f"{errors[0].error}); shared history is undetermined.",
                )
                return
            tried = ", ".join(f"{p.spec.repository} @ {p.spec.ref}" for p in probed)
            resolution.record(
                f"ancestry: NO candidate shares history with the fork (tried {tried})"
            )
            resolution.flag(
                ReviewReason.NO_SHARED_HISTORY,
                "The ARCoS fork has no commit in common with any candidate "
                "upstream, so it was imported rather than forked. No commit "
                "count is reported; a content comparison can propose a base "
                "release for a person to approve.",
            )
            self._content_base(resolution, source, specs, pinned)
            return

        if resolution.curated and resolution.curated.repository \
                and winner != resolution.curated.repository:
            choice = select_ref(probed, winner, _pairs(data))
            ref = choice.target.spec.ref if choice else "?"
            resolution.curated.conflicting_repository = winner
            resolution.curated.conflicting_ref = ref
            if not resolution.curated.replacement_allowed:
                resolution.curated.conflict = (
                    f"config/overrides.yaml names {resolution.curated.repository}, "
                    f"which shares no history with the fork; {winner} @ {ref} "
                    f"does. The curated mapping was not replaced."
                )
                resolution.flag(
                    ReviewReason.CURATED_CONFLICT,
                    f"Curated upstream {resolution.curated.repository} shares no "
                    f"history with the ARCoS fork, but {winner} @ {ref} does. A "
                    f"person decides: update config/overrides.yaml, or set "
                    f"allow_ancestry_replacement: true for this package.",
                )
                resolution.record(
                    f"ancestry: curated conflict - {winner} @ {ref} shares "
                    f"history, the curated {resolution.curated.repository} does not"
                )
                return
            resolution.record(
                f"ancestry: curated {resolution.curated.repository} replaced by "
                f"{winner}, as allow_ancestry_replacement permits"
            )

        choice = select_ref(probed, winner, _pairs(data))
        target, base = choice.target, choice.base
        # History found an origin the metadata did not name.
        resolution.clear(ReviewReason.NO_CANDIDATE, "No DEP-12, debian/watch")
        current = (
            resolution.upstream.repository if resolution.upstream else None,
            resolution.upstream.ref if resolution.upstream else None,
        )
        if winner != current[0]:
            resolution.record(
                f"ancestry: the fork descends from {winner} @ {target.spec.ref}, "
                f"not {current[0]} @ {current[1]}; corrected"
            )
            resolution.method = Method.ANCESTRY
            resolution.evidence_source = "git merge-base against the ARCoS fork"
            resolution.evidence_url = winner
        elif target.spec.ref != current[1]:
            resolution.record(
                f"refs: compared against {target.spec.ref} "
                f"({target.spec.strategy.value}) rather than {current[1]}: "
                f"{choice.reason}"
            )
        resolution.upstream = Upstream(
            repository=winner, ref=target.spec.ref, is_tag=target.spec.kind == "tag"
        )
        for p in probed:
            if p.spec.key == target.spec.key:
                resolution.candidates[probed.index(p)].accepted = True

        upstream_version = debian_upstream_version(source.version) if source else ""
        resolution.ref_selection = RefSelection(
            ref=target.spec.ref, kind=target.spec.kind,
            strategy=target.spec.strategy, sha=target.sha, reason=choice.reason,
            debian_upstream_version=upstream_version or None,
            series=series_of(upstream_version) if upstream_version else None,
            base_tag=base.spec.ref if base else None,
            base_sha=base.sha if base else None,
            arcos_contains_base=base.in_arcos if base else None,
            is_fallback=target.spec.strategy.is_fallback,
        )
        resolution.resolved_sha = target.sha
        resolution.upstream_commit_date = target.date
        resolution.merge_bases = list(target.merge_bases)
        resolution.merge_base = target.merge_bases[0] if target.merge_bases else None
        resolution.behind = target.behind
        resolution.arcos_only = target.arcos_only
        resolution.confidence = Confidence.HIGH
        resolution.verification_level = VerificationLevel.SHARED_HISTORY.value
        mb_text = ", ".join(m[:12] for m in target.merge_bases)
        resolution.verification = (
            f"git merge-base: the fork shares history with {winner} @ "
            f"{target.spec.ref} (merge base {mb_text}; {len(probed)} candidate "
            f"ref(s) probed)"
        )
        resolution.record(
            f"ancestry verified: {target.spec.ref} @ {str(target.sha)[:12]}, "
            f"merge base(s) {mb_text}; {target.behind} upstream commit(s) not in "
            f"ARCoS, {target.arcos_only} ARCoS commit(s) not upstream (raw counts)"
        )
        if len(target.merge_bases) > 1:
            resolution.record(
                f"ancestry: {len(target.merge_bases)} merge bases (criss-cross "
                f"history); counts are head-based, not from any one merge base"
            )
        self._plausibility(resolution, choice, source)

    # -- content -------------------------------------------------------------

    def _content_base(self, resolution, source, specs, pinned) -> None:
        """No shared history: find the closest release by content, for review."""
        if not (resolution.upstream and resolution.upstream.repository):
            return
        if self.content_base is None:
            resolution.record(
                "content: base detection is not configured for this run"
            )
            return
        repository = resolution.upstream.repository
        listing = self.verifier.list_refs(repository)
        approval = self._approval(resolution.package, resolution.release)
        targets = [s.ref for s in specs if s.repository == repository
                   and s.kind == "branch"]
        try:
            match = self.content_base.detect(
                package=resolution.package, release=resolution.release,
                arcos_url=resolution.arcos_repository,
                arcos_ref=pinned or resolution.arcos_branch,
                upstream_url=repository,
                tags=listing.tags if listing.ok else {},
                upstream_version=(debian_upstream_version(source.version)
                                  if source else ""),
                names=[resolution.package, source.package if source else "",
                       _repo_name(repository)],
                source=source,
                target_refs=targets,
                approved=approval,
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            log.warning("content base detection failed for %s: %s",
                        resolution.package, exc)
            resolution.record(f"content: base detection failed: {exc}")
            return
        resolution.content_match = match
        if match.base_tag:
            resolution.record(
                f"content: closest upstream release by tree comparison is "
                f"{match.base_tag} ({match.files_differing} of "
                f"{match.files_compared} files differ, score {match.score:.3f})"
            )
        if not match.approved:
            if match.base_tag:
                resolution.flag(
                    ReviewReason.CONTENT_MATCH_UNAPPROVED,
                    f"The closest release by content is {match.base_tag}. It is "
                    f"not used until a person approves it (apm "
                    f"approve-content-base).",
                )
            return

        resolution.verification_level = VerificationLevel.CONTENT_MATCH_APPROVED.value
        # An approved content match answers the question NO_SHARED_HISTORY
        # raised; it no longer blocks.
        resolution.review_reasons = [
            r for r in resolution.review_reasons
            if r != ReviewReason.NO_SHARED_HISTORY.value
        ]
        target_ref = match_target(match)
        target_sha = listing.sha_of(target_ref) if listing.ok else None
        resolution.upstream = Upstream(
            repository=repository, ref=target_ref,
            is_tag=target_ref == match.base_tag,
        )
        resolution.resolved_sha = target_sha or match.base_sha
        resolution.ref_selection = RefSelection(
            ref=target_ref, kind="tag" if target_ref == match.base_tag else "branch",
            strategy=RefStrategy.EXACT_TAG if target_ref == match.base_tag
            else RefStrategy.MAINTENANCE_BRANCH,
            sha=resolution.resolved_sha,
            reason=f"approved content match: ARCoS corresponds to {match.base_tag}",
            debian_upstream_version=(debian_upstream_version(source.version)
                                     if source else None),
            base_tag=match.base_tag, base_sha=match.base_sha,
        )
        resolution.merge_base = None
        resolution.merge_bases = []
        synthetic = match.notes and next(
            (n for n in match.notes if n.startswith("synthetic-counts:")), None
        )
        resolution.behind, resolution.arcos_only = _synthetic_counts(synthetic)
        resolution.counts_basis = (
            f"SYNTHESIZED from an approved content match, not from shared "
            f"history: upstream commits {match.base_tag}..{target_ref}, and "
            f"ARCoS commits after the matched tree {str(match.arcos_tree)[:12]}"
        )
        resolution.verification = (
            f"content match approved by {match.verified_by} on "
            f"{match.verified_at}: {match.method}, base {match.base_tag}, score "
            f"{match.score:.3f} (no shared git history)"
        )
        resolution.confidence = Confidence.MEDIUM
        resolution.warn(
            "Ancestry is synthesized from a content match; the fork shares no "
            "git history with upstream."
        )

    def _approval(self, package: str, release: str) -> Optional[dict]:
        override = self._override_for(package, release)
        if override.get("content_base"):
            record = dict(override["content_base"])
            record.setdefault("approval_source", "config/overrides.yaml")
            return record
        if self.approvals is not None:
            return self.approvals.get(package, release)
        return None

    # -- plausibility ------------------------------------------------------

    def _plausibility(self, resolution, choice, source) -> None:
        """Warn about results that are possible but suspicious. Never rejects."""
        limits = self.settings.raw.get("plausibility", {}) or {}
        many_behind = int(limits.get("behind_warning", 1000))
        many_local = int(limits.get("arcos_only_warning", 1000))
        max_age = float(limits.get("merge_base_age_years", 4))
        target = choice.target
        strategy = target.spec.strategy
        release_ref = strategy not in (
            RefStrategy.DEFAULT_BRANCH, RefStrategy.PACKAGING_FALLBACK,
            RefStrategy.CURATED, RefStrategy.MANUAL,
        )
        if target.behind is not None and target.behind >= many_behind:
            resolution.warn(
                f"{target.behind} upstream commits on {target.spec.ref} are not "
                f"in ARCoS"
                + (f" - unusually many for a {strategy.value.replace('_', ' ')}"
                   if release_ref else "")
                + "; check that the fork really tracks this series."
            )
        if target.arcos_only is not None and target.arcos_only >= many_local:
            resolution.warn(
                f"{target.arcos_only} ARCoS commits are not in {target.spec.ref}; "
                f"the fork may be based on a different series, or carry "
                f"imported history."
            )
        age = _years_between(target.merge_base_date, target.date)
        if age is not None and age >= max_age:
            resolution.warn(
                f"The merge base is {age:.1f} years older than {target.spec.ref}'s "
                f"tip; the fork diverged a long time ago."
            )
        upstream_version = debian_upstream_version(source.version) if source else ""
        want = series_of(upstream_version) if upstream_version else None
        named = _series_in_name(target.spec.ref)
        if want and named and named != want:
            resolution.warn(
                f"Release mismatch: {target.spec.ref} names series {named}, but "
                f"Debian ships {upstream_version} (series {want})."
            )
        base = choice.base
        if base is not None and base.in_arcos is False:
            resolution.warn(
                f"ARCoS does not contain {base.spec.ref}, the upstream tag for "
                f"Debian's {upstream_version}: {base.behind} commit(s) of that "
                f"release are not in the fork, so it is based on an older "
                f"release or a different line."
            )
        if upstream_version and target.spec.ref in DEVELOPMENT_BRANCHES:
            resolution.warn(
                f"Compared against development branch {target.spec.ref} although "
                f"Debian ships a stable release ({upstream_version}); no release "
                f"tag or maintenance branch shared history with the fork."
            )
        if resolution.curated and resolution.curated.ref and \
                resolution.curated.ref != target.spec.ref and \
                resolution.curated.repository == target.spec.repository:
            resolution.record(
                f"refs: curated ref '{resolution.curated.ref}' kept as a "
                f"candidate; the release-appropriate {target.spec.ref} was "
                f"selected (pin_ref: true in config/overrides.yaml keeps the "
                f"curated ref)"
            )

    # -- ARCoS ---------------------------------------------------------------

    def _verify_arcos(self, resolution: Resolution, pinned: Optional[str]) -> None:
        """Record what ARCoS actually ships, and whether the branch agrees.

        The pinned commit from the release manifest is the answer: it is what the
        release builds. The branch tip is reported only as context, because the
        two genuinely diverge - the trixie manifest names branch `aminor` while
        pinning a commit that branch does not contain.
        """
        if pinned:
            resolution.arcos_commit = pinned
            resolution.record(
                f"ARCoS ships {pinned[:12]} (pinned by the {resolution.release} "
                f"release manifest)"
            )
        if not resolution.arcos_repository:
            return

        check = self.verifier.branch_tip(
            resolution.arcos_repository, resolution.arcos_branch
        )
        if not check.reachable:
            resolution.record(f"ARCoS fork unreachable ({check.error})")
            return
        if not check.ref_exists:
            resolution.record(
                f"ARCoS fork has no branch '{resolution.arcos_branch}'; the "
                f"manifest's branch field is stale"
            )
            return
        if not pinned:
            resolution.arcos_commit = check.sha
            resolution.record(
                f"no pinned commit in the manifest; using branch "
                f"'{resolution.arcos_branch}' tip {check.sha[:12]}"
            )
        elif check.sha != pinned:
            resolution.note(
                f"Branch '{resolution.arcos_branch}' has moved to "
                f"{check.sha[:12]}; the release still pins {pinned[:12]}."
            )

    def resolve_all(self, packages: list, releases: Optional[list] = None) -> list:
        wanted = releases or self.settings.release_ids
        rows = []
        for package in packages:
            ships_in = [r for r in wanted if r in (package.releases or wanted)]
            for release in ships_in:
                log.info("resolving %s/%s", package.name, release)
                rows.append(self.resolve_one(package, release))
        return rows


def match_target(match) -> str:
    """After approval: the series branch containing the base tag, else the tag."""
    for note in match.notes or []:
        if note.startswith("target:"):
            return note.split(":", 1)[1].strip()
    return match.base_tag


def _synthetic_counts(note: Optional[str]):
    if not note:
        return None, None
    numbers = re.findall(r"(behind|arcos_only)=(\d+)", note)
    values = {k: int(v) for k, v in numbers}
    return values.get("behind"), values.get("arcos_only")


def _join(specs: Sequence[RefCandidateSpec], data: dict) -> List[ProbedRef]:
    """Pair each candidate with the probe's answer for it, by position."""
    answers = data.get("candidates", [])
    joined = []
    for index, spec in enumerate(specs):
        answer = answers[index] if index < len(answers) else {}
        if answer.get("sha") and not spec.sha:
            spec.sha = answer.get("sha")
        joined.append(ProbedRef(
            spec=spec, index=index,
            reachable=bool(answer.get("reachable")),
            shared=bool(answer.get("shared")) and not answer.get("error"),
            sha=answer.get("sha"), date=answer.get("date") or None,
            merge_bases=list(answer.get("merge_bases") or []),
            merge_base_date=answer.get("merge_base_date") or None,
            behind=answer.get("behind"), arcos_only=answer.get("arcos_only"),
            in_arcos=answer.get("in_arcos"),
            error=answer.get("error") if answer else "no answer from the probe",
            error_kind=answer.get("error_kind") if answer else "PROBE_FAILED",
        ))
    return joined


def _pairs(data: dict):
    return {tuple(pair) for pair in data.get("contains", []) if len(pair) == 2}


def _candidate_model(p: ProbedRef) -> UpstreamCandidate:
    return UpstreamCandidate(
        repository=p.spec.repository, ref=p.spec.ref,
        source=ResolutionMethod.ANCESTRY, accepted=False,
        shares_history=p.shared if not p.error else None,
        rejected_reason=(p.error if p.error else
                         None if p.shared else "no shared history"),
        strategy=p.spec.strategy, kind=p.spec.kind, sha=p.sha,
        behind=p.behind, arcos_only=p.arcos_only, in_arcos=p.in_arcos,
        error=p.error,
    )


def _repo_name(url: str) -> str:
    name = (url or "").rstrip("/").rsplit("/", 1)[-1]
    return name[:-4] if name.endswith(".git") else name


def _series_in_name(ref: str) -> Optional[str]:
    match = re.search(r"(?<!\d)(\d+)[._](\d+)(?!\d)", ref or "")
    return f"{match.group(1)}.{match.group(2)}" if match else None


def _years_between(older: Optional[str], newer: Optional[str]) -> Optional[float]:
    try:
        a = datetime.fromisoformat(older)
        b = datetime.fromisoformat(newer)
    except (TypeError, ValueError):
        return None
    return (b - a).total_seconds() / (365.25 * 86400)


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _category_override(override: Optional[dict], default: Category) -> Category:
    """Let a curated entry correct a classification the rules get wrong."""
    if not override:
        return default
    raw = override.get("category")
    if not raw:
        return default
    try:
        return Category(raw)
    except ValueError:
        log.warning("unknown category %r in overrides; ignoring", raw)
        return default
