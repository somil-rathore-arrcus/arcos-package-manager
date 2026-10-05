"""Find the upstream release an imported ARCoS tree corresponds to.

Some ARCoS packages (babeltrace, ctypesgen, rsyslog) were imported from a
tarball, so they share no git history with any upstream and merge-base has
nothing to say. Their content still says something: the ARCoS tree - its root
import commit, or the commit it ships - is compared file by file with the
upstream release tags, and with Debian's orig tarball where there is one, and
the closest match is reported with its evidence.

That is a finding, not a verification. It becomes the fork's base only when a
person approves it (services.approvals), and every number computed from it is
labelled as synthesized from a content match.

Tree comparison needs no file contents: two trees are compared by the blob ids
they list, which a blobless fetch already has. Line counts are computed only for
the few closest tags.
"""

from __future__ import annotations

import hashlib
import io
import logging
import lzma
import re
import shlex
import tarfile
from typing import Dict, List, Optional, Sequence, Tuple

from ..domain.models import ContentMatch, ContentMatchCandidate
from ..gitio.workspace import GitWorkspaceError
from ..upstream.refs import canonical, series_of, tag_versions

log = logging.getLogger(__name__)

EXCLUDES = ("debian", ".git", ".gitignore", ".gitattributes", ".github",
            ".gitlab-ci.yml", ".travis.yml", ".pc")
_NUMBER = re.compile(r"(\d+)")


class ContentBaseService:
    def __init__(self, workspaces, http_cache=None, archive: Optional[dict] = None,
                 max_tags: int = 150, top: int = 3,
                 max_orig_bytes: int = 200 * 1024 * 1024,
                 min_score: float = 0.6) -> None:
        self.workspaces = workspaces
        self.http_cache = http_cache
        self.archive = archive or {}
        self.max_tags = max_tags
        self.top = top
        self.max_orig_bytes = max_orig_bytes
        # Below this a "closest" tag is not a match, and is not proposed.
        self.min_score = min_score

    # -- choosing what to compare -----------------------------------------

    def pick_tags(self, tags: Dict[str, str], upstream_version: str,
                  names: Sequence[str], approved_tag: Optional[str] = None
                  ) -> List[str]:
        """Release tags worth comparing, closest version first.

        With a Debian version, the tags of its series (and its major version, in
        case the series is wrong); without one, every tag that reads as a
        version, newest first.
        """
        want = series_of(upstream_version) if upstream_version else None
        major = want.split(".")[0] if want else None
        scored = []
        for tag in tags:
            readings = tag_versions(tag, names)
            if not readings:
                continue
            best = max(readings, key=len)
            if want:
                series = {series_of(r.replace("_", ".")) for r in readings}
                majors = {s.split(".")[0] for s in series if s}
                if want not in series and major not in majors:
                    continue
            scored.append((_version_key(best), tag))
        scored.sort(reverse=True)
        picked = [tag for _, tag in scored][: self.max_tags]
        if approved_tag and approved_tag in tags and approved_tag not in picked:
            picked.append(approved_tag)
        return picked

    # -- the comparison ----------------------------------------------------

    def detect(self, package: str, release: str, arcos_url: str, arcos_ref: str,
               upstream_url: str, tags: Dict[str, str], upstream_version: str,
               names: Sequence[str], source=None, target_refs: Sequence[str] = (),
               approved: Optional[dict] = None) -> ContentMatch:
        match = ContentMatch(method="tree-compare")
        approved_tag = (approved or {}).get("base_tag")
        chosen = self.pick_tags(tags, upstream_version, names, approved_tag)
        if not chosen:
            match.notes.append("no upstream release tags to compare against")
            return match

        workspace = self.workspaces.for_package(package, release)
        workspace.ensure()
        arcos_head = workspace.fetch_side("arcos", arcos_url, arcos_ref)
        roots = workspace.shell(
            f"git rev-list --max-parents=0 {shlex.quote(arcos_head)}", check=True
        ).text.split()
        trees: List[Tuple[str, str]] = [(r, "root import commit") for r in roots[:3]]
        if arcos_head not in roots:
            trees.append((arcos_head, "ARCoS commit"))

        workspace.set_remote("upstream", upstream_url)
        local = {tag: f"refs/apm/content/{i}" for i, tag in enumerate(chosen)}
        self._fetch_tags(workspace, upstream_url, local)
        candidates = self._score(workspace, trees, local)
        candidates.sort(key=lambda c: (-c.score, c.files_differing, c.tag))

        if upstream_version and (not candidates or candidates[0].score < self.min_score):
            # Nothing in Debian's series matches. The fork may simply be of a
            # different series than Debian ships (ARCoS's babeltrace is 2.x
            # while Debian's babeltrace is 1.5), so every release tag is tried.
            wider = [t for t in self.pick_tags(tags, "", names) if t not in local]
            if wider:
                extra = {tag: f"refs/apm/content/w{i}" for i, tag in enumerate(wider)}
                self._fetch_tags(workspace, upstream_url, extra)
                local.update(extra)
                candidates += self._score(workspace, trees, extra)
                candidates.sort(key=lambda c: (-c.score, c.files_differing, c.tag))
                match.notes.append(
                    f"no tag of Debian's {upstream_version} series matched; all "
                    f"{len(local)} release tags were compared"
                )
        for candidate in candidates[: self.top]:
            self._line_counts(workspace, candidate, local[candidate.tag])

        orig = self._orig_tarball(workspace, source, trees)
        match.candidates = candidates[: max(self.top, 10)] + ([orig] if orig else [])
        if not candidates:
            match.notes.append("no tag could be compared")
            return match

        best = candidates[0]
        exact = [c for c in candidates[:3] if upstream_version and any(
            canonical(v) == canonical(upstream_version)
            for v in tag_versions(c.tag, names))]
        # Debian's own version wins a tie - but only a real tie: an older tag
        # whose files are a subset of the tree also scores 1.0, and the files
        # only ARCoS has are what tell the two apart.
        if exact and exact[0].score >= best.score - 1e-9 \
                and exact[0].files_differing <= best.files_differing:
            best = exact[0]
        match.base_tag, match.base_sha = best.tag, tags.get(best.tag)
        match.arcos_tree = best.arcos_tree
        match.arcos_tree_label = next(
            (label for sha, label in trees if sha == best.arcos_tree), "")
        match.score = best.score
        match.files_compared = best.files_compared
        match.files_differing = best.files_differing
        if best.lines_added is not None:
            match.lines_differing = (best.lines_added or 0) + (best.lines_removed or 0)
        if len(candidates) > 1 and abs(candidates[1].score - best.score) < 0.005:
            match.notes.append(
                f"ambiguous: {candidates[1].tag} scores within 0.5% of {best.tag}"
            )
        if best.score < self.min_score:
            match.notes.append(
                f"weak: the closest tag {best.tag} scores {best.score:.3f}, below "
                f"{self.min_score}; no release is proposed as the base"
            )
        if orig is not None:
            match.notes.append(
                f"Debian orig tarball: {orig.files_differing} of "
                f"{orig.files_compared} files differ from {str(orig.arcos_tree)[:12]}"
            )

        if approved:
            self._apply_approval(workspace, match, approved, tags, upstream_url,
                                 arcos_head, target_refs)
        return match

    def _fetch_tags(self, workspace, upstream_url: str, local: Dict[str, str]) -> None:
        specs = " ".join(
            shlex.quote(f"+refs/tags/{tag}:{ref}") for tag, ref in local.items()
        )
        fetched = workspace.shell(
            f"git fetch -q --no-tags --filter=blob:none upstream {specs}",
            timeout=workspace.long_timeout,
        )
        if not fetched.ok:
            raise GitWorkspaceError(
                f"could not fetch {len(local)} release tags from {upstream_url}",
                fetched.stderr.strip(),
            )

    @property
    def threshold(self) -> float:
        return self.min_score

    def _score(self, workspace, trees, local: Dict[str, str]
               ) -> List[ContentMatchCandidate]:
        """One shell call: for each tree and tag, how many files differ."""
        excludes = " ".join(shlex.quote(f":(exclude){e}") for e in EXCLUDES)
        # ls-tree takes literal paths only - no :(exclude) magic - so the
        # excluded top-level entries are filtered out of its listing instead.
        skip = "|".join(re.escape(e) for e in EXCLUDES)
        lines = []
        for tag, ref in local.items():
            lines.append(
                f"T=$(git ls-tree -r --name-only {ref} | "
                f"grep -cvE {shlex.quote(f'^({skip})(/|$)')})"
            )
            for sha, _label in trees:
                lines.append(
                    f"S=$(git diff-tree -r --no-renames --name-status {sha} {ref} "
                    f"-- . {excludes} | awk '{{c[$1]++}} END {{printf \"%d %d %d\", "
                    f"c[\"M\"]+c[\"T\"], c[\"A\"], c[\"D\"]}}')"
                )
                lines.append(f"echo {shlex.quote(ref)} {sha} $T $S")
        result = workspace.shell("\n".join(lines), timeout=workspace.long_timeout)
        if not result.ok:
            raise GitWorkspaceError("tree comparison failed", result.stderr.strip())
        by_ref = {ref: tag for tag, ref in local.items()}
        best: Dict[str, ContentMatchCandidate] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) != 6 or parts[0] not in by_ref:
                continue
            ref, sha, total, modified, only_upstream, only_arcos = parts
            total, modified = int(total), int(modified)
            only_upstream, only_arcos = int(only_upstream), int(only_arcos)
            # Files only in ARCoS are mostly what a tarball adds (configure,
            # Makefile.in) and penalise every tag alike; what separates the
            # tags is what they change and what ARCoS lacks.
            score = 1.0 - (modified + only_upstream) / max(total, 1)
            candidate = ContentMatchCandidate(
                tag=by_ref[ref], method="tree-compare", arcos_tree=sha,
                files_compared=total,
                files_differing=modified + only_upstream + only_arcos,
                score=round(max(score, 0.0), 6),
            )
            current = best.get(candidate.tag)
            if current is None or candidate.score > current.score:
                best[candidate.tag] = candidate
        return list(best.values())

    def _line_counts(self, workspace, candidate: ContentMatchCandidate,
                     ref: str) -> None:
        excludes = " ".join(shlex.quote(f":(exclude){e}") for e in EXCLUDES)
        result = workspace.shell(
            f"git diff --shortstat --no-renames {candidate.arcos_tree} {ref} "
            f"-- . {excludes}",
            timeout=workspace.long_timeout,
        )
        if not result.ok:
            return
        added = re.search(r"(\d+) insertion", result.stdout)
        removed = re.search(r"(\d+) deletion", result.stdout)
        candidate.lines_added = int(added.group(1)) if added else 0
        candidate.lines_removed = int(removed.group(1)) if removed else 0

    def _orig_tarball(self, workspace, source, trees) -> Optional[ContentMatchCandidate]:
        """Compare the ARCoS tree with Debian's orig tarball by blob id."""
        if source is None or self.http_cache is None or not source.directory:
            return None
        version = source.version.split(":", 1)[-1].rsplit("-", 1)[0]
        base = f"{(source.mirror or self.archive.get('mirror', '')).rstrip('/')}/" \
               f"{source.directory.strip('/')}/{source.package}_{version}.orig.tar"
        raw = None
        for suffix in (".xz", ".gz", ".bz2"):
            try:
                raw = self.http_cache.get(base + suffix, allow_missing=True)
            except Exception as exc:  # noqa: BLE001 - evidence, not a requirement
                log.info("orig tarball %s: %s", base + suffix, exc)
                raw = None
            if raw:
                break
        if not raw or len(raw) > self.max_orig_bytes:
            return None
        try:
            blobs = _tar_blob_ids(raw)
        except (tarfile.TarError, lzma.LZMAError, EOFError, OSError) as exc:
            log.info("unreadable orig tarball for %s: %s", source.package, exc)
            return None
        best = None
        for sha, _label in trees:
            listing = workspace.shell(f"git ls-tree -r {sha}")
            if not listing.ok:
                continue
            arcos = {}
            for line in listing.stdout.splitlines():
                meta, _, path = line.partition("\t")
                parts = meta.split()
                if len(parts) == 3 and parts[1] == "blob":
                    arcos[path] = parts[2]
            paths = {p for p in set(blobs) | set(arcos)
                     if not p.split("/", 1)[0] in EXCLUDES}
            differing = sum(1 for p in paths if blobs.get(p) != arcos.get(p))
            candidate = ContentMatchCandidate(
                tag=f"debian-orig:{version}", method="orig-tarball",
                arcos_tree=sha, files_compared=len(paths),
                files_differing=differing,
                score=round(1.0 - differing / max(len(paths), 1), 6),
            )
            if best is None or candidate.score > best.score:
                best = candidate
        return best

    def _apply_approval(self, workspace, match: ContentMatch, approved: dict,
                        tags: Dict[str, str], upstream_url: str,
                        arcos_head: str, target_refs: Sequence[str]) -> None:
        tag = approved.get("base_tag")
        problems = []
        if approved.get("repository") and approved["repository"] != upstream_url:
            problems.append(f"it names {approved['repository']}, not {upstream_url}")
        if tag not in tags:
            problems.append(f"tag {tag} no longer exists upstream")
        elif approved.get("base_sha") and not tags[tag].startswith(approved["base_sha"]):
            problems.append(
                f"tag {tag} moved from {approved['base_sha'][:12]} to {tags[tag][:12]}"
            )
        if problems:
            match.notes.append("approval not applied: " + "; ".join(problems))
            return
        if tag != match.base_tag:
            match.notes.append(
                f"approved base {tag} differs from the best content match "
                f"{match.base_tag}; the person's decision stands"
            )
            match.base_tag, match.base_sha = tag, tags[tag]
            chosen = next((c for c in match.candidates if c.tag == tag), None)
            if chosen is not None:
                match.score, match.arcos_tree = chosen.score, chosen.arcos_tree
                match.files_compared = chosen.files_compared
                match.files_differing = chosen.files_differing
        match.approved = True
        match.verified_by = approved.get("verified_by")
        match.verified_at = str(approved.get("verified_at") or "")
        match.approval_source = approved.get("approval_source")
        match.method = approved.get("method") or match.method
        if approved.get("arcos_tree"):
            match.arcos_tree = approved["arcos_tree"]

        base_sha = tags[tag]
        target = tag
        for ref in target_refs:
            fetched = workspace.shell(
                f"git fetch -q --no-tags --filter=blob:none upstream "
                f"{shlex.quote(f'+refs/heads/{ref}:refs/apm/content-target')}",
                timeout=workspace.long_timeout,
            )
            if not fetched.ok:
                continue
            contains = workspace.shell(
                f"git merge-base --is-ancestor {base_sha} refs/apm/content-target"
            )
            if contains.ok:
                target = ref
                break
        match.notes.append(f"target: {target}")
        target_rev = base_sha if target == tag else "refs/apm/content-target"
        behind = workspace.shell(
            f"git rev-list --count --no-merges {target_rev} ^{base_sha}", check=True
        ).text
        arcos_only = workspace.shell(
            f"git rev-list --count --no-merges {arcos_head} ^{match.arcos_tree}",
            check=True,
        ).text
        match.notes.append(f"synthetic-counts: behind={behind} arcos_only={arcos_only}")


def _tar_blob_ids(raw: bytes) -> Dict[str, str]:
    """path -> git blob id for every file in a tarball, top directory removed."""
    blobs = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            data = handle.read()
            path = member.name.split("/", 1)[1] if "/" in member.name else member.name
            header = f"blob {len(data)}\0".encode()
            blobs[path] = hashlib.sha1(header + data).hexdigest()
    return blobs


def _version_key(text: str) -> tuple:
    return tuple(int(n) for n in _NUMBER.findall(text or "")[:6]) or (0,)
