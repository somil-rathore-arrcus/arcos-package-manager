"""The consolidated publishing report: each release apart, every package placed,
the links a person opens, and read-only checks on GitHub (a local fork here)."""

from __future__ import annotations

import json

from apm.domain.enums import PublishStatus
from apm.domain.models import PublishResult
from apm.gitio.transport import LocalGit
from apm.gitio.workspace import GitWorkspace
from apm.publishing_report import (
    REPORT_STEM, VERIFICATION_FILE, build, verify_branch, write,
)
from apm.services.publish_ledger import PublishLedger
from apm.services.upstream_md_publisher import sha256_text

import gitfixtures as g
from test_upstream_md_publisher import (
    FakeGitHub, PATH, _release_publisher, _target, Remote,
)

TRACKING = "debian-upstream-tracking"


def _plan(out, release, entries, skipped=()):
    (out / f"upstream-md-plan-{release}.json").write_text(json.dumps({
        "release": release, "skipped": list(skipped),
        "entries": [{"package": name, "status": "VERIFIED",
                     "arcos_repository": f"Arrcus/{name}", "arcos_branch": base,
                     "base_branch": base, "sha256": "0" * 64, "outcome": outcome}
                    for name, base, outcome in entries],
    }))


def _pushed(name, release, base, branch=TRACKING, **extra):
    return PublishResult(package=name, release=release,
                         status=PublishStatus.BRANCH_PUSHED, repository=f"Arrcus/{name}",
                         base_branch=base, branch=branch, commit=name[0] * 40,
                         pushed=True, **extra)


def _world(out):
    _plan(out, "bookworm", [("aaa", "aminor", "CREATED"), ("bbb", "aminor", "CREATED"),
                            ("ONL-standalone", "aminor", "CREATED"),
                            ("same", "aminor", "NO_CHANGE")],
          skipped=[{"package": "arcapi", "status": "NO_UPSTREAM",
                    "reason": "arrcus native"}])
    _plan(out, "trixie", [("aaa", "aminor", "CREATED"),
                          ("ddd", "aminor-trixie", "CREATED")])
    PublishLedger.for_release(out, "bookworm").record(_pushed("aaa", "bookworm", "aminor"))
    trixie = PublishLedger.for_release(out, "trixie")
    trixie.record(_pushed("ddd", "trixie", "aminor-trixie",
                          branch=f"{TRACKING}-trixie"))
    trixie.record(PublishResult(
        package="aaa", release="trixie", status=PublishStatus.COVERED,
        repository="Arrcus/aaa", base_branch="aminor", branch=TRACKING,
        commit="a" * 40, covered_by="bookworm"))


def test_each_release_has_its_own_pushed_covered_and_not_pushed(tmp_path):
    _world(tmp_path)

    data = build(tmp_path, {"ONL-standalone": "no debian/ directory"},
                 ["bookworm", "trixie"])

    bookworm, trixie = data["releases"]["bookworm"], data["releases"]["trixie"]
    assert list(data["releases"]) == ["bookworm", "trixie"]
    assert [r["package"] for r in bookworm["pushed"]] == ["aaa"]
    assert bookworm["covered"] == []
    assert {r["package"]: r["status"] for r in bookworm["not_pushed"]} == {
        "ONL-standalone": "SKIPPED_EXCLUDED", "arcapi": "NO_UPSTREAM",
        "bbb": "NOT_PUSHED", "same": "NO_CHANGE",
    }
    assert [r["package"] for r in trixie["pushed"]] == ["ddd"]
    [covered] = trixie["covered"]
    assert (covered["package"], covered["covered_by"], covered["branch"]) == \
        ("aaa", "bookworm", TRACKING)
    assert covered["compare_url"] == \
        f"https://github.com/Arrcus/aaa/compare/aminor...{TRACKING}"
    assert trixie["not_pushed"] == []


def test_the_markdown_lists_the_file_and_compare_links_per_release(tmp_path):
    _world(tmp_path)
    dated = tmp_path / "upstream-md-branches-2026-11-10.md"
    dated.write_text("# the preserved report\n")

    path = write(tmp_path, {}, ["bookworm", "trixie"])

    text = path.read_text()
    assert path.name == f"{REPORT_STEM}.md"
    bookworm = text[text.index("## Bookworm"):text.index("## Trixie")]
    trixie = text[text.index("## Trixie"):]
    assert f"https://github.com/Arrcus/aaa/blob/{TRACKING}/{PATH}" in bookworm
    assert f"https://github.com/Arrcus/aaa/compare/aminor...{TRACKING}" in bookworm
    assert "ddd" not in bookworm and "### Covered by another release's branch (0)" in bookworm
    assert f"https://github.com/Arrcus/ddd/compare/aminor-trixie...{TRACKING}-trixie" in trixie
    assert "### Covered by another release's branch (1)" in trixie
    assert "| aaa | Arrcus/aaa | bookworm: `debian-upstream-tracking` → `aminor` |" in trixie
    assert "This tool opens no pull request" in text
    assert json.loads(path.with_suffix(".json").read_text())["releases"]["trixie"]["covered"]
    # A dated report a person wrote is never touched.
    assert dated.read_text() == "# the preserved report\n"


def test_a_check_on_github_is_shown_only_for_the_commit_it_checked(tmp_path):
    _world(tmp_path)
    (tmp_path / VERIFICATION_FILE).write_text(json.dumps({"branches": [
        {"release": "bookworm", "package": "aaa", "commit": "a" * 40, "ok": True,
         "problems": [], "checked_at": "2026-11-11T10:00:00+00:00"},
        {"release": "trixie", "package": "ddd", "commit": "f" * 40, "ok": True,
         "problems": [], "checked_at": "2026-11-11T10:00:00+00:00"},
    ]}))

    text = write(tmp_path, {}, ["bookworm", "trixie"]).read_text()

    assert "| yes (2026-11-11) |" in text.split("## Trixie")[0]
    assert "| not checked |" in text.split("## Trixie")[1]
    assert "| Bookworm | 1 | 0 | 4 | 1/1 |" in text
    assert "| Trixie | 1 | 1 | 0 | 0/1 |" in text


def test_verifying_a_branch_reads_it_back_and_writes_nothing(tmp_path):
    remote = Remote(tmp_path / "remote")
    publisher = _release_publisher(tmp_path, FakeGitHub(token=""))
    target = _target(remote)
    pushed = publisher.publish(target, apply=True)
    record = pushed.model_copy(update={"repository": "Arrcus/mstpd"})
    branches = remote.branches()
    workspace = GitWorkspace(LocalGit(), str(tmp_path / "verify"), allow_push=False)
    workspace.ensure()

    good = verify_branch(workspace, remote.url, record, sha256_text(target.content))
    wrong_file = verify_branch(workspace, remote.url, record, "0" * 64)

    assert good["ok"] is True and good["problems"] == []
    assert good["target_tip"] == remote.ref("aminor")
    assert wrong_file["problems"] == [f"{PATH} is not the reviewed file"]

    # Someone else moves the branch: it no longer is what was recorded.
    g.git(remote.source, "fetch", "-q", str(remote.bare), f"{TRACKING}:moved")
    g.git(remote.source, "checkout", "-q", "moved")
    g.commit(remote.source, "other.txt", "x\n", "not ours")
    g.git(remote.source, "push", "-q", str(remote.bare), f"HEAD:refs/heads/{TRACKING}")
    moved = verify_branch(workspace, remote.url, record, sha256_text(target.content))
    assert moved["ok"] is False and "not the recorded" in moved["problems"][0]
    assert remote.branches() == branches
