"""Where a pushed branch stands, from git refs and - with a token - the API.

The refs prove only what they can: that a pull request was opened from the
branch's commit, and its number. Checks are never reported passing without
GitHub's answer.
"""

from __future__ import annotations

import pytest

from apm.domain.enums import CiState, ErrorCode
from apm.domain.models import CiStatus, PullRequest
from apm.gitio.transport import GitResult
from apm.services.publish_status import (
    PublishStatusError, PublishStatusService, validate_own_branch,
)

URL = "ssh://git@github.com/Arrcus/mstpd.git"
BRANCH = "upstream-metadata/bookworm/mstpd"
HEAD = "c5b52470cc029e8db4a7c2e5d46196338abb4d58"


class FakeTransport:
    def __init__(self, stdout="", ok=True, stderr=""):
        self.result = GitResult(ok, stdout, stderr)
        self.calls = []

    def run(self, args, timeout=None):
        self.calls.append(args)
        return self.result


class FakeTransports:
    def __init__(self, transport):
        self.transport = transport

    def for_url(self, url):
        return self.transport


class FakeGitHub:
    def __init__(self, token=""):
        self.token = token
        self.lookups = []

    def find_pull_request(self, slug, head, base, state="open"):
        self.lookups.append((slug, head, base, state))
        return PullRequest(number=35, url="https://github.com/Arrcus/mstpd/pull/35",
                           state="open", head=head, base=base)

    def ci_status(self, slug, sha):
        if not self.token:
            return CiStatus(state=CiState.UNAVAILABLE, sha=sha, reason="no API credential")
        return CiStatus(state=CiState.PENDING, sha=sha)


def _refs(*lines):
    return "".join(f"{sha}\t{ref}\n" for sha, ref in lines)


def _service(stdout="", ok=True, stderr="", token=""):
    transport = FakeTransport(stdout, ok, stderr)
    github = FakeGitHub(token)
    return PublishStatusService(FakeTransports(transport), github), transport, github


def test_a_pull_request_opened_from_the_branch_is_found_without_a_token():
    service, transport, _ = _service(_refs(
        ("9bfe3d63804150f3deef7071e5f4c34cdd3d5534", "refs/pull/34/head"),
        (HEAD, f"refs/heads/{BRANCH}"),
        (HEAD, "refs/pull/35/head"),
    ))
    report = service.status(package="mstpd", repository_url=URL, branch=BRANCH,
                            base="aminor")
    assert report.branch_exists and report.head_sha == HEAD
    assert [(p.number, p.url) for p in report.pull_requests] == [
        (35, "https://github.com/Arrcus/mstpd/pull/35")]
    # Only what refs can prove: no state, and the checks need the API.
    assert report.pull_request is None and report.api_access is False
    assert report.ci.state is CiState.UNAVAILABLE
    assert transport.calls == [["ls-remote", URL, f"refs/heads/{BRANCH}",
                                "refs/pull/*/head"]]


def test_no_pull_request_yet_is_an_empty_list():
    service, _, _ = _service(_refs(
        (HEAD, f"refs/heads/{BRANCH}"),
        ("9bfe3d63804150f3deef7071e5f4c34cdd3d5534", "refs/pull/34/head"),
    ))
    report = service.status(package="mstpd", repository_url=URL, branch=BRANCH)
    assert report.branch_exists and report.pull_requests == []


def test_a_branch_not_on_the_fork_has_nothing_to_check():
    service, _, _ = _service(_refs(
        ("9bfe3d63804150f3deef7071e5f4c34cdd3d5534", "refs/pull/34/head")))
    report = service.status(package="mstpd", repository_url=URL, branch=BRANCH)
    assert report.branch_exists is False and report.head_sha is None
    assert report.ci.state is CiState.NONE and "not on Arrcus/mstpd" in report.ci.reason


def test_refs_that_cannot_be_read_are_an_error_not_an_empty_answer():
    service, _, _ = _service(ok=False, stderr="fatal: Could not read from remote")
    with pytest.raises(PublishStatusError) as excinfo:
        service.status(package="mstpd", repository_url=URL, branch=BRANCH)
    assert excinfo.value.code is ErrorCode.REPOSITORY_UNAVAILABLE


def test_with_a_token_the_api_adds_state_and_checks():
    service, _, github = _service(_refs((HEAD, f"refs/heads/{BRANCH}")), token="tok")
    report = service.status(package="mstpd", repository_url=URL, branch=BRANCH,
                            base="aminor")
    assert report.api_access is True
    assert report.pull_request.number == 35
    assert github.lookups == [("Arrcus/mstpd", BRANCH, "aminor", "all")]
    assert report.ci.state is CiState.PENDING and report.ci.sha == HEAD


@pytest.mark.parametrize("branch", [
    "aminor", "main", "refs/heads/aminor", "upstream-metadata/../aminor",
    "upstream/x;rm -rf /", "",
])
def test_only_branches_this_tool_creates_are_looked_up(branch):
    with pytest.raises(PublishStatusError) as excinfo:
        validate_own_branch(branch)
    assert excinfo.value.code is ErrorCode.INVALID_REQUEST


def test_a_non_github_repository_is_refused():
    service, _, _ = _service()
    with pytest.raises(PublishStatusError):
        service.status(package="x", repository_url="https://salsa.debian.org/x.git",
                       branch=BRANCH)
