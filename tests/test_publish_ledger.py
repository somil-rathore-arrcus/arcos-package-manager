"""The results ledger: the only record of which branches and PRs exist."""

from __future__ import annotations

import json

import pytest

from apm.domain.enums import PublishStatus
from apm.domain.models import PublishResult, PullRequest
from apm.services.publish_ledger import PublishLedger, ledger_paths


def _result(package, status, **extra):
    return PublishResult(package=package, release="bookworm", status=status,
                         **extra)


def test_results_survive_a_reload_and_render_a_table(tmp_path):
    ledger = PublishLedger.for_release(tmp_path, "bookworm")
    ledger.record(_result(
        "mstpd", PublishStatus.PR_OPENED,
        branch="upstream-metadata/bookworm/mstpd", commit="a" * 40,
        pull_request=PullRequest(number=3, url="https://github.com/Arrcus/mstpd/pull/3"),
        diff="+secret diff body",
    ))
    ledger.record(_result("linux", PublishStatus.PUSH_FAILED,
                          error="remote rejected | policy"))

    again = PublishLedger.for_release(tmp_path, "bookworm")
    assert again.get("mstpd").pull_request.number == 3
    assert again.get("mstpd").updated_at is not None
    assert [e.package for e in again.entries()] == ["linux", "mstpd"]

    payload = json.loads(ledger.json_path.read_text())
    assert payload["counts"] == {"PR_OPENED": 1, "PUSH_FAILED": 1}
    assert "diff" not in payload["entries"][0]

    table = ledger.md_path.read_text()
    assert "| mstpd | PR_OPENED | upstream-metadata/bookworm/mstpd | aaaaaaaaaaaa | " \
           "https://github.com/Arrcus/mstpd/pull/3 |  |" in table
    assert "remote rejected \\| policy" in table


def test_a_dry_run_ledger_is_a_different_file(tmp_path):
    real, _ = ledger_paths(tmp_path, "bookworm")
    dry, _ = ledger_paths(tmp_path, "bookworm", dry_run=True)
    assert real != dry
    assert real.name == "upstream-md-pr-results-bookworm.json"


def test_an_unreadable_ledger_is_never_silently_replaced(tmp_path):
    path, _ = ledger_paths(tmp_path, "bookworm")
    path.write_text("{not json")
    with pytest.raises(RuntimeError):
        PublishLedger.for_release(tmp_path, "bookworm")
    assert path.read_text() == "{not json"
