"""On-disk cache for downloaded Debian artifacts.

The Debian Sources indices are ~10 MB each and the pool tarballs are fetched one
per package, so every network read goes through here. Reruns during development
then cost nothing and behave identically offline.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)


class HttpCache:
    def __init__(
        self,
        directory: Path,
        ttl_seconds: int = 86400,
        timeout_seconds: int = 120,
    ) -> None:
        self.directory = Path(directory).expanduser()
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        # Keep a readable tail so the cache dir can be inspected by eye.
        tail = url.rstrip("/").rsplit("/", 1)[-1][:60].replace(os.sep, "_")
        return self.directory / f"{digest}__{tail}"

    def _is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        if self.ttl_seconds <= 0:
            return True
        return (time.time() - path.stat().st_mtime) < self.ttl_seconds

    def get(self, url: str, *, allow_missing: bool = False) -> Optional[bytes]:
        """Return the URL's bytes, from cache when fresh.

        With allow_missing, a 404 is a normal answer and returns None rather than
        raising - several packages legitimately have no pool tarball at the
        version the index names.
        """
        path = self._path_for(url)
        if self._is_fresh(path):
            return path.read_bytes()

        log.debug("fetching %s", url)
        try:
            response = httpx.get(
                url, timeout=self.timeout_seconds, follow_redirects=True
            )
        except httpx.HTTPError as exc:
            # Serve a stale copy rather than failing the whole run on a blip.
            if path.exists():
                log.warning("fetch failed for %s (%s); using stale cache", url, exc)
                return path.read_bytes()
            if allow_missing:
                log.warning("fetch failed for %s: %s", url, exc)
                return None
            raise

        if response.status_code == 404 and allow_missing:
            return None
        response.raise_for_status()

        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(path)
        return response.content
