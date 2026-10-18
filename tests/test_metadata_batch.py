"""Generating debian/upstream.md across a release, and planning the PRs.

The safety properties are the point of these tests: a package with no upstream
gets no file, a file this tool did not write is never silently replaced, an
unverified branch is said to be unverified - in the file, the plan and the
generator's output - and the plan is a description of work that has not been
done - no branch, no push, no pull request.
"""

from __future__ import annotations

import json

import pytest

from apm.domain.enums import (
    PackageCategory, RefStrategy, ResolutionMethod, ResolutionMode,
    ResolutionStatus, UpstreamMdOutcome,
)
from apm.domain.models import (
    PackageSource, RefSelection, Repository, ResolutionEvidence,
    UpstreamResolution,
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
    assert result.entries[0].outcome == UpstreamMdOutcome.CREATED.value


def test_the_file_is_the_two_fields_from_the_mapping(tmp_path):
    resolution = _resolution()
    _service().run([resolution], "bookworm", tmp_path)
    content = (tmp_path / "iputils" / "debian" / "upstream.md").read_text()

    assert content == (
        f"Upstream Source: {resolution.upstream_repository.url}\n"
        "\n"
        "Current Upstream Branch: master\n"
    )


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
    entry = result.entries[0]
    assert entry.status == "NEEDS_REVIEW"
    assert content == (
        "Upstream Source: N/A - not verified (status NEEDS_REVIEW)\n\n"
        "Current Upstream Branch: N/A - not verified (status NEEDS_REVIEW)\n"
    )
    # The candidate is a candidate: it never reaches the file.
    assert "github.com" not in content
    assert entry.reason == "no candidate shared history"
    assert entry.branch_verified is False
    assert entry.flags[0]["code"] == "BRANCH_NOT_VERIFIED"


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


# -- the two values and their flags, in the plan ---------------------------------

def _tag_only(package="ethtool", tag="v6.1", contains=True) -> UpstreamResolution:
    return _resolution(
        package,
        upstream_repository=Repository(
            url=f"https://git.kernel.org/pub/scm/network/{package}/{package}.git"),
        upstream_ref=tag, upstream_branch=None, upstream_tag=tag,
        ref_selection=RefSelection(
            ref=tag, kind="tag", strategy=RefStrategy.EXACT_TAG,
            reason=f"{tag} is the tag for the shipped version",
            debian_upstream_version="6.1", base_tag=tag,
            arcos_contains_base=contains,
        ),
    )


def _plan(tmp_path, resolutions):
    service = _service()
    result = service.run(resolutions, "bookworm", tmp_path / "md")
    written = service.write_plan(result, tmp_path)
    payload = json.loads(written["json"].read_text())
    return (result, {e["package"]: e for e in payload["entries"]},
            written["markdown"].read_text())


def test_the_plan_records_what_the_file_says_for_a_branch(tmp_path):
    _, entries, _ = _plan(tmp_path, [_resolution()])
    entry = entries["iputils"]
    assert entry["upstream_source"] == "https://github.com/iputils/iputils.git"
    assert entry["current_upstream_branch"] == "master"
    assert entry["upstream_branch"] == "master"
    assert entry["upstream_tag"] == ""
    assert entry["branch_verified"] is True
    assert entry["flags"] == []

    content = (tmp_path / "md" / "iputils" / "debian" / "upstream.md").read_text()
    assert f"Upstream Source: {entry['upstream_source']}" in content
    assert f"Current Upstream Branch: {entry['current_upstream_branch']}" in content


def test_the_plan_records_a_tag_as_a_tag_and_flags_it(tmp_path):
    _, entries, _ = _plan(tmp_path, [_tag_only()])
    entry = entries["ethtool"]
    assert entry["current_upstream_branch"] == (
        "N/A - no upstream branch verified; upstream tag v6.1"
    )
    assert entry["upstream_branch"] == ""
    assert entry["upstream_tag"] == "v6.1"
    assert entry["branch_verified"] is False
    assert [f["code"] for f in entry["flags"]] == ["BRANCH_NOT_VERIFIED", "TAG_ONLY"]
    assert all(set(f) == {"code", "detail"} and f["detail"] for f in entry["flags"])


def test_the_plan_keeps_every_key_it_had(tmp_path):
    """Readers of the plan - the publisher, the verifier - still find theirs."""
    _, entries, _ = _plan(tmp_path, [_resolution()])
    assert {
        "package", "release", "status", "outcome", "arcos_repository",
        "base_branch", "branch", "commit_message", "path", "local_path",
        "upstream_repository", "upstream_ref", "reason", "existing_unknown",
        "sha256", "bytes", "diff",
        "upstream_source", "current_upstream_branch", "upstream_branch",
        "upstream_tag", "branch_verified", "flags",
    } <= set(entries["iputils"])


def test_the_readable_plan_shows_the_values_and_the_flags(tmp_path):
    _, _, readable = _plan(tmp_path, [
        _resolution(),
        _tag_only("libpam-radius-auth", "release_2_0_0", contains=False),
    ])
    assert ("| Package | ARCoS repository | Base branch | Branch | Outcome | "
            "Upstream Source | Current Upstream Branch | Flags |") in readable
    assert ("| iputils | Arrcus/iputils | aminor | upstream-metadata/bookworm/"
            "iputils | CREATED | https://github.com/iputils/iputils.git | master |"
            "  |") in readable
    assert ("| N/A - no upstream branch verified; upstream tag release_2_0_0 "
            "(the ARCoS fork does not contain this tag) | BRANCH_NOT_VERIFIED, "
            "TAG_ONLY, BASE_TAG_NOT_IN_FORK |") in readable

    flagged = readable.split("## Flagged for review", 1)[1]
    assert "- libpam-radius-auth\n" in flagged
    assert "- iputils" not in flagged
    assert "  - BRANCH_NOT_VERIFIED: " in flagged
    assert "  - TAG_ONLY: upstream tag release_2_0_0 is the only ref" in flagged
    assert "  - BASE_TAG_NOT_IN_FORK: " in flagged
    assert "does not stop a package from being published" in flagged


def test_a_plan_with_nothing_flagged_says_so(tmp_path):
    _, _, readable = _plan(tmp_path, [_resolution()])
    assert "## Flagged for review" in readable
    assert "No package is flagged." in readable


def test_a_fallback_branch_is_written_and_flagged(tmp_path):
    ifupdown2 = _resolution(
        "ifupdown2",
        ref_selection=RefSelection(
            ref="master", kind="branch", strategy=RefStrategy.DEFAULT_BRANCH,
            reason="fell back to the default branch", is_fallback=True),
        warnings=["no upstream tag for the shipped version"],
    )
    result, entries, _ = _plan(tmp_path, [ifupdown2])
    entry = entries["ifupdown2"]
    assert entry["current_upstream_branch"] == "master"
    assert entry["branch_verified"] is True
    assert entry["flags"] == [
        {"code": "FALLBACK_REF", "detail": "fell back to the default branch"},
        {"code": "MAPPING_WARNINGS",
         "detail": "no upstream tag for the shipped version"},
    ]
    assert [e.package for e in result.flagged] == ["ifupdown2"]


def test_the_generator_prints_the_flagged_packages(tmp_path, monkeypatch, capsys):
    from apm import generate_upstream_md

    class FakeMapping:
        def all(self):
            return [_resolution(), _tag_only()]

    class FakeContainer:
        mapping = FakeMapping()
        metadata_batch = _service()

    monkeypatch.setattr(generate_upstream_md, "Container", FakeContainer)
    assert generate_upstream_md.run(release="bookworm", branch="aminor",
                                    out_dir=tmp_path) == 0

    printed = capsys.readouterr().out
    assert "Flagged for review (1)" in printed
    assert "ethtool  BRANCH_NOT_VERIFIED, TAG_ONLY" in printed
    assert "iputils  " not in printed.split("Flagged for review", 1)[1]
    assert "Nothing was pushed. No pull request was created." in printed
