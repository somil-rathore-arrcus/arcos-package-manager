"""The Docker deployment contract: what the image and compose file promise.

Read from the files themselves, so a change that would bake a secret into the
image, make config/ writable, run as root or drop the healthcheck fails here
before anyone builds it. The real build-and-run validation happens on the
deployment host; these keep the promises from eroding in between.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
APP = COMPOSE["services"]["app"]
IGNORE = [l.strip() for l in (ROOT / ".dockerignore").read_text().splitlines()
          if l.strip() and not l.startswith("#")]


def _stage(name: str) -> str:
    match = re.search(rf"^FROM .* AS {name}\n(.*?)(?=^FROM |\Z)", DOCKERFILE,
                      re.M | re.S)
    assert match, f"no {name} stage"
    return match.group(1)


# -- the image ---------------------------------------------------------------

def test_the_dashboard_is_built_in_a_node_stage_and_node_stays_out_of_runtime():
    assert re.search(r"^FROM node:\S+ AS frontend-build", DOCKERFILE, re.M)
    assert "npm ci" in _stage("frontend-build")
    runtime = _stage("runtime")
    assert "COPY --from=frontend-build /app/frontend/dist /app/frontend/dist" in runtime
    assert re.search(r"^FROM python:\S+ AS base", DOCKERFILE, re.M)
    assert "node" not in _stage("base").lower()


def test_the_runtime_is_the_default_target_and_listens_on_8080():
    assert DOCKERFILE.rstrip().splitlines()[-1].strip().startswith('"--timeout-graceful')
    assert list(re.finditer(r"^FROM .* AS (\S+)", DOCKERFILE, re.M))[-1].group(1) == "runtime"
    runtime = _stage("runtime")
    assert "EXPOSE 8080" in runtime
    assert '"--host", "0.0.0.0", "--port", "8080"' in runtime


def test_the_runtime_runs_unprivileged_with_a_configurable_uid():
    assert "ARG APP_UID" in DOCKERFILE and "ARG APP_GID" in DOCKERFILE
    assert "USER apm" in _stage("runtime")
    assert "USER root" not in DOCKERFILE
    assert "chmod 777" not in DOCKERFILE and "chmod -R 777" not in DOCKERFILE


def test_no_secret_or_machine_specific_value_is_baked_into_the_image():
    for forbidden in ("APM_GITHUB_TOKEN", "APM_SSH_PASSWORD", "id_ed25519", "id_rsa",
                      "/home/lakshya", "10.27."):
        assert forbidden not in DOCKERFILE, forbidden
    # .env.example is a template; a real .env never is copied.
    assert not re.search(r"^COPY .*\.env(?!\.example)", DOCKERFILE, re.M)
    copies = re.findall(r"^COPY (?!--from)(.+)$", DOCKERFILE, re.M)
    copied = " ".join(copies)
    assert ".ssh" not in copied and "out/" not in copied and "workspace" not in copied


def test_dependencies_are_installed_from_pinned_constraints():
    assert "pip install -r requirements.txt -c constraints.txt" in DOCKERFILE
    pins = (ROOT / "constraints.txt").read_text()
    for package in ("fastapi", "uvicorn", "pydantic", "httpx", "PyYAML", "openpyxl"):
        assert re.search(rf"^{package}==\S+$", pins, re.M), package


def test_the_healthcheck_uses_the_cheap_liveness_endpoint():
    runtime = _stage("runtime")
    assert "HEALTHCHECK" in runtime and "/health'" in runtime
    assert "/api/health" not in runtime, "the detailed report runs git"


def test_github_host_keys_come_from_githubs_api_over_https():
    script = (ROOT / "docker" / "github-known-hosts.py").read_text()
    assert "https://api.github.com/meta" in script
    assert "/etc/ssh/ssh_known_hosts" in script


# -- the build context ---------------------------------------------------------

@pytest.mark.parametrize("pattern", [
    ".git", ".venv", "frontend/node_modules", ".env", ".env.*", "out", "workspace",
    "**/id_ed25519*", "**/*.pem",
])
def test_the_build_context_excludes_secrets_and_runtime_state(pattern):
    assert pattern in IGNORE


def test_the_env_example_is_kept_and_holds_no_secret():
    assert "!.env.example" in IGNORE
    example = (ROOT / ".env.example").read_text()
    assert re.search(r"^APM_GITHUB_TOKEN=$", example, re.M)
    assert not re.search(r"^APM_SSH_PASSWORD=\S", example, re.M)
    assert "10.27." not in example and "/home/lakshya" not in example


# -- compose -------------------------------------------------------------------

def test_one_service_publishes_8080_to_container_8080():
    assert list(COMPOSE["services"]) == ["app"]
    assert APP["ports"] == ["${APM_BIND_ADDRESS:-0.0.0.0}:${APM_PORT:-8080}:8080"]
    assert APP["build"]["target"] == "runtime"
    assert APP["build"]["args"] == {"APP_UID": "${APM_UID:-1000}",
                                    "APP_GID": "${APM_GID:-1000}"}


def test_the_container_is_hardened():
    assert APP["read_only"] is True
    assert APP["tmpfs"] == ["/tmp"]
    assert APP["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in APP["security_opt"]
    assert APP["init"] is True
    assert not APP.get("privileged")
    assert "network_mode" not in APP and "pid" not in APP
    assert not any("docker.sock" in v for v in APP["volumes"])


def test_writable_state_is_mounted_and_configuration_is_read_only():
    volumes = APP["volumes"]
    assert "./config:/app/config:ro" in volumes
    assert "${APM_OUT_HOST_DIR:-./out}:/app/out" in volumes
    assert "${APM_WORKSPACE_HOST_DIR:-./workspace}:/var/tmp/arcos-package-manager" in volumes
    env = APP["environment"]
    assert env["APM_WORKSPACE_DIR"] == "/var/tmp/arcos-package-manager"
    assert env["APM_PACKAGES_FILE"].startswith("/app/out/")
    assert env["APM_APPROVALS_FILE"].startswith("/app/out/")


def test_ssh_material_is_mounted_read_only_from_configurable_paths():
    ssh = [v for v in APP["volumes"] if ".ssh" in v]
    assert ssh and all(v.endswith(":ro") for v in ssh)
    assert all(v.startswith("${APM_") for v in ssh), "no host path is hard-coded"
    assert APP["environment"]["APM_GIT_SSH_IDENTITY_FILE"] == \
        "${APM_GIT_SSH_KEY_FILE:+/home/apm/.ssh/id_git}"


def test_secrets_reach_the_container_only_through_the_environment():
    assert APP["env_file"] == [".env"]
    assert "APM_GITHUB_TOKEN" not in APP["environment"]


def test_the_default_host_directories_exist_after_a_clone():
    """Docker creates a missing bind-mount source as root, which the
    unprivileged container then cannot write - so the defaults are tracked."""
    tracked = subprocess.run(["git", "ls-files", "out/.gitkeep", "workspace/.gitkeep"],
                             cwd=ROOT, capture_output=True, text=True).stdout.split()
    if tracked:
        assert tracked == ["out/.gitkeep", "workspace/.gitkeep"]
    gitignore = (ROOT / ".gitignore").read_text()
    assert "!out/.gitkeep" in gitignore and "!workspace/.gitkeep" in gitignore


# -- the entrypoint -----------------------------------------------------------

def _entrypoint(tmp_path, **env):
    dirs = {name: tmp_path / name for name in ("out", "ws", "cache")}
    for d in dirs.values():
        d.mkdir(exist_ok=True)
    script = (ROOT / "docker" / "entrypoint.sh").read_text().replace(
        "/app/out", str(dirs["out"]))
    path = tmp_path / "entrypoint.sh"
    path.write_text(script)
    base = {"PATH": os.environ["PATH"], "APM_WORKSPACE_DIR": str(dirs["ws"]),
            "APM_CACHE_DIR": str(dirs["cache"])}
    base.update(env)
    return dirs, subprocess.run(["sh", str(path), "echo", "server-started"],
                                env=base, capture_output=True, text=True)


def test_the_entrypoint_starts_the_command_and_never_prints_a_secret(tmp_path):
    _, result = _entrypoint(tmp_path, APM_GITHUB_TOKEN="ghp_not-a-real-token-123",
                            APM_SSH_PASSWORD="not-a-real-password")
    assert result.returncode == 0
    assert result.stdout.strip().endswith("server-started")
    assert "github token=set" in result.stdout
    assert "ghp_not-a-real-token-123" not in result.stdout + result.stderr
    assert "not-a-real-password" not in result.stdout + result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_the_entrypoint_refuses_an_unwritable_workspace_with_a_fix(tmp_path):
    dirs, _ = _entrypoint(tmp_path)
    dirs["ws"].chmod(0o555)
    try:
        _, result = _entrypoint(tmp_path)
    finally:
        dirs["ws"].chmod(0o755)
    assert result.returncode == 1
    assert "server-started" not in result.stdout
    assert "APM_UID/APM_GID" in result.stderr


def test_the_entrypoint_refuses_an_unreadable_key(tmp_path):
    _, result = _entrypoint(tmp_path, APM_GIT_SSH_IDENTITY_FILE=str(tmp_path / "missing"))
    assert result.returncode == 1 and "is not readable" in result.stderr


def test_the_entrypoint_is_posix_sh():
    if shutil.which("sh"):
        subprocess.run(["sh", "-n", str(ROOT / "docker" / "entrypoint.sh")], check=True)
