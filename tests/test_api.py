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
)
from apm.domain.models import (
    Branch, CherryPickPreview, CherryPickResult, CommitInfo, ComparisonResult,
    ComparisonSummary, CriticalityAssessment, DebianRelease, Package,
    PublishResult, PullRequest, Repository, ResolutionEvidence,
    UpstreamMdDocument, UpstreamResolution,
)
from apm.services.comparison_service import ComparisonError
from apm.services.metadata_batch_service import MetadataBatchService
from apm.services.github_service import GitHubError
from apm.services.patch_service import PatchError
from apm.services.upstream_md_publisher import validate_branch
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
    def __init__(self, token="tok"):
        self.token = token
        self.created = []

    @property
    def can_create_pull_requests(self):
        return bool(self.token)

    def create_pull_request(self, slug, head, base, title, body, draft=False):
        if not self.token:
            raise GitHubError(ErrorCode.AUTH_REQUIRED, "token required")
        self.created.append((slug, head, base))
        return PullRequest(number=1, url="https://github.com/o/r/pull/1",
                           title=title, base=base, head=head)

    def get_pull_request(self, slug, number):
        return PullRequest(number=number, url=f"https://github.com/o/r/pull/{number}")


class FakePublisher:
    """Records what the route asked for; the real publisher has its own tests."""

    def __init__(self):
        self.calls = []
        self.preflights = []

    def preflight(self, slugs):
        self.preflights.append(list(slugs))

    def publish(self, target, *, apply, draft=False, title_prefix="",
                branch=None, prior=None):
        validate_branch(branch or "upstream-metadata/bookworm/x", target.base_branch)
        self.calls.append({"package": target.package, "apply": apply,
                           "branch": branch, "content": target.content})
        return PublishResult(
            package=target.package, release=target.release,
            status=PublishStatus.PR_OPENED if apply else PublishStatus.DRY_RUN_OK,
            repository=target.slug, base_branch=target.base_branch,
            branch=branch or f"upstream-metadata/bookworm/{target.package}",
            pull_request=PullRequest(number=1, url="https://github.com/o/r/pull/1")
            if apply else None,
        )


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

    def capabilities(self):
        return {"create_pull_requests": self.github.can_create_pull_requests}


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
    assert "create_pull_requests" in body["capabilities"]


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
    }
    assert client.post("/api/patches/cherry-pick", json=payload).json()["pushed"] is False
    payload["push"] = True
    assert client.post("/api/patches/cherry-pick", json=payload).json()["pushed"] is True


def test_cherry_pick_onto_the_target_branch_is_rejected(app_container):
    client, _ = app_container
    response = client.post("/api/patches/cherry-pick", json={
        "package": "pyrad", "release": "bookworm", "arcos_branch": "aminor",
        "upstream_repository": "https://github.com/x/y.git",
        "upstream_ref": "master", "shas": ["a" * 40], "branch_name": "aminor",
    })
    assert response.status_code == 400


def test_pull_request_creation_requires_a_token(app_container):
    client, fake = app_container
    fake.github.token = ""
    response = client.post("/api/pull-requests", json={
        "package": "pyrad", "release": "bookworm", "arcos_branch": "aminor",
        "head_branch": "upstream/pyrad/x",
    })
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"


def test_pull_request_is_created_only_on_an_explicit_call(app_container):
    client, fake = app_container
    # Resolving and comparing must not have created anything.
    client.get("/api/upstream/pyrad", params={"release": "bookworm"})
    client.post("/api/comparison", json={"package": "pyrad", "release": "bookworm"})
    assert fake.github.created == []

    client.post("/api/pull-requests", json={
        "package": "pyrad", "release": "bookworm", "arcos_branch": "aminor",
        "head_branch": "upstream/pyrad/x", "applied": ["a" * 40],
    })
    assert len(fake.github.created) == 1


def test_upstream_md_generation_writes_nothing_remote(app_container):
    client, fake = app_container
    body = client.post("/api/upstream-md/generate", json={
        "package": "pyrad", "release": "bookworm",
    }).json()
    assert body["outcome"] == "CREATED"
    assert fake.github.created == []


def test_upstream_md_pr_requires_confirmation(app_container):
    client, _ = app_container
    response = client.post("/api/upstream-md/pr", json={
        "package": "pyrad", "release": "bookworm", "confirm": False,
    })
    assert response.status_code == 400


@pytest.fixture
def md_out(tmp_path, monkeypatch):
    from apm.api.routes import upstream_md as md_route

    monkeypatch.setattr(md_route, "OUT_DIR", tmp_path)
    return tmp_path


def test_upstream_md_pr_goes_through_the_publisher_and_is_recorded(
        app_container, md_out):
    client, fake = app_container
    body = client.post("/api/upstream-md/pr", json={
        "package": "pyrad", "release": "bookworm", "confirm": True,
    }).json()
    assert body["status"] == "PR_OPENED"
    assert body["pull_request"]["url"] == "https://github.com/o/r/pull/1"
    assert fake.publisher.preflights == [["Arrcus/pyrad"]]
    assert fake.publisher.calls == [{"package": "pyrad", "apply": True,
                                     "branch": None, "content": "# pyrad\n"}]

    listed = client.get("/api/upstream-md/prs", params={"release": "bookworm"})
    assert [e["package"] for e in listed.json()] == ["pyrad"]


def test_upstream_md_dry_run_needs_no_confirmation_and_records_nothing(
        app_container, md_out):
    client, fake = app_container
    body = client.post("/api/upstream-md/pr", json={
        "package": "pyrad", "release": "bookworm", "dry_run": True,
    }).json()
    assert body["status"] == "DRY_RUN_OK"
    assert fake.publisher.calls[0]["apply"] is False
    assert fake.publisher.preflights == []
    assert client.get("/api/upstream-md/prs").json() == []


@pytest.mark.parametrize("package", ["babeltrace", "arcapi"])
def test_upstream_md_pr_refuses_unverified_mappings(app_container, md_out, package):
    client, fake = app_container
    response = client.post("/api/upstream-md/pr", json={
        "package": package, "release": "bookworm", "confirm": True,
    })
    assert response.status_code == 400
    assert fake.publisher.calls == []


def test_upstream_md_pr_refuses_the_target_branch(app_container, md_out):
    client, _ = app_container
    response = client.post("/api/upstream-md/pr", json={
        "package": "pyrad", "release": "bookworm", "confirm": True,
        "branch_name": "aminor",
    })
    assert response.status_code == 400


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
