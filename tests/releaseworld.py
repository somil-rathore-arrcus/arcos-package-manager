"""A small but complete release world, in real git, for resolver tests.

    upstream  main:   A - B - C - D - E - G        (development tip)
                          |       |
                       v1.2.0   v1.3.0
              stable-1.2:  B - F1 - F2             (maintenance branch)

    arcos (bookworm)  forked at v1.2.0, plus local work X
    arcos (trixie)    forked at v1.3.0, plus local work Y
    packaging         Debian's packaging repo: its own history, with
                      debian/bookworm and debian/trixie branches
    stranger          an unrelated repository - the wrong upstream

Debian ships 1.2.0 in bookworm and 1.3.0 in trixie, so the release-appropriate
refs are stable-1.2 (containing v1.2.0) and v1.3.0, never main.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import gitfixtures as fx
from apm.cache import HttpCache
from apm.config import Release, Settings
from apm.debian.dep12 import DebianArtifacts
from apm.debian.sources_index import SourcesIndex
from apm.gitio.ancestry import AncestryChecker
from apm.gitio.transport import Transports
from apm.gitio.verify import Verifier
from apm.gitio.workspaces import WorkspaceManager
from apm.models import DebianSource, Package
from apm.resolve import Resolver
from apm.services.approvals import ApprovalStore
from apm.services.content_base_service import ContentBaseService


def url(path: Path) -> str:
    return "file://" + str(path)


def build(root: Path) -> SimpleNamespace:
    w = SimpleNamespace(root=root, shas={})
    up = fx.init(root / "upstream")
    for name in "AB":
        w.shas[name] = fx.commit(up, f"{name}.c", f"int {name.lower()};\n",
                                 f"commit {name}")
    fx.git(up, "tag", "-a", "v1.2.0", "-m", "1.2.0")
    fx.git(up, "checkout", "-q", "-b", "stable-1.2")
    w.shas["F1"] = fx.commit(up, "B.c", "int b; /* fix one */\n", "fix one",
                             "Fixes: 0123456789ab (\"commit B\")\n")
    w.shas["F2"] = fx.commit(up, "F2.c", "int f2;\n", "fix two",
                             "This fixes CVE-2026-1111.\n")
    fx.git(up, "checkout", "-q", "main")
    for name in "CD":
        w.shas[name] = fx.commit(up, f"{name}.c", f"int {name.lower()};\n",
                                 f"commit {name}")
    fx.git(up, "tag", "-a", "v1.3.0", "-m", "1.3.0")
    for name in "EG":
        w.shas[name] = fx.commit(up, f"{name}.c", f"int {name.lower()};\n",
                                 f"commit {name}")
    w.upstream = up

    w.arcos = fx.clone(up, root / "arcos-bookworm")
    fx.git(w.arcos, "reset", "-q", "--hard", "v1.2.0")
    w.shas["X"] = fx.commit(w.arcos, "X.c", "int x;\n", "arcos local work X")

    w.arcos_trixie = fx.clone(up, root / "arcos-trixie")
    fx.git(w.arcos_trixie, "reset", "-q", "--hard", "v1.3.0")
    w.shas["Y"] = fx.commit(w.arcos_trixie, "Y.c", "int y;\n", "arcos work Y")

    pkg = fx.init(root / "packaging")
    fx.commit(pkg, "debian/control", "Source: demo\n", "packaging")
    fx.git(pkg, "branch", "debian/bookworm")
    fx.git(pkg, "branch", "debian/trixie")
    fx.git(pkg, "branch", "debian/sid")
    w.packaging = pkg

    stranger = fx.init(root / "stranger")
    fx.commit(stranger, "z.c", "int z;\n", "unrelated root")
    w.stranger = stranger
    return w


SETTINGS_RAW = {
    "manifest": {"branches": {"bookworm": "aminor", "trixie": "aminor-trixie"}},
    "releases": [
        {"id": "bookworm", "suite": "oldstable", "version": "12"},
        {"id": "trixie", "suite": "stable", "version": "13"},
    ],
    "archive": {"mirror": "http://invalid.example"},
    "packaging_hosts": ["salsa.debian.org"],
    "forge_hosts": ["github.com"],
    "kernel": {},
    "ancestry": {
        "enabled": True,
        "packaging_refs": ["debian/sid", "master"],
        "skip": [], "primary_only": [], "max_candidates": 12,
    },
    "plausibility": {"behind_warning": 1000, "arcos_only_warning": 1000,
                     "merge_base_age_years": 4},
}


def settings(**ancestry) -> Settings:
    raw = {k: (dict(v) if isinstance(v, dict) else v) for k, v in SETTINGS_RAW.items()}
    raw["ancestry"].update(ancestry)
    return Settings(raw)


def package(w, release="bookworm", arcos=None, name="demo") -> Package:
    arcos = arcos or (w.arcos if release == "bookworm" else w.arcos_trixie)
    return Package(
        name=name, arcos_repository=url(arcos), github_repository=f"Arrcus/{name}",
        submodule_path=f"packages/{name}", branches={release: "main"},
        releases=[release], commits={release: fx.git(arcos, "rev-parse", "HEAD")},
    )


def source(w, release="bookworm", version=None, vcs=True) -> DebianSource:
    version = version or ("1.2.0-1" if release == "bookworm" else "1.3.0-2")
    return DebianSource(
        package="demo", version=version, directory="pool/main/d/demo",
        suite=release, vcs_git=url(w.packaging) if vcs else None,
    )


def resolver(w, tmp: Path, overrides=None, sources=None, settings_obj=None,
             with_content=True, artifacts=None, monkeypatch=None) -> Resolver:
    transports = Transports(backend="local")
    workspaces = WorkspaceManager(transports, root=str(tmp / "ws"),
                                  private_url_hint="file:///local")
    ancestry = AncestryChecker(transports, tmp / "cache" / "ancestry",
                               workspace_root=str(tmp / "ws"))
    content = ContentBaseService(workspaces) if with_content else None
    r = Resolver(
        settings_obj or settings(), overrides or {},
        HttpCache(tmp / "cache" / "http"), Verifier(transports), ancestry,
        content_base=content, approvals=ApprovalStore(tmp / "out" / "approvals.yaml"),
    )
    for release in ("bookworm", "trixie"):
        entries = (sources or {}).get(release, {})
        r._indices[release] = SourcesIndex(release, entries)
    if monkeypatch is not None:
        monkeypatch.setattr(
            "apm.resolve.fetch_artifacts",
            lambda *a, **k: artifacts or DebianArtifacts({}, None, True),
        )
    return r
