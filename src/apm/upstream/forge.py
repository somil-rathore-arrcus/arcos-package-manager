"""Turn an arbitrary project URL into a cloneable git repository URL.

Debian records a package's origin as whatever URL the maintainer had - a release
page, an issue tracker, a project homepage, a tarball directory. Only some of
those can be turned into a git remote, and guessing wrong is worse than saying
nothing, because a wrong upstream produces a confident, meaningless diff.

So this module only returns a URL it can justify structurally, and refuses the
cases that look close but are not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

# Paths that are part of a forge's UI, not part of the repository identity.
_GITHUB_TRAILERS = {
    "releases", "issues", "tags", "tree", "blob", "wiki", "commits",
    "pulls", "archive", "raw", "compare", "actions", "downloads",
}

_SCHEME_FIXUPS = (
    ("git://github.com/", "https://github.com/"),
    ("git://git.kernel.org/", "https://git.kernel.org/"),
    ("git+https://", "https://"),
    ("git+ssh://", "ssh://"),
)


@dataclass
class ForgeRepo:
    url: str
    host: str
    owner: str
    name: str

    @property
    def web_url(self) -> str:
        if self.host == "git.kernel.org":
            return self.url
        return self.url[:-4] if self.url.endswith(".git") else self.url


def normalise_scheme(url: str) -> str:
    url = (url or "").strip()
    for old, new in _SCHEME_FIXUPS:
        if url.startswith(old):
            url = new + url[len(old):]
    # scp-style: git@github.com:owner/repo.git
    m = re.match(r"^(?:\w+@)?([\w.-]+):(?!//)(.+)$", url)
    if m and "/" in m.group(2):
        url = f"https://{m.group(1)}/{m.group(2)}"
    return url


def is_packaging_host(url: str, packaging_hosts: set) -> bool:
    host = urlparse(normalise_scheme(url or "")).netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    return host in {h.lower() for h in packaging_hosts}


def to_repo(url: Optional[str], forge_hosts: set) -> Optional[ForgeRepo]:
    """Return a cloneable repo for `url`, or None if one cannot be justified."""
    if not url:
        return None
    parsed = urlparse(normalise_scheme(url))
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    path = parsed.path.strip("/")

    if host.endswith("github.io"):
        # An org or user page. The org is knowable, the repository is not -
        # "lldpd.github.io" does not mean the repo is lldpd/lldpd. Refusing here
        # is what sends such packages to a curated, verified mapping instead.
        return None

    if host == "git.kernel.org":
        if not path.startswith("pub/scm/"):
            return None
        if not path.endswith(".git"):
            path = path + ".git"
        clean = f"https://git.kernel.org/{path}"
        name = path.rsplit("/", 1)[-1][:-4]
        return ForgeRepo(clean, host, "kernel", name)

    if host not in {h.lower() for h in forge_hosts}:
        return None

    segments = [s for s in path.split("/") if s]
    if len(segments) < 2:
        return None

    if host in ("github.com", "bitbucket.org", "codeberg.org"):
        owner, name = segments[0], segments[1]
        if len(segments) > 2 and segments[2] not in _GITHUB_TRAILERS:
            return None
    else:
        # GitLab-style: the project may sit under nested groups, and /-/ marks
        # the start of the UI path.
        if "-" in segments:
            segments = segments[: segments.index("-")]
        for trailer in ("tree", "blob", "commits", "tags", "releases"):
            if trailer in segments:
                segments = segments[: segments.index(trailer)]
        if len(segments) < 2:
            return None
        owner, name = "/".join(segments[:-1]), segments[-1]

    name = name[:-4] if name.endswith(".git") else name
    if not name or name in _GITHUB_TRAILERS:
        return None

    return ForgeRepo(f"https://{host}/{owner}/{name}.git", host, owner, name)
