"""Preview and cherry-pick, against real git repositories.

The safety properties matter more than the happy path: a preview must leave
nothing behind, a conflict must stop rather than be resolved, and the target
branch must never be written to.
"""

from __future__ import annotations

import shutil

import pytest

from apm.domain.enums import ErrorCode, PreviewOutcome
from apm.domain.models import PatchSelection
from apm.gitio.transport import Transports
from apm.gitio.workspaces import WorkspaceManager
from apm.services.patch_service import PatchError, PatchService

import gitfixtures as fx

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required"
)


@pytest.fixture
def world(tmp_path):
    upstream = fx.init(tmp_path / "upstream")
    shas = {}
    for name in "ABC":
        shas[name] = fx.commit(upstream, f"{name}.txt", f"{name}\n", f"commit {name}")
    arcos = fx.clone(upstream, tmp_path / "arcos")
    shas["D"] = fx.commit(upstream, "shared.txt", "upstream D\n", "commit D")
    shas["E"] = fx.commit(upstream, "E.txt", "E\n", "commit E")
    return {"upstream": upstream, "arcos": arcos, "shas": shas, "root": tmp_path}


def _service(tmp_path) -> PatchService:
    return PatchService(
        WorkspaceManager(
            Transports(backend="local"),
            root=str(tmp_path / "workspaces"),
            private_url_hint="file:///local",
        )
    )


def _selection(world, shas) -> PatchSelection:
    return PatchSelection(
        package="demo", debian_release="bookworm", arcos_branch="main",
        upstream_repository=str(world["upstream"]), upstream_ref="main",
        shas=shas,
    )


def test_clean_preview_lists_the_files_it_would_change(world):
    preview = _service(world["root"]).preview(
        _selection(world, [world["shas"]["D"], world["shas"]["E"]]),
        str(world["arcos"]),
    )
    assert preview.outcome is PreviewOutcome.CLEAN
    assert preview.applied == [world["shas"]["D"], world["shas"]["E"]]
    assert {c.path for c in preview.files_changed} == {"shared.txt", "E.txt"}


def test_preview_does_not_modify_the_target_branch(world):
    before = fx.git(world["arcos"], "rev-parse", "HEAD")
    _service(world["root"]).preview(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"])
    )
    assert fx.git(world["arcos"], "rev-parse", "HEAD") == before
    assert fx.git(world["arcos"], "status", "--porcelain") == ""


def test_selection_is_applied_oldest_first_whatever_order_was_clicked(world):
    """Order must be topological, not by date.

    These fixture commits are made in the same second, so a date sort orders
    them arbitrarily - which reversed a pair of patches and manufactured a
    conflict the series did not contain.
    """
    preview = _service(world["root"]).preview(
        _selection(world, [world["shas"]["E"], world["shas"]["D"]]),
        str(world["arcos"]),
    )
    assert preview.outcome is PreviewOutcome.CLEAN
    assert preview.order == [world["shas"]["D"], world["shas"]["E"]]


def test_conflict_is_reported_and_never_resolved(world):
    """ARCoS edits the same file, so D cannot apply cleanly."""
    fx.commit(world["arcos"], "shared.txt", "arcos version\n", "arcos edits shared")

    preview = _service(world["root"]).preview(
        _selection(world, [world["shas"]["D"], world["shas"]["E"]]),
        str(world["arcos"]),
    )

    assert preview.outcome is PreviewOutcome.CONFLICT
    assert preview.failed_sha == world["shas"]["D"]
    assert "shared.txt" in preview.conflicts
    assert preview.applied == []


def test_empty_selection_is_refused_not_guessed(world):
    preview = _service(world["root"]).preview(
        _selection(world, []), str(world["arcos"])
    )
    assert preview.outcome is PreviewOutcome.EMPTY


def test_cherry_pick_creates_a_new_branch_and_leaves_the_target_alone(world):
    service = _service(world["root"])
    result = service.cherry_pick(
        _selection(world, [world["shas"]["D"], world["shas"]["E"]]),
        str(world["arcos"]), branch_name="upstream/demo/test",
    )
    assert result.outcome is PreviewOutcome.CLEAN
    assert result.new_branch == "upstream/demo/test"
    assert result.applied == [world["shas"]["D"], world["shas"]["E"]]
    assert result.pushed is False
    # The source branch in the ARCoS repository is untouched.
    assert fx.git(world["arcos"], "rev-parse", "main") == fx.git(
        world["arcos"], "rev-parse", "HEAD"
    )


def test_cherry_pick_refuses_to_target_the_tracked_branch(world):
    with pytest.raises(PatchError) as excinfo:
        _service(world["root"]).cherry_pick(
            _selection(world, [world["shas"]["D"]]), str(world["arcos"]),
            branch_name="main",
        )
    assert excinfo.value.code is ErrorCode.INVALID_REQUEST


def test_cherry_pick_with_push_writes_the_branch_to_the_repository(world):
    """Push to a bare clone, which is what a real remote looks like."""
    bare = world["root"] / "arcos-bare.git"
    fx.git(world["root"], "clone", "-q", "--bare", str(world["arcos"]), str(bare))

    result = _service(world["root"]).cherry_pick(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"]),
        branch_name="upstream/demo/pushed", push=True, push_url=str(bare),
    )
    assert result.pushed is True
    assert "upstream/demo/pushed" in fx.git(bare, "branch", "--list", "upstream/*")


def test_conflicting_cherry_pick_pushes_nothing(world):
    fx.commit(world["arcos"], "shared.txt", "arcos version\n", "arcos edits shared")
    bare = world["root"] / "arcos-bare2.git"
    fx.git(world["root"], "clone", "-q", "--bare", str(world["arcos"]), str(bare))

    result = _service(world["root"]).cherry_pick(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"]),
        branch_name="upstream/demo/conflict", push=True, push_url=str(bare),
    )
    assert result.outcome is PreviewOutcome.CONFLICT
    assert result.pushed is False
    assert fx.git(bare, "branch", "--list", "upstream/*") == ""


def test_missing_base_branch_is_reported(world):
    with pytest.raises(PatchError) as excinfo:
        _service(world["root"]).cherry_pick(
            _selection(world, [world["shas"]["D"]]), str(world["arcos"]),
            branch_name="upstream/demo/x", base_ref="no-such-branch",
        )
    assert excinfo.value.code is ErrorCode.BRANCH_NOT_FOUND
