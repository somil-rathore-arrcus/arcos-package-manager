"""GitHub interactions, fully mocked.

No test here may reach the network or create a real pull request.
"""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from apm.domain.enums import ErrorCode, PreviewOutcome, ResolutionStatus
from apm.domain.models import (
    CherryPickResult, CommitInfo, CriticalityAssessment, Repository,
    UpstreamResolution,
)
from apm.domain.enums import Criticality
from apm.services.github_service import (
    GitHubError, GitHubService, PR_LINK_MAX_LENGTH, bare_pull_request_link,
    build_patch_pr_body, pull_request_link, repo_slug,
)


def _service(handler, token="tok"):
    transport = httpx.MockTransport(handler)
    return GitHubService(token=token, client=httpx.Client(transport=transport))


PR_PAYLOAD = {
    "number": 7, "html_url": "https://github.com/o/r/pull/7", "state": "open",
    "title": "t", "body": "b", "base": {"ref": "aminor"},
    "head": {"ref": "upstream/demo/1"}, "draft": False,
}


@pytest.mark.parametrize("url,expected", [
    ("ssh://git@github.com/Arrcus/pyrad.git", "Arrcus/pyrad"),
    ("git@github.com:Arrcus/pyrad.git", "Arrcus/pyrad"),
    ("https://github.com/Arrcus/pyrad", "Arrcus/pyrad"),
    ("https://salsa.debian.org/debian/x.git", None),
])
def test_repo_slug(url, expected):
    assert repo_slug(url) == expected


def test_create_pull_request_returns_the_url():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json=PR_PAYLOAD)

    pr = _service(handler).create_pull_request(
        "o/r", "upstream/demo/1", "aminor", "t", "b"
    )
    assert pr.url == "https://github.com/o/r/pull/7"
    assert pr.number == 7
    assert pr.already_existed is False


def test_an_existing_pull_request_is_reused_not_duplicated():
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=[PR_PAYLOAD])
        raise AssertionError("must not POST when a PR is already open")

    pr = _service(handler).create_pull_request(
        "o/r", "upstream/demo/1", "aminor", "t", "b"
    )
    assert pr.already_existed is True
    assert "POST" not in calls


def test_without_a_token_creating_a_pr_is_refused_before_any_request():
    def handler(request):
        raise AssertionError("must not call GitHub without a token")

    service = _service(handler, token="")
    assert service.can_create_pull_requests is False
    with pytest.raises(GitHubError) as excinfo:
        service.create_pull_request("o/r", "h", "b", "t", "body")
    assert excinfo.value.code is ErrorCode.AUTH_REQUIRED


def test_401_is_reported_as_auth_required():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(GitHubError) as excinfo:
        _service(handler).create_pull_request("o/r", "h", "b", "t", "body")
    assert excinfo.value.code is ErrorCode.AUTH_REQUIRED


def test_404_explains_that_private_repos_look_like_this():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(GitHubError) as excinfo:
        _service(handler).create_pull_request("o/r", "h", "b", "t", "body")
    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert "token" in str(excinfo.value).lower()


def test_pr_body_lists_commits_with_their_evidence():
    resolution = UpstreamResolution(
        package="demo", debian_release="bookworm",
        status=ResolutionStatus.VERIFIED,
        upstream_repository=Repository(url="https://github.com/u/d.git"),
        upstream_ref="master",
    )
    result = CherryPickResult(
        outcome=PreviewOutcome.CLEAN, package="demo", base_branch="aminor",
        new_branch="upstream/demo/1", applied=["a" * 40],
    )
    commits = [CommitInfo(
        sha="a" * 40, short_sha="a" * 12, subject="fix overflow",
        criticality=CriticalityAssessment(
            level=Criticality.CRITICAL, evidence=["references CVE-2026-1"],
            cve_ids=["CVE-2026-1"],
        ),
    )]
    body = build_patch_pr_body(resolution, result, commits=commits)
    assert "fix overflow" in body
    assert "CRITICAL" in body and "CVE-2026-1" in body
    assert "cherry-pick -x" in body
    assert "the target branch was not modified" in body.lower()


def test_repository_preflight_returns_the_repo():
    def handler(request):
        assert request.url.path == "/repos/Arrcus/mstpd"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={"full_name": "Arrcus/mstpd",
                                         "permissions": {"pull": True}})

    assert _service(handler).get_repository("Arrcus/mstpd")["full_name"] == \
        "Arrcus/mstpd"


def test_repository_preflight_names_missing_sso_authorisation():
    def handler(request):
        return httpx.Response(
            403, json={"message": "Resource protected by organization SAML"},
            headers={"X-GitHub-SSO": "required; url=https://github.com/orgs/Arrcus/sso"},
        )

    with pytest.raises(GitHubError) as raised:
        _service(handler).get_repository("Arrcus/mstpd")
    assert raised.value.code is ErrorCode.AUTH_REQUIRED
    assert "SSO" in str(raised.value)
    assert "orgs/Arrcus/sso" in raised.value.detail


def test_repository_preflight_needs_a_token_and_sends_nothing_without_one():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    with pytest.raises(GitHubError):
        _service(handler, token="").get_repository("Arrcus/mstpd")
    assert calls == []


def test_find_pull_request_can_include_closed_ones():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=[{
            **PR_PAYLOAD, "state": "closed",
            "head": {"ref": "upstream-metadata/bookworm/mstpd", "sha": "c5b5" * 10},
        }])

    pr = _service(handler).find_pull_request(
        "Arrcus/mstpd", "upstream-metadata/bookworm/mstpd", "aminor", state="all",
    )
    assert seen["state"] == "all"
    assert seen["head"] == "Arrcus:upstream-metadata/bookworm/mstpd"
    assert pr.state == "closed"
    # A name every proposal in a repository shares is matched by this commit.
    assert pr.head_sha == "c5b5" * 10


def test_a_merged_pull_request_is_not_reported_as_closed():
    def handler(request):
        return httpx.Response(200, json=[{**PR_PAYLOAD, "state": "closed",
                                          "merged_at": "2026-09-25T10:00:00Z"}])

    pr = _service(handler).find_pull_request("Arrcus/mstpd", "b", "aminor",
                                             state="all")
    assert pr.state == "merged"


# -- the push-only link -------------------------------------------------------

BRANCH = "upstream-metadata/bookworm/mstpd"


def _query(link):
    return {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}


def test_the_link_opens_githubs_form_for_exactly_this_branch():
    title = "mstpd: add debian/upstream.md with verified upstream"
    body = "Problem Description :\nA | B & C #1 100% ünï\n\n```text\nx\n```"

    link, complete = pull_request_link("Arrcus/mstpd", "aminor", BRANCH,
                                       title, body)

    assert complete is True
    # Slashes in the branch stay as they are; everything in the form is
    # percent-encoded, so no character of the body can end the parameter.
    assert link.startswith(
        f"https://github.com/Arrcus/mstpd/compare/aminor...{BRANCH}?quick_pull=1&"
    )
    assert "title=mstpd%3A%20add%20debian%2Fupstream.md%20with%20verified%20upstream" in link
    assert " " not in link and "\n" not in link and "#" not in link
    assert link.count("&") == 2
    assert _query(link) == {"quick_pull": "1", "title": title, "body": body}


def test_a_body_too_long_for_a_url_is_left_out_whole():
    body = "Problem Description :\n" + "x" * PR_LINK_MAX_LENGTH

    link, complete = pull_request_link("Arrcus/mstpd", "aminor", BRANCH, "t", body)

    assert complete is False
    assert len(link) <= PR_LINK_MAX_LENGTH
    assert _query(link) == {"quick_pull": "1", "title": "t"}


def test_the_length_limit_counts_the_encoded_link():
    body = " " * 100  # three characters each once encoded
    short, _ = pull_request_link("o/r", "aminor", BRANCH, "t")

    _, fits = pull_request_link("o/r", "aminor", BRANCH, "t", body,
                                max_length=len(short) + 6 + 300)
    _, too_long = pull_request_link("o/r", "aminor", BRANCH, "t", body,
                                    max_length=len(short) + 6 + 299)
    assert (fits, too_long) == (True, False)


def test_a_link_without_a_title_or_body_is_the_bare_compare_page():
    link, complete = pull_request_link("Arrcus/mstpd", "aminor", BRANCH)
    assert (link, complete) == (
        f"https://github.com/Arrcus/mstpd/compare/aminor...{BRANCH}?quick_pull=1",
        True,
    )
    full, _ = pull_request_link("Arrcus/mstpd", "aminor", BRANCH, "t", "b")
    assert bare_pull_request_link(full) == link
    assert bare_pull_request_link(None) == ""


def test_the_link_honours_another_web_host():
    link, _ = pull_request_link("o/r", "main", "upstream-metadata/x",
                                web_url="https://ghe.example.com/")
    assert link == "https://ghe.example.com/o/r/compare/main...upstream-metadata/x?quick_pull=1"


# -- CI status --------------------------------------------------------------------
#
# Only GitHub's own answer is ever a pass. No access, no permission, or no
# answer is UNAVAILABLE; nothing reported yet is NONE.

from apm.domain.enums import CiState  # noqa: E402
from apm.domain.models import AppliedCommit, ValidationReport, ValidationRun  # noqa: E402


def _ci(check_runs=(), statuses=(), runs_status=200, status_status=200):
    def handler(request):
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(runs_status, json={"check_runs": list(check_runs)}
                                  if runs_status == 200 else {"message": "Forbidden"})
        if request.url.path.endswith("/status"):
            return httpx.Response(status_status, json={"statuses": list(statuses)})
        return httpx.Response(404, json={"message": "Not Found"})
    return _service(handler).ci_status("Arrcus/mstpd", "c" * 40)


def _run(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion,
            "html_url": f"https://github.com/Arrcus/mstpd/runs/{name}",
            "output": {"title": f"{name} title"}}


def test_without_a_token_checks_are_unavailable_not_passing():
    status = GitHubService(token="").ci_status("Arrcus/mstpd", "c" * 40)
    assert status.state is CiState.UNAVAILABLE
    assert "APM_GITHUB_TOKEN" in status.reason and status.checks == []


def test_every_check_passing_is_success():
    status = _ci([_run("description"), _run("build", conclusion="skipped")],
                 [{"context": "jenkins/jira", "state": "success",
                   "target_url": "https://ci/1", "description": "ok"}])
    assert status.state is CiState.SUCCESS
    assert {c.name for c in status.checks} == {"description", "build", "jenkins/jira"}
    assert status.checks[0].url.endswith("/description")


def test_one_failed_check_fails_the_commit_even_while_others_run():
    status = _ci([_run("description", conclusion="failure"),
                  _run("build", status="in_progress", conclusion=None)])
    assert status.state is CiState.FAILURE


def test_a_failed_commit_status_fails_the_commit():
    status = _ci([_run("description")],
                 [{"context": "jenkins/jira", "state": "error"}])
    assert status.state is CiState.FAILURE


def test_a_running_check_is_pending():
    status = _ci([_run("description"), _run("build", status="queued", conclusion=None)])
    assert status.state is CiState.PENDING


def test_nothing_reported_is_none_not_success():
    assert _ci().state is CiState.NONE


def test_a_token_that_cannot_read_checks_is_unavailable():
    status = _ci(runs_status=403)
    assert status.state is CiState.UNAVAILABLE
    assert "403" in status.reason


def test_github_unreachable_is_unavailable():
    def handler(request):
        raise httpx.ConnectTimeout("timed out")
    status = _service(handler).ci_status("Arrcus/mstpd", "c" * 40)
    assert status.state is CiState.UNAVAILABLE
    assert "could not be reached" in status.reason


# -- patch pull request description -----------------------------------------------

def _patch_result(**extra):
    return CherryPickResult(
        outcome=PreviewOutcome.CLEAN, package="demo", base_branch="aminor",
        new_branch="upstream/demo/1", applied=["a" * 40, "b" * 40],
        base_sha="3" * 40,
        commits=[AppliedCommit(sha="a" * 40, subject="fix overflow"),
                 AppliedCommit(sha="b" * 40, subject="tidy")],
        **extra,
    )


def _patch_resolution():
    return UpstreamResolution(
        package="demo", debian_release="bookworm",
        status=ResolutionStatus.VERIFIED,
        upstream_repository=Repository(url="https://github.com/u/d.git"),
        upstream_ref="master", merge_base="9" * 40,
    )


def test_patch_pr_body_starts_with_the_sections_arcos_ci_requires():
    body = build_patch_pr_body(_patch_resolution(), _patch_result())
    lines = body.splitlines()
    assert lines[0] == "Problem Description :" and len(lines[1]) > 2
    for heading in ("Root Cause :", "Fix Details :"):
        i = lines.index(heading)
        assert len(lines[i + 1]) > 2
    assert "999999999999" in body  # the merge base
    assert "33333333" in body      # the base the branch was built on


def test_patch_pr_body_links_each_upstream_commit():
    body = build_patch_pr_body(_patch_resolution(), _patch_result())
    assert f"https://github.com/u/d/commit/{'a' * 40}" in body
    assert "fix overflow" in body and "tidy" in body


def test_patch_pr_body_says_when_nothing_was_validated():
    body = build_patch_pr_body(_patch_resolution(), _patch_result(
        validation=ValidationReport(configured=False, message="none")))
    assert "nothing was built or tested" in body


def test_patch_pr_body_reports_each_validation_command():
    validation = ValidationReport(
        configured=True, passed=True, message="All 1 validation command(s) passed.",
        runs=[ValidationRun(command="debian/rules build", exit_code=0,
                            duration_seconds=12.5)])
    body = build_patch_pr_body(_patch_resolution(), _patch_result(validation=validation))
    assert "`debian/rules build` — exit 0 (12.5s)" in body
    assert "All 1 validation command(s) passed." in body


# -- failures a caller must be able to tell apart -------------------------------

def _answer(status, headers=None, message="Forbidden"):
    def handler(request):
        return httpx.Response(status, headers=headers or {}, json={"message": message})
    return _service(handler)


@pytest.mark.parametrize("status,code", [
    (401, ErrorCode.AUTH_REQUIRED), (403, ErrorCode.AUTH_REQUIRED),
    (404, ErrorCode.NOT_FOUND), (500, ErrorCode.INTERNAL),
])
def test_a_pull_request_lookup_that_fails_says_so_instead_of_none(status, code):
    with pytest.raises(GitHubError) as excinfo:
        _answer(status).find_pull_request("Arrcus/mstpd", "b", "aminor", state="all")
    assert excinfo.value.code is code


@pytest.mark.parametrize("status,headers,message", [
    (429, {"Retry-After": "60"}, "Too Many Requests"),
    (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1793300000"},
     "API rate limit exceeded"),
    (403, {}, "You have exceeded a secondary rate limit"),
])
def test_a_spent_quota_is_a_rate_limit_not_a_bad_token(status, headers, message):
    service = _answer(status, headers, message)
    for call in (lambda: service.get_repository("Arrcus/mstpd"),
                 lambda: service.find_pull_request("Arrcus/mstpd", "b", "aminor"),
                 lambda: service.get_pull_request("Arrcus/mstpd", 7)):
        with pytest.raises(GitHubError) as excinfo:
            call()
        assert excinfo.value.code is ErrorCode.RATE_LIMITED
        assert "retry" in str(excinfo.value)


def test_creating_a_pull_request_on_a_spent_quota_is_a_rate_limit():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(429, json={"message": "Too Many Requests"})
    with pytest.raises(GitHubError) as excinfo:
        _service(handler).create_pull_request("Arrcus/mstpd", "b", "aminor", "t", "b")
    assert excinfo.value.code is ErrorCode.RATE_LIMITED


def test_reading_a_pull_request_needs_a_token():
    with pytest.raises(GitHubError) as excinfo:
        _service(lambda r: httpx.Response(200, json=PR_PAYLOAD), token="") \
            .get_pull_request("Arrcus/mstpd", 7)
    assert excinfo.value.code is ErrorCode.AUTH_REQUIRED


def test_a_failure_on_a_later_page_of_checks_is_not_a_pass():
    pages = {
        "1": [_run(f"ok-{i}") for i in range(100)],
        "2": [_run("late", conclusion="failure")],
    }

    def handler(request):
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={
                "total_count": 101,
                "check_runs": pages.get(request.url.params.get("page"), [])})
        return httpx.Response(200, json={"total_count": 0, "statuses": []})

    status = _service(handler).ci_status("Arrcus/mstpd", "c" * 40)
    assert status.state is CiState.FAILURE
    assert len(status.checks) == 101


def test_more_checks_than_can_be_read_are_unavailable_not_a_pass():
    def handler(request):
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(200, json={"total_count": 5000,
                                             "check_runs": [_run("ok")] * 100})
        return httpx.Response(200, json={"statuses": []})

    status = _service(handler).ci_status("Arrcus/mstpd", "c" * 40)
    assert status.state is CiState.UNAVAILABLE
    assert "not all read" in status.reason


def test_a_spent_quota_leaves_checks_unavailable():
    status = _answer(429, {}, "Too Many Requests").ci_status("Arrcus/mstpd", "c" * 40)
    assert status.state is CiState.UNAVAILABLE
    assert "rate limit" in status.reason
