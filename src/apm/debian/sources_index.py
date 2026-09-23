"""Read Debian source-package metadata straight from the archive `Sources` index.

This is the exact data `apt-cache showsrc` serves, so it needs no Debian
container and no apt: the index is a static file on the mirror. Bookworm carries
34,335 source packages and trixie 37,643, so every package ARCoS forks is
covered, including the ones an HTML scrape of packages.debian.org would miss.

Suites are merged in order, later winning, so -updates and -security override
the base suite the same way apt would.
"""

from __future__ import annotations

import logging
import lzma
from typing import Optional

from ..cache import HttpCache
from ..models import DebianSource
from .deb822 import parse_stanzas

log = logging.getLogger(__name__)


class SourcesIndex:
    """All source packages for one Debian release."""

    def __init__(self, release: str, entries: dict) -> None:
        self.release = release
        self.entries = entries

    def get(self, source_package: str) -> Optional[DebianSource]:
        return self.entries.get(source_package)

    def __len__(self) -> int:
        return len(self.entries)


def _index_urls(release: str, archive: dict) -> list:
    mirror = archive.get("mirror", "").rstrip("/")
    security = archive.get("security_mirror", "").rstrip("/")
    components = archive.get("components", ["main"])
    urls = []
    for suite_tmpl in archive.get("suites", []):
        suite = suite_tmpl.format(release=release)
        for component in components:
            urls.append(
                (suite, mirror, f"{mirror}/dists/{suite}/{component}/source/Sources.xz")
            )
    for suite_tmpl in archive.get("security_suites", []):
        suite = suite_tmpl.format(release=release)
        for component in components:
            urls.append(
                (suite, security,
                 f"{security}/dists/{suite}/{component}/source/Sources.xz")
            )
    return urls


def load_index(release: str, archive: dict, cache: HttpCache) -> SourcesIndex:
    entries = {}
    for suite, mirror, url in _index_urls(release, archive):
        raw = cache.get(url, allow_missing=True)
        if raw is None:
            log.info("no Sources index at %s (suite may not exist yet)", url)
            continue
        text = lzma.decompress(raw).decode("utf-8", errors="replace")
        count = 0
        for stanza in parse_stanzas(text):
            name = stanza.get("Package")
            if not name:
                continue
            entries[name] = DebianSource(
                package=name,
                version=stanza.get("Version", ""),
                directory=stanza.get("Directory", ""),
                suite=suite,
                mirror=mirror,
                vcs_git=_vcs_url(stanza.get("Vcs-Git")),
                vcs_branch=_vcs_branch(stanza.get("Vcs-Git")),
                vcs_browser=_first_line(stanza.get("Vcs-Browser")),
                homepage=_first_line(stanza.get("Homepage")),
            )
            count += 1
        log.info("%s: %d source packages from %s", release, count, suite)
    return SourcesIndex(release, entries)


def _first_line(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value.split("\n", 1)[0].strip() or None


def _vcs_url(value: Optional[str]) -> Optional[str]:
    """The URL part of Vcs-Git, without the ' -b branch' suffix."""
    first = _first_line(value)
    if not first:
        return None
    return first.split(" -b ", 1)[0].strip() or None


def _vcs_branch(value: Optional[str]) -> Optional[str]:
    """The branch Vcs-Git names, when it names one.

    Debian writes it inline: "https://salsa.debian.org/x/y.git -b debian/master".
    Dropping it loses the only statement Debian makes about which branch of the
    packaging repository the release was built from.
    """
    first = _first_line(value)
    if not first or " -b " not in first:
        return None
    return first.split(" -b ", 1)[1].strip().split()[0] or None
