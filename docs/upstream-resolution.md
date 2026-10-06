# Upstream resolution

How the tool decides, for one package and one Debian release, which upstream
repository and which branch or tag the ARCoS fork tracks - and whether that is
proven.

## Run it

```bash
# A whole release: discovery, resolution, workbook and JSON
docker compose exec app apm resolve-release --release bookworm

# One package again, reusing the catalogue on disk (other rows are kept)
docker compose exec app apm resolve-release --release bookworm --package mstpd --no-discover
```

In the dashboard: choose the Debian release, the package and the ARCoS target
branch, then **Resolve upstream**. The dashboard serves the stored mapping;
the API resolves live only with `refresh=true` or when a package has no row.
**Manual** mode checks a repository and ref you type in - still verified, see
[Manual resolution](#manual-resolution).

## Discovery

The package list is read at runtime, never written down:

- `Arrcus/arrcus_rel` keeps one manifest branch per Debian release
  (`config/settings.yaml` → `manifest.branches`: `bookworm: aminor`,
  `trixie: aminor-trixie`). They genuinely differ, so each release is read
  from its own branch.
- `.gitmodules` on that branch lists the packages; the commit the
  superproject **pins** for each submodule is what ships - not the
  `branch =` line, which can be stale.
- Every submodule is a package except `excluded_submodules` (`arrcus_docs`).
  Some live at the top level of the manifest (`linux`, `ONL-standalone`,
  `ONL-xc`, `arcos-upgrade`, `medusa-bootstrap`), not under `packages/`.

```bash
docker compose exec app apm discover                            # writes out/packages.yaml
docker compose exec app apm discover --json --release bookworm  # prints the packages
```

## Debian metadata

Source metadata comes from the archive's `Sources` index (what
`apt-cache showsrc` reads), with `-updates` and `-security` merged over the
base suite as apt would. DEP-12 (`debian/upstream/metadata`) and
`debian/watch` come from the package's `.debian.tar.xz` in the pool. All of
it is cached in the cache volume.

## The candidate chain

Ordered; the first hit is preferred, and every step records why.

| Method | Source | Confidence |
|---|---|---|
| `curated` | `config/overrides.yaml` | as declared |
| `kernel_series` | Debian kernel version → `linux-X.Y.y` | high |
| `dep12` | `debian/upstream/metadata` `Repository:` | high |
| `watch_forge` | `debian/watch`, only when it names a git forge | medium |
| `homepage_forge` | `Homepage:` normalised to a forge repository | medium |

`Vcs-Git` is never treated as the upstream: it is the Debian *packaging*
repository, recorded in its own column. A URL only becomes a repository when
the conversion is justified - `https://lldpd.github.io` names an
organisation's page, not a repository, and is refused.

## Ancestry decides which repository

Metadata gives a plausible upstream. Shared history proves it.

```
lttng-tools   github.com/lttng/lttng-tools        NO SHARED HISTORY
              salsa.debian.org/debian/ltt-control merge-base 59bc0559
```

ARCoS forked the **Debian packaging repository** for some packages and the
**project's own repository** for others, and nothing in the metadata says
which. So `AncestryChecker` runs `git merge-base` against every candidate -
including the packaging repository - and the first repository, in priority
order, that shares history wins. `origin_kind` records which kind it is
(`debian_packaging` or the project's own).

**A curated repository is never silently replaced.** If `overrides.yaml`
names repository A, A shares no history, and B does, the package is
`NEEDS_REVIEW` / `CURATED_CONFLICT`; A stays the upstream and B is recorded
beside it. Replacement happens only when a person allows it
(`allow_ancestry_replacement: true` on the override, or
`ancestry.allow_curated_replacement` in settings).

The probe fetches the commit graph only (no file contents) into persistent
work directories, and caches its result keyed by the ARCoS commit and every
candidate's commit, so a moved upstream is probed again. Failures are never
cached.

## The release decides which ref

The question is never "how far behind master". For a Bookworm package the
candidate refs (`src/apm/upstream/refs.py`) are, in order of preference:

| Strategy | Ref | Example |
|---|---|---|
| `exact_tag` | the upstream tag for Debian's upstream version (debian/watch's pattern first, then tag-name heuristics) | `v6.1.0`, `openssl-3.0.20`, `libnl3_7_0` |
| `maintenance_branch` / `kernel_series` | the branch of that version's major.minor series | `openssl-3.0`, `linux-6.1.y`, `stable-0.13` |
| `packaging_tag` / `packaging_branch` | the packaging repository's DEP-14 tag and release branch | `debian/1.0.16-1+deb12u1`, `debian/bookworm` |
| `curated` / `manual` | what a person named | |
| `packaging_fallback` / `default_branch` | `debian/sid`, `master`, `main` - **explicit fallback only** | |

All of them are probed, and the choice comes from the history:

- the **base** is the exact tag - the release Debian ships;
- the **target** is the maintenance branch that *contains* that tag (the
  release plus later fixes), or the tag itself when there is no maintenance
  branch;
- only when no release ref shares history does a curated ref or, last, a
  default branch become the target, labelled as a fallback.

The choice is recorded in `ref_selection`: ref, kind, strategy, SHA, reason,
Debian upstream version, series, base tag, and whether ARCoS contains the base
tag. `pin_ref: true` on an override keeps the curated ref. Trixie runs through
the same code with its own versions, tags and `debian/trixie` branch.

## Status, verification level, confidence

Three separate fields.

| Verification level | Meaning |
|---|---|
| `NONE` | nothing contacted, or the ref does not exist |
| `REF_EXISTS` | `ls-remote` resolved the ref - the repository exists, nothing more |
| `SHARED_HISTORY` | `git merge-base` found common ancestry with the ARCoS commit |
| `CONTENT_MATCH_APPROVED` | no shared history, but a person approved a content match |

| Status | Meaning |
|---|---|
| `VERIFIED` | upstream identified **and** relationship proven (`SHARED_HISTORY` or `CONTENT_MATCH_APPROVED`, no blocking reason) |
| `NEEDS_REVIEW` | a candidate exists but proof is missing or contradictory |
| `NO_UPSTREAM` | evidence that there is nothing external to track |
| `FAILED` | the resolver could not finish - a probe, fetch or network error |

`REF_EXISTS` alone is never `VERIFIED`. The reason is also a code
(`review_reasons`):

| Code | Meaning | What a person can do |
|---|---|---|
| `NO_CANDIDATE` | no upstream candidate found | add an override, or confirm `no_upstream` |
| `NO_SHARED_HISTORY` | candidates exist, none shares history | review the content match ([below](#no-shared-history)) |
| `CURATED_CONFLICT` | the curated repository shares no history, another candidate does | decide which is right; update `overrides.yaml` |
| `INVALID_REF` | the ref does not exist | fix the override or the metadata |
| `ANCESTRY_NOT_PROBED` | the package is in `ancestry.skip`, or the probe was disabled | re-run with the probe |
| `CONTENT_MATCH_UNAPPROVED` | a content match exists but nobody approved it | `apm approve-content-base` |
| `PROBE_FAILED`, `NETWORK_ERROR`, `ARCOS_UNREACHABLE` | errors: the row is `FAILED` | fix the cause and resolve again |

An error is never turned into a finding: an unreachable candidate is not "no
shared history", and a failed `rev-list` is not "0 behind".

**Category** is separate again and says what kind of package it is:
`debian_upstream`, `debian_no_upstream`, `third_party`, `arrcus_native`,
`vendor`. A vendor blob with no upstream is a finished answer.

## Counts

`Upstream Commits Not In ARCoS (raw)` is `git rev-list --count --no-merges
ARCOS..UPSTREAM` against the selected ref; `ARCoS Commits Not In Upstream
(raw)` is the reverse. They are head-based, so criss-cross history does not
inflate them, and they are labelled *raw*: backports are not excluded. The
real backlog - present, missing, critical - comes from a comparison
([git-comparison.md](git-comparison.md)).

## Plausibility warnings

Suspicious results are flagged, never rejected (`warnings`, and the Needs
Review sheet): thousands of commits behind a release ref
(`plausibility.behind_warning`), thousands of ARCoS-only commits, a merge base
years older than the tip, a ref naming another series than Debian's version,
ARCoS not containing the tag for Debian's version, a development branch used
for a stable Debian release.

## No shared history

Some forks were imported from tarballs and share no history with anything.
They are `NEEDS_REVIEW` / `NO_SHARED_HISTORY`, with no count.
`ContentBaseService` then compares the ARCoS tree (the root import commit and
the shipped commit) with the upstream release tags of the matching series - by
blob id, so no file contents are downloaded - and with Debian's orig tarball,
and records the closest match (`content_match`: tag, score, files and lines
differing, method). Below `content_base.min_score` (0.6) nothing is proposed.

A match is used only after a person approves it:

```bash
docker compose exec app apm approve-content-base --release bookworm --package babeltrace \
    --tag v1.5.11 --verified-by "Full Name" --confirm babeltrace
docker compose exec app apm resolve-release --release bookworm --package babeltrace --no-discover
```

(Or in the dashboard: the content-match box on the resolution card.) The
approval is stored with its provenance - who, when, method, tag, tag SHA,
score - in `out/approvals.yaml`; a permanent one can be moved into a
`content_base:` block in `config/overrides.yaml`. The package then becomes
`CONTENT_MATCH_APPROVED`, and every count says it was synthesized from a
content match.

## Manual resolution

Manual does not mean unchecked. A repository and ref typed into the dashboard
(or sent to `POST /api/upstream/verify`) are `REF_EXISTS` after `ls-remote`,
`VERIFIED` only if they share history with the fork, `NEEDS_REVIEW` /
`NO_SHARED_HISTORY` if not, and `FAILED` if the check could not run.

## Outputs

`apm resolve-release --release <r>` writes:

| File | Contents |
|---|---|
| `out/upstream-mapping-<r>.xlsx` | Sheets **Mapping**, **Summary**, **Needs Review**; status colours; clickable links |
| `out/upstream-mapping-<r>.csv` | The Mapping sheet as CSV |
| `out/upstream-mapping.{csv,xlsx}` | All releases (other releases' rows are carried through) |
| `out/upstream-resolutions.json` | Every field at full fidelity. **The dashboard and the API read this** |

With `--package`, only those packages are re-resolved; the release's other
rows are carried through from the JSON. `apm resolve` is the older
all-releases command: it writes only `out/upstream-mapping.{csv,xlsx}`, which
the dashboard does not read while the JSON exists - use `resolve-release`.

The release workbook's first twenty columns, in order: Package, ARCoS
Repository, ARCoS Path, ARCoS Release, ARCoS Branch, Debian Release, Debian
Source Package, Debian Version, Debian VCS Repository, Debian VCS Branch,
Upstream Repository, Upstream Branch, Upstream Tag, Resolution Mode,
Resolution Status, Resolution Reason, Evidence Source, Evidence URL,
Verification Method, Common Ancestor. Further columns carry the category,
confidence, method, origin kind, verification level, review reasons, counts and
the evidence trail. An unknown value is left empty - an empty cell is a fact.

Check the invariants of a produced mapping:

```bash
docker compose exec app python scripts/verify_output.py
```

## Adding an override

```yaml
packages:
  net-snmp:
    repository: https://github.com/net-snmp/net-snmp.git
    ref: master              # a candidate; release refs are still preferred
    confidence: high
    pin_ref: false           # true: use exactly this ref
    allow_ancestry_replacement: false
    reason: >-
      Debian's watch file points at SourceForge, where releases are published
      rather than developed.
```

Other keys: `debian_source_package` (or `null`), `no_upstream: true`,
`category`, `content_base:` and a `releases:` block for per-release values.
After the change is merged and pulled: `docker compose restart`, then
`apm resolve-release --release <r> --package <p> --no-discover`.
