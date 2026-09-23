"""Wires the services together, once.

The CLI, the API and the tests all build the same object graph from here, so
there is no second place where a service is constructed slightly differently.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from ..cache import HttpCache
from ..config import Environment, load_overrides, load_settings
from ..gitio.ancestry import AncestryChecker
from ..gitio.verify import Verifier
from ..gitio.workspaces import WorkspaceManager
from ..resolve import Resolver
from .catalog_service import CatalogService
from .comparison_service import ComparisonService
from .criticality_service import CriticalityService
from .github_service import GitHubService
from .mapping_store import MappingStore
from .metadata_batch_service import MetadataBatchService
from .patch_id_service import PatchIdService
from .patch_service import PatchService
from .upstream_md_service import UpstreamMdService
from .upstream_service import UpstreamService

log = logging.getLogger(__name__)


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
        self.ancestry = AncestryChecker(
            self.transports,
            self.environment.cache_dir / "ancestry",
            enabled=self.settings.ancestry.get("enabled", True),
            timeout=self.environment.git_long_timeout,
        )
        self.workspaces = WorkspaceManager(
            self.transports,
            root=self.environment.workspace_dir,
            timeout=self.environment.git_timeout,
            long_timeout=self.environment.git_long_timeout,
            committer_name=self.environment.committer_name,
            committer_email=self.environment.committer_email,
            private_url_hint=_private_hint(self.settings),
        )

        self.resolver = Resolver(
            self.settings, self.overrides, self.http_cache, self.verifier,
            self.ancestry,
        )
        self.catalog = CatalogService(self.settings, self.verifier)
        self.mapping = MappingStore()
        self.upstream = UpstreamService(
            self.resolver, self.catalog, self.verifier, self.settings,
            workspaces=self.workspaces, mapping=self.mapping,
        )
        self.criticality = CriticalityService()
        self.patch_ids = PatchIdService()
        self.comparison = ComparisonService(
            self.workspaces, self.criticality, self.patch_ids
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
        }


def _private_hint(settings) -> str:
    """A URL that matches the private patterns, used to pick the workspace host."""
    patterns = settings.private_repository_patterns
    if patterns and "github" in patterns[0]:
        return "ssh://git@github.com/arrcus/placeholder.git"
    return "ssh://git@github.com/arrcus/placeholder.git"


@lru_cache(maxsize=1)
def get_container() -> Container:
    return Container()
