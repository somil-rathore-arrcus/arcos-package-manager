"""Resolution must serve the mapping, not recompute it.

The regression this guards is the reason the dashboard hung: every package
selection triggered a full live resolution, including an ancestry probe that
clones both repositories.
"""

from __future__ import annotations

import pytest

from apm.domain.enums import ErrorCode, ResolutionStatus
from apm.domain.models import Package, Repository, UpstreamResolution
from apm.services.upstream_service import UpstreamService, UpstreamServiceError


class ExplodingResolver:
    """Stands in for live resolution, which must not be reached."""

    def __init__(self):
        self.calls = 0

    def resolve_one(self, package, release):
        self.calls += 1
        raise AssertionError(
            "live resolution ran when a mapping row was available"
        )


class FakeMapping:
    def __init__(self, rows):
        self.rows = rows
        self.gets = 0

    def get(self, package, release):
        self.gets += 1
        found = self.rows.get((package, release))
        return found.model_copy(deep=True) if found else None


class FakeCatalog:
    def __init__(self, packages):
        self._packages = packages

    def package(self, name):
        return next((p for p in self._packages if p.name == name), None)


class FakeVerifier:
    def __init__(self, sha="f" * 40, exists=True, reachable=True):
        self.sha, self.exists, self.reachable = sha, exists, reachable
        self.calls = 0

    def branch_tip(self, repository, branch):
        self.calls += 1
        from apm.gitio.verify import RefCheck
        return RefCheck(self.reachable, self.exists, self.sha, branch)


class FakeSettings:
    packaging_hosts = {"salsa.debian.org"}


RESOLUTION = UpstreamResolution(
    package="pyrad", debian_release="bookworm",
    status=ResolutionStatus.VERIFIED,
    arcos_repository="ssh://git@github.com/Arrcus/pyrad.git",
    arcos_branch="aminor", arcos_commit="3b043f16bb8e" + "0" * 28,
    upstream_repository=Repository(url="https://github.com/wichert/pyrad.git"),
    upstream_ref="master", behind=109, arcos_only=19,
)

PACKAGE = Package(
    name="pyrad", arcos_repository="ssh://git@github.com/Arrcus/pyrad.git",
    github_repository="Arrcus/pyrad", releases=["bookworm", "trixie"],
)


def _service(mapping_rows=None, resolver=None, verifier=None):
    return UpstreamService(
        resolver or ExplodingResolver(),
        FakeCatalog([PACKAGE]),
        verifier or FakeVerifier(),
        FakeSettings(),
        mapping=FakeMapping(mapping_rows or {}),
    )


def test_a_mapped_package_never_triggers_live_resolution():
    resolver = ExplodingResolver()
    service = _service({("pyrad", "bookworm"): RESOLUTION}, resolver)

    resolution = service.resolve("pyrad", "bookworm")

    assert resolution.status is ResolutionStatus.VERIFIED
    assert resolution.upstream_ref == "master"
    assert resolver.calls == 0


def test_repeated_lookups_do_not_reread_the_mapping():
    mapping = FakeMapping({("pyrad", "bookworm"): RESOLUTION})
    service = UpstreamService(
        ExplodingResolver(), FakeCatalog([PACKAGE]), FakeVerifier(),
        FakeSettings(), mapping=mapping,
    )
    service.resolve("pyrad", "bookworm")
    service.resolve("pyrad", "bookworm")
    assert mapping.gets == 1


def test_refresh_bypasses_the_mapping_and_resolves_live():
    """The escape hatch must still exist for a stale or absent row."""
    class Recording(ExplodingResolver):
        def resolve_one(self, package, release):
            self.calls += 1
            raise UpstreamServiceError(ErrorCode.INTERNAL, "live path reached")

    service = _service({("pyrad", "bookworm"): RESOLUTION}, Recording())
    with pytest.raises(UpstreamServiceError):
        service.resolve("pyrad", "bookworm", refresh=True)


def test_a_package_not_in_the_release_manifest_is_refused_early():
    service = _service({("pyrad", "bookworm"): RESOLUTION})
    with pytest.raises(UpstreamServiceError) as excinfo:
        service.resolve("pyrad", "forky")
    assert excinfo.value.code is ErrorCode.NOT_FOUND


def test_unknown_package_is_refused():
    service = _service()
    with pytest.raises(UpstreamServiceError) as excinfo:
        service.resolve("nope", "bookworm")
    assert excinfo.value.code is ErrorCode.NOT_FOUND


def test_choosing_another_branch_retargets_without_live_resolution():
    verifier = FakeVerifier(sha="a" * 40)
    resolver = ExplodingResolver()
    service = _service({("pyrad", "bookworm"): RESOLUTION}, resolver, verifier)

    resolution = service.resolve("pyrad", "bookworm", arcos_branch="other")

    assert resolution.arcos_branch == "other"
    assert resolution.arcos_commit == "a" * 40
    assert resolver.calls == 0
    assert any("instead of the commit" in note for note in resolution.notes)


def test_a_branch_that_does_not_exist_is_reported_not_guessed():
    service = _service(
        {("pyrad", "bookworm"): RESOLUTION},
        verifier=FakeVerifier(exists=False),
    )
    with pytest.raises(UpstreamServiceError) as excinfo:
        service.resolve("pyrad", "bookworm", arcos_branch="ghost")
    assert excinfo.value.code is ErrorCode.BRANCH_NOT_FOUND
