"""Manifest parsing, including the per-package branch overrides."""

from apm.discovery.gitmodules import parse_gitmodules

MANIFEST = """\
[submodule "packages/arrcus-sw"]
\tpath = packages/arrcus-sw
\turl = git@github.com:Arrcus/arrcus_sw.git
\tbranch = aminor
[submodule "linux"]
\tpath = linux
\turl = git@github.com:Arrcus/linux.git
\tbranch = aminor
[submodule "packages/rsyslog"]
\tpath = packages/rsyslog
\turl = git@github.com:Arrcus/rsyslog.git
\tbranch = aminor-trixie
[submodule "ONL-xc"]
\tpath = ONL-xc
\turl = git@github.com:arrcus/ONL.git
\tbranch = aminor-xc
"""


def test_parses_every_submodule():
    subs = parse_gitmodules(MANIFEST)
    assert [s.package_name for s in subs] == [
        "arrcus-sw", "linux", "rsyslog", "ONL-xc",
    ]


def test_top_level_and_nested_paths_both_work():
    subs = {s.package_name: s for s in parse_gitmodules(MANIFEST)}
    assert subs["linux"].path == "linux"
    assert subs["arrcus-sw"].path == "packages/arrcus-sw"


def test_per_package_branches_are_preserved():
    subs = {s.package_name: s for s in parse_gitmodules(MANIFEST)}
    assert subs["rsyslog"].branch == "aminor-trixie"
    assert subs["ONL-xc"].branch == "aminor-xc"
    assert subs["linux"].branch == "aminor"


def test_scp_style_urls_become_owner_name():
    subs = {s.package_name: s for s in parse_gitmodules(MANIFEST)}
    assert subs["arrcus-sw"].github_repository == "Arrcus/arrcus_sw"
    assert subs["arrcus-sw"].ssh_url == "ssh://git@github.com/Arrcus/arrcus_sw.git"
    # The manifest spells the owner inconsistently; it is preserved as written.
    assert subs["ONL-xc"].github_repository == "arrcus/ONL"


def test_malformed_entry_is_skipped():
    assert parse_gitmodules('[submodule "x"]\n\tpath = x\n') == []
