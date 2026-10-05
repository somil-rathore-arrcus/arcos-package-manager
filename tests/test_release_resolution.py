"""Resolution, end to end on real git: the right ref for the release, and a
status that claims no more than the evidence proves.

Scenarios from the correctness brief: normal fork, multiple merge bases, wrong
repository, wrong branch, maintenance branch vs master, tarball import, content
match, curated override and conflict, git failure, stale cache, release
mismatch, Bookworm and Trixie.
"""

from __future__ import annotations

import shutil

import pytest

import gitfixtures as fx
import releaseworld as rw
from apm.domain.enums import RefStrategy, ReviewReason, VerificationLevel
from apm.gitio.ancestry import ProbeError
from apm.models import Status
from apm.upstream.strategies import Candidate, ChainResult
from apm.models import Confidence, Method, Upstream

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


@pytest.fixture
def world(tmp_path):
    return rw.build(tmp_path / "world")


def curated(w, repo=None, ref="main", **extra):
    entry = {"repository": rw.url(repo or w.upstream), "ref": ref,
             "confidence": "high", "reason": "hand-verified"}
    entry.update(extra)
    return {"demo": entry}


def resolve(w, tmp_path, monkeypatch, release="bookworm", overrides=None,
            version=None, vcs=True, settings_obj=None, arcos=None):
    sources = {release: {"demo": rw.source(w, release, version, vcs=vcs)}}
    r = rw.resolver(w, tmp_path, overrides=overrides, sources=sources,
                    settings_obj=settings_obj, monkeypatch=monkeypatch)
    return r.resolve_one(rw.package(w, release, arcos=arcos), release), r


# -- A. the release-appropriate ref ------------------------------------------

def test_bookworm_compares_against_the_maintenance_branch_not_master(
        world, tmp_path, monkeypatch):
    """Normal fork with common history, and maintenance branch vs master."""
    res, _ = resolve(world, tmp_path, monkeypatch, overrides=curated(world))

    assert res.status is Status.VERIFIED
    assert res.verification_level == VerificationLevel.SHARED_HISTORY.value
    assert res.upstream.ref == "stable-1.2"
    assert res.ref_selection.strategy is RefStrategy.MAINTENANCE_BRANCH
    assert res.ref_selection.base_tag == "v1.2.0"
    assert res.ref_selection.arcos_contains_base is True
    assert res.ref_selection.series == "1.2"
    # stable-1.2 holds F1 and F2 beyond the release; main would have said 6.
    assert res.behind == 2
    assert res.arcos_only == 1
    assert res.merge_bases == [fx.git(world.upstream, "rev-parse", "v1.2.0^{commit}")]
    main = next(c for c in res.candidates if c.ref == "main")
    assert main.behind > res.behind and main.accepted is False
    # The human's ref is kept visible in the evidence, not silently dropped.
    assert any("curated ref 'main' kept as a candidate" in e for e in res.evidence)


def test_trixie_compares_against_its_own_release_tag(world, tmp_path, monkeypatch):
    res, _ = resolve(world, tmp_path, monkeypatch, release="trixie",
                     overrides=curated(world))
    assert res.status is Status.VERIFIED
    assert res.upstream.ref == "v1.3.0" and res.upstream.is_tag
    assert res.ref_selection.strategy is RefStrategy.EXACT_TAG
    assert "no maintenance branch" in res.ref_selection.reason
    assert res.behind == 0 and res.arcos_only == 1


def test_bookworm_and_trixie_resolve_independently(world, tmp_path, monkeypatch):
    bookworm, _ = resolve(world, tmp_path / "b", monkeypatch, overrides=curated(world))
    trixie, _ = resolve(world, tmp_path / "t", monkeypatch, release="trixie",
                        overrides=curated(world))
    assert (bookworm.upstream.ref, trixie.upstream.ref) == ("stable-1.2", "v1.3.0")
    assert bookworm.arcos_commit != trixie.arcos_commit


def test_a_pinned_curated_ref_is_used_and_flagged_as_a_development_branch(
        world, tmp_path, monkeypatch):
    """Wrong branch in the correct repository, when a person insists on it."""
    res, _ = resolve(world, tmp_path, monkeypatch,
                     overrides=curated(world, pin_ref=True))
    assert res.status is Status.VERIFIED
    assert res.upstream.ref == "main"
    assert res.ref_selection.strategy is RefStrategy.CURATED
    assert any("development branch main" in w for w in res.warnings)


def test_a_curated_ref_that_does_not_exist_needs_review(world, tmp_path, monkeypatch):
    """Wrong branch in the correct repository: it does not exist at all."""
    res, _ = resolve(world, tmp_path, monkeypatch,
                     overrides=curated(world, ref="no-such-branch"))
    assert res.status is Status.NEEDS_REVIEW
    assert ReviewReason.INVALID_REF.value in res.review_reasons


# -- B. existence is not verification ----------------------------------------

def test_without_the_ancestry_probe_a_reachable_upstream_is_not_verified(
        world, tmp_path, monkeypatch):
    sources = {"bookworm": {"demo": rw.source(world)}}
    r = rw.resolver(world, tmp_path, overrides=curated(world), sources=sources,
                    monkeypatch=monkeypatch)
    r.ancestry.enabled = False
    res = r.resolve_one(rw.package(world), "bookworm")
    assert res.verification_level == VerificationLevel.REF_EXISTS.value
    assert res.status is Status.NEEDS_REVIEW
    assert ReviewReason.ANCESTRY_NOT_PROBED.value in res.review_reasons
    assert res.confidence is Confidence.MEDIUM, "a curated 'high' is not proof"


# -- C. counts from the heads, not one merge base ----------------------------

def test_criss_cross_history_counts_from_the_heads(tmp_path, monkeypatch):
    """Multiple merge bases. Counting from the first merge base would report
    ARCoS's own commit b1 as missing upstream, because upstream merged it."""
    up = fx.init(tmp_path / "up")
    fx.commit(up, "o", "o\n", "O")
    fx.git(up, "tag", "-a", "v1.0", "-m", "1.0")
    arcos = fx.clone(up, tmp_path / "arcos")
    a1 = fx.commit(up, "a1", "a1\n", "a1")
    b1 = fx.commit(arcos, "b1", "b1\n", "b1")
    fx.git(up, "fetch", "-q", str(arcos), "main:refs/remotes/arcos/main")
    fx.git(arcos, "fetch", "-q", "origin")
    fx.git(up, "merge", "-q", "--no-edit", "--no-ff", b1)
    fx.git(arcos, "merge", "-q", "--no-edit", "--no-ff", a1)
    u3 = fx.commit(up, "u3", "u3\n", "u3 upstream fix")
    fx.commit(arcos, "x3", "x3\n", "x3 arcos work")

    w = rw.build(tmp_path / "world")
    w.upstream, w.arcos = up, arcos
    res, _ = resolve(w, tmp_path / "r", monkeypatch, vcs=False, version="1.0-1",
                     overrides=curated(w, ref="main", pin_ref=True))
    assert res.status is Status.VERIFIED
    assert len(res.merge_bases) == 2
    assert res.behind == 1, "only u3 is missing; b1 is ARCoS's own commit"
    assert any("criss-cross" in e for e in res.evidence)
    del u3


# -- H. no common history -------------------------------------------------------

def _tarball_import(w, tmp_path):
    """An ARCoS repository created from the v1.2.0 tree: no shared history."""
    imported = fx.init(tmp_path / "imported")
    for name in ("A.c", "B.c"):
        (imported / name).write_text(
            fx.git(w.upstream, "show", f"v1.2.0:{name}") + "\n")
    fx.git(imported, "add", ".")
    fx.git(imported, "commit", "-q", "-m", "import demo 1.2.0 tarball")
    fx.commit(imported, "X.c", "int x;\n", "arcos local work X")
    return imported


def test_a_tarball_import_needs_review_and_reports_no_count(world, tmp_path,
                                                            monkeypatch):
    imported = _tarball_import(world, tmp_path)
    res, _ = resolve(world, tmp_path, monkeypatch, overrides=curated(world),
                     arcos=imported, vcs=False)
    assert res.status is Status.NEEDS_REVIEW
    assert ReviewReason.NO_SHARED_HISTORY.value in res.review_reasons
    assert res.status is not Status.NO_UPSTREAM, "the upstream is known"
    assert res.behind is None and res.arcos_only is None, "no count is invented"
    assert res.merge_base is None


def test_a_content_match_is_proposed_but_not_used_until_approved(
        world, tmp_path, monkeypatch):
    imported = _tarball_import(world, tmp_path)
    res, _ = resolve(world, tmp_path, monkeypatch, overrides=curated(world),
                     arcos=imported, vcs=False)
    match = res.content_match
    assert match.base_tag == "v1.2.0"
    assert match.score == 1.0 and match.files_differing == 0
    assert match.files_compared == 2, "the tag's files are counted, not assumed"
    other = next(c for c in match.candidates if c.tag == "v1.3.0")
    assert 0 < other.score < 1 and other.files_differing == 2
    assert match.arcos_tree_label == "root import commit"
    assert match.approved is False
    assert ReviewReason.CONTENT_MATCH_UNAPPROVED.value in res.review_reasons
    assert res.status is Status.NEEDS_REVIEW


def test_an_approved_content_match_verifies_with_provenance_and_synthesized_counts(
        world, tmp_path, monkeypatch):
    imported = _tarball_import(world, tmp_path)
    first, r = resolve(world, tmp_path, monkeypatch, overrides=curated(world),
                       arcos=imported, vcs=False)
    best = first.content_match
    r.approvals.approve("demo", "bookworm", {
        "repository": rw.url(world.upstream), "base_tag": best.base_tag,
        "base_sha": best.base_sha, "method": best.method, "score": best.score,
        "arcos_tree": best.arcos_tree, "verified_by": "Somil Rathore",
    })

    res = r.resolve_one(rw.package(world, arcos=imported), "bookworm")
    assert res.status is Status.VERIFIED
    assert res.verification_level == VerificationLevel.CONTENT_MATCH_APPROVED.value
    assert res.content_match.approved and res.content_match.verified_by == "Somil Rathore"
    assert res.content_match.verified_at
    assert res.upstream.ref == "stable-1.2", "the series branch containing the tag"
    assert res.behind == 2 and res.arcos_only == 1
    assert res.counts_basis.startswith("SYNTHESIZED from an approved content match")
    assert any("synthesized" in w.lower() for w in res.warnings)


# -- I. curated decisions are not silently replaced -------------------------

def test_a_curated_upstream_with_no_history_is_a_conflict_not_a_replacement(
        world, tmp_path, monkeypatch):
    """Wrong repository, curated - and the packaging repo shares history."""
    # The history lives "elsewhere": a packaging-style repository whose
    # release branch descends from the same commits as the fork.
    fx.git(world.upstream, "branch", "debian/bookworm", "stable-1.2")
    world.packaging = world.upstream
    res, _ = resolve(world, tmp_path, monkeypatch,
                     overrides=curated(world, repo=world.stranger))
    assert res.status is Status.NEEDS_REVIEW
    assert ReviewReason.CURATED_CONFLICT.value in res.review_reasons
    assert res.upstream.repository == rw.url(world.stranger), "not replaced"
    assert res.curated.conflicting_repository == rw.url(world.upstream)
    assert "was not replaced" in res.curated.conflict
    assert res.behind is None


def test_replacement_happens_only_when_configured(world, tmp_path, monkeypatch):
    fx.git(world.upstream, "branch", "debian/bookworm", "stable-1.2")
    world.packaging = world.upstream
    res, _ = resolve(world, tmp_path, monkeypatch, overrides=curated(
        world, repo=world.stranger, allow_ancestry_replacement=True))
    assert res.status is Status.VERIFIED
    assert res.upstream.repository == rw.url(world.upstream)
    assert res.curated.repository == rw.url(world.stranger), "the decision stays visible"
    assert any("replaced by" in e for e in res.evidence)


def test_metadata_pointing_at_the_wrong_repository_is_corrected_by_history(
        world, tmp_path, monkeypatch):
    """Wrong repository from DEP-12 (not curated): history may correct it."""
    fx.git(world.upstream, "branch", "debian/bookworm", "stable-1.2")
    world.packaging = world.upstream
    monkeypatch.setattr("apm.resolve.resolve_chain", lambda *a, **k: ChainResult(
        candidate=Candidate(Upstream(rw.url(world.stranger)), Method.DEP12,
                            Confidence.HIGH, "DEP-12", source="DEP-12")))
    res, _ = resolve(world, tmp_path, monkeypatch)
    assert res.status is Status.VERIFIED
    assert res.method is Method.ANCESTRY
    assert res.upstream.repository == rw.url(world.upstream)
    assert any("not " + rw.url(world.stranger) in e for e in res.evidence)


# -- L. errors are not findings ----------------------------------------------

def test_an_unreachable_upstream_is_failed_not_needs_review(world, tmp_path,
                                                             monkeypatch):
    res, _ = resolve(world, tmp_path, monkeypatch,
                     overrides=curated(world, repo=tmp_path / "nowhere"))
    assert res.status is Status.FAILED
    assert ReviewReason.NETWORK_ERROR.value in res.review_reasons
    assert res.behind is None and res.verification_level == "NONE"


def test_a_probe_failure_is_failed_and_reports_no_numbers(world, tmp_path,
                                                          monkeypatch):
    sources = {"bookworm": {"demo": rw.source(world)}}
    r = rw.resolver(world, tmp_path, overrides=curated(world), sources=sources,
                    monkeypatch=monkeypatch)

    def broken(*a, **k):
        raise ProbeError("PROBE_FAILED", "the probe was killed")

    monkeypatch.setattr(r.ancestry, "check", broken)
    res = r.resolve_one(rw.package(world), "bookworm")
    assert res.status is Status.FAILED
    assert ReviewReason.PROBE_FAILED.value in res.review_reasons
    assert res.behind is None and res.arcos_only is None and res.merge_base is None
    assert "not a finding of no shared history" in res.notes[-1]


def test_an_unfetchable_arcos_fork_is_failed(world, tmp_path, monkeypatch):
    sources = {"bookworm": {"demo": rw.source(world)}}
    r = rw.resolver(world, tmp_path, overrides=curated(world), sources=sources,
                    monkeypatch=monkeypatch)
    pkg = rw.package(world)
    pkg.arcos_repository = rw.url(tmp_path / "no-such-fork")
    res = r.resolve_one(pkg, "bookworm")
    assert res.status is Status.FAILED
    assert ReviewReason.ARCOS_UNREACHABLE.value in res.review_reasons


# -- J. the probe cache follows the commits ---------------------------------

def test_a_moved_upstream_invalidates_the_ancestry_cache(world, tmp_path, monkeypatch):
    first, r = resolve(world, tmp_path, monkeypatch, overrides=curated(world))
    assert first.behind == 2

    fx.git(world.upstream, "checkout", "-q", "stable-1.2")
    fx.commit(world.upstream, "F3.c", "int f3;\n", "fix three")
    fx.git(world.upstream, "checkout", "-q", "main")

    again, _ = resolve(world, tmp_path, monkeypatch, overrides=curated(world))
    assert again.behind == 3, "a cached answer for the old tip must not be reused"
    assert again.resolved_sha != first.resolved_sha


def test_an_unchanged_world_is_served_from_the_cache(world, tmp_path, monkeypatch):
    resolve(world, tmp_path, monkeypatch, overrides=curated(world))
    calls = []
    sources = {"bookworm": {"demo": rw.source(world)}}
    r = rw.resolver(world, tmp_path, overrides=curated(world), sources=sources,
                    monkeypatch=monkeypatch)
    real_shell = r.ancestry.transports.local.shell

    def counting(command, timeout=None):
        if "apm-ancestry-probe" in command and "cat >" not in command:
            calls.append(command)
        return real_shell(command, timeout=timeout)

    monkeypatch.setattr(r.ancestry.transports.local, "shell", counting)
    res = r.resolve_one(rw.package(world), "bookworm")
    assert res.behind == 2 and calls == []


# -- M. plausibility ---------------------------------------------------------

def test_a_fork_older_than_the_debian_release_is_flagged(world, tmp_path, monkeypatch):
    """Release/tag mismatch: ARCoS is on 1.1-era code, Debian ships 1.2.0."""
    older = fx.clone(world.upstream, tmp_path / "older")
    fx.git(older, "reset", "-q", "--hard", world.shas["A"])
    res, _ = resolve(world, tmp_path, monkeypatch, overrides=curated(world),
                     arcos=older)
    assert res.status is Status.VERIFIED, "suspicious, but not rejected"
    assert any("does not contain v1.2.0" in w for w in res.warnings)
    assert res.ref_selection.arcos_contains_base is False


def test_a_ref_from_another_series_is_a_release_mismatch(world, tmp_path, monkeypatch):
    fx.git(world.upstream, "branch", "stable-1.1", world.shas["A"])
    res, _ = resolve(world, tmp_path, monkeypatch,
                     overrides=curated(world, ref="stable-1.1", pin_ref=True))
    assert any("Release mismatch: stable-1.1 names series 1.1" in w
               for w in res.warnings)


def test_thousands_behind_a_release_ref_is_flagged(world, tmp_path, monkeypatch):
    s = rw.settings()
    s.raw["plausibility"] = {"behind_warning": 2, "arcos_only_warning": 1}
    res, _ = resolve(world, tmp_path, monkeypatch, overrides=curated(world),
                     settings_obj=s)
    assert res.status is Status.VERIFIED
    assert any("unusually many for a maintenance branch" in w for w in res.warnings)
    assert any("ARCoS commits are not in" in w for w in res.warnings)


# -- packaging lineage ----------------------------------------------------------

def test_no_metadata_but_a_packaging_fork_is_verified_through_its_release_branch(
        tmp_path, monkeypatch):
    """A fork of the Debian packaging repo: the release branch, never sid."""
    w = rw.build(tmp_path / "world")
    pkg = w.packaging
    fx.git(pkg, "checkout", "-q", "debian/bookworm")
    fx.commit(pkg, "debian/changelog", "demo (1.2.0-1) bookworm\n", "bookworm upload")
    fx.git(pkg, "tag", "-a", "debian/1.2.0-1", "-m", "1.2.0-1")
    fx.git(pkg, "checkout", "-q", "debian/sid")
    fx.commit(pkg, "debian/changelog", "demo (1.4.0-1) unstable\n", "sid upload")
    fx.git(pkg, "checkout", "-q", "main")
    arcos = fx.clone(pkg, tmp_path / "arcos-pkg", branch="debian/bookworm")
    fx.commit(arcos, "debian/arcos", "x\n", "arcos packaging tweak")
    monkeypatch.setattr("apm.resolve.resolve_chain", lambda *a, **k: ChainResult(
        candidate=None))
    sources = {"bookworm": {"demo": rw.source(w)}}
    r = rw.resolver(w, tmp_path, sources=sources, monkeypatch=monkeypatch)
    pkg_def = rw.package(w, arcos=arcos)
    res = r.resolve_one(pkg_def, "bookworm")
    assert res.status is Status.VERIFIED
    assert res.upstream.ref == "debian/bookworm"
    assert res.ref_selection.base_tag == "debian/1.2.0-1"
    assert ReviewReason.NO_CANDIDATE.value not in res.review_reasons
    sid = next(c for c in res.candidates if c.ref == "debian/sid")
    assert sid.accepted is False


def test_a_fork_of_another_series_is_found_by_widening_the_search(
        world, tmp_path, monkeypatch):
    """Debian ships 1.2.0, but ARCoS imported 2.0.0 (as ARCoS's babeltrace is
    2.x while Debian's babeltrace is 1.5): no tag of the 1.x line matches, so
    every release tag is compared and 2.0.0 is proposed."""
    up = world.upstream
    fx.git(up, "checkout", "-q", "-b", "two", "v1.3.0")
    for name in ("A.c", "B.c", "C.c", "D.c"):
        (up / name).write_text(f"/* rewritten for 2.0 */ int {name[0].lower()}2;\n")
    fx.git(up, "commit", "-q", "-am", "2.0 rewrite")
    fx.git(up, "tag", "-a", "v2.0.0", "-m", "2.0.0")
    fx.git(up, "checkout", "-q", "main")
    imported = fx.init(tmp_path / "imported20")
    for name in ("A.c", "B.c", "C.c", "D.c"):
        (imported / name).write_text(fx.git(up, "show", f"v2.0.0:{name}") + "\n")
    fx.git(imported, "add", ".")
    fx.git(imported, "commit", "-q", "-m", "import 2.0.0")

    res, _ = resolve(world, tmp_path, monkeypatch, overrides=curated(world),
                     arcos=imported, vcs=False)
    assert res.content_match.base_tag == "v2.0.0"
    assert res.content_match.score == 1.0
    assert any("release tags were compared" in n for n in res.content_match.notes)
    assert ReviewReason.CONTENT_MATCH_UNAPPROVED.value in res.review_reasons


def test_a_weak_content_match_is_not_proposed(world, tmp_path, monkeypatch):
    unrelated = fx.init(tmp_path / "unrelated")
    for i in range(8):
        fx.commit(unrelated, f"f{i}.py", f"x = {i}\n", f"file {i}")
    res, _ = resolve(world, tmp_path, monkeypatch, overrides=curated(world),
                     arcos=unrelated, vcs=False)
    assert ReviewReason.NO_SHARED_HISTORY.value in res.review_reasons
    assert ReviewReason.CONTENT_MATCH_UNAPPROVED.value not in res.review_reasons
    assert any("No upstream release matches" in n for n in res.notes)
