"""The results ledger: the only record of which branches and PRs exist."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from apm.domain.enums import PublishStatus
from apm.domain.models import PublishResult, PullRequest
from apm.services.github_service import pull_request_link
from apm.services.publish_ledger import PublishLedger, ledger_paths, supersedes

VERIFY = Path(__file__).resolve().parents[1] / "scripts" / "verify_pr_ledger.py"
BRANCH = "upstream-metadata/bookworm/mstpd"


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


def _pushed(**extra):
    link, _ = pull_request_link("Arrcus/mstpd", "aminor", BRANCH,
                                "mstpd: add debian/upstream.md", "Problem Description :\nx")
    values = dict(repository="Arrcus/mstpd", base_branch="aminor", branch=BRANCH,
                  commit="a" * 40, pushed=True, pr_create_url=link)
    values.update(extra)
    return _result("mstpd", PublishStatus.BRANCH_PUSHED, **values)


def test_a_pushed_only_branch_keeps_its_full_link_and_shows_a_short_one(tmp_path):
    ledger = PublishLedger.for_release(tmp_path, "bookworm")
    entry = _pushed()
    ledger.record(entry)

    payload = json.loads(ledger.json_path.read_text())
    assert payload["counts"] == {"BRANCH_PUSHED": 1}
    assert payload["entries"][0]["pr_create_url"] == entry.pr_create_url
    assert "body=" in payload["entries"][0]["pr_create_url"]

    table = ledger.md_path.read_text()
    assert (f"| mstpd | BRANCH_PUSHED | {BRANCH} | aaaaaaaaaaaa | "
            f"https://github.com/Arrcus/mstpd/compare/aminor...{BRANCH}"
            f"?quick_pull=1 |  |") in table
    assert "title=" not in table and "Problem" not in table

    again = PublishLedger.for_release(tmp_path, "bookworm")
    assert again.get("mstpd").status is PublishStatus.BRANCH_PUSHED
    assert again.get("mstpd").pr_create_url == entry.pr_create_url


def _verify(tmp_path, *entries):
    out = tmp_path / "out"
    ledger = PublishLedger.for_release(out, "bookworm")
    ledger.record_all(entries)
    spec = importlib.util.spec_from_file_location("verify_pr_ledger", VERIFY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    return module.main("bookworm"), module.failures


def test_the_ledger_check_accepts_a_pushed_only_branch(tmp_path, capsys):
    code, failures = _verify(tmp_path, _pushed())
    assert (code, failures) == (0, [])


@pytest.mark.parametrize("change", [
    {"pushed": False},
    {"commit": "a" * 12},
    {"pr_create_url": None},
    {"pr_create_url": "https://github.com/Arrcus/other/compare/aminor..."
                      f"{BRANCH}?quick_pull=1"},
    {"pr_create_url": "https://github.com/Arrcus/mstpd/compare/main..."
                      f"{BRANCH}?quick_pull=1"},
    {"pr_create_url": "https://github.com/Arrcus/mstpd/compare/aminor..."
                      f"{BRANCH}-other?quick_pull=1"},
    {"pull_request": PullRequest(number=1, url="https://github.com/Arrcus/mstpd/pull/1")},
])
def test_the_ledger_check_rejects_a_pushed_only_entry_that_is_not_what_it_says(
        tmp_path, capsys, change):
    code, failures = _verify(tmp_path, _pushed(**change))
    assert code == 1
    assert any("pushed-only" in f for f in failures)


# -- what may replace what ----------------------------------------------------------

S = PublishStatus
_PR = PullRequest(number=3, url="https://github.com/Arrcus/mstpd/pull/3")


def _opened(status=S.PR_OPENED):
    return _result("mstpd", status, branch=BRANCH, commit="a" * 40, pushed=True,
                   pull_request=_PR)


@pytest.mark.parametrize("later", [
    _result("mstpd", S.DRIFT, error="upstream master moved"),
    _result("mstpd", S.SKIPPED_EXCLUDED, error="excluded on the command line"),
    _result("mstpd", S.SKIPPED_NEEDS_REVIEW, error="the mapping now says NEEDS_REVIEW"),
    _result("mstpd", S.ERROR, error="could not list refs"),
    _result("mstpd", S.BRANCH_EXISTS, commit="b" * 40),
    _result("mstpd", S.PUSH_FAILED, error="rejected"),
    _result("mstpd", S.CONFLICT),
])
def test_a_run_that_pushed_nothing_never_erases_a_pushed_branch(tmp_path, later):
    ledger = PublishLedger.for_release(tmp_path, "bookworm")
    ledger.record(_pushed())

    assert ledger.record(later) is False

    again = PublishLedger.for_release(tmp_path, "bookworm").get("mstpd")
    assert again.status is S.BRANCH_PUSHED
    assert (again.commit, again.pushed) == ("a" * 40, True)
    assert again.pr_create_url == _pushed().pr_create_url


@pytest.mark.parametrize("later", [
    _result("mstpd", S.BRANCH_PUSHED, commit="a" * 40, pushed=True),
    _result("mstpd", S.DRIFT, commit="a" * 40, pushed=True),
    _result("mstpd", S.PR_FAILED, commit="a" * 40, pushed=True),
    _result("mstpd", S.NO_CHANGE),
    _opened(),
    _opened(S.PR_MERGED),
    _opened(S.PR_CLOSED),
])
def test_a_pushed_branch_gives_way_to_what_still_knows_the_branch(later):
    assert supersedes(later, _pushed())


@pytest.mark.parametrize("previous", [
    _opened(S.PR_OPENED), _opened(S.PR_EXISTS), _opened(S.PR_MERGED),
    _opened(S.PR_CLOSED),
    _result("mstpd", S.PR_FAILED, branch=BRANCH, commit="a" * 40, pushed=True),
    _result("mstpd", S.DRIFT, branch=BRANCH, commit="a" * 40, pushed=True),
])
def test_every_record_before_push_only_is_replaced_as_it_always_was(tmp_path,
                                                                    previous):
    """Only BRANCH_PUSHED is guarded: a run that never meets one writes exactly
    what it wrote before push-only existed."""
    ledger = PublishLedger.for_release(tmp_path, "bookworm")
    ledger.record(previous)

    for later in (_result("mstpd", S.SKIPPED_EXCLUDED), _result("mstpd", S.DRIFT),
                  _result("mstpd", S.NO_CHANGE), _pushed()):
        assert supersedes(later, previous)
    assert ledger.record(_result("mstpd", S.ERROR, error="x")) is True
    assert PublishLedger.for_release(tmp_path, "bookworm").get("mstpd").status \
        is S.ERROR


def test_anything_replaces_a_record_of_nothing_that_exists():
    for status in (S.DRY_RUN_OK, S.DRIFT, S.ERROR, S.PUSH_FAILED, S.NO_CHANGE):
        assert supersedes(_result("mstpd", S.SKIPPED_EXCLUDED),
                          _result("mstpd", status))
    assert supersedes(_result("mstpd", S.DRIFT), None)
    # A BRANCH_PUSHED entry without its commit guards nothing.
    assert supersedes(_result("mstpd", S.ERROR), _pushed(commit=None))


def test_record_all_writes_what_it_may_and_keeps_the_rest(tmp_path):
    ledger = PublishLedger.for_release(tmp_path, "bookworm")
    ledger.record_all([_pushed(), _result("linux", S.ERROR)])

    written = ledger.record_all([_result("mstpd", S.SKIPPED_EXCLUDED),
                                 _result("linux", S.SKIPPED_EXCLUDED)])

    assert written == 1
    assert ledger.get("mstpd").status is S.BRANCH_PUSHED
    assert ledger.get("linux").status is S.SKIPPED_EXCLUDED
