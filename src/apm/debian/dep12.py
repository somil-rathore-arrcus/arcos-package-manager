"""Fetch a package's debian/ directory to read its upstream declarations.

Two files matter, and they are the only places Debian records where a package
actually comes from:

  debian/upstream/metadata  DEP-12. Its Repository: field is the one field in
                            Debian that names the true upstream git repository.
  debian/watch              Where new tarballs are looked for. Sometimes a forge
                            URL, often just a download page - useful, not proof.

Neither is guaranteed to exist. Absence is a normal outcome and is reported as
such rather than treated as an error.
"""

from __future__ import annotations

import io
import logging
import lzma
import re
import tarfile
from dataclasses import dataclass, field
from typing import Dict, Optional

from ..cache import HttpCache
from ..models import DebianSource
from .deb822 import parse_stanzas

log = logging.getLogger(__name__)


@dataclass
class DebianArtifacts:
    dep12: dict
    watch: Optional[str]
    fetched: bool
    note: Optional[str] = None
    # What Debian changed on top of the upstream release: read from the same
    # tarball, so the patch history costs no second download.
    changelog: Optional[str] = None
    patch_series: Optional[str] = None
    patches: Dict[str, str] = field(default_factory=dict)
    source_format: Optional[str] = None
    url: Optional[str] = None

    @property
    def dep12_repository(self) -> Optional[str]:
        for key in ("Repository", "repository"):
            value = self.dep12.get(key)
            if value:
                return value.split("\n")[0].strip()
        return None

    @property
    def dep12_browse(self) -> Optional[str]:
        for key in ("Repository-Browse", "repository-browse"):
            value = self.dep12.get(key)
            if value:
                return value.split("\n")[0].strip()
        return None


def _strip_epoch(version: str) -> str:
    return version.split(":", 1)[-1]


def _tarball_urls(source: DebianSource, mirror: str = "") -> list:
    """Candidate debian.tar.* URLs for this source version.

    A native package has no separate .debian.tar.*; its whole source is one
    tarball, which also contains debian/. Both shapes are tried.
    """
    version = _strip_epoch(source.version)
    base = f"{(source.mirror or mirror).rstrip('/')}/{source.directory.strip('/')}"
    stem = f"{source.package}_{version}"
    return [
        f"{base}/{stem}.debian.tar.xz",
        f"{base}/{stem}.debian.tar.gz",
        f"{base}/{stem}.debian.tar.bz2",
        f"{base}/{stem}.tar.xz",
        f"{base}/{stem}.tar.gz",
    ]


def fetch_artifacts(
    source: DebianSource, mirror: str, cache: HttpCache
) -> DebianArtifacts:
    if not source.directory:
        return DebianArtifacts({}, None, False, "no Directory field in the index")

    for url in _tarball_urls(source, mirror):
        raw = cache.get(url, allow_missing=True)
        if raw is None:
            continue
        try:
            return _extract(raw, url)
        except (tarfile.TarError, lzma.LZMAError, EOFError) as exc:
            log.warning("could not read %s: %s", url, exc)
            return DebianArtifacts({}, None, False, f"unreadable archive: {exc}")

    return DebianArtifacts(
        {}, None, False, "no debian tarball found at the indexed version"
    )


# A patch larger than this is recorded by name only; its content is not needed
# to say it exists, and a vendored tarball-sized diff is not worth holding.
MAX_PATCH_BYTES = 2 * 1024 * 1024


def _extract(raw: bytes, url: str) -> DebianArtifacts:
    artifacts = DebianArtifacts({}, None, True, url=url)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            # Paths are either debian/... or <pkg>-<ver>/debian/... depending on
            # whether the tarball is a debian diff or a full native source.
            name = member.name
            relative = _debian_relative(name)
            if relative is None:
                continue
            if relative == "upstream/metadata":
                artifacts.dep12 = _parse_dep12(_read(tar, member))
            elif relative == "watch":
                artifacts.watch = _read(tar, member)
            elif relative == "changelog":
                artifacts.changelog = _read(tar, member)
            elif relative == "source/format":
                artifacts.source_format = _read(tar, member).strip() or None
            elif relative == "patches/series":
                artifacts.patch_series = _read(tar, member)
            elif relative.startswith("patches/"):
                patch = relative[len("patches/"):]
                artifacts.patches[patch] = (
                    _read(tar, member) if member.size <= MAX_PATCH_BYTES else ""
                )
    return artifacts


def _debian_relative(name: str) -> Optional[str]:
    """'debian/x' or '<pkg>-<ver>/debian/x' -> 'x'; anything else -> None."""
    parts = name.split("/")
    if parts and parts[0] == "debian":
        return "/".join(parts[1:]) or None
    if len(parts) > 2 and parts[1] == "debian":
        return "/".join(parts[2:]) or None
    return None


def _read(tar: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    handle = tar.extractfile(member)
    if handle is None:
        return ""
    return handle.read().decode("utf-8", errors="replace")


def _parse_dep12(text: str) -> dict:
    """DEP-12 is YAML in practice, but a deb822 read is enough for flat keys.

    Using the stanza parser avoids a YAML dependency failing the whole lookup on
    one malformed file, which does happen in the archive.
    """
    for stanza in parse_stanzas(text):
        if stanza:
            return stanza
    return {}


_WATCH_CONTINUATION = re.compile(r"\\\s*\n\s*")


def watch_urls(watch: Optional[str]) -> list:
    """Extract the candidate URLs from a debian/watch file.

    Comments, the version= line and opts=... are stripped; what remains starts
    with the URL being watched.
    """
    if not watch:
        return []
    text = _WATCH_CONTINUATION.sub(" ", watch)
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("version="):
            continue
        # Drop a leading opts="..." or opts=... block.
        line = re.sub(r'^opts\s*=\s*"[^"]*"\s*', "", line)
        line = re.sub(r"^opts\s*=\s*\S+\s+", "", line)
        match = re.search(r"(https?://\S+|ftp://\S+)", line)
        if match:
            urls.append(match.group(1))
    return urls
