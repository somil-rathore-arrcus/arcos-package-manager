"""URL normalisation refuses the cases that look resolvable but are not."""

import pytest

from apm.upstream.forge import is_packaging_host, to_repo

FORGES = {"github.com", "gitlab.com", "salsa.debian.org", "codeberg.org",
          "bitbucket.org"}
PACKAGING = {"salsa.debian.org", "git.debian.org", "alioth.debian.org"}


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/thom311/libnl/releases",
     "https://github.com/thom311/libnl.git"),
    ("https://github.com/wichert/pyrad.git",
     "https://github.com/wichert/pyrad.git"),
    ("git://github.com/foo/bar.git", "https://github.com/foo/bar.git"),
    ("git@github.com:Arrcus/pyrad.git", "https://github.com/Arrcus/pyrad.git"),
    ("https://gitlab.com/group/sub/proj/-/tree/main",
     "https://gitlab.com/group/sub/proj.git"),
    ("https://git.kernel.org/pub/scm/network/ethtool/ethtool.git",
     "https://git.kernel.org/pub/scm/network/ethtool/ethtool.git"),
])
def test_resolvable_urls(url, expected):
    repo = to_repo(url, FORGES)
    assert repo is not None and repo.url == expected


@pytest.mark.parametrize("url", [
    # An org page. The org is knowable, the repository is not: lldpd.github.io
    # does not imply lldpd/lldpd, and guessing produced a wrong mapping before.
    "https://lldpd.github.io",
    "https://sf.net/net-snmp/net-snmp-5.9.tar.gz",
    "https://openssl-library.org/source/",
    "https://github.com/onlyowner",
    "",
    None,
])
def test_unresolvable_urls_return_none(url):
    assert to_repo(url, FORGES) is None


def test_packaging_host_detection():
    assert is_packaging_host("https://salsa.debian.org/debian/lldpd.git", PACKAGING)
    assert not is_packaging_host("https://github.com/lldpd/lldpd.git", PACKAGING)


def test_web_url_drops_the_git_suffix_except_on_cgit():
    assert to_repo("https://github.com/a/b.git", FORGES).web_url == \
        "https://github.com/a/b"
    kernel = to_repo(
        "https://git.kernel.org/pub/scm/network/ethtool/ethtool.git", FORGES
    )
    assert kernel.web_url.endswith(".git")
