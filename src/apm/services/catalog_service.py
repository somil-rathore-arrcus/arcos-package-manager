"""What exists: releases, packages, and the branches a package really has.

Nothing here is hard-coded. The package list comes from the release manifests, so
selecting Trixie shows the packages Trixie actually ships and not Bookworm's set,
and branch lists come from the repository rather than an assumption that every
package is on `aminor`.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from ..config import load_packages, load_settings
from ..domain.models import Branch, DebianRelease, Package
from ..services.adapters import to_domain_package

log = logging.getLogger(__name__)


class CatalogService:
    def __init__(self, settings, verifier, packages_path=None,
                 branch_cache_ttl: int = 900) -> None:
        self.settings = settings
        self.verifier = verifier
        self._packages_path = packages_path
        self._packages: Optional[List[Package]] = None
        self._branch_cache: Dict[str, tuple] = {}
        self._branch_cache_ttl = branch_cache_ttl

    def reload(self) -> None:
        self._packages = None
        self._branch_cache.clear()

    def releases(self) -> List[DebianRelease]:
        return [
            DebianRelease(
                id=r.id,
                name=f"Debian {r.id.capitalize()}",
                suite=r.suite,
                version=r.version,
            )
            for r in self.settings.releases
        ]

    def packages(self, release: Optional[str] = None) -> List[Package]:
        if self._packages is None:
            self._packages = [
                to_domain_package(p) for p in load_packages(self._packages_path)
            ]
        if release is None:
            return list(self._packages)
        # A package absent from a release's manifest does not ship there.
        return [p for p in self._packages if p.ships_in(release)]

    def package(self, name: str) -> Optional[Package]:
        return next((p for p in self.packages() if p.name == name), None)

    def branches(self, package_name: str, release: str) -> List[Branch]:
        """Branches the ARCoS fork publishes, with the shipped one marked.

        Some ARCoS forks publish thousands of branches, so this is a list to
        search rather than a short menu - but it is the real list, and the
        branch the release manifest names is flagged in it.
        """
        package = self.package(package_name)
        if package is None:
            return []

        configured = package.branch_for(release)
        key = package.arcos_repository
        cached = self._branch_cache.get(key)
        now = time.time()
        if cached and now - cached[0] < self._branch_cache_ttl:
            names = cached[1]
        else:
            names = self.verifier.list_branches(package.arcos_repository)
            self._branch_cache[key] = (now, names)

        if not names:
            # Discovery failed; offer what the manifest names rather than
            # pretending the package has no branches.
            names = [configured] if configured else []

        default = self._default_branch(package.arcos_repository)
        return [
            Branch(
                name=name,
                is_default=name == default,
                is_configured=name == configured,
                web_url=(
                    f"https://github.com/{package.github_repository}/tree/{name}"
                    if package.github_repository else None
                ),
            )
            for name in sorted(names, key=lambda n: (n != configured, n))
        ]

    def _default_branch(self, repository: str) -> Optional[str]:
        check = self.verifier.resolve_ref(repository, None)
        return check.resolved_ref if check.reachable else None
