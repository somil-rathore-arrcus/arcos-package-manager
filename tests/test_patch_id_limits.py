"""Backport detection must degrade, never hang.

patch-id is the most expensive part of a comparison: it materialises every diff.
When it cannot finish, the comparison still has to return - with backport
detection marked unavailable and the reason stated, so nobody reads a partial
result as a complete one.
"""

from __future__ import annotations

import pytest

from apm.gitio.workspace import GitWorkspaceError
from apm.services.patch_id_service import PatchIdService


class FakeWorkspace:
    def __init__(self, upstream=None, arcos=None, raises=None):
        self.upstream = upstream or {}
        self.arcos = arcos or {}
        self.raises = raises
        self.calls = 0

    def patch_ids(self, rev_range, limit=None, timeout=None):
        self.calls += 1
        if self.raises:
            raise self.raises
        return self.upstream if self.calls == 1 else self.arcos


def test_matches_an_upstream_commit_to_its_backport():
    workspace = FakeWorkspace(
        upstream={"upstream1": "patchA", "upstream2": "patchB"},
        arcos={"arcos1": "patchA"},
    )
    result = PatchIdService().compare(workspace, "base", "up", "arcos")

    assert result.available is True
    assert result.equivalent == {"upstream1": "arcos1"}
    assert result.is_backported("upstream1") is True
    assert result.is_backported("upstream2") is False


def test_without_a_merge_base_equivalence_is_undefined_not_empty():
    result = PatchIdService().compare(FakeWorkspace(), "", "up", "arcos")
    assert result.available is False
    assert "no common ancestor" in result.reason


def test_a_timeout_is_reported_rather_than_raised():
    workspace = FakeWorkspace(
        raises=GitWorkspaceError("patch-id did not complete", timed_out=True)
    )
    result = PatchIdService(timeout=1).compare(workspace, "base", "up", "arcos")

    assert result.available is False
    assert "patch-id could not be computed" in result.reason
    assert result.equivalent == {}


def test_an_enormous_range_is_skipped_before_any_diffing():
    """A kernel-sized comparison must not spend minutes materialising diffs."""
    workspace = FakeWorkspace(upstream={"a": "p"}, arcos={"b": "p"})
    service = PatchIdService(max_total_commits=100)

    result = service.compare(workspace, "base", "up", "arcos", total_commits=50_000)

    assert result.available is False
    assert "beyond the 100" in result.reason
    assert workspace.calls == 0, "must not diff anything once over the limit"


def test_a_range_within_the_limit_still_runs():
    workspace = FakeWorkspace(upstream={"a": "p"}, arcos={"b": "p"})
    result = PatchIdService(max_total_commits=100).compare(
        workspace, "base", "up", "arcos", total_commits=99
    )
    assert result.available is True
    assert result.equivalent == {"a": "b"}


def test_nothing_on_the_arcos_side_means_no_backports_but_still_available():
    workspace = FakeWorkspace(upstream={"a": "p"}, arcos={})
    result = PatchIdService().compare(workspace, "base", "up", "arcos")
    assert result.available is True
    assert result.equivalent == {}


def test_one_arcos_commit_is_not_claimed_by_two_upstream_commits():
    workspace = FakeWorkspace(
        upstream={"u1": "same", "u2": "same"}, arcos={"a1": "same"},
    )
    result = PatchIdService().compare(workspace, "base", "up", "arcos")
    assert result.equivalent == {"u1": "a1", "u2": "a1"}
