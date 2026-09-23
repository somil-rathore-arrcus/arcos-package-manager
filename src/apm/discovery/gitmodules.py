"""Discover the ARCoS package set from the arrcus_rel release manifest.

arrcus_rel tracks every shipped package as a git submodule, so .gitmodules on the
release branch IS the package list. Reading it beats any hand-kept list: it
cannot drift from what actually ships.

Submodules are not all under packages/ (linux, ONL-standalone and others sit at
the top level) and they do not all track the same branch (rsyslog tracks
aminor-trixie, ONL-xc tracks aminor-xc), so both are read per entry rather than
assumed.
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass
from typing import Optional

from ..gitio.transport import Transports

log = logging.getLogger(__name__)

_SUBMODULE_HEADER = re.compile(r'^\[submodule "([^"]+)"\]', re.MULTILINE)


@dataclass
class Submodule:
    name: str
    path: str
    url: str
    branch: Optional[str]
    # The commit the superproject actually pins for this submodule.
    pinned_commit: Optional[str] = None

    @property
    def package_name(self) -> str:
        """The package id: the last path element, which is how ARCoS names it."""
        return self.path.rstrip("/").rsplit("/", 1)[-1]

    @property
    def github_repository(self) -> str:
        """owner/name, from any of the URL spellings .gitmodules uses."""
        m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?/?$", self.url)
        return f"{m.group(1)}/{m.group(2)}" if m else ""

    @property
    def ssh_url(self) -> str:
        """Normalise to ssh://git@github.com/owner/name.git for git commands."""
        repo = self.github_repository
        return f"ssh://git@github.com/{repo}.git" if repo else self.url


def parse_gitmodules(text: str) -> list:
    """Parse .gitmodules into submodule records."""
    submodules = []
    parts = _SUBMODULE_HEADER.split(text)
    # split() yields [preamble, name1, body1, name2, body2, ...]
    for i in range(1, len(parts), 2):
        name, body = parts[i], parts[i + 1]
        fields = {}
        for line in body.splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            fields[key.strip().lower()] = value.strip()
        path = fields.get("path")
        url = fields.get("url")
        if not path or not url:
            log.warning("submodule %s has no path/url; skipped", name)
            continue
        submodules.append(
            Submodule(name=name, path=path, url=url, branch=fields.get("branch"))
        )
    return submodules


def read_manifest(transports: Transports, repository: str, branch: str,
                  path: str = ".gitmodules") -> str:
    """Read one file from a branch of a remote repo without a full clone."""
    git = transports.for_url(repository)
    workdir = "/tmp/apm-manifest"
    command = (
        f"rm -rf {workdir} && "
        f"git clone -q --depth 1 --filter=blob:none --no-checkout "
        f"--branch {shlex.quote(branch)} {shlex.quote(repository)} {workdir} && "
        f"cd {workdir} && git show {shlex.quote(branch)}:{shlex.quote(path)}"
    )
    result = git.shell(command, timeout=300)
    if not result.ok or not result.stdout.strip():
        raise RuntimeError(
            f"could not read {path} from {repository}@{branch}: "
            f"{result.stderr.strip()[:300]}"
        )
    return result.stdout


def discover(transports: Transports, settings) -> dict:
    """Read every release manifest and return {release: [Submodule]}.

    A package missing from a release's manifest does not ship in that release.
    That is a fact worth reporting, not a gap to paper over.
    """
    manifest = settings.manifest
    repository = manifest.get("repository")
    file_path = manifest.get("file", ".gitmodules")
    branches = manifest.get("branches", {})
    excluded = settings.excluded_submodules

    per_release = {}
    for release in settings.release_ids:
        branch = branches.get(release)
        if not branch:
            log.warning("no manifest branch configured for %s; skipped", release)
            continue
        text = read_manifest(transports, repository, branch, file_path)
        submodules = parse_gitmodules(text)
        kept = [
            s for s in submodules
            if s.package_name not in excluded and s.path not in excluded
        ]
        gitlinks = read_gitlinks(transports, repository, branch)
        for sub in kept:
            sub.pinned_commit = gitlinks.get(sub.path)
        missing = [s.package_name for s in kept if not s.pinned_commit]
        if missing:
            log.warning(
                "%s: no pinned commit for %s", release, ", ".join(sorted(missing))
            )
        log.info(
            "%s manifest (%s): %d submodules, %d packages after exclusions",
            release, branch, len(submodules), len(kept),
        )
        per_release[release] = kept
    return per_release


def read_gitlinks(transports: Transports, repository: str, branch: str) -> dict:
    """Return {submodule path: pinned commit SHA} for a manifest branch.

    The commit recorded in the superproject tree is what actually ships. The
    `branch = ...` line in .gitmodules is only a hint for
    `git submodule update --remote`, and it goes stale: the trixie manifest
    still says linux tracks `aminor` while pinning a 6.12 commit that branch
    does not contain. Trusting the branch field there would pair a 6.1 fork
    against linux-6.12.y and report a six-figure, meaningless commit backlog.
    """
    git = transports.for_url(repository)
    workdir = "/tmp/apm-gitlinks"
    command = (
        f"rm -rf {workdir} && "
        f"git clone -q --depth 1 --filter=blob:none --no-checkout "
        f"--branch {shlex.quote(branch)} {shlex.quote(repository)} {workdir} && "
        f"cd {workdir} && git ls-tree -r HEAD"
    )
    result = git.shell(command, timeout=300)
    if not result.ok:
        log.warning(
            "could not read gitlinks from %s@%s: %s",
            repository, branch, result.stderr.strip()[:200],
        )
        return {}

    links = {}
    for line in result.stdout.splitlines():
        # <mode> <type> <sha>\t<path>; gitlinks are mode 160000.
        if not line.startswith("160000"):
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 3:
            links[path.strip()] = parts[2]
    log.info("%s@%s pins %d submodule commits", repository, branch, len(links))
    return links
