"""Unit tests for the evidence the correctness changes rest on."""

from __future__ import annotations

import json
import shutil

import pytest

import gitfixtures as fx
from apm.debian.dep12 import DebianArtifacts
from apm.debian.patches import build_report, parse_changelog, parse_patch
from apm.domain.enums import (
    Criticality, RefStrategy, ResolutionMode, ResolutionStatus, ReviewReason,
    VerificationLevel,
)
from apm.domain.models import (
    ComparisonSnapshot, ContentMatch, CuratedUpstream, RefSelection, Repository,
    SecurityEvidence, UpstreamResolution,
)
from apm.gitio.ancestry import AncestryChecker, ProbeError
from apm.gitio.transport import GitResult, LocalGit, Transports
from apm.gitio.verify import RefListing, classify_git_error
from apm.gitio.workspace import GitWorkspace, GitWorkspaceError
from apm.services.criticality_service import CriticalityService
from apm.services.upstream_md_service import UpstreamMdService
from apm.upstream.refs import (
    ProbedRef, RefCandidateSpec, dep14_tag, packaging_candidates, select_ref,
    upstream_candidates,
)

git_required = pytest.mark.skipif(shutil.which("git") is None, reason="git")


# -- A. candidates per release ------------------------------------------------

LISTING = RefListing(
    ok=True,
    branches={"main": "m", "openssl-3.0": "s30", "openssl-3.1": "s31",
              "debian/bookworm": "db", "debian/trixie": "dt", "debian/sid": "ds"},
    tags={"openssl-3.0.20": "t3020", "openssl-3.5.4": "t354",
          "debian/3.0.20-1_deb12u2": "pb", "debian/3.5.4-1": "pt"},
    head="main",
)


def test_bookworm_candidates_put_the_release_before_master():
    specs = upstream_candidates("u", LISTING, "3.0.20", None, ["openssl"],
                                "master", RefStrategy.CURATED)
    assert [(s.ref, s.strategy) for s in specs][:3] == [
        ("openssl-3.0.20", RefStrategy.EXACT_TAG),
        ("openssl-3.0", RefStrategy.MAINTENANCE_BRANCH),
        ("master", RefStrategy.CURATED),
    ]
    assert specs[-1].strategy is RefStrategy.DEFAULT_BRANCH
    assert "openssl-3.1" not in {s.ref for s in specs}


def test_trixie_candidates_follow_trixies_version():
    specs = upstream_candidates("u", LISTING, "3.5.4", None, ["openssl"],
                                None, RefStrategy.DEFAULT_BRANCH)
    assert specs[0].ref == "openssl-3.5.4" and specs[0].strategy is RefStrategy.EXACT_TAG
    assert all("3.0" not in s.ref for s in specs)


@pytest.mark.parametrize("release,version,tag,branch", [
    ("bookworm", "3.0.20-1~deb12u2", "debian/3.0.20-1_deb12u2", "debian/bookworm"),
    ("trixie", "3.5.4-1", "debian/3.5.4-1", "debian/trixie"),
])
def test_packaging_candidates_name_the_release_not_sid(release, version, tag, branch):
    specs = packaging_candidates("p", LISTING, version, release,
                                 "12" if release == "bookworm" else "13", None,
                                 ["debian/sid"])
    assert specs[0].ref == tag and specs[0].strategy is RefStrategy.PACKAGING_TAG
    assert specs[1].ref == branch and specs[1].strategy is RefStrategy.PACKAGING_BRANCH
    sid = next(s for s in specs if s.ref == "debian/sid")
    assert sid.strategy is RefStrategy.PACKAGING_FALLBACK


def test_dep14_mangling():
    assert dep14_tag("1:6.1-1") == "debian/1%6.1-1"
    assert dep14_tag("3.0.20-1~deb12u2") == "debian/3.0.20-1_deb12u2"


def _probed(ref, strategy, index, behind, sha=None, shared=True, kind="branch"):
    return ProbedRef(spec=RefCandidateSpec("u", ref, strategy, kind), index=index,
                     reachable=True, shared=shared, sha=sha or ref, behind=behind)


def test_selection_prefers_the_series_branch_that_contains_the_tag():
    probed = [_probed("v1.2.0", RefStrategy.EXACT_TAG, 0, 0, kind="tag"),
              _probed("stable-1.2", RefStrategy.MAINTENANCE_BRANCH, 1, 4),
              _probed("main", RefStrategy.DEFAULT_BRANCH, 2, 900)]
    choice = select_ref(probed, "u", contains={(0, 1), (0, 2)})
    assert choice.target.spec.ref == "stable-1.2" and choice.base.spec.ref == "v1.2.0"


def test_selection_falls_back_explicitly_and_says_so():
    probed = [_probed("v1.2.0", RefStrategy.EXACT_TAG, 0, 0, shared=False),
              _probed("main", RefStrategy.DEFAULT_BRANCH, 1, 900)]
    choice = select_ref(probed, "u")
    assert choice.target.spec.ref == "main"
    assert choice.reason.startswith("FALLBACK")


def test_a_series_branch_that_does_not_contain_the_tag_is_not_the_target():
    probed = [_probed("v1.2.0", RefStrategy.EXACT_TAG, 0, 0, kind="tag"),
              _probed("stable-1.2", RefStrategy.MAINTENANCE_BRANCH, 1, 4)]
    choice = select_ref(probed, "u", contains=set())
    assert choice.target.spec.ref == "v1.2.0"


# -- F. Debian patch history ---------------------------------------------------

CHANGELOG = """\
demo (1.2.0-1+deb12u1) bookworm-security; urgency=high

  * Non-maintainer upload by the Security Team.
  * Fix CVE-2026-1001: buffer overflow (CVE-2026-1001.patch)

 -- Security Team <team@security.debian.org>  Mon, 01 Jun 2026 10:00:00 +0000

demo (1.2.0-1) unstable; urgency=medium

  * New upstream release.

 -- Maintainer <m@example.org>  Mon, 01 Jan 2026 10:00:00 +0000

demo (1.1.0-3) unstable; urgency=low

  * Fix CVE-2025-0001.

 -- Maintainer <m@example.org>  Mon, 01 Jun 2025 10:00:00 +0000
"""

SECURITY_PATCH = """\
From: Upstream Dev <dev@example.org>
Subject: [PATCH] check length before copy
Origin: upstream, https://github.com/demo/demo/commit/0123456789abcdef0123456789abcdef01234567
Bug-Debian: https://bugs.debian.org/1000001
---
--- a/a.c
+++ b/a.c
"""

VENDOR_PATCH = """\
Description: use Debian paths
Origin: vendor
Forwarded: not-needed
---
--- a/b.c
+++ b/b.c
"""


def test_changelog_entries_and_security_uploads():
    uploads = parse_changelog(CHANGELOG)
    assert [u.version for u in uploads] == ["1.2.0-1+deb12u1", "1.2.0-1", "1.1.0-3"]
    assert uploads[0].security and uploads[0].cve_ids == ["CVE-2026-1001"]
    assert uploads[0].urgency == "high"
    assert not uploads[1].security


def test_dep3_headers_name_the_upstream_commit_and_the_category():
    patch = parse_patch("check-length.patch", SECURITY_PATCH)
    assert patch.subject == "check length before copy"
    assert patch.upstream_commit == "0123456789abcdef0123456789abcdef01234567"
    assert patch.category == "upstream_backport"
    vendor = parse_patch("paths.patch", VENDOR_PATCH)
    assert vendor.category == "debian_specific"


def test_the_report_covers_the_shipped_version_and_links_cves_to_patches():
    artifacts = DebianArtifacts(
        {}, None, True, changelog=CHANGELOG,
        patch_series="CVE-2026-1001.patch\npaths.patch # comment\n",
        patches={"CVE-2026-1001.patch": SECURITY_PATCH, "paths.patch": VENDOR_PATCH},
        source_format="3.0 (quilt)",
    )
    report = build_report("demo", "1.2.0-1+deb12u1", "bookworm", artifacts)
    assert [u.version for u in report.uploads] == ["1.2.0-1+deb12u1", "1.2.0-1"]
    assert report.cve_ids == ["CVE-2026-1001"]
    security = report.security_patches
    assert [p.name for p in security] == ["CVE-2026-1001.patch"]
    assert report.patches[1].category == "debian_specific"


def test_a_source_that_could_not_be_fetched_is_unavailable_not_empty():
    report = build_report("demo", "1.0-1", "bookworm",
                          DebianArtifacts({}, None, False, "no tarball"))
    assert report.available is False and report.reason == "no tarball"


# -- L. git failures are never zero --------------------------------------------

class Failing:
    name = "fake"

    def __init__(self, result):
        self.result = result

    def shell(self, command, timeout=None):
        return self.result


def test_a_failed_rev_list_count_raises_instead_of_returning_zero():
    ws = GitWorkspace(Failing(GitResult(False, "", "fatal: bad revision")), "/x")
    with pytest.raises(GitWorkspaceError):
        ws.count("a..b")


def test_merge_base_distinguishes_unrelated_from_broken():
    unrelated = GitWorkspace(Failing(GitResult(False, "", "")), "/x")
    assert unrelated.merge_bases("a", "b") == []
    broken = GitWorkspace(Failing(GitResult(False, "", "fatal: Not a valid object")), "/x")
    with pytest.raises(GitWorkspaceError):
        broken.merge_bases("a", "b")


@pytest.mark.parametrize("stderr,kind", [
    ("fatal: couldn't find remote ref nope", "INVALID_REF"),
    ("ssh: Could not resolve host github.com", "NETWORK_ERROR"),
    ("fatal: unable to access 'https://x/': Connection refused", "NETWORK_ERROR"),
    ("fatal: something else", "GIT_ERROR"),
])
def test_git_errors_are_classified(stderr, kind):
    assert classify_git_error(stderr) == kind


def test_a_probe_that_dies_is_a_probe_error(tmp_path):
    class Dead:
        name = "dead"

        def shell(self, command, timeout=None):
            if "cat >" in command:
                return GitResult(True, "", "")
            return GitResult(False, "", "Killed")

    transports = Transports(backend="local")
    transports.local = Dead()
    checker = AncestryChecker(transports, tmp_path / "c", workspace_root=str(tmp_path))
    with pytest.raises(ProbeError) as raised:
        checker.check("demo", "bookworm", "file:///x", "main", [("u", "main", None)])
    assert raised.value.kind == "PROBE_FAILED"
    assert not list((tmp_path / "c").iterdir()), "a failure is never cached"


@git_required
def test_the_probe_reports_a_missing_ref_and_an_unreachable_fork(tmp_path):
    up = fx.init(tmp_path / "up")
    fx.commit(up, "a", "a\n", "a")
    checker = AncestryChecker(Transports(backend="local"), tmp_path / "c",
                              workspace_root=str(tmp_path / "ws"))
    data = checker.check("demo", "bookworm", "file://" + str(up), "main",
                         [("file://" + str(up), "nope", None)])
    [cand] = data["candidates"]
    assert cand["error_kind"] == "INVALID_REF" and "behind" not in cand
    with pytest.raises(ProbeError):
        checker.check("demo", "bookworm", "file://" + str(tmp_path / "gone"), "main",
                      [("file://" + str(up), "main", None)], refresh=True)


def test_the_cache_key_changes_when_any_commit_moves():
    a = AncestryChecker.cache_key("arcos", "main", "aaa", [("u", "main", "s1")])
    b = AncestryChecker.cache_key("arcos", "main", "aaa", [("u", "main", "s2")])
    c = AncestryChecker.cache_key("arcos", "main", "bbb", [("u", "main", "s1")])
    assert a != b and a != c


def test_local_git_can_carry_a_mounted_key_without_a_password():
    git = LocalGit(ssh_identity="/home/apm/.ssh/id_git",
                   known_hosts="/home/apm/.ssh/known_hosts")
    command = git.git_ssh_command()
    assert "-i /home/apm/.ssh/id_git" in command and "BatchMode=yes" in command
    assert git._env()["GIT_SSH_COMMAND"] == command
    assert LocalGit()._env() is None


# -- G. criticality ------------------------------------------------------------

def test_external_evidence_makes_a_commit_critical_and_says_where_from():
    service = CriticalityService()
    assessment = service.assess("tidy up", "")
    assert assessment.level is Criticality.UNKNOWN
    service.enrich(assessment, [SecurityEvidence(
        source="debian-patch", identifier="CVE-2026-9999", detail="patch x")])
    assert assessment.level is Criticality.CRITICAL
    assert assessment.sources == ["debian-patch"]
    assert assessment.cve_ids == ["CVE-2026-9999"]


def test_message_regexes_still_work_and_record_their_source():
    a = CriticalityService().assess("fix", "Cc: stable@vger.kernel.org\n")
    assert a.level is Criticality.STABLE_RELEVANT and a.sources == ["commit-message"]


# -- D. the permanent record ---------------------------------------------------

def _resolution(**overrides):
    values = dict(
        package="iproute2", debian_release="bookworm",
        status=ResolutionStatus.VERIFIED, mode=ResolutionMode.AUTO,
        verification_level=VerificationLevel.SHARED_HISTORY, confidence="high",
        github_repository="Arrcus/iproute2", arcos_branch="aminor",
        arcos_commit="a" * 40,
        upstream_repository=Repository(url="https://git.kernel.org/x/iproute2.git"),
        upstream_ref="v6.1.0", upstream_tag="v6.1.0", upstream_commit="b" * 40,
        upstream_commit_date="2022-12-12T10:00:00+00:00",
        merge_base="c" * 40, merge_bases=["c" * 40], behind=0, arcos_only=434,
        counts_basis="raw commit-graph counts, head-based",
        ref_selection=RefSelection(
            ref="v6.1.0", kind="tag", strategy=RefStrategy.EXACT_TAG,
            reason="v6.1.0 is the tag for the shipped version",
            debian_upstream_version="6.1.0", series="6.1", base_tag="v6.1.0",
            base_sha="b" * 40, arcos_contains_base=True),
    )
    values.update(overrides)
    return UpstreamResolution(**values)


def test_upstream_md_records_the_release_reference_and_labels_raw_counts():
    text = UpstreamMdService().render(_resolution())
    assert "## Release reference" in text
    assert "- Upstream base tag: v6.1.0 (bbbbbbbbbbbb)" in text
    assert "- Compared against: v6.1.0 (tag, exact_tag)" in text
    assert "- Verification level: SHARED_HISTORY" in text
    assert "- Upstream commits not in ARCoS (raw): 0" in text
    assert "measured aaaaaaaaaaaa..bbbbbbbbbbbb" in text
    assert "## Backlog" not in text, "no comparison, no backlog claim"
    assert text == UpstreamMdService().render(_resolution()), "deterministic"


def test_upstream_md_states_the_backlog_only_from_a_matching_comparison():
    snapshot = ComparisonSnapshot(
        arcos_commit="a" * 40, upstream_repository="https://git.kernel.org/x/iproute2.git",
        upstream_ref="v6.1.0", upstream_commit="b" * 40, base_tag="v6.1.0",
        series="6.1", relevant_upstream=12, definitely_present=3,
        probably_present=1, missing=6, unknown_presence=2, critical_missing=1)
    text = UpstreamMdService().render(_resolution(comparison=snapshot))
    assert "- Relevant upstream commits: 12" in text
    assert "- Already present: 4 (definitely 3, probably 1)" in text
    assert "- Missing: 6" in text and "- Critical/security missing: 1" in text


def test_upstream_md_records_curated_conflicts_and_unapproved_content_matches():
    text = UpstreamMdService().render(_resolution(
        status=ResolutionStatus.NEEDS_REVIEW,
        verification_level=VerificationLevel.REF_EXISTS,
        review_reasons=[ReviewReason.CURATED_CONFLICT, ReviewReason.NO_SHARED_HISTORY],
        curated=CuratedUpstream(repository="https://github.com/a/a.git", ref="master",
                                conflict="it shares no history"),
        content_match=ContentMatch(base_tag="v1.0", score=0.98, files_compared=50,
                                   files_differing=1),
        behind=None, arcos_only=None, reason="conflict"))
    assert "- Review reasons: CURATED_CONFLICT, NO_SHARED_HISTORY" in text
    assert "- Curated conflict: it shares no history" in text
    assert "- Approved: no - not used for any comparison" in text
    assert "(raw)" not in text


# -- J / mapping: full fidelity, snapshots only when they match ----------------

def test_the_json_mapping_round_trips_and_attaches_only_matching_snapshots(tmp_path):
    from apm.services.comparison_store import ComparisonStore
    from apm.services.mapping_store import MappingStore

    resolution = _resolution()
    (tmp_path / "upstream-resolutions.json").write_text(json.dumps(
        {"resolutions": [resolution.model_dump(mode="json")]}))
    (tmp_path / "upstream-mapping.csv").write_text("Package\n")
    snapshot = ComparisonSnapshot(
        arcos_commit="a" * 40, upstream_repository=resolution.upstream_repository.url,
        upstream_ref="v6.1.0", upstream_commit="b" * 40, relevant_upstream=5)
    (tmp_path / "c" / "bookworm").mkdir(parents=True)
    (tmp_path / "c" / "bookworm" / "iproute2.json").write_text(snapshot.model_dump_json())

    store = MappingStore(tmp_path / "upstream-mapping.csv",
                         comparisons=ComparisonStore(tmp_path / "c"))
    loaded = store.get("iproute2", "bookworm")
    assert loaded.ref_selection.base_tag == "v6.1.0"
    assert loaded.upstream_commit == "b" * 40, "full SHA, not the CSV's 12"
    assert loaded.comparison.relevant_upstream == 5

    stale = snapshot.model_copy(update={"upstream_commit": "d" * 40})
    (tmp_path / "c" / "bookworm" / "iproute2.json").write_text(stale.model_dump_json())
    assert store.get("iproute2", "bookworm").comparison is None


# -- Part 5: runtime state is written where it is writable --------------------

def test_approvals_live_in_the_writable_output_directory(tmp_path, monkeypatch):
    from apm.services.approvals import APPROVALS_FILE_ENV, ApprovalStore, approvals_file

    monkeypatch.delenv(APPROVALS_FILE_ENV, raising=False)
    assert approvals_file(tmp_path) == tmp_path / "out" / "approvals.yaml"
    monkeypatch.setenv(APPROVALS_FILE_ENV, str(tmp_path / "elsewhere.yaml"))
    store = ApprovalStore(approvals_file(tmp_path))
    with pytest.raises(ValueError):
        store.approve("demo", "bookworm", {"base_tag": "v1"})
    entry = store.approve("demo", "bookworm", {
        "repository": "u", "base_tag": "v1", "base_sha": "s", "method": "tree-compare",
        "verified_by": "Somil"})
    assert entry["verified_at"]
    assert store.get("demo", "bookworm")["verified_by"] == "Somil"
    assert store.revoke("demo", "bookworm") and store.get("demo", "bookworm") is None


def test_doctor_reports_storage_without_needing_git(tmp_path, monkeypatch, capsys):
    from apm import config, doctor

    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv(config.PACKAGES_FILE_ENV, str(tmp_path / "out" / "packages.yaml"))
    monkeypatch.setenv("APM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("APM_WORKSPACE_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("APM_GIT_BACKEND", "local")
    monkeypatch.delenv("APM_GITHUB_TOKEN", raising=False)
    doctor.run_doctor(check_manifest=False)
    out = capsys.readouterr().out
    assert f"PASS  output directory {tmp_path / 'out'} is writable" in out
    assert f"PASS  runtime package catalogue {tmp_path / 'out' / 'packages.yaml'}" in out
    assert "WARN  APM_GITHUB_TOKEN is not set" in out


# -- manual upstream override -------------------------------------------------

@git_required
def test_a_manual_upstream_is_verified_only_by_shared_history(tmp_path):
    from apm.gitio.verify import Verifier
    from apm.gitio.workspaces import WorkspaceManager
    from apm.services.upstream_service import UpstreamService

    up = fx.init(tmp_path / "up")
    fx.commit(up, "a", "a\n", "a")
    arcos = fx.clone(up, tmp_path / "arcos")
    fx.commit(up, "b", "b\n", "b")
    stranger = fx.init(tmp_path / "stranger")
    fx.commit(stranger, "z", "z\n", "z")
    transports = Transports(backend="local")
    base = UpstreamResolution(
        package="demo", debian_release="bookworm",
        status=ResolutionStatus.NEEDS_REVIEW, arcos_repository=str(arcos),
        arcos_branch="main", arcos_commit=fx.git(arcos, "rev-parse", "HEAD"))

    class Fixed(UpstreamService):
        def resolve(self, *a, **k):
            return base.model_copy(deep=True)

    class Settings:
        packaging_hosts = set()

    service = Fixed(None, None, Verifier(transports), Settings(),
                    workspaces=WorkspaceManager(transports, root=str(tmp_path / "ws"),
                                                private_url_hint="file:///x"))
    good = service.resolve_manual("demo", "bookworm", str(up), "main")
    assert good.status is ResolutionStatus.VERIFIED
    assert good.verification_level is VerificationLevel.SHARED_HISTORY
    assert good.behind == 1 and good.ref_selection.strategy is RefStrategy.MANUAL

    bad = service.resolve_manual("demo", "bookworm", str(stranger), "main")
    assert bad.status is ResolutionStatus.NEEDS_REVIEW
    assert ReviewReason.NO_SHARED_HISTORY in bad.review_reasons
    assert bad.behind is None


@git_required
def test_the_probe_keeps_refs_so_later_fetches_negotiate(tmp_path):
    """Without a ref, git has nothing to advertise as 'have', and every
    candidate fetch re-downloads the whole history."""
    up = fx.init(tmp_path / "up")
    fx.commit(up, "a", "a\n", "a")
    fx.git(up, "branch", "stable")
    checker = AncestryChecker(Transports(backend="local"), tmp_path / "c",
                              workspace_root=str(tmp_path / "ws"))
    checker.check("demo", "bookworm", "file://" + str(up), "main",
                  [("file://" + str(up), "stable", None),
                   ("file://" + str(up), "main", None)])
    refs = fx.git(tmp_path / "ws" / "ancestry" / "demo__bookworm", "for-each-ref",
                  "--format=%(refname)", "refs/apm/probe").split()
    assert refs == ["refs/apm/probe/arcos", "refs/apm/probe/c0", "refs/apm/probe/c1"]


def test_the_kernel_series_is_not_padded_with_the_mainline_tip():
    listing = RefListing(ok=True, branches={"master": "m", "linux-6.1.y": "s"},
                         tags={"v6.1.150": "t"}, head="master")
    specs = upstream_candidates("k", listing, "6.1.150", None, ["linux"],
                                "linux-6.1.y", RefStrategy.KERNEL_SERIES)
    assert [s.ref for s in specs] == ["v6.1.150", "linux-6.1.y"]
