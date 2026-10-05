"""Wires the services together, once.

The CLI, the API and the tests all build the same object graph from here, so
there is no second place where a service is constructed slightly differently.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from ..cache import HttpCache
from ..config import ROOT, Environment, load_overrides, load_settings
from ..debian.dep12 import fetch_artifacts
from ..debian.patches import build_report
from ..gitio.ancestry import AncestryChecker
from ..gitio.verify import Verifier
from ..gitio.workspaces import WorkspaceManager
from ..resolve import Resolver
from .approvals import ApprovalStore, approvals_file
from .backport_service import BackportDetector
from .catalog_service import CatalogService
from .comparison_service import ComparisonService
from .comparison_store import ComparisonStore
from .content_base_service import ContentBaseService
from .criticality_service import CriticalityService
from .github_service import GitHubService
from .mapping_store import MappingStore
from .metadata_batch_service import MetadataBatchService
from .patch_id_service import PatchIdService
from .patch_service import PatchService
from .security_service import (
    DebianSecurityProvider, OsvSecurityProvider, SecurityService,
)
from .upstream_md_publisher import BRANCH_TEMPLATE, UpstreamMdPublisher
from .upstream_md_service import UpstreamMdService
from .upstream_service import UpstreamService

log = logging.getLogger(__name__)

OUT_DIR = ROOT / "out"


def build_workspaces(environment: Environment, settings, transports):
    return WorkspaceManager(
        transports,
        root=environment.workspace_dir,
        timeout=environment.git_timeout,
        long_timeout=environment.git_long_timeout,
        committer_name=environment.committer_name,
        committer_email=environment.committer_email,
        private_url_hint=_private_hint(settings),
    )


def build_resolver(environment: Environment, settings, transports,
                   http_cache: HttpCache, verifier: Verifier = None,
                   ancestry_enabled: bool = True, workspaces=None,
                   overrides: dict = None) -> Resolver:
    """The one way a Resolver is put together - CLI, release run and API alike."""
    verifier = verifier or Verifier(transports)
    workspaces = workspaces or build_workspaces(environment, settings, transports)
    ancestry = AncestryChecker(
        transports, environment.cache_dir / "ancestry",
        enabled=settings.ancestry.get("enabled", True) and ancestry_enabled,
        timeout=environment.git_long_timeout,
        workspace_root=workspaces.root,
        cache_ttl=int(settings.ancestry.get("cache_ttl_seconds", 7 * 86400)),
    )
    content_cfg = settings.raw.get("content_base", {}) or {}
    content = None
    if content_cfg.get("enabled", True):
        content = ContentBaseService(
            workspaces, http_cache=http_cache, archive=settings.archive,
            max_tags=int(content_cfg.get("max_tags", 150)),
        )
    return Resolver(
        settings, load_overrides() if overrides is None else overrides,
        http_cache, verifier, ancestry, content_base=content,
        approvals=ApprovalStore(approvals_file(ROOT)),
    )


class Container:
    def __init__(self, environment: Environment = None, settings=None) -> None:
        self.environment = environment or Environment.from_env()
        self.settings = settings or load_settings()
        self.overrides = load_overrides()

        self.transports = self.environment.transports(self.settings)
        self.http_cache = HttpCache(
            self.environment.cache_dir / "http",
            self.environment.cache_ttl,
            self.environment.http_timeout,
        )
        self.verifier = Verifier(self.transports)
        self.workspaces = build_workspaces(
            self.environment, self.settings, self.transports
        )
        self.resolver = build_resolver(
            self.environment, self.settings, self.transports, self.http_cache,
            verifier=self.verifier, workspaces=self.workspaces,
            overrides=self.overrides,
        )
        self.ancestry = self.resolver.ancestry
        self.approvals = self.resolver.approvals
        self.catalog = CatalogService(self.settings, self.verifier)
        self.comparison_store = ComparisonStore(OUT_DIR / "comparisons")
        self.mapping = MappingStore(comparisons=self.comparison_store)
        self.upstream = UpstreamService(
            self.resolver, self.catalog, self.verifier, self.settings,
            workspaces=self.workspaces, mapping=self.mapping,
        )
        self.criticality = CriticalityService()
        self.patch_ids = PatchIdService()
        security_cfg = self.settings.raw.get("security", {}) or {}
        providers = [DebianSecurityProvider()]
        osv = security_cfg.get("osv", {}) or {}
        if osv.get("enabled", True):
            providers.append(OsvSecurityProvider(
                api_url=osv.get("api_url", "https://api.osv.dev/v1"),
                cache_dir=self.environment.cache_dir / "osv",
                ttl=int(osv.get("cache_ttl_seconds", 86400)),
                timeout=int(osv.get("timeout_seconds", 20)),
            ))
        self.security = SecurityService(providers)
        self.comparison = ComparisonService(
            self.workspaces, self.criticality, self.patch_ids,
            backports=BackportDetector(
                content_check_limit=int(
                    self.settings.raw.get("comparison", {}).get(
                        "content_check_limit", 1000)),
                timeout=self.environment.git_long_timeout,
            ),
            security=self.security,
            debian_patches=self.debian_patches_for,
            store=self.comparison_store,
        )
        self.patches = PatchService(self.workspaces)
        self.upstream_md = UpstreamMdService()
        self.metadata_batch = MetadataBatchService(
            self.upstream_md, workspaces=self.workspaces
        )
        self.github = GitHubService(
            token=self.environment.github_token,
            api_url=self.environment.github_api_url,
            timeout=self.environment.http_timeout,
        )
        publish = self.settings.upstream_md_publish
        self.publisher = UpstreamMdPublisher(
            self.workspaces, self.github,
            author_name=self.environment.committer_name,
            author_email=self.environment.committer_email,
            branch_template=publish["branch_template"] or BRANCH_TEMPLATE,
            exclude=publish["exclude"],
            defer=publish["defer"],
        )

    def debian_patches_for(self, resolution):
        """(report, {patch: text}) for the Debian version a resolution names."""
        if resolution.debian is None:
            return None
        source = self.resolver.debian_source(
            resolution.package, resolution.debian_release)
        if source is None:
            return None
        artifacts = fetch_artifacts(
            source, self.settings.archive.get("mirror", ""), self.http_cache)
        report = build_report(source.package, source.version,
                              resolution.debian_release, artifacts)
        return report, dict(artifacts.patches)

    def capabilities(self) -> dict:
        """What this deployment can actually do, so the UI can say so up front."""
        return {
            "read_private_repositories": (
                self.environment.git_backend != "local"
                or self.environment.ssh.configured
            ),
            "create_pull_requests": self.github.can_create_pull_requests,
            "git": self.transports.describe(),
            "workspace_root": self.workspaces.root,
            "mapping": self.mapping.describe(),
            # Write operations are disabled outright without a token, so the UI
            # can say so up front rather than failing at the last step.
            "read_only": not self.github.can_create_pull_requests,
            "storage": self.storage(),
        }

    def storage(self) -> dict:
        """Whether the runtime files can be written, without writing them."""
        import os

        from ..config import packages_file

        def writable(path) -> bool:
            path = path if path.is_dir() else path.parent
            return path.exists() and os.access(path, os.W_OK)

        target = packages_file()
        return {
            "out_dir": str(OUT_DIR),
            "out_writable": writable(OUT_DIR),
            "packages_file": str(target),
            "packages_file_writable": writable(target),
            "approvals_file": str(self.approvals.path),
        }


def _private_hint(settings) -> str:
    """A URL that matches the private patterns, used to pick the workspace host.

    Workspaces live on whichever host can reach the ARCoS forks, and every fork
    is under github.com/arrcus, so one representative URL is enough.
    """
    return "ssh://git@github.com/arrcus/placeholder.git"


@lru_cache(maxsize=1)
def get_container() -> Container:
    return Container()
