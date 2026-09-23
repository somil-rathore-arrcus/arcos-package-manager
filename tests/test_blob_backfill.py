"""Blobs have to actually arrive before a diff can be read.

Both halves of this were wrong at once, and the symptom was silent:

  * `git remote remove` on every fetch deleted remote.<name>.promisor, so the
    workspace could not lazily fetch the blobs a blobless fetch had skipped;
  * a second fetch without --filter backfills nothing, because the ref has not
    moved and git reports it up to date. --refetch is needed - and a stored
    partialclonefilter is applied to the refetch too, so it must go first.
"""

from __future__ import annotations

from apm.gitio.workspace import GitWorkspace


class FakeResult:
    def __init__(self, ok=True, stdout="", stderr=""):
        self.ok, self.stdout, self.stderr = ok, stdout, stderr

    @property
    def text(self):
        return self.stdout.strip()


class ScriptedTransport:
    name = "fake"

    def __init__(self, config_value=""):
        self.commands = []
        self.config_value = config_value

    def shell(self, command, timeout=None):
        self.commands.append(command)
        if "config --local --get apm.blobs" in command:
            return FakeResult(ok=bool(self.config_value), stdout=self.config_value)
        return FakeResult(ok=True)


def _workspace(config_value=""):
    transport = ScriptedTransport(config_value)
    return GitWorkspace(transport, "/tmp/ws"), transport


def test_setting_a_remote_keeps_its_partial_clone_configuration():
    workspace, transport = _workspace()
    workspace.set_remote("arcos", "ssh://git@github.com/Arrcus/x.git")

    command = transport.commands[-1]
    assert "remote remove" not in command, (
        "removing the remote deletes its promisor config, which is what makes "
        "missing blobs unfetchable"
    )
    assert "remote set-url" in command
    assert "remote add" in command, "a remote that does not exist yet still needs adding"


def test_backfill_drops_the_stored_filter_before_refetching():
    workspace, transport = _workspace()
    workspace.backfill_blobs("upstream", "https://example.invalid/x.git", "master",
                             "a" * 40)

    fetch = next(c for c in transport.commands if "git fetch" in c)
    assert "--refetch" in fetch
    assert "partialclonefilter" in fetch and "--unset" in fetch
    assert "--filter=blob:none" not in fetch


def test_a_successful_backfill_is_remembered_against_the_head_commit():
    workspace, transport = _workspace()
    workspace.backfill_blobs("upstream", "https://example.invalid/x.git", "master",
                             "b" * 40)
    assert any(
        "config --local apm.blobs.upstream" in c and "b" * 40 in c
        for c in transport.commands
    )


def test_backfill_is_skipped_when_the_same_head_is_already_complete():
    """Repeat comparisons must not re-download every object each time."""
    head = "c" * 40
    workspace, transport = _workspace(config_value=head)
    assert workspace.backfill_blobs("upstream", "https://x.invalid/x.git",
                                    "master", head) is None
    assert not any("git fetch" in c for c in transport.commands)


def test_a_moved_head_backfills_again():
    workspace, transport = _workspace(config_value="c" * 40)
    result = workspace.backfill_blobs("upstream", "https://x.invalid/x.git",
                                      "master", "d" * 40)
    assert result is not None
    assert any("git fetch" in c and "--refetch" in c for c in transport.commands)
