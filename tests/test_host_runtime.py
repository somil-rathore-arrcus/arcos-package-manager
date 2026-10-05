"""Running on a host without Docker: one process serves dashboard and API.

The deployment host has no Node, no nginx and (by policy) no docker compose,
so the backend serves the built dashboard itself. These tests hold the
properties that matter: the API is never shadowed by the dashboard, client
routes load the app, nothing outside the build can be read, and the runtime
scripts put state in out/ and read .env without executing it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apm.api import deps
from apm.domain.models import DebianRelease

ROOT = Path(__file__).resolve().parents[1]


class FakeCatalog:
    def releases(self):
        return [DebianRelease(id="bookworm", name="Debian Bookworm",
                              suite="oldstable", version="12")]


class FakeContainer:
    catalog = FakeCatalog()


@pytest.fixture
def built(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>ARCoS dashboard</html>")
    (dist / "assets" / "app.js").write_text("console.log('app')")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("not for the browser")
    monkeypatch.setenv("APM_FRONTEND_DIST", str(dist))
    from apm.api.main import create_app

    app = create_app()
    app.dependency_overrides[deps.container] = lambda: FakeContainer()
    return TestClient(app)


def test_the_dashboard_is_served_at_the_root(built):
    response = built.get("/")
    assert response.status_code == 200
    assert "ARCoS dashboard" in response.text


def test_assets_and_top_level_files_are_served(built):
    assert built.get("/assets/app.js").text == "console.log('app')"
    assert built.get("/favicon.svg").text == "<svg/>"


def test_a_client_side_route_loads_the_app(built):
    response = built.get("/packages/mstpd")
    assert response.status_code == 200 and "ARCoS dashboard" in response.text


def test_the_api_is_never_shadowed_by_the_dashboard(built):
    response = built.get("/api/releases")
    assert response.status_code == 200
    assert response.json()[0]["id"] == "bookworm"


def test_an_unknown_api_route_is_a_json_404_not_the_app(built):
    response = built.get("/api/no-such-route")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_nothing_outside_the_build_can_be_read(built):
    response = built.get("/..%2Fsecret.txt")
    assert "not for the browser" not in response.text


def test_without_a_build_the_api_runs_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("APM_FRONTEND_DIST", str(tmp_path / "missing"))
    from apm.api.main import create_app

    app = create_app()
    app.dependency_overrides[deps.container] = lambda: FakeContainer()
    client = TestClient(app)
    assert client.get("/").status_code == 404
    assert client.get("/api/releases").status_code == 200


# -- the runtime scripts ------------------------------------------------------

SCRIPTS = ["env.sh", "setup-venv.sh", "apm", "build-frontend.sh", "server.sh"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_runtime_scripts_are_valid_bash(name):
    subprocess.run(["bash", "-n", str(ROOT / "scripts" / name)], check=True)


def _env(tmp_path, dotenv: str, **extra):
    """Source a copy of scripts/env.sh from a project root with this .env."""
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    (tmp_path / ".env").write_text(dotenv)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("APM_")}
    env.update(extra)
    out = subprocess.run(
        ["bash", "-c", '. scripts/env.sh; echo "$APM_HOST|$APM_PORT|'
                       '${APM_PACKAGES_FILE:-unset}|$APM_RUN_DIR"'],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip().split("|"), out.stderr


def test_defaults_listen_on_every_interface_and_keep_state_in_out(tmp_path):
    (host, port, packages, run), stderr = _env(
        tmp_path, "APM_COMMITTER_NAME=Somil Rathore\nAPM_GIT_BACKEND=local\n")
    assert (host, port) == ("0.0.0.0", "8080")
    assert packages == str(tmp_path / "out" / "packages.yaml")
    assert run == str(tmp_path / "out" / "run")
    assert "command not found" not in stderr, ".env must never be executed"


def test_dotenv_values_are_read_and_the_environment_wins(tmp_path):
    (host, port, packages, _), _ = _env(
        tmp_path, "APM_PORT=9090\nAPM_PACKAGES_FILE=/srv/apm/packages.yaml\n",
        APM_HOST="10.0.0.5")
    assert (host, port) == ("10.0.0.5", "9090")
    assert packages == "unset", "the application reads .env's own value"


def test_server_status_reports_not_running(tmp_path):
    shutil.copytree(ROOT / "scripts", tmp_path / "scripts")
    result = subprocess.run(["bash", "scripts/server.sh", "status"], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 1 and "not running" in result.stdout
