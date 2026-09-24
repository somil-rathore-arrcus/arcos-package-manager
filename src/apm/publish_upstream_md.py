"""Propose the reviewed debian/upstream.md files as pull requests.

    apm publish-upstream-md --release bookworm --package mstpd            # dry run
    apm publish-upstream-md --release bookworm --package mstpd \\
        --apply --confirm mstpd
    apm publish-upstream-md --release bookworm --all --apply --confirm-count 26

A dry run is the default. It performs every read and builds - and checks - the
exact commit that would be pushed, then stops: no branch, no push, no PR. Its
results go to upstream-md-pr-dryrun-<release>.json so they never overwrite the
record of a real run.

--apply pushes one branch per package under upstream-metadata/<release>/ and
opens a pull request against the package's ARCoS branch. It needs
APM_GITHUB_TOKEN, and a confirmation that names what it is about to push.
Results are recorded per package in out/upstream-md-pr-results-<release>.json;
re-running resumes from there instead of repeating work.

What is committed is the file generate-upstream-md wrote and you reviewed, byte
for byte. If it changed since, or the mapping no longer renders it, the package
is reported as DRIFT and left alone.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import List

from .config import ROOT
from .domain.enums import PublishStatus
from .domain.models import PublishResult

log = logging.getLogger("apm")


def run(release: str = "bookworm", out_dir: Path = None, plan: Path = None,
        packages_wanted: List[str] = None, all_packages: bool = False,
        apply: bool = False, confirm: List[str] = None,
        confirm_count: int = None, draft: bool = False, title_prefix: str = "",
        limit: int = None, stop_after_failures: int = 3,
        retry_failed: bool = False, recheck: bool = False,
        exclude: List[str] = None, show_diff: bool = False,
        container=None) -> int:
    from .services.container import Container
    from .services.publish_ledger import PublishLedger
    from .services.upstream_md_publisher import PublishError, load_targets

    out_dir = Path(out_dir or ROOT / "out")
    plan = Path(plan or out_dir / f"upstream-md-plan-{release}.json")
    if not plan.exists():
        print(f"no plan at {plan}. Run `apm generate-upstream-md --release "
              f"{release} --branch aminor` and review it first.", file=sys.stderr)
        return 1
    if not packages_wanted and not all_packages:
        print("name the packages with --package, or pass --all", file=sys.stderr)
        return 1

    container = container or Container()
    publisher = container.publisher
    for name in exclude or []:
        publisher.exclude.setdefault(name, "excluded on the command line")

    targets, settled = load_targets(
        plan, container.mapping, container.upstream_md, publisher.exclude
    )
    if packages_wanted:
        wanted = set(packages_wanted)
        known = {t.package for t in targets} | {s.package for s in settled}
        unknown = wanted - known
        if unknown:
            print(f"not in {plan.name}: {', '.join(sorted(unknown))}",
                  file=sys.stderr)
            return 1
        targets = [t for t in targets if t.package in wanted]
        settled = [s for s in settled if s.package in wanted]

    history = PublishLedger.for_release(out_dir, release)
    ledger = history if apply else PublishLedger.for_release(
        out_dir, release, dry_run=True
    )
    queue, kept = publisher.select(
        targets, history, retry_failed=retry_failed, recheck=recheck, limit=limit,
    )

    if apply and confirm:
        names = {t.package for t, _ in queue}
        if set(confirm) != names:
            print(
                f"--confirm names {', '.join(sorted(confirm)) or 'nothing'} but "
                f"this run would push {', '.join(sorted(names)) or 'nothing'}. "
                f"Nothing was pushed.", file=sys.stderr,
            )
            return 2
        confirm_count = len(queue)

    mode = "APPLY - branches will be pushed and PRs opened" if apply \
        else "DRY RUN - nothing will be pushed"
    print(f"{mode}\n")
    print(f"  plan        {plan}")
    print(f"  to process  {len(queue)}")
    print(f"  already done {len(kept)}")
    print(f"  not eligible {len(settled)}\n")

    def progress(index, total, package):
        print(f"[{index}/{total}] {package}", flush=True)

    try:
        results = publisher.publish_many(
            queue, apply=apply, ledger=ledger, confirm_count=confirm_count,
            stop_after_failures=stop_after_failures, draft=draft,
            title_prefix=title_prefix, progress=progress,
        )
    except PublishError as exc:
        print(f"\nstopped: {exc}", file=sys.stderr)
        if exc.detail:
            print(f"  {exc.detail}", file=sys.stderr)
        return 2

    # Settled packages are recorded too, so the summary accounts for every one -
    # but never over the record of a PR that exists.
    ledger.record_all(
        s for s in settled
        if not (history.get(s.package) and history.get(s.package).status.is_done)
    )

    _print_results(results, show_diff)
    everything = results + kept + settled
    print("\nSummary:\n")
    for status, count in sorted(Counter(r.status.value for r in everything).items()):
        print(f"  {status:<22} {count}")
    print(f"\n  ledger  {ledger.json_path}")
    print(f"          {ledger.md_path}")
    if not apply:
        print("\nNothing was pushed. No pull request was created.")
    return 1 if any(r.status.is_failure for r in results) else 0


def _print_results(results: List[PublishResult], show_diff: bool) -> None:
    if not results:
        return
    print("\n| Package | Status | Branch | Commit | PR URL | Error |")
    print("|---|---|---|---|---|---|")
    for r in results:
        url = r.pull_request.url if r.pull_request and r.pull_request.url else ""
        print(f"| {r.package} | {r.status.value} | {r.branch} | "
              f"{(r.commit or '')[:12]} | {url} | {r.error or ''} |")
    for r in results:
        if r.status is not PublishStatus.DRY_RUN_OK:
            continue
        print(f"\n{r.package}: {r.title}")
        print(f"  {r.repository} {r.base_branch} @ {r.base_sha} -> "
              f"{r.branch} @ {r.commit}")
        if r.base_moved:
            print(f"  note: {r.base_branch} has moved past the pinned commit "
                  f"{(r.pinned_commit or '')[:12]}")
        if show_diff and r.diff:
            print(r.diff)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--release", default="bookworm")
    parser.add_argument("--out-dir", default=str(ROOT / "out"))
    parser.add_argument(
        "--plan", help="default: <out-dir>/upstream-md-plan-<release>.json"
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--package", action="append",
                       help="a package to propose; repeatable")
    scope.add_argument("--all", action="store_true",
                       help="every eligible package in the plan")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="the default: build and check, push nothing")
    mode.add_argument("--apply", action="store_true",
                      help="push branches and open pull requests")
    parser.add_argument(
        "--confirm", action="append",
        help="with --apply: the package(s) you expect to be pushed; must match",
    )
    parser.add_argument(
        "--confirm-count", type=int,
        help="with --apply: how many packages you expect to be pushed",
    )
    parser.add_argument("--draft", action="store_true",
                        help="open the pull requests as drafts")
    parser.add_argument("--title-prefix", default="",
                        help='e.g. "BR-123: " for a Jira id')
    parser.add_argument("--limit", type=int,
                        help="process at most this many packages")
    parser.add_argument("--stop-after-failures", type=int, default=3,
                        help="stop after this many consecutive failures (0: never)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="only packages whose last result was a failure")
    parser.add_argument("--recheck", action="store_true",
                        help="also revisit packages that already have a PR")
    parser.add_argument("--exclude", action="append",
                        help="leave this package out; repeatable")
    parser.add_argument("--show-diff", action="store_true",
                        help="print each dry-run diff")


def run_from_args(args) -> int:
    return run(
        release=args.release, out_dir=Path(args.out_dir),
        plan=Path(args.plan) if args.plan else None,
        packages_wanted=args.package, all_packages=args.all, apply=args.apply,
        confirm=args.confirm, confirm_count=args.confirm_count,
        draft=args.draft, title_prefix=args.title_prefix, limit=args.limit,
        stop_after_failures=args.stop_after_failures,
        retry_failed=args.retry_failed, recheck=args.recheck,
        exclude=args.exclude, show_diff=args.show_diff,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="apm.publish_upstream_md",
        description="Propose the reviewed debian/upstream.md files as pull "
                    "requests. A dry run unless --apply is given.",
    )
    add_arguments(parser)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    return run_from_args(args)


if __name__ == "__main__":
    raise SystemExit(main())
