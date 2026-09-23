"""Resolving a package's upstream, automatically or from a user's input.

Automatic resolution reuses the verified mapping pipeline unchanged - it is the
part of this system with the most evidence behind it, and rewriting it to fit an
API would have thrown that away.

Manual resolution is not a bypass. A repository and ref supplied by a person are
checked the same way a discovered one is: the repository must be reachable, the
ref must exist, and where the fork has history the two must share an ancestor.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple

from ..domain.enums import (
    ErrorCode, ResolutionMethod, ResolutionMode, ResolutionStatus,
)
from ..domain.models import (
    Repository, ResolutionEvidence, UpstreamCandidate, UpstreamResolution,
)
from ..upstream.forge import is_packaging_host
from .adapters import to_domain_resolution

log = logging.getLogger(__name__)


class UpstreamServiceError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class UpstreamService:
    def __init__(self, resolver, catalog, verifier, settings,
                 workspaces=None, cache_ttl: int = 900, mapping=None) -> None:
        self.resolver = resolver
        self.catalog = catalog
        self.verifier = verifier
        self.settings = settings
        self.workspaces = workspaces
        self.mapping = mapping
        self._cache: Dict[Tuple[str, str, str], tuple] = {}
        self._cache_ttl = cache_ttl

    # -- automatic ---------------------------------------------------------

    def resolve(self, package_name: str, release: str,
                arcos_branch: Optional[str] = None,
                refresh: bool = False) -> UpstreamResolution:
        package = self.catalog.package(package_name)
        if package is None:
            raise UpstreamServiceError(
                ErrorCode.NOT_FOUND, f"No package named '{package_name}'."
            )
        if not package.ships_in(release):
            raise UpstreamServiceError(
                ErrorCode.NOT_FOUND,
                f"'{package_name}' is not in the {release} release manifest, so "
                f"it does not ship in {release}.",
            )

        key = (package_name, release, arcos_branch or "")
        cached = self._cache.get(key)
        if cached and not refresh and time.time() - cached[0] < self._cache_ttl:
            return cached[1]

        resolution = None
        if self.mapping is not None and not refresh:
            # The mapping run already did this work - Debian metadata, ls-remote
            # against every candidate, merge-base to prove ancestry. Re-running
            # it to fill a dropdown costs seconds at best, minutes at worst, and
            # returns the same answer.
            resolution = self.mapping.get(package_name, release)
            if resolution is not None:
                log.debug("served %s/%s from the mapping", package_name, release)

        if resolution is None:
            log.info(
                "resolving %s/%s live%s", package_name, release,
                " (refresh requested)" if refresh else
                " (no mapping row; run `apm resolve` to avoid this)",
            )
            internal = self._load_internal(package_name)
            record = self.resolver.resolve_one(internal, release)
            resolution = to_domain_resolution(
                record, packaging_hosts=self.settings.packaging_hosts
            )

        if arcos_branch and arcos_branch != resolution.arcos_branch:
            resolution = self._retarget(resolution, arcos_branch)

        self._cache[key] = (time.time(), resolution)
        return resolution

    def _load_internal(self, package_name: str):
        from ..config import load_packages

        internal = next(
            (p for p in load_packages() if p.name == package_name), None
        )
        if internal is None:
            raise UpstreamServiceError(
                ErrorCode.NOT_FOUND, f"No package named '{package_name}'."
            )
        return internal

    def _retarget(self, resolution: UpstreamResolution,
                  branch: str) -> UpstreamResolution:
        """Point the resolution at a branch the user picked instead of the pinned one."""
        check = self.verifier.branch_tip(resolution.arcos_repository, branch)
        if not check.reachable:
            raise UpstreamServiceError(
                ErrorCode.REPOSITORY_UNAVAILABLE,
                f"Could not reach {resolution.arcos_repository}.",
                check.error or "",
            )
        if not check.ref_exists:
            raise UpstreamServiceError(
                ErrorCode.BRANCH_NOT_FOUND,
                f"{resolution.arcos_repository} has no branch '{branch}'.",
            )
        updated = resolution.model_copy(deep=True)
        updated.arcos_branch = branch
        updated.arcos_commit = check.sha
        updated.notes.append(
            f"Comparing branch '{branch}' ({check.sha[:12]}) instead of the "
            f"commit the release manifest pins."
        )
        return updated

    # -- manual ------------------------------------------------------------

    def resolve_manual(self, package_name: str, release: str, repository: str,
                       ref: str, arcos_branch: Optional[str] = None
                       ) -> UpstreamResolution:
        """Accept a user's upstream only after checking it the same way."""
        if not repository or not ref:
            raise UpstreamServiceError(
                ErrorCode.INVALID_REQUEST,
                "A manual upstream needs both a repository and a branch or tag.",
            )

        base = self.resolve(package_name, release, arcos_branch)

        check = self.verifier.resolve_ref(repository, ref)
        if not check.reachable:
            raise UpstreamServiceError(
                ErrorCode.REPOSITORY_UNAVAILABLE,
                f"{repository} could not be reached. For a private repository "
                f"this is also what missing access looks like.",
                check.error or "",
            )
        if not check.ref_exists:
            raise UpstreamServiceError(
                ErrorCode.BRANCH_NOT_FOUND,
                f"{repository} has no branch or tag named '{ref}'.",
            )

        resolution = base.model_copy(deep=True)
        resolution.mode = ResolutionMode.MANUAL
        resolution.method = ResolutionMethod.MANUAL
        resolution.upstream_repository = Repository(
            url=repository,
            is_packaging=is_packaging_host(
                repository, self.settings.packaging_hosts
            ),
        )
        resolution.upstream_ref = ref
        # Which kind of ref it is comes from the same ls-remote that proved it
        # exists, so a manual mapping reports branch and tag as separately as an
        # automatic one does.
        resolution.upstream_branch = ref if check.ref_kind != "tag" else None
        resolution.upstream_tag = ref if check.ref_kind == "tag" else None
        resolution.upstream_commit = check.sha
        resolution.origin_kind = (
            "debian_packaging"
            if resolution.upstream_repository.is_packaging else "project"
        )
        resolution.status = ResolutionStatus.VERIFIED
        resolution.confidence = "manual"
        resolution.merge_base = None
        resolution.behind = None
        resolution.arcos_only = None
        resolution.evidence = [
            ResolutionEvidence(
                kind="manual",
                detail=f"Supplied by a user: {repository} @ {ref}",
                url=repository if repository.startswith("http") else None,
            ),
            ResolutionEvidence(
                kind="git",
                detail=f"git verified {repository} @ {ref} -> {check.sha[:12]}",
            ),
        ]
        resolution.candidates = [
            UpstreamCandidate(
                repository=repository, ref=ref, source=ResolutionMethod.MANUAL,
                accepted=True,
            )
        ]

        self._check_manual_ancestry(resolution)
        return resolution

    def _check_manual_ancestry(self, resolution: UpstreamResolution) -> None:
        """Say so when a manual upstream shares no history with the fork.

        Not an error - a user may know something the tool does not - but a
        comparison against it would have no common ancestor, and that is worth
        knowing before running one.
        """
        if self.workspaces is None or not resolution.arcos_commit:
            return
        workspace = self.workspaces.scratch(
            f"manual-{resolution.package}-{resolution.debian_release}"
        )
        try:
            workspace.destroy()
            workspace.ensure()
            arcos_head = workspace.fetch_side(
                "arcos", resolution.arcos_repository, resolution.arcos_commit
            )
            upstream_head = workspace.fetch_side(
                "upstream", resolution.upstream_repository.url,
                resolution.upstream_ref,
            )
            merge_base = workspace.merge_base(arcos_head, upstream_head)
            if merge_base:
                resolution.merge_base = merge_base
                resolution.behind = workspace.count(
                    f"{merge_base}..{upstream_head}", no_merges=True
                )
                resolution.arcos_only = workspace.count(
                    f"{merge_base}..{arcos_head}", no_merges=True
                )
                resolution.evidence.append(
                    ResolutionEvidence(
                        kind="ancestry",
                        detail=(
                            f"shares history with the fork: merge base "
                            f"{merge_base[:12]}, {resolution.behind} behind, "
                            f"{resolution.arcos_only} ARCoS-only"
                        ),
                    )
                )
            else:
                resolution.status = ResolutionStatus.NEEDS_REVIEW
                resolution.notes.append(
                    "This repository shares no history with the ARCoS fork. It "
                    "exists and the ref is real, but a commit comparison "
                    "against it would have no common ancestor."
                )
                resolution.evidence.append(
                    ResolutionEvidence(
                        kind="ancestry",
                        detail="no common ancestor with the ARCoS fork",
                    )
                )
        except Exception as exc:  # noqa: BLE001 - advisory only
            log.info("manual ancestry check skipped: %s", exc)
            resolution.notes.append(
                f"Ancestry could not be checked for this manual upstream: {exc}"
            )
        finally:
            workspace.destroy()
