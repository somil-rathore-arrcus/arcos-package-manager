"""The mapping store, which is what makes the dashboard fast.

The point of these is that the store returns the SAME answer live resolution
would, without doing the work - and that the values the comparison engine needs
survive the round trip through CSV.
"""

from __future__ import annotations

import textwrap

import pytest

from apm.domain.enums import PackageCategory, ResolutionStatus
from apm.services.mapping_store import MappingStore

HEADER = (
    "Package,Category,Debian Release,Status,Confidence,Resolution Method,"
    "ARCoS Repository,ARCoS Branch,ARCoS Commit,Debian Source Package,"
    "Debian Version,Upstream Repository,Upstream Ref,Origin Kind,"
    "Upstream Commit,Commits Behind,ARCoS-only Commits,Merge Base,"
    "Vcs-Git (packaging),Homepage,ARCoS Link,Debian Link,Upstream Link,"
    "Evidence,Notes\n"
)

ROWS = textwrap.dedent('''\
    pyrad,debian_upstream,bookworm,VERIFIED,high,dep12,Arrcus/pyrad,aminor,3b043f16bb8e,pyrad,2.1-3,https://github.com/wichert/pyrad.git,master,project,074aa3d33999,109,19,984ad177f02d,https://salsa.debian.org/python-team/packages/pyrad.git,https://pypi.org/project/pyrad,https://github.com/Arrcus/pyrad/commit/x,https://packages.debian.org/source/bookworm/pyrad,https://github.com/wichert/pyrad/tree/master,"debian/upstream/metadata Repository: https://github.com/wichert/pyrad.git
    ancestry verified: merge-base 984ad177f02d, 109 commits behind, 19 ARCoS-only",
    bcmsdk,vendor,bookworm,NO_UPSTREAM,none,not_applicable,Arrcus/bcm_sdk,aminor,aaaaaaaaaaaa,,,,,,,,,,,,https://github.com/Arrcus/bcm_sdk/commit/y,,,,"No upstream exists for this package; it is vendor."
    babeltrace,debian_upstream,bookworm,NEEDS_REVIEW,low,curated,Arrcus/babeltrace,aminor,bbbbbbbbbbbb,babeltrace,1.5.11-1,https://github.com/efficios/babeltrace.git,stable-1.5,project,,,,,,,,,,"ancestry: NO candidate shares history with the fork","The ARCoS fork has no commit in common with any candidate upstream."
''')


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "upstream-mapping.csv"
    path.write_text(HEADER + ROWS, encoding="utf-8")
    return MappingStore(path)


def test_loads_every_row(store):
    described = store.describe()
    assert described["available"] is True
    assert described["rows"] == 3
    assert described["statuses"] == {
        "VERIFIED": 1, "NO_UPSTREAM": 1, "NEEDS_REVIEW": 1,
    }


def test_verified_row_is_comparable_and_complete(store):
    resolution = store.get("pyrad", "bookworm")
    assert resolution.status is ResolutionStatus.VERIFIED
    assert resolution.upstream_repository.url == "https://github.com/wichert/pyrad.git"
    assert resolution.upstream_ref == "master"
    assert resolution.behind == 109 and resolution.arcos_only == 19
    assert resolution.merge_base == "984ad177f02d"
    assert resolution.debian.source_package == "pyrad"
    assert resolution.debian.version == "2.1-3"
    assert resolution.origin_kind == "project"
    assert resolution.comparable is True


def test_multi_line_evidence_survives_the_csv(store):
    evidence = store.get("pyrad", "bookworm").evidence
    assert len(evidence) == 2
    kinds = {e.kind for e in evidence}
    assert "dep12" in kinds and "ancestry" in kinds
    assert any(e.url == "https://github.com/wichert/pyrad.git" for e in evidence)


def test_no_upstream_row_is_not_comparable_and_is_not_a_failure(store):
    resolution = store.get("bcmsdk", "bookworm")
    assert resolution.status is ResolutionStatus.NO_UPSTREAM
    assert resolution.category is PackageCategory.VENDOR
    assert resolution.upstream_repository is None
    assert resolution.comparable is False
    assert resolution.notes


def test_needs_review_keeps_its_reason_and_invents_nothing(store):
    resolution = store.get("babeltrace", "bookworm")
    assert resolution.status is ResolutionStatus.NEEDS_REVIEW
    # It still records the candidate that was considered, but not as proven.
    assert resolution.merge_base is None
    assert resolution.notes and "no commit in common" in resolution.notes[0]
    assert resolution.comparable is False


def test_unknown_package_returns_nothing_rather_than_guessing(store):
    assert store.get("nope", "bookworm") is None
    assert store.get("pyrad", "trixie") is None


def test_returns_a_copy_so_callers_cannot_corrupt_the_store(store):
    first = store.get("pyrad", "bookworm")
    first.upstream_ref = "tampered"
    assert store.get("pyrad", "bookworm").upstream_ref == "master"


def test_missing_file_is_not_an_error(tmp_path):
    store = MappingStore(tmp_path / "absent.csv")
    assert store.available is False
    assert store.get("pyrad", "bookworm") is None
    assert store.describe()["rows"] == 0


def test_reloads_when_the_file_changes(store, tmp_path):
    assert store.get("pyrad", "bookworm").upstream_ref == "master"
    path = tmp_path / "upstream-mapping.csv"
    path.write_text(
        HEADER + ROWS.replace(",master,project", ",development,project"),
        encoding="utf-8",
    )
    import os
    os.utime(path, (0, 0))  # force a different mtime
    assert store.get("pyrad", "bookworm").upstream_ref == "development"


def test_rejected_candidates_survive_the_round_trip(tmp_path):
    """A resolution served from the mapping must not look unconsidered.

    The store used to build only the accepted candidate, so the dashboard's
    "Candidates considered" panel showed one row - reading as though nothing
    else had been looked at, when the packaging repository had in fact been
    found, probed and turned down.
    """
    row = (
        'iputils,debian_upstream,bookworm,VERIFIED,high,dep12,Arrcus/iputils,'
        'aminor,991673a12674,iputils,3:20221126-1,'
        'https://github.com/iputils/iputils.git,master,project,18717a3984c8,'
        '142,272,10b50784aae3,https://salsa.debian.org/debian/iputils.git,,'
        ',,,"debian/upstream/metadata Repository: https://github.com/iputils/iputils.git\n'
        'Vcs-Git https://salsa.debian.org/debian/iputils.git is Debian packaging, not upstream",\n'
    )
    path = tmp_path / "upstream-mapping.csv"
    path.write_text(HEADER + row, encoding="utf-8")

    resolution = MappingStore(path).get("iputils", "bookworm")
    accepted = [c for c in resolution.candidates if c.accepted]
    rejected = [c for c in resolution.candidates if not c.accepted]

    assert [c.repository for c in accepted] == [
        "https://github.com/iputils/iputils.git"
    ]
    assert [c.repository for c in rejected] == [
        "https://salsa.debian.org/debian/iputils.git"
    ]
    assert "not upstream" in rejected[0].rejected_reason
