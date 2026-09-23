"""Where git commands actually run.

The machine driving this tool does not necessarily have access to the private
repositories it needs to read. A separate host may hold that access, reached
directly or through a jump host. None of that belongs in application logic, so
this module turns it into one decision - "does this URL need the bridge?" - made
from configuration, and every call site just asks for a transport by URL.

Supported backends:

  local   run git here. Correct when this host already has the access.
  ssh     run git on a configured host. Key-based auth, an ssh_config alias, a
          ProxyJump chain or (discouraged) a password all work, because the ssh
          client resolves them - this code only assembles the command.

No host, user, key or organisation name is hard-coded. Which repositories need
the bridge is itself configuration.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger(__name__)


class GitError(RuntimeError):
    pass


@dataclass
class GitResult:
    ok: bool
    stdout: str
    stderr: str

    @property
    def text(self) -> str:
        return self.stdout.strip()


@dataclass
class SshConfig:
    """How to reach the host that holds repository access.

    `host` may be an ssh_config alias, in which case user, port, key and any
    ProxyJump can live in ~/.ssh/config and none of them need to be set here.
    """

    host: str = ""
    user: str = ""
    port: Optional[int] = None
    identity_file: str = ""
    proxy_jump: str = ""
    # Only for hosts that permit nothing better. Never committed.
    password: str = ""
    strict_host_key_checking: str = "accept-new"
    known_hosts_file: str = ""
    connect_timeout: int = 30
    extra_options: List[str] = field(default_factory=list)

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    @property
    def configured(self) -> bool:
        return bool(self.host)


class LocalGit:
    """Run git on this machine."""

    name = "local"

    def __init__(self, timeout: int = 60) -> None:
        self.timeout = timeout

    def run(self, args: List[str], timeout: Optional[int] = None) -> GitResult:
        return self.shell(
            "git " + " ".join(shlex.quote(a) for a in args), timeout=timeout
        )

    def shell(self, command: str, timeout: Optional[int] = None) -> GitResult:
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired:
            return GitResult(False, "", f"timed out after {timeout or self.timeout}s")
        return GitResult(proc.returncode == 0, proc.stdout, proc.stderr)


class SshGit:
    """Run git on a configured host."""

    name = "ssh"

    def __init__(self, config: SshConfig, timeout: int = 60) -> None:
        if not config.configured:
            raise GitError(
                "The ssh git backend is selected but no host is configured. "
                "Set APM_SSH_HOST (see .env.example and docs/deployment.md)."
            )
        self.config = config
        self.timeout = timeout

    def _command(self, remote_command: str, timeout: int) -> List[str]:
        cfg = self.config
        argv: List[str] = []
        if cfg.password:
            # sshpass is a fallback for hosts that allow nothing better; key or
            # agent auth is preferred and needs nothing here.
            argv += ["sshpass", "-p", cfg.password]
        argv += ["ssh", "-o", "BatchMode=" + ("no" if cfg.password else "yes")]
        argv += ["-o", f"StrictHostKeyChecking={cfg.strict_host_key_checking}"]
        argv += ["-o", f"ConnectTimeout={min(cfg.connect_timeout, timeout)}"]
        argv += ["-o", "LogLevel=ERROR"]
        if cfg.known_hosts_file:
            argv += ["-o", f"UserKnownHostsFile={cfg.known_hosts_file}"]
        if cfg.identity_file:
            argv += ["-i", cfg.identity_file, "-o", "IdentitiesOnly=yes"]
        if cfg.port:
            argv += ["-p", str(cfg.port)]
        if cfg.proxy_jump:
            argv += ["-J", cfg.proxy_jump]
        for option in cfg.extra_options:
            argv += ["-o", option]
        argv += [cfg.target, remote_command]
        return argv

    def run(self, args: List[str], timeout: Optional[int] = None) -> GitResult:
        return self.shell(
            "git " + " ".join(shlex.quote(a) for a in args), timeout=timeout
        )

    def shell(self, command: str, timeout: Optional[int] = None) -> GitResult:
        limit = timeout or self.timeout
        try:
            proc = subprocess.run(
                self._command(command, limit),
                capture_output=True,
                text=True,
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            return GitResult(False, "", f"timed out after {limit}s")
        except FileNotFoundError as exc:
            return GitResult(False, "", f"{exc}")
        return GitResult(proc.returncode == 0, proc.stdout, proc.stderr)


class Transports:
    """Chooses where a given repository's git commands run."""

    def __init__(
        self,
        ssh_config: Optional[SshConfig] = None,
        backend: str = "auto",
        private_patterns: Optional[List[str]] = None,
        timeout: int = 60,
    ) -> None:
        self.backend = backend
        self.timeout = timeout
        self.ssh_config = ssh_config or SshConfig()
        self._patterns = [
            re.compile(p, re.IGNORECASE) for p in (private_patterns or [])
        ]
        self.local = LocalGit(timeout=timeout)
        self._ssh: Optional[SshGit] = None

    def matches_private(self, url: str) -> bool:
        """Whether this repository is one the configured bridge exists for."""
        return any(p.search(url or "") for p in self._patterns)

    def ssh(self) -> SshGit:
        if self._ssh is None:
            self._ssh = SshGit(self.ssh_config, timeout=self.timeout)
        return self._ssh

    def for_url(self, url: str):
        """The transport to use for `url`.

        'local' and 'ssh' force one backend for everything, which is what a
        deployment with direct access, or one entirely behind a bridge, wants.
        'auto' sends only the configured private repositories over the bridge,
        so public upstreams stay local and fast.
        """
        if self.backend == "local":
            return self.local
        if self.backend == "ssh":
            return self.ssh()
        if self.ssh_config.configured and self.matches_private(url):
            return self.ssh()
        return self.local

    def describe(self) -> dict:
        return {
            "backend": self.backend,
            "ssh_configured": self.ssh_config.configured,
            "ssh_host": self.ssh_config.host or None,
            "ssh_proxy_jump": self.ssh_config.proxy_jump or None,
            "private_patterns": [p.pattern for p in self._patterns],
        }
