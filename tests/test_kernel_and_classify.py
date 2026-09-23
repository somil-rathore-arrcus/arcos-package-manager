"""Kernel series derivation and package classification."""

import pytest

from apm.classify import classify
from apm.config import load_settings
from apm.models import Category, DebianSource
from apm.upstream.kernel import series_for_version

SETTINGS = load_settings()


@pytest.mark.parametrize("version,expected", [
    ("6.1.187-1", "linux-6.1.y"),
    ("6.12.107-1", "linux-6.12.y"),
    ("1:6.1-1", "linux-6.1.y"),
    ("bogus", None),
    ("", None),
])
def test_series_for_version(version, expected):
    assert series_for_version(version) == expected


def test_bookworm_and_trixie_kernels_differ():
    """Guards the mis-pairing that produced a 182,919-commit backlog."""
    assert series_for_version("6.1.187-1") != series_for_version("6.12.107-1")


DEBIAN = DebianSource(
    package="pyrad", version="2.1-3", directory="pool/main/p/pyrad",
    suite="bookworm",
)


@pytest.mark.parametrize("name,source,has_upstream,expected", [
    ("pyrad", DEBIAN, True, Category.DEBIAN_UPSTREAM),
    ("lldpd", DEBIAN, False, Category.DEBIAN_NO_UPSTREAM),
    ("bcmsdk", None, False, Category.VENDOR),
    ("mlnx-en", None, False, Category.VENDOR),
    ("intel-ice", None, False, Category.VENDOR),
    ("confd", None, False, Category.VENDOR),
    ("mstpd", None, True, Category.THIRD_PARTY),
    ("arcapi", None, False, Category.ARRCUS_NATIVE),
    ("medusa-underlay", None, False, Category.ARRCUS_NATIVE),
])
def test_classification(name, source, has_upstream, expected):
    assert classify(name, source, has_upstream, SETTINGS) == expected


def test_upstream_version_strips_epoch_and_revision():
    assert DebianSource("x", "1:6.1-1", "d", "s").upstream_version == "6.1"
    assert DebianSource("x", "6.1.187-1", "d", "s").upstream_version == "6.1.187"
    assert DebianSource("x", "5.9.3+dfsg-2+deb12u1", "d", "s").upstream_version == \
        "5.9.3+dfsg-2+deb12u1".rsplit("-", 1)[0]
