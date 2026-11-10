"""The consolidated debian/upstream.md publishing report, Bookworm and Trixie.

    apm upstream-md-report              # rebuild from the plans and the records
    apm upstream-md-report --verify     # and check every branch on GitHub first

out/upstream-md-publishing-report.md (with a .json beside it) is rebuilt after
every push - by `apm publish-upstream-md --apply` and by the dashboard alike -
from what the tool recorded, so it never needs editing by hand. Each release
has its own section:

  pushed       the branches waiting for a pull request, each with the direct
               link to its debian/upstream.md and the Compare Link a person
               opens the pull request from
  covered      packages another release's branch already proposes, byte for
               byte, so they have no branch of their own
  not pushed   every other package of the plan, with the reason

No pull request is opened by this tool; the report says which links a person
opens them from. --verify reads each branch back through the configured git
host (ls-remote, then a shallow blobless fetch): the branch is at the recorded
commit, that commit changes only debian/upstream.md, and the file is the
reviewed one. The result is kept in upstream-md-publishing-verification.json
and shown in the report until a branch's commit changes.

Dated reports a person wrote (upstream-md-branches-<date>.md) are never read
or written here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .domain.enums import PublishStatus
from .domain.models import PublishResult
from .services.publish_ledger import _atomic_write

log = logging.getLogger(__name__)

REPORT_STEM = "upstream-md-publishing-report"
VERIFICATION_FILE = "upstream-md-publishing-verification.json"
UPSTREAM_MD_PATH = "debian/upstream.md"

_PLAN_PREFIX = "upstream-md-plan-"
_LEDGER_PREFIX = "upstream-md-pr-results-"


# -- inputs -------------------------------------------------------------------

def releases_in(out_dir: Path, order: Sequence[str] = ()) -> List[str]:
    """Every release with a plan or a ledger, in settings.yaml's order."""
    found = set()
    for prefix in (_PLAN_PREFIX, _LEDGER_PREFIX):
        for path in Path(out_dir).glob(f"{prefix}*.json"):
            found.add(path.stem[len(prefix):])
    rank = {name: i for i, name in enumerate(order)}
    return sorted(found, key=lambda r: (r not in rank, rank.get(r, 0), r))


def _plan(out_dir: Path, release: str) -> dict:
    path = Path(out_dir) / f"{_PLAN_PREFIX}{release}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _records(out_dir: Path, release: str) -> Dict[str, PublishResult]:
    from .services.publish_ledger import PublishLedger

    return {e.package: e for e in PublishLedger.for_release(out_dir, release).entries()}


def load_verification(out_dir: Path) -> Dict[str, dict]:
    path = Path(out_dir) / VERIFICATION_FILE
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {f"{v['release']}/{v['package']}": v for v in payload.get("branches", [])}


# -- building -----------------------------------------------------------------

def _row(record: PublishResult) -> dict:
    return {
        "package": record.package,
        "release": record.release,
        "status": record.status.value,
        "repository": record.repository,
        "branch": record.branch,
        "target": record.base_branch,
        "commit": record.commit or "",
        "file_url": record.file_url or "",
        "compare_url": record.compare_url or "",
        "pull_request": record.pull_request.url if record.pull_request else "",
        "covered_by": record.covered_by or "",
        "recorded": record.updated_at.isoformat() if record.updated_at else "",
    }


def build(out_dir: Path, exclude: Optional[Dict[str, str]] = None,
          order: Sequence[str] = ()) -> dict:
    """Every release's pushed, covered and not-pushed packages, as data."""
    exclude = exclude or {}
    verification = load_verification(out_dir)
    releases = {}
    for release in releases_in(out_dir, order):
        plan = _plan(out_dir, release)
        entries = {e["package"]: e for e in plan.get("entries", [])}
        skipped = {i["package"]: i for i in plan.get("skipped", [])}
        records = _records(out_dir, release)

        pushed, covered, not_pushed = [], [], []
        for name in sorted(records):
            record = records[name]
            if record.status is PublishStatus.COVERED:
                covered.append(_row(record))
            elif record.pushed and record.commit:
                row = _row(record)
                check = verification.get(f"{release}/{name}")
                if check and check.get("commit") == record.commit:
                    row["verified"] = check
                pushed.append(row)
        done = {r["package"] for r in pushed + covered}
        for name in sorted(set(entries) | set(skipped) | set(records)):
            if name in done:
                continue
            record = records.get(name)
            if record is not None:
                status, reason = record.status.value, record.error or ""
            elif name in skipped:
                item = skipped[name]
                status, reason = item.get("status", ""), item.get("reason", "")
            elif name in exclude:
                status, reason = PublishStatus.SKIPPED_EXCLUDED.value, exclude[name]
            elif entries[name].get("outcome") == "NO_CHANGE":
                status, reason = "NO_CHANGE", "the target branch already has this file"
            else:
                status, reason = "NOT_PUSHED", "eligible in the reviewed plan; not pushed yet"
            entry = entries.get(name, {})
            not_pushed.append({
                "package": name, "release": release, "status": status,
                "reason": " ".join(str(reason).split()),
                "repository": entry.get("arcos_repository", ""),
                "target": entry.get("arcos_branch", "")
                or entry.get("base_branch", ""),
            })
        releases[release] = {
            "plan": bool(plan), "pushed": pushed, "covered": covered,
            "not_pushed": not_pushed,
        }
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "releases": releases,
    }


def _cell(value) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _verified(row: dict) -> str:
    check = row.get("verified")
    if not check:
        return "not checked"
    when = check.get("checked_at", "")[:10]
    if check.get("ok"):
        return f"yes ({when})"
    return f"NO ({when}): " + "; ".join(check.get("problems", []))


def render(data: dict) -> str:
    releases = data["releases"]
    lines = [
        "# debian/upstream.md publishing report",
        "",
        f"Generated {data['generated_at']} from the reviewed plans and the "
        "publishing records. Rebuilt automatically after every push; edit "
        "nothing here by hand.",
        "",
        "**This tool opens no pull request.** Each branch below waits for a "
        "person to open its pull request from the Compare Link. No branch is "
        "ever force-pushed, and the target branches (`aminor`, "
        "`aminor-trixie`, ...) are never written to.",
        "",
        "| Release | Pushed | Covered by another release | Not pushed | Verified on GitHub |",
        "|---|---|---|---|---|",
    ]
    for release, part in releases.items():
        verified = sum(1 for r in part["pushed"] if (r.get("verified") or {}).get("ok"))
        lines.append(f"| {release.capitalize()} | {len(part['pushed'])} | "
                     f"{len(part['covered'])} | {len(part['not_pushed'])} | "
                     f"{verified}/{len(part['pushed'])} |")
    lines.append("")

    for release, part in releases.items():
        targets = sorted({r["target"] for r in part["pushed"] + part["covered"]})
        lines += [f"## {release.capitalize()}", ""]
        if targets:
            lines += ["Target branch(es): " + ", ".join(f"`{t}`" for t in targets), ""]
        if not part["plan"]:
            lines += ["No reviewed plan for this release.", ""]

        lines += [f"### Pushed - waiting for a pull request ({len(part['pushed'])})", ""]
        if part["pushed"]:
            lines += [
                "| Package | Repository | Branch → target | Commit | Status | "
                "debian/upstream.md | Compare Link | Verified on GitHub |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for r in part["pushed"]:
                status = r["status"]
                if r["pull_request"]:
                    status += f" ({r['pull_request']})"
                lines.append(
                    f"| {r['package']} | {r['repository']} | `{r['branch']}` → "
                    f"`{r['target']}` | `{r['commit'][:12]}` | {_cell(status)} | "
                    f"{r['file_url']} | {r['compare_url']} | {_cell(_verified(r))} |")
        else:
            lines.append("None.")
        lines.append("")

        lines += [f"### Covered by another release's branch ({len(part['covered'])})", ""]
        if part["covered"]:
            lines += [
                "The same file is already proposed to the same repository and "
                "target branch, so these packages have no branch of their own; "
                "the covering branch's pull request serves both releases.",
                "",
                "| Package | Repository | Covered by | Commit | debian/upstream.md | "
                "Compare Link |",
                "|---|---|---|---|---|---|",
            ]
            for r in part["covered"]:
                lines.append(
                    f"| {r['package']} | {r['repository']} | {r['covered_by']}: "
                    f"`{r['branch']}` → `{r['target']}` | `{r['commit'][:12]}` | "
                    f"{r['file_url']} | {r['compare_url']} |")
        else:
            lines.append("None.")
        lines.append("")

        lines += [f"### Not pushed ({len(part['not_pushed'])})", ""]
        if part["not_pushed"]:
            lines += ["| Package | Status | Reason |", "|---|---|---|"]
            for r in part["not_pushed"]:
                lines.append(f"| {r['package']} | {r['status']} | {_cell(r['reason'])} |")
        else:
            lines.append("None.")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write(out_dir: Path, exclude: Optional[Dict[str, str]] = None,
          order: Optional[Sequence[str]] = None) -> Path:
    """Rebuild the report from what is recorded. Returns the Markdown path."""
    if exclude is None or order is None:
        from .config import load_settings

        settings = load_settings()
        exclude = settings.upstream_md_publish["exclude"] if exclude is None else exclude
        order = settings.release_ids if order is None else order
    data = build(out_dir, exclude, order)
    base = Path(out_dir) / REPORT_STEM
    _atomic_write(base.with_suffix(".json"), json.dumps(data, indent=2) + "\n")
    _atomic_write(base.with_suffix(".md"), render(data))
    return base.with_suffix(".md")


# -- checking on GitHub -------------------------------------------------------

def verify_branch(workspace, url: str, record: PublishResult,
                  expected_sha256: Optional[str]) -> dict:
    """Read one pushed branch back. Never writes to the remote."""
    problems: List[str] = []
    tip = workspace.remote_ref(url, f"refs/heads/{record.branch}")
    target_tip = workspace.remote_ref(url, f"refs/heads/{record.base_branch}")
    content = None
    if tip is None:
        problems.append(f"{record.branch} is not on {record.repository}")
    elif tip != record.commit:
        problems.append(f"{record.branch} is at {tip[:12]}, not the recorded "
                        f"{(record.commit or '')[:12]}")
    else:
        workspace.fetch_side("verify", url, f"refs/heads/{record.branch}", depth=2)
        changed = workspace.changed_paths(f"{tip}^", tip)
        if changed != [UPSTREAM_MD_PATH]:
            problems.append(f"the commit changes {changed}, not only {UPSTREAM_MD_PATH}")
        content = workspace.read_file(UPSTREAM_MD_PATH, rev=tip)
        if content is None:
            problems.append(f"{UPSTREAM_MD_PATH} is missing")
        elif expected_sha256 and \
                hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_sha256:
            problems.append(f"{UPSTREAM_MD_PATH} is not the reviewed file")
    return {
        "release": record.release, "package": record.package,
        "repository": record.repository, "branch": record.branch,
        "target": record.base_branch, "commit": record.commit,
        "ok": not problems, "problems": problems,
        "target_tip": target_tip,
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def verify(out_dir: Path, workspaces, order: Sequence[str] = (),
           progress=None) -> List[dict]:
    """Check every pushed and covering branch the records name; keep the result."""
    checks: List[dict] = []
    workspace = workspaces.scratch("verify-publishing")
    try:
        for release in releases_in(out_dir, order):
            plan = {e["package"]: e for e in _plan(out_dir, release).get("entries", [])}
            for name, record in sorted(_records(out_dir, release).items()):
                if not (record.pushed and record.commit) or \
                        record.status is PublishStatus.COVERED:
                    continue
                if progress:
                    progress(release, name)
                workspace.destroy()
                workspace.ensure()
                url = f"ssh://git@github.com/{record.repository}.git"
                try:
                    checks.append(verify_branch(
                        workspace, url, record, plan.get(name, {}).get("sha256")))
                except Exception as exc:  # noqa: BLE001 - recorded, never fatal
                    log.warning("verifying %s/%s failed: %s", release, name, exc)
                    checks.append({
                        "release": release, "package": name,
                        "repository": record.repository, "branch": record.branch,
                        "target": record.base_branch, "commit": record.commit,
                        "ok": False, "problems": [f"could not be read: {exc}"],
                        "checked_at": datetime.now(timezone.utc)
                        .replace(microsecond=0).isoformat(),
                    })
    finally:
        workspace.destroy()
    _atomic_write(Path(out_dir) / VERIFICATION_FILE,
                  json.dumps({"branches": checks}, indent=2) + "\n")
    return checks


# -- command line ---------------------------------------------------------------

def run(out_dir: Path = None, check: bool = False, container=None) -> int:
    from .config import ROOT

    out_dir = Path(out_dir or ROOT / "out")
    if check:
        from .services.container import Container

        container = container or Container()
        checks = verify(out_dir, container.workspaces,
                        order=container.settings.release_ids,
                        progress=lambda r, p: print(f"checking {r}/{p}", flush=True))
        bad = [c for c in checks if not c["ok"]]
        print(f"\nChecked {len(checks)} branch(es) on GitHub: "
              f"{len(checks) - len(bad)} as recorded, {len(bad)} not.")
        for c in bad:
            print(f"  {c['release']}/{c['package']}: {'; '.join(c['problems'])}")
    path = write(out_dir)
    print(f"\nReport: {path}\n        {path.with_suffix('.json')}")
    print("No pull request was created.")
    return 1 if check and any(not c["ok"] for c in checks) else 0
