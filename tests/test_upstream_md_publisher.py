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
from urllib.parse import parse_qs, urlsplit

import pytest

import gitfixtures as g
from apm.domain.enums import (
    ErrorCode, PackageCategory, PublishStatus, RefStrategy, ResolutionMethod,
    ResolutionMode, ResolutionStatus, UpstreamMdOutcome, VerificationLevel,
)
from apm.domain.models import (
    PublishResult, PullRequest, RefSelection, Repository, ResolutionEvidence,
    UpstreamResolution,
)
from apm.gitio.transport import LocalGit
from apm.gitio.workspace import GitWorkspace
from apm.services.github_service import GitHubError, build_upstream_md_pr_body
from apm.services.publish_ledger import PublishLedger
from apm.services.upstream_md_publisher import (
    BRANCH_TEMPLATE, TRACKING_BRANCH, PublishError, PublishTarget,
    UpstreamMdPublisher, branch_for, commit_title, lint_content, load_targets,
    names_proposal, sha256_text, target_from_resolution, template_for,
    validate_branch, validate_template,
)
from apm.services.upstream_md_service import MARKER, UpstreamMdService

PATH = "debian/upstream.md"
BRANCH = "upstream-metadata/bookworm/mstpd"
SRC = Path(__file__).resolve().parents[1] / "src" / "apm"


# -- fixtures -----------------------------------------------------------------

def _resolution(package="mstpd", url="", remote=None, **overrides) -> UpstreamResolution:
    if remote is not None:
        url = remote.url
        overrides.setdefault("upstream_repository",
                             Repository(url=remote.upstream_url))
        overrides.setdefault("upstream_commit", remote.upstream_sha)
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
        merge_base="76289208dcaa", merge_bases=["76289208dcaa"], behind=101,
        arcos_only=39, verification_level=VerificationLevel.SHARED_HISTORY,
        counts_basis="raw commit-graph counts, head-based",
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
        # The upstream project, local too: the publisher re-reads its ref
        # before proposing, and a test must never reach github.com.
        upstream = g.init(root / "upstream-src")
        g.commit(upstream, "README", "mstpd\n", "upstream")
        self.upstream_bare = root / "upstream.git"
        g.git(root, "clone", "-q", "--bare", str(upstream), str(self.upstream_bare))
        g.git(self.upstream_bare, "branch", "-m", "main", "master")
        self.upstream_url = "file://" + str(self.upstream_bare)
        self.upstream_sha = g.git(self.upstream_bare, "rev-parse", "master")
        self.upstream_src = upstream

    def move_upstream(self):
        """Upstream master gains a commit after the mapping was made."""
        g.git(self.upstream_src, "remote", "add", "bare", str(self.upstream_bare),
              check=False)
        g.commit(self.upstream_src, "NEWS", "later\n", "later upstream work")
        g.git(self.upstream_src, "push", "-q", "bare", "HEAD:refs/heads/master")

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


class RecordingWorkspace(GitWorkspace):
    """A real workspace that also writes down every push it is asked for."""

    def __init__(self, pushes, *args) -> None:
        super().__init__(*args)
        self.pushes = pushes

    def push_commit(self, url, sha, branch):
        self.pushes.append((sha, branch))
        return super().push_commit(url, sha, branch)


class FakeWorkspaces:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.pushes = []

    def scratch(self, label):
        return RecordingWorkspace(self.pushes, LocalGit(timeout=60),
                                  str(self.root / label))


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
    resolution = _resolution(remote=remote)
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
    assert "| Merge base | 76289208dcaa |" in body
    assert "101 upstream commit(s) not in ARCoS, 39 ARCoS commit(s) not upstream" in body
    assert "not a list of missing fixes" in body
    assert "| Verification level | SHARED_HISTORY |" in body


def test_pr_body_carries_the_file_and_the_two_values_it_records():
    resolution = _resolution()
    content = UpstreamMdService().render(resolution)

    body = build_upstream_md_pr_body(resolution, content=content)

    # The CI sections come first, then the file exactly as committed.
    assert body.index("Problem Description :") < body.index("Root Cause :") \
        < body.index("Fix Details :") < body.index("```text")
    assert "```text\n" + content + "```" in body
    assert "records the upstream source and the current upstream branch" in body
    assert "| Upstream Source | https://github.com/mstpd/mstpd.git |" in body
    assert "| Current Upstream Branch | master |" in body
    assert body.count("Adds debian/upstream.md") == 1
    assert "Review notes :" not in body, "nothing to flag on a verified branch"


def test_pr_body_renders_the_file_itself_when_not_given_it():
    resolution = _resolution()
    body = build_upstream_md_pr_body(resolution)
    assert UpstreamMdService().render(resolution) in body


def test_pr_body_says_updates_for_a_file_already_there():
    body = build_upstream_md_pr_body(_resolution(),
                                     outcome=UpstreamMdOutcome.UPDATED)
    assert "Updates debian/upstream.md" in body
    assert _ci_description_check(body)


def _tag_only(**overrides):
    values = dict(
        upstream_branch=None, upstream_tag="release_2_0_0",
        upstream_ref="release_2_0_0",
        ref_selection=RefSelection(
            ref="release_2_0_0", kind="tag", strategy=RefStrategy.EXACT_TAG,
            reason="Debian 2.0.0 is tagged release_2_0_0",
            debian_upstream_version="2.0.0", base_tag="release_2_0_0",
            arcos_contains_base=False,
        ),
        warnings=["the fork does not contain the release tag"],
    )
    values.update(overrides)
    return _resolution("libpam-radius-auth", **values)


def test_pr_body_for_a_tag_only_mapping_names_the_tag_and_says_what_to_check():
    body = build_upstream_md_pr_body(_tag_only())

    assert _ci_description_check(body)
    assert ("| Current Upstream Branch | N/A - no upstream branch verified; "
            "upstream tag release_2_0_0 (the ARCoS fork does not contain this "
            "tag) |") in body
    notes = body[body.index("Review notes :"):]
    assert [line.split("`")[1] for line in notes.splitlines()
            if line.startswith("- `")] == [
        "BRANCH_NOT_VERIFIED", "TAG_ONLY", "BASE_TAG_NOT_IN_FORK",
        "MAPPING_WARNINGS",
    ]
    assert "Debian 2.0.0 is tagged release_2_0_0" in notes
    assert "the fork does not contain the release tag" in notes
    # The warnings are one of the notes, not repeated in a section of their own.
    assert body.count("the fork does not contain the release tag") == 1


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
    resolution = _resolution(remote=remote)
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
        _resolution(remote=remote), content
    )

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.NO_CHANGE
    assert remote.branches() == ["aminor"] and github.created == []


def test_a_hand_written_file_is_a_conflict_and_is_not_replaced(tmp_path):
    remote = Remote(tmp_path / "remote", upstream_md="# Upstream\n\nhand notes\n")
    github = FakeGitHub()
    publisher = _publisher_for(tmp_path, remote, github)
    resolution = _resolution(remote=remote)
    target = target_from_resolution(resolution, UpstreamMdService().render(resolution))

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.CONFLICT
    assert remote.branches() == ["aminor"] and github.created == []


def test_an_older_generated_file_is_updated(tmp_path):
    remote = Remote(tmp_path / "remote",
                    upstream_md=f"<!-- {MARKER}. -->\n\nold\n")
    publisher = _publisher_for(tmp_path, remote)
    resolution = _resolution(remote=remote)
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
    resolution = _resolution(remote=remote)
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
    resolution = _resolution(remote=remote)
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
        resolution = _resolution(name, remote=remote)
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
        # The mapping now names another branch, which the file records.
        "moved": _resolution("moved", upstream_branch="main", upstream_ref="main"),
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


def _real_mapping_has_levels() -> bool:
    return (REAL_OUT / "upstream-resolutions.json").exists()


@pytest.mark.skipif(
    not (REAL_OUT / "upstream-md-plan-bookworm.json").exists()
    or not _real_mapping_has_levels(),
    reason="needs a bookworm mapping and plan generated with verification levels",
)
def test_the_real_bookworm_plan_only_proposes_proven_packages(tmp_path):
    """Against the actual generated files: every proposal is proven, nothing drifts."""
    from apm.config import load_settings
    from apm.services.mapping_store import MappingStore

    plan = json.loads((REAL_OUT / "upstream-md-plan-bookworm.json").read_text())
    copy = tmp_path / "upstream-md-plan-bookworm.json"
    copy.write_text(json.dumps(plan))
    os.symlink(REAL_OUT / "upstream-md", tmp_path / "upstream-md")

    from apm.services.comparison_store import ComparisonStore

    # Wired as the container wires it. A comparison snapshot that matches the
    # mapping's commits adds the backlog to the PR body only, never to the file.
    mapping = MappingStore(REAL_OUT / "upstream-mapping.csv",
                           comparisons=ComparisonStore(REAL_OUT / "comparisons"))
    targets, settled = load_targets(
        copy, mapping, UpstreamMdService(),
        load_settings().upstream_md_publish["exclude"],
    )

    drift = [s for s in settled if s.status is PublishStatus.DRIFT]
    assert not drift, [(s.package, s.error) for s in drift]
    assert targets, "a regenerated bookworm plan proposes at least one package"
    assert all(t.base_branch == "aminor" for t in targets)
    assert "ONL-standalone" not in {t.package for t in targets}
    for target in targets:
        assert target.resolution.status is ResolutionStatus.VERIFIED
        assert target.resolution.verification_level.proves_relationship


def test_the_curated_upstream_conflicts_stay_excluded_until_decided():
    """These forks came from the salsa packaging repository, not the curated
    GitHub upstream; which one the file names is a person's call. The shipped
    settings exclude them, so even a re-resolution that marks one VERIFIED is
    never proposed - with or without push-only."""
    from apm.config import load_settings

    exclude = load_settings().upstream_md_publish["exclude"]
    conflicts = ["liburcu", "lldpd", "lttng-tools", "lttng-ust", "net-snmp"]
    publisher = UpstreamMdPublisher(None, FakeGitHub(), "a", "a@b",
                                    exclude=exclude)
    for package in conflicts:
        assert "awaiting a human decision" in exclude.get(package, ""), package
        resolution = _resolution(package)
        assert resolution.status is ResolutionStatus.VERIFIED
        target = target_from_resolution(
            resolution, UpstreamMdService().render(resolution))
        for push_only in (False, True):
            result = publisher.publish(target, apply=True, push_only=push_only)
            assert result.status is PublishStatus.SKIPPED_EXCLUDED
            assert result.error == exclude[package]


# -- staleness and proof (the correctness brief, J and B) ---------------------

def test_an_upstream_that_moved_since_the_mapping_is_drift_not_a_pr(env):
    remote, github, publisher, target = env
    remote.move_upstream()

    for apply in (False, True):
        result = publisher.publish(target, apply=apply)
        assert result.status is PublishStatus.DRIFT
        assert "moved from" in result.error and "since the mapping" in result.error
    assert remote.branches() == ["aminor"], "nothing was pushed"
    assert github.created == []


def test_a_resolution_that_only_proves_existence_is_never_proposed(tmp_path):
    remote = Remote(tmp_path / "remote")
    github = FakeGitHub()
    publisher = _publisher_for(tmp_path, remote, github)
    resolution = _resolution(remote=remote,
                             verification_level=VerificationLevel.REF_EXISTS)
    target = target_from_resolution(resolution, UpstreamMdService().render(resolution))

    result = publisher.publish(target, apply=True)
    assert result.status is PublishStatus.SKIPPED_NEEDS_REVIEW
    assert "REF_EXISTS" in result.error
    assert remote.branches() == ["aminor"] and github.created == []


def test_the_plan_refuses_a_mapping_without_verification_levels(tmp_path):
    resolution = _resolution(verification_level=VerificationLevel.NONE)
    content = UpstreamMdService().render(resolution)
    md = tmp_path / "upstream-md" / "mstpd" / "debian" / "upstream.md"
    md.parent.mkdir(parents=True)
    md.write_text(content)
    plan = tmp_path / "upstream-md-plan-bookworm.json"
    plan.write_text(json.dumps({"release": "bookworm", "entries": [{
        "package": "mstpd", "status": "VERIFIED", "arcos_repository": "Arrcus/mstpd",
        "base_branch": "aminor", "sha256": sha256_text(content)}]}))
    targets, settled = load_targets(plan, FakeMapping({"mstpd": resolution}),
                                    UpstreamMdService())
    assert targets == []
    assert settled[0].status is PublishStatus.SKIPPED_NEEDS_REVIEW
    assert "regenerate" in settled[0].error


# -- push only: the branch, a link, and no pull request ---------------------------

def _query(link):
    return {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}


def test_push_only_without_a_token_pushes_once_and_opens_no_pr(env, tmp_path):
    remote, github, publisher, target = env
    github.token = ""
    tip = remote.ref("aminor")

    result = publisher.publish(target, apply=True, push_only=True)

    assert result.status is PublishStatus.BRANCH_PUSHED, result.error
    assert result.error is None and result.pushed is True
    assert publisher.workspaces.pushes == [(result.commit, BRANCH)]
    assert remote.branches() == ["aminor", BRANCH]
    assert remote.ref(BRANCH) == result.commit
    assert remote.ref("aminor") == tip, "the target branch must never move"
    assert github.created == [] and result.pull_request is None
    # Without a token not even a lookup is made.
    assert github.lookups == [] and github.repos == []

    link = result.pr_create_url
    assert link.startswith(
        "https://github.com/Arrcus/mstpd/compare/"
        "aminor...upstream-metadata/bookworm/mstpd?quick_pull=1&"
    )
    assert "title=mstpd%3A%20add%20debian%2Fupstream.md%20with%20verified%20upstream" in link
    query = _query(link)
    assert query["title"] == result.title
    assert _ci_description_check(query["body"])
    assert target.content in query["body"]


def test_push_only_is_not_done_and_not_a_failure():
    status = PublishStatus.BRANCH_PUSHED
    assert not status.is_done
    assert not status.is_retryable and not status.is_failure


def test_push_only_dry_run_pushes_nothing_and_shows_the_link(env):
    remote, github, publisher, target = env
    github.token = ""

    result = publisher.publish(target, apply=False, push_only=True)

    assert result.status is PublishStatus.DRY_RUN_OK
    assert publisher.workspaces.pushes == [] and remote.branches() == ["aminor"]
    assert "/compare/aminor...upstream-metadata/bookworm/mstpd?quick_pull=1" in \
        result.pr_create_url
    assert github.created == []


def test_a_plain_dry_run_gives_no_link(env):
    remote, github, publisher, target = env
    assert publisher.publish(target, apply=False).pr_create_url is None


def test_rerunning_push_only_after_the_push_does_not_push_again(env):
    remote, github, publisher, target = env
    github.token = ""
    first = publisher.publish(target, apply=True, push_only=True)

    for apply in (False, True):
        again = publisher.publish(target, apply=apply, push_only=True, prior=first)
        assert again.commit == first.commit
        assert again.pr_create_url == first.pr_create_url
    assert again.status is PublishStatus.BRANCH_PUSHED
    assert len(publisher.workspaces.pushes) == 1
    assert remote.ref(BRANCH) == first.commit and github.created == []


def test_a_token_run_after_push_only_opens_the_pr_without_pushing_again(env):
    remote, github, publisher, target = env
    github.token = ""
    pushed = publisher.publish(target, apply=True, push_only=True)

    github.token = "tok"
    opened = publisher.publish(target, apply=True, prior=pushed)

    assert opened.status is PublishStatus.PR_OPENED, opened.error
    assert opened.commit == pushed.commit
    assert len(publisher.workspaces.pushes) == 1
    [pr] = github.created
    assert (pr["head"], pr["base"], pr["title"]) == (BRANCH, "aminor", pushed.title)
    # The tool opens the same description the link would have filled in.
    assert pr["body"] == _query(pushed.pr_create_url)["body"]


def test_a_pr_a_person_opened_from_the_link_is_found_not_duplicated(env):
    remote, github, publisher, target = env
    github.token = ""
    pushed = publisher.publish(target, apply=True, push_only=True)
    github._open[BRANCH] = PullRequest(
        number=12, state="open", url="https://github.com/Arrcus/mstpd/pull/12",
    )

    github.token = "tok"
    result = publisher.publish(target, apply=True, prior=pushed)

    assert result.status is PublishStatus.PR_EXISTS
    assert result.pull_request.number == 12 and github.created == []


@pytest.mark.parametrize("earlier,status", [
    (PullRequest(number=7, state="closed", url="https://github.com/x/pull/7"),
     PublishStatus.PR_CLOSED),
    (PullRequest(number=9, state="merged", url="https://github.com/x/pull/9"),
     PublishStatus.PR_MERGED),
])
def test_push_only_with_a_token_still_respects_an_earlier_pr(tmp_path, earlier,
                                                              status):
    remote = Remote(tmp_path / "remote")
    github = FakeGitHub(earlier=earlier)
    publisher = _publisher_for(tmp_path, remote, github)
    resolution = _resolution(remote=remote)
    target = target_from_resolution(resolution, UpstreamMdService().render(resolution))

    result = publisher.publish(target, apply=True, push_only=True)

    assert result.status is status
    assert publisher.workspaces.pushes == [] and remote.branches() == ["aminor"]
    assert github.created == []


def test_push_only_stops_for_the_same_reasons_as_any_apply(tmp_path):
    def run(remote, **overrides):
        github = FakeGitHub(token="")
        publisher = UpstreamMdPublisher(
            FakeWorkspaces(tmp_path / f"ws-{len(seen)}"), github, "a", "a@b",
        )
        resolution = _resolution(remote=remote)
        target = target_from_resolution(
            resolution, UpstreamMdService().render(resolution))
        for key, value in overrides.items():
            setattr(target, key, value)
        result = publisher.publish(target, apply=True, push_only=True)
        assert publisher.workspaces.pushes == [] and github.created == []
        assert result.pr_create_url is None
        seen.append(result.status)
        return result

    seen = []
    plain = Remote(tmp_path / "plain")
    run(plain, status=ResolutionStatus.NEEDS_REVIEW)
    run(Remote(tmp_path / "hand", upstream_md="# Upstream\n\nhand notes\n"))
    moved = Remote(tmp_path / "moved")
    moved.move_upstream()
    run(moved)
    taken = Remote(tmp_path / "taken")
    foreign = taken.add_branch(BRANCH)
    run(taken)

    assert seen == [PublishStatus.SKIPPED_NEEDS_REVIEW, PublishStatus.CONFLICT,
                    PublishStatus.DRIFT, PublishStatus.BRANCH_EXISTS]
    assert plain.branches() == ["aminor"]
    assert taken.ref(BRANCH) == foreign


def test_push_only_preflight_needs_neither_a_token_nor_the_api(tmp_path):
    github = FakeGitHub(token="")
    publisher = _publisher_for(tmp_path, None, github)

    publisher.preflight(["Arrcus/mstpd", "Arrcus/forbidden"], push_only=True)

    assert github.repos == []
    with pytest.raises(PublishError) as raised:
        publisher.preflight(["Arrcus/mstpd"])
    assert raised.value.code is ErrorCode.AUTH_REQUIRED


def test_a_batch_without_a_token_refuses_unless_push_only(tmp_path):
    pairs = _targets(tmp_path, ["aaa", "bbb"])
    github = FakeGitHub(token="")
    publisher = _publisher_for(tmp_path, None, github)
    ledger = PublishLedger.for_release(tmp_path / "out", "bookworm")
    queue, _ = publisher.select([t for _, t in pairs], ledger)

    with pytest.raises(PublishError) as raised:
        publisher.publish_many(queue, apply=True, ledger=ledger, confirm_count=2)
    assert raised.value.code is ErrorCode.AUTH_REQUIRED
    assert "Nothing was pushed" in str(raised.value)
    assert publisher.workspaces.pushes == []
    assert all(r.branches() == ["aminor"] for r, _ in pairs)

    results = publisher.publish_many(queue, apply=True, ledger=ledger,
                                     confirm_count=2, push_only=True)

    assert [r.status for r in results] == [PublishStatus.BRANCH_PUSHED] * 2
    assert [b for _, b in publisher.workspaces.pushes] == [
        "upstream-metadata/bookworm/aaa", "upstream-metadata/bookworm/bbb",
    ]
    assert github.created == []
    reloaded = PublishLedger.for_release(tmp_path / "out", "bookworm")
    assert reloaded.get("bbb").pr_create_url == results[1].pr_create_url

    # Not done, so the next run picks them up again; not failed, so a retry
    # of failures leaves them alone.
    queue, kept = publisher.select([t for _, t in pairs], reloaded)
    assert [t.package for t, _ in queue] == ["aaa", "bbb"] and kept == []
    queue, kept = publisher.select([t for _, t in pairs], reloaded,
                                   retry_failed=True)
    assert queue == [] and len(kept) == 2


def test_push_only_needs_the_same_confirmation(tmp_path):
    pairs = _targets(tmp_path, ["aaa", "bbb"])
    publisher = _publisher_for(tmp_path, None, FakeGitHub(token=""))
    queue, _ = publisher.select([t for _, t in pairs])
    for count in (None, 1, 3):
        with pytest.raises(PublishError):
            publisher.publish_many(queue, apply=True, confirm_count=count,
                                   push_only=True)
    assert publisher.workspaces.pushes == []
    assert all(r.branches() == ["aminor"] for r, _ in pairs)


# -- push only: what a later run must not lose ------------------------------------

def test_a_pushed_only_branch_survives_an_upstream_move(tmp_path):
    remote = Remote(tmp_path / "remote")
    github = FakeGitHub(token="")
    publisher = _publisher_for(tmp_path, remote, github)
    resolution = _resolution(remote=remote)
    content = UpstreamMdService().render(resolution)
    target = target_from_resolution(resolution, content)
    out = tmp_path / "out"
    ledger = PublishLedger.for_release(out, "bookworm")
    [pushed] = publisher.publish_many([(target, None)], apply=True, ledger=ledger,
                                      confirm_count=1, push_only=True)
    assert pushed.status is PublishStatus.BRANCH_PUSHED

    # Upstream gains a commit while the branch waits for a person. A token run
    # stops at DRIFT - but the record of the branch must outlive that.
    remote.move_upstream()
    github.token = "tok"
    [drift] = publisher.publish_many([(target, ledger.get("mstpd"))], apply=True,
                                     ledger=ledger, confirm_count=1)
    assert drift.status is PublishStatus.DRIFT
    kept = PublishLedger.for_release(out, "bookworm").get("mstpd")
    assert kept.status is PublishStatus.BRANCH_PUSHED
    assert (kept.commit, kept.pushed) == (pushed.commit, True)
    assert kept.pr_create_url == pushed.pr_create_url

    # Re-resolved: a new upstream commit, the same two-field file.
    moved = _resolution(remote=remote,
                        upstream_commit=g.git(remote.upstream_bare, "rev-parse",
                                              "master"))
    assert UpstreamMdService().render(moved) == content
    again = target_from_resolution(moved, content)
    [opened] = publisher.publish_many([(again, ledger.get("mstpd"))], apply=True,
                                      ledger=ledger, confirm_count=1)

    assert opened.status is PublishStatus.PR_OPENED, opened.error
    assert opened.commit == pushed.commit
    assert len(publisher.workspaces.pushes) == 1 and len(github.created) == 1


def test_a_failure_after_the_push_does_not_erase_the_branch_record(env, tmp_path):
    remote, github, publisher, target = env
    github.token = ""
    ledger = PublishLedger.for_release(tmp_path / "out", "bookworm")
    [pushed] = publisher.publish_many([(target, None)], apply=True, ledger=ledger,
                                      confirm_count=1, push_only=True)

    # The upstream cannot be reached for a run: an ERROR, nothing more.
    target.resolution.upstream_repository = Repository(
        url="file:///nonexistent/upstream.git")
    [failed] = publisher.publish_many([(target, ledger.get("mstpd"))],
                                      apply=True, ledger=ledger,
                                      confirm_count=1, push_only=True)
    assert failed.status is PublishStatus.ERROR
    assert ledger.get("mstpd").status is PublishStatus.BRANCH_PUSHED
    assert ledger.get("mstpd").commit == pushed.commit


def test_a_pushed_branch_holding_an_older_file_is_drift_not_a_pr(env, tmp_path):
    remote, github, publisher, target = env
    github.token = ""
    ledger = PublishLedger.for_release(tmp_path / "out", "bookworm")
    [pushed] = publisher.publish_many([(target, None)], apply=True, ledger=ledger,
                                      confirm_count=1, push_only=True)

    # The plan was regenerated with a different file after the push.
    newer = _resolution(remote=remote, upstream_branch="main", upstream_ref="main")
    g.git(remote.upstream_bare, "branch", "main", "master")
    changed = target_from_resolution(newer, UpstreamMdService().render(newer))
    assert changed.content != target.content
    github.token = "tok"
    [stale] = publisher.publish_many([(changed, ledger.get("mstpd"))], apply=True,
                                     ledger=ledger, confirm_count=1)

    assert stale.status is PublishStatus.DRIFT
    assert "holds a different debian/upstream.md" in stale.error
    assert github.created == [] and len(publisher.workspaces.pushes) == 1
    # Still recognised as this tool's branch - and no longer offered as a link.
    record = ledger.get("mstpd")
    assert record.status is PublishStatus.DRIFT
    assert (record.commit, record.pushed) == (pushed.commit, True)

    # Until the branch is deleted, every run - push-only or not - stops there.
    for push_only in (False, True):
        [again] = publisher.publish_many([(changed, ledger.get("mstpd"))],
                                         apply=True, ledger=ledger,
                                         confirm_count=1, push_only=push_only)
        assert again.status is PublishStatus.DRIFT, again.error
        assert again.pr_create_url is None
    assert github.created == [] and len(publisher.workspaces.pushes) == 1

    # Deleted on the fork, it is pushed again with the reviewed file.
    g.git(remote.bare, "update-ref", "-d", f"refs/heads/{BRANCH}")
    [opened] = publisher.publish_many([(changed, ledger.get("mstpd"))],
                                      apply=True, ledger=ledger, confirm_count=1)
    assert opened.status is PublishStatus.PR_OPENED, opened.error
    assert opened.commit != pushed.commit
    assert g.git(remote.bare, "show", f"{opened.commit}:{PATH}") + "\n" == \
        changed.content


@pytest.mark.parametrize("token", ["tok", ""])
def test_push_only_never_downgrades_a_pull_request(env, tmp_path, token):
    remote, github, publisher, target = env
    ledger = PublishLedger.for_release(tmp_path / "out", "bookworm")
    [opened] = publisher.publish_many([(target, None)], apply=True, ledger=ledger,
                                      confirm_count=1)
    assert opened.status is PublishStatus.PR_OPENED
    github.token = token

    # --recheck, or the API: the PR is open and the branch still there.
    [again] = publisher.publish_many([(target, ledger.get("mstpd"))], apply=True,
                                     ledger=ledger, confirm_count=1,
                                     push_only=True)

    assert again.status is PublishStatus.PR_OPENED
    assert again.pull_request.url == opened.pull_request.url
    assert again.pr_create_url is None
    record = PublishLedger.for_release(tmp_path / "out", "bookworm").get("mstpd")
    assert record.status is PublishStatus.PR_OPENED
    assert record.pull_request.url == opened.pull_request.url
    assert len(publisher.workspaces.pushes) == 1 and len(github.created) == 1


@pytest.mark.parametrize("status", [PublishStatus.PR_MERGED,
                                    PublishStatus.PR_CLOSED])
def test_push_only_without_a_token_trusts_the_ledger_about_a_pr(env, status):
    remote, github, publisher, target = env
    github.token = ""
    prior = PublishResult(
        package="mstpd", release="bookworm", status=status, branch=BRANCH,
        repository="Arrcus/mstpd", base_branch="aminor", commit="a" * 40,
        pushed=True, pull_request=PullRequest(
            number=4, state="merged" if status is PublishStatus.PR_MERGED
            else "closed", url="https://github.com/Arrcus/mstpd/pull/4"),
    )
    # The branch is gone, as GitHub leaves it after a merge or a rejection.
    assert remote.branches() == ["aminor"]

    for apply in (False, True):
        result = publisher.publish(target, apply=apply, push_only=True,
                                   prior=prior)
        assert result.status is status
        assert result.pull_request.number == 4 and result.pr_create_url is None
    assert publisher.workspaces.pushes == [] and remote.branches() == ["aminor"]
    assert github.lookups == []


def test_push_only_with_a_token_finds_an_open_pr_instead_of_linking(env):
    remote, github, publisher, target = env
    github.token = ""
    pushed = publisher.publish(target, apply=True, push_only=True)
    # A person opened it from the link; the ledger does not know yet.
    github._open[BRANCH] = PullRequest(
        number=12, state="open", url="https://github.com/Arrcus/mstpd/pull/12")

    github.token = "tok"
    result = publisher.publish(target, apply=True, push_only=True, prior=pushed)

    assert result.status is PublishStatus.PR_EXISTS
    assert result.pull_request.number == 12 and result.error is None
    assert result.pr_create_url is None
    assert len(publisher.workspaces.pushes) == 1 and github.created == []


def test_a_merged_pr_whose_branch_was_deleted_is_no_change(env, tmp_path):
    remote, github, publisher, target = env
    github.token = ""
    ledger = PublishLedger.for_release(tmp_path / "out", "bookworm")
    [pushed] = publisher.publish_many([(target, None)], apply=True, ledger=ledger,
                                      confirm_count=1, push_only=True)
    # Merged from the link, and GitHub deleted the branch.
    g.git(remote.bare, "update-ref", "refs/heads/aminor", pushed.commit)
    g.git(remote.bare, "update-ref", "-d", f"refs/heads/{BRANCH}")
    github.earlier = PullRequest(number=5, state="merged",
                                 url="https://github.com/Arrcus/mstpd/pull/5")

    github.token = "tok"
    [result] = publisher.publish_many([(target, ledger.get("mstpd"))], apply=True,
                                      ledger=ledger, confirm_count=1)

    # The target already has the file - as for any package - and the record of
    # the waiting branch gives way, so its link leaves the links file.
    assert result.status is PublishStatus.NO_CHANGE
    assert ledger.get("mstpd").status is PublishStatus.NO_CHANGE
    assert len(publisher.workspaces.pushes) == 1 and github.created == []


def test_a_run_that_never_meets_push_only_behaves_as_before(tmp_path):
    """An earlier PR is looked up only for a file that would be proposed, as it
    always was: an identical file is NO_CHANGE, a hand-written one CONFLICT,
    whatever happened to an earlier pull request."""
    merged = PullRequest(number=9, state="merged", url="https://github.com/x/pull/9")
    content = UpstreamMdService().render(_resolution())
    for upstream_md, status in [(content, PublishStatus.NO_CHANGE),
                                ("# Upstream\n\nhand notes\n",
                                 PublishStatus.CONFLICT)]:
        remote = Remote(tmp_path / status.value, upstream_md=upstream_md)
        github = FakeGitHub(earlier=merged)
        publisher = UpstreamMdPublisher(FakeWorkspaces(tmp_path / f"ws-{status.value}"),
                                        github, "a", "a@b")
        target = target_from_resolution(_resolution(remote=remote), content)

        result = publisher.publish(target, apply=True)

        assert result.status is status
        assert github.lookups == [] and publisher.workspaces.pushes == []


def test_push_only_selection_keeps_what_it_can_do_nothing_more_for(tmp_path):
    publisher = UpstreamMdPublisher(None, FakeGitHub(), "a", "a@b")
    names = ["done", "pushed", "closed", "failed", "new"]
    targets = [PublishTarget(n, "bookworm", ResolutionStatus.VERIFIED, "u",
                             f"Arrcus/{n}", "aminor", "x\n") for n in names]
    ledger = PublishLedger.for_release(tmp_path, "bookworm")
    for name, status in [("done", PublishStatus.PR_OPENED),
                         ("pushed", PublishStatus.BRANCH_PUSHED),
                         ("closed", PublishStatus.PR_CLOSED),
                         ("failed", PublishStatus.PR_FAILED)]:
        ledger.record(PublishResult(package=name, release="bookworm",
                                    status=status))

    queue, kept = publisher.select(targets, ledger, push_only=True)
    assert sorted(t.package for t, _ in queue) == ["failed", "new"]
    assert sorted(k.package for k in kept) == ["closed", "done", "pushed"]

    # --limit therefore moves on instead of taking the same packages again.
    queue, _ = publisher.select(targets, ledger, push_only=True, limit=1)
    assert [t.package for t, _ in queue] == ["failed"]

    # A token run queues the pushed branch, to open its pull request.
    queue, _ = publisher.select(targets, ledger)
    assert sorted(t.package for t, _ in queue) == ["closed", "failed", "new",
                                                   "pushed"]
    queue, _ = publisher.select(targets, ledger, push_only=True, recheck=True)
    assert len(queue) == len(names)


# -- debian-upstream-tracking: one branch name for the whole repository --------

def test_a_release_with_its_own_template_gets_its_own_branch(tmp_path):
    """trixie's proposal goes beside bookworm's, never onto it."""
    publisher = UpstreamMdPublisher(
        FakeWorkspaces(tmp_path / "ws"), FakeGitHub(), author_name="a",
        author_email="a@b", branch_template=TRACKING_BRANCH,
        release_templates={"trixie": "debian-upstream-tracking-trixie"},
    )
    bookworm = _target(None)
    trixie = _target(None, debian_release="trixie", arcos_branch="aminor-trixie")
    assert publisher.branch_of(bookworm) == TRACKING_BRANCH
    assert publisher.branch_of(trixie) == "debian-upstream-tracking-trixie"
    assert template_for("trixie", TRACKING_BRANCH, {"trixie": "t"}) == "t"
    assert template_for("bookworm", TRACKING_BRANCH, {"trixie": "t"}) == TRACKING_BRANCH
    assert template_for("bookworm", TRACKING_BRANCH, None) == TRACKING_BRANCH


def test_the_shipped_settings_name_one_branch_per_release():
    from apm.config import load_settings
    from apm.services.upstream_md_publisher import validate_template

    publish = load_settings().upstream_md_publish
    templates = publish["release_branch_templates"]
    assert publish["branch_template"] == TRACKING_BRANCH
    assert templates == {"trixie": "debian-upstream-tracking-trixie"}
    for template in templates.values():
        assert validate_template(template) == template

def _tracking_publisher(tmp_path, github=None):
    return UpstreamMdPublisher(
        FakeWorkspaces(tmp_path / "ws-tracking"), github or FakeGitHub(),
        author_name="Somil Rathore", author_email="somil.rathore@arrcus.com",
        branch_template=TRACKING_BRANCH,
    )


def _target(remote, package="mstpd", **overrides):
    resolution = _resolution(package, remote=remote, **overrides)
    return target_from_resolution(resolution,
                                  UpstreamMdService().render(resolution))


@pytest.mark.parametrize("name", [
    TRACKING_BRANCH, "debian-upstream-tracking/bookworm", BRANCH,
    "debian-upstream-tracking-trixie",
])
def test_both_conventions_name_branches_the_workflow_may_create(name):
    validate_branch(name, "aminor")


@pytest.mark.parametrize("name", [
    "debian-upstream-tracking-x", "debian-upstream-trackingx",
    "debian-upstream-tracking-tx", "debian-upstream-tracking-Trixie",
    "debian-upstream-tracking--trixie", "debian-upstream-tracking-trixie/x",
    "debian-upstream-tracking-trixie-2", "debian-upstream-tracking-",
    "debian-upstream-tracking/", "debian-upstream-tracking/a..b",
    "x/debian-upstream-tracking", "upstream-metadata", "upstream-metadata/",
])
def test_near_misses_of_either_convention_are_refused(name):
    with pytest.raises(PublishError):
        validate_branch(name, "aminor")


@pytest.mark.parametrize("template", [
    BRANCH_TEMPLATE, TRACKING_BRANCH, "debian-upstream-tracking/{release}",
])
def test_templates_in_either_convention_are_accepted(template):
    assert validate_template(template) == template


@pytest.mark.parametrize("template", [
    "aminor", "{release}", "releases/{release}/{package}",
    "upstream-metadata/{pkg}", "debian-upstream-tracking/{0}",
])
def test_a_template_outside_the_conventions_is_refused(template):
    with pytest.raises(PublishError):
        validate_template(template)


def test_only_a_name_with_the_release_and_package_identifies_a_proposal():
    assert names_proposal(BRANCH, "mstpd", "bookworm")
    assert not names_proposal(TRACKING_BRANCH, "mstpd", "bookworm")
    assert not names_proposal("debian-upstream-tracking/bookworm", "mstpd",
                              "bookworm")
    assert not names_proposal("upstream-metadata/trixie/mstpd", "mstpd",
                              "bookworm")


def test_the_tracking_name_pushes_one_branch_and_leaves_the_target(tmp_path):
    remote = Remote(tmp_path / "remote")
    github = FakeGitHub(token="")
    publisher = _tracking_publisher(tmp_path, github)
    aminor = remote.ref("aminor")

    result = publisher.publish(_target(remote), apply=True, push_only=True)

    assert result.status is PublishStatus.BRANCH_PUSHED, result.error
    assert result.branch == TRACKING_BRANCH
    assert remote.branches() == ["aminor", TRACKING_BRANCH]
    assert remote.ref(TRACKING_BRANCH) == result.commit
    assert remote.ref("aminor") == aminor and github.created == []
    assert f"aminor...{TRACKING_BRANCH}" in result.pr_create_url


def test_a_branch_pushed_under_the_old_name_keeps_it_after_a_switch(env, tmp_path):
    remote, github, publisher, target = env
    github.token = ""
    old = publisher.publish(target, apply=True, push_only=True)
    assert old.branch == BRANCH

    switched = _tracking_publisher(tmp_path, github)
    for apply in (False, True):
        again = switched.publish(target, apply=apply, push_only=True, prior=old)
        assert (again.branch, again.commit) == (BRANCH, old.commit), again.error
    assert switched.workspaces.pushes == []

    github.token = "tok"
    opened = switched.publish(target, apply=True, prior=old)

    assert opened.status is PublishStatus.PR_OPENED, opened.error
    assert [pr["head"] for pr in github.created] == [BRANCH]
    assert remote.branches() == ["aminor", BRANCH]
    assert switched.workspaces.pushes == []


def test_a_package_never_pushed_takes_the_new_name(env, tmp_path):
    remote, github, _publisher, target = env
    failed = PublishResult(package="mstpd", release="bookworm", branch=BRANCH,
                           status=PublishStatus.PUSH_FAILED)

    assert _tracking_publisher(tmp_path, github).branch_of(target, failed) == \
        TRACKING_BRANCH


def test_a_second_release_never_takes_over_the_shared_branch(tmp_path):
    remote = Remote(tmp_path / "remote")
    publisher = _tracking_publisher(tmp_path, FakeGitHub(token=""))
    bookworm = publisher.publish(_target(remote), apply=True, push_only=True)
    trixie = _target(remote, debian_release="trixie")

    for apply in (False, True):
        result = publisher.publish(trixie, apply=apply, push_only=True,
                                   others=[bookworm])
        assert result.status is PublishStatus.BRANCH_EXISTS
        assert "mstpd (bookworm)" in result.error
    stranger = publisher.publish(trixie, apply=True, push_only=True)
    assert "no record of creating it" in stranger.error

    assert remote.ref(TRACKING_BRANCH) == bookworm.commit
    assert len(publisher.workspaces.pushes) == 1


def test_two_packages_of_one_repository_never_share_a_branch_in_a_run(tmp_path):
    remote = Remote(tmp_path / "remote")
    targets = [_target(remote, package,
                       github_repository="Arrcus/onl-testbed")
               for package in ("ONL-standalone", "ONL-xc")]
    publisher = _tracking_publisher(tmp_path, FakeGitHub(token=""))

    queue, refused = publisher.refuse_shared_branches(
        [(t, None) for t in targets])

    assert [t.package for t, _ in queue] == ["ONL-standalone"]
    [second] = refused
    assert (second.package, second.status) == ("ONL-xc",
                                               PublishStatus.BRANCH_EXISTS)
    assert "ONL-standalone (bookworm)" in second.error

    # Per release and package, nothing is shared.
    queue, refused = _publisher_for(tmp_path, remote).refuse_shared_branches(
        [(t, None) for t in targets])
    assert len(queue) == 2 and refused == []


def test_another_proposals_pr_under_the_shared_name_decides_nothing(tmp_path):
    remote = Remote(tmp_path / "remote")
    # Another release's merged pull request; its branch was deleted.
    github = FakeGitHub(earlier=PullRequest(
        number=3, state="merged", head=TRACKING_BRANCH, head_sha="f" * 40,
        url="https://github.com/Arrcus/mstpd/pull/3"))
    publisher = _tracking_publisher(tmp_path, github)

    result = publisher.publish(_target(remote), apply=True)

    assert result.status is PublishStatus.PR_OPENED, result.error
    assert remote.ref(TRACKING_BRANCH) == result.commit


def test_this_packages_pr_under_the_shared_name_still_counts(tmp_path):
    remote = Remote(tmp_path / "remote")
    github = FakeGitHub(token="")
    publisher = _tracking_publisher(tmp_path, github)
    target = _target(remote)
    pushed = publisher.publish(target, apply=True, push_only=True)
    github.token = "tok"
    github.earlier = PullRequest(
        number=4, state="closed", head=TRACKING_BRANCH, head_sha=pushed.commit,
        url="https://github.com/Arrcus/mstpd/pull/4")

    again = publisher.publish(target, apply=True, prior=pushed)

    assert again.status is PublishStatus.PR_CLOSED
    assert github.created == [] and len(publisher.workspaces.pushes) == 1


def test_a_pull_request_that_could_not_be_opened_keeps_the_link(env):
    remote, github, publisher, target = env
    github.fail_create = "Validation Failed"

    result = publisher.publish(target, apply=True)

    assert result.status is PublishStatus.PR_FAILED
    assert remote.ref(BRANCH) == result.commit
    assert result.pr_create_url.startswith(
        f"https://github.com/Arrcus/mstpd/compare/aminor...{BRANCH}")
