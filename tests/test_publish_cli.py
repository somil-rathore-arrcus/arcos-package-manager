"""apm publish-upstream-md, end to end against a local fork and a fake GitHub.

The fake GitHub has no way to open a pull request; the command never asks."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import gitfixtures as g
from apm.domain.models import PullRequest
from apm.publish_upstream_md import run
from apm.services.upstream_md_publisher import UpstreamMdPublisher, sha256_text
from apm.services.upstream_md_service import UpstreamMdService

from test_upstream_md_publisher import (
    FakeGitHub, FakeMapping, FakeWorkspaces, Remote, _resolution,
)


def _setup(tmp_path):
    remote = Remote(tmp_path / "remote")
    resolution = _resolution(remote=remote)
    service = UpstreamMdService()
    content = service.render(resolution)
    out = tmp_path / "out"
    md = out / "upstream-md-bookworm" / "mstpd" / "debian" / "upstream.md"
    md.parent.mkdir(parents=True)
    md.write_text(content)
    (out / "upstream-md-plan-bookworm.json").write_text(json.dumps({
        "release": "bookworm",
        "entries": [{"package": "mstpd", "status": "VERIFIED",
                     "arcos_repository": "Arrcus/mstpd", "base_branch": "aminor",
                     "sha256": sha256_text(content)}],
        "skipped": [{"package": "arcapi", "status": "NO_UPSTREAM",
                     "reason": "arrcus native"}],
    }))
    github = FakeGitHub()
    container = SimpleNamespace(
        mapping=FakeMapping({"mstpd": resolution}), upstream_md=service,
        publisher=UpstreamMdPublisher(FakeWorkspaces(tmp_path / "ws"), github,
                                      "Somil Rathore", "somil.rathore@arrcus.com"),
    )
    return remote, github, container, out


def test_dry_run_is_the_default_and_writes_its_own_ledger(tmp_path, capsys):
    remote, github, container, out = _setup(tmp_path)

    assert run(out_dir=out, all_packages=True, container=container) == 0

    assert remote.branches() == ["aminor"] and github.created == []
    assert not (out / "upstream-md-pr-results-bookworm.json").exists()
    dry = json.loads((out / "upstream-md-pr-dryrun-bookworm.json").read_text())
    assert dry["counts"] == {"DRY_RUN_OK": 1, "SKIPPED_NO_UPSTREAM": 1}
    assert "Nothing was pushed" in capsys.readouterr().out


def test_apply_needs_a_matching_confirmation(tmp_path):
    remote, github, container, out = _setup(tmp_path)

    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
               confirm=["zenoh"], container=container) == 2
    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
               container=container) == 2
    assert remote.branches() == ["aminor"]


def test_apply_pushes_records_the_links_and_a_rerun_does_nothing(tmp_path):
    remote, github, container, out = _setup(tmp_path)

    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
               confirm=["mstpd"], container=container) == 0
    ledger = json.loads((out / "upstream-md-pr-results-bookworm.json").read_text())
    [entry] = ledger["entries"]
    assert entry["status"] == "BRANCH_PUSHED" and entry["pull_request"] is None
    assert entry["compare_url"] == \
        f"https://github.com/Arrcus/mstpd/compare/aminor...{BRANCH}"
    assert entry["file_url"] == \
        f"https://github.com/Arrcus/mstpd/blob/{BRANCH}/debian/upstream.md"
    assert remote.branches() == ["aminor", BRANCH]
    links = (out / "upstream-md-pr-links-bookworm.md").read_text()
    assert f"- Compare Link: {entry['compare_url']}" in links
    assert f"- debian/upstream.md: {entry['file_url']}" in links
    report = (out / "upstream-md-publishing-report.md").read_text()
    assert entry["compare_url"] in report and entry["file_url"] in report

    # Already pushed: nothing left to confirm, nothing pushed, no pull request.
    assert run(out_dir=out, all_packages=True, apply=True, confirm_count=0,
               container=container) == 0
    assert len(container.publisher.workspaces.pushes) == 1
    assert github.created == []


def test_unknown_packages_are_refused(tmp_path):
    _, _, container, out = _setup(tmp_path)
    assert run(out_dir=out, packages_wanted=["nope"], container=container) == 1


# -- push only ------------------------------------------------------------------

BRANCH = "upstream-metadata/bookworm/mstpd"
VERIFY_LEDGER = Path(__file__).resolve().parents[1] / "scripts" / "verify_pr_ledger.py"


def _verify_ledger(root):
    """scripts/verify_pr_ledger.py, pointed at `root`/out instead of the repo."""
    spec = importlib.util.spec_from_file_location("verify_pr_ledger", VERIFY_LEDGER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    return module.main("bookworm")


def test_push_only_without_a_token_pushes_and_writes_the_links(tmp_path, capsys):
    remote, github, container, out = _setup(tmp_path)
    github.token = ""

    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
               push_only=True, confirm=["mstpd"], container=container) == 0

    printed = capsys.readouterr().out
    assert "APPLY - branches will be pushed; no pull request will be opened " \
           "(this tool never opens one)" in printed
    links = out / "upstream-md-pr-links-bookworm.md"
    assert f"Branches were pushed. No pull request was created. Open each one " \
           f"with its link in {links}; the person opening it needs access to " \
           f"the repository." in printed
    assert remote.branches() == ["aminor", BRANCH]
    assert github.created == [] and github.lookups == []

    [entry] = json.loads(
        (out / "upstream-md-pr-results-bookworm.json").read_text())["entries"]
    assert entry["status"] == "BRANCH_PUSHED" and entry["pushed"] is True
    assert entry["pull_request"] is None
    link = entry["pr_create_url"]
    assert link.startswith(f"https://github.com/Arrcus/mstpd/compare/aminor..."
                           f"{BRANCH}?quick_pull=1&title=")
    # The table shows the page; the full, pre-filled link is in the links file.
    short = f"https://github.com/Arrcus/mstpd/compare/aminor...{BRANCH}?quick_pull=1 |"
    assert short in printed

    text = links.read_text()
    assert f"- Link: {link}\n" in text
    assert f"- Commit: `{entry['commit']}`" in text
    assert "mstpd: add debian/upstream.md with verified upstream" in text
    fence = "````markdown\n"
    body = text[text.index(fence) + len(fence):text.rindex("\n````")]
    assert body == parse_qs(urlsplit(link).query)["body"][0]
    assert body.startswith("Problem Description :")
    content = (out / "upstream-md-bookworm" / "mstpd" / "debian" / "upstream.md").read_text()
    assert content in body

    assert _verify_ledger(tmp_path) == 0
    assert "FAILURES: none" in capsys.readouterr().out


def test_a_run_asking_for_pull_requests_is_refused_and_pushes_nothing(tmp_path,
                                                                      capsys):
    remote, github, container, out = _setup(tmp_path)

    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
               confirm=["mstpd"], push_only=False, container=container) == 2

    assert "never opens a pull request" in capsys.readouterr().err
    assert remote.branches() == ["aminor"]
    assert not (out / "upstream-md-pr-links-bookworm.md").exists()


def test_push_only_needs_the_same_confirmation(tmp_path):
    remote, github, container, out = _setup(tmp_path)
    github.token = ""

    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True, push_only=True,
               confirm=["zenoh"], container=container) == 2
    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True, push_only=True,
               container=container) == 2
    assert run(out_dir=out, all_packages=True, apply=True, push_only=True,
               confirm_count=2, container=container) == 2
    assert remote.branches() == ["aminor"]
    assert container.publisher.workspaces.pushes == []


def test_push_only_dry_run_shows_the_links_and_pushes_nothing(tmp_path, capsys):
    remote, github, container, out = _setup(tmp_path)
    github.token = ""

    assert run(out_dir=out, all_packages=True, push_only=True,
               container=container) == 0

    printed = capsys.readouterr().out
    assert "DRY RUN - nothing will be pushed" in printed
    assert "Nothing was pushed. No pull request was created." in printed
    assert remote.branches() == ["aminor"]
    assert not (out / "upstream-md-pr-links-bookworm.md").exists()
    assert not (out / "upstream-md-pr-results-bookworm.json").exists()
    dry = (out / "upstream-md-pr-links-dryrun-bookworm.md").read_text()
    assert "Dry run: nothing was pushed" in dry
    assert f"compare/aminor...{BRANCH}?quick_pull=1&title=" in dry


def test_a_token_never_turns_a_pushed_branch_into_a_pull_request(tmp_path):
    remote, github, container, out = _setup(tmp_path)
    github.token = ""
    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True, push_only=True,
               confirm=["mstpd"], container=container) == 0
    pushed = remote.ref(BRANCH)

    # Again: nothing more it can do, so the package is kept, not queued -
    # nothing to confirm, nothing pushed. A token changes nothing.
    for token in ("", "tok"):
        github.token = token
        assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
                   confirm=["mstpd"], container=container) == 2
        assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
                   confirm_count=0, container=container) == 0

    [entry] = json.loads(
        (out / "upstream-md-pr-results-bookworm.json").read_text())["entries"]
    assert entry["status"] == "BRANCH_PUSHED"
    assert entry["commit"] == pushed and remote.ref(BRANCH) == pushed
    assert len(container.publisher.workspaces.pushes) == 1
    assert github.created == []


def test_a_pushed_branch_is_not_forgotten_when_the_package_stops_being_eligible(
        tmp_path):
    remote, github, container, out = _setup(tmp_path)
    github.token = ""
    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True, push_only=True,
               confirm=["mstpd"], container=container) == 0

    # Excluded now: the ledger must still say the branch is ours.
    assert run(out_dir=out, all_packages=True, apply=True, push_only=True,
               confirm_count=0, exclude=["mstpd"], container=container) == 0

    ledger = json.loads((out / "upstream-md-pr-results-bookworm.json").read_text())
    entry = {e["package"]: e for e in ledger["entries"]}["mstpd"]
    assert entry["status"] == "BRANCH_PUSHED" and entry["commit"] == remote.ref(BRANCH)


def test_the_dry_run_lists_what_a_reviewer_should_check(tmp_path, capsys):
    remote, github, container, out = _setup(tmp_path)
    # A warning is a review flag, not part of the file, so the reviewed file
    # still matches the plan and the package is still proposed.
    container.mapping.rows["mstpd"].warnings = ["check the fork's history"]

    assert run(out_dir=out, all_packages=True, container=container) == 0

    printed = capsys.readouterr().out
    assert "| mstpd | DRY_RUN_OK |" in printed
    assert "review: MAPPING_WARNINGS - check the fork's history" in printed


def _setup_many(tmp_path, names):
    """A plan of several packages, each with its own local fork."""
    service = UpstreamMdService()
    out = tmp_path / "out"
    rows, entries, remotes = {}, [], {}
    for name in names:
        remotes[name] = Remote(tmp_path / f"remote-{name}")
        rows[name] = _resolution(name, remote=remotes[name])
        content = service.render(rows[name])
        md = out / "upstream-md-bookworm" / name / "debian" / "upstream.md"
        md.parent.mkdir(parents=True)
        md.write_text(content)
        entries.append({"package": name, "status": "VERIFIED",
                        "arcos_repository": f"Arrcus/{name}",
                        "base_branch": "aminor", "sha256": sha256_text(content)})
    (out / "upstream-md-plan-bookworm.json").write_text(json.dumps({
        "release": "bookworm", "entries": entries, "skipped": [],
    }))
    github = FakeGitHub(token="")
    container = SimpleNamespace(
        mapping=FakeMapping(rows), upstream_md=service,
        publisher=UpstreamMdPublisher(FakeWorkspaces(tmp_path / "ws"), github,
                                      "Somil Rathore", "somil.rathore@arrcus.com"),
    )
    return remotes, github, container, out


def test_push_only_batches_with_a_limit_move_on_to_new_packages(tmp_path, capsys):
    remotes, github, container, out = _setup_many(tmp_path, ["aaa", "bbb"])

    for expected in (["aaa"], ["aaa", "bbb"]):
        assert run(out_dir=out, all_packages=True, limit=1, apply=True,
                   push_only=True, confirm_count=1, container=container) == 0
        pushed = [b.rsplit("/", 1)[1] for _, b in
                  container.publisher.workspaces.pushes]
        assert pushed == expected

    # Both waiting: nothing left to push, so nothing to count either.
    assert run(out_dir=out, all_packages=True, apply=True, push_only=True,
               confirm_count=0, container=container) == 0
    printed = capsys.readouterr().out
    assert "pushed, waiting for a PR 2" in printed
    assert "No branch was pushed by this run, and no pull request was created. " \
           "2 branch(es) pushed earlier are waiting for a pull request" in printed
    assert "No branch is waiting" not in printed
    assert len(container.publisher.workspaces.pushes) == 2


def test_the_links_file_keeps_every_branch_still_waiting(tmp_path, capsys):
    remotes, github, container, out = _setup_many(tmp_path, ["aaa", "bbb"])
    assert run(out_dir=out, packages_wanted=["aaa"], apply=True, push_only=True,
               confirm=["aaa"], container=container) == 0
    assert run(out_dir=out, packages_wanted=["bbb"], apply=True, push_only=True,
               confirm=["bbb"], container=container) == 0

    links = (out / "upstream-md-pr-links-bookworm.md").read_text()
    assert "## aaa" in links and "## bbb" in links
    assert links.count("- Pushed: yes - waiting for a pull request") == 2
    ledger = json.loads(
        (out / "upstream-md-pr-results-bookworm.json").read_text())["entries"]
    for entry in ledger:
        assert f"- Link: {entry['pr_create_url']}\n" in links

    # A dry run lists them too, marked as pushed, beside nothing new.
    assert run(out_dir=out, all_packages=True, push_only=True,
               container=container) == 0
    dry = (out / "upstream-md-pr-links-dryrun-bookworm.md").read_text()
    assert "## aaa" in dry and "## bbb" in dry
    assert "Pushed: no" not in dry.split("\n\n", 2)[2]

    # Once a pull request exists for one - a person opened it from the link,
    # and a later run found it - only the other is still listed.
    from apm.domain.enums import PublishStatus
    from apm.services.publish_ledger import PublishLedger

    ledger = PublishLedger.for_release(out, "bookworm")
    ledger.record(ledger.get("aaa").model_copy(update={
        "status": PublishStatus.PR_EXISTS, "pull_request": PullRequest(
            number=5, state="open", url="https://github.com/Arrcus/aaa/pull/5"),
    }))
    assert run(out_dir=out, all_packages=True, apply=True, push_only=True,
               confirm_count=0, container=container) == 0
    links = (out / "upstream-md-pr-links-bookworm.md").read_text()
    assert "## aaa" not in links and "## bbb" in links


def test_a_waiting_link_too_long_for_its_description_is_rebuilt(tmp_path):
    remotes, github, container, out = _setup_many(tmp_path, ["aaa"])
    assert run(out_dir=out, packages_wanted=["aaa"], apply=True, push_only=True,
               confirm=["aaa"], container=container) == 0
    ledger_path = out / "upstream-md-pr-results-bookworm.json"
    ledger = json.loads(ledger_path.read_text())
    full = ledger["entries"][0]["pr_create_url"]
    body = parse_qs(urlsplit(full).query)["body"][0]
    # As a link written without its body would be stored.
    ledger["entries"][0]["pr_create_url"] = full.split("&body=")[0]
    ledger_path.write_text(json.dumps(ledger))

    assert run(out_dir=out, all_packages=True, apply=True, push_only=True,
               confirm_count=0, container=container) == 0
    links = (out / "upstream-md-pr-links-bookworm.md").read_text()
    assert "The description is too long for the link" in links
    assert f"````markdown\n{body}\n````" in links


def test_a_closed_pull_request_is_never_pushed_again(tmp_path):
    remote, github, container, out = _setup(tmp_path)
    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
               confirm=["mstpd"], container=container) == 0
    pushed = remote.ref(BRANCH)
    # A person opened it from the link; a reviewer closed it and deleted the
    # branch. A run with a token finds that and records it.
    github.earlier = PullRequest(number=1, state="closed", head=BRANCH,
                                 head_sha=pushed,
                                 url="https://github.com/Arrcus/mstpd/pull/1")
    g.git(remote.bare, "update-ref", "-d", f"refs/heads/{BRANCH}")
    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True, recheck=True,
               confirm=["mstpd"], container=container) == 0
    entry = json.loads((out / "upstream-md-pr-results-bookworm.json")
                       .read_text())["entries"][0]
    assert entry["status"] == "PR_CLOSED"

    # Without a token the closed PR cannot be seen - the ledger remembers it.
    github.token = ""
    for recheck in (False, True):
        count = 1 if recheck else 0
        assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
                   recheck=recheck, confirm_count=count,
                   container=container) == 0
    assert remote.branches() == ["aminor"]
    assert len(container.publisher.workspaces.pushes) == 1
    entry = json.loads((out / "upstream-md-pr-results-bookworm.json")
                       .read_text())["entries"][0]
    assert entry["status"] == "PR_CLOSED"
    assert github.created == []


def test_a_release_never_pushes_a_second_branch_for_the_same_file(
        tmp_path, capsys):
    from apm.services.upstream_md_publisher import TRACKING_BRANCH

    remote, github, container, out = _setup(tmp_path)
    github.token = ""
    container.publisher.branch_template = TRACKING_BRANCH
    container.publisher.release_templates = {"trixie": "debian-upstream-tracking-trixie"}
    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
               confirm=["mstpd"], container=container) == 0
    pushed = remote.ref(TRACKING_BRANCH)
    assert pushed

    # trixie builds the same repository from the same aminor: same plan, the
    # same reviewed file, another release.
    plan = json.loads((out / "upstream-md-plan-bookworm.json").read_text())
    plan["release"] = "trixie"
    (out / "upstream-md-plan-trixie.json").write_text(json.dumps(plan))
    md = out / "upstream-md-trixie" / "mstpd" / "debian" / "upstream.md"
    md.parent.mkdir(parents=True)
    md.write_text((out / "upstream-md-bookworm" / "mstpd" / "debian" /
                   "upstream.md").read_text())
    trixie = _resolution(remote=remote, debian_release="trixie")
    container.mapping = FakeMapping({"mstpd": trixie})
    capsys.readouterr()

    # Naming it as a push is refused: there is nothing to push.
    assert run(release="trixie", out_dir=out, packages_wanted=["mstpd"],
               apply=True, confirm=["mstpd"], container=container) == 2
    assert run(release="trixie", out_dir=out, packages_wanted=["mstpd"],
               apply=True, confirm_count=0, container=container) == 0

    assert "another release's branch covers 1: never pushed" in capsys.readouterr().out
    ledger = json.loads((out / "upstream-md-pr-results-trixie.json").read_text())
    [entry] = ledger["entries"]
    assert entry["status"] == "COVERED" and entry["covered_by"] == "bookworm"
    assert (entry["branch"], entry["commit"]) == (TRACKING_BRANCH, pushed)
    assert remote.branches() == ["aminor", TRACKING_BRANCH]
    assert len(container.publisher.workspaces.pushes) == 1

    report = (out / "upstream-md-publishing-report.md").read_text()
    trixie_part = report[report.index("## Trixie"):]
    assert "### Covered by another release's branch (1)" in trixie_part
    assert "bookworm: `debian-upstream-tracking` → `aminor`" in trixie_part
    assert _verify_ledger(tmp_path) == 0
