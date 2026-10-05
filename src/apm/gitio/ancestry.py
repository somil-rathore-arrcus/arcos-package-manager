"""Prove which upstream an ARCoS fork actually came from, and how it relates.

Resolution by metadata produces a plausible upstream. Only shared git history
proves it, and the difference is not academic: ARCoS forked the Debian PACKAGING
repository for lttng-tools, liburcu, lttng-ust and others, so comparing them
against the projects' own repositories finds no common ancestor and any "behind"
count would be meaningless. Conversely openssl really does fork upstream GitHub.

There is no rule that predicts which - it has to be measured, so this runs the
probe (ancestry_probe.sh) against every candidate ref in one round trip and
returns what git said, errors included.

The counts are head-based - ARCOS..CANDIDATE and CANDIDATE..ARCOS - not
"everything after one merge base", which over-counts whenever history is
criss-crossed or ARCoS merged upstream more than once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shlex
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .transport import Transports

log = logging.getLogger(__name__)

PROBE = Path(__file__).with_name("ancestry_probe.sh")
# Bumped whenever the probe's output changes meaning, so an old cache entry
# cannot be read as a new one.
PROBE_VERSION = 3


class ProbeError(RuntimeError):
    """The probe could not produce an answer. Never a finding about a package."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class AncestryChecker:
    def __init__(self, transports: Transports, cache_dir: Path,
                 enabled: bool = True, timeout: int = 600,
                 workspace_root: str = "", cache_ttl: int = 7 * 86400) -> None:
        self.transports = transports
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        # Bounded: a probe that cannot finish in this long is a failure to
        # report, not something to keep a request waiting on.
        self.timeout = timeout
        # On the git host. Persistent, so a second probe fetches almost nothing.
        self.root = (workspace_root or "/var/tmp/arcos-package-manager").rstrip("/")
        self.cache_ttl = cache_ttl
        self._installed = set()

    @property
    def probe_path(self) -> str:
        return f"{self.root}/bin/apm-ancestry-probe-v{PROBE_VERSION}.sh"

    def _install(self, git) -> None:
        """Copy the probe to wherever the git commands run."""
        if git.name in self._installed:
            return
        script = PROBE.read_text(encoding="utf-8")
        target = shlex.quote(self.probe_path)
        result = git.shell(
            f"mkdir -p {shlex.quote(self.root + '/bin')} && "
            f"cat > {target} <<'APM_PROBE_EOF'\n{script}\nAPM_PROBE_EOF\n"
            f"chmod +x {target}",
            timeout=120,
        )
        if not result.ok:
            raise ProbeError(
                "PROBE_FAILED",
                f"could not install the ancestry probe at {self.probe_path}: "
                f"{(result.stderr or '').strip()[:200]}",
            )
        self._installed.add(git.name)

    @staticmethod
    def cache_key(arcos_url: str, arcos_ref: str, arcos_sha: Optional[str],
                  candidates: Sequence[Tuple[str, str, Optional[str]]]) -> dict:
        """What a cached answer is only valid for.

        The ARCoS commit and every candidate's commit, as ls-remote saw them
        just now. When any of them moves, the key no longer matches and the
        probe runs again - a result for yesterday's upstream tip is not a
        result for today's.
        """
        return {
            "probe_version": PROBE_VERSION,
            "arcos_url": arcos_url,
            "arcos_ref": arcos_ref,
            "arcos_sha": arcos_sha,
            "candidates": [list(c) for c in candidates],
        }

    def check(self, package: str, release: str, arcos_url: str, arcos_ref: str,
              candidates: Sequence[Tuple[str, str, Optional[str]]],
              refresh: bool = False, arcos_sha: Optional[str] = None) -> dict:
        """The probe result for `candidates` [(url, ref, sha-or-None)].

        Raises ProbeError when the probe could not run or the ARCoS commit could
        not be fetched; those are failures to measure, and must not be mistaken
        for "no shared history".
        """
        if not self.enabled:
            raise ProbeError("ANCESTRY_NOT_PROBED", "the ancestry probe is disabled")
        if not candidates:
            raise ProbeError("NO_CANDIDATE", "no candidate refs to probe")

        key = self.cache_key(arcos_url, arcos_ref, arcos_sha, candidates)
        cache_file = self.cache_dir / f"{package}__{release}.json"
        if not refresh:
            cached = self._read_cache(cache_file, key)
            if cached is not None:
                return cached

        git = self.transports.for_url(arcos_url)
        self._install(git)
        workdir = f"{self.root}/ancestry/{package}__{release}"
        args = [workdir, arcos_url, arcos_ref]
        for url, ref, _sha in candidates:
            args.extend([url, ref or "HEAD"])
        command = f"{shlex.quote(self.probe_path)} " + " ".join(
            shlex.quote(a) for a in args
        )

        result = git.shell(command, timeout=self.timeout)
        lines = (result.stdout or "").strip().splitlines()
        if not result.ok or not lines:
            raise ProbeError(
                "PROBE_FAILED",
                f"the ancestry probe did not complete: "
                f"{(result.stderr or 'no output').strip()[:300]}",
            )
        try:
            data = json.loads(lines[-1])
        except ValueError as exc:
            raise ProbeError(
                "PROBE_FAILED", f"the ancestry probe returned unparseable output: {exc}"
            ) from exc
        if data.get("error") and "arcos_ok" not in data:
            raise ProbeError("PROBE_FAILED", str(data["error"]))
        if not data.get("arcos_ok"):
            # A fork that could not be fetched is a failure to look, not an
            # answer - and it is not cached, so the next run asks again.
            kind = data.get("error_kind") or "PROBE_FAILED"
            raise ProbeError(
                "ARCOS_UNREACHABLE" if kind == "NETWORK_ERROR" else kind,
                data.get("error") or "the ARCoS fork could not be fetched",
            )

        # A candidate whose fetch failed on the network is not cached either:
        # one flaky round trip must not become a permanent verdict.
        transient = any(
            c.get("error_kind") in ("NETWORK_ERROR", "GIT_ERROR")
            for c in data.get("candidates", [])
        )
        if not transient:
            payload = {"key": key, "created": time.time(), "data": data}
            cache_file.write_text(json.dumps(payload, indent=1))
        return data

    def _read_cache(self, path: Path, key: dict) -> Optional[dict]:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("key") != key:
            return None
        # A key with an unknown commit on either side cannot be trusted to
        # notice movement, so it also ages out.
        if self.cache_ttl and time.time() - payload.get("created", 0) > self.cache_ttl:
            return None
        return payload.get("data")


def key_digest(key: dict) -> str:
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def best_candidate(data: Optional[dict]) -> Optional[dict]:
    """The first repository with shared history, and within it the closest ref.

    Kept for callers that only need "which candidate"; release-aware choice of
    the ref within a repository is apm.upstream.refs.select_ref.

    Candidates arrive in priority order: what the metadata says the upstream is,
    then that repository's release refs, then the Debian packaging repository.
    The first REPOSITORY with any shared history wins, so a fork that really
    does descend from the project's own repository is not quietly reassigned to
    the packaging repository just because that happens to be fewer commits
    ahead.
    """
    if not data or not data.get("arcos_ok"):
        return None
    shared = [c for c in data.get("candidates", []) if c.get("shared")
              and not c.get("error")]
    if not shared:
        return None
    winner = first_shared_repository(data)
    return min(
        [c for c in shared if c["url"] == winner],
        key=lambda c: c.get("behind", 1 << 30),
    )


def first_shared_repository(data: Optional[dict]) -> Optional[str]:
    """In candidate order, the first repository any of whose refs share history."""
    if not data:
        return None
    order: List[str] = []
    for candidate in data.get("candidates", []):
        if candidate["url"] not in order:
            order.append(candidate["url"])
    shared = {c["url"] for c in data.get("candidates", [])
              if c.get("shared") and not c.get("error")}
    return next((url for url in order if url in shared), None)
