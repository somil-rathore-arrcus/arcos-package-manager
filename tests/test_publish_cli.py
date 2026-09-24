"""apm publish-upstream-md, end to end against a local fork and a fake GitHub."""

from __future__ import annotations

import json
from types import SimpleNamespace

from apm.publish_upstream_md import run
from apm.services.upstream_md_publisher import UpstreamMdPublisher, sha256_text
from apm.services.upstream_md_service import UpstreamMdService

from test_upstream_md_publisher import (
    FakeGitHub, FakeMapping, FakeWorkspaces, Remote, _resolution,
)


def _setup(tmp_path):
    remote = Remote(tmp_path / "remote")
    resolution = _resolution(url=remote.url)
    service = UpstreamMdService()
    content = service.render(resolution)
    out = tmp_path / "out"
    md = out / "upstream-md" / "mstpd" / "debian" / "upstream.md"
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


def test_apply_opens_the_pr_records_it_and_a_rerun_does_nothing(tmp_path):
    remote, github, container, out = _setup(tmp_path)

    assert run(out_dir=out, packages_wanted=["mstpd"], apply=True,
               confirm=["mstpd"], container=container) == 0
    ledger = json.loads((out / "upstream-md-pr-results-bookworm.json").read_text())
    [entry] = ledger["entries"]
    assert entry["status"] == "PR_OPENED"
    assert entry["pull_request"]["url"].endswith("/pull/1")
    assert remote.branches() == ["aminor", "upstream-metadata/bookworm/mstpd"]

    # Already done: nothing left to confirm, nothing pushed, no second PR.
    assert run(out_dir=out, all_packages=True, apply=True, confirm_count=0,
               container=container) == 0
    assert len(github.created) == 1


def test_unknown_packages_are_refused(tmp_path):
    _, _, container, out = _setup(tmp_path)
    assert run(out_dir=out, packages_wanted=["nope"], container=container) == 1
