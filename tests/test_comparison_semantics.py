"""What the comparison says about each upstream commit, on real git.

Backports in all their shapes (cherry-picked, adapted, partial, reverted),
Debian-only patches, security evidence from outside the commit message,
moving branches, criss-cross history and synthesized ancestry.
"""

from __future__ import annotations

import shutil

import pytest

import gitfixtures as fx
from apm.domain.enums import (
    CommitClass, Criticality, ErrorCode, Presence, RefStrategy, ResolutionStatus,
    VerificationLevel,
)
from apm.domain.models import (
    ContentMatch, DebianPatch, DebianPatchReport, RefSelection, Repository,
    UpstreamResolution,
)
from apm.gitio.transport import Transports
from apm.gitio.workspaces import WorkspaceManager
from apm.services.comparison_service import ComparisonError, ComparisonService
from apm.services.comparison_store import ComparisonStore
from apm.services.security_service import (
    DebianSecurityProvider, OsvSecurityProvider, SecurityService,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")

BASE_FILE = "".join(f"line {i}\n" for i in range(1, 21))


@pytest.fixture
def world(tmp_path):
    """Upstream: base with a 20-line file, then the commits under test."""
    up = fx.init(tmp_path / "upstream")
    fx.commit(up, "lib.c", BASE_FILE, "base")
    fx.commit(up, "other.c", "int other;\n", "other base")
    arcos = fx.clone(up, tmp_path / "arcos")
    return {"up": up, "arcos": arcos, "root": tmp_path}


def _service(root, **kwargs):
    manager = WorkspaceManager(Transports(backend="local"),
                               root=str(root / "ws"), private_url_hint="file:///x")
    return ComparisonService(manager, **kwargs)


def _resolution(world, **overrides):
    values = dict(
        package="demo", debian_release="bookworm",
        status=ResolutionStatus.VERIFIED,
        verification_level=VerificationLevel.SHARED_HISTORY,
        arcos_repository=str(world["arcos"]), arcos_branch="main",
        upstream_repository=Repository(url=str(world["up"])), upstream_ref="main",
    )
    values.update(overrides)
    return UpstreamResolution(**values)


def _edit(text, old, new):
    assert old in text
    return text.replace(old, new)


def _by_subject(result):
    commits = result.missing_upstream + result.already_backported
    return {c.subject: c for c in commits}


# -- E. backports --------------------------------------------------------------

def test_a_cherry_pick_trailer_is_definitely_present(world):
    fx.commit(world["arcos"], "x.c", "x\n", "arcos X")
    fixed = _edit(BASE_FILE, "line 5\n", "line 5 fixed\n")
    sha = fx.commit(world["up"], "lib.c", fixed, "fix line 5")
    fx.git(world["arcos"], "fetch", "-q", "origin")
    # Applied by hand with a different diff, but the trailer names the commit.
    (world["arcos"] / "lib.c").write_text(_edit(BASE_FILE, "line 5\n",
                                                "line 5 fixed\nextra\n"))
    fx.git(world["arcos"], "commit", "-q", "-am",
           f"fix line 5\n\n(cherry picked from commit {sha})")

    result = _service(world["root"]).compare(_resolution(world))
    commit = _by_subject(result)["fix line 5"]
    assert commit.presence is Presence.DEFINITELY_PRESENT
    assert commit.classification is CommitClass.ALREADY_BACKPORTED
    assert "cherry-picked" in commit.presence_evidence[0]
    assert result.summary.definitely_present == 1
    assert [c.subject for c in result.arcos_only] == ["arcos X"]


def test_an_identical_patch_id_is_definitely_present(world):
    fx.commit(world["arcos"], "x.c", "x\n", "arcos X")
    sha = fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 3\n", "line 3!\n"),
                    "fix three")
    fx.git(world["arcos"], "fetch", "-q", "origin")
    fx.git(world["arcos"], "cherry-pick", sha)
    commit = _by_subject(_service(world["root"]).compare(_resolution(world)))["fix three"]
    assert commit.presence is Presence.DEFINITELY_PRESENT
    assert "identical patch-id" in commit.presence_evidence[0]


def test_an_adapted_backport_is_probably_present_not_missing(world):
    """Same change on code whose context differs: patch-id and apply both
    differ, but every line the change adds is already there."""
    upstream_text = _edit(BASE_FILE, "line 10\n",
                          "line 10\n    check_bounds(len);\n    validate(ptr);\n")
    fx.commit(world["up"], "lib.c", upstream_text, "add bounds checks")
    arcos_text = _edit(BASE_FILE, "line 9\n", "line 9 arcos variant\n")
    arcos_text = _edit(arcos_text, "line 11\n", "line 11 arcos variant\n")
    arcos_text = _edit(arcos_text, "line 10\n",
                       "line 10\n    check_bounds(len);\n    validate(ptr);\n")
    (world["arcos"] / "lib.c").write_text(arcos_text)
    fx.git(world["arcos"], "commit", "-q", "-am", "local hardening")

    commit = _by_subject(_service(world["root"]).compare(_resolution(world)))[
        "add bounds checks"]
    assert commit.presence is Presence.PROBABLY_PRESENT
    assert "adapted backport" in commit.presence_evidence[0]


def test_a_partial_backport_is_unknown_never_claimed_present(world):
    a = _edit(BASE_FILE, "line 2\n", "line 2\n    first_guard();\n    first_log();\n")
    (world["up"] / "lib.c").write_text(a)
    (world["up"] / "other.c").write_text("int other;\n    second_guard();\n"
                                         "    second_log();\n")
    fx.git(world["up"], "commit", "-q", "-am", "guards in two files")
    # ARCoS took only lib.c's half, with different surrounding context.
    partial = _edit(BASE_FILE, "line 1\n", "line 1 arcos\n")
    partial = _edit(partial, "line 3\n", "line 3 arcos\n")
    partial = _edit(partial, "line 2\n", "line 2\n    first_guard();\n    first_log();\n")
    (world["arcos"] / "lib.c").write_text(partial)
    fx.git(world["arcos"], "commit", "-q", "-am", "take half of the guards")

    commit = _by_subject(_service(world["root"]).compare(_resolution(world)))[
        "guards in two files"]
    assert commit.presence is Presence.UNKNOWN
    assert "partially present: 1 of 2" in commit.presence_evidence[0]
    assert commit.classification is CommitClass.MISSING_UPSTREAM


def test_a_change_that_applies_cleanly_is_missing(world):
    fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 7\n", "line 7 fix\n"),
              "fix seven")
    result = _service(world["root"]).compare(_resolution(world))
    commit = _by_subject(result)["fix seven"]
    assert commit.presence is Presence.MISSING
    assert "applies cleanly" in commit.presence_evidence[0]
    assert result.summary.missing == 1 and result.summary.relevant_upstream == 1


def test_an_upstream_commit_reverted_upstream_nets_to_nothing(world):
    sha = fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 4\n", "line 4 bad\n"),
                    "risky change")
    fx.git(world["up"], "revert", "--no-edit", sha)
    result = _service(world["root"]).compare(_resolution(world))
    risky = _by_subject(result)["risky change"]
    assert risky.reverted_by is not None
    assert result.summary.reverted_upstream >= 1
    assert result.summary.missing == 0, "a reverted pair is not a missing fix"


def test_a_backport_arcos_reverted_is_missing_again(world):
    fx.commit(world["arcos"], "x.c", "x\n", "arcos X")
    sha = fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 6\n", "line 6!\n"),
                    "fix six")
    fx.git(world["arcos"], "fetch", "-q", "origin")
    fx.git(world["arcos"], "cherry-pick", "-x", sha)
    picked = fx.git(world["arcos"], "rev-parse", "HEAD")
    fx.git(world["arcos"], "revert", "--no-edit", picked)

    commit = _by_subject(_service(world["root"]).compare(_resolution(world)))["fix six"]
    assert commit.presence is Presence.MISSING
    assert "reverted it" in commit.presence_evidence[0]


# -- C. head-based ranges -------------------------------------------------------

def test_criss_cross_history_does_not_report_arcos_work_as_missing(world):
    up, arcos = world["up"], world["arcos"]
    a1 = fx.commit(up, "a1.c", "a1\n", "a1 upstream")
    b1 = fx.commit(arcos, "b1.c", "b1\n", "b1 arcos")
    fx.git(up, "fetch", "-q", str(arcos), "main:refs/remotes/arcos/main")
    fx.git(arcos, "fetch", "-q", "origin")
    fx.git(up, "merge", "-q", "--no-edit", "--no-ff", b1)
    fx.git(arcos, "merge", "-q", "--no-edit", "--no-ff", a1)
    fx.commit(up, "u3.c", "u3\n", "u3 upstream")

    result = _service(world["root"]).compare(_resolution(world))
    assert len(result.summary.merge_bases) == 2
    assert [c.subject for c in result.missing_upstream] == ["u3 upstream"]
    assert "b1 arcos" not in {c.subject for c in result.missing_upstream}
    assert any("criss-cross" in w for w in result.warnings)


def test_commits_in_the_shipped_release_are_marked(world):
    fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 1\n", "line 1 r\n"),
              "in the release")
    fx.git(world["up"], "tag", "v1.0")
    fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 1\n", "line 1 rr\n"),
              "after the release")
    resolution = _resolution(world, ref_selection=RefSelection(
        ref="main", kind="branch", strategy=RefStrategy.MAINTENANCE_BRANCH,
        base_tag="v1.0", series="1.0"))
    result = _service(world["root"]).compare(resolution)
    by = _by_subject(result)
    assert by["in the release"].in_base_release is True
    assert by["after the release"].in_base_release is False
    assert result.summary.base_tag == "v1.0"
    assert result.summary.missing_in_base_release == 1


# -- F. Debian patches are evidence, not upstream commits ----------------------

def _report(*patches):
    return DebianPatchReport(source_package="demo", version="1.0-1",
                             release="bookworm", patches=list(patches))


def test_debian_only_patches_are_reported_apart_with_presence(world):
    applied = _edit(BASE_FILE, "line 15\n", "line 15 debian\n")
    (world["arcos"] / "lib.c").write_text(applied)
    fx.git(world["arcos"], "commit", "-q", "-am", "apply debian tweak")
    fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 2\n", "line 2 up\n"),
              "upstream change")
    tweak = ("Description: debian tweak\nOrigin: vendor\n---\n"
             "--- a/lib.c\n+++ b/lib.c\n@@ -14,3 +14,3 @@\n line 14\n-line 15\n"
             "+line 15 debian\n line 16\n")
    missing = ("Description: another\nForwarded: not-needed\n---\n"
               "--- a/other.c\n+++ b/other.c\n@@ -1 +1 @@\n-int other;\n"
               "+int other_debian;\n")
    texts = {"tweak.patch": tweak, "missing.patch": missing}
    report = _report(DebianPatch(name="tweak.patch", category="debian_specific"),
                     DebianPatch(name="missing.patch", category="debian_specific"))

    result = _service(world["root"],
                      debian_patches=lambda r: (report, texts)).compare(
        _resolution(world))
    presence = {p.name: p.presence for p in result.debian_patches.patches}
    assert presence == {"tweak.patch": Presence.PROBABLY_PRESENT,
                        "missing.patch": Presence.MISSING}
    assert {c.subject for c in result.missing_upstream} == {"upstream change"}
    assert result.summary.relevant_upstream == 1, "Debian patches are not counted"


def test_a_patch_carried_in_the_arcos_quilt_series_is_definitely_present(world):
    fx.commit(world["arcos"], "debian/patches/series", "fix.patch\n", "carry it")
    report = _report(DebianPatch(name="fix.patch", category="upstream_backport"))
    result = _service(world["root"], debian_patches=lambda r: (
        report, {"fix.patch": "--- a/x\n+++ b/x\n"})).compare(_resolution(world))
    patch = result.debian_patches.patches[0]
    assert patch.presence is Presence.DEFINITELY_PRESENT
    assert "debian/patches/series" in patch.presence_evidence[0]


# -- G. security evidence from outside the message -----------------------------

def test_a_debian_security_patch_makes_its_upstream_commit_critical(world):
    fix = fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 8\n", "line 8 safe\n"),
                    "tidy line 8")
    plain = fx.commit(world["up"], "other.c", "int other2;\n", "rename other")
    report = _report(DebianPatch(
        name="CVE-2026-2222.patch", category="security", cve_ids=["CVE-2026-2222"],
        upstream_commit=fix[:12]))
    service = _service(world["root"], security=SecurityService([DebianSecurityProvider()]),
                       debian_patches=lambda r: (report, {}))
    result = service.compare(_resolution(world))
    by = _by_subject(result)
    assert by["tidy line 8"].criticality.level is Criticality.CRITICAL
    assert "debian-patch" in by["tidy line 8"].criticality.sources
    assert "CVE-2026-2222" in by["tidy line 8"].criticality.cve_ids
    # No evidence is UNKNOWN - never "safe".
    assert by["rename other"].criticality.level is Criticality.UNKNOWN
    finding = next(f for f in result.security if f.identifier == "CVE-2026-2222")
    assert finding.status == "missing"
    del plain


class FakeOsv:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload or {}, error, []

    def post(self, url, json=None):
        import httpx

        self.calls.append(json)
        if self.error:
            raise httpx.ConnectError(self.error)
        return httpx.Response(200, json=self.payload,
                              request=httpx.Request("POST", url))


def test_an_osv_fix_commit_marks_the_missing_commit_critical(world, tmp_path):
    fix = fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 9\n", "line 9 ok\n"),
                    "quiet fix")
    payload = {"vulns": [{"id": "OSV-2026-1", "aliases": ["CVE-2026-3333"],
                          "summary": "overflow",
                          "affected": [{"ranges": [{"type": "GIT",
                                                    "repo": str(world["up"]),
                                                    "events": [{"introduced": "0"},
                                                               {"fixed": fix}]}]}]}]}
    client = FakeOsv(payload)
    osv = OsvSecurityProvider(client=client, cache_dir=tmp_path / "osv")
    result = _service(world["root"], security=SecurityService([osv])).compare(
        _resolution(world))
    commit = _by_subject(result)["quiet fix"]
    assert commit.criticality.level is Criticality.CRITICAL
    assert commit.criticality.sources == ["osv"]
    assert client.calls[0] == {"commit": result.summary.merge_bases[0]}
    assert result.security[0].status == "missing"


def test_an_unavailable_source_says_so_and_changes_nothing(world, tmp_path):
    fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 9\n", "line 9 ok\n"),
              "quiet fix")
    osv = OsvSecurityProvider(client=FakeOsv(error="no route"),
                              cache_dir=tmp_path / "osv")
    result = _service(world["root"], security=SecurityService([osv])).compare(
        _resolution(world))
    assert result.security_sources == ["osv: unavailable (ConnectError: no route)"]
    assert _by_subject(result)["quiet fix"].criticality.level is Criticality.UNKNOWN


# -- J. a moving upstream, and stale snapshots -----------------------------------

def test_an_upstream_that_moved_since_the_mapping_is_flagged_and_not_attached(
        world, tmp_path):
    first = fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 1\n", "l1\n"), "one")
    stale = _resolution(world, upstream_commit=first,
                        arcos_commit=fx.git(world["arcos"], "rev-parse", "HEAD"))
    fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 2\n", "l2\n"), "two")

    store = ComparisonStore(tmp_path / "comparisons")
    result = _service(world["root"], store=store).compare(stale)
    assert any("has moved since the mapping" in w for w in result.warnings)
    assert store.for_resolution(stale) is None, "a stale snapshot is never served"
    fresh = stale.model_copy(update={"upstream_commit": result.upstream_commit})
    snapshot = store.for_resolution(fresh)
    assert snapshot is not None and snapshot.relevant_upstream == 2


# -- H. no history, and synthesized ancestry -------------------------------------

def test_no_common_ancestor_is_an_explicit_error(world, tmp_path):
    stranger = fx.init(tmp_path / "stranger")
    fx.commit(stranger, "z", "z\n", "z")
    with pytest.raises(ComparisonError) as raised:
        _service(world["root"]).compare(
            _resolution(world, upstream_repository=Repository(url=str(stranger))))
    assert raised.value.code is ErrorCode.NO_COMMON_ANCESTOR


def test_an_unproven_resolution_is_refused_before_any_git(world):
    with pytest.raises(ComparisonError) as raised:
        _service(world["root"]).compare(
            _resolution(world, verification_level=VerificationLevel.REF_EXISTS))
    assert raised.value.code is ErrorCode.UPSTREAM_NOT_RESOLVED


def test_an_approved_content_match_gives_a_labelled_synthesized_comparison(world,
                                                                           tmp_path):
    fx.git(world["up"], "tag", "v1.0")
    fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 3\n", "line 3 up\n"),
              "after 1.0")
    imported = fx.init(tmp_path / "imported")
    (imported / "lib.c").write_text(BASE_FILE)
    (imported / "other.c").write_text("int other;\n")
    fx.git(imported, "add", ".")
    fx.git(imported, "commit", "-q", "-m", "import 1.0")
    root = fx.git(imported, "rev-parse", "HEAD")
    fx.commit(imported, "x.c", "x\n", "arcos X")
    match = ContentMatch(base_tag="v1.0",
                         base_sha=fx.git(world["up"], "rev-parse", "v1.0"),
                         arcos_tree=root, approved=True, verified_by="Somil",
                         verified_at="2026-10-05", score=1.0)
    resolution = _resolution(
        world, arcos_repository=str(imported),
        verification_level=VerificationLevel.CONTENT_MATCH_APPROVED,
        content_match=match)

    result = _service(world["root"]).compare(resolution)
    assert result.summary.synthesized_ancestry is True
    assert result.summary.has_common_ancestor is False
    assert result.summary.counts_basis.startswith("SYNTHESIZED")
    assert [c.subject for c in result.missing_upstream] == ["after 1.0"]
    assert [c.subject for c in result.arcos_only] == ["arcos X"]
    assert any("SYNTHESIZED" in w for w in result.warnings)


def test_the_presence_counts_partition_the_relevant_commits(world):
    fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 7\n", "l7\n"), "a")
    fx.commit(world["up"], "other.c", "int o2;\n", "b")
    s = _service(world["root"]).compare(_resolution(world)).summary
    assert s.relevant_upstream == (s.definitely_present + s.probably_present
                                   + s.missing + s.unknown_presence
                                   + s.reverted_upstream)
    assert "git apply --check" in s.backport_detection


def test_a_manual_comparison_never_replaces_the_mappings_snapshot(world, tmp_path):
    """Snapshots are per pair of commits: comparing against another upstream
    must not make the mapping's backlog disappear from upstream.md."""
    fx.commit(world["up"], "lib.c", _edit(BASE_FILE, "line 1\n", "l1\n"), "one")
    other = fx.clone(world["up"], tmp_path / "other-upstream")
    fx.commit(other, "lib.c", _edit(BASE_FILE, "line 2\n", "l2\n"), "elsewhere")
    store = ComparisonStore(tmp_path / "comparisons")
    service = _service(world["root"], store=store)

    mapped = service.compare(_resolution(world))
    service.compare(_resolution(world, upstream_repository=Repository(url=str(other))))

    resolution = _resolution(world, arcos_commit=mapped.arcos_commit,
                             upstream_commit=mapped.upstream_commit)
    snapshot = store.for_resolution(resolution)
    assert snapshot is not None and snapshot.upstream_commit == mapped.upstream_commit


def test_a_legacy_snapshot_file_is_still_read(world, tmp_path):
    store = ComparisonStore(tmp_path / "comparisons")
    result = _service(world["root"]).compare(_resolution(world))
    legacy = tmp_path / "comparisons" / "bookworm" / "demo.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(result.snapshot().model_dump_json())
    resolution = _resolution(world, arcos_commit=result.arcos_commit,
                             upstream_commit=result.upstream_commit)
    assert store.for_resolution(resolution) is not None
