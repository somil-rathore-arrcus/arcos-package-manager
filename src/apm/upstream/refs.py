"""Which upstream ref a release's package should be compared against.

The question is never "how far is ARCoS behind master". A Bookworm package is a
fork of one upstream release, so the refs worth measuring are, in order:

  1. the upstream tag for the version Debian ships       (the release itself)
  2. the maintenance branch of that version's series     (fixes after it)
  3. the Debian packaging tag/branch for the release     (packaging lineage)
  4. anything else - a curated ref, the default branch   (explicit fallback)

Candidates are generated here from the Debian version, debian/watch and the
remote's own tags and branches; git history then decides which of them the ARCoS
commit actually relates to (see select_ref). A default branch is only ever a
labelled last resort, because it is the development tip, and comparing a stable
release against a development tip reports thousands of commits that were never
meant for that release.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..domain.enums import RefStrategy

log = logging.getLogger(__name__)

# Suffixes Debian adds when it repacks a tarball; they are not in any tag.
_REPACK = re.compile(
    r"(?:[+~.](?:dfsg|ds|repack|debian|nmu|really\S*))\d*(?:\.\d+)?$",
    re.IGNORECASE,
)
_SERIES = re.compile(r"^(\d+)\.(\d+)")
_TAG_PREFIXES = ("release", "rel", "version", "ver", "v")
_ARCHIVE_SUFFIX = re.compile(
    r"\\?\.(?:tar(?:\\?\.(?:gz|xz|bz2|lz|zst))?|tgz|tbz2?|txz|zip)\$?$"
)

# uscan's version=4 substitutions, enough to turn a watch pattern into a regex.
_USCAN_MACROS = {
    "@ANY_VERSION@": r"[-_]?(\d[\-+\.:\~\da-zA-Z]*)",
    "@ARCHIVE_EXT@": r"(?:\.tar\.xz|\.tar\.bz2|\.tar\.gz|\.zip|\.tgz|\.tbz|\.txz)",
    "@SIGNATURE_EXT@": r"(?:\.asc|\.pgp|\.gpg|\.sig|\.sign)",
    "@DEB_EXT@": r"[\+~](debian|dfsg|ds|deb)(\.)?(\d+)?$",
}

BASE_ROLES = (RefStrategy.EXACT_TAG, RefStrategy.PACKAGING_TAG)
SERIES_ROLES = (
    RefStrategy.MAINTENANCE_BRANCH, RefStrategy.KERNEL_SERIES,
    RefStrategy.PACKAGING_BRANCH,
)
CONFIGURED_ROLES = (RefStrategy.CURATED, RefStrategy.MANUAL)
DEVELOPMENT_BRANCHES = {"master", "main", "develop", "devel", "trunk", "next"}


@dataclass
class RefCandidateSpec:
    """One (repository, ref) worth probing, and why it was proposed."""

    repository: str
    ref: str
    strategy: RefStrategy
    kind: str = "branch"
    sha: Optional[str] = None
    note: str = ""

    @property
    def key(self) -> Tuple[str, str]:
        return (self.repository, self.ref)


# -- versions -----------------------------------------------------------------

def debian_upstream_version(debian_version: str) -> str:
    """The upstream part of a Debian version: no epoch, revision or repack.

    1:6.1-1 -> 6.1, 5.9.3+dfsg-2+deb12u1 -> 5.9.3, 3:20221126-1+deb12u1 -> 20221126
    """
    version = (debian_version or "").strip().split(":", 1)[-1]
    if "-" in version:
        version = version.rsplit("-", 1)[0]
    previous = None
    while previous != version:
        previous = version
        version = _REPACK.sub("", version)
    return version


def series_of(version: str) -> Optional[str]:
    """major.minor, or None for a version with no series (a date, a single
    number)."""
    match = _SERIES.match(version or "")
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2)}"


def canonical(version: str) -> str:
    """Separators normalised so 1_1_1w == 1.1.1w and 2.0~rc1 == 2.0-rc1."""
    text = (version or "").strip().lower()
    text = re.sub(r"[_~\-+]", ".", text)
    text = re.sub(r"\.{2,}", ".", text)
    # 2.0.rc1 and 2.0rc1 are the same pre-release.
    text = re.sub(r"\.(?=(?:rc|alpha|beta|pre)\d*$)", "", text)
    return text.strip(".")


def _loose(version: str) -> str:
    """6.1.0 -> 6.1: trailing .0 components are often dropped in one spelling."""
    text = canonical(version)
    while text.endswith(".0") and text.count(".") > 1:
        text = text[:-2]
    return text


def tag_versions(tag: str, names: Iterable[str] = ()) -> List[str]:
    """Every version a tag name can be read as; empty if it carries none.

    openssl-3.0.20 -> 3.0.20, OpenSSL_1_1_1w -> 1_1_1w, v6.1.0 -> 6.1.0,
    release_2_0_0 -> 2_0_0. libnl3_7_0 reads as both 3_7_0 (prefix "libnl")
    and 7_0 (prefix "libnl3"), so every reading is kept and the caller decides.
    """
    prefixes = sorted({n.lower() for n in names if n} | set(_TAG_PREFIXES),
                      key=len, reverse=True)
    results: List[str] = []
    pending = [(tag or "").strip()]
    seen = set()
    while pending:
        text = pending.pop()
        if not text or text in seen:
            continue
        seen.add(text)
        if text[:1].isdigit():
            results.append(text)
        lowered = text.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix) and len(text) > len(prefix):
                rest = text[len(prefix):]
                if rest[0] in "-_./ ":
                    rest = rest[1:]
                if rest and (rest[0].isdigit() or rest[0] in "vV"):
                    pending.append(rest)
    return results


def tag_version(tag: str, names: Iterable[str] = ()) -> Optional[str]:
    """The most specific version reading of a tag, or None."""
    readings = tag_versions(tag, names)
    return max(readings, key=len) if readings else None


def watch_tag_patterns(watch: Optional[str], package: str = "") -> List[re.Pattern]:
    """The version patterns debian/watch declares, as compiled regexes.

    They are matched against the hrefs a forge would serve for a tag (see
    watch_version_for_tag) rather than edited into tag regexes, because the
    patterns are written against those hrefs and editing them breaks as often
    as it works.
    """
    if not watch:
        return []
    text = re.sub(r"\\\s*\n\s*", " ", watch)
    patterns = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("version="):
            continue
        line = re.sub(r'^opts\s*=\s*"[^"]*"\s*', "", line)
        line = re.sub(r"^opts\s*=\s*\S+\s+", "", line)
        tokens = line.split()
        if not tokens:
            continue
        if len(tokens) > 1 and not tokens[1].startswith(
                ("debian", "uupdate", "same", "ignore")):
            raw = tokens[1]
        else:
            # version=4 allows the pattern glued onto the URL's last part.
            raw = tokens[0].rsplit("/", 1)[-1]
        for macro, value in _USCAN_MACROS.items():
            raw = raw.replace(macro, value)
        raw = raw.replace("@PACKAGE@", re.escape(package))
        if "(" not in raw:
            continue
        try:
            patterns.append(re.compile(raw))
        except re.error as exc:
            log.debug("unusable watch pattern %r: %s", raw, exc)
    return patterns


def watch_version_for_tag(patterns: Sequence[re.Pattern], tag: str) -> Optional[str]:
    """The version a watch pattern extracts from `tag`, trying each href shape
    a forge publishes for it."""
    shapes = (
        f"refs/tags/{tag}", tag, f"{tag}.tar.gz",
        f"/archive/refs/tags/{tag}.tar.gz", f"/archive/{tag}.tar.gz",
        f"/tags/{tag}.tar.gz", f"/releases/download/{tag}/{tag}.tar.gz",
    )
    for pattern in patterns:
        for shape in shapes:
            match = pattern.fullmatch(shape)
            if match and match.groups():
                return ".".join(g for g in match.groups() if g)
    return None


def exact_tags(tags: Dict[str, str], upstream_version: str,
               watch: Optional[str] = None, names: Sequence[str] = (),
               limit: int = 2) -> List[Tuple[str, str, str]]:
    """Tags that name exactly `upstream_version`: [(tag, sha, how)].

    debian/watch's own pattern is tried first, because it is the package
    maintainer's statement of how tags map to versions. Then name heuristics,
    and last a loose match that ignores trailing .0 components.
    """
    if not upstream_version or not tags:
        return []
    want = canonical(upstream_version)
    want_loose = _loose(upstream_version)
    found: List[Tuple[str, str, str]] = []
    seen_shas = set()

    def add(tag: str, how: str) -> None:
        sha = tags[tag]
        if sha in seen_shas or any(t == tag for t, _, _ in found):
            return
        seen_shas.add(sha)
        found.append((tag, sha, how))

    patterns = watch_tag_patterns(watch, names[0] if names else "")
    if patterns:
        for tag in sorted(tags):
            version = watch_version_for_tag(patterns, tag)
            if version and canonical(version) == want:
                add(tag, "debian/watch pattern")
    ordered = sorted(tags, key=lambda t: (not t.lower().startswith("v"), t))
    for tag in ordered:
        if any(canonical(v) == want for v in tag_versions(tag, names)):
            add(tag, "tag name")
    if not found:
        for tag in ordered:
            if any(_loose(v) == want_loose for v in tag_versions(tag, names)):
                add(tag, "tag name, ignoring trailing .0")
    return found[:limit]


def maintenance_branches(branches: Iterable[str], upstream_version: str,
                         limit: int = 3) -> List[str]:
    """Branches whose name carries this version's major.minor series.

    The number must stand alone: bookworm's ethtool is 6.1, and a loose match
    picks "ethtool-6.14.y"; "3.0" must not match the "230" of a branch named
    after pull request 230.
    """
    series = series_of(upstream_version)
    if not series:
        return []
    major, minor = series.split(".")
    pattern = re.compile(r"(?<!\d)%s[._]%s(?!\d)" % (major, minor))
    hits = [b for b in branches if pattern.search(b)]
    # Shortest first: "openssl-3.0" before "openssl-3.0-stable-backport".
    hits.sort(key=lambda b: (len(b), b))
    return hits[:limit]


def dep14_tag(debian_version: str) -> str:
    """DEP-14 packaging tag: debian/<version>, ':' -> '%', '~' -> '_'."""
    return "debian/" + (debian_version or "").replace(":", "%").replace("~", "_")


# -- candidates ---------------------------------------------------------------

def upstream_candidates(repository: str, listing, upstream_version: str,
                        watch: Optional[str], names: Sequence[str],
                        configured_ref: Optional[str],
                        configured_strategy: RefStrategy,
                        pin: bool = False) -> List[RefCandidateSpec]:
    """Refs of the project's own repository, most release-appropriate first."""
    tags = listing.tags if listing is not None and listing.ok else {}
    branches = listing.branches if listing is not None and listing.ok else {}
    head = listing.head if listing is not None and listing.ok else None
    out: List[RefCandidateSpec] = []

    def add(ref: Optional[str], strategy: RefStrategy, kind: str, note: str = ""):
        if not ref or any(c.ref == ref for c in out):
            return
        sha = tags.get(ref) if kind == "tag" else branches.get(ref)
        out.append(RefCandidateSpec(repository, ref, strategy, kind, sha, note))

    configured_kind = "tag" if configured_ref in tags and configured_ref not in \
        branches else "branch"
    if pin and configured_ref:
        add(configured_ref, configured_strategy, configured_kind,
            "pinned in config/overrides.yaml (pin_ref)")
        return out

    for tag, _sha, how in exact_tags(tags, upstream_version, watch, names):
        add(tag, RefStrategy.EXACT_TAG, "tag",
            f"the upstream tag for Debian's {upstream_version} ({how})")
    if configured_strategy is RefStrategy.KERNEL_SERIES:
        add(configured_ref, configured_strategy, "branch",
            "stable series derived from the Debian version")
    for branch in maintenance_branches(branches, upstream_version):
        add(branch, RefStrategy.MAINTENANCE_BRANCH, "branch",
            f"maintenance branch for the {series_of(upstream_version)} series")
    if configured_ref:
        add(configured_ref, configured_strategy, configured_kind,
            "named by the curated mapping" if configured_strategy is
            RefStrategy.CURATED else "named by the resolution chain")
    if configured_strategy is not RefStrategy.KERNEL_SERIES:
        # The kernel's series is derived from the Debian version; probing the
        # mainline tip as well would cost a second kernel history for a ref
        # that can never be chosen over the series.
        add(head or ("HEAD" if not configured_ref else None),
            RefStrategy.DEFAULT_BRANCH, "branch",
            "the repository's default branch - a development tip, used only "
            "as a fallback")
    return out


def packaging_candidates(repository: str, listing, debian_version: str,
                         codename: str, release_number: str,
                         vcs_branch: Optional[str],
                         fallback_refs: Sequence[str]) -> List[RefCandidateSpec]:
    """Refs of the Debian packaging repository, the shipped release first.

    `debian/sid` is what Debian is preparing next, not what Bookworm ships, so it
    is a labelled fallback behind the release's own tag and branch.
    """
    ok = listing is not None and listing.ok
    tags = listing.tags if ok else {}
    branches = listing.branches if ok else {}
    out: List[RefCandidateSpec] = []

    def add(ref: Optional[str], strategy: RefStrategy, kind: str, note: str,
            require_listed: bool = True):
        if not ref or any(c.ref == ref for c in out):
            return
        listed = ref in (tags if kind == "tag" else branches)
        if require_listed and ok and not listed:
            return
        sha = tags.get(ref) if kind == "tag" else branches.get(ref)
        out.append(RefCandidateSpec(repository, ref, strategy, kind, sha, note))

    if debian_version:
        add(dep14_tag(debian_version), RefStrategy.PACKAGING_TAG, "tag",
            f"DEP-14 tag for the shipped version {debian_version}")
        bare = debian_version.split(":", 1)[-1]
        add("debian/" + bare.replace("~", "_"), RefStrategy.PACKAGING_TAG, "tag",
            f"packaging tag for {debian_version} (epoch omitted)")
    for name in (f"debian/{codename}", codename, f"debian/{release_number}",
                 f"debian/{codename}-security", f"{codename}-security"):
        if codename and name.strip("/"):
            add(name, RefStrategy.PACKAGING_BRANCH, "branch",
                f"the packaging branch for {codename}")
    if vcs_branch:
        strategy = (RefStrategy.PACKAGING_BRANCH
                    if codename and codename in vcs_branch
                    else RefStrategy.PACKAGING_FALLBACK)
        add(vcs_branch, strategy, "branch",
            f"the branch the {codename or 'release'} Sources index names in "
            f"Vcs-Git", require_listed=False)
    for ref in fallback_refs:
        add(ref, RefStrategy.PACKAGING_FALLBACK, "branch",
            "packaging development branch - a fallback, not the release",
            require_listed=bool(ok))
    return out


# -- choosing -----------------------------------------------------------------

@dataclass
class ProbedRef:
    """A candidate joined with what the ancestry probe measured for it."""

    spec: RefCandidateSpec
    index: int
    reachable: bool = False
    shared: bool = False
    sha: Optional[str] = None
    date: Optional[str] = None
    merge_bases: List[str] = field(default_factory=list)
    merge_base_date: Optional[str] = None
    behind: Optional[int] = None
    arcos_only: Optional[int] = None
    in_arcos: Optional[bool] = None
    error: Optional[str] = None
    error_kind: Optional[str] = None


@dataclass
class RefChoice:
    target: ProbedRef
    base: Optional[ProbedRef]
    reason: str
    repository_candidates: List[ProbedRef]


def select_ref(probed: Sequence[ProbedRef], repository: str,
               contains: Iterable[Tuple[int, int]] = ()) -> Optional[RefChoice]:
    """Choose the comparison target within one repository.

    The shipped release's tag is the base. When a maintenance branch of the same
    series shares history and contains that tag, it is the target: it holds the
    release plus the fixes made after it. Otherwise the tag itself is the
    target. Only when neither exists does a configured ref (curated, manual) or,
    last, a fallback branch become the target - and the reason says which.

    `contains` holds (i, j) pairs meaning candidate i is an ancestor of j.
    """
    mine = [p for p in probed if p.spec.repository == repository and p.shared]
    if not mine:
        return None
    ancestor = set(contains)

    def best(items: List[ProbedRef]) -> Optional[ProbedRef]:
        if not items:
            return None
        return min(items, key=lambda p: (p.behind if p.behind is not None
                                         else 1 << 30, p.index))

    bases = [p for p in mine if p.spec.strategy in BASE_ROLES]
    base = bases[0] if bases else None
    series = [p for p in mine if p.spec.strategy in SERIES_ROLES]
    if base is not None:
        containing = [s for s in series if (base.index, s.index) in ancestor
                      or s.sha == base.sha]
        target = best(containing)
        if target is not None:
            return RefChoice(
                target, base,
                f"{target.spec.ref} is the {target.spec.strategy.value.replace('_', ' ')} "
                f"containing {base.spec.ref}, the tag for the shipped version",
                mine,
            )
        return RefChoice(
            base, base,
            f"{base.spec.ref} is the tag for the shipped version"
            + ("; no maintenance branch of its series contains it"
               if series else "; the project keeps no maintenance branch for "
                              "this series"),
            mine,
        )
    target = best(series)
    if target is not None:
        return RefChoice(
            target, None,
            f"{target.spec.ref}: {target.spec.note or target.spec.strategy.value}"
            f"; no tag names the shipped version",
            mine,
        )
    configured = [p for p in mine if p.spec.strategy in CONFIGURED_ROLES]
    if configured:
        target = configured[0]
        return RefChoice(
            target, None,
            f"{target.spec.ref} was named by a person "
            f"({target.spec.strategy.value}); no release tag or series branch "
            f"was found to prefer",
            mine,
        )
    target = best([p for p in mine if p.spec.strategy.is_fallback]) or best(mine)
    return RefChoice(
        target, None,
        f"FALLBACK: {target.spec.ref} ({target.spec.note or target.spec.strategy.value}); "
        f"no release tag, maintenance branch or packaging release branch "
        f"shares history with the fork",
        mine,
    )
