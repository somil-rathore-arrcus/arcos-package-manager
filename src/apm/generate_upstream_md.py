"""Generate debian/upstream.md for every package in a release, locally.

    PYTHONPATH=src python -m apm.generate_upstream_md --release bookworm \\
        --branch aminor

Files are written under out/upstream-md/<package>/debian/upstream.md and a
branch/commit plan is written beside them. Nothing is pushed and no pull request
is opened: this command has no write access to any remote and does not ask for
a GitHub token.

Each file is rendered from the SAME verified resolution the mapping and the
dashboard use, so the record committed to a package repository cannot disagree
with what the tool says.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from .config import ROOT
from .services.container import Container

log = logging.getLogger("apm")


def run(release: str = "bookworm", branch: str = "", out_dir: Path = None,
        packages_wanted=None, include_needs_review: bool = False,
        read_remote: bool = True) -> int:
    container = Container()
    out_dir = Path(out_dir or ROOT / "out")
    md_dir = out_dir / "upstream-md"

    resolutions = [
        r for r in container.mapping.all() if r.debian_release == release
    ]
    if not resolutions:
        print(
            f"no mapping rows for {release}. Run `python -m apm.resolve_bookworm "
            f"--release {release}` first.",
            file=sys.stderr,
        )
        return 1

    if branch:
        # The requirement names the ARCoS branch explicitly. Honour it as a
        # filter rather than as an override: retargeting a package onto a branch
        # its release does not ship would make the file say something untrue.
        wrong = [
            r.package for r in resolutions
            if r.arcos_branch and r.arcos_branch != branch
        ]
        resolutions = [r for r in resolutions if r.arcos_branch == branch]
        if wrong:
            print(
                f"note: {len(wrong)} package(s) do not track '{branch}' and were "
                f"left out: {', '.join(sorted(wrong))}"
            )
    if packages_wanted:
        wanted = set(packages_wanted)
        resolutions = [r for r in resolutions if r.package in wanted]

    if not resolutions:
        print(f"no packages to generate for {release}/{branch}", file=sys.stderr)
        return 1

    print(f"Generating {len(resolutions)} package(s) for {release}\n")

    def progress(index, total, package):
        print(f"[{index}/{total}] {package}", flush=True)

    result = container.metadata_batch.run(
        resolutions, release, md_dir,
        include_needs_review=include_needs_review,
        read_remote=read_remote,
        progress=progress,
    )
    plan = container.metadata_batch.write_plan(result, out_dir)

    print("\nOutcome summary:\n")
    for outcome, count in sorted(Counter(result.outcomes).items()):
        print(f"  {outcome:<12} {count}")
    print(f"\n  {'generated':<12} {len(result.entries)}")
    print(f"  {'skipped':<12} {len(result.skipped)}")

    unreadable = [e.package for e in result.entries if e.existing_unknown]
    if unreadable:
        print(
            f"\n  {len(unreadable)} fork(s) could not be read, so their outcome "
            f"is what WOULD happen if no file is there: "
            f"{', '.join(sorted(unreadable))}"
        )

    print("\nGenerated:\n")
    print(f"  {md_dir}/<package>/debian/upstream.md")
    print(f"  {plan['json']}")
    print(f"  {plan['markdown']}")
    print("\nNothing was pushed. No pull request was created.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="apm.generate_upstream_md",
        description="Render debian/upstream.md for a release. Writes nothing remote.",
    )
    parser.add_argument("--release", default="bookworm")
    parser.add_argument(
        "--branch", default="",
        help="only packages tracking this ARCoS branch (e.g. aminor)",
    )
    parser.add_argument("--package", action="append")
    parser.add_argument("--out-dir", default=str(ROOT / "out"))
    parser.add_argument(
        "--include-needs-review", action="store_true",
        help=(
            "also write a file for NEEDS_REVIEW packages. It records the status "
            "and the reason; it never invents an upstream."
        ),
    )
    parser.add_argument(
        "--offline", action="store_true",
        help=(
            "do not read the existing file from each fork. Every outcome then "
            "reads CREATED, which is a guess, so it is not the default."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    return run(
        release=args.release,
        branch=args.branch,
        out_dir=Path(args.out_dir),
        packages_wanted=args.package,
        include_needs_review=args.include_needs_review,
        read_remote=not args.offline,
    )


if __name__ == "__main__":
    raise SystemExit(main())
