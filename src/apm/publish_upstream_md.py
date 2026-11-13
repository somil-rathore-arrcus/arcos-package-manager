"""Push the reviewed debian/upstream.md files, one branch per package.

    apm publish-upstream-md --release bookworm --package mstpd            # dry run
    apm publish-upstream-md --release bookworm --package mstpd \\
        --apply --confirm mstpd
    apm publish-upstream-md --release bookworm --all --apply --confirm-count 21

A dry run is the default. It performs every read and builds - and checks - the
exact commit that would be pushed, then stops: no branch, no push. Its results
go to upstream-md-pr-dryrun-<release>.json so they never overwrite the record of
a real run.

--apply pushes one branch per package (named by settings.yaml, or by the
package's record when it already has one) and stops there. No pull request is
opened - this application never opens one - and no GitHub token is needed. Each
package ends BRANCH_PUSHED with its Compare Link and a link to GitHub's
pre-filled "open a pull request" page; a person with access to the repository
opens the pull request from there. A package whose repository and target branch
another release's branch already proposes, with the identical file, ends COVERED
and gets no branch of its own. --push-only is accepted, and changes nothing:
every apply is push-only. An apply also needs a confirmation that names what it
is about to push, and runs only where pushing is allowed (APM_ALLOW_PUSH).

Results are recorded per package in out/upstream-md-pr-results-<release>.json;
re-running resumes from there instead of repeating work. After every apply,
out/upstream-md-pr-links-<release>.md is rewritten with every branch the ledger
has waiting - not only this run's - with its title and description, and the
consolidated report out/upstream-md-publishing-report.md is rebuilt for every
release (see publishing_report). A dry run writes the links its pushes would
give, and the ones already waiting, to out/upstream-md-pr-links-dryrun-<release>.md.

What is committed is the file generate-upstream-md wrote and you reviewed, byte
for byte, from out/upstream-md-<release>/. If it changed since, or the mapping
no longer renders it, the package is reported as DRIFT and left alone.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote, urlsplit

from .config import ROOT
from .domain.enums import PublishStatus
from .domain.models import PublishResult

log = logging.getLogger("apm")

_LINKED = (PublishStatus.BRANCH_PUSHED, PublishStatus.DRY_RUN_OK)


def run(release: str = "bookworm", out_dir: Path = None, plan: Path = None,
        packages_wanted: List[str] = None, all_packages: bool = False,
        apply: bool = False, confirm: List[str] = None,
        confirm_count: int = None, title_prefix: str = "",
        limit: int = None, stop_after_failures: int = 3,
        retry_failed: bool = False, recheck: bool = False,
        exclude: List[str] = None, show_diff: bool = False,
        push_only: bool = True, container=None) -> int:
    from .services.container import Container
    from .services.publish_ledger import PublishLedger, all_records
    from .services.upstream_md_publisher import (
        NO_PULL_REQUESTS, PublishError, load_targets,
    )

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
    # Every eligible package's target, not only the ones named: a waiting
    # branch's description is rebuilt from it when its link could not hold it.
    all_targets = {t.package: t for t in targets}
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
    if not push_only:
        print(f"stopped: {NO_PULL_REQUESTS} Nothing was pushed.", file=sys.stderr)
        return 2
    queue, kept = publisher.select(
        targets, history, retry_failed=retry_failed, recheck=recheck, limit=limit,
    )
    queue, shared = publisher.refuse_shared_branches(queue)
    settled = settled + shared
    waiting = [k for k in kept if k.status is PublishStatus.BRANCH_PUSHED]
    others = all_records(out_dir)
    pushes = publisher.would_push(queue, others)

    if apply and confirm:
        names = {t.package for t, _ in pushes}
        if set(confirm) != names:
            print(
                f"--confirm names {', '.join(sorted(confirm)) or 'nothing'} but "
                f"this run would push {', '.join(sorted(names)) or 'nothing'}. "
                f"Nothing was pushed.", file=sys.stderr,
            )
            return 2
        confirm_count = len(pushes)

    if apply:
        mode = ("APPLY - branches will be pushed; no pull request will be "
                "opened (this tool never opens one)")
    else:
        mode = ("DRY RUN - nothing will be pushed; showing the links an apply "
                "would give")
    print(f"{mode}\n")
    print(f"  plan        {plan}")
    print(f"  to process  {len(queue)}")
    if len(pushes) != len(queue):
        print(f"    of which another release's branch covers "
              f"{len(queue) - len(pushes)}: never pushed")
    print(f"  already done {len(kept) - len(waiting)}")
    if waiting:
        print(f"  pushed, waiting for a PR {len(waiting)}")
    print(f"  not eligible {len(settled)}\n")

    def progress(index, total, package):
        print(f"[{index}/{total}] {package}", flush=True)

    try:
        results = publisher.publish_many(
            queue, apply=apply, ledger=ledger, confirm_count=confirm_count,
            stop_after_failures=stop_after_failures,
            title_prefix=title_prefix, progress=progress, others=others,
        )
    except PublishError as exc:
        print(f"\nstopped: {exc}", file=sys.stderr)
        if exc.detail:
            print(f"  {exc.detail}", file=sys.stderr)
        return 2

    # Settled packages are recorded too, so the summary accounts for every one -
    # but never over the record of a PR that exists. Nor over a branch a
    # push-only run pushed: the ledger itself keeps that record
    # (publish_ledger.supersedes), so a later run still knows the branch.
    ledger.record_all(
        s for s in settled
        if not (history.get(s.package) and history.get(s.package).status.is_done)
    )

    targets_by_package = {t.package: t for t, _ in queue}
    _print_results(results, show_diff, targets_by_package)
    waiting_now = [e for e in history.entries()
                   if e.status is PublishStatus.BRANCH_PUSHED]
    report = None
    if apply:
        links, report = refresh_records(out_dir, release, history,
                                        list(all_targets.values()))
    else:
        # Release-wide, from the real ledger, plus what this dry run would push.
        listed = {e.package: e for e in waiting_now}
        listed.update((r.package, r) for r in results
                      if r.status in _LINKED and r.pr_create_url)
        links = write_links(
            links_path(out_dir, release, dry_run=True), release,
            [listed[name] for name in sorted(listed)], all_targets, dry_run=True,
        )
    everything = results + kept + settled
    print("\nSummary:\n")
    for status, count in sorted(Counter(r.status.value for r in everything).items()):
        print(f"  {status:<22} {count}")
    print(f"\n  ledger  {ledger.json_path}")
    print(f"          {ledger.md_path}")
    print(f"  links   {links}")
    if report is not None:
        print(f"  report  {report}")
    if apply:
        if any(r.status is PublishStatus.BRANCH_PUSHED for r in results):
            print(f"\nBranches were pushed. No pull request was created. Open "
                  f"each one with its link in {links}; the person opening it "
                  f"needs access to the repository.")
        elif waiting_now:
            print(f"\nNo branch was pushed by this run, and no pull request was "
                  f"created. {len(waiting_now)} branch(es) pushed earlier are "
                  f"waiting for a pull request: open each one with its link in "
                  f"{links}; the person opening it needs access to the "
                  f"repository.")
        else:
            print("\nNo branch is waiting for a pull request. No pull request "
                  "was created.")
    if not apply:
        print("\nNothing was pushed. No pull request was created.")
    return 1 if any(r.status.is_failure for r in results) else 0


def refresh_records(out_dir: Path, release: str, ledger, targets) -> tuple:
    """After a push - from this command or the dashboard - rewrite what a person
    works from: the release's links file, with every branch its ledger has
    waiting, and the consolidated report for every release. A report that
    cannot be rebuilt is logged, never a reason to fail a push that happened.
    Returns (links path, report path or None)."""
    from .publishing_report import write as write_report

    waiting = [e for e in ledger.entries() if e.status is PublishStatus.BRANCH_PUSHED]
    links = write_links(links_path(out_dir, release), release, waiting,
                        {t.package: t for t in targets})
    try:
        report = write_report(out_dir)
    except Exception:  # noqa: BLE001 - the push already happened
        log.exception("the publishing report could not be rebuilt")
        report = None
    return links, report


def links_path(out_dir: Path, release: str, dry_run: bool = False) -> Path:
    """Where a push-only run writes its links. A dry run gets its own file, as
    it gets its own ledger, so it never replaces the links of a real push."""
    stem = "upstream-md-pr-links-dryrun" if dry_run else "upstream-md-pr-links"
    return Path(out_dir) / f"{stem}-{release}.md"


def write_links(path: Path, release: str, results: List[PublishResult],
                targets: Dict, dry_run: bool = False) -> Path:
    """Per package: the link that opens its pull request, and what it fills in.

    `results` are the entries to list: every branch the ledger has waiting
    (BRANCH_PUSHED) and, for a dry run, the links its pushes would give.

    The title and description are repeated in full, because a description too
    long for a URL is left out of the link and has to be pasted - and ARCoS CI
    rejects a pull request whose description lost its three sections.
    """
    linked = [r for r in results if r.status in _LINKED and r.pr_create_url]
    if dry_run:
        intro = ('Dry run: nothing was pushed by this run. A link marked '
                 '"Pushed: no" works only once an apply has pushed its branch.')
    else:
        intro = ("These branches were pushed and no pull request was opened. "
                 "Open each one with its link; the person opening it needs "
                 "access to the repository. Keep the description's Problem "
                 "Description :, Root Cause : and Fix Details : sections - "
                 "ARCoS CI checks for them.")
    lines = [f"# debian/upstream.md pull request links - {release}", "", intro, ""]
    if not linked:
        lines += ["No branch is waiting for a pull request.", ""]
    for r in linked:
        body = _description(r, targets.get(r.package))
        lines += [
            f"## {r.package}",
            "",
            f"- Repository: {r.repository}",
            f"- Base branch: `{r.base_branch}`",
            f"- Branch: `{r.branch}`",
            f"- Commit: `{r.commit or ''}`",
            "- Pushed: yes - waiting for a pull request" if r.pushed else
            "- Pushed: no - the link works once an apply pushes the branch",
            f"- Link: {r.pr_create_url}",
        ]
        if r.compare_url:
            lines += [f"- Compare Link: {r.compare_url}",
                      f"- debian/upstream.md: {r.file_url}"]
        if "&body=" not in r.pr_create_url:
            lines.append("- The description is too long for the link: paste "
                         "it from below.")
        lines += ["", "Title:", "", "```text", r.title, "```", "", "Description:", ""]
        if body is None:
            lines += ["Not available: it was too long for the link, and the "
                      "package is not eligible in the current plan, so it "
                      "cannot be rebuilt. Regenerate the plan with the package "
                      "and run this again.", ""]
            continue
        # Four backticks: the description has a fenced block of its own.
        lines += ["````markdown", body, "````", ""]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return path


def _description(entry: PublishResult, target) -> Optional[str]:
    """The description `entry`'s link fills in; when the link could not hold
    it, the one its target gives today; None when there is neither."""
    from .services.upstream_md_publisher import pull_request_body

    for part in urlsplit(entry.pr_create_url or "").query.split("&"):
        name, _, value = part.partition("=")
        if name == "body":
            return unquote(value)
    if target is not None:
        return pull_request_body(target, entry)
    return None


def _print_results(results: List[PublishResult], show_diff: bool,
                   targets: Optional[Dict] = None) -> None:
    from .services.github_service import bare_pull_request_link
    from .services.upstream_md_service import review_flags

    if not results:
        return
    print("\n| Package | Status | Branch | Commit | PR URL | Error |")
    print("|---|---|---|---|---|---|")
    for r in results:
        # A push-only link with its description runs to kilobytes; the table
        # gets the page itself, and the links file the full one.
        if r.pull_request and r.pull_request.url:
            url = r.pull_request.url
        elif r.status in _LINKED:
            url = bare_pull_request_link(r.pr_create_url)
        else:
            url = ""
        print(f"| {r.package} | {r.status.value} | {r.branch} | "
              f"{(r.commit or '')[:12]} | {url} | {r.error or ''} |")
    for r in results:
        if r.status not in _LINKED:
            continue
        print(f"\n{r.package}: {r.title}")
        print(f"  {r.repository} {r.base_branch} @ {r.base_sha} -> "
              f"{r.branch} @ {r.commit}")
        if r.base_moved:
            print(f"  note: {r.base_branch} has moved past the pinned commit "
                  f"{(r.pinned_commit or '')[:12]}")
        # What a reviewer should check; never a reason to stop.
        target = (targets or {}).get(r.package)
        if target is not None and target.resolution is not None:
            for flag in review_flags(target.resolution):
                print(f"  review: {flag.code} - {flag.detail}")
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
                      help="push branches (never opens a pull request)")
    parser.add_argument(
        "--confirm", action="append",
        help="with --apply: the package(s) you expect to be pushed; must match",
    )
    parser.add_argument(
        "--confirm-count", type=int,
        help="with --apply: how many packages you expect to be pushed",
    )
    parser.add_argument(
        "--push-only", action="store_true",
        help="accepted for older command lines: every apply pushes the branch "
             "and opens no pull request",
    )
    parser.add_argument("--title-prefix", default="",
                        help='e.g. "BR-123: " for a Jira id')
    parser.add_argument("--limit", type=int,
                        help="process at most this many packages")
    parser.add_argument("--stop-after-failures", type=int, default=3,
                        help="stop after this many consecutive failures (0: never)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="only packages whose last result was a failure")
    parser.add_argument("--recheck", action="store_true",
                        help="also revisit packages that already have a PR "
                             "(and, with --push-only, branches already pushed)")
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
        title_prefix=args.title_prefix, limit=args.limit,
        stop_after_failures=args.stop_after_failures,
        retry_failed=args.retry_failed, recheck=args.recheck,
        exclude=args.exclude, show_diff=args.show_diff,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="apm.publish_upstream_md",
        description="Push the reviewed debian/upstream.md files, one branch "
                    "per package; never opens a pull request. A dry run unless "
                    "--apply is given.",
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
