"""Is an upstream change already in the ARCoS tree? Evidence in tiers.

patch-id is exact and cheap, but it answers only "is the same diff there". A
backport adapted to older code has a different diff by definition, so a
differing patch-id is not proof that a change is missing - and a loose textual
resemblance is not proof that it is present. Each commit gets the strongest
answer the evidence supports:

  DEFINITELY_PRESENT  an ARCoS commit says "(cherry picked from commit X)", or
                      carries a byte-identical change (patch-id --stable)
  PROBABLY_PRESENT    the change's post-image is already in the ARCoS tree
                      (git apply --check -R), or every line it adds is there
  MISSING             it applies cleanly and is not there, or none of its
                      lines are there; or ARCoS applied it and reverted it
  UNKNOWN             partially present, empty, not checked, or undecidable

Reverts are paired: an upstream commit reverted later upstream nets to nothing,
and a backport ARCoS reverted is no longer present.

The content checks run in one shell call against a private index built from
the ARCoS commit, so nothing is checked out and nothing is written.
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..domain.enums import Presence
from ..domain.models import DebianPatchReport
from ..gitio.workspace import GitWorkspaceError, LogEntry

log = logging.getLogger(__name__)

TRAILERS = (
    re.compile(r"\(cherry[- ]picked from commit ([0-9a-f]{7,40})\)", re.IGNORECASE),
    re.compile(r"^\s*\[\s*upstream commit ([0-9a-f]{7,40})\s*\]", re.I | re.M),
    re.compile(r"^\s*commit ([0-9a-f]{40}) upstream\.?\s*$", re.I | re.M),
    re.compile(
        r"^\s*(?:backport(?:ed)?\s+(?:of|from)|upstream[- ]commit)\s*:?\s*"
        r"(?:commit\s+)?([0-9a-f]{7,40})\b", re.I | re.M,
    ),
)
REVERT = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.IGNORECASE)
_SIGNIFICANT = 4


@dataclass
class PresenceResult:
    presence: Presence
    evidence: List[str] = field(default_factory=list)
    arcos_sha: Optional[str] = None
    reverts: Optional[str] = None
    reverted_by: Optional[str] = None


@dataclass
class Assessment:
    results: Dict[str, PresenceResult] = field(default_factory=dict)
    # ARCoS commits that carry an upstream change, so they are not ARCoS work.
    carriers: Dict[str, str] = field(default_factory=dict)
    reverted_pairs: List[Tuple[str, str]] = field(default_factory=list)
    content_checked: int = 0
    notes: List[str] = field(default_factory=list)


def _match_prefix(sha: str, prefixes: Dict[str, str]) -> Optional[str]:
    for prefix, value in prefixes.items():
        if len(prefix) >= 7 and sha.startswith(prefix):
            return value
    return None


class BackportDetector:
    def __init__(self, content_check_limit: int = 1000, timeout: int = 900) -> None:
        self.content_check_limit = content_check_limit
        self.timeout = timeout

    def assess(self, workspace, upstream: Sequence[LogEntry],
               arcos: Sequence[LogEntry], patch_equivalent: Dict[str, str],
               arcos_head: str, content: bool = True) -> Assessment:
        out = Assessment()

        claimed: Dict[str, str] = {}
        for entry in arcos:
            for pattern in TRAILERS:
                for match in pattern.finditer(entry.body or ""):
                    claimed.setdefault(match.group(1).lower(), entry.sha)
        arcos_reverts: Dict[str, str] = {}
        for entry in arcos:
            for match in REVERT.finditer(entry.body or ""):
                arcos_reverts.setdefault(match.group(1).lower(), entry.sha)

        upstream_shas = [e.sha for e in upstream]
        pending: List[str] = []
        for entry in upstream:
            sha = entry.sha
            carrier = _match_prefix(sha, claimed)
            how = f"ARCoS commit {carrier[:12]} records it as cherry-picked" \
                if carrier else None
            if carrier is None and sha in patch_equivalent:
                carrier = patch_equivalent[sha]
                how = f"ARCoS commit {carrier[:12]} has the identical patch-id"
            if carrier is not None:
                reverter = _match_prefix(carrier, arcos_reverts)
                if reverter:
                    out.results[sha] = PresenceResult(
                        Presence.MISSING,
                        [f"{how}, but ARCoS reverted it in {reverter[:12]}"],
                        arcos_sha=carrier,
                    )
                else:
                    out.results[sha] = PresenceResult(
                        Presence.DEFINITELY_PRESENT, [how], arcos_sha=carrier,
                    )
                    out.carriers[carrier] = sha
                continue
            pending.append(sha)

        checked = pending[: self.content_check_limit] if content else []
        if content and len(pending) > len(checked):
            out.notes.append(
                f"{len(pending) - len(checked)} commit(s) beyond the content-check "
                f"limit of {self.content_check_limit} are UNKNOWN"
            )
        if checked:
            try:
                verdicts = self._apply_checks(workspace, arcos_head, checked)
                undecided = [s for s in checked if verdicts.get(s) == "NEITHER"]
                lines = self._line_evidence(workspace, arcos_head, undecided) \
                    if undecided else {}
            except GitWorkspaceError as exc:
                log.warning("content checks failed: %s", exc)
                out.notes.append(f"content checks could not run: {exc}")
                verdicts, lines = {}, {}
            out.content_checked = len(verdicts)
            for sha in checked:
                out.results[sha] = _verdict(verdicts.get(sha), lines.get(sha))
        for sha in pending:
            if sha not in out.results:
                out.results[sha] = PresenceResult(
                    Presence.UNKNOWN,
                    ["not content-checked" + ("" if content else
                                               ": file contents unavailable")],
                )

        # Reverts within the upstream range: the pair nets to nothing.
        for entry in upstream:
            for match in REVERT.finditer(entry.body or ""):
                target = next((s for s in upstream_shas
                               if s.startswith(match.group(1).lower())), None)
                if target is None or target == entry.sha:
                    continue
                if out.results[target].presence in (
                        Presence.DEFINITELY_PRESENT, Presence.PROBABLY_PRESENT):
                    continue
                out.reverted_pairs.append((target, entry.sha))
                out.results[target].reverted_by = entry.sha
                out.results[target].evidence.append(
                    f"reverted upstream by {entry.sha[:12]}; applying neither is "
                    f"equivalent to applying both"
                )
                out.results[entry.sha].reverts = target
                out.results[entry.sha].evidence.append(
                    f"reverts {target[:12]}, which ARCoS does not carry"
                )
        return out

    # -- content checks ----------------------------------------------------

    def _apply_checks(self, workspace, arcos_head: str,
                      shas: Sequence[str]) -> Dict[str, str]:
        """REVERSE / FORWARD / NEITHER / EMPTY / ERROR for each commit."""
        script = _INDEX_PRELUDE.format(head=shlex.quote(arcos_head)) + (
            "for S in " + " ".join(shlex.quote(s) for s in shas) + "; do\n"
            '  if ! git diff-tree -p --binary --no-commit-id -r "$S^" "$S" > "$P" 2>/dev/null; then echo "$S ERROR"; continue; fi\n'
            '  if [ ! -s "$P" ]; then echo "$S EMPTY"; continue; fi\n'
            '  if git apply --cached --check -R "$P" 2>/dev/null; then echo "$S REVERSE";\n'
            '  elif git apply --cached --check "$P" 2>/dev/null; then echo "$S FORWARD";\n'
            '  else echo "$S NEITHER"; fi\n'
            "done\n"
        )
        result = workspace.shell(script, timeout=self.timeout)
        if not result.ok:
            raise GitWorkspaceError("apply checks failed", result.stderr.strip())
        verdicts = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2:
                verdicts[parts[0]] = parts[1]
        return verdicts

    def _line_evidence(self, workspace, arcos_head: str,
                       shas: Sequence[str]) -> Dict[str, Tuple[int, int, int]]:
        """(files present, files absent, files total) by added/removed lines."""
        diff = workspace.shell(
            "git log --no-walk=unsorted --no-color -p -U0 --format='APM-COMMIT %H' "
            + " ".join(shlex.quote(s) for s in shas),
            timeout=self.timeout,
        )
        if not diff.ok:
            raise GitWorkspaceError("git log -p failed", diff.stderr.strip())
        per_commit = _parse_u0(diff.stdout)
        paths = sorted({p for files in per_commit.values() for p in files})
        contents = cat_files(workspace, arcos_head, paths, self.timeout)
        evidence = {}
        for sha, files in per_commit.items():
            present = absent = 0
            for path, change in files.items():
                verdict = _file_verdict(change, contents.get(path))
                present += verdict == "present"
                absent += verdict == "absent"
            evidence[sha] = (present, absent, len(files))
        return evidence

    # -- Debian patches ----------------------------------------------------

    def assess_debian_patches(self, workspace, report: DebianPatchReport,
                              patch_texts: Dict[str, str], arcos_head: str,
                              upstream_presence: Dict[str, PresenceResult]
                              ) -> None:
        """Set presence on each Debian patch: carried, applied, missing, unknown."""
        if report is None or not report.patches:
            return
        series = workspace.shell(
            f"git show {shlex.quote(arcos_head)}:debian/patches/series"
        )
        carried = set()
        if series.ok:
            for line in series.stdout.splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    carried.add(line.split()[0])

        checkable = []
        for index, patch in enumerate(report.patches):
            text = patch_texts.get(patch.name, "")
            if patch.name in carried:
                patch.presence = Presence.DEFINITELY_PRESENT
                patch.presence_evidence.append(
                    "listed in ARCoS debian/patches/series")
            elif text.strip():
                checkable.append((index, patch))
            else:
                patch.presence = Presence.UNKNOWN
                patch.presence_evidence.append("patch content not available")
            if patch.upstream_commit:
                twin = next((r for s, r in upstream_presence.items()
                             if s.startswith(patch.upstream_commit.lower())), None)
                if twin is not None:
                    patch.presence_evidence.append(
                        f"upstream commit {patch.upstream_commit[:12]} is "
                        f"{twin.presence.value} in ARCoS"
                    )

        if not checkable:
            return
        for index, patch in checkable:
            workspace.write_file(f".git/apm-debian/{index}.patch",
                                 patch_texts[patch.name])
        script = _INDEX_PRELUDE.format(head=shlex.quote(arcos_head)) + (
            "for I in " + " ".join(str(i) for i, _ in checkable) + "; do\n"
            '  F=.git/apm-debian/$I.patch; R=NEITHER\n'
            "  for L in 1 0; do\n"
            '    if git apply --cached --check -R -p$L "$F" 2>/dev/null; then R=REVERSE; break; fi\n'
            '    if git apply --cached --check -p$L "$F" 2>/dev/null; then R=FORWARD; break; fi\n'
            "  done\n"
            '  echo "$I $R"\n'
            "done\n"
            "rm -rf .git/apm-debian\n"
        )
        result = workspace.shell(script, timeout=self.timeout)
        verdicts = {}
        if result.ok:
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0].isdigit():
                    verdicts[int(parts[0])] = parts[1]
        for index, patch in checkable:
            verdict = verdicts.get(index)
            if verdict == "REVERSE":
                patch.presence = Presence.PROBABLY_PRESENT
                patch.presence_evidence.append(
                    "already applied to the ARCoS source (git apply --check -R)")
            elif verdict == "FORWARD":
                patch.presence = Presence.MISSING
                patch.presence_evidence.append(
                    "applies cleanly to the ARCoS source; not present")
            else:
                patch.presence = Presence.UNKNOWN
                patch.presence_evidence.append(
                    "neither applies nor reverses cleanly - partially present "
                    "or adapted" if verdict == "NEITHER"
                    else "the check could not run")


_INDEX_PRELUDE = (
    "set -u\n"
    "IDX=$(mktemp .git/apm-check-idx.XXXXXX) || exit 97\n"
    'P=$(mktemp .git/apm-check-patch.XXXXXX) || exit 97\n'
    'rm -f "$IDX"\n'
    "trap 'rm -f \"$IDX\" \"$P\"' EXIT\n"
    'export GIT_INDEX_FILE="$IDX"\n'
    "git read-tree {head} || exit 98\n"
)


def _verdict(verdict: Optional[str],
             lines: Optional[Tuple[int, int, int]]) -> PresenceResult:
    if verdict == "REVERSE":
        return PresenceResult(
            Presence.PROBABLY_PRESENT,
            ["its post-image is already in the ARCoS tree (git apply --check -R)"],
        )
    if verdict == "FORWARD":
        return PresenceResult(
            Presence.MISSING, ["applies cleanly to the ARCoS tree; not present"],
        )
    if verdict == "EMPTY":
        return PresenceResult(Presence.UNKNOWN, ["the commit changes nothing"])
    if verdict == "NEITHER" and lines is not None:
        present, absent, total = lines
        if total and present == total:
            return PresenceResult(
                Presence.PROBABLY_PRESENT,
                [f"adapted backport: every file ({total}) already carries the "
                 f"lines it adds, though the diff does not apply as-is"],
            )
        if total and absent == total:
            return PresenceResult(
                Presence.MISSING,
                [f"none of its added lines are in ARCoS ({total} file(s)); it "
                 f"does not apply cleanly either, so it will need adapting"],
            )
        if present:
            return PresenceResult(
                Presence.UNKNOWN,
                [f"partially present: {present} of {total} file(s) carry the "
                 f"change"],
            )
        return PresenceResult(
            Presence.UNKNOWN,
            ["neither applies nor reverses cleanly, and the line comparison "
             "is inconclusive"],
        )
    if verdict == "NEITHER":
        return PresenceResult(
            Presence.UNKNOWN, ["neither applies nor reverses cleanly"])
    return PresenceResult(
        Presence.UNKNOWN,
        ["the content check could not run for this commit"
         if verdict in (None, "ERROR") else f"content check: {verdict}"],
    )


def _parse_u0(text: str) -> Dict[str, Dict[str, dict]]:
    """git log -p -U0 output -> {sha: {path: {added, removed, deleted}}}."""
    commits: Dict[str, Dict[str, dict]] = {}
    current: Optional[Dict[str, dict]] = None
    path = None
    deleted_marker = ""
    for line in text.splitlines():
        if line.startswith("APM-COMMIT "):
            current = commits.setdefault(line.split()[1], {})
            path = None
            continue
        if current is None:
            continue
        if line.startswith("diff --git "):
            path = None
            continue
        if line.startswith("--- "):
            old = line[4:].strip()
            deleted_marker = old
            continue
        if line.startswith("+++ "):
            new = line[4:].strip()
            if new == "/dev/null":
                name = deleted_marker[2:] if deleted_marker.startswith("a/") else deleted_marker
                path = name
                current[path] = {"added": [], "removed": [], "deleted": True}
            else:
                path = new[2:] if new.startswith("b/") else new
                current.setdefault(path, {"added": [], "removed": [], "deleted": False})
            continue
        if path is None or line.startswith("@@"):
            continue
        if line.startswith("+"):
            current[path]["added"].append(line[1:].strip())
        elif line.startswith("-"):
            current[path]["removed"].append(line[1:].strip())
    return commits


def _file_verdict(change: dict, content: Optional[str]) -> str:
    """present / absent / unclear for one file's share of a change."""
    if change.get("deleted"):
        return "present" if content is None else "absent"
    lines = set(l.strip() for l in (content or "").splitlines())
    added = [l for l in change["added"] if len(l) >= _SIGNIFICANT]
    removed = [l for l in change["removed"] if len(l) >= _SIGNIFICANT]
    if len(added) >= 2:
        ratio = sum(1 for l in added if l in lines) / len(added)
        gone = (sum(1 for l in removed if l not in lines) / len(removed)
                if removed else 1.0)
        if ratio >= 0.9 and gone >= 0.5:
            return "present"
        if ratio <= 0.1:
            return "absent"
        return "unclear"
    if not added and len(removed) >= 2:
        gone = sum(1 for l in removed if l not in lines) / len(removed)
        if gone >= 0.9:
            return "present"
        if gone <= 0.1:
            return "absent"
    return "unclear"


def cat_files(workspace, rev: str, paths: Iterable[str],
              timeout: int = 900) -> Dict[str, Optional[str]]:
    """{path: content, or None if the file does not exist at rev}. One call.

    Contents travel base64-encoded: the transport decodes output as text, and a
    binary file in the list must not take the whole answer down with it.
    """
    paths = list(paths)
    if not paths:
        return {}
    listing = " ".join(shlex.quote(p) for p in paths)
    result = workspace.shell(
        f"R={shlex.quote(rev)}\n"
        f"for F in {listing}; do\n"
        '  if git cat-file -e "$R:$F" 2>/dev/null; then\n'
        '    printf "APM-FILE %s\\n" "$(printf %s "$F" | base64 | tr -d \'\\n\')"\n'
        '    git cat-file blob "$R:$F" | base64 | tr -d \'\\n\'; echo\n'
        '  else\n'
        '    printf "APM-MISSING %s\\n" "$(printf %s "$F" | base64 | tr -d \'\\n\')"\n'
        "  fi\n"
        "done\n",
        timeout=timeout,
    )
    if not result.ok:
        raise GitWorkspaceError("reading ARCoS files failed", result.stderr.strip())
    out: Dict[str, Optional[str]] = {path: None for path in paths}
    lines = result.stdout.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("APM-MISSING "):
            out[_b64decode(line.split(" ", 1)[1])] = None
            i += 1
            continue
        if line.startswith("APM-FILE ") and i + 1 < len(lines):
            out[_b64decode(line.split(" ", 1)[1])] = _b64decode(lines[i + 1])
            i += 2
            continue
        i += 1
    return out


def _b64decode(text: str) -> str:
    import base64

    return base64.b64decode(text.strip() or b"").decode("utf-8", errors="replace")
