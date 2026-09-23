#!/usr/bin/env python3
"""Check the produced mapping against what we know must be true.

Run after `apm resolve`. These are the invariants that caught real bugs during
development, so they are worth re-running whenever the resolution logic changes.

    ./.venv/bin/python scripts/verify_output.py [out/upstream-mapping.csv]
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "out" / "upstream-mapping.csv"

failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + (f"  -> {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main(path: Path) -> int:
    rows = list(csv.DictReader(path.open()))
    by_release = Counter(r["Debian Release"] for r in rows)

    check("every row has a Category", all(r["Category"] for r in rows))
    check("every row has a Status", all(r["Status"] for r in rows))
    check("every row has an ARCoS commit", all(r["ARCoS Commit"] for r in rows))
    check("every row has an ARCoS link", all(r["ARCoS Link"] for r in rows))

    check(
        "bookworm has more packages than trixie (eight are bookworm-only)",
        by_release.get("bookworm", 0) > by_release.get("trixie", 0),
        str(dict(by_release)),
    )

    # The kernel: a fork pinned to 6.1 compared against linux-6.12.y once
    # reported a backlog of 182,919 commits. The series must follow the release.
    kernel = {r["Debian Release"]: r for r in rows if r["Package"] == "linux"}
    for release, expected in (("bookworm", "linux-6.1.y"), ("trixie", "linux-6.12.y")):
        row = kernel.get(release)
        check(
            f"linux/{release} tracks {expected}",
            bool(row) and row["Upstream Ref"] == expected,
            row["Upstream Ref"] if row else "missing",
        )
    if len(kernel) == 2:
        check(
            "the kernel is pinned to a different commit per release",
            kernel["bookworm"]["ARCoS Commit"] != kernel["trixie"]["ARCoS Commit"],
            f'{kernel["bookworm"]["ARCoS Commit"]} vs {kernel["trixie"]["ARCoS Commit"]}',
        )

    # A package with no upstream is a finished answer, never a failure.
    misfiled = sorted({
        r["Package"] for r in rows
        if r["Category"] in ("arrcus_native", "vendor")
        and r["Status"] != "NO_UPSTREAM"
    })
    check("arrcus_native and vendor packages report NO_UPSTREAM", not misfiled,
          ", ".join(misfiled))

    unresolved = sorted({r["Package"] for r in rows if r["Status"] == "UNRESOLVED"})
    check("nothing is left UNRESOLVED", not unresolved, ", ".join(unresolved))

    # An upstream nobody contacted is a guess.
    unverified = sorted({
        r["Package"] for r in rows
        if r["Status"] == "VERIFIED" and not r["Upstream Commit"]
    })
    check("every VERIFIED row resolved an upstream commit", not unverified,
          ", ".join(unverified))

    nolink = sorted({
        r["Package"] for r in rows
        if r["Upstream Repository"] and not r["Upstream Link"]
    })
    check("every resolved upstream has a link", not nolink, ", ".join(nolink))

    # Both were silent mis-selections caused by substring version matching.
    series_bugs = []
    for row in rows:
        ref, version = row["Upstream Ref"], row["Debian Version"]
        m = re.match(r"^(?:\d+:)?(\d+)\.(\d+)", version or "")
        if not (m and ref and re.search(r"(?<!\d)\d+[._]\d+(?!\d)", ref)):
            continue
        want = re.compile(r"(?<!\d)%s[._]%s(?!\d)" % (m.group(1), m.group(2)))
        if not want.search(ref):
            series_bugs.append(f'{row["Package"]}/{row["Debian Release"]}: '
                               f'{version} -> {ref}')
    check("no row tracks a version series that contradicts its Debian version",
          not series_bugs, "; ".join(series_bugs))

    # A merge base means the comparison is real rather than nominal.
    proven = [r for r in rows if r["Merge Base"]]
    check("some rows have a proven common ancestor", bool(proven),
          f"{len(proven)} of {len(rows)}")
    bad_counts = [
        f'{r["Package"]}/{r["Debian Release"]}' for r in proven
        if not (r["Commits Behind"] or "").isdigit()
    ]
    check("every proven row carries a commit-behind count", not bad_counts,
          ", ".join(bad_counts))

    # --- the Bookworm/aminor requirement -------------------------------------
    # Every row that is not a finished VERIFIED answer has to say why, and no
    # row may carry an upstream it did not establish.
    unexplained = sorted({
        f'{r["Package"]}/{r["Debian Release"]}' for r in rows
        if r["Status"] != "VERIFIED" and not (r.get("Reason") or r.get("Notes"))
    })
    check("every non-VERIFIED row records a reason", not unexplained,
          ", ".join(unexplained))

    no_evidence = sorted({
        f'{r["Package"]}/{r["Debian Release"]}' for r in rows
        if r["Status"] == "VERIFIED" and not (r.get("Evidence") or "").strip()
    })
    check("every VERIFIED row carries evidence", not no_evidence,
          ", ".join(no_evidence))

    no_ref = sorted({
        f'{r["Package"]}/{r["Debian Release"]}' for r in rows
        if r["Status"] == "VERIFIED"
        and not (r.get("Upstream Branch") or r.get("Upstream Tag")
                 or r.get("Upstream Ref"))
    })
    check("every VERIFIED row names a branch or a tag", not no_ref,
          ", ".join(no_ref))

    invented = sorted({
        f'{r["Package"]}/{r["Debian Release"]}' for r in rows
        if r["Status"] == "NO_UPSTREAM" and r["Upstream Repository"]
    })
    check("no NO_UPSTREAM row carries an upstream repository", not invented,
          ", ".join(invented))

    both = sorted({
        f'{r["Package"]}/{r["Debian Release"]}' for r in rows
        if r.get("Upstream Branch") and r.get("Upstream Tag")
    })
    check("no row is both a branch and a tag", not both, ", ".join(both))

    # The Debian packaging repository is recorded, never used as the upstream.
    packaging_as_upstream = sorted({
        f'{r["Package"]}/{r["Debian Release"]}' for r in rows
        if r["Upstream Repository"]
        and r["Upstream Repository"] == r.get("Vcs-Git (packaging)")
        and r.get("Origin Kind") != "debian_packaging"
    })
    check("a packaging repository used as upstream is labelled as one",
          not packaging_as_upstream, ", ".join(packaging_as_upstream))

    print()
    print(f"rows: {len(rows)}   " + "   ".join(
        f"{k}={v}" for k, v in sorted(Counter(r["Status"] for r in rows).items())
    ))
    print("FAILURES:", ", ".join(failures) if failures else "none")
    return 1 if failures else 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not target.exists():
        print(f"no mapping at {target}; run `apm resolve` first", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(target))
