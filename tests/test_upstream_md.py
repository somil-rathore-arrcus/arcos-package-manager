"""debian/upstream.md generation.

Determinism matters because the outcome is decided by comparing the render with
what is already committed; if the render wobbled, every run would look like a
change.
"""

import pytest

from apm.domain.enums import (
    ResolutionMethod, ResolutionMode, ResolutionStatus, UpstreamMdOutcome,
)
from apm.domain.models import (
    PackageSource, Repository, ResolutionEvidence, UpstreamResolution,
)
from apm.services.upstream_md_service import UpstreamMdService

SERVICE = UpstreamMdService()


def _resolution(**overrides) -> UpstreamResolution:
    base = dict(
        package="pyrad", debian_release="bookworm",
        status=ResolutionStatus.VERIFIED, mode=ResolutionMode.AUTO,
        method=ResolutionMethod.DEP12, confidence="high",
        github_repository="Arrcus/pyrad", arcos_branch="aminor",
        arcos_commit="3b043f16bb8e",
        debian=PackageSource(
            source_package="pyrad", debian_release="bookworm", version="2.1-3",
            suite="bookworm",
            vcs_git="https://salsa.debian.org/python-team/packages/pyrad.git",
        ),
        upstream_repository=Repository(url="https://github.com/wichert/pyrad.git"),
        upstream_ref="master", upstream_commit="074aa3d33999",
        origin_kind="project", merge_base="984ad177f02d", behind=126, arcos_only=25,
        evidence=[ResolutionEvidence(kind="dep12", detail="DEP-12 Repository field")],
    )
    base.update(overrides)
    return UpstreamResolution(**base)


def test_render_contains_every_section():
    text = SERVICE.render(_resolution())
    for heading in ("## Package", "## ARCoS", "## Debian", "## Upstream",
                    "## Resolution", "## Evidence"):
        assert heading in text
    assert "https://github.com/wichert/pyrad.git" in text
    assert "- Branch: master" in text
    assert "- Commits behind upstream: 126" in text


def test_render_is_deterministic():
    assert SERVICE.render(_resolution()) == SERVICE.render(_resolution())


def test_render_has_no_timestamp_or_local_path():
    text = SERVICE.render(_resolution())
    assert "/Users/" not in text and "/tmp/" not in text
    import re
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:", text)


def test_missing_values_render_as_a_marker_not_blank():
    text = SERVICE.render(_resolution(debian=None, upstream_commit=None))
    assert "- Source package: N/A" in text
    assert "- Commit: N/A" in text


def test_absent_file_is_created():
    assert SERVICE.generate(_resolution()).outcome is UpstreamMdOutcome.CREATED


def test_identical_file_is_no_change():
    content = SERVICE.render(_resolution())
    assert SERVICE.generate(_resolution(), existing=content).outcome is \
        UpstreamMdOutcome.NO_CHANGE


def test_whitespace_only_difference_is_still_no_change():
    content = SERVICE.render(_resolution())
    noisy = content.replace("\n", "  \n").replace("\n", "\r\n") + "\n\n"
    assert SERVICE.generate(_resolution(), existing=noisy).outcome is \
        UpstreamMdOutcome.NO_CHANGE


def test_our_own_older_file_is_updated_with_a_diff():
    stale = SERVICE.render(_resolution(upstream_ref="old-branch"))
    document = SERVICE.generate(_resolution(), existing=stale)
    assert document.outcome is UpstreamMdOutcome.UPDATED
    assert "old-branch" in document.diff and "master" in document.diff


def test_a_hand_written_file_is_a_conflict_not_an_overwrite():
    """Someone else's file may hold things this tool does not know."""
    document = SERVICE.generate(
        _resolution(), existing="# pyrad\n\nNotes written by hand.\n"
    )
    assert document.outcome is UpstreamMdOutcome.CONFLICT
    assert document.existing_content is not None


def test_no_upstream_package_is_refused():
    with pytest.raises(ValueError):
        SERVICE.generate(_resolution(status=ResolutionStatus.NO_UPSTREAM))
