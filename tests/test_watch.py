"""debian/watch only helps when it names a git forge.

lldpd and net-snmp are the reason this matters: both have watch files, neither
points at a repository, and treating their URLs as upstream would produce a
mapping that cannot be cloned.
"""

from pathlib import Path

from apm.debian.dep12 import watch_urls
from apm.upstream.forge import to_repo

DATA = Path(__file__).parent / "data"
FORGES = {"github.com", "gitlab.com", "salsa.debian.org", "codeberg.org"}


def _forge_repos(name):
    urls = watch_urls((DATA / name).read_text())
    return [r for r in (to_repo(u, FORGES) for u in urls) if r]


def test_v3_watch_with_opts_yields_a_url_but_no_forge():
    urls = watch_urls((DATA / "lldpd.watch").read_text())
    assert urls == ["https://vincent.bernat.ch/en/projects"]
    assert _forge_repos("lldpd.watch") == []


def test_v4_watch_sourceforge_is_not_a_forge():
    urls = watch_urls((DATA / "net-snmp.watch").read_text())
    assert urls and urls[0].startswith("https://sf.net/")
    assert _forge_repos("net-snmp.watch") == []


def test_github_releases_url_resolves_to_the_repository():
    repos = _forge_repos("libnl3.watch")
    assert [r.url for r in repos] == ["https://github.com/thom311/libnl.git"]


def test_empty_watch():
    assert watch_urls(None) == []
    assert watch_urls("version=4\n# nothing else\n") == []
