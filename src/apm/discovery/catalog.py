"""Turn the per-release manifests into the package catalogue (packages.yaml).

Written to config.packages_file(): config/packages.yaml in a checkout, or
wherever APM_PACKAGES_FILE points (the container's writable out/ mount).

One entry per package, carrying which releases ship it and the ARCoS branch each
release tracks. Generated - curated decisions belong in overrides.yaml, so
regenerating this file never destroys human work.
"""

from __future__ import annotations

import datetime as _dt
import os
import tempfile
from pathlib import Path

import yaml

HEADER = """# ARCoS package catalogue - GENERATED, do not edit by hand.
#
# Source: {repository}
# Manifest branches: {branches}
# Regenerate: python -m apm discover
#
# 'releases' lists only the releases whose manifest actually contains the
# package. Eight packages ship in bookworm but not trixie, so this is not the
# same list for every entry.
#
# Curated upstream mappings belong in config/overrides.yaml, which always wins.
"""


def build_catalog(per_release: dict, settings) -> dict:
    packages = {}
    for release, submodules in per_release.items():
        for sub in submodules:
            entry = packages.setdefault(
                sub.package_name,
                {
                    "repository": sub.ssh_url,
                    "github_repository": sub.github_repository,
                    "submodule_path": sub.path,
                    "releases": [],
                    "branches": {},
                    "commits": {},
                    "debian_source_package": None,
                },
            )
            if release not in entry["releases"]:
                entry["releases"].append(release)
            entry["branches"][release] = sub.branch or "aminor"
            if sub.pinned_commit:
                entry["commits"][release] = sub.pinned_commit

    for entry in packages.values():
        entry["releases"].sort(key=lambda r: settings.release_ids.index(r))

    return {
        "version": 1,
        "generated_at": _dt.date.today().isoformat(),
        "generated_from": {
            "repository": settings.manifest.get("repository"),
            "branches": settings.manifest.get("branches", {}),
            "file": settings.manifest.get("file", ".gitmodules"),
        },
        "packages": dict(sorted(packages.items())),
    }


def write_catalog(catalog: dict, path: Path, settings) -> None:
    """Write the catalogue atomically: a reader never sees half a file, and a
    failed write leaves the previous catalogue in place."""
    header = HEADER.format(
        repository=settings.manifest.get("repository"),
        branches=settings.manifest.get("branches", {}),
    )
    body = yaml.safe_dump(catalog, sort_keys=False, default_flow_style=False, width=100)
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    except OSError as exc:
        raise OSError(
            exc.errno,
            f"cannot write the package catalogue to {path}: {exc.strerror}. "
            f"Point APM_PACKAGES_FILE at a writable path (the container uses "
            f"/app/out/packages.yaml).",
        ) from exc
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(header + "\n" + body)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
