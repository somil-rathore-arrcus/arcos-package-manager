"""Where the generated package catalogue is written and read.

In the container config/ is mounted read-only, so `discover` must write the
catalogue somewhere writable (APM_PACKAGES_FILE) without making config/ writable,
and everything that reads the catalogue must then read that same file.
"""

from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from apm import config
from apm.discovery.catalog import write_catalog

SETTINGS = config.Settings({"manifest": {"repository": "ssh://x/arrcus_rel.git",
                                         "branches": {"bookworm": "aminor"}}})


def _catalog(*names):
    return {"packages": {
        name: {"repository": f"ssh://git@github.com/Arrcus/{name}.git",
               "github_repository": f"Arrcus/{name}", "releases": ["bookworm"],
               "branches": {"bookworm": "aminor"}}
        for name in names
    }}


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """A read-only config/ holding a committed catalogue, and no env override."""
    seed = tmp_path / "config"
    seed.mkdir()
    (seed / "packages.yaml").write_text(yaml.safe_dump(_catalog("seeded")))
    monkeypatch.setattr(config, "CONFIG_DIR", seed)
    monkeypatch.setattr(config, "ROOT", tmp_path)       # no stray .env
    monkeypatch.delenv(config.PACKAGES_FILE_ENV, raising=False)
    seed.chmod(0o555)
    yield seed
    seed.chmod(0o755)


def test_without_an_override_the_checkout_layout_is_unchanged(seeded):
    assert config.packages_file() == seeded / "packages.yaml"
    assert [p.name for p in config.load_packages()] == ["seeded"]


def test_the_override_is_where_discovery_writes(seeded, tmp_path, monkeypatch):
    runtime = tmp_path / "out" / "packages.yaml"
    monkeypatch.setenv(config.PACKAGES_FILE_ENV, str(runtime))
    assert config.packages_file() == runtime


def test_until_discovery_runs_the_committed_catalogue_is_read(
        seeded, tmp_path, monkeypatch):
    monkeypatch.setenv(config.PACKAGES_FILE_ENV, str(tmp_path / "out" / "packages.yaml"))
    assert config.packages_source() == seeded / "packages.yaml"
    assert [p.name for p in config.load_packages()] == ["seeded"]


def test_after_discovery_the_runtime_catalogue_wins(seeded, tmp_path, monkeypatch):
    runtime = tmp_path / "out" / "packages.yaml"
    monkeypatch.setenv(config.PACKAGES_FILE_ENV, str(runtime))

    write_catalog(_catalog("mstpd", "cre2"), config.packages_file(), SETTINGS)

    assert runtime.read_text().startswith("# ARCoS package catalogue - GENERATED")
    assert sorted(p.name for p in config.load_packages()) == ["cre2", "mstpd"]
    # The read-only committed file was never touched.
    assert "seeded" in (seeded / "packages.yaml").read_text()


def test_the_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "new" / "dir" / "packages.yaml"
    write_catalog(_catalog("a"), target, SETTINGS)
    write_catalog(_catalog("b"), target, SETTINGS)
    assert "Arrcus/b" in target.read_text()
    assert [p.name for p in target.parent.iterdir()] == ["packages.yaml"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_location_says_how_to_fix_it(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        with pytest.raises(OSError) as raised:
            write_catalog(_catalog("a"), locked / "packages.yaml", SETTINGS)
        assert "APM_PACKAGES_FILE" in str(raised.value)
        assert list(locked.iterdir()) == []
    finally:
        locked.chmod(0o755)


def test_discover_writes_to_the_override_not_to_config(seeded, tmp_path,
                                                       monkeypatch, capsys):
    from apm import cli

    runtime = tmp_path / "out" / "packages.yaml"
    monkeypatch.setenv(config.PACKAGES_FILE_ENV, str(runtime))
    monkeypatch.setattr(cli, "_context", lambda: (SETTINGS, None, None, None))
    monkeypatch.setattr(cli, "discover", lambda transports, settings: {})
    monkeypatch.setattr(cli, "build_catalog",
                        lambda per_release, settings: _catalog("mstpd"))

    assert cli.cmd_discover(Namespace(json=False, release=None)) == 0

    assert "Arrcus/mstpd" in runtime.read_text()
    assert str(runtime) in capsys.readouterr().out


def test_resolve_release_writes_to_the_override_not_to_config(
        seeded, tmp_path, monkeypatch):
    """The exact failure on the VM: rediscovery wrote into read-only config/."""
    from apm import resolve_bookworm

    runtime = tmp_path / "out" / "packages.yaml"
    monkeypatch.setenv(config.PACKAGES_FILE_ENV, str(runtime))
    monkeypatch.setattr(resolve_bookworm, "load_settings", lambda: SETTINGS)
    monkeypatch.setattr(resolve_bookworm, "discover", lambda transports, settings: {})
    monkeypatch.setattr(resolve_bookworm, "build_catalog",
                        lambda per_release, settings: _catalog("mstpd"))

    # An unknown package stops the run right after discovery, before any
    # network resolution.
    code = resolve_bookworm.run(release="bookworm", out_dir=tmp_path / "out",
                                packages_wanted=["not-a-package"])

    assert code == 1
    assert "Arrcus/mstpd" in runtime.read_text()


def test_the_container_keeps_config_read_only_and_points_at_out():
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
    )["services"]["backend"]
    assert "./config:/app/config:ro" in compose["volumes"]
    assert "./out:/app/out" in compose["volumes"]
    assert compose["environment"]["APM_PACKAGES_FILE"] == "/app/out/packages.yaml"
