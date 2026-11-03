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
    # git's own advice to resolve and --continue does not apply: the workspace
    # is gone. Its error line stays.
    assert not any("hint:" in w for w in preview.warnings)
    assert any("could not apply" in w for w in preview.warnings)


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


# -- validation ------------------------------------------------------------------
#
# The configured commands run in the patched tree. Nothing configured must read
# as "not checked", never as a pass, and a failing command must stop the push.

def _validating(tmp_path, commands, timeout=60) -> PatchService:
    return PatchService(
        WorkspaceManager(
            Transports(backend="local"),
            root=str(tmp_path / "workspaces"),
            private_url_hint="file:///local",
        ),
        validation_commands=commands, validation_timeout=timeout,
    )


def test_without_validation_commands_nothing_claims_to_have_passed(world):
    preview = _service(world["root"]).preview(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"])
    )
    assert preview.outcome is PreviewOutcome.CLEAN
    assert preview.validation.configured is False
    assert preview.validation.passed is None
    assert "nothing was built or tested" in preview.validation.message


def test_validation_runs_in_the_patched_tree(world):
    """E.txt exists only once E is applied, so passing proves the command saw
    the patched tree, not the target branch."""
    preview = _validating(world["root"], ["test -f E.txt", "grep -q E E.txt"]).preview(
        _selection(world, [world["shas"]["D"], world["shas"]["E"]]), str(world["arcos"])
    )
    assert preview.outcome is PreviewOutcome.CLEAN
    assert preview.validation.passed is True
    assert [r.exit_code for r in preview.validation.runs] == [0, 0]


def test_a_failing_validation_is_reported_with_its_real_exit_status(world):
    preview = _validating(
        world["root"], ["echo building; echo 'error: broken' >&2; exit 3", "true"],
    ).preview(_selection(world, [world["shas"]["D"]]), str(world["arcos"]))
    assert preview.outcome is PreviewOutcome.VALIDATION_FAILED
    assert preview.validation.passed is False
    [run] = preview.validation.runs  # stops at the first failure
    assert run.exit_code == 3 and not run.timed_out
    assert "building" in run.output and "error: broken" in run.output
    assert "__apm_validation_exit" not in run.output
    assert "exited with status 3" in preview.validation.message


def test_a_validation_that_overruns_times_out_and_fails(world):
    preview = _validating(world["root"], ["sleep 5"], timeout=1).preview(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"])
    )
    assert preview.outcome is PreviewOutcome.VALIDATION_FAILED
    [run] = preview.validation.runs
    assert run.timed_out is True and run.exit_code is None
    assert "timed out" in preview.validation.message


def test_a_failing_validation_blocks_the_push(world):
    bare = world["root"] / "arcos-bare-validation.git"
    fx.git(world["root"], "clone", "-q", "--bare", str(world["arcos"]), str(bare))

    result = _validating(world["root"], ["false"]).cherry_pick(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"]),
        branch_name="upstream/demo/unvalidated", push=True, push_url=str(bare),
    )
    assert result.outcome is PreviewOutcome.VALIDATION_FAILED
    assert result.pushed is False and result.head_sha is None
    assert fx.git(bare, "branch", "--list", "upstream/*") == ""


def test_a_validated_push_records_what_was_applied_and_checked(world):
    bare = world["root"] / "arcos-bare-validated.git"
    fx.git(world["root"], "clone", "-q", "--bare", str(world["arcos"]), str(bare))

    result = _validating(world["root"], ["test -f shared.txt"]).cherry_pick(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"]),
        branch_name="upstream/demo/validated", push=True, push_url=str(bare),
    )
    assert result.outcome is PreviewOutcome.CLEAN and result.pushed is True
    assert result.validation.passed is True
    [commit] = result.commits
    assert commit.sha == world["shas"]["D"] and commit.subject == "commit D"
    assert commit.author and commit.date


# -- a pushed patch branch is always new, always ours, and exactly what was applied

def _bare(world, name):
    bare = world["root"] / name
    fx.git(world["root"], "clone", "-q", "--bare", str(world["arcos"]), str(bare))
    return bare


@pytest.mark.parametrize("name,base", [
    ("main", "refs/heads/main"),          # the target, spelled another way
    ("release", "main"),                  # any branch outside upstream/
    ("upstream/", "main"),
    ("upstream/demo/../main", "main"),
    ("upstream/demo/x y", "main"),
])
def test_a_patch_branch_outside_upstream_is_refused_before_anything_runs(
        world, name, base):
    bare = _bare(world, f"refused-{abs(hash(name))}.git")
    main = fx.git(bare, "rev-parse", "main")

    with pytest.raises(PatchError) as excinfo:
        _service(world["root"]).cherry_pick(
            _selection(world, [world["shas"]["D"]]), str(world["arcos"]),
            branch_name=name, base_ref=base, push=True, push_url=str(bare))

    assert excinfo.value.code is ErrorCode.INVALID_REQUEST
    assert fx.git(bare, "rev-parse", "main") == main


def test_an_existing_branch_is_never_extended(world):
    bare = _bare(world, "existing.git")
    fx.git(bare, "branch", "upstream/demo/taken", "main")
    taken = fx.git(bare, "rev-parse", "upstream/demo/taken")

    with pytest.raises(PatchError) as excinfo:
        _service(world["root"]).cherry_pick(
            _selection(world, [world["shas"]["D"]]), str(world["arcos"]),
            branch_name="upstream/demo/taken", push=True, push_url=str(bare))

    assert excinfo.value.code is ErrorCode.CONFLICT
    assert fx.git(bare, "rev-parse", "upstream/demo/taken") == taken


def test_a_validation_that_commits_does_not_reach_the_pushed_branch(world):
    bare = _bare(world, "validated.git")
    service = _validating(world["root"], [
        "git -c user.name=v -c user.email=v@x commit -q --allow-empty -m 'from validation'"])

    result = service.cherry_pick(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"]),
        branch_name="upstream/demo/validated", push=True, push_url=str(bare))

    assert result.pushed and result.validation.passed is True
    pushed = fx.git(bare, "rev-parse", "upstream/demo/validated")
    assert pushed == result.head_sha
    assert fx.git(bare, "log", "-1", "--format=%s", pushed) == "commit D"


def test_a_failure_that_is_not_a_conflict_is_not_called_one(world, monkeypatch):
    from apm.gitio.transport import GitResult
    from apm.gitio.workspace import GitWorkspace

    monkeypatch.setattr(GitWorkspace, "cherry_pick",
                        lambda self, sha: GitResult(False, "", f"fatal: bad object {sha}"))

    preview = _service(world["root"]).preview(
        _selection(world, [world["shas"]["D"]]), str(world["arcos"]))

    assert preview.outcome is PreviewOutcome.FAILED
    assert preview.conflicts == []
    assert "could not be applied" in preview.message


def test_a_validation_command_may_end_with_a_comment(world):
    preview = _validating(world["root"], ["test -f E.txt  # E is applied"]).preview(
        _selection(world, [world["shas"]["D"], world["shas"]["E"]]), str(world["arcos"]))

    assert preview.validation.passed is True
    assert [r.exit_code for r in preview.validation.runs] == [0]
