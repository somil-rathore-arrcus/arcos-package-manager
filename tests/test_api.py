"""The API layer, with services replaced.

These check routing, validation and error mapping - that a missing branch comes
back 404 and a missing token 401, so the UI can tell them apart. The services
themselves are covered by their own tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apm.api import deps
from apm.api.main import create_app
from apm.domain.enums import (
    CommitClass, Criticality, ErrorCode, PreviewOutcome, PublishStatus,
    ResolutionMethod, ResolutionMode, ResolutionStatus, UpstreamMdOutcome,
    VerificationLevel,
)
from apm.domain.models import (
    Branch, CherryPickPreview, CherryPickResult, CommitInfo, ComparisonResult,
    ComparisonSummary, CriticalityAssessment, DebianRelease, Package,
    PublishResult, PullRequest, Repository, ResolutionEvidence,
    UpstreamMdDocument, UpstreamResolution,
)
from apm.services.comparison_service import ComparisonError
from apm.services.metadata_batch_service import MetadataBatchService
from apm.services.patch_service import PatchError
from apm.services.upstream_md_publisher import sha256_text
from apm.services.upstream_service import UpstreamServiceError


RESOLUTION = UpstreamResolution(
    package="pyrad", debian_release="bookworm",
    status=ResolutionStatus.VERIFIED, mode=ResolutionMode.AUTO,
    method=ResolutionMethod.DEP12, confidence="high",
    arcos_repository="ssh://git@github.com/Arrcus/pyrad.git",
    github_repository="Arrcus/pyrad", arcos_branch="aminor",
    arcos_commit="3b043f16bb8e",
    upstream_repository=Repository(url="https://github.com/wichert/pyrad.git"),
    upstream_ref="master", upstream_commit="074aa3d33999",
    evidence=[ResolutionEvidence(kind="dep12", detail="DEP-12 Repository field")],
)

COMMIT = CommitInfo(
    sha="d" * 40, short_sha="d" * 12, subject="fix overflow",
    classification=CommitClass.MISSING_UPSTREAM,
    criticality=CriticalityAssessment(
        level=Criticality.CRITICAL, evidence=["references CVE-2026-1"],
        cve_ids=["CVE-2026-1"],
    ),
)

COMPARISON = ComparisonResult(
    package="pyrad", debian_release="bookworm",
    arcos_repository="ssh://git@github.com/Arrcus/pyrad.git",
    arcos_branch="aminor", upstream_repository="https://github.com/wichert/pyrad.git",
    upstream_ref="master",
    summary=ComparisonSummary(
        merge_base="9" * 40, has_common_ancestor=True, missing_upstream=1,
        arcos_only=2, already_backported=1, critical=1,
    ),
    missing_upstream=[COMMIT],
)


class FakeCatalog:
    def releases(self):
        return [DebianRelease(id="bookworm", name="Debian Bookworm",
                              suite="oldstable", version="12")]

    def packages(self, release=None):
        pkgs = [
            Package(name="pyrad", arcos_repository="ssh://git@github.com/Arrcus/pyrad.git",
                    github_repository="Arrcus/pyrad", releases=["bookworm", "trixie"]),
            Package(name="zenoh", arcos_repository="ssh://git@github.com/Arrcus/zenoh.git",
                    github_repository="Arrcus/zenoh", releases=["bookworm"]),
        ]
        return [p for p in pkgs if release is None or p.ships_in(release)]

    def package(self, name):
        return next((p for p in self.packages() if p.name == name), None)

    def branches(self, package, release):
        return [Branch(name="aminor", is_configured=True),
                Branch(name="main", is_default=True)]


class FakeUpstream:
    def __init__(self):
        self.manual_calls = []

    def resolve(self, package, release, arcos_branch=None, refresh=False):
        if package == "nope":
            raise UpstreamServiceError(ErrorCode.NOT_FOUND, "No package 'nope'.")
        out = RESOLUTION.model_copy(deep=True)
        out.package = package
        if package == "babeltrace":
            out.status = ResolutionStatus.NEEDS_REVIEW
        if package == "arcapi":
            out.status = ResolutionStatus.NO_UPSTREAM
        return out

    def resolve_manual(self, package, release, repository, ref, arcos_branch=None):
        self.manual_calls.append((repository, ref))
        if ref == "missing":
            raise UpstreamServiceError(
                ErrorCode.BRANCH_NOT_FOUND, f"{repository} has no ref '{ref}'."
            )
        out = RESOLUTION.model_copy(deep=True)
        out.mode = ResolutionMode.MANUAL
        out.upstream_repository = Repository(url=repository)
        out.upstream_ref = ref
        return out


class FakeComparison:
    def compare(self, resolution, arcos_branch=None, refresh=False):
        if resolution.package == "stranger":
            raise ComparisonError(
                ErrorCode.NO_COMMON_ANCESTOR, "shares no history"
            )
        return COMPARISON


class FakePatches:
    def preview(self, selection, repository, base_ref=None):
        if "conflict" in selection.shas:
            return CherryPickPreview(
                outcome=PreviewOutcome.CONFLICT, package=selection.package,
                arcos_branch=selection.arcos_branch, failed_sha="conflict",
                conflicts=["shared.txt"],
            )
        return CherryPickPreview(
            outcome=PreviewOutcome.CLEAN, package=selection.package,
            arcos_branch=selection.arcos_branch, applied=selection.shas,
        )

    def cherry_pick(self, selection, repository, branch_name=None, base_ref=None,
                    push=False):
        if branch_name == base_ref:
            raise PatchError(ErrorCode.INVALID_REQUEST, "same branch")
        return CherryPickResult(
            outcome=PreviewOutcome.CLEAN, package=selection.package,
            base_branch=base_ref or selection.arcos_branch,
            new_branch=branch_name or "upstream/pyrad/x",
            applied=selection.shas, pushed=push,
        )


class FakeGitHub:
    """Lookups only, as the real client: there is no method that opens a pull
    request, so a route calling one would fail; `created` stays empty."""

    def __init__(self, token="tok"):
        self.token = token
        self.created = []

    @property
    def has_token(self):
        return bool(self.token)

    def get_pull_request(self, slug, number):
        return PullRequest(number=number, url=f"https://github.com/o/r/pull/{number}")

    def find_pull_request(self, slug, head, base, state="open"):
        return None

    def ci_status(self, slug, sha):
        from apm.domain.enums import CiState
        from apm.domain.models import CiStatus
        if not self.token:
            return CiStatus(state=CiState.UNAVAILABLE, sha=sha, reason="no API credential")
        return CiStatus(state=CiState.SUCCESS, sha=sha)


class FakeRefs:
    """ls-remote of Arrcus/pyrad: a pushed patch branch with PR #9 opened from it."""

    PUSHED = "e" * 40

    def __init__(self):
        self.calls = []

    def for_url(self, url):
        return self

    def run(self, args, timeout=None):
        from apm.gitio.transport import GitResult
        self.calls.append(args)
        return GitResult(True, f"{self.PUSHED}\trefs/heads/upstream/pyrad/x\n"
                               f"{self.PUSHED}\trefs/pull/9/head\n"
                               f"{'f' * 40}\trefs/pull/8/head\n", "")


class FakePublisher:
    """Records what the route asked for; the real publisher has its own tests."""

    def __init__(self):
        self.calls = []
        self.preflights = 0
        self.exclude = {}

    def preflight(self, push_only=True):
        self.preflights += 1

    def publish(self, target, *, apply, title_prefix="", prior=None,
                push_only=True, others=()):
        self.calls.append({"package": target.package, "apply": apply,
                           "content": target.content, "base": target.base_branch})
        branch = "debian-upstream-tracking"
        return PublishResult(
            package=target.package, release=target.release,
            status=PublishStatus.BRANCH_PUSHED if apply else PublishStatus.DRY_RUN_OK,
            repository=target.slug, base_branch=target.base_branch,
            branch=branch, pushed=apply, commit="c" * 40,
            pr_create_url=f"https://github.com/{target.slug}/compare/"
                          f"{target.base_branch}...{branch}?quick_pull=1",
        )


class FakeMapping:
    def __init__(self, rows=None):
        self.rows = rows or {}

    def get(self, package, release):
        return self.rows.get(package)


class FakeUpstreamMd:
    def render(self, resolution):
        return "# pyrad\n"

    def generate(self, resolution, existing=None):
        return UpstreamMdDocument(
            package=resolution.package, debian_release=resolution.debian_release,
            content="# pyrad\n", outcome=UpstreamMdOutcome.CREATED,
        )


class FakeWorkspaces:
    """Stands in for a workspace host that is unreachable.

    Generation must still work: an unreadable existing file means "there is no
    file", not "fail the request".
    """

    root = "/tmp/ws"

    def scratch(self, label):
        raise RuntimeError("workspace host unavailable")

    def probe(self):
        return {"root": self.root, "writable": True}


class FakeContainer:
    def __init__(self):
        self.catalog = FakeCatalog()
        self.upstream = FakeUpstream()
        self.comparison = FakeComparison()
        self.patches = FakePatches()
        self.github = FakeGitHub()
        self.upstream_md = FakeUpstreamMd()
        self.workspaces = FakeWorkspaces()
        # The real batch service, on the unreachable workspace host above: an
        # existing file that cannot be read must not fail the request.
        self.metadata_batch = MetadataBatchService(
            self.upstream_md, workspaces=self.workspaces
        )
        self.publisher = FakePublisher()
        self.mapping = FakeMapping()
        from apm.services.publish_status import PublishStatusService
        self.refs = FakeRefs()
        self.publish_status = PublishStatusService(self.refs, self.github)

    def capabilities(self):
        return {"create_pull_requests": False, "pull_requests": "never",
                "push_enabled": True}


@pytest.fixture
def app_container():
    fake = FakeContainer()
    app = create_app()
    app.dependency_overrides[deps.container] = lambda: fake
    return TestClient(app), fake


def test_health_reports_capabilities(app_container):
    client, _ = app_container
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["capabilities"]["create_pull_requests"] is False
    assert body["capabilities"]["pull_requests"] == "never"


def test_releases_and_packages_are_discovered(app_container):
    client, _ = app_container
    assert [r["id"] for r in client.get("/api/releases").json()] == ["bookworm"]
    assert len(client.get("/api/packages").json()) == 2


def test_package_list_is_filtered_by_release(app_container):
    """zenoh ships in bookworm only; trixie must not offer it."""
    client, _ = app_container
    trixie = [p["name"] for p in
              client.get("/api/packages", params={"release": "trixie"}).json()]
    assert trixie == ["pyrad"]


def test_unknown_package_is_404(app_container):
    client, _ = app_container
    assert client.get("/api/packages/nope").status_code == 404


def test_branches_come_from_discovery(app_container):
    client, _ = app_container
    branches = client.get(
        "/api/branches", params={"package": "pyrad", "release": "bookworm"}
    ).json()
    assert {b["name"] for b in branches} == {"aminor", "main"}
    assert any(b["is_configured"] for b in branches)


def test_upstream_resolution_includes_evidence(app_container):
    client, _ = app_container
    body = client.get(
        "/api/upstream/pyrad", params={"release": "bookworm"}
    ).json()
    assert body["status"] == "VERIFIED"
    assert body["upstream_ref"] == "master"
    assert body["evidence"][0]["kind"] == "dep12"


def test_unknown_package_resolution_maps_to_404(app_container):
    client, _ = app_container
    response = client.get("/api/upstream/nope", params={"release": "bookworm"})
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_manual_upstream_is_verified_not_trusted(app_container):
    client, fake = app_container
    response = client.post("/api/upstream/verify", json={
        "package": "pyrad", "release": "bookworm",
        "repository": "https://github.com/x/y.git", "ref": "main",
    })
    assert response.status_code == 200
    assert response.json()["mode"] == "MANUAL"
    assert fake.upstream.manual_calls == [("https://github.com/x/y.git", "main")]


def test_manual_upstream_with_a_bad_ref_is_404(app_container):
    client, _ = app_container
    response = client.post("/api/upstream/verify", json={
        "package": "pyrad", "release": "bookworm",
        "repository": "https://github.com/x/y.git", "ref": "missing",
    })
    assert response.status_code == 404
    assert response.json()["code"] == "BRANCH_NOT_FOUND"


def test_comparison_separates_the_three_commit_groups(app_container):
    client, _ = app_container
    body = client.post("/api/comparison", json={
        "package": "pyrad", "release": "bookworm",
    }).json()
    assert body["summary"]["missing_upstream"] == 1
    assert body["summary"]["arcos_only"] == 2
    assert body["summary"]["already_backported"] == 1
    assert body["missing_upstream"][0]["criticality"]["level"] == "CRITICAL"


def test_no_common_ancestor_is_422_not_a_generic_failure(app_container):
    client, _ = app_container
    response = client.post("/api/comparison", json={
        "package": "stranger", "release": "bookworm",
    })
    assert response.status_code == 422
    assert response.json()["code"] == "NO_COMMON_ANCESTOR"


def test_preview_reports_conflicts(app_container):
    client, _ = app_container
    body = client.post("/api/patches/preview", json={
        "package": "pyrad", "release": "bookworm", "arcos_branch": "aminor",
        "upstream_repository": "https://github.com/x/y.git",
        "upstream_ref": "master", "shas": ["conflict"],
    }).json()
    assert body["outcome"] == "CONFLICT"
    assert body["conflicts"] == ["shared.txt"]


def test_cherry_pick_does_not_push_unless_asked(app_container):
    client, _ = app_container
    payload = {
        "package": "pyrad", "release": "bookworm", "arcos_branch": "aminor",
        "upstream_repository": "https://github.com/x/y.git",
        "upstream_ref": "master", "shas": ["a" * 40],
        "expected_base_sha": "b" * 40,
    }
    assert client.post("/api/patches/cherry-pick", json=payload).json()["pushed"] is False
    payload["push"] = True
    assert client.post("/api/patches/cherry-pick", json=payload).json()["pushed"] is True


def test_cherry_pick_without_a_previewed_base_is_refused(app_container):
    """The server, not the UI, insists the selection was previewed against a
    known tip - so a stale selection cannot be applied by skipping a step."""
    client, _ = app_container
    response = client.post("/api/patches/cherry-pick", json={
        "package": "pyrad", "release": "bookworm", "arcos_branch": "aminor",
        "upstream_repository": "https://github.com/x/y.git",
        "upstream_ref": "master", "shas": ["a" * 40], "push": True,
    })
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert "expected_base_sha" in response.json()["message"]


def test_cherry_pick_onto_the_target_branch_is_rejected(app_container):
    client, _ = app_container
    response = client.post("/api/patches/cherry-pick", json={
        "package": "pyrad", "release": "bookworm", "arcos_branch": "aminor",
        "upstream_repository": "https://github.com/x/y.git",
        "upstream_ref": "master", "shas": ["a" * 40], "branch_name": "aminor",
    })
    assert response.status_code == 400


@pytest.mark.parametrize("token", ["tok", ""])
def test_no_pull_request_can_be_opened_through_the_api(app_container, token):
    client, fake = app_container
    fake.github.token = token
    fake.api_token = SECRET
    for headers in ({}, {"Authorization": f"Bearer {SECRET}"}):
        response = client.post("/api/pull-requests", json={
            "package": "pyrad", "release": "bookworm", "arcos_branch": "aminor",
            "head_branch": "upstream/pyrad/x", "applied": ["a" * 40],
        }, headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "PULL_REQUESTS_DISABLED"
    assert fake.github.created == []


def test_upstream_md_generation_writes_nothing_remote(app_container):
    client, fake = app_container
    body = client.post("/api/upstream-md/generate", json={
        "package": "pyrad", "release": "bookworm",
    }).json()
    assert body["outcome"] == "CREATED"
    assert isinstance(body["review_flags"], list)
    assert fake.github.created == []


def test_upstream_md_pr_requires_confirmation(app_container):
    client, _ = app_container
    response = client.post("/api/upstream-md/pr", json={
        "package": "pyrad", "release": "bookworm", "confirm": False,
    })
    assert response.status_code == 400


@pytest.fixture
def md_out(tmp_path, monkeypatch, app_container):
    """A reviewed bookworm plan for pyrad, as generate-upstream-md writes it,
    with babeltrace and arcapi among the packages it settles."""
    from apm.api.routes import upstream_md as md_route

    monkeypatch.setattr(md_route, "OUT_DIR", tmp_path)
    _, fake = app_container
    _write_plan(tmp_path, {"pyrad": "# pyrad\n"}, skipped=[
        {"package": "babeltrace", "status": "NEEDS_REVIEW", "reason": "imported"},
        {"package": "arcapi", "status": "NO_UPSTREAM", "reason": "arrcus native"},
    ])
    fake.mapping = FakeMapping({"pyrad": RESOLUTION.model_copy(
        update={"verification_level": VerificationLevel.SHARED_HISTORY})})
    return tmp_path


def _write_plan(out, files, release="bookworm", skipped=(), base="aminor"):
    entries = []
    for package, content in files.items():
        path = out / f"upstream-md-{release}" / package / "debian" / "upstream.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        entries.append({"package": package, "status": "VERIFIED",
                        "arcos_repository": f"Arrcus/{package}",
                        "base_branch": base, "sha256": sha256_text(content)})
    import json

    (out / f"upstream-md-plan-{release}.json").write_text(json.dumps({
        "release": release, "entries": entries, "skipped": list(skipped)}))


@pytest.mark.parametrize("token", ["tok", ""])
def test_upstream_md_push_goes_through_the_reviewed_plan_and_is_recorded(
        app_container, md_out, token):
    client, fake = app_container
    fake.github.token = token
    body = client.post("/api/upstream-md/pr", json={
        "package": "pyrad", "release": "bookworm", "confirm": True,
    }).json()
    assert body["status"] == "BRANCH_PUSHED" and body["pull_request"] is None
    assert body["compare_url"] == \
        "https://github.com/Arrcus/pyrad/compare/aminor...debian-upstream-tracking"
    assert body["file_url"] == ("https://github.com/Arrcus/pyrad/blob/"
                                "debian-upstream-tracking/debian/upstream.md")
    assert fake.publisher.preflights == 1
    assert fake.publisher.calls == [{"package": "pyrad", "apply": True,
                                     "content": "# pyrad\n", "base": "aminor"}]
    assert fake.github.created == []

    listed = client.get("/api/upstream-md/prs", params={"release": "bookworm"})
    assert [e["package"] for e in listed.json()] == ["pyrad"]
    # What a person works from is rewritten after every push.
    assert body["compare_url"] in (md_out / "upstream-md-pr-links-bookworm.md").read_text()
    report = (md_out / "upstream-md-publishing-report.md").read_text()
    assert body["compare_url"] in report and body["file_url"] in report


def test_upstream_md_dry_run_needs_no_confirmation_and_records_nothing(
        app_container, md_out):
    client, fake = app_container
    body = client.post("/api/upstream-md/pr", json={
        "package": "pyrad", "release": "bookworm", "dry_run": True,
    }).json()
    assert body["status"] == "DRY_RUN_OK"
    assert fake.publisher.calls[0]["apply"] is False
    assert fake.publisher.preflights == 0
    assert client.get("/api/upstream-md/prs").json() == []
    assert not (md_out / "upstream-md-publishing-report.md").exists()


def test_upstream_md_push_only_still_needs_confirmation(app_container, md_out):
    client, fake = app_container
    response = client.post("/api/upstream-md/pr", json={
        "package": "pyrad", "release": "bookworm", "push_only": True,
    })
    assert response.status_code == 400
    assert fake.publisher.calls == [] and fake.publisher.preflights == 0


def test_asking_the_api_for_a_pull_request_is_refused(app_container, md_out):
    client, fake = app_container
    for dry_run in (False, True):
        response = client.post("/api/upstream-md/pr", json={
            "package": "pyrad", "release": "bookworm", "confirm": True,
            "dry_run": dry_run, "push_only": False,
        })
        assert response.status_code == 403
        assert response.json()["code"] == "PULL_REQUESTS_DISABLED"
    assert fake.publisher.calls == [] and fake.publisher.preflights == 0


def _real_publisher(app_container, md_out, tmp_path, github):
    """Route and real publisher on a local fork, from a reviewed plan."""
    from apm.services.upstream_md_publisher import UpstreamMdPublisher
    from apm.services.upstream_md_service import UpstreamMdService
    from test_upstream_md_publisher import (
        FakeWorkspaces as RecordingWorkspaces, Remote, _resolution,
    )

    _, fake = app_container
    remote = Remote(tmp_path / "remote")
    resolution = _resolution(remote=remote)
    fake.upstream_md = UpstreamMdService()
    fake.mapping = FakeMapping({"mstpd": resolution})
    _write_plan(md_out, {"mstpd": fake.upstream_md.render(resolution)})
    fake.publisher = UpstreamMdPublisher(
        RecordingWorkspaces(tmp_path / "ws"), github, "a", "a@b",
        branch_template="debian-upstream-tracking",
    )
    return remote


def test_upstream_md_push_through_the_real_publisher(app_container, md_out,
                                                     tmp_path):
    """No token, one push, no pull request - and only where pushing is allowed."""
    from test_upstream_md_publisher import FakeGitHub as RecordingGitHub

    client, fake = app_container
    github = RecordingGitHub(token="")
    remote = _real_publisher(app_container, md_out, tmp_path, github)
    push = {"package": "mstpd", "release": "bookworm", "confirm": True}

    fake.publisher.workspaces.allow_push = False
    refused = client.post("/api/upstream-md/pr", json=push)
    assert refused.status_code == 403 and refused.json()["code"] == "PUSH_DISABLED"
    assert remote.branches() == ["aminor"]
    fake.publisher.workspaces.allow_push = True

    body = client.post("/api/upstream-md/pr", json=push).json()

    assert body["status"] == "BRANCH_PUSHED", body
    assert remote.branches() == ["aminor", "debian-upstream-tracking"]
    assert fake.publisher.workspaces.pushes == [
        (body["commit"], "debian-upstream-tracking")]
    assert "/compare/aminor...debian-upstream-tracking?quick_pull=1&title=" \
        in body["pr_create_url"]
    assert body["compare_url"].endswith("/compare/aminor...debian-upstream-tracking")
    assert body["file_url"].endswith("/blob/debian-upstream-tracking/debian/upstream.md")
    assert github.created == [] and github.lookups == [] and github.repos == []
    [entry] = client.get("/api/upstream-md/prs").json()
    assert entry["pr_create_url"] == body["pr_create_url"]

    # Again: the same branch and link, never a second branch.
    again = client.post("/api/upstream-md/pr", json=push).json()
    assert (again["status"], again["commit"]) == ("BRANCH_PUSHED", body["commit"])
    assert len(fake.publisher.workspaces.pushes) == 1


def test_upstream_md_push_leaves_a_recorded_pull_request_alone(
        app_container, md_out, tmp_path):
    """A package whose PR is open: no push, no link, no downgrade of the
    record to BRANCH_PUSHED."""
    from apm.services.publish_ledger import PublishLedger
    from test_upstream_md_publisher import FakeGitHub as RecordingGitHub

    client, fake = app_container
    github = RecordingGitHub()
    _real_publisher(app_container, md_out, tmp_path, github)
    push = {"package": "mstpd", "release": "bookworm", "confirm": True}
    pushed = client.post("/api/upstream-md/pr", json=push).json()
    assert pushed["status"] == "BRANCH_PUSHED"
    # A person opened it from the link, and a later run found it.
    ledger = PublishLedger.for_release(md_out, "bookworm")
    ledger.record(ledger.get("mstpd").model_copy(update={
        "status": PublishStatus.PR_EXISTS, "pull_request": PullRequest(
            number=12, state="open", url="https://github.com/Arrcus/mstpd/pull/12"),
    }))

    for token in ("tok", ""):
        github.token = token
        body = client.post("/api/upstream-md/pr", json=push).json()
        assert body["status"] == "PR_EXISTS", body
        assert body["pull_request"]["url"].endswith("/pull/12")

    assert len(fake.publisher.workspaces.pushes) == 1 and github.created == []
    [entry] = client.get("/api/upstream-md/prs").json()
    assert entry["status"] == "PR_EXISTS"


@pytest.mark.parametrize("package,status", [
    ("babeltrace", "SKIPPED_NEEDS_REVIEW"), ("arcapi", "SKIPPED_NO_UPSTREAM"),
])
def test_upstream_md_push_refuses_what_the_plan_settles(app_container, md_out,
                                                        package, status):
    client, fake = app_container
    for dry_run in (False, True):
        body = client.post("/api/upstream-md/pr", json={
            "package": package, "release": "bookworm", "confirm": True,
            "dry_run": dry_run,
        }).json()
        assert body["status"] == status and body["pushed"] is False
    assert fake.publisher.calls == []
    assert client.get("/api/upstream-md/prs").json() == []


def test_a_package_outside_the_reviewed_plan_cannot_be_pushed(app_container,
                                                              md_out):
    client, fake = app_container
    for dry_run in (False, True):
        response = client.post("/api/upstream-md/pr", json={
            "package": "zenoh", "release": "bookworm", "confirm": True,
            "dry_run": dry_run,
        })
        assert response.status_code == 400
        assert "not in the reviewed bookworm plan" in response.json()["message"]
    missing = client.post("/api/upstream-md/pr", json={
        "package": "pyrad", "release": "trixie", "confirm": True})
    assert missing.status_code == 404
    assert fake.publisher.calls == []


def test_a_reviewed_file_changed_since_review_is_drift_not_a_push(app_container,
                                                                  md_out):
    client, fake = app_container
    (md_out / "upstream-md-bookworm" / "pyrad" / "debian" / "upstream.md") \
        .write_text("# pyrad, edited after review\n")
    for dry_run in (False, True):
        body = client.post("/api/upstream-md/pr", json={
            "package": "pyrad", "release": "bookworm", "confirm": True,
            "dry_run": dry_run,
        }).json()
        assert body["status"] == "DRIFT", body
    assert fake.publisher.calls == []


@pytest.mark.parametrize("field", [
    {"branch_name": "aminor"}, {"branch_name": "debian-upstream-tracking-x"},
    {"branch_name": "upstream-metadata/bookworm/pyrad"},
    {"arcos_branch": "aminor-trixie"}, {"arcos_branch": "aminor"},
    {"draft": True},
])
def test_a_request_cannot_name_the_branch_or_the_target(app_container, md_out,
                                                        field):
    client, fake = app_container
    for dry_run in (False, True):
        response = client.post("/api/upstream-md/pr", json={
            "package": "pyrad", "release": "bookworm", "confirm": True,
            "dry_run": dry_run, **field,
        })
        assert response.status_code == 422, response.text
    assert fake.publisher.calls == [] and fake.publisher.preflights == 0


def test_upstream_md_generation_for_no_upstream_is_a_400_not_a_500(app_container):
    client, _ = app_container
    response = client.post("/api/upstream-md/generate", json={
        "package": "arcapi", "release": "bookworm",
    })
    assert response.status_code == 400


def test_reports_are_listed_from_disk(app_container, tmp_path, monkeypatch):
    from apm.api.routes import reports as reports_route

    (tmp_path / "upstream-mapping-bookworm.xlsx").write_bytes(b"xlsx")
    (tmp_path / "upstream-mapping.csv").write_text("Package\n")
    (tmp_path / "notes.txt").write_text("not a report")
    monkeypatch.setattr(reports_route, "OUT_DIR", tmp_path)

    client, _ = app_container
    body = client.get("/api/reports").json()

    names = {r["name"] for r in body}
    assert names == {"upstream-mapping-bookworm.xlsx", "upstream-mapping.csv"}
    release = {r["name"]: r["release"] for r in body}
    assert release["upstream-mapping-bookworm.xlsx"] == "bookworm"
    assert release["upstream-mapping.csv"] == ""


def test_a_report_can_be_downloaded(app_container, tmp_path, monkeypatch):
    from apm.api.routes import reports as reports_route

    (tmp_path / "upstream-mapping-bookworm.csv").write_text("Package\niputils\n")
    monkeypatch.setattr(reports_route, "OUT_DIR", tmp_path)

    client, _ = app_container
    response = client.get("/api/reports/upstream-mapping-bookworm.csv")
    assert response.status_code == 200
    assert "iputils" in response.text


def test_a_report_name_cannot_escape_the_output_directory(app_container, tmp_path,
                                                          monkeypatch):
    """The name is matched against the listing, never joined onto a path."""
    from apm.api.routes import reports as reports_route

    monkeypatch.setattr(reports_route, "OUT_DIR", tmp_path)
    client, _ = app_container
    for name in ("../../etc/passwd", "..%2f..%2fetc%2fpasswd", "settings.yaml"):
        assert client.get(f"/api/reports/{name}").status_code == 404


# -- publish status and the patch pull request ----------------------------------

def test_publish_status_finds_the_pull_request_from_git_refs(app_container):
    client, fake = app_container
    fake.github.token = ""
    body = client.get("/api/publish-status", params={
        "package": "pyrad", "branch": "upstream/pyrad/x", "base": "aminor"}).json()
    assert body["repository"] == "Arrcus/pyrad"
    assert body["branch_exists"] is True and body["head_sha"] == FakeRefs.PUSHED
    assert body["pull_requests"] == [
        {"number": 9, "url": "https://github.com/Arrcus/pyrad/pull/9"}]
    assert body["ci"]["state"] == "UNAVAILABLE" and body["api_access"] is False


def test_publish_status_refuses_a_branch_the_tool_does_not_create(app_container):
    client, fake = app_container
    response = client.get("/api/publish-status", params={
        "package": "pyrad", "branch": "aminor"})
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert fake.refs.calls == []


def test_publish_status_for_an_unknown_package_is_404(app_container):
    client, _ = app_container
    response = client.get("/api/publish-status", params={
        "package": "nope", "branch": "upstream/nope/x"})
    assert response.status_code == 404


def _cherry_pick(client, push):
    return client.post("/api/patches/cherry-pick", json={
        "package": "pyrad", "release": "bookworm", "arcos_branch": "aminor",
        "upstream_repository": "https://github.com/wichert/pyrad.git",
        "upstream_ref": "master", "shas": ["d" * 40],
        "expected_base_sha": "3" * 40, "push": push,
    }).json()


def test_a_pushed_patch_branch_carries_its_pr_description_and_link(app_container):
    client, _ = app_container
    body = _cherry_pick(client, push=True)
    assert body["pr_title"] == "pyrad: pull 1 upstream commit(s) into aminor"
    assert body["pr_body"].startswith("Problem Description :")
    assert "Root Cause :" in body["pr_body"] and "Fix Details :" in body["pr_body"]
    assert body["pr_create_url"].startswith(
        "https://github.com/Arrcus/pyrad/compare/aminor...upstream/pyrad/x?quick_pull=1")


def test_an_unpushed_patch_branch_has_no_link(app_container):
    client, _ = app_container
    body = _cherry_pick(client, push=False)
    assert body["pr_body"] and body["pr_create_url"] is None


# -- the deployment's own API token (APM_API_TOKEN) -----------------------------

PICK = {
    "package": "pyrad", "release": "bookworm", "arcos_branch": "aminor",
    "upstream_repository": "https://github.com/x/y.git",
    "upstream_ref": "master", "shas": ["a" * 40], "expected_base_sha": "b" * 40,
}
SECRET = "Zq9-api-secret"


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer wrong"}, {"Authorization": SECRET},
    {"Authorization": f"Basic {SECRET}"},
])
def test_writes_need_the_api_token_when_one_is_set(app_container, headers):
    client, fake = app_container
    fake.api_token = SECRET

    for path, body in [
        ("/api/patches/cherry-pick", {**PICK, "push": True}),
        ("/api/upstream-md/pr", {"package": "pyrad", "release": "bookworm",
                                 "confirm": True}),
        ("/api/upstream/content-base/approve",
         {"package": "pyrad", "release": "bookworm", "tag": "v1",
          "verified_by": "Somil", "confirm": True}),
        ("/api/upstream-md/generate", {"package": "pyrad", "release": "bookworm",
                                       "write_local": True}),
    ]:
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 401, path
        assert response.json()["code"] == "API_TOKEN_REQUIRED", path
        assert SECRET not in response.text
    assert fake.github.created == [] and fake.publisher.calls == []


def test_the_right_api_token_lets_a_write_through(app_container, md_out):
    client, fake = app_container
    fake.api_token = SECRET
    headers = {"Authorization": f"Bearer {SECRET}"}

    picked = client.post("/api/patches/cherry-pick", json={**PICK, "push": True},
                         headers=headers)
    pushed = client.post("/api/upstream-md/pr", headers=headers, json={
        "package": "pyrad", "release": "bookworm", "confirm": True})

    assert picked.status_code == 200 and picked.json()["pushed"] is True
    assert pushed.status_code == 200 and pushed.json()["status"] == "BRANCH_PUSHED"


def test_reads_and_dry_runs_never_need_the_api_token(app_container, md_out):
    client, fake = app_container
    fake.api_token = SECRET

    assert client.get("/api/upstream/pyrad", params={"release": "bookworm"}).status_code == 200
    assert client.post("/api/patches/cherry-pick", json=PICK).json()["pushed"] is False
    dry = client.post("/api/upstream-md/pr", json={
        "package": "pyrad", "release": "bookworm", "dry_run": True})
    assert dry.status_code == 200, dry.text


def test_without_an_api_token_writes_behave_as_before(app_container):
    client, fake = app_container
    assert client.post("/api/patches/cherry-pick",
                       json={**PICK, "push": True}).json()["pushed"] is True


def test_cors_is_refused_for_every_origin(monkeypatch):
    monkeypatch.setenv("APM_CORS_ORIGINS", "http://a.example, *")
    with pytest.raises(RuntimeError, match="'\\*' is refused"):
        create_app()


def test_cors_allows_only_what_the_dashboard_sends(monkeypatch):
    monkeypatch.setenv("APM_CORS_ORIGINS", "http://dash.example")
    client = TestClient(create_app())

    allowed = client.options("/api/health", headers={
        "Origin": "http://dash.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    other = client.options("/api/health", headers={
        "Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://dash.example"
    assert "access-control-allow-credentials" not in allowed.headers
    assert other.status_code == 400


def test_a_git_failure_is_reported_as_one_not_as_a_bare_500(app_container, monkeypatch):
    from apm.gitio.workspace import GitWorkspaceError

    client, fake = app_container

    def broken(*args, **kwargs):
        raise GitWorkspaceError("git fetch failed", "fatal: could not read from remote")

    monkeypatch.setattr(fake.patches, "cherry_pick", broken)
    response = client.post("/api/patches/cherry-pick", json=PICK)

    assert response.status_code == 502
    assert response.json() == {
        "code": "GIT_ERROR", "message": "git failed: git fetch failed",
        "detail": "fatal: could not read from remote"}
