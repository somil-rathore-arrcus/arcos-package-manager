"""Which repositories go over the access bridge is configuration, not code.

Nothing here may depend on a particular organisation name or host: a deployment
with direct access must be able to turn the bridge off, and one entirely behind a
bridge must be able to send everything over it.
"""

import pytest

from apm.gitio.transport import SshConfig, Transports

BRIDGE = SshConfig(host="build-host", user="builder")
PATTERNS = [r"github\.com[:/]+arrcus/"]

PRIVATE = [
    "ssh://git@github.com/Arrcus/linux.git",
    "git@github.com:Arrcus/pyrad.git",
    "https://github.com/Arrcus/arrcus_rel",
    # The manifest spells the owner in both cases.
    "git@github.com:arrcus/ONL.git",
]
PUBLIC = [
    "https://github.com/lldpd/lldpd.git",
    "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git",
    "https://salsa.debian.org/debian/lldpd.git",
    "https://github.com/notarrcus/thing.git",
    "",
]


def _auto():
    return Transports(ssh_config=BRIDGE, backend="auto", private_patterns=PATTERNS)


@pytest.mark.parametrize("url", PRIVATE)
def test_configured_private_repositories_use_the_bridge(url):
    assert _auto().for_url(url).name == "ssh"


@pytest.mark.parametrize("url", PUBLIC)
def test_everything_else_runs_locally(url):
    assert _auto().for_url(url).name == "local"


@pytest.mark.parametrize("url", PRIVATE + PUBLIC)
def test_local_backend_forces_everything_local(url):
    transports = Transports(
        ssh_config=BRIDGE, backend="local", private_patterns=PATTERNS
    )
    assert transports.for_url(url).name == "local"


@pytest.mark.parametrize("url", PRIVATE + PUBLIC)
def test_ssh_backend_forces_everything_over_the_bridge(url):
    transports = Transports(
        ssh_config=BRIDGE, backend="ssh", private_patterns=PATTERNS
    )
    assert transports.for_url(url).name == "ssh"


def test_without_a_configured_bridge_auto_stays_local():
    """A laptop with no bridge configured must not fail; it just runs locally."""
    transports = Transports(backend="auto", private_patterns=PATTERNS)
    assert transports.for_url(PRIVATE[0]).name == "local"


def test_patterns_are_configurable_not_baked_in():
    transports = Transports(
        ssh_config=BRIDGE, backend="auto",
        private_patterns=[r"gitlab\.internal/"],
    )
    assert transports.for_url("https://gitlab.internal/team/app.git").name == "ssh"
    assert transports.for_url(PRIVATE[0]).name == "local"


def test_ssh_command_carries_proxy_jump_and_identity():
    config = SshConfig(
        host="build", user="ci", port=2222,
        identity_file="/keys/id", proxy_jump="jump@bastion",
    )
    argv = Transports(ssh_config=config, backend="ssh").ssh()._command("git status", 60)
    assert "-J" in argv and "jump@bastion" in argv
    assert "-i" in argv and "/keys/id" in argv
    assert "-p" in argv and "2222" in argv
    assert argv[-2] == "ci@build"


def test_no_password_means_batch_mode():
    argv = Transports(
        ssh_config=SshConfig(host="h"), backend="ssh"
    ).ssh()._command("git status", 60)
    assert "BatchMode=yes" in argv
