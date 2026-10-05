"""`apm doctor`: can this host do the job? Checked, not assumed.

Storage the run writes to, the git host and its workspace, private and public
repository access, and the GitHub API token - each reported PASS, WARN or FAIL
with the reason. No secret is ever printed: the token and passwords are reported
only as set or not set.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import List

from .config import CONFIG_DIR, ROOT, Environment, load_settings, packages_file

results: List[tuple] = []


def _report(level: str, name: str, detail: str = "") -> None:
    results.append((level, name))
    print(f"{level:<5} {name}" + (f"  -> {detail}" if detail else ""), flush=True)


def _writable(path: Path) -> str:
    try:
        path.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=str(path), prefix=".apm-doctor.")
        os.close(handle)
        os.unlink(temp)
        return ""
    except OSError as exc:
        return f"{exc.strerror or exc}"


def run_doctor(repositories=(), release: str = "bookworm",
               check_manifest: bool = True) -> int:
    results.clear()
    env = Environment.from_env()
    settings = load_settings()

    print("== storage")
    out = ROOT / "out"
    problem = _writable(out)
    _report("FAIL" if problem else "PASS", f"output directory {out} is writable",
            problem)
    target = packages_file()
    problem = _writable(target.parent)
    _report("FAIL" if problem else "PASS",
            f"runtime package catalogue {target} is writable", problem)
    if target.parent.resolve() == CONFIG_DIR.resolve():
        ro = _writable(CONFIG_DIR)
        _report("WARN" if ro else "PASS",
                "the catalogue is written into config/ (APM_PACKAGES_FILE unset)",
                "config/ is read-only here; set APM_PACKAGES_FILE" if ro else "")
    else:
        _report("PASS", "config/ is not written to",
                "read-only" if _writable(CONFIG_DIR) else "writable, but unused")
    from .services.approvals import approvals_file

    approvals = approvals_file(ROOT)
    problem = _writable(approvals.parent)
    _report("FAIL" if problem else "PASS", f"approvals file {approvals} is writable",
            problem)
    problem = _writable(env.cache_dir)
    _report("FAIL" if problem else "PASS", f"cache {env.cache_dir} is writable",
            problem)

    print("== git")
    transports = env.transports(settings)
    described = transports.describe()
    _report("PASS", f"git backend {described['backend']}",
            f"ssh bridge {'configured' if described['ssh_configured'] else 'not configured'}"
            f", local ssh key {'set' if described['local_git_ssh_key'] else 'not set'}"
            f", ssh password {'set' if env.ssh.password else 'not set'}")
    from .services.container import build_workspaces

    workspaces = build_workspaces(env, settings, transports)
    probe = workspaces.probe()
    _report("PASS" if probe["writable"] else "FAIL",
            f"workspace {probe['root']} on the {probe['transport']} git host",
            probe["git_version"] or probe["error"] or "")
    if check_manifest:
        manifest = settings.manifest.get("repository", "")
        branch = settings.manifest.get("branches", {}).get(release, "")
        git = transports.for_url(manifest)
        result = git.run(["ls-remote", manifest, f"refs/heads/{branch}"])
        _report("PASS" if result.ok and result.text else "FAIL",
                f"private repository access: {manifest} {branch}",
                (result.text[:12] if result.ok and result.text
                 else (result.stderr.strip()[:200] or "no such branch")))
        for repo in repositories:
            url = f"ssh://git@github.com/{repo}.git"
            result = transports.for_url(url).run(
                ["ls-remote", "--symref", url, "HEAD"])
            _report("PASS" if result.ok else "FAIL", f"git access to {repo}",
                    result.stderr.strip()[:200] if not result.ok else "")
        public = "https://github.com/mstpd/mstpd.git"
        result = transports.for_url(public).run(["ls-remote", public, "HEAD"])
        _report("PASS" if result.ok else "FAIL", "public upstream access (github.com)",
                result.stderr.strip()[:200] if not result.ok else "")
        cmd = "ssh -T -o BatchMode=yes git@github.com 2>&1; true"
        identity = transports.local.git_ssh_command()
        if identity and described["backend"] == "local":
            cmd = f"{identity} -T git@github.com 2>&1; true"
        who = transports.for_url(manifest).shell(cmd, timeout=30)
        line = next((l for l in who.stdout.splitlines() if "Hi " in l), "")
        _report("PASS" if line else "WARN", "GitHub SSH identity for pushes",
                line.split("!")[0].replace("Hi ", "authenticated as ")
                if line else "could not confirm (push access is checked at push time)")

    print("== GitHub API")
    from .services.github_service import GitHubError, GitHubService

    github = GitHubService(env.github_token, env.github_api_url,
                           timeout=env.http_timeout)
    if not env.github_token:
        _report("WARN", "APM_GITHUB_TOKEN is not set",
                "read-only: dry runs work, pull requests cannot be opened")
        try:
            response = github._request("GET", "/rate_limit")
            _report("PASS" if response.status_code == 200 else "FAIL",
                    f"GitHub API reachable at {env.github_api_url} (unauthenticated)",
                    f"HTTP {response.status_code}")
        except GitHubError as exc:
            _report("FAIL", f"GitHub API reachable at {env.github_api_url}", str(exc))
    else:
        _report("PASS", "APM_GITHUB_TOKEN is set", "value not shown")
        try:
            response = github._request("GET", "/rate_limit")
            limit = response.json().get("rate", {}) if response.status_code == 200 else {}
            _report("PASS" if response.status_code == 200 else "FAIL",
                    f"GitHub API reachable at {env.github_api_url}",
                    f"HTTP {response.status_code}, {limit.get('remaining', '?')} "
                    f"requests left of {limit.get('limit', '?')}")
        except GitHubError as exc:
            _report("FAIL", f"GitHub API reachable at {env.github_api_url}", str(exc))
        for repo in repositories:
            try:
                data = github.get_repository(repo)
                perms = data.get("permissions") or {}
                _report("PASS", f"token can see {repo}",
                        f"default branch {data.get('default_branch')}, "
                        f"permissions {sorted(k for k, v in perms.items() if v)}")
            except GitHubError as exc:
                _report("FAIL", f"token can see {repo}", f"{exc} {exc.detail}".strip())

    failed = [name for level, name in results if level == "FAIL"]
    print()
    print("FAILURES:", ", ".join(failed) if failed else "none")
    return 1 if failed else 0

