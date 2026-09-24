"""Load and layer the three config files.

Precedence, highest first: overrides.yaml (hand-verified) > packages.yaml
(generated) > settings.yaml (static). Regenerating packages.yaml therefore never
discards a human's decision.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

from .gitio.transport import SshConfig, Transports
from .models import Package

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"

# The generated package catalogue. Committed as config/packages.yaml, but
# `discover` rewrites it at runtime - and in a container config/ is mounted
# read-only, so the rewrite goes wherever APM_PACKAGES_FILE points instead.
PACKAGES_FILE_ENV = "APM_PACKAGES_FILE"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


@dataclass
class Release:
    id: str
    suite: str
    version: str


@dataclass
class Settings:
    raw: dict

    @property
    def manifest(self) -> dict:
        return self.raw.get("manifest", {})

    @property
    def excluded_submodules(self) -> set:
        return set(self.raw.get("excluded_submodules", []))

    @property
    def releases(self) -> list:
        return [Release(**r) for r in self.raw.get("releases", [])]

    @property
    def release_ids(self) -> list:
        return [r.id for r in self.releases]

    @property
    def archive(self) -> dict:
        return self.raw.get("archive", {})

    @property
    def packaging_hosts(self) -> set:
        return set(self.raw.get("packaging_hosts", []))

    @property
    def forge_hosts(self) -> set:
        return set(self.raw.get("forge_hosts", []))

    @property
    def kernel(self) -> dict:
        return self.raw.get("kernel", {})

    @property
    def ancestry(self) -> dict:
        return self.raw.get("ancestry", {})

    @property
    def private_repository_patterns(self) -> List[str]:
        """Default patterns for repositories that need the access bridge.

        A fallback only - APM_PRIVATE_REPOSITORY_PATTERNS overrides it, so no
        deployment is stuck with what is written here.
        """
        return list(self.raw.get("private_repository_patterns", []))

    @property
    def upstream_md_publish(self) -> dict:
        """Branch template, exclusions and ordering for publish-upstream-md."""
        block = self.raw.get("upstream_md_publish", {}) or {}
        return {
            "branch_template": block.get("branch_template", ""),
            "exclude": {
                str(k): " ".join(str(v or "excluded in settings.yaml").split())
                for k, v in (block.get("exclude") or {}).items()
            },
            "defer": list(block.get("defer") or []),
        }

    def is_vendor(self, name: str) -> bool:
        patterns = self.raw.get("classification", {}).get("vendor_patterns", [])
        return any(re.search(p, name) for p in patterns)


@dataclass
class Environment:
    """Runtime settings. Everything here comes from the environment, never from a
    committed file, because it names hosts, paths and occasionally credentials.
    """

    git_backend: str = "auto"
    ssh: SshConfig = field(default_factory=SshConfig)
    private_repository_patterns: List[str] = field(default_factory=list)
    github_token: str = ""
    github_api_url: str = "https://api.github.com"
    cache_dir: Path = Path("~/.cache/arcos-package-manager")
    workspace_dir: str = ""
    cache_ttl: int = 86400
    http_timeout: int = 120
    git_timeout: int = 60
    git_long_timeout: int = 900
    committer_name: str = "ARCoS Package Manager"
    committer_email: str = "arcos-package-manager@localhost"

    @classmethod
    def from_env(cls) -> "Environment":
        _load_dotenv(ROOT / ".env")
        env = os.environ

        ssh = SshConfig(
            host=env.get("APM_SSH_HOST", ""),
            user=env.get("APM_SSH_USER", ""),
            port=int(env["APM_SSH_PORT"]) if env.get("APM_SSH_PORT") else None,
            identity_file=env.get("APM_SSH_IDENTITY_FILE", ""),
            proxy_jump=env.get("APM_SSH_PROXY_JUMP", ""),
            password=env.get("APM_SSH_PASSWORD", ""),
            strict_host_key_checking=env.get(
                "APM_SSH_STRICT_HOST_KEY_CHECKING", "accept-new"
            ),
            known_hosts_file=env.get("APM_SSH_KNOWN_HOSTS", ""),
            connect_timeout=int(env.get("APM_SSH_CONNECT_TIMEOUT", "30")),
            extra_options=_split(env.get("APM_SSH_OPTIONS", "")),
        )

        return cls(
            git_backend=env.get("APM_GIT_BACKEND", "auto"),
            ssh=ssh,
            private_repository_patterns=_split(
                env.get("APM_PRIVATE_REPOSITORY_PATTERNS", "")
            ),
            github_token=env.get("APM_GITHUB_TOKEN", ""),
            github_api_url=env.get("APM_GITHUB_API_URL", "https://api.github.com"),
            cache_dir=Path(
                env.get("APM_CACHE_DIR", "~/.cache/arcos-package-manager")
            ).expanduser(),
            workspace_dir=env.get("APM_WORKSPACE_DIR", ""),
            cache_ttl=int(env.get("APM_CACHE_TTL", "86400")),
            http_timeout=int(env.get("APM_HTTP_TIMEOUT", "120")),
            git_timeout=int(env.get("APM_GIT_TIMEOUT", "60")),
            git_long_timeout=int(env.get("APM_GIT_LONG_TIMEOUT", "900")),
            committer_name=env.get("APM_COMMITTER_NAME", "ARCoS Package Manager"),
            committer_email=env.get(
                "APM_COMMITTER_EMAIL", "arcos-package-manager@localhost"
            ),
        )

    def transports(self, settings: Optional["Settings"] = None) -> Transports:
        """Build the git transport chooser.

        Which repositories need the bridge is configuration: the environment wins,
        then settings.yaml, so a deployment can change it without code changes.
        """
        patterns = self.private_repository_patterns
        if not patterns and settings is not None:
            patterns = settings.private_repository_patterns
        return Transports(
            ssh_config=self.ssh,
            backend=self.git_backend,
            private_patterns=patterns,
            timeout=self.git_timeout,
        )

    def redacted(self) -> dict:
        """A description safe to log or return from an API."""
        return {
            "git_backend": self.git_backend,
            "ssh_host": self.ssh.host or None,
            "ssh_user": self.ssh.user or None,
            "ssh_proxy_jump": self.ssh.proxy_jump or None,
            "ssh_password_set": bool(self.ssh.password),
            "github_token_set": bool(self.github_token),
            "cache_dir": str(self.cache_dir),
        }


def _split(raw: str) -> List[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _load_dotenv(path: Path) -> None:
    """Populate os.environ from .env without overwriting a real env var."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_settings(path: Optional[Path] = None) -> Settings:
    return Settings(_read_yaml(path or CONFIG_DIR / "settings.yaml"))


def load_overrides(path: Optional[Path] = None) -> dict:
    data = _read_yaml(path or CONFIG_DIR / "overrides.yaml")
    return data.get("packages", {}) or {}


def packages_file() -> Path:
    """Where `discover` writes the package catalogue.

    APM_PACKAGES_FILE when set (the container points it at the writable out/
    mount), otherwise config/packages.yaml as in a local checkout.
    """
    _load_dotenv(ROOT / ".env")
    configured = os.environ.get(PACKAGES_FILE_ENV, "").strip()
    return Path(configured).expanduser() if configured else CONFIG_DIR / "packages.yaml"


def packages_source() -> Path:
    """Where the package catalogue is read from.

    The runtime file once `discover` has written one; until then the catalogue
    committed in config/, so a fresh deployment still has a package list.
    """
    runtime = packages_file()
    return runtime if runtime.exists() else CONFIG_DIR / "packages.yaml"


def load_packages(path: Optional[Path] = None) -> list:
    data = _read_yaml(path or packages_source())
    packages = []
    for name, entry in (data.get("packages") or {}).items():
        packages.append(
            Package(
                name=name,
                arcos_repository=entry.get("repository", ""),
                github_repository=entry.get("github_repository", ""),
                submodule_path=entry.get("submodule_path", ""),
                branches=entry.get("branches", {}) or {},
                releases=entry.get("releases", []) or [],
                commits=entry.get("commits", {}) or {},
                debian_source_package=entry.get("debian_source_package"),
            )
        )
    return packages
