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
        return httpx.Response(200, json=[{**PR_PAYLOAD, "state": "closed"}])

    pr = _service(handler).find_pull_request(
        "Arrcus/mstpd", "upstream-metadata/bookworm/mstpd", "aminor", state="all",
    )
    assert seen["state"] == "all"
    assert seen["head"] == "Arrcus:upstream-metadata/bookworm/mstpd"
    assert pr.state == "closed"


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
