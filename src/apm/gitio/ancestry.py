"""Prove which upstream an ARCoS fork actually came from.

Resolution by metadata produces a plausible upstream. Only shared git history
proves it, and the difference is not academic: ARCoS forked the Debian PACKAGING
repository for lttng-tools, liburcu, lttng-ust and babeltrace, so comparing them
against the projects' own repositories finds no common ancestor and any "behind"
count would be meaningless. Conversely openssl really does fork upstream GitHub.

There is no rule that predicts which - it has to be measured, so this runs
git merge-base against each candidate and lets the answer decide.
"""

from __future__ import annotations

import json
import logging
import shlex
from pathlib import Path
from typing import Optional

from .transport import Transports

log = logging.getLogger(__name__)

PROBE = Path(__file__).with_name("ancestry_probe.sh")
REMOTE_PROBE = "/tmp/apm-ancestry-probe.sh"


class AncestryChecker:
    def __init__(self, transports: Transports, cache_dir: Path,
                 enabled: bool = True, timeout: int = 600) -> None:
        self.transports = transports
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        # Bounded: a probe that cannot finish in this long is a failure to
        # report, not something to keep a request waiting on.
        self.timeout = timeout
        self._installed = False

    def _install(self, git) -> bool:
        """Copy the probe to wherever the git commands run."""
        if self._installed:
            return True
        script = PROBE.read_text(encoding="utf-8")
        result = git.shell(
            f"cat > {REMOTE_PROBE} <<'APM_PROBE_EOF'\n{script}\nAPM_PROBE_EOF\n"
            f"chmod +x {REMOTE_PROBE}",
            timeout=120,
        )
        self._installed = result.ok
        if not result.ok:
            log.warning("could not install ancestry probe: %s", result.stderr[:200])
        return result.ok

    def check(self, package: str, release: str, arcos_url: str, arcos_ref: str,
              candidates: list, refresh: bool = False) -> Optional[dict]:
        """Return the probe result, from cache unless refresh is asked for."""
        if not self.enabled or not candidates:
            return None

        key = f"{package}__{release}"
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists() and not refresh:
            try:
                return json.loads(cache_file.read_text())
            except ValueError:
                pass

        git = self.transports.for_url(arcos_url)
        if not self._install(git):
            return None

        args = [f"/tmp/apm-anc/{key}", arcos_url, arcos_ref]
        for url, ref in candidates:
            args.extend([url, ref or "HEAD"])
        command = f"{REMOTE_PROBE} " + " ".join(shlex.quote(a) for a in args)

        result = git.shell(command, timeout=self.timeout)
        if not result.ok or not result.stdout.strip():
            log.warning(
                "ancestry probe failed for %s/%s: %s",
                package, release, (result.stderr or "")[:200],
            )
            return None
        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except ValueError as exc:
            log.warning("ancestry probe returned unparseable output for %s: %s",
                        package, exc)
            return None

        # A fork that could not be fetched is a transient failure, not a finding.
        # Caching it would make one flaky SSH round trip permanent.
        if data.get("arcos_ok"):
            cache_file.write_text(json.dumps(data, indent=1))
        else:
            log.warning(
                "%s/%s: the ARCoS fork could not be fetched; not cached, will "
                "retry on the next run", package, release,
            )
        return data


def best_candidate(data: Optional[dict]) -> Optional[dict]:
    """The best shared-history candidate, respecting the order they were offered.

    Candidates arrive in priority order: what the metadata says the upstream is,
    then that repository's maintenance branches, then the Debian packaging
    repository. The first REPOSITORY with any shared history wins, so a fork that
    really does descend from the project's own repository is not quietly
    reassigned to the packaging repository just because that happens to be fewer
    commits ahead.

    Within the winning repository, fewest commits behind wins - that is how the
    right maintenance series is picked out of several that all share history.
    """
    if not data or not data.get("arcos_ok"):
        return None
    shared = [c for c in data.get("candidates", []) if c.get("shared")]
    if not shared:
        return None

    # First-seen repository order, taken from the full candidate list so that
    # priority survives even when earlier candidates were unreachable.
    order = []
    for candidate in data.get("candidates", []):
        if candidate["url"] not in order:
            order.append(candidate["url"])

    winner = min(shared, key=lambda c: order.index(c["url"]))["url"]
    return min(
        [c for c in shared if c["url"] == winner],
        key=lambda c: c.get("behind", 1 << 30),
    )
