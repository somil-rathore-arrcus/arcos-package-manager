"""Proposing debian/upstream.md: branch, commit, push, PR - and what must never happen.

These drive real git: a bare repository stands in for the ARCoS fork, reached
over file:// with partial clone enabled so the shallow, blobless path the
publisher uses on the linux fork is the path under test. GitHub is a fake that
records every call, so "no PR was opened" is an assertion, not a hope.

The safety properties are the point: the target branch never moves, the commit
never carries a second file, nothing is forced, a hand-written file is never
replaced, and a failure is recorded and survived.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

import gitfixtures as g
from apm.domain.enums import (
    ErrorCode, PackageCategory, PublishStatus, ResolutionMethod, ResolutionMode,
    ResolutionStatus, UpstreamMdOutcome,
)
from apm.domain.models import (
    PublishResult, PullRequest, Repository, ResolutionEvidence,
    UpstreamResolution,
)
from apm.gitio.transport import LocalGit
from apm.gitio.workspace import GitWorkspace
from apm.services.github_service import GitHubError, build_upstream_md_pr_body
from apm.services.publish_ledger import PublishLedger
from apm.services.upstream_md_publisher import (
    BRANCH_TEMPLATE, PublishError, PublishTarget, UpstreamMdPublisher,
    branch_for, commit_title, lint_content, load_targets, sha256_text,
    target_from_resolution, validate_branch,
)
from apm.services.upstream_md_service import MARKER, UpstreamMdService

PATH = "debian/upstream.md"
BRANCH = "upstream-metadata/bookworm/mstpd"
SRC = Path(__file__).resolve().parents[1] / "src" / "apm"


# -- fixtures -----------------------------------------------------------------

def _resolution(package="mstpd", url="", **overrides) -> UpstreamResolution:
    values = dict(
        package=package, debian_release="bookworm",
        status=ResolutionStatus.VERIFIED, mode=ResolutionMode.AUTO,
        method=ResolutionMethod.CURATED, category=PackageCategory.THIRD_PARTY,
        confidence="high",
        arcos_repository=url or f"ssh://git@github.com/Arrcus/{package}.git",
        github_repository=f"Arrcus/{package}", arcos_branch="aminor",
        arcos_release="aminor", arcos_path=f"packages/{package}",
        upstream_repository=Repository(url=f"https://github.com/{package}/{package}.git"),
        upstream_ref="master", upstream_branch="master",
        upstream_commit="2e747d80ad48", origin_kind="project",
        merge_base="76289208dcaa", behind=101, arcos_only=39,
        evidence_source="config/overrides.yaml (hand-verified)",
        verification="git merge-base",
        evidence=[ResolutionEvidence(kind="curated", detail="curated override")],
    )
    values.update(overrides)
    return UpstreamResolution(**values)


class Remote:
    """A bare 'ARCoS fork' with an aminor branch holding debian/control."""

    def __init__(self, root: Path, upstream_md: str = None) -> None:
        self.root = root
        source = g.init(root / "source")
        g.git(source, "checkout", "-q", "-b", "aminor")
        (source / "debian").mkdir()
        g.commit(source, "debian/control", "Source: mstpd\n", "packaging")
        if upstream_md is not None:
            g.commit(source, PATH, upstream_md, "existing upstream.md")
        self.bare = root / "fork.git"
        g.git(root, "clone", "-q", "--bare", str(source), str(self.bare))
        g.git(self.bare, "config", "uploadpack.allowFilter", "true")
        self.url = "file://" + str(self.bare)
        self.source = source

    def ref(self, name: str):
        out = g.git(self.bare, "rev-parse", "--verify", "-q", name, check=False)
        return out or None

    def branches(self):
        return sorted(g.git(self.bare, "for-each-ref", "--format=%(refname:short)",
                            "refs/heads").split())

    def reject_pushes(self):
        hook = self.bare / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'protected by policy' >&2\nexit 1\n")
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)

    def add_branch(self, name: str):
        g.git(self.source, "checkout", "-q", "-b", name.replace("/", "-"))
        sha = g.commit(self.source, "other.txt", "someone else\n", "not ours")
        g.git(self.source, "push", "-q", str(self.bare), f"HEAD:refs/heads/{name}")
        g.git(self.source, "checkout", "-q", "aminor")
        return sha


class FakeWorkspaces:
    def __init__(self, root: Path) -> None:
        self.root = root

    def scratch(self, label):
        return GitWorkspace(LocalGit(timeout=60), str(self.root / label))


class FakeGitHub:
    def __init__(self, token="tok", fail_create=None, earlier=None):
        self.token = token
        self.fail_create = fail_create
        self.earlier = earlier
        self.created = []
        self.lookups = []
        self.repos = []
        self._open = {}

    @property
    def can_create_pull_requests(self):
        return bool(self.token)

    def get_repository(self, slug):
        self.repos.append(slug)
        if slug == "Arrcus/forbidden":
            raise GitHubError(ErrorCode.AUTH_REQUIRED, "not SSO-authorised")
        return {"full_name": slug}

    def find_pull_request(self, slug, head, base, state="open"):
        self.lookups.append((slug, head, base, state))
        if head in self._open:
            return self._open[head]
        return self.earlier

    def create_pull_request(self, slug, head, base, title, body, draft=False):
        if not self.token:
            raise GitHubError(ErrorCode.AUTH_REQUIRED, "token required")
        if head in self._open:
            return self._open[head].model_copy(update={"already_existed": True})
        if self.fail_create:
            raise GitHubError(ErrorCode.INTERNAL, self.fail_create)
        self.created.append({"slug": slug, "head": head, "base": base,
                             "title": title, "body": body, "draft": draft})
        pr = PullRequest(number=len(self.created), state="open", title=title,
                         url=f"https://github.com/{slug}/pull/{len(self.created)}",
                         base=base, head=head, draft=draft)
        self._open[head] = pr
        return pr


@pytest.fixture
def env(tmp_path):
    remote = Remote(tmp_path / "remote")
    github = FakeGitHub()
    publisher = UpstreamMdPublisher(
        FakeWorkspaces(tmp_path / "ws"), github,
        author_name="Somil Rathore", author_email="somil.rathore@arrcus.com",
    )
    resolution = _resolution(url=remote.url)
    content = UpstreamMdService().render(resolution)
    target = target_from_resolution(resolution, content)
    return remote, github, publisher, target


def _publisher_for(tmp_path, remote, github=None):
    return UpstreamMdPublisher(
        FakeWorkspaces(tmp_path / "ws"), github or FakeGitHub(),
        author_name="Somil Rathore", author_email="somil.rathore@arrcus.com",
    )


# -- naming and content -------------------------------------------------------

def test_branch_is_per_release_and_package():
    assert branch_for("mstpd", "bookworm") == BRANCH
    assert BRANCH_TEMPLATE.startswith("upstream-metadata/")


@pytest.mark.parametrize("name", [
    "aminor", "main", "", "docs/upstream-md/bookworm",
    "upstream-metadata/bad name", "upstream-metadata/a..b",
    "upstream-metadata/x~1", "upstream-metadata/x:y", "upstream-metadata/x/",
    "upstream-metadata/x.lock",
])
def test_branches_outside_the_workflow_are_refused(name):
    with pytest.raises(PublishError):
        validate_branch(name, "aminor")


def test_the_target_branch_itself_is_refused_even_under_the_prefix():
    with pytest.raises(PublishError):
        validate_branch("upstream-metadata/x", "upstream-metadata/x")


def test_commit_titles_say_add_or_update_and_take_a_prefix():
    assert commit_title("mstpd", UpstreamMdOutcome.CREATED) == \
        "mstpd: add debian/upstream.md with verified upstream"
    assert commit_title("mstpd", UpstreamMdOutcome.UPDATED, "BR-1: ") == \
        "BR-1: mstpd: update debian/upstream.md with verified upstream"


def test_rendered_files_pass_the_whitespace_rules():
    assert lint_content(UpstreamMdService().render(_resolution())) is None


@pytest.mark.parametrize("content,problem", [
    ("", "empty"), ("a\r\n", "carriage"), ("a", "newline"), ("a\n\n", "newline"),
    ("a \nb\n", "trailing whitespace"),
])
def test_whitespace_problems_are_caught_before_git(content, problem):
    assert problem in lint_content(content)


def _ci_description_check(body: str) -> bool:
    """The logic of ARCoS .github/workflows/description.py, restated."""
    keys = ["Problem Description :", "Root Cause :", "Fix Details :"]
    positions = sorted((body.find(k), k) for k in keys)
    if any(pos == -1 for pos, _ in positions):
        return False
    for i, (pos, key) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(body)
        if len(body[pos + len(key):end].strip()) <= 2:
            return False
    return True


def test_pr_body_passes_the_arcos_description_check():
    body = build_upstream_md_pr_body(_resolution(), "457f1b77da8cd329")
    assert _ci_description_check(body)
    assert "457f1b77da8c" in body
    assert "76289208dcaa - 101 behind, 39 ARCoS-only" in body


# -- dry run ------------------------------------------------------------------

def test_dry_run_builds_the_commit_and_pushes_nothing(env):
    remote, github, publisher, target = env
    tip = remote.ref("aminor")

    result = publisher.publish(target, apply=False)

    assert result.status is PublishStatus.DRY_RUN_OK
    assert result.base_sha == tip
    assert result.commit and len(result.commit) == 40
    assert result.outcome is UpstreamMdOutcome.CREATED
    assert result.title == "mstpd: add debian/upstream.md with verified upstream"
    assert remote.branches() == ["aminor"]
    assert github.created == []
    assert result.pushed is False


def test_dry_run_needs_no_token(env):
    remote, github, publisher, target = env
    github.token = ""
    assert publisher.publish(target, apply=False).status is PublishStatus.DRY_RUN_OK


# -- apply --------------------------------------------------------------------

def test_apply_pushes_one_file_on_a_new_branch_and_opens_a_pr(env):
    remote, github, publisher, target = env
    tip = remote.ref("aminor")

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.PR_OPENED, result.error
    assert result.pushed is True
    assert remote.ref("aminor") == tip, "the target branch must never move"
    assert remote.ref(BRANCH) == result.commit
    assert g.git(remote.bare, "rev-parse", f"{result.commit}^") == tip
    changed = g.git(remote.bare, "diff-tree", "-r", "--no-commit-id",
                    "--name-only", tip, result.commit).split()
    assert changed == [PATH]
    assert g.git(remote.bare, "show", f"{result.commit}:{PATH}") + "\n" == target.content
    assert g.git(remote.bare, "log", "-1", "--format=%an <%ae>|%cn <%ce>",
                 result.commit) == \
        "Somil Rathore <somil.rathore@arrcus.com>|Somil Rathore <somil.rathore@arrcus.com>"

    [pr] = github.created
    assert pr["slug"] == "Arrcus/mstpd"
    assert (pr["head"], pr["base"]) == (BRANCH, "aminor")
    assert pr["title"] == result.title
    assert _ci_description_check(pr["body"])
    assert result.pull_request.url.endswith("/pull/1")


def test_apply_without_a_token_refuses_before_any_push(env):
    remote, github, publisher, target = env
    github.token = ""
    with pytest.raises(PublishError) as raised:
        publisher.publish(target, apply=True)
    assert raised.value.code is ErrorCode.AUTH_REQUIRED
    assert remote.branches() == ["aminor"]


def test_rerunning_after_success_neither_pushes_nor_opens_again(env, tmp_path):
    remote, github, publisher, target = env
    first = publisher.publish(target, apply=True)
    pushed = remote.ref(BRANCH)

    second = publisher.publish(target, apply=True, prior=first)

    assert second.status is PublishStatus.PR_EXISTS
    assert remote.ref(BRANCH) == pushed
    assert len(github.created) == 1


def test_a_failed_pr_is_retried_without_pushing_again(tmp_path):
    remote = Remote(tmp_path / "remote")
    github = FakeGitHub(fail_create="Validation Failed")
    publisher = _publisher_for(tmp_path, remote, github)
    resolution = _resolution(url=remote.url)
    target = target_from_resolution(resolution, UpstreamMdService().render(resolution))

    failed = publisher.publish(target, apply=True)
    assert failed.status is PublishStatus.PR_FAILED
    assert failed.pushed is True and remote.ref(BRANCH) == failed.commit
    assert "Validation Failed" in failed.error

    github.fail_create = None
    retried = publisher.publish(target, apply=True, prior=failed)
    assert retried.status is PublishStatus.PR_OPENED
    assert retried.commit == failed.commit
    assert len(github.created) == 1


def test_a_rejected_push_is_recorded_and_opens_no_pr(env):
    remote, github, publisher, target = env
    remote.reject_pushes()

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.PUSH_FAILED
    assert "protected by policy" in result.error
    assert github.created == []
    assert remote.branches() == ["aminor"]


# -- what stops a package for a person ----------------------------------------

def test_an_identical_file_is_no_change(tmp_path):
    resolution = _resolution()
    content = UpstreamMdService().render(resolution)
    remote = Remote(tmp_path / "remote", upstream_md=content)
    github = FakeGitHub()
    publisher = _publisher_for(tmp_path, remote, github)
    target = target_from_resolution(
        _resolution(url=remote.url), content
    )

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.NO_CHANGE
    assert remote.branches() == ["aminor"] and github.created == []


def test_a_hand_written_file_is_a_conflict_and_is_not_replaced(tmp_path):
    remote = Remote(tmp_path / "remote", upstream_md="# Upstream\n\nhand notes\n")
    github = FakeGitHub()
    publisher = _publisher_for(tmp_path, remote, github)
    resolution = _resolution(url=remote.url)
    target = target_from_resolution(resolution, UpstreamMdService().render(resolution))

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.CONFLICT
    assert remote.branches() == ["aminor"] and github.created == []


def test_an_older_generated_file_is_updated(tmp_path):
    remote = Remote(tmp_path / "remote",
                    upstream_md=f"<!-- {MARKER}. -->\n\nold\n")
    publisher = _publisher_for(tmp_path, remote)
    resolution = _resolution(url=remote.url)
    target = target_from_resolution(resolution, UpstreamMdService().render(resolution))

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.PR_OPENED, result.error
    assert result.outcome is UpstreamMdOutcome.UPDATED
    assert result.title.startswith("mstpd: update ")


def test_a_branch_this_tool_did_not_create_is_left_alone(env):
    remote, github, publisher, target = env
    foreign = remote.add_branch(BRANCH)

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.BRANCH_EXISTS
    assert remote.ref(BRANCH) == foreign
    assert github.created == []


def test_a_closed_pr_is_not_reopened(tmp_path):
    remote = Remote(tmp_path / "remote")
    github = FakeGitHub(earlier=PullRequest(number=7, state="closed",
                                            url="https://github.com/x/pull/7"))
    publisher = _publisher_for(tmp_path, remote, github)
    resolution = _resolution(url=remote.url)
    target = target_from_resolution(resolution, UpstreamMdService().render(resolution))

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.PR_CLOSED
    assert remote.branches() == ["aminor"] and github.created == []


def test_a_closed_pr_is_not_reopened_when_resuming_a_pushed_branch(env):
    remote, github, publisher, target = env
    first = publisher.publish(target, apply=True)
    # A reviewer closes it; the ledger still says PR_OPENED.
    closed = first.pull_request.model_copy(update={"state": "closed"})
    github._open.clear()
    github.earlier = closed

    again = publisher.publish(target, apply=True, prior=first)

    assert again.status is PublishStatus.PR_CLOSED
    assert len(github.created) == 1


def test_a_merged_pr_is_done_not_a_problem(tmp_path):
    remote = Remote(tmp_path / "remote")
    github = FakeGitHub(earlier=PullRequest(number=9, state="merged",
                                            url="https://github.com/x/pull/9"))
    publisher = _publisher_for(tmp_path, remote, github)
    resolution = _resolution(url=remote.url)
    target = target_from_resolution(resolution, UpstreamMdService().render(resolution))

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.PR_MERGED
    assert result.status.is_done and result.error is None
    assert remote.branches() == ["aminor"] and github.created == []


def test_a_missing_target_branch_is_an_error_not_a_crash(env):
    remote, github, publisher, target = env
    target.base_branch = "no-such-branch"
    result = publisher.publish(target, apply=False)
    assert result.status is PublishStatus.ERROR
    assert "no branch no-such-branch" in result.error


def test_unverified_and_excluded_packages_never_reach_git(env):
    remote, github, publisher, target = env
    target.status = ResolutionStatus.NEEDS_REVIEW
    assert publisher.publish(target, apply=True).status is \
        PublishStatus.SKIPPED_NEEDS_REVIEW
    target.status = ResolutionStatus.NO_UPSTREAM
    assert publisher.publish(target, apply=True).status is \
        PublishStatus.SKIPPED_NO_UPSTREAM
    target.status = ResolutionStatus.VERIFIED
    publisher.exclude["mstpd"] = "no debian/"
    excluded = publisher.publish(target, apply=True)
    assert excluded.status is PublishStatus.SKIPPED_EXCLUDED
    assert excluded.error == "no debian/"
    assert remote.branches() == ["aminor"] and github.created == []


def test_the_caller_cannot_name_the_target_branch(env):
    remote, github, publisher, target = env
    with pytest.raises(PublishError):
        publisher.publish(target, apply=True, branch="aminor")
    assert remote.branches() == ["aminor"]


def test_a_file_with_bad_whitespace_is_never_committed(env):
    remote, github, publisher, target = env
    target.content = target.content + "\n"
    result = publisher.publish(target, apply=True)
    assert result.status is PublishStatus.ERROR
    assert "exactly one newline" in result.error
    assert remote.branches() == ["aminor"]


# -- the commit primitive -----------------------------------------------------

def test_a_stray_staged_file_cannot_reach_the_commit(tmp_path):
    remote = Remote(tmp_path / "remote")
    ws = GitWorkspace(LocalGit(), str(tmp_path / "ws"))
    ws.ensure()
    head = ws.fetch_side("arcos", remote.url, "aminor", depth=1)
    # Something left in the workspace's own index and tree.
    (tmp_path / "ws" / "stray.txt").write_text("unrelated\n")
    ws.shell("git add stray.txt", check=True)

    commit = ws.commit_file_on(head, PATH, "x\n", "m", "a", "a@b")

    assert ws.changed_paths(head, commit) == [PATH]


def test_exact_bytes_survive_the_trip(tmp_path):
    remote = Remote(tmp_path / "remote")
    ws = GitWorkspace(LocalGit(), str(tmp_path / "ws"))
    ws.ensure()
    head = ws.fetch_side("arcos", remote.url, "aminor", depth=1)
    tricky = "$HOME `id` $(rm -rf /) 'q' \"d\" \\ EOF\nAPM_CONTENT_EOF\nünïcødé\n"

    commit = ws.commit_file_on(head, PATH, tricky, "m", "a", "a@b")

    assert ws.read_file(PATH, rev=commit) == tricky


def test_write_file_writes_exactly_what_it_is_given(tmp_path):
    ws = GitWorkspace(LocalGit(), str(tmp_path / "ws"))
    ws.ensure()
    ws.write_file(PATH, "one\n")
    assert (tmp_path / "ws" / PATH).read_text() == "one\n"


def test_nothing_in_the_publish_path_can_force_or_merge():
    publisher = (SRC / "services" / "upstream_md_publisher.py").read_text()
    workspace = (SRC / "gitio" / "workspace.py").read_text()
    push_commit = workspace[workspace.index("def push_commit"):
                            workspace.index("def _b64")]
    assert "force" not in push_commit.replace("Never forced", "")
    assert "force" not in publisher.split('"""', 2)[2]
    assert "/merge" not in publisher


# -- many packages ------------------------------------------------------------

def _targets(tmp_path, names):
    out = []
    for name in names:
        remote = Remote(tmp_path / f"remote-{name}")
        resolution = _resolution(name, url=remote.url)
        out.append((remote, target_from_resolution(
            resolution, UpstreamMdService().render(resolution))))
    return out


def test_one_failure_does_not_stop_the_batch(tmp_path):
    pairs = _targets(tmp_path, ["aaa", "bbb", "ccc"])
    pairs[1][0].reject_pushes()
    github = FakeGitHub()
    publisher = _publisher_for(tmp_path, None, github)
    ledger = PublishLedger.for_release(tmp_path / "out", "bookworm")
    queue, _ = publisher.select([t for _, t in pairs], ledger)

    results = publisher.publish_many(queue, apply=True, ledger=ledger,
                                     confirm_count=3)

    assert [r.status for r in results] == [
        PublishStatus.PR_OPENED, PublishStatus.PUSH_FAILED, PublishStatus.PR_OPENED,
    ]
    assert ledger.get("bbb").status is PublishStatus.PUSH_FAILED
    reloaded = PublishLedger.for_release(tmp_path / "out", "bookworm")
    assert reloaded.get("ccc").pull_request.url.endswith("/pull/2")


def test_the_confirmation_must_match_what_would_be_pushed(tmp_path):
    pairs = _targets(tmp_path, ["aaa", "bbb"])
    publisher = _publisher_for(tmp_path, None)
    queue, _ = publisher.select([t for _, t in pairs])
    with pytest.raises(PublishError):
        publisher.publish_many(queue, apply=True, confirm_count=1)
    assert all(r.branches() == ["aminor"] for r, _ in pairs)


def test_preflight_failure_stops_before_any_push(tmp_path):
    pairs = _targets(tmp_path, ["aaa"])
    target = pairs[0][1]
    target.slug = "Arrcus/forbidden"
    publisher = _publisher_for(tmp_path, None)
    with pytest.raises(PublishError) as raised:
        publisher.publish_many([(target, None)], apply=True, confirm_count=1)
    assert "SSO" in str(raised.value)
    assert pairs[0][0].branches() == ["aminor"]


def test_consecutive_failures_stop_the_run(tmp_path):
    pairs = _targets(tmp_path, ["aaa", "bbb", "ccc"])
    for remote, _ in pairs:
        remote.reject_pushes()
    publisher = _publisher_for(tmp_path, None)
    queue, _ = publisher.select([t for _, t in pairs])
    results = publisher.publish_many(queue, apply=True, confirm_count=3,
                                     stop_after_failures=2)
    assert len(results) == 2


def test_an_unexpected_exception_is_recorded_and_the_batch_continues(tmp_path):
    pairs = _targets(tmp_path, ["aaa", "bbb"])
    pairs[0][1].repository_url = "file:///nonexistent/fork.git"
    publisher = _publisher_for(tmp_path, None)
    queue, _ = publisher.select([t for _, t in pairs])
    results = publisher.publish_many(queue, apply=False)
    assert results[0].status is PublishStatus.ERROR
    assert results[1].status is PublishStatus.DRY_RUN_OK


def test_selection_skips_done_work_and_retries_only_failures(tmp_path):
    publisher = UpstreamMdPublisher(None, FakeGitHub(), "a", "a@b",
                                    defer=["linux"])
    targets = [PublishTarget(n, "bookworm", ResolutionStatus.VERIFIED, "u",
                             f"Arrcus/{n}", "aminor", "x\n")
               for n in ["linux", "zzz", "aaa", "mmm"]]
    ledger = PublishLedger.for_release(tmp_path, "bookworm")
    ledger.record(PublishResult(package="aaa", release="bookworm",
                                status=PublishStatus.PR_OPENED))
    ledger.record(PublishResult(package="mmm", release="bookworm",
                                status=PublishStatus.PUSH_FAILED))

    queue, kept = publisher.select(targets, ledger)
    assert [t.package for t, _ in queue] == ["mmm", "zzz", "linux"]
    assert [k.package for k in kept] == ["aaa"]

    queue, _ = publisher.select(targets, ledger, retry_failed=True)
    assert [t.package for t, _ in queue] == ["mmm"]

    queue, _ = publisher.select(targets, ledger, recheck=True, limit=2)
    assert [t.package for t, _ in queue] == ["aaa", "mmm"]


# -- reading the reviewed plan ------------------------------------------------

class FakeMapping:
    def __init__(self, rows):
        self.rows = rows

    def get(self, package, release):
        return self.rows.get(package)


def _plan(tmp_path, entries, skipped=()):
    for entry in entries:
        content = entry.pop("_content", None)
        if content is not None:
            path = tmp_path / "upstream-md" / entry["package"] / "debian" / "upstream.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    plan = tmp_path / "upstream-md-plan-bookworm.json"
    plan.write_text(json.dumps({"release": "bookworm", "entries": entries,
                                "skipped": list(skipped)}))
    return plan


def _entry(package, content, **extra):
    values = {"package": package, "status": "VERIFIED", "outcome": "CREATED",
              "arcos_repository": f"Arrcus/{package}", "base_branch": "aminor",
              "sha256": sha256_text(content), "_content": content}
    values.update(extra)
    return values


def test_the_plan_decides_eligibility_and_catches_drift(tmp_path):
    service = UpstreamMdService()
    good, moved, edited, unhashed, gone = (
        _resolution(n) for n in ["good", "moved", "edited", "unhashed", "gone"]
    )
    plan = _plan(tmp_path, [
        _entry("good", service.render(good)),
        _entry("moved", service.render(moved)),
        _entry("edited", service.render(edited), sha256="0" * 64),
        _entry("unhashed", service.render(unhashed), sha256=""),
        _entry("ONL-standalone", "x\n"),
        _entry("nomap", "x\n"),
        {"package": "gone", "status": "VERIFIED", "arcos_repository": "Arrcus/gone",
         "base_branch": "aminor", "sha256": "1" * 64},
    ], skipped=[
        {"package": "arcapi", "status": "NO_UPSTREAM", "reason": "arrcus native"},
        {"package": "babeltrace", "status": "NEEDS_REVIEW", "reason": "imported"},
    ])
    mapping = FakeMapping({
        "good": good, "edited": edited, "unhashed": unhashed, "gone": gone,
        "moved": _resolution("moved", behind=5),
    })

    targets, settled = load_targets(plan, mapping, service,
                                    {"ONL-standalone": "no debian/"})

    assert [t.package for t in targets] == ["good"]
    assert targets[0].slug == "Arrcus/good"
    assert targets[0].repository_url == "ssh://git@github.com/Arrcus/good.git"
    status = {s.package: s.status for s in settled}
    assert status == {
        "arcapi": PublishStatus.SKIPPED_NO_UPSTREAM,
        "babeltrace": PublishStatus.SKIPPED_NEEDS_REVIEW,
        "moved": PublishStatus.DRIFT,
        "edited": PublishStatus.DRIFT,
        "unhashed": PublishStatus.ERROR,
        "ONL-standalone": PublishStatus.SKIPPED_EXCLUDED,
        "nomap": PublishStatus.ERROR,
        "gone": PublishStatus.DRIFT,
    }


REAL_OUT = Path(__file__).resolve().parents[1] / "out"


@pytest.mark.skipif(
    not (REAL_OUT / "upstream-md-plan-bookworm.json").exists()
    or not (REAL_OUT / "upstream-mapping.csv").exists(),
    reason="needs the generated bookworm plan and mapping",
)
def test_the_real_bookworm_plan_yields_26_proposals(tmp_path):
    """Against the actual generated files: 27 generated, ONL-standalone excluded."""
    from apm.config import load_settings
    from apm.services.mapping_store import MappingStore

    plan = json.loads((REAL_OUT / "upstream-md-plan-bookworm.json").read_text())
    for entry in plan["entries"]:
        path = REAL_OUT / "upstream-md" / entry["package"] / "debian" / "upstream.md"
        entry.setdefault("sha256", hashlib.sha256(path.read_bytes()).hexdigest())
    copy = tmp_path / "upstream-md-plan-bookworm.json"
    copy.write_text(json.dumps(plan))
    os.symlink(REAL_OUT / "upstream-md", tmp_path / "upstream-md")

    targets, settled = load_targets(
        copy, MappingStore(REAL_OUT / "upstream-mapping.csv"), UpstreamMdService(),
        load_settings().upstream_md_publish["exclude"],
    )

    drift = [s for s in settled if s.status is PublishStatus.DRIFT]
    assert not drift, [(s.package, s.error) for s in drift]
    assert len(targets) == 26
    assert all(t.base_branch == "aminor" for t in targets)
    assert "ONL-standalone" not in {t.package for t in targets}
    assert {t.package for t in targets} >= {"mstpd", "linux", "libnl3"}
    libnl = next(t for t in targets if t.package == "libnl3")
    assert libnl.slug == "arrcus/libnl"
