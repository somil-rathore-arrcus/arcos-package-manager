"""Confirm a resolved upstream actually exists, by asking the server.

A mapping that has not been verified is a guess. Every upstream this tool
reports has had its repository contacted and its ref resolved to a SHA.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Optional

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


@dataclass
class RefListing:
    """Every branch and tag a remote publishes, from one ls-remote.

    `ok` is False when the remote could not be asked. An empty listing from a
    reachable remote and a listing that failed lead to opposite conclusions, so
    they must never look alike.
    """

    ok: bool
    branches: Dict[str, str] = field(default_factory=dict)
    # Tag name -> the commit it names (peeled for annotated tags).
    tags: Dict[str, str] = field(default_factory=dict)
    head: Optional[str] = None
    error: Optional[str] = None

    def sha_of(self, ref: str) -> Optional[str]:
        return self.branches.get(ref) or self.tags.get(ref)

    def kind_of(self, ref: str) -> Optional[str]:
        if ref in self.branches:
            return "branch"
        if ref in self.tags:
            return "tag"
        return None


class Verifier:
    def __init__(self, transports: Transports) -> None:
        self.transports = transports
        self._cache = {}
        self._branches = {}
        self._listings: Dict[str, RefListing] = {}

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
        """Every branch name the remote publishes. Empty when it cannot be
        asked - callers that must tell the two apart use list_refs."""
        if repository in self._branches:
            return self._branches[repository]
        listing = self.list_refs(repository)
        if not listing.ok:
            log.warning("could not list branches of %s: %s", repository,
                        listing.error)
        names = sorted(listing.branches)
        self._branches[repository] = names
        return names

    def list_refs(self, repository: str) -> RefListing:
        """Branches, tags and the default branch, in one round trip."""
        if repository in self._listings:
            return self._listings[repository]
        git = self.transports.for_url(repository)
        out = git.run(
            ["ls-remote", "--symref", repository, "HEAD", "refs/heads/*",
             "refs/tags/*"]
        )
        if not out.ok:
            # Not cached: a transient failure must not become the answer for
            # the rest of the run.
            return RefListing(ok=False, error=_clean(out.stderr))
        listing = parse_ls_remote(out.stdout)
        self._listings[repository] = listing
        return listing


def parse_ls_remote(text: str) -> RefListing:
    """Parse `git ls-remote --symref` output into a RefListing."""
    listing = RefListing(ok=True)
    peeled: Dict[str, str] = {}
    for line in (text or "").splitlines():
        head = re.match(r"^ref:\s+refs/heads/(\S+)\s+HEAD$", line)
        if head:
            listing.head = head.group(1)
            continue
        match = _SHA_LINE.match(line.strip())
        if not match:
            continue
        sha, ref = match.group(1), match.group(2)
        if ref.startswith("refs/heads/"):
            listing.branches[ref[len("refs/heads/"):]] = sha
        elif ref.startswith("refs/tags/"):
            name = ref[len("refs/tags/"):]
            if name.endswith("^{}"):
                peeled[name[:-3]] = sha
            else:
                listing.tags.setdefault(name, sha)
    # An annotated tag's own object is not a commit; the peeled line is.
    listing.tags.update(peeled)
    return listing


def classify_git_error(stderr: str) -> str:
    """NETWORK_ERROR, INVALID_REF or GIT_ERROR, from what git printed."""
    text = (stderr or "").lower()
    if any(s in text for s in (
        "couldn't find remote ref", "not our ref", "no such ref",
        "invalid refspec", "unadvertised object", "bad object",
        "not a valid object name", "unknown revision",
    )):
        return "INVALID_REF"
    if any(s in text for s in (
        "could not resolve host", "connection timed out", "connection refused",
        "timed out after", "network is unreachable", "could not read from remote",
        "permission denied", "repository not found", "unable to access",
        "connection reset", "host key verification failed", "early eof",
        "the remote end hung up",
    )):
        return "NETWORK_ERROR"
    return "GIT_ERROR"


def _clean(text: str) -> str:
    """Collapse git's multi-line stderr into one reportable line."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    # Drop noise that says nothing about the cause.
    lines = [l for l in lines if not l.startswith("Warning: Permanently added")]
    return "; ".join(lines[:3])[:300] if lines else "unknown git error"
