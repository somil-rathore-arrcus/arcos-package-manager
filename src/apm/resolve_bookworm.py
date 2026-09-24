"""Discover, resolve and report one Debian release - Bookworm by default.

    PYTHONPATH=src python -m apm.resolve_bookworm

Discovery runs first and is not optional: the package list comes from the
arrcus_rel manifest at runtime, so the row count is whatever actually ships,
never a number written down somewhere. `--no-discover` reuses the catalogue
already on disk when the manifest has not moved.

The release is a parameter with a default, not a hard-coded value: Trixie runs
through the same path with --release trixie.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from .cache import HttpCache
from .config import (
    ROOT, Environment, load_overrides, load_packages, load_settings,
    packages_file,
)
from .discovery.catalog import build_catalog, write_catalog
from .discovery.gitmodules import discover
from .gitio.ancestry import AncestryChecker
from .gitio.verify import Verifier
from .models import Status
from .report import (
    to_row, write_csv_rows, write_release_reports, write_xlsx_rows,
)
from .resolve import Resolver

log = logging.getLogger("apm")


def run(release: str = "bookworm", out_dir: Path = None, rediscover: bool = True,
        ancestry_enabled: bool = True, packages_wanted=None) -> int:
    settings = load_settings()
    env = Environment.from_env()
    cache = HttpCache(env.cache_dir / "http", env.cache_ttl, env.http_timeout)
    transports = env.transports(settings)
    out_dir = Path(out_dir or ROOT / "out")

    manifest_branch = settings.manifest.get("branches", {}).get(release)
    if not manifest_branch:
        print(
            f"no manifest branch configured for {release}; add one under "
            f"manifest.branches in config/settings.yaml",
            file=sys.stderr,
        )
        return 1

    if rediscover:
        print(
            f"Discovering {settings.manifest.get('repository')} "
            f"{manifest_branch} packages..."
        )
        per_release = discover(transports, settings)
        catalog = build_catalog(per_release, settings)
        write_catalog(catalog, packages_file(), settings)

    packages = [p for p in load_packages() if release in (p.releases or [release])]
    if packages_wanted:
        wanted = set(packages_wanted)
        packages = [p for p in packages if p.name in wanted]
        missing = wanted - {p.name for p in packages}
        if missing:
            print(f"unknown package(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 1
    if not packages:
        print(f"no packages discovered for {release}", file=sys.stderr)
        return 1

    print(f"\n{len(packages)} packages in {release} ({manifest_branch})\n")

    ancestry = AncestryChecker(
        transports, env.cache_dir / "ancestry",
        enabled=settings.ancestry.get("enabled", True) and ancestry_enabled,
        timeout=env.git_long_timeout,
    )
    resolver = Resolver(
        settings, load_overrides(), cache, Verifier(transports), ancestry
    )

    resolutions = []
    total = len(packages)
    for index, package in enumerate(sorted(packages, key=lambda p: p.name), 1):
        print(f"[{index}/{total}] {package.name}", flush=True)
        resolutions.append(resolver.resolve_one(package, release))

    # The canonical mapping keeps every release the catalogue knows about, so
    # resolving one release must not delete the others' rows from it.
    merged = _merge_with_existing(resolutions, out_dir / "upstream-mapping.csv", release)
    write_csv_rows(merged, out_dir / "upstream-mapping.csv")
    write_xlsx_rows(merged, out_dir / "upstream-mapping.xlsx")
    written = write_release_reports(resolutions, out_dir, release)

    counts = Counter(r.status.value for r in resolutions)
    print("\nResolution summary:\n")
    for status in [s.value for s in Status]:
        if counts.get(status):
            print(f"  {status:<14} {counts[status]}")
    print(f"\n  {'TOTAL':<14} {len(resolutions)}")

    print("\nGenerated:\n")
    print(f"  {written['xlsx']}")
    print(f"  {written['csv']}")
    print(f"  {out_dir / 'upstream-mapping.csv'}   (all releases, canonical)")
    print(f"  {out_dir / 'upstream-mapping.xlsx'}  (all releases, canonical)")

    unexplained = [
        r.package for r in resolutions
        if r.status is not Status.VERIFIED and not r.reason
    ]
    if unexplained:
        print(
            f"\nWARNING: no reason recorded for {', '.join(unexplained)}",
            file=sys.stderr,
        )
    return 0


def _merge_with_existing(resolutions: list, csv_path: Path, release: str) -> list:
    """This release's freshly resolved rows, plus the other releases' as they were.

    Resolving bookworm alone must not make trixie vanish from the mapping the
    dashboard reads. Carried-through rows are the exact dictionaries that were
    written before - nothing is re-derived from them, so nothing can change
    meaning on the way through.
    """
    import csv as _csv

    rows = [to_row(r) for r in resolutions]
    if not csv_path.exists():
        return rows

    with csv_path.open(encoding="utf-8") as handle:
        for record in _csv.DictReader(handle):
            if (record.get("Debian Release") or "").strip() != release:
                rows.append(dict(record))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="apm.resolve_bookworm",
        description="Discover and resolve one Debian release end to end.",
    )
    parser.add_argument("--release", default="bookworm")
    parser.add_argument("--out-dir", default=str(ROOT / "out"))
    parser.add_argument("--package", action="append")
    parser.add_argument(
        "--no-discover", action="store_true",
        help="reuse the package catalogue on disk instead of re-reading the "
             "manifest",
    )
    parser.add_argument(
        "--no-ancestry", action="store_true",
        help="skip the merge-base probe (faster, but the upstream is unproven)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    return run(
        release=args.release,
        out_dir=Path(args.out_dir),
        rediscover=not args.no_discover,
        ancestry_enabled=not args.no_ancestry,
        packages_wanted=args.package,
    )


if __name__ == "__main__":
    raise SystemExit(main())
