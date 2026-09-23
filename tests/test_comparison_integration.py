"""The comparison engine, against real git repositories.

Covers the classification rules the whole tool depends on: an ARCoS-specific
commit is not a missing upstream patch, and a backport applied under a different
SHA is not missing either.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from apm.domain.enums import CommitClass, Criticality, ErrorCode, ResolutionStatus
from apm.domain.models import Repository, UpstreamResolution
from apm.gitio.transport import Transports
from apm.gitio.workspaces import WorkspaceManager
from apm.services.comparison_service import ComparisonError, ComparisonService

import gitfixtures as fx

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required"
)


@pytest.fixture
def world(tmp_path):
    """Upstream A-B-C-D-E; ARCoS forked at C."""
    upstream = fx.init(tmp_path / "upstream")
    shas = {}
    for name in "ABC":
        shas[name] = fx.commit(upstream, f"{name}.txt", f"{name}\n", f"commit {name}")
    arcos = fx.clone(upstream, tmp_path / "arcos")
    shas["D"] = fx.commit(
        upstream, "D.txt", "D\n", "commit D",
        "This addresses CVE-2026-12345.\n",
    )
    shas["E"] = fx.commit(
        upstream, "E.txt", "E\n", "commit E",
        "Cc: stable@vger.kernel.org\n",
    )
    return {"upstream": upstream, "arcos": arcos, "shas": shas, "root": tmp_path}


def _service(tmp_path) -> ComparisonService:
    manager = WorkspaceManager(
        Transports(backend="local"),
        root=str(tmp_path / "workspaces"),
        private_url_hint="file:///local",
    )
    return ComparisonService(manager)


def _resolution(world, upstream_ref="main", arcos_ref=None) -> UpstreamResolution:
    return UpstreamResolution(
        package="demo", debian_release="bookworm",
        status=ResolutionStatus.VERIFIED,
        arcos_repository=str(world["arcos"]),
        arcos_branch="main",
        arcos_commit=arcos_ref,
        upstream_repository=Repository(url=str(world["upstream"])),
        upstream_ref=upstream_ref,
    )


def test_normal_upstream_reports_only_the_missing_commits(world):
    result = _service(world["root"]).compare(_resolution(world))
    assert [c.subject for c in result.missing_upstream] == ["commit D", "commit E"]
    assert result.summary.missing_upstream == 2
    assert result.summary.arcos_only == 0
    assert result.summary.merge_base == world["shas"]["C"]


def test_arcos_specific_commits_are_not_counted_as_missing(world):
    """The central rule: local work is not a backlog of upstream patches."""
    fx.commit(world["arcos"], "X.txt", "X\n", "arcos X")
    fx.commit(world["arcos"], "Y.txt", "Y\n", "arcos Y")

    result = _service(world["root"]).compare(_resolution(world))

    assert [c.subject for c in result.missing_upstream] == ["commit D", "commit E"]
    assert [c.subject for c in result.arcos_only] == ["arcos X", "arcos Y"]
    assert result.summary.arcos_only == 2
    assert all(
        c.classification is CommitClass.MISSING_UPSTREAM
        for c in result.missing_upstream
    )
    assert all(
        c.classification is CommitClass.ARCOS_ONLY for c in result.arcos_only
    )
    # And no ARCoS commit leaked into the missing list.
    assert not {"arcos X", "arcos Y"} & {c.subject for c in result.missing_upstream}


def test_a_backport_under_a_different_sha_is_not_missing(world):
    """Cherry-pick D onto a diverged ARCoS branch.

    ARCoS commits X first, so D lands on a different parent and necessarily gets
    a different SHA - which is what every real backport looks like, and what a
    SHA comparison would wrongly report as still missing.
    """
    fx.commit(world["arcos"], "X.txt", "X\n", "arcos X")
    fx.git(world["arcos"], "fetch", "-q", "origin")
    fx.git(world["arcos"], "cherry-pick", world["shas"]["D"])
    arcos_d = fx.git(world["arcos"], "rev-parse", "HEAD")
    assert arcos_d != world["shas"]["D"]

    result = _service(world["root"]).compare(_resolution(world))

    assert [c.subject for c in result.missing_upstream] == ["commit E"]
    assert [c.subject for c in result.already_backported] == ["commit D"]
    backported = result.already_backported[0]
    assert backported.classification is CommitClass.ALREADY_BACKPORTED
    assert backported.patch.equivalent_sha == arcos_d
    assert result.summary.already_backported == 1
    # The backported commit must not also be counted as ARCoS-specific work.
    assert [c.subject for c in result.arcos_only] == ["arcos X"]


def test_criticality_is_attached_from_evidence(world):
    result = _service(world["root"]).compare(_resolution(world))
    by_subject = {c.subject: c for c in result.missing_upstream}

    d = by_subject["commit D"]
    assert d.criticality.level is Criticality.CRITICAL
    assert d.criticality.cve_ids == ["CVE-2026-12345"]

    e = by_subject["commit E"]
    assert e.criticality.level is Criticality.STABLE_RELEVANT
    assert e.criticality.cc_stable is True

    assert result.summary.critical == 1
    assert result.summary.stable_relevant == 1


def test_every_missing_commit_is_counted_in_exactly_one_criticality_tile(world):
    """The four levels must add up to the missing count.

    NORMAL was missing from the summary, so 16 of iputils' 142 missing commits
    appeared in no tile and could not be reached by any filter.
    """
    fx.commit(world["upstream"], "F.txt", "F\n", "commit F",
              "Fixes: 1234567890ab (\"an earlier commit\")\n")
    fx.commit(world["upstream"], "G.txt", "G\n", "commit G", "Routine tidy-up.\n")
    summary = _service(world["root"]).compare(_resolution(world)).summary

    assert summary.normal == 1, "a Fixes: trailer is NORMAL, not UNKNOWN"
    assert (
        summary.critical + summary.stable_relevant + summary.normal
        + summary.unknown_criticality
    ) == summary.missing_upstream


def test_commit_with_no_evidence_is_unknown(world):
    fx.commit(world["upstream"], "F.txt", "F\n", "commit F", "Routine tidy-up.\n")
    result = _service(world["root"]).compare(_resolution(world))
    f = next(c for c in result.missing_upstream if c.subject == "commit F")
    assert f.criticality.level is Criticality.UNKNOWN
    assert result.summary.unknown_criticality == 1


def test_up_to_date_fork_reports_nothing_missing(world):
    fx.git(world["arcos"], "fetch", "-q", "origin")
    fx.git(world["arcos"], "merge", "-q", "--ff-only", "origin/main")
    result = _service(world["root"]).compare(_resolution(world))
    assert result.missing_upstream == []
    assert result.summary.missing_upstream == 0


def test_unrelated_repository_has_no_common_ancestor(world, tmp_path):
    """An imported tree, not a fork. Saying so beats inventing a backlog."""
    stranger = fx.init(tmp_path / "stranger")
    fx.commit(stranger, "z.txt", "z\n", "unrelated root")

    resolution = _resolution(world)
    resolution.upstream_repository = Repository(url=str(stranger))

    with pytest.raises(ComparisonError) as excinfo:
        _service(world["root"]).compare(resolution)
    assert excinfo.value.code is ErrorCode.NO_COMMON_ANCESTOR


def test_missing_upstream_branch_is_reported_clearly(world):
    with pytest.raises(ComparisonError) as excinfo:
        _service(world["root"]).compare(_resolution(world, upstream_ref="nope"))
    assert excinfo.value.code is ErrorCode.BRANCH_NOT_FOUND


def test_unresolved_upstream_is_refused_before_touching_git(world):
    resolution = _resolution(world)
    resolution.status = ResolutionStatus.NO_UPSTREAM
    with pytest.raises(ComparisonError) as excinfo:
        _service(world["root"]).compare(resolution)
    assert excinfo.value.code is ErrorCode.UPSTREAM_NOT_RESOLVED


def test_comparison_uses_the_pinned_commit_when_one_is_given(world):
    """What ships is the pinned commit, even when the branch has moved past it."""
    pinned = world["shas"]["C"]
    fx.commit(world["arcos"], "later.txt", "later\n", "arcos moved on")

    result = _service(world["root"]).compare(_resolution(world, arcos_ref=pinned))

    assert result.arcos_commit == pinned
    assert result.arcos_only == []
