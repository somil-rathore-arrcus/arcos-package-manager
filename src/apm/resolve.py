"""Resolve every package, for every release it ships in.

One Resolution per (package, release). A package absent from a release's
manifest produces no row for that release at all - eight packages ship in
bookworm but not trixie, and inventing rows for them would be fiction.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .cache import HttpCache
from .classify import classify
from .debian.dep12 import fetch_artifacts
from .debian.sources_index import SourcesIndex, load_index
from .gitio.ancestry import AncestryChecker, best_candidate
from .gitio.verify import Verifier
from .models import Category, Confidence, Method, Resolution, Status, Upstream
from .upstream.forge import is_packaging_host
from .upstream.strategies import resolve as resolve_chain

log = logging.getLogger(__name__)


class Resolver:
    def __init__(self, settings, overrides: dict, cache: HttpCache,
                 verifier: Verifier,
                 ancestry: Optional[AncestryChecker] = None) -> None:
        self.settings = settings
        self.overrides = overrides or {}
        self.cache = cache
        self.verifier = verifier
        self.ancestry = ancestry
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

    def resolve_one(self, package, release: str) -> Resolution:
        override = self._override_for(package.name, release)
        branch = package.branch_for(release)
        pinned = (package.commits or {}).get(release)

        resolution = Resolution(
            package=package.name,
            release=release,
            category=Category.ARRCUS_NATIVE,
            status=Status.UNRESOLVED,
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
                resolution.note(override.get("reason", "no upstream exists"))
                self._verify_arcos(resolution, pinned)
                return resolution
            # A package that should have an upstream but whose metadata named
            # none is not a finished answer: it is work for a person, so it is
            # NEEDS_REVIEW with the reason attached rather than a silent gap.
            resolution.status = (
                Status.NEEDS_REVIEW
                if resolution.category
                in (Category.DEBIAN_UPSTREAM, Category.DEBIAN_NO_UPSTREAM,
                    Category.THIRD_PARTY)
                else Status.NO_UPSTREAM
            )
            resolution.method = (
                Method.NOT_APPLICABLE
                if resolution.status == Status.NO_UPSTREAM
                else Method.UNRESOLVED
            )
            if resolution.status == Status.NO_UPSTREAM:
                resolution.note(
                    "No upstream exists for this package; it is "
                    f"{resolution.category.value.replace('_', ' ')}."
                )
            else:
                resolution.note(
                    "No DEP-12, debian/watch or Homepage field named a git "
                    "forge for this package, so no upstream candidate could be "
                    "proposed from Debian metadata. It needs a curated mapping "
                    "in config/overrides.yaml."
                )
                # Nothing in the metadata named an upstream, but the fork may
                # still descend from the Debian packaging repository - that is
                # exactly where several ARCoS forks come from. Ask the history.
                self._check_ancestry(resolution, source, pinned)
                self._set_origin_kind(resolution)
            self._verify_arcos(resolution, pinned)
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

        # Verify: an upstream nobody contacted is a guess.
        check = self.verifier.resolve_ref(
            candidate.upstream.repository, candidate.upstream.ref
        )
        if not check.reachable:
            resolution.status = Status.NEEDS_REVIEW
            resolution.confidence = Confidence.LOW
            resolution.record(f"git: repository unreachable ({check.error})")
        elif not check.ref_exists:
            resolution.status = Status.NEEDS_REVIEW
            resolution.confidence = Confidence.LOW
            resolution.record(
                f"git: repository reachable but ref "
                f"'{candidate.upstream.ref}' does not exist"
            )
        else:
            resolution.resolved_sha = check.sha
            resolution.verification = (
                f"git ls-remote resolved {candidate.upstream.repository} "
                f"{check.resolved_ref or candidate.upstream.ref} to a commit"
            )
            if candidate.upstream.ref is None:
                resolution.upstream = Upstream(
                    repository=candidate.upstream.repository,
                    ref=check.resolved_ref,
                )
                resolution.record(
                    f"git: no ref configured; using the upstream default "
                    f"branch '{check.resolved_ref}'"
                )
            resolution.upstream.is_tag = check.ref_kind == "tag"
            resolution.record(
                f"git verified {candidate.upstream.repository} @ "
                f"{resolution.upstream.ref} -> {check.sha[:12]}"
            )
            resolution.status = (
                Status.VERIFIED
                if candidate.confidence == Confidence.HIGH
                else Status.NEEDS_REVIEW
            )

        self._check_ancestry(resolution, source, pinned)
        self._set_origin_kind(resolution)
        self._verify_arcos(resolution, pinned)
        return resolution

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

    def _ancestry_candidates(self, resolution, source) -> list:
        """What the fork might have come from, in the order worth testing.

        The Debian packaging repository is included deliberately. ARCoS forked it
        for several packages, so excluding it on the grounds that it is "only
        packaging" is what makes those packages look like they have no origin.
        """
        candidates = []
        if resolution.upstream and resolution.upstream.repository:
            candidates.append(
                (resolution.upstream.repository, resolution.upstream.ref or "HEAD")
            )
        # Maintenance branches matching the Debian version. A project's default
        # branch is usually its development tip, which is the wrong comparison
        # for a fork pinned to a stable series: openssl trails master by 18,000
        # commits but only a few hundred on its own 3.0 branch.
        if resolution.upstream and source is not None:
            for ref in self._series_refs(
                resolution.upstream.repository, source.upstream_version
            ):
                candidates.append((resolution.upstream.repository, ref))

        vcs = source.vcs_git if source else None
        if vcs:
            for ref in self.settings.ancestry.get("packaging_refs", []):
                candidates.append((vcs, ref))
        seen, unique = set(), []
        for item in candidates:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    def _series_refs(self, repository: str, upstream_version: str) -> list:
        """Branches of `repository` whose name carries this version's series."""
        match = re.match(r"^(\d+)\.(\d+)", upstream_version or "")
        if not match:
            return []
        major, minor = match.group(1), match.group(2)
        branches = self.verifier.list_branches(repository)
        if not branches:
            return []
        # The version must appear as a whole number, not as a digit run inside a
        # longer one: bookworm's ethtool is 6.1, and a loose match picks
        # "ethtool-6.14.y", a series three years newer. Likewise "3.0" must not
        # match the "230" in a branch named after pull request 230.
        patterns = [
            re.compile(r"(?<!\d)%s[._]%s(?!\d)" % (major, minor)),
        ]
        hits = [b for b in branches if any(p.search(b) for p in patterns)]
        # Shortest first: "openssl-3.0" before "openssl-3.0-stable-backport".
        hits.sort(key=len)
        return hits[:3]

    def _check_ancestry(self, resolution, source, pinned) -> None:
        """Replace the metadata guess with the origin the history proves."""
        if self.ancestry is None or not self.ancestry.enabled:
            return
        if resolution.package in set(self.settings.ancestry.get("skip", [])):
            resolution.record(
                "ancestry probe skipped for this repository (configured)"
            )
            return

        candidates = self._ancestry_candidates(resolution, source)
        if not candidates:
            return

        arcos_ref = pinned or resolution.arcos_branch
        data = self.ancestry.check(
            resolution.package, resolution.release,
            resolution.arcos_repository, arcos_ref, candidates,
        )
        if data is None:
            resolution.record("ancestry could not be probed")
            return
        if not data.get("arcos_ok"):
            resolution.record("ancestry: the ARCoS fork could not be fetched")
            return

        best = best_candidate(data)
        if best is None:
            tried = ", ".join(f"{u} @ {r}" for u, r in candidates)
            resolution.status = Status.NEEDS_REVIEW
            resolution.confidence = Confidence.LOW
            resolution.record(
                f"ancestry: NO candidate shares history with the fork (tried {tried})"
            )
            resolution.note(
                "The ARCoS fork has no commit in common with any candidate "
                "upstream, so it was imported rather than forked. A commit-level "
                "comparison is not meaningful until a real origin is identified."
            )
            return

        chosen_repo, chosen_ref = best["url"], best["ref"]
        current = (
            resolution.upstream.repository if resolution.upstream else None,
            resolution.upstream.ref if resolution.upstream else None,
        )
        if (chosen_repo, chosen_ref) != current:
            resolution.record(
                f"ancestry: the fork descends from {chosen_repo} @ {chosen_ref}, "
                f"not {current[0]} @ {current[1]}; corrected"
            )
            resolution.upstream = Upstream(repository=chosen_repo, ref=chosen_ref)
            resolution.method = Method.ANCESTRY
            resolution.evidence_source = "git merge-base against the ARCoS fork"
            resolution.evidence_url = chosen_repo
        resolution.resolved_sha = best.get("candidate_sha")
        resolution.merge_base = best.get("merge_base")
        resolution.behind = best.get("behind")
        resolution.arcos_only = best.get("arcos_only")
        resolution.confidence = Confidence.HIGH
        resolution.status = Status.VERIFIED
        resolution.verification = (
            f"git merge-base: the fork shares history with {chosen_repo} @ "
            f"{chosen_ref} at {str(best.get('merge_base'))[:12]} "
            f"({len(candidates)} candidate ref(s) probed)"
        )
        resolution.record(
            f"ancestry verified: merge-base {str(best.get('merge_base'))[:12]}, "
            f"{best.get('behind')} commits behind, "
            f"{best.get('arcos_only')} ARCoS-only"
        )

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
