#!/usr/bin/env python3
"""Check the debian/upstream.md results ledger after a publish run.

    python scripts/verify_pr_ledger.py [release]

Invariants that must hold whatever happened during the run:
  - every branch the tool created is under upstream-metadata/
  - no recorded branch is the branch it targets
  - every PR_OPENED / PR_EXISTS entry has a pushed commit and a PR URL
  - every pushed entry names a full commit SHA

It also reports how many of the plan's packages the ledger accounts for; a
partial rollout (--limit, --package) is expected to leave some out.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + (f"  -> {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main(release: str) -> int:
    ledger_path = ROOT / "out" / f"upstream-md-pr-results-{release}.json"
    plan_path = ROOT / "out" / f"upstream-md-plan-{release}.json"
    if not ledger_path.exists():
        print(f"no ledger at {ledger_path}", file=sys.stderr)
        return 2
    entries = json.loads(ledger_path.read_text())["entries"]

    branches = [e for e in entries if e.get("branch")]
    outside = [e["package"] for e in branches
               if not e["branch"].startswith("upstream-metadata/")]
    check("every branch is under upstream-metadata/", not outside,
          ", ".join(outside))
    onto_base = [e["package"] for e in branches
                 if e["branch"] == e.get("base_branch")]
    check("no branch is the branch it targets", not onto_base,
          ", ".join(onto_base))

    opened = [e for e in entries if e["status"] in ("PR_OPENED", "PR_EXISTS")]
    incomplete = [
        e["package"] for e in opened
        if not e.get("pushed") or not e.get("commit")
        or not (e.get("pull_request") or {}).get("url")
    ]
    check("every open PR has a pushed commit and a URL", not incomplete,
          ", ".join(incomplete))
    bad_sha = [e["package"] for e in entries if e.get("pushed")
               and not re.fullmatch(r"[0-9a-f]{40}", e.get("commit") or "")]
    check("every pushed entry names a full commit", not bad_sha,
          ", ".join(bad_sha))

    packages = [e["package"] for e in entries]
    check("one entry per package", len(packages) == len(set(packages)))

    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        planned = {e["package"] for e in plan.get("entries", [])} | \
            {s["package"] for s in plan.get("skipped", [])}
        missing = sorted(planned - set(packages))
        print(f"  info  ledger accounts for {len(planned) - len(missing)} of "
              f"{len(planned)} plan packages"
              + (f"; not yet: {', '.join(missing)}" if missing else ""))

    counts = {}
    for e in entries:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    print()
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("FAILURES:", ", ".join(failures) if failures else "none")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "bookworm"))
