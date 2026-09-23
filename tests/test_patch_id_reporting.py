"""A backport search that did not run must never read as one that found nothing.

These are regressions for a real failure. `git log -p | git patch-id` was run as
a shell pipeline, so the pipeline's exit status was patch-id's - zero - even when
`git log` had died on a missing blob. The mapping came back empty, and an empty
mapping was indistinguishable from "no backports exist". The comparison then
reported `already_backported: 0` with no warning at all, while 142 upstream
commits carried no patch id between them.
"""

from __future__ import annotations

import pytest

from apm.gitio.workspace import GitWorkspace, GitWorkspaceError
from apm.services.patch_id_service import PatchIdService


class FakeResult:
    def __init__(self, ok=True, stdout="", stderr=""):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr

    @property
    def text(self):
        return self.stdout.strip()


class RecordingTransport:
    """Captures the shell commands a workspace issues."""

    name = "fake"

    def __init__(self, result=None):
        self.commands = []
        self.result = result or FakeResult()

    def shell(self, command, timeout=None):
        self.commands.append(command)
        return self.result


def _workspace(result=None):
    transport = RecordingTransport(result)
    return GitWorkspace(transport, "/tmp/ws"), transport


def test_patch_id_does_not_hide_git_log_behind_a_pipeline():
    """The exit status must be git log's, not the last command in a pipe."""
    workspace, transport = _workspace()
    workspace.patch_ids("base..head")

    command = transport.commands[-1]
    assert "git log" in command and "git patch-id" in command
    assert "| git patch-id" not in command, (
        "git log must not be piped into patch-id: a pipeline reports only the "
        "last command's exit status, so a failed git log reads as success"
    )
    # Staged through a file instead, so git log's own exit status survives.
    assert '> "$TMP"' in command and '< "$TMP"' in command
    assert 'exit "$rc"' in command


def test_a_failed_patch_id_raises_instead_of_returning_nothing():
    workspace, _ = _workspace(
        FakeResult(ok=False, stderr="fatal: unable to read b71512d3")
    )
    with pytest.raises(GitWorkspaceError):
        workspace.patch_ids("base..head")


def test_patch_id_output_is_parsed_as_patchid_then_sha():
    workspace, _ = _workspace(
        FakeResult(stdout="aaaa1111 cccc3333\nbbbb2222 dddd4444\n")
    )
    assert workspace.patch_ids("base..head") == {
        "cccc3333": "aaaa1111", "dddd4444": "bbbb2222",
    }


class FakeWorkspace:
    def __init__(self, upstream=None, arcos=None):
        self.upstream = upstream or {}
        self.arcos = arcos or {}
        self.calls = 0

    def patch_ids(self, rev_range, limit=None, timeout=None):
        self.calls += 1
        return self.upstream if self.calls == 1 else self.arcos


def test_no_patch_ids_for_a_range_that_has_commits_is_a_failure_to_look():
    """The bug in one assertion: 142 commits, zero patch ids, reported as fine."""
    result = PatchIdService().compare(
        FakeWorkspace(upstream={}, arcos={"a": "p"}),
        "base", "up", "arcos", expected_upstream=142, expected_arcos=272,
    )
    assert result.available is False
    assert "142 commits" in result.reason
    assert "could not be read" in result.reason


def test_the_arcos_side_is_checked_too():
    result = PatchIdService().compare(
        FakeWorkspace(upstream={"u": "p"}, arcos={}),
        "base", "up", "arcos", expected_upstream=1, expected_arcos=272,
    )
    assert result.available is False
    assert "ARCoS side" in result.reason


def test_an_empty_range_with_no_patch_ids_is_still_a_measured_zero():
    """Nothing to diff is not the same as failing to diff."""
    result = PatchIdService().compare(
        FakeWorkspace(upstream={}, arcos={}),
        "base", "up", "arcos", expected_upstream=0, expected_arcos=0,
    )
    assert result.available is True
    assert result.equivalent == {}
