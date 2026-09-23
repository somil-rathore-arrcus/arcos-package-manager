"""Criticality must follow evidence, and must not invent it."""

import pytest

from apm.domain.enums import Criticality
from apm.services.criticality_service import CriticalityService

SERVICE = CriticalityService()


def test_cve_is_critical_and_records_the_identifier():
    a = SERVICE.assess("net: fix overflow", "This fixes CVE-2026-12345.\n")
    assert a.level is Criticality.CRITICAL
    assert a.cve_ids == ["CVE-2026-12345"]
    assert any("CVE-2026-12345" in e for e in a.evidence)


def test_cc_stable_is_stable_relevant():
    a = SERVICE.assess("usb: fix reset", "Cc: stable@vger.kernel.org # 6.1\n")
    assert a.level is Criticality.STABLE_RELEVANT
    assert a.cc_stable is True


def test_fixes_trailer_is_normal_not_critical():
    a = SERVICE.assess("mm: correct accounting", "Fixes: a1b2c3d4e5f6 (\"mm: add\")\n")
    assert a.level is Criticality.NORMAL
    assert a.fixes == ["a1b2c3d4e5f6"]


def test_security_advisory_is_critical():
    assert SERVICE.assess("update", "See DSA-5555-1.").level is Criticality.CRITICAL
    assert SERVICE.assess("x", "GHSA-abcd-1234-wxyz").level is Criticality.CRITICAL


def test_no_evidence_is_unknown_not_normal():
    a = SERVICE.assess("refactor the parser", "Tidy up.\n")
    assert a.level is Criticality.UNKNOWN
    assert a.evidence == []


@pytest.mark.parametrize("subject,body", [
    ("fix a crash in the scheduler", "This fixes a serious crash."),
    ("SECURITY HARDENING of the allocator", ""),
    ("urgent: repair data corruption", "very important"),
    ("fix use-after-free", "found by fuzzing"),
])
def test_alarming_words_alone_are_not_evidence(subject, body):
    """Scary vocabulary is not evidence; otherwise everything is critical."""
    assert SERVICE.assess(subject, body).level is Criticality.UNKNOWN


def test_fixes_must_be_a_trailer_not_prose():
    a = SERVICE.assess("fix", "This fixes the thing that abc1234 broke.\n")
    assert a.fixes == []
    assert a.level is Criticality.UNKNOWN


def test_cve_outranks_cc_stable():
    a = SERVICE.assess("fix", "CVE-2026-1111\nCc: stable@vger.kernel.org\n")
    assert a.level is Criticality.CRITICAL
    assert a.cc_stable is True


def test_duplicate_cves_are_listed_once():
    a = SERVICE.assess("CVE-2026-2222", "Also CVE-2026-2222 and cve-2026-2222.")
    assert a.cve_ids == ["CVE-2026-2222"]


def test_empty_message():
    assert SERVICE.assess("", "").level is Criticality.UNKNOWN
