"""Confirm a resolved upstream actually exists, by asking the server.

A mapping that has not been verified is a guess. Every upstream this tool
reports has had its repository contacted and its ref resolved to a SHA.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .transport import Transports

log = logging.getLogger(__name__)

_SHA_LINE = re.compile(r"^([0-9a-f]{40})\s+(\S+)$", re.MULTILINE)


@dataclass
class RefCheck:
    reachable: bool
    ref_exists: bool
    sha: Optional[str]
    resolved_ref: Optional[str]
    error: Optional[str] = None
    # "branch" or "tag" - a ref may be either, and which one it is changes what
    # tracking it means: a tag is a fixed point, a branch keeps moving.
    ref_kind: Optional[str] = None


class Verifier:
    def __init__(self, transports: Transports) -> None:
        self.transports = transports
        self._cache = {}
        self._branches = {}

    def resolve_ref(self, repository: str, ref: Optional[str]) -> RefCheck:
        """ls-remote the repository and pin `ref` to a SHA.

        With no ref, HEAD's target branch is used, which is what "the upstream's
        own default" means.
        """
        key = (repository, ref)
        if key in self._cache:
            return self._cache[key]
        result = self._resolve_ref(repository, ref)
        self._cache[key] = result
        return result

    def _resolve_ref(self, repository: str, ref: Optional[str]) -> RefCheck:
        git = self.transports.for_url(repository)

        if not ref:
            out = git.run(["ls-remote", "--symref", repository, "HEAD"])
            if not out.ok:
                return RefCheck(False, False, None, None, _clean(out.stderr))
            head = re.search(r"^ref:\s+refs/heads/(\S+)\s+HEAD$", out.stdout, re.M)
            sha = _SHA_LINE.search(out.stdout)
            return RefCheck(
                reachable=True,
                ref_exists=bool(sha),
                sha=sha.group(1) if sha else None,
                resolved_ref=head.group(1) if head else "HEAD",
                ref_kind="branch",
            )

        # Ask for the branch and the tag in one round trip; a ref may be either.
        out = git.run(
            [
                "ls-remote",
                repository,
                f"refs/heads/{ref}",
                f"refs/tags/{ref}",
                f"refs/tags/{ref}^{{}}",
            ]
        )
        if not out.ok:
            return RefCheck(False, False, None, None, _clean(out.stderr))

        matches = _SHA_LINE.findall(out.stdout)
        if not matches:
            # Reachable, but no such ref. That is a real answer, not an error.
            return RefCheck(True, False, None, None, None)

        # Prefer a peeled tag (^{}) - it points at the commit, not the tag object.
        peeled = [m for m in matches if m[1].endswith("^{}")]
        chosen = peeled[0] if peeled else matches[0]
        kind = "branch" if chosen[1].startswith("refs/heads/") else "tag"
        return RefCheck(True, True, chosen[0], ref, ref_kind=kind)

    def branch_tip(self, repository: str, branch: str) -> RefCheck:
        return self.resolve_ref(repository, branch)

    def list_branches(self, repository: str) -> list:
        """Every branch name the remote publishes."""
        if repository in self._branches:
            return self._branches[repository]
        git = self.transports.for_url(repository)
        out = git.run(["ls-remote", "--heads", repository])
        names = []
        if out.ok:
            for line in out.stdout.splitlines():
                if "refs/heads/" in line:
                    names.append(line.split("refs/heads/", 1)[1].strip())
        self._branches[repository] = names
        return names


def _clean(text: str) -> str:
    """Collapse git's multi-line stderr into one reportable line."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    # Drop noise that says nothing about the cause.
    lines = [l for l in lines if not l.startswith("Warning: Permanently added")]
    return "; ".join(lines[:3])[:300] if lines else "unknown git error"
