"""A selection is re-checked against the branches as they are when applied.

The comparison may have been computed against the pinned commit, or an hour
ago. Between selecting and applying, the target branch can move and upstream can
be rewritten. The server re-validates every selected commit, and a cherry-pick
refuses to run on a base nobody previewed.
"""

from __future__ import annotations

import shutil

import pytest

import gitfixtures as fx
from apm.domain.enums import ErrorCode, PreviewOutcome
from apm.domain.models import PatchSelection
from apm.gitio.transport import Transports
from apm.gitio.workspaces import WorkspaceManager
from apm.services.patch_service import PatchError, PatchService

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


@pytest.fixture
def world(tmp_path):
    up = fx.init(tmp_path / "upstream")
    for name in "ABC":
        fx.commit(up, f"{name}.txt", f"{name}\n", f"commit {name}")
    arcos = fx.clone(up, tmp_path / "arcos")
    shas = {"C": fx.git(arcos, "rev-parse", "HEAD")}
    shas["D"] = fx.commit(up, "D.txt", "D\n", "commit D")
    shas["E"] = fx.commit(up, "E.txt", "E\n", "commit E")
    return {"up": up, "arcos": arcos, "shas": shas, "root": tmp_path}


def _service(root) -> PatchService:
    return PatchService(WorkspaceManager(
        Transports(backend="local"), root=str(root / "ws"),
        private_url_hint="file:///x"))


def _selection(world, shas, **extra) -> PatchSelection:
    return PatchSelection(
        package="demo", debian_release="bookworm", arcos_branch="main",
        upstream_repository=str(world["up"]), upstream_ref="main", shas=shas,
        comparison_arcos_commit=world["shas"]["C"], **extra)


def test_a_valid_selection_previews_with_the_base_it_used(world):
    preview = _service(world["root"]).preview(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"]))
    assert preview.outcome is PreviewOutcome.CLEAN
    assert preview.validated is True
    assert preview.base_sha == world["shas"]["C"]
    assert preview.base_moved is False


def test_a_commit_the_target_branch_already_has_is_refused(world):
    """The target moved between comparison and apply, and now contains D."""
    fx.git(world["arcos"], "pull", "-q", "--ff-only", str(world["up"]), "main")
    with pytest.raises(PatchError) as raised:
        _service(world["root"]).preview(
            _selection(world, [world["shas"]["D"]]), str(world["arcos"]))
    assert raised.value.code is ErrorCode.STALE_SELECTION
    assert "already in main" in str(raised.value)


def test_a_moved_base_is_revalidated_and_reported(world):
    """The target moved, but not past the selection: allowed, and said."""
    fx.commit(world["arcos"], "X.txt", "X\n", "arcos X")
    preview = _service(world["root"]).preview(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"]))
    assert preview.outcome is PreviewOutcome.CLEAN
    assert preview.base_moved is True
    assert preview.base_sha != world["shas"]["C"]
    assert any("re-validated" in w for w in preview.warnings)


def test_a_rewritten_upstream_makes_the_selection_stale(world):
    old_e = world["shas"]["E"]
    fx.git(world["up"], "reset", "-q", "--hard", world["shas"]["D"])
    fx.commit(world["up"], "E2.txt", "E2\n", "commit E rewritten")
    with pytest.raises(PatchError) as raised:
        _service(world["root"]).preview(_selection(world, [old_e]), str(world["arcos"]))
    assert raised.value.code is ErrorCode.STALE_SELECTION


def test_a_cherry_pick_onto_a_base_that_moved_after_preview_is_refused(world):
    service = _service(world["root"])
    preview = service.preview(_selection(world, [world["shas"]["D"]]),
                              str(world["arcos"]))
    fx.commit(world["arcos"], "late.txt", "late\n", "landed after the preview")

    with pytest.raises(PatchError) as raised:
        service.cherry_pick(
            _selection(world, [world["shas"]["D"]], expected_base_sha=preview.base_sha),
            str(world["arcos"]), branch_name="upstream/demo/x")
    assert raised.value.code is ErrorCode.BASE_MOVED


def test_a_cherry_pick_on_the_previewed_base_applies_to_a_new_branch(world):
    service = _service(world["root"])
    preview = service.preview(_selection(world, [world["shas"]["D"]]),
                              str(world["arcos"]))
    result = service.cherry_pick(
        _selection(world, [world["shas"]["D"]], expected_base_sha=preview.base_sha),
        str(world["arcos"]), branch_name="upstream/demo/x")
    assert result.outcome is PreviewOutcome.CLEAN
    assert result.base_sha == preview.base_sha
    assert fx.git(world["arcos"], "rev-parse", "main") == world["shas"]["C"]


def test_an_unknown_sha_is_refused_not_ignored(world):
    with pytest.raises(PatchError) as raised:
        _service(world["root"]).preview(
            _selection(world, ["0123456789abcdef0123456789abcdef01234567"]),
            str(world["arcos"]))
    assert raised.value.code is ErrorCode.STALE_SELECTION


def test_there_is_no_force_push_anywhere_in_the_patch_path():
    import inspect

    from apm.gitio.workspace import GitWorkspace

    assert "force" not in inspect.signature(GitWorkspace.push).parameters
    source = inspect.getsource(GitWorkspace)
    assert "--force" not in source and "+refs/heads" not in source.split(
        "def push(")[1].split("def ")[0]
