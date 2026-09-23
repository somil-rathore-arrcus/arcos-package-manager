"""Command line entry point: discover, resolve, report."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .cache import HttpCache
from .config import (
    CONFIG_DIR, ROOT, Environment, load_overrides, load_packages, load_settings,
)
from .discovery.catalog import build_catalog, write_catalog
from .discovery.gitmodules import discover
from .gitio.ancestry import AncestryChecker
from .gitio.verify import Verifier
from .report import write_csv, write_xlsx
from .resolve import Resolver

log = logging.getLogger("apm")


def _context():
    settings = load_settings()
    env = Environment.from_env()
    cache = HttpCache(env.cache_dir / "http", env.cache_ttl, env.http_timeout)
    transports = env.transports(settings)
    return settings, env, cache, transports


def cmd_discover(args) -> int:
    settings, _, _, transports = _context()
    per_release = discover(transports, settings)
    catalog = build_catalog(per_release, settings)
    write_catalog(catalog, CONFIG_DIR / "packages.yaml", settings)
    packages = catalog["packages"]
    if args.json:
        # The discovery result itself, for anything that wants the package set
        # without parsing YAML. The count is whatever the manifest holds.
        discovered = [
            {
                "package": name,
                "repository": entry.get("github_repository", ""),
                "path": entry.get("submodule_path", ""),
                "releases": entry.get("releases", []),
                "branches": entry.get("branches", {}),
            }
            for name, entry in sorted(packages.items())
            if not args.release or args.release in entry.get("releases", [])
        ]
        print(json.dumps(discovered, indent=2))
        return 0
    print(f"discovered {len(packages)} packages -> {CONFIG_DIR / 'packages.yaml'}")
    for release in settings.release_ids:
        count = sum(1 for p in packages.values() if release in p["releases"])
        print(f"  {release}: {count}")
    return 0


def cmd_resolve_release(args) -> int:
    """Discover + resolve one release, and write its report. See resolve_bookworm."""
    from .resolve_bookworm import run as run_release

    return run_release(
        release=args.release,
        out_dir=Path(args.out_dir),
        rediscover=not args.no_discover,
        ancestry_enabled=not args.no_ancestry,
        packages_wanted=args.package,
    )


def cmd_generate_upstream_md(args) -> int:
    from .generate_upstream_md import run as run_md

    return run_md(
        release=args.release,
        branch=args.branch,
        out_dir=Path(args.out_dir),
        packages_wanted=args.package,
        include_needs_review=args.include_needs_review,
        read_remote=not args.offline,
    )


def cmd_resolve(args) -> int:
    settings, env, cache, transports = _context()
    packages = load_packages()
    if not packages:
        print("no packages; run `discover` first", file=sys.stderr)
        return 1
    if args.package:
        wanted = set(args.package)
        packages = [p for p in packages if p.name in wanted]
        missing = wanted - {p.name for p in packages}
        if missing:
            print(f"unknown package(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 1

    ancestry = AncestryChecker(
        transports,
        env.cache_dir / "ancestry",
        enabled=settings.ancestry.get("enabled", True) and not args.no_ancestry,
    )
    resolver = Resolver(
        settings, load_overrides(), cache, Verifier(transports), ancestry
    )
    resolutions = resolver.resolve_all(packages, args.release or None)

    out_dir = Path(args.out_dir)
    write_csv(resolutions, out_dir / "upstream-mapping.csv")
    write_xlsx(resolutions, out_dir / "upstream-mapping.xlsx")

    from collections import Counter
    counts = Counter(r.status.value for r in resolutions)
    print(f"resolved {len(resolutions)} rows")
    for status, count in counts.most_common():
        print(f"  {status:<14} {count}")
    print(f"wrote {out_dir / 'upstream-mapping.csv'}")
    print(f"wrote {out_dir / 'upstream-mapping.xlsx'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="apm", description="ARCoS package upstream mapping"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser(
        "discover", help="read the arrcus_rel manifests -> config/packages.yaml"
    )
    p_discover.add_argument(
        "--json", action="store_true",
        help="print the discovered packages as JSON (package, repository, path)",
    )
    p_discover.add_argument(
        "--release", help="with --json, list only packages shipping in this release"
    )
    p_discover.set_defaults(func=cmd_discover)

    p_resolve = sub.add_parser(
        "resolve", help="resolve upstreams and write the mapping sheet"
    )
    p_resolve.add_argument("--release", action="append", help="limit to a release")
    p_resolve.add_argument("--package", action="append", help="limit to a package")
    p_resolve.add_argument("--out-dir", default=str(ROOT / "out"))
    p_resolve.add_argument(
        "--no-ancestry", action="store_true",
        help="skip the merge-base probe (faster, but the upstream is unproven)",
    )
    p_resolve.set_defaults(func=cmd_resolve)

    p_release = sub.add_parser(
        "resolve-release",
        help="discover + resolve ONE release and write its own report",
    )
    p_release.add_argument("--release", default="bookworm")
    p_release.add_argument("--out-dir", default=str(ROOT / "out"))
    p_release.add_argument("--package", action="append")
    p_release.add_argument("--no-discover", action="store_true")
    p_release.add_argument("--no-ancestry", action="store_true")
    p_release.set_defaults(func=cmd_resolve_release)

    p_md = sub.add_parser(
        "generate-upstream-md",
        help="render debian/upstream.md for a release; writes nothing remote",
    )
    p_md.add_argument("--release", default="bookworm")
    p_md.add_argument("--branch", default="")
    p_md.add_argument("--package", action="append")
    p_md.add_argument("--out-dir", default=str(ROOT / "out"))
    p_md.add_argument("--include-needs-review", action="store_true")
    p_md.add_argument("--offline", action="store_true")
    p_md.set_defaults(func=cmd_generate_upstream_md)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
