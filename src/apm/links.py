"""Web URLs for the things a row refers to.

Every identifier in the sheet should be one click from the page that proves it,
so the reviewer can check a mapping without reconstructing URLs by hand.
"""

from __future__ import annotations

from typing import Optional


def arcos_web(github_repository: str, branch: str = "") -> Optional[str]:
    if not github_repository:
        return None
    base = f"https://github.com/{github_repository}"
    return f"{base}/tree/{branch}" if branch else base


def arcos_commit_web(github_repository: str, sha: Optional[str]) -> Optional[str]:
    if not (github_repository and sha):
        return None
    return f"https://github.com/{github_repository}/commit/{sha}"


def debian_source_web(release: str, source_package: Optional[str]) -> Optional[str]:
    if not source_package:
        return None
    return f"https://packages.debian.org/source/{release}/{source_package}"


def upstream_web(repository: Optional[str], ref: Optional[str] = None) -> Optional[str]:
    """A browsable page for the upstream repo, at the tracked ref where possible."""
    if not repository:
        return None
    url = repository[:-4] if repository.endswith(".git") else repository

    if "git.kernel.org" in repository:
        # cgit keeps the .git in the path and takes the ref as a query parameter.
        return f"{repository}/log/?h={ref}" if ref else f"{repository}/log/"
    if "github.com" in url or "codeberg.org" in url:
        return f"{url}/tree/{ref}" if ref else url
    if "gitlab.com" in url or "salsa.debian.org" in url:
        return f"{url}/-/tree/{ref}" if ref else url
    return url
