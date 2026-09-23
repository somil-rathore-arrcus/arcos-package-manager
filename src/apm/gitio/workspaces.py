"""Hands out git workspaces, on whichever host the repository needs.

Keyed by package and release so repeated comparisons reuse the clone. The root
lives on the host git runs on, which with the ssh backend is the remote machine,
so it is configuration rather than a local temp directory.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .workspace import GitWorkspace

log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class WorkspaceManager:
    def __init__(self, transports, root: str = "",
                 timeout: int = 60, long_timeout: int = 900,
                 committer_name: str = "ARCoS Package Manager",
                 committer_email: str = "arcos@localhost",
                 private_url_hint: str = "") -> None:
        self.transports = transports
        self.root = root or "/var/tmp/arcos-package-manager"
        self.timeout = timeout
        self.long_timeout = long_timeout
        self.committer_name = committer_name
        self.committer_email = committer_email
        # Workspaces must be created on the host that can reach the ARCoS forks,
        # so the transport is chosen from a representative private URL rather
        # than per git command.
        self.private_url_hint = private_url_hint or "ssh://git@github.com/arrcus/x.git"

    def transport(self):
        return self.transports.for_url(self.private_url_hint)

    def _path(self, *parts: str) -> str:
        safe = [_SAFE.sub("-", p) for p in parts if p]
        return "/".join([self.root.rstrip("/"), *safe])

    def _make(self, path: str) -> GitWorkspace:
        return GitWorkspace(
            transport=self.transport(),
            path=path,
            timeout=self.timeout,
            long_timeout=self.long_timeout,
            committer_name=self.committer_name,
            committer_email=self.committer_email,
        )

    def for_package(self, package: str, release: str) -> GitWorkspace:
        """A long-lived workspace for comparing one package. Reused across calls."""
        return self._make(self._path("repos", f"{package}__{release}"))

    def scratch(self, label: str) -> GitWorkspace:
        """A throwaway workspace for an operation that must not touch anything."""
        return self._make(self._path("scratch", label))

    def probe(self) -> dict:
        """Check the workspace root is usable, so failures surface early."""
        transport = self.transport()
        result = transport.shell(
            f"mkdir -p {self.root} && test -w {self.root} && "
            f"command -v git >/dev/null && git --version",
            timeout=self.timeout,
        )
        return {
            "root": self.root,
            "transport": transport.name,
            "writable": result.ok,
            "git_version": result.text if result.ok else None,
            "error": None if result.ok else result.stderr.strip()[:200],
        }
