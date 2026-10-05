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
from .models import Status
from .report import (
    to_row, write_csv_rows, write_release_reports, write_resolutions_json,
    write_xlsx_rows,
)

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

    from .services.container import build_resolver

    resolver = build_resolver(
        env, settings, transports, cache, ancestry_enabled=ancestry_enabled,
        overrides=load_overrides(),
    )

    resolutions = []
    total = len(packages)
    for index, package in enumerate(sorted(packages, key=lambda p: p.name), 1):
        print(f"[{index}/{total}] {package.name}", flush=True)
        resolution = resolver.resolve_one(package, release)
        resolutions.append(resolution)
        print(f"        {resolution.status.value} / "
              f"{resolution.verification_level}"
              + (f" ({', '.join(resolution.review_reasons)})"
                 if resolution.review_reasons else "")
              + (f" -> {resolution.upstream.ref}" if resolution.upstream
                 and resolution.status is Status.VERIFIED else ""),
              flush=True)

    if packages_wanted:
        # A partial run updates only the packages it resolved; the rest of the
        # release's rows are carried through unchanged.
        resolutions = _merge_release(resolutions, out_dir, release, resolver,
                                     load_packages())

    # The canonical mapping keeps every release the catalogue knows about, so
    # resolving one release must not delete the others' rows from it.
    merged = _merge_with_existing(resolutions, out_dir / "upstream-mapping.csv", release)
    write_csv_rows(merged, out_dir / "upstream-mapping.csv")
    write_xlsx_rows(merged, out_dir / "upstream-mapping.xlsx")
    written = write_release_reports(resolutions, out_dir, release)
    json_path = write_resolutions_json(
        resolutions, out_dir / "upstream-resolutions.json", release)

    counts = Counter(r.status.value for r in resolutions)
    print("\nResolution summary:\n")
    for status in [s.value for s in Status]:
        if counts.get(status):
            print(f"  {status:<14} {counts[status]}")
    print(f"\n  {'TOTAL':<14} {len(resolutions)}")
    levels = Counter(r.verification_level for r in resolutions)
    print("\nVerification level:\n")
    for level, count in sorted(levels.items()):
        print(f"  {level:<24} {count}")
    reasons = Counter(code for r in resolutions for code in r.review_reasons)
    if reasons:
        print("\nReview reasons:\n")
        for code, count in sorted(reasons.items()):
            print(f"  {code:<24} {count}")
    warned = [r.package for r in resolutions if r.warnings]
    if warned:
        print(f"\nPlausibility warnings on {len(warned)} package(s): "
              f"{', '.join(sorted(warned))}")

    print("\nGenerated:\n")
    print(f"  {written['xlsx']}")
    print(f"  {written['csv']}")
    print(f"  {json_path}   (full fidelity, read by the dashboard)")
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


def _merge_release(resolutions: list, out_dir: Path, release: str, resolver,
                   packages) -> list:
    """Fresh rows for the packages just resolved; every other package of the
    release re-read from the full-fidelity JSON so the release reports stay
    complete. A package with no earlier row is simply absent."""
    import json

    from .services.adapters import to_domain_resolution  # noqa: F401

    path = out_dir / "upstream-resolutions.json"
    done = {r.package for r in resolutions}
    if not path.exists():
        return resolutions
    previous = [
        item for item in json.loads(path.read_text()).get("resolutions", [])
        if item.get("debian_release") == release and item.get("package") not in done
    ]
    return resolutions + [_from_domain(item) for item in previous]


def _from_domain(item: dict):
    """A stored domain resolution back into a pipeline record, for reporting."""
    from .domain.models import UpstreamResolution
    from .models import (
        Category, Confidence, DebianSource, Method, Resolution, Status, Upstream,
    )

    r = UpstreamResolution.model_validate(item)
    debian = None
    if r.debian:
        debian = DebianSource(
            package=r.debian.source_package, version=r.debian.version,
            directory=r.debian.directory, suite=r.debian.suite,
            vcs_git=r.debian.vcs_git, vcs_branch=r.debian.vcs_branch,
            vcs_browser=r.debian.vcs_browser, homepage=r.debian.homepage,
        )
    try:
        confidence = Confidence(r.confidence)
    except ValueError:
        confidence = Confidence.MEDIUM
    out = Resolution(
        package=r.package, release=r.debian_release,
        category=Category(r.category.value) if r.category else Category.ARRCUS_NATIVE,
        status=Status(r.status.value) if r.status.value in Status.__members__
        else Status.FAILED,
        method=Method(r.method.value) if r.method.value in
        {m.value for m in Method} else Method.UNRESOLVED,
        confidence=confidence,
        arcos_repository=r.arcos_repository, github_repository=r.github_repository,
        arcos_branch=r.arcos_branch, arcos_commit=r.arcos_commit,
        arcos_path=r.arcos_path, arcos_release=r.arcos_release,
        mode=r.mode.value.lower(), evidence_source=r.evidence_source,
        evidence_url=r.evidence_url, verification=r.verification, debian=debian,
        upstream=Upstream(r.upstream_repository.url, r.upstream_ref,
                          is_tag=bool(r.upstream_tag))
        if r.upstream_repository else None,
        resolved_sha=r.upstream_commit, merge_base=r.merge_base,
        origin_kind=r.origin_kind, behind=r.behind, arcos_only=r.arcos_only,
        evidence=[e.detail for e in r.evidence], notes=list(r.notes),
        verification_level=r.verification_level.value,
        review_reasons=[x.value for x in r.review_reasons],
        warnings=list(r.warnings), merge_bases=list(r.merge_bases),
        counts_basis=r.counts_basis, upstream_commit_date=r.upstream_commit_date,
        ref_selection=r.ref_selection, curated=r.curated,
        content_match=r.content_match, debian_patches=r.debian_patches,
        candidates=[c for c in r.candidates if c.source.value == "ancestry"],
    )
    return out


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
