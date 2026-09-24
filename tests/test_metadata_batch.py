"""Generating debian/upstream.md across a release, and planning the PRs.

The safety properties are the point of these tests: a package with no upstream
gets no file, a file this tool did not write is never silently replaced, and the
plan is a description of work that has not been done - no branch, no push, no
pull request.
"""

from __future__ import annotations

import json

import pytest

from apm.domain.enums import (
    PackageCategory, ResolutionMethod, ResolutionMode, ResolutionStatus,
    UpstreamMdOutcome,
)
from apm.domain.models import (
    PackageSource, Repository, ResolutionEvidence, UpstreamResolution,
)
from apm.services.metadata_batch_service import MetadataBatchService
from apm.services.upstream_md_service import UpstreamMdService


def _resolution(package="iputils", status=ResolutionStatus.VERIFIED,
                **overrides) -> UpstreamResolution:
    values = dict(
        package=package, debian_release="bookworm", status=status,
        mode=ResolutionMode.AUTO, method=ResolutionMethod.DEP12,
        category=PackageCategory.DEBIAN_UPSTREAM, confidence="high",
        arcos_repository=f"ssh://git@github.com/Arrcus/{package}.git",
        github_repository=f"Arrcus/{package}", arcos_branch="aminor",
        arcos_release="aminor", arcos_path=f"packages/{package}",
        arcos_commit="9" * 40,
        debian=PackageSource(
            source_package=package, debian_release="bookworm",
            version="3:20221126-1+deb12u1", suite="bookworm",
            vcs_git="https://salsa.debian.org/debian/iputils.git",
            vcs_branch="debian/master",
        ),
        upstream_repository=Repository(url="https://github.com/iputils/iputils.git"),
        upstream_ref="master", upstream_branch="master",
        upstream_commit="1" * 40, origin_kind="project",
        merge_base="a" * 40, behind=142, arcos_only=272,
        evidence_source="debian/upstream/metadata (DEP-12) Repository:",
        evidence_url="https://github.com/iputils/iputils.git",
        verification="git merge-base against the ARCoS fork",
        evidence=[ResolutionEvidence(kind="dep12", detail="DEP-12 Repository:")],
    )
    values.update(overrides)
    return UpstreamResolution(**values)


def _service(existing=None, unreadable=False) -> MetadataBatchService:
    service = MetadataBatchService(UpstreamMdService(), workspaces=None)
    if not unreadable:
        service.existing_content = lambda resolution: (existing, False)
    return service


def test_a_file_is_written_under_the_package_repository_layout(tmp_path):
    result = _service().run([_resolution()], "bookworm", tmp_path)

    written = tmp_path / "iputils" / "debian" / "upstream.md"
    assert written.exists()
    content = written.read_text()
    assert "# Upstream Information" in content
    assert "https://github.com/iputils/iputils.git" in content
    assert result.entries[0].outcome == UpstreamMdOutcome.CREATED.value


def test_the_file_matches_the_mapping_it_came_from(tmp_path):
    resolution = _resolution()
    _service().run([resolution], "bookworm", tmp_path)
    content = (tmp_path / "iputils" / "debian" / "upstream.md").read_text()

    assert f"- Repository: {resolution.upstream_repository.url}" in content
    assert "- Branch: master" in content
    assert "- VCS branch: debian/master" in content
    assert "- Release: aminor" in content
    assert "- Status: VERIFIED" in content


def test_a_package_with_no_upstream_gets_no_file(tmp_path):
    result = _service().run(
        [_resolution("arcapi", status=ResolutionStatus.NO_UPSTREAM,
                     upstream_repository=None, upstream_ref=None,
                     category=PackageCategory.ARRCUS_NATIVE,
                     reason="arrcus native")],
        "bookworm", tmp_path,
    )
    assert result.entries == []
    assert result.skipped[0]["package"] == "arcapi"
    assert not (tmp_path / "arcapi").exists()


def test_needs_review_is_left_out_unless_asked_for(tmp_path):
    needs_review = _resolution("babeltrace", status=ResolutionStatus.NEEDS_REVIEW,
                               reason="no candidate shared history")
    assert _service().run([needs_review], "bookworm", tmp_path).entries == []

    result = _service().run([needs_review], "bookworm", tmp_path,
                            include_needs_review=True)
    content = (tmp_path / "babeltrace" / "debian" / "upstream.md").read_text()
    assert result.entries[0].status == "NEEDS_REVIEW"
    assert "- Status: NEEDS_REVIEW" in content
    assert "- Reason: no candidate shared history" in content


def test_an_unchanged_file_is_reported_as_no_change(tmp_path):
    rendered = UpstreamMdService().render(_resolution())
    result = _service(existing=rendered).run([_resolution()], "bookworm", tmp_path)
    assert result.entries[0].outcome == UpstreamMdOutcome.NO_CHANGE.value


def test_a_file_this_tool_did_not_write_is_a_conflict_not_an_update(tmp_path):
    result = _service(existing="# upstream\n\nHand written by a maintainer.\n").run(
        [_resolution()], "bookworm", tmp_path,
    )
    entry = result.entries[0]
    assert entry.outcome == UpstreamMdOutcome.CONFLICT.value
    assert entry.diff, "a conflict must show what differs"


def test_an_unreadable_fork_is_flagged_rather_than_called_created(tmp_path):
    result = _service(unreadable=True).run([_resolution()], "bookworm", tmp_path,
                                           read_remote=False)
    assert result.entries[0].existing_unknown is True


def test_the_plan_describes_work_that_has_not_been_done(tmp_path):
    service = _service()
    result = service.run([_resolution()], "bookworm", tmp_path / "md")
    written = service.write_plan(result, tmp_path)

    payload = json.loads(written["json"].read_text())
    assert payload["pushed"] is False
    assert payload["pull_requests_created"] is False
    entry = payload["entries"][0]
    assert entry["branch"] == "upstream-metadata/bookworm/iputils"
    assert entry["base_branch"] == "aminor"
    assert entry["commit_message"] == (
        "iputils: add debian/upstream.md with verified upstream"
    )
    assert entry["path"] == "debian/upstream.md"

    readable = written["markdown"].read_text()
    assert "no pull request was opened" in readable.lower()


def test_the_plan_hashes_the_exact_bytes_it_wrote(tmp_path):
    import hashlib

    service = _service()
    result = service.run([_resolution()], "bookworm", tmp_path / "md")
    entry = json.loads(
        service.write_plan(result, tmp_path)["json"].read_text()
    )["entries"][0]

    data = (tmp_path / "md" / "iputils" / "debian" / "upstream.md").read_bytes()
    assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["bytes"] == len(data)


def test_only_the_named_release_is_generated(tmp_path):
    result = _service().run(
        [_resolution(), _resolution("ethtool", debian_release="trixie")],
        "bookworm", tmp_path,
    )
    assert [e.package for e in result.entries] == ["iputils"]


def test_rendering_is_deterministic():
    service = UpstreamMdService()
    assert service.render(_resolution()) == service.render(_resolution())
