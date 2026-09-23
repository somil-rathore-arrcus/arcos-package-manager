"""Build real git repositories on disk for integration tests.

The comparison engine is only meaningful against actual git history, so these
tests drive real repositories rather than mocking git's answers - a mock would
happily confirm whatever the code already believes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        env={
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(repo),
        },
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main", ".")
    return path


def commit(repo: Path, filename: str, content: str, subject: str,
           body: str = "") -> str:
    (repo / filename).write_text(content)
    git(repo, "add", filename)
    message = f"{subject}\n\n{body}" if body else subject
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def clone(source: Path, target: Path, branch: str = "main") -> Path:
    subprocess.run(
        ["git", "clone", "-q", "--branch", branch, str(source), str(target)],
        check=True, capture_output=True,
    )
    return target
