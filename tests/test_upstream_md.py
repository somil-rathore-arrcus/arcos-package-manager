"""debian/upstream.md generation.

The file is two fields and nothing else, and every value in it is a recorded
fact: a branch the mapping verified, or an N/A sentence that says exactly what
is missing. A tag is never written up as a branch and an unverified candidate is
never written at all - those are the two ways a plausible-looking file would be
wrong, and most of these tests are about them. A tag-only mapping names a branch
only where the probe measured the default branch as holding the fork's base.

Determinism matters because the outcome is decided by comparing the render with
what is already committed; if the render wobbled, every run would look like a
change.
"""

import importlib.util
import re
from pathlib import Path

import pytest

from apm.domain.enums import (
    RefStrategy, ResolutionMethod, ResolutionMode, ResolutionStatus,
    UpstreamMdOutcome,
)
from apm.domain.models import (
    ComparisonSnapshot, PackageSource, RefSelection, Repository,
    ResolutionEvidence, UpstreamCandidate, UpstreamResolution,
)
from apm.services.upstream_md_publisher import lint_content
from apm.services.upstream_md_service import (
    BASE_TAG_NOT_IN_FORK, BRANCH_FROM_TAG, BRANCH_LABEL, BRANCH_NOT_VERIFIED,
    FALLBACK_REF,
    MAPPING_WARNINGS, MARKER, SOURCE_LABEL, TAG_ONLY, BranchField, ReviewFlag,
    UpstreamMdService, branch_field, classify, parse_fields, review_flags,
    source_field,
)

SERVICE = UpstreamMdService()
URL = "https://github.com/mstpd/mstpd.git"


def _resolution(**overrides) -> UpstreamResolution:
    """mstpd as the bookworm mapping has it: a verified branch."""
    base = dict(
        package="mstpd", debian_release="bookworm",
        status=ResolutionStatus.VERIFIED, mode=ResolutionMode.AUTO,
        method=ResolutionMethod.DEP12, confidence="high",
        github_repository="Arrcus/mstpd", arcos_branch="aminor",
        arcos_commit="3b043f16bb8e",
        debian=PackageSource(
            source_package="mstpd", debian_release="bookworm",
            version="0.0.8-2", suite="bookworm",
            vcs_git="https://salsa.debian.org/debian/mstpd.git",
        ),
        upstream_repository=Repository(url=URL),
        upstream_ref="master", upstream_branch="master",
        upstream_commit="074aa3d33999",
        origin_kind="project", merge_base="984ad177f02d", behind=126, arcos_only=25,
        evidence=[ResolutionEvidence(kind="dep12", detail="DEP-12 Repository field")],
    )
    base.update(overrides)
    return UpstreamResolution(**base)


def _tag_only(tag="v6.1", contains=True, **overrides) -> UpstreamResolution:
    """ethtool's shape: the release resolved to a tag, and no branch is known."""
    values = dict(
        package="ethtool",
        upstream_repository=Repository(
            url="https://git.kernel.org/pub/scm/network/ethtool/ethtool.git"),
        upstream_ref=tag, upstream_branch=None, upstream_tag=tag,
        ref_selection=RefSelection(
            ref=tag, kind="tag", strategy=RefStrategy.EXACT_TAG,
            reason=f"{tag} is the tag for the shipped Debian version",
            debian_upstream_version="6.1", base_tag=tag,
            arcos_contains_base=contains,
        ),
    )
    values.update(overrides)
    return _resolution(**values)


ETHTOOL = "https://git.kernel.org/pub/scm/network/ethtool/ethtool.git"


def _measured(ref, kind, strategy, arcos_only, repository=ETHTOOL,
              shares=True) -> UpstreamCandidate:
    return UpstreamCandidate(
        repository=repository, ref=ref, source=ResolutionMethod.ANCESTRY,
        accepted=kind == "tag", shares_history=shares, strategy=strategy,
        kind=kind, arcos_only=arcos_only)


def _probed(tag_outside=116, branch_outside=14, tag="v6.1", branch="master",
            extra=(), **overrides) -> UpstreamResolution:
    """ethtool as the probe measured it: v6.1 selected, master measured too.

    The fork has 116 commits outside v6.1 and 14 outside master - it sits on
    master, well past the release Debian ships.
    """
    candidates = [
        _measured(tag, "tag", RefStrategy.EXACT_TAG, tag_outside),
        _measured(branch, "branch", RefStrategy.DEFAULT_BRANCH, branch_outside),
        *extra,
    ]
    return _tag_only(tag, candidates=candidates, **overrides)


def _ifupdown2() -> UpstreamResolution:
    """A branch, but reached by falling back, with a warning recorded."""
    return _resolution(
        package="ifupdown2",
        upstream_repository=Repository(url="https://github.com/cumulusnetworks/ifupdown2.git"),
        ref_selection=RefSelection(
            ref="master", kind="branch", strategy=RefStrategy.DEFAULT_BRANCH,
            reason="no tag for 3.0.0 upstream; fell back to the default branch",
            debian_upstream_version="3.0.0", is_fallback=True,
        ),
        warnings=["upstream has no tag for the shipped version"],
    )


# Every shape the renderer has a rule for, so the format invariants below are
# checked against all of them rather than only the happy path.
VARIANTS = {
    "branch": _resolution(),
    "tag only": _tag_only(),
    "tag only, from the ref selection": _tag_only(upstream_tag=None),
    "tag not in the fork": _tag_only("release_2_0_0", contains=False,
                                     package="libpam-radius-auth"),
    "branch from the tag's probe": _probed(),
    "branch from the probe, tag not in the fork": _probed(
        68, 68, "release_2_0_0", contains=False, package="libpam-radius-auth"),
    "branch from the ref selection": _resolution(
        upstream_branch=None,
        ref_selection=RefSelection(ref="master", kind="branch",
                                   strategy=RefStrategy.CURATED)),
    "ref of unknown kind": _resolution(upstream_branch=None, upstream_ref="abc123"),
    "nothing recorded": _resolution(upstream_branch=None, upstream_ref=None),
    "no repository": _resolution(upstream_repository=None),
    "fallback with warnings": _ifupdown2(),
    "needs review": _resolution(status=ResolutionStatus.NEEDS_REVIEW,
                                reason="curated upstream shares no history"),
    "failed": _resolution(status=ResolutionStatus.FAILED),
}


def _codes(resolution):
    return [flag.code for flag in review_flags(resolution)]


# -- the file -----------------------------------------------------------------

def test_a_branch_package_is_exactly_two_fields():
    assert SERVICE.render(_resolution()) == (
        "Upstream Source: https://github.com/mstpd/mstpd.git\n"
        "\n"
        "Current Upstream Branch: master\n"
    )
    assert SOURCE_LABEL == "Upstream Source"
    assert BRANCH_LABEL == "Current Upstream Branch"


def test_a_verified_branch_raises_no_flag():
    assert branch_field(_resolution()) == BranchField(
        value="master", branch="master", verified=True)
    assert review_flags(_resolution()) == []


def test_nothing_but_the_two_fields_reaches_the_file():
    """The evidence, counts and backlog belong to the PR body, not the file."""
    detailed = _resolution(
        comparison=ComparisonSnapshot(
            arcos_commit="a" * 40, upstream_repository=URL, upstream_ref="master",
            upstream_commit="b" * 40, relevant_upstream=12, missing=6),
        evidence_source="debian/upstream/metadata", evidence_url=URL,
        verification="git merge-base", reason="anything",
    )
    assert SERVICE.render(detailed) == SERVICE.render(_resolution())
    text = SERVICE.render(detailed)
    for absent in ("#", "<!--", MARKER, "Evidence", "Backlog", "126", "aminor",
                   "salsa"):
        assert absent not in text


def test_a_tag_is_named_but_never_written_up_as_a_branch():
    field = branch_field(_tag_only())
    assert field == BranchField(
        value="N/A - no upstream branch verified; upstream tag v6.1",
        tag="v6.1", verified=False)
    assert SERVICE.render(_tag_only()).endswith(
        "Current Upstream Branch: N/A - no upstream branch verified; "
        "upstream tag v6.1\n"
    )


def test_a_tag_known_only_from_the_ref_selection_is_still_a_tag():
    """A row from an older mapping can carry the kind only in the selection."""
    field = branch_field(_tag_only("v6.1.0", upstream_tag=None, upstream_ref="v6.1.0"))
    assert field.value == "N/A - no upstream branch verified; upstream tag v6.1.0"
    assert field.tag == "v6.1.0" and field.branch is None and not field.verified


def test_a_tag_the_fork_does_not_contain_says_so():
    resolution = _tag_only("release_2_0_0", contains=False,
                           package="libpam-radius-auth")
    assert branch_field(resolution).value == (
        "N/A - no upstream branch verified; upstream tag release_2_0_0 "
        "(the ARCoS fork does not contain this tag)"
    )
    assert _codes(resolution) == [BRANCH_NOT_VERIFIED, TAG_ONLY,
                                  BASE_TAG_NOT_IN_FORK]


def test_the_fork_note_is_only_for_the_tag_actually_named():
    """A different base tag missing from the fork is a flag, not a claim about
    the tag the file names."""
    resolution = _tag_only("v6.1", contains=False)
    resolution.ref_selection.base_tag = "v6.0"
    assert branch_field(resolution).value == (
        "N/A - no upstream branch verified; upstream tag v6.1"
    )
    assert BASE_TAG_NOT_IN_FORK in _codes(resolution)


def test_a_tag_only_fork_on_the_default_branch_names_that_branch():
    """ethtool: the fork has fewer commits outside master than outside v6.1."""
    field = branch_field(_probed())
    assert (field.value, field.branch, field.tag, field.verified) == (
        "master", "master", "v6.1", True)
    assert "14 commit(s) outside master against 116 outside v6.1" in field.basis
    assert SERVICE.render(_probed()).endswith("Current Upstream Branch: master\n")


def test_a_branch_from_a_tag_is_flagged_not_left_unverified():
    flags = review_flags(_probed())
    assert [f.code for f in flags] == [BRANCH_FROM_TAG]
    detail = flags[0].detail
    assert detail.startswith("the mapping selected upstream tag v6.1, not a "
                             "branch; master is written because the ancestry "
                             "probe measured master")
    assert detail.endswith("; v6.1 is the tag for the shipped Debian version")


def test_equal_counts_mean_the_fork_is_at_the_tag_on_the_branch():
    """libnl3: nothing past libnl3_7_0, and libnl3_7_0 is on main."""
    resolution = _probed(79, 79, "libnl3_7_0", "main", package="libnl3")
    assert branch_field(resolution).value == "main"


def test_a_fork_with_commits_the_default_branch_lacks_keeps_the_tag():
    """A fork taken from a side branch has more outside master than outside
    its tag; naming master would be a guess."""
    resolution = _probed(tag_outside=20, branch_outside=21)
    assert branch_field(resolution).value == (
        "N/A - no upstream branch verified; upstream tag v6.1")
    assert _codes(resolution) == [BRANCH_NOT_VERIFIED, TAG_ONLY]


@pytest.mark.parametrize("candidates", [
    # Nothing measured beyond the tag.
    [_measured("v6.1", "tag", RefStrategy.EXACT_TAG, 116)],
    # The default branch shares no history with the fork.
    [_measured("v6.1", "tag", RefStrategy.EXACT_TAG, 116),
     _measured("master", "branch", RefStrategy.DEFAULT_BRANCH, None, shares=False)],
    # Measured, but the count is missing.
    [_measured("v6.1", "tag", RefStrategy.EXACT_TAG, 116),
     _measured("master", "branch", RefStrategy.DEFAULT_BRANCH, None)],
    # No count for the tag to compare with.
    [_measured("v6.1", "tag", RefStrategy.EXACT_TAG, None),
     _measured("master", "branch", RefStrategy.DEFAULT_BRANCH, 14)],
    # The placeholder used when the remote's HEAD could not be listed.
    [_measured("v6.1", "tag", RefStrategy.EXACT_TAG, 116),
     _measured("HEAD", "branch", RefStrategy.DEFAULT_BRANCH, 14)],
    # The Debian packaging repository's branch is not the upstream's.
    [_measured("v6.1", "tag", RefStrategy.EXACT_TAG, 116),
     _measured("debian/latest", "branch", RefStrategy.PACKAGING_FALLBACK, 14,
               repository="https://salsa.debian.org/kernel-team/ethtool.git")],
    # Another repository's default branch.
    [_measured("v6.1", "tag", RefStrategy.EXACT_TAG, 116),
     _measured("master", "branch", RefStrategy.DEFAULT_BRANCH, 14,
               repository="https://github.com/other/ethtool.git")],
    # A non-default branch of the upstream: not what the rule measures.
    [_measured("v6.1", "tag", RefStrategy.EXACT_TAG, 116),
     _measured("v6.1-fixes", "branch", RefStrategy.MAINTENANCE_BRANCH, 14)],
    # The default branch measured, but against a different tag.
    [_measured("v6.0", "tag", RefStrategy.EXACT_TAG, 116),
     _measured("master", "branch", RefStrategy.DEFAULT_BRANCH, 14)],
])
def test_without_a_measurement_of_the_default_branch_the_tag_stays_n_a(candidates):
    resolution = _tag_only(candidates=candidates)
    assert branch_field(resolution).value == (
        "N/A - no upstream branch verified; upstream tag v6.1")
    assert BRANCH_FROM_TAG not in _codes(resolution)


def _netfilterqueue(branch_outside=14, extra=()):
    """python-netfilterqueue on trixie: v1.1.0 selected, and master measured -
    but recorded as the curated ref, because overrides.yaml names master and it
    is also the repository's default branch."""
    url = "https://github.com/oremanj/python-netfilterqueue.git"
    return _tag_only(
        "v1.1.0", package="python-netfilterqueue",
        upstream_repository=Repository(url=url),
        candidates=[
            _measured("v1.1.0", "tag", RefStrategy.EXACT_TAG, 14, repository=url),
            _measured("master", "branch", RefStrategy.CURATED, branch_outside,
                      repository=url),
            *extra,
        ])


def test_a_branch_overrides_yaml_names_counts_when_measured():
    field = branch_field(_netfilterqueue())
    assert (field.value, field.tag, field.verified) == ("master", "v1.1.0", True)
    assert "master, the branch config/overrides.yaml names" in field.basis
    assert _codes(_netfilterqueue()) == [BRANCH_FROM_TAG]


def test_a_named_branch_with_more_outside_than_the_tag_keeps_the_tag():
    assert branch_field(_netfilterqueue(branch_outside=15)).value == (
        "N/A - no upstream branch verified; upstream tag v1.1.0")


@pytest.mark.parametrize("default_outside,named_outside,want", [
    (10, 14, "main"),     # the default branch is closer to the fork
    (14, 10, "master"),   # the named branch is closer
    (14, 14, "master"),   # a tie goes to the branch a person named
])
def test_of_two_qualifying_branches_the_closer_then_the_named_wins(
        default_outside, named_outside, want):
    url = "https://github.com/oremanj/python-netfilterqueue.git"
    resolution = _netfilterqueue(
        branch_outside=named_outside,
        extra=[_measured("main", "branch", RefStrategy.DEFAULT_BRANCH,
                         default_outside, repository=url)])
    assert branch_field(resolution).value == want
    row = _tag_row("v1.1.0", **{"Upstream Repository": url})
    value = parse_fields(SERVICE.render(resolution))
    assert VERIFY.field_problems("python-netfilterqueue", row, value,
                                 _as_json(resolution)) == []


def test_a_branch_from_the_probe_still_reports_the_missing_base_tag():
    """libpam-radius-auth: based before release_2_0_0, on master."""
    resolution = VARIANTS["branch from the probe, tag not in the fork"]
    assert branch_field(resolution).value == "master"
    assert _codes(resolution) == [BRANCH_FROM_TAG, BASE_TAG_NOT_IN_FORK]


def test_an_unverified_mapping_never_shows_the_probed_branch():
    resolution = _probed(status=ResolutionStatus.NEEDS_REVIEW)
    assert branch_field(resolution).value == (
        "N/A - not verified (status NEEDS_REVIEW)")


def test_a_branch_recorded_only_by_the_ref_selection_is_used():
    resolution = VARIANTS["branch from the ref selection"]
    assert branch_field(resolution) == BranchField(
        value="master", branch="master", verified=True)
    assert BRANCH_NOT_VERIFIED not in _codes(resolution)


def test_a_ref_of_unknown_kind_is_reported_not_assumed_to_be_a_branch():
    resolution = VARIANTS["ref of unknown kind"]
    assert branch_field(resolution) == BranchField(
        value="N/A - no upstream branch verified; ref abc123 is not recorded "
              "as a branch or a tag")
    flags = review_flags(resolution)
    assert [f.code for f in flags] == [BRANCH_NOT_VERIFIED]
    assert "abc123" in flags[0].detail and "branch or a tag" in flags[0].detail


def test_no_ref_at_all_is_plain_n_a():
    resolution = VARIANTS["nothing recorded"]
    assert branch_field(resolution).value == "N/A - no upstream branch verified"
    assert review_flags(resolution) == [
        ReviewFlag(BRANCH_NOT_VERIFIED, "the mapping records no upstream ref")]


def test_a_verified_mapping_without_a_repository_is_n_a():
    assert source_field(VARIANTS["no repository"]) == "N/A"
    assert SERVICE.render(VARIANTS["no repository"]).startswith(
        "Upstream Source: N/A\n")


@pytest.mark.parametrize("status", [ResolutionStatus.NEEDS_REVIEW,
                                    ResolutionStatus.FAILED,
                                    ResolutionStatus.PARTIAL])
def test_an_unverified_mapping_never_shows_its_candidate(status):
    """--include-needs-review writes a file; it must not name the candidate."""
    resolution = _resolution(status=status, reason="no shared history")
    text = SERVICE.render(resolution)
    unverified = f"N/A - not verified (status {status.value})"
    assert text == (f"Upstream Source: {unverified}\n\n"
                    f"Current Upstream Branch: {unverified}\n")
    assert "github.com" not in text and "master" not in text
    flags = review_flags(resolution)
    assert flags[0].code == BRANCH_NOT_VERIFIED
    assert status.value in flags[0].detail


def test_a_value_cannot_add_a_line_to_the_file():
    text = SERVICE.render(_resolution(upstream_branch=" master \n"))
    assert text == SERVICE.render(_resolution())


# -- review flags ---------------------------------------------------------------

def test_a_tag_only_package_is_flagged_with_the_tag_and_the_reason():
    flags = review_flags(_tag_only())
    assert [f.code for f in flags] == [BRANCH_NOT_VERIFIED, TAG_ONLY]
    assert "v6.1" in flags[0].detail and "not a branch" in flags[0].detail
    assert flags[1].detail == (
        "upstream tag v6.1 is the only ref recorded; v6.1 is the tag for the "
        "shipped Debian version"
    )


def test_the_generated_document_carries_the_flags_the_dashboard_shows():
    """The dashboard reads the flags from the document, so they must be the
    plan's flags, not a second opinion - and a clean package has none."""
    document = SERVICE.generate(_tag_only())
    assert [(f.code, f.detail) for f in document.review_flags] == [
        (f.code, f.detail) for f in review_flags(_tag_only())
    ]
    assert SERVICE.generate(_resolution()).review_flags == []


def test_the_missing_base_tag_names_debians_version():
    flag = review_flags(VARIANTS["tag not in the fork"])[-1]
    assert flag.code == BASE_TAG_NOT_IN_FORK
    assert "release_2_0_0" in flag.detail and "6.1" in flag.detail


def test_a_fallback_branch_is_written_but_flagged():
    resolution = _ifupdown2()
    assert branch_field(resolution).value == "master"
    assert branch_field(resolution).verified
    assert review_flags(resolution) == [
        ReviewFlag(FALLBACK_REF,
                   "no tag for 3.0.0 upstream; fell back to the default branch"),
        ReviewFlag(MAPPING_WARNINGS, "upstream has no tag for the shipped version"),
    ]


def test_mapping_warnings_are_joined_into_one_flag():
    flags = review_flags(_resolution(warnings=["first", "  ", "second"]))
    assert flags == [ReviewFlag(MAPPING_WARNINGS, "first; second")]


def test_a_branch_from_a_tag_takes_the_tag_flags_place_in_the_order():
    resolution = _probed(contains=False, warnings=["w"])
    resolution.ref_selection.is_fallback = True
    assert _codes(resolution) == [BRANCH_FROM_TAG, BASE_TAG_NOT_IN_FORK,
                                  FALLBACK_REF, MAPPING_WARNINGS]


def test_flags_come_in_a_fixed_order():
    resolution = _tag_only("v1", contains=False, warnings=["w"])
    resolution.ref_selection.is_fallback = True
    assert _codes(resolution) == [BRANCH_NOT_VERIFIED, TAG_ONLY,
                                  BASE_TAG_NOT_IN_FORK, FALLBACK_REF,
                                  MAPPING_WARNINGS]
    assert review_flags(resolution) == review_flags(resolution)


def test_a_flag_does_not_change_the_file():
    """Flags are for the reviewer; the file records only what was verified."""
    assert SERVICE.render(_ifupdown2()).endswith(
        "Current Upstream Branch: master\n")


# -- the format, on every variant -----------------------------------------------

@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_variant_passes_the_commit_lint(name):
    assert lint_content(SERVICE.render(VARIANTS[name])) is None


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_variant_parses_back_to_its_fields(name):
    resolution = VARIANTS[name]
    assert parse_fields(SERVICE.render(resolution)) == {
        SOURCE_LABEL: source_field(resolution),
        BRANCH_LABEL: branch_field(resolution).value,
    }


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_variant_is_deterministic_and_has_no_timestamp_or_path(name):
    text = SERVICE.render(VARIANTS[name])
    assert text == SERVICE.render(VARIANTS[name].model_copy(deep=True))
    assert "/Users/" not in text and "/tmp/" not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:", text)
    assert len(text.splitlines()) == 3


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_only_n_a_values_are_unverified(name):
    """BRANCH_NOT_VERIFIED is raised exactly when the branch field is N/A."""
    resolution = VARIANTS[name]
    unverified = branch_field(resolution).value.startswith("N/A")
    assert (BRANCH_NOT_VERIFIED in _codes(resolution)) is unverified
    assert branch_field(resolution).verified is not unverified


# -- reading a file -------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Upstream Source: https://x/y.git\n\nCurrent Upstream Branch: main\n",
    # Lakshya's own spelling, a space before the colon.
    "Upstream Source : https://x/y.git\nCurrent Upstream Branch : main\n",
    "Upstream Source: https://x/y.git  \r\n\r\nCurrent Upstream Branch:main\r\n\r\n",
    "\nCurrent Upstream Branch: main\nUpstream Source: https://x/y.git",
])
def test_both_colon_spellings_and_line_endings_parse(text):
    assert parse_fields(text) == {
        "Upstream Source": "https://x/y.git", "Current Upstream Branch": "main"}


@pytest.mark.parametrize("text", [
    "Upstream Source: a\n\nCurrent Upstream Branch: b\n\nNotes: hand written\n",
    "# Upstream\n\nUpstream Source: a\nCurrent Upstream Branch: b\n",
    "Upstream Source: a\nUpstream Source: a\nCurrent Upstream Branch: b\n",
    "Upstream Source: a\n",
    "Current Upstream Branch: b\n",
    "  Upstream Source: a\nCurrent Upstream Branch: b\n",
    "upstream source: a\nCurrent Upstream Branch: b\n",
    f"<!-- {MARKER}. -->\n\nUpstream Source: a\nCurrent Upstream Branch: b\n",
    "",
])
def test_anything_else_is_not_the_two_field_format(text):
    assert parse_fields(text) is None


def test_no_file_parses_to_nothing():
    assert parse_fields(None) is None


# -- what writing it would do ---------------------------------------------------

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


def test_an_older_two_field_file_is_updated_with_a_diff():
    stale = SERVICE.render(_resolution(upstream_branch="old-branch"))
    document = SERVICE.generate(_resolution(), existing=stale)
    assert document.outcome is UpstreamMdOutcome.UPDATED
    assert "-Current Upstream Branch: old-branch" in document.diff
    assert "+Current Upstream Branch: master" in document.diff


def test_a_hand_written_two_field_file_is_ours_to_update():
    """It holds nothing the generated file would lose, so it is not a conflict."""
    by_hand = ("Upstream Source : https://github.com/mstpd/mstpd\n"
               "Current Upstream Branch : main\n")
    outcome, diff = classify(by_hand, SERVICE.render(_resolution()))
    assert outcome is UpstreamMdOutcome.UPDATED
    assert "+Current Upstream Branch: master" in diff


def test_a_file_from_an_older_version_of_the_tool_is_updated():
    legacy = (f"<!-- {MARKER}. Edits are preserved. -->\n\n# Upstream Information\n"
              f"\n## Upstream\n\n- Repository: {URL}\n- Branch: master\n")
    document = SERVICE.generate(_resolution(), existing=legacy)
    assert document.outcome is UpstreamMdOutcome.UPDATED
    assert "-# Upstream Information" in document.diff


@pytest.mark.parametrize("existing", [
    "# mstpd\n\nNotes written by hand.\n",
    "Upstream Source: a\n\nCurrent Upstream Branch: b\n\nSee the wiki.\n",
])
def test_any_other_file_is_a_conflict_not_an_overwrite(existing):
    """Someone else's file may hold things this tool does not know."""
    document = SERVICE.generate(_resolution(), existing=existing)
    assert document.outcome is UpstreamMdOutcome.CONFLICT
    assert document.existing_content == existing
    assert document.diff


def test_no_upstream_package_is_refused():
    with pytest.raises(ValueError):
        SERVICE.generate(_resolution(status=ResolutionStatus.NO_UPSTREAM))


# -- scripts/verify_upstream_md.py ------------------------------------------------

def _verifier():
    """The script stays standalone (no apm import), so load it by path."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_upstream_md.py"
    spec = importlib.util.spec_from_file_location("verify_upstream_md", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = _verifier()


def _row(**cells):
    row = {"Resolution Status": "VERIFIED", "Upstream Repository": URL,
           "Upstream Branch": "master", "Upstream Tag": ""}
    row.update(cells)
    return row


def test_the_verifier_reads_files_the_way_the_service_does():
    samples = [SERVICE.render(v) for v in VARIANTS.values()] + [
        "Upstream Source : a\nCurrent Upstream Branch : b\n",
        "Upstream Source: a\n\nCurrent Upstream Branch: b\n\nNotes: x\n",
        "Upstream Source: a\nUpstream Source: a\nCurrent Upstream Branch: b\n",
    ]
    for text in samples:
        assert VERIFY.parse_fields(text) == parse_fields(text), text
    assert VERIFY.NO_BRANCH == branch_field(VARIANTS["nothing recorded"]).value


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_the_verifier_accepts_every_generated_layout(name):
    assert VERIFY.shape_problem(name, SERVICE.render(VARIANTS[name])) is None


def test_the_verifier_rejects_extra_lines_and_a_different_layout():
    assert "two fields" in VERIFY.shape_problem(
        "p", "Upstream Source: a\n\nCurrent Upstream Branch: b\n\nmore\n")
    assert "laid out" in VERIFY.shape_problem(
        "p", "Upstream Source : a\nCurrent Upstream Branch : b\n")


def _tag_row(tag="v6.1", contains="yes", **cells):
    """A tag-only row as the release CSV writes it, evidence columns included."""
    return _row(**{"Upstream Repository": ETHTOOL, "Upstream Branch": "",
                   "Upstream Tag": tag, "Upstream Base Tag": tag,
                   "ARCoS Contains Base Tag": contains, **cells})


@pytest.mark.parametrize("resolution,row", [
    (_resolution(), _row()),
    (_tag_only(), _row(**{
        "Upstream Repository": ETHTOOL,
        "Upstream Branch": "", "Upstream Tag": "v6.1"})),
    (_tag_only(), _tag_row()),
    (VARIANTS["tag not in the fork"], _tag_row(
        "release_2_0_0", "no", **{"Upstream Repository": ETHTOOL})),
    # A CSV written before the evidence columns cannot say; either is accepted.
    (VARIANTS["tag not in the fork"], _row(**{
        "Upstream Repository": ETHTOOL, "Upstream Branch": "",
        "Upstream Tag": "release_2_0_0"})),
    (VARIANTS["ref of unknown kind"], _row(**{"Upstream Branch": ""})),
    (VARIANTS["nothing recorded"], _row(**{"Upstream Branch": ""})),
    (VARIANTS["no repository"], _row(**{"Upstream Repository": ""})),
    (VARIANTS["needs review"], _row(**{"Resolution Status": "NEEDS_REVIEW"})),
])
def test_the_verifier_agrees_with_the_mapping_row(resolution, row):
    value = parse_fields(SERVICE.render(resolution))
    assert VERIFY.field_problems(resolution.package, row, value) == []


def _as_json(resolution):
    """The candidates as upstream-resolutions.json stores them."""
    return [c.model_dump(mode="json") for c in resolution.candidates]


@pytest.mark.parametrize("resolution,row", [
    (_probed(), _tag_row()),
    (VARIANTS["branch from the probe, tag not in the fork"],
     _tag_row("release_2_0_0", "no")),
    (_probed(79, 79, "libnl3_7_0", "main"), _tag_row("libnl3_7_0")),
    (_netfilterqueue(), _tag_row("v1.1.0", **{
        "Upstream Repository": "https://github.com/oremanj/python-netfilterqueue.git"})),
    # Measured, but not qualifying: the N/A form is still what is required.
    (_probed(20, 21), _tag_row()),
])
def test_the_verifier_re_derives_a_branch_from_the_probe(resolution, row):
    value = parse_fields(SERVICE.render(resolution))
    assert VERIFY.field_problems(resolution.package, row, value,
                                 _as_json(resolution)) == []


def test_the_verifier_rejects_a_derived_branch_it_cannot_re_derive():
    value = parse_fields(SERVICE.render(_probed()))
    # No measurements: the CSV alone supports only the N/A form.
    assert VERIFY.field_problems("ethtool", _tag_row(), value)
    # Measurements that do not qualify.
    assert VERIFY.field_problems("ethtool", _tag_row(), value,
                                 _as_json(_probed(20, 21)))
    # A different branch from the one measured.
    other = {SOURCE_LABEL: ETHTOOL, BRANCH_LABEL: "main"}
    assert VERIFY.field_problems("ethtool", _tag_row(), other,
                                 _as_json(_probed()))


def test_the_verifier_rejects_n_a_where_the_probe_supports_the_branch():
    """The renderer always writes the branch then; N/A means it did not."""
    value = parse_fields(SERVICE.render(_tag_only()))
    assert VERIFY.field_problems("ethtool", _tag_row(), value,
                                 _as_json(_probed()))


@pytest.mark.parametrize("value,row", [
    # A branch the mapping does not record.
    ({SOURCE_LABEL: URL, BRANCH_LABEL: "main"}, _row()),
    # A tag passed off as a branch.
    ({SOURCE_LABEL: URL, BRANCH_LABEL: "v6.1"},
     _row(**{"Upstream Branch": "", "Upstream Tag": "v6.1"})),
    # A guess where the mapping records nothing.
    ({SOURCE_LABEL: URL, BRANCH_LABEL: "master"}, _row(**{"Upstream Branch": ""})),
    # A different upstream.
    ({SOURCE_LABEL: "https://github.com/other/mstpd.git", BRANCH_LABEL: "master"},
     _row()),
    # The candidate of an unverified mapping.
    ({SOURCE_LABEL: URL, BRANCH_LABEL: "master"},
     _row(**{"Resolution Status": "NEEDS_REVIEW"})),
    # Another package's tag, which starts with this one's.
    ({SOURCE_LABEL: ETHTOOL,
      BRANCH_LABEL: "N/A - no upstream branch verified; upstream tag v6.1.0"},
     _tag_row()),
    # A fork note the row contradicts: it says the fork has the tag.
    ({SOURCE_LABEL: ETHTOOL,
      BRANCH_LABEL: "N/A - no upstream branch verified; upstream tag v6.1 "
                    "(the ARCoS fork does not contain this tag)"},
     _tag_row()),
    # The fork note missing where the row calls for it.
    ({SOURCE_LABEL: ETHTOOL,
      BRANCH_LABEL: "N/A - no upstream branch verified; upstream tag v6.1"},
     _tag_row(contains="no")),
    # A branch smuggled in after the right tag.
    ({SOURCE_LABEL: ETHTOOL,
      BRANCH_LABEL: "N/A - no upstream branch verified; upstream tag v6.1-rc1 "
                    "and branch master"},
     _tag_row()),
    # A guess dressed as N/A where the mapping records nothing.
    ({SOURCE_LABEL: URL, BRANCH_LABEL: "N/A; probably master"},
     _row(**{"Upstream Branch": ""})),
    ({SOURCE_LABEL: URL, BRANCH_LABEL: "N/A - no upstream branch verified; "
                                       "probably master"},
     _row(**{"Upstream Branch": ""})),
])
def test_the_verifier_catches_a_file_the_mapping_does_not_support(value, row):
    assert VERIFY.field_problems("mstpd", row, value)


def test_the_verifier_holds_the_plan_to_the_file():
    text = SERVICE.render(_tag_only())
    entry = {
        "package": "ethtool",
        "upstream_source": source_field(_tag_only()),
        "current_upstream_branch": branch_field(_tag_only()).value,
        "flags": [{"code": f.code, "detail": f.detail}
                  for f in review_flags(_tag_only())],
    }
    assert VERIFY.plan_entry_problems(entry, text) == []

    unflagged = dict(entry, flags=[])
    assert BRANCH_NOT_VERIFIED in VERIFY.plan_entry_problems(unflagged, text)[0]
    stale = dict(entry, current_upstream_branch="master")
    assert VERIFY.plan_entry_problems(stale, text)
    old_plan = {"package": "ethtool", "upstream_ref": "v6.1"}
    assert "regenerate" in VERIFY.plan_entry_problems(old_plan, text)[0]


def test_the_verifier_requires_the_flag_on_a_branch_from_a_tag():
    text = SERVICE.render(_probed())
    field = branch_field(_probed())
    entry = {
        "package": "ethtool",
        "upstream_source": source_field(_probed()),
        "current_upstream_branch": field.value,
        "upstream_tag": field.tag,
        "flags": [{"code": f.code, "detail": f.detail}
                  for f in review_flags(_probed())],
    }
    assert VERIFY.plan_entry_problems(entry, text) == []
    unflagged = dict(entry, flags=[])
    assert BRANCH_FROM_TAG in VERIFY.plan_entry_problems(unflagged, text)[0]
    # And never on a plain branch.
    plain = SERVICE.render(_resolution())
    stray = {"package": "mstpd", "upstream_source": URL,
             "current_upstream_branch": "master", "upstream_tag": "",
             "flags": [{"code": BRANCH_FROM_TAG, "detail": "x"}]}
    assert BRANCH_FROM_TAG in VERIFY.plan_entry_problems(stray, plain)[0]
