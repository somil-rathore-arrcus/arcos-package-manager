#!/usr/bin/env python3
"""Check the generated debian/upstream.md files against the mapping they came from.

The whole point of generating them is that the committed record and the tool
cannot disagree. This re-reads the files as a stranger would and holds them
against the release CSV, so a renderer that quietly writes a branch the mapping
does not record - or a guess where the mapping has none - is caught before
anything is proposed.

Each file must be exactly two fields, "Upstream Source" and "Current Upstream
Branch". Standalone on purpose: it does not import apm, so a bug in the renderer
cannot also be a bug in the check. A tag-only row's branch is re-derived here
from the probe measurements in upstream-resolutions.json, beside the CSV.

    ./.venv/bin/python scripts/verify_upstream_md.py [bookworm]
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional


def metadata_branch(name: str) -> bool:
    """debian-upstream-tracking, debian-upstream-tracking-<release>, or a name
    under debian-upstream-tracking/ or upstream-metadata/: the publisher's rule,
    restated rather than imported."""
    return name == "debian-upstream-tracking" or bool(
        re.fullmatch(r"debian-upstream-tracking-[a-z][a-z0-9]{2,}", name)) or any(
        name.startswith(prefix) and len(name) > len(prefix)
        for prefix in ("debian-upstream-tracking/", "upstream-metadata/"))

ROOT = Path(__file__).resolve().parents[1]

SOURCE = "Upstream Source"
BRANCH = "Current Upstream Branch"
# The renderer's fixed N/A sentences, restated rather than imported.
NO_BRANCH = "N/A - no upstream branch verified"
NOT_IN_FORK = " (the ARCoS fork does not contain this tag)"
FLAG_BRANCH_NOT_VERIFIED = "BRANCH_NOT_VERIFIED"
FLAG_BRANCH_FROM_TAG = "BRANCH_FROM_TAG"

_FIELD = re.compile(
    rf"^({re.escape(SOURCE)}|{re.escape(BRANCH)})\s*:\s*(.*?)\s*$"
)
# A ref the mapping holds without saying whether it is a branch or a tag.
_UNKNOWN_REF = re.compile(
    rf"{re.escape(NO_BRANCH)}; ref \S+ is not recorded as a branch or a tag"
)

failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + (f"  -> {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def parse_fields(text: str) -> Optional[Dict[str, str]]:
    """The two fields, or None if the file holds anything else or repeats one."""
    found: Dict[str, str] = {}
    for line in re.sub(r"\r\n?", "\n", text).split("\n"):
        if not line.strip():
            continue
        match = _FIELD.match(line)
        if match is None or match.group(1) in found:
            return None
        found[match.group(1)] = match.group(2)
    return found if set(found) == {SOURCE, BRANCH} else None


def shape_problem(package: str, text: str) -> Optional[str]:
    """Why a file is not the two-field file the generator writes, or None."""
    value = parse_fields(text)
    if value is None:
        return f"{package}: not exactly the two fields {SOURCE!r} and {BRANCH!r}"
    if text != f"{SOURCE}: {value[SOURCE]}\n\n{BRANCH}: {value[BRANCH]}\n":
        return (f"{package}: not laid out as generated (one blank line between "
                f"the fields, one trailing newline)")
    return None


def held_branch(row: dict, tag: str, candidates) -> Optional[str]:
    """The branch a tag-only row's file names instead of N/A, or None.

    The renderer's rule, restated: the upstream default branch, or the branch
    overrides.yaml names, that the ancestry probe measured against the same
    ARCoS commit, when it shares history with the fork and the fork has no more
    commits outside it than outside the tag. Fewest commits outside wins, then
    the named branch.
    `candidates` are the row's probe measurements from upstream-resolutions.json;
    without them nothing can be derived, so only the N/A forms are allowed.
    """
    url = (row.get("Upstream Repository") or "").strip()
    measured = [c for c in candidates or []
                if (c.get("repository") or "").strip() == url and url
                and c.get("shares_history") and c.get("arcos_only") is not None]
    tagged = [c for c in measured
              if c.get("kind") == "tag" and (c.get("ref") or "").strip() == tag]
    if not tagged:
        return None
    branch = [c for c in measured
              if c.get("strategy") in ("default_branch", "curated")
              and c.get("kind") == "branch"
              and (c.get("ref") or "").strip() not in ("", "HEAD")
              and c["arcos_only"] <= tagged[0]["arcos_only"]]
    if not branch:
        return None
    best = min(branch, key=lambda c: (c["arcos_only"], c.get("strategy") != "curated"))
    return best["ref"].strip()


def field_problems(package: str, row: dict, value: Dict[str, str],
                   candidates=None) -> List[str]:
    """Where a file's two values disagree with its mapping row."""
    problems = []
    status = (row.get("Resolution Status") or "").strip()
    source, branch = value[SOURCE], value[BRANCH]
    if status != "VERIFIED":
        # Only reachable with --include-needs-review: the candidate upstream
        # must not appear, in either field.
        unverified = f"N/A - not verified (status {status})"
        if source != unverified:
            problems.append(f"{package} {SOURCE}: file={source!r} want={unverified!r}")
        if branch != unverified:
            problems.append(f"{package} {BRANCH}: file={branch!r} want={unverified!r}")
        return problems

    want = (row.get("Upstream Repository") or "").strip() or "N/A"
    if source != want:
        problems.append(f"{package} {SOURCE}: file={source!r} mapping={want!r}")

    mapped_branch = (row.get("Upstream Branch") or "").strip()
    mapped_tag = (row.get("Upstream Tag") or "").strip()
    if mapped_branch:
        if branch != mapped_branch:
            problems.append(f"{package} {BRANCH}: file={branch!r} "
                            f"mapping={mapped_branch!r}")
    elif mapped_tag:
        # A tag is not a branch: the file must say no branch was verified, and
        # name the tag rather than pass it off as one. Compared whole, so a
        # longer tag, an added branch or a fork note the row does not support
        # cannot hide behind the right prefix.
        held = held_branch(row, mapped_tag, candidates)
        allowed = [held] if held else _tag_values(row, mapped_tag)
        if branch not in allowed:
            problems.append(f"{package} {BRANCH}: file={branch!r} "
                            f"want={' or '.join(repr(a) for a in allowed)}")
    elif branch != NO_BRANCH and not _UNKNOWN_REF.fullmatch(branch):
        problems.append(f"{package} {BRANCH}: file={branch!r}, mapping records "
                        f"no branch or tag")
    return problems


def _tag_values(row: dict, tag: str) -> List[str]:
    """The branch values a tag-only row allows, most specific first.

    The fork note belongs exactly where the row says the fork lacks the base tag
    and that tag is the one named. A CSV without those columns cannot say, so
    either form is accepted there.
    """
    plain = f"{NO_BRANCH}; upstream tag {tag}"
    noted = plain + NOT_IN_FORK
    if "ARCoS Contains Base Tag" not in row or "Upstream Base Tag" not in row:
        return [plain, noted]
    lacks = (row.get("ARCoS Contains Base Tag") or "").strip().lower() == "no"
    named = (row.get("Upstream Base Tag") or "").strip() == tag
    return [noted] if lacks and named else [plain]


def plan_entry_problems(entry: dict, text: str) -> List[str]:
    """Whether the plan records what the file says, and flags what it should."""
    package = entry["package"]
    if "upstream_source" not in entry or "current_upstream_branch" not in entry:
        return [f"{package}: the plan predates the two-field file; regenerate"]
    value = parse_fields(text) or {}
    problems = []
    if entry["upstream_source"] != value.get(SOURCE):
        problems.append(f"{package} upstream_source: plan="
                        f"{entry['upstream_source']!r} file={value.get(SOURCE)!r}")
    if entry["current_upstream_branch"] != value.get(BRANCH):
        problems.append(f"{package} current_upstream_branch: plan="
                        f"{entry['current_upstream_branch']!r} "
                        f"file={value.get(BRANCH)!r}")
    flagged = FLAG_BRANCH_NOT_VERIFIED in {
        f.get("code") for f in entry.get("flags") or []
    }
    unverified = (value.get(BRANCH) or "N/A").startswith("N/A")
    if flagged != unverified:
        problems.append(
            f"{package}: {FLAG_BRANCH_NOT_VERIFIED} is "
            f"{'set' if flagged else 'missing'} but the branch field is "
            f"{value.get(BRANCH)!r}"
        )
    # A branch derived from a tag is written, but always flagged for review.
    from_tag = FLAG_BRANCH_FROM_TAG in {
        f.get("code") for f in entry.get("flags") or []
    }
    derived = bool(entry.get("upstream_tag")) and not unverified
    if from_tag != derived:
        problems.append(
            f"{package}: {FLAG_BRANCH_FROM_TAG} is "
            f"{'set' if from_tag else 'missing'} but the plan records tag "
            f"{entry.get('upstream_tag') or 'none'} and branch field "
            f"{value.get(BRANCH)!r}"
        )
    return problems


def main(release: str) -> int:
    mapping_path = ROOT / "out" / f"upstream-mapping-{release}.csv"
    md_dir = ROOT / "out" / f"upstream-md-{release}"
    plan_path = ROOT / "out" / f"upstream-md-plan-{release}.json"

    if not mapping_path.exists():
        print(f"no mapping at {mapping_path}", file=sys.stderr)
        return 2
    if not md_dir.exists():
        print(f"no generated files at {md_dir}", file=sys.stderr)
        return 2

    rows = {r["Package"]: r for r in csv.DictReader(mapping_path.open())}
    # The probe's measurements, which the CSV does not carry.
    resolutions_path = ROOT / "out" / "upstream-resolutions.json"
    measured = {}
    if resolutions_path.exists():
        for item in json.loads(resolutions_path.read_text()).get("resolutions", []):
            if item.get("debian_release") == release:
                measured[item["package"]] = item.get("candidates") or []
    generated = {
        path.parent.parent.name: path
        for path in md_dir.glob("*/debian/upstream.md")
    }
    texts = {p: path.read_bytes().decode("utf-8") for p, path in generated.items()}

    check("every generated file has a mapping row",
          all(p in rows for p in generated),
          ", ".join(sorted(set(generated) - set(rows))))

    # A file for a package with nothing to track would be fiction.
    invented = sorted(
        p for p in generated if rows.get(p, {}).get("Resolution Status") == "NO_UPSTREAM"
    )
    check("no file was generated for a NO_UPSTREAM package", not invented,
          ", ".join(invented))

    shape, mismatches = [], []
    for package, text in sorted(texts.items()):
        problem = shape_problem(package, text)
        if problem:
            shape.append(problem)
        value = parse_fields(text)
        if value is not None and package in rows:
            mismatches += field_problems(package, rows[package], value,
                                         measured.get(package))

    check("every file has exactly the two fields and nothing else", not shape,
          "; ".join(shape[:5]))
    check("every field matches the mapping row", not mismatches,
          "; ".join(mismatches[:5]))

    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        check("the plan pushed nothing", plan.get("pushed") is False)
        check("the plan created no pull request",
              plan.get("pull_requests_created") is False)
        planned = {e["package"] for e in plan["entries"]}
        check("the plan covers exactly the generated files",
              planned == set(generated),
              f"plan={len(planned)} files={len(generated)}")
        bad_base = [
            e["package"] for e in plan["entries"]
            if e["branch"] == e["base_branch"]
        ]
        check("no planned branch is the branch it targets", not bad_base,
              ", ".join(bad_base))
        outside = [
            e["package"] for e in plan["entries"]
            if not metadata_branch(e["branch"])
        ]
        check("every planned branch is one this workflow creates", not outside,
              ", ".join(outside))

        recorded = []
        for entry in plan["entries"]:
            if entry["package"] in texts:
                recorded += plan_entry_problems(entry, texts[entry["package"]])
        check("the plan records each file's values and flags every unverified "
              "branch", not recorded, "; ".join(recorded[:5]))

        # The publisher commits these bytes only if they still hash to what
        # the plan recorded; say so here first rather than at push time.
        hashed = [e for e in plan["entries"] if e.get("sha256")]
        if hashed:
            changed = [
                e["package"] for e in hashed
                if e["package"] in generated and hashlib.sha256(
                    generated[e["package"]].read_bytes()
                ).hexdigest() != e["sha256"]
            ]
            check("every generated file still matches the plan's hash",
                  not changed, ", ".join(changed))
        else:
            print("  note  the plan has no content hashes; regenerate it before "
                  "publish-upstream-md")

    print()
    print(f"generated: {len(generated)}   mapping rows: {len(rows)}")
    print("FAILURES:", ", ".join(failures) if failures else "none")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "bookworm"))
