# Upstream resolution

## The problem

Debian does not record where a package's code lives in one reliable field, and
ARCoS forks are not all forks of the same kind of thing. Some track the upstream
project; others track the Debian packaging repository; a few were imported from
tarballs and share no history with anything.

Guessing is worse than saying nothing: a confident but wrong upstream produces a
diff that looks authoritative and means nothing.

## The chain

Ordered. First hit wins, and every step records why.

| Method | Source | Confidence |
|---|---|---|
| `curated` | `config/overrides.yaml` | as declared |
| `kernel_series` | Debian kernel version → `linux-X.Y.y` | high |
| `dep12` | `debian/upstream/metadata` `Repository:` | high |
| `watch_forge` | `debian/watch`, only when it names a git forge | medium |
| `homepage_forge` | `Homepage:` normalised to a forge repo | medium |

`Vcs-Git` is never treated as upstream by the chain — it is the Debian
*packaging* repository, carried in its own column instead.

A URL only becomes a repository when the conversion is justified.
`https://lldpd.github.io` is refused: the organisation is knowable, the
repository is not, and inferring `lldpd/lldpd` from it would be a coincidence.

## Ancestry decides which repository

Metadata gives a plausible upstream. Shared history proves it.

```
lttng-tools   github.com/lttng/lttng-tools        NO SHARED HISTORY
              salsa.debian.org/debian/ltt-control merge-base 59bc0559
```

ARCoS forked the **Debian packaging repository** for some packages and the
**project's own repository** for others, and nothing in the metadata
distinguishes the two, so `AncestryChecker` measures it with `git merge-base`
against every candidate - including the packaging repository - and the first
repository (in priority order) that shares history wins.

**A curated repository is never silently replaced.** If `config/overrides.yaml`
names repository A, A shares no history, and B does, the row is
`NEEDS_REVIEW` with reason `CURATED_CONFLICT`; the curated mapping stays the
upstream and B is recorded beside it. Replacement happens only when a person
allows it (`allow_ancestry_replacement: true` on the override, or
`ancestry.allow_curated_replacement` in settings), and the curated decision
stays visible in the evidence either way. A candidate that came only from
metadata (DEP-12, watch, Homepage) may still be corrected by history.

## The release decides which ref

The question is never "how far behind master". For a Bookworm package the
candidate refs (`apm/upstream/refs.py`) are, in order:

| Strategy | Ref | Example |
|---|---|---|
| `exact_tag` | the upstream tag for Debian's upstream version (debian/watch's pattern first, then tag-name heuristics) | `v6.1.0`, `openssl-3.0.20`, `libnl3_7_0` |
| `maintenance_branch` / `kernel_series` | the branch of that version's major.minor series | `openssl-3.0`, `linux-6.1.y`, `stable-0.13` |
| `packaging_tag` / `packaging_branch` | the Debian packaging repository's DEP-14 tag and release branch | `debian/1.0.16-1+deb12u1`, `debian/bookworm` |
| `curated` / `manual` | what a person named | |
| `packaging_fallback` / `default_branch` | `debian/sid`, `master`, `main` - **explicit fallback only** | |

All of them are probed, and the choice is made from what the history says:

- the **base** is the exact tag (the shipped release itself);
- the **target** is the maintenance branch that *contains* that tag - the
  release plus the fixes made after it - or the tag itself when the project
  keeps no maintenance branch;
- only when no release ref shares history does a curated ref or, last, a
  default branch become the target, and `Ref Selection` says `FALLBACK`.

The choice is recorded as evidence (`ref_selection`: ref, kind, strategy, SHA,
reason, Debian upstream version, series, base tag, whether ARCoS contains it).
Trixie runs through the same code with its own version, tags and
`debian/trixie` branch. `pin_ref: true` on an override keeps the curated ref.

## Status, verification level and confidence

Three separate fields:

| Verification level | Meaning |
|---|---|
| `NONE` | nothing contacted, or the ref does not exist |
| `REF_EXISTS` | `ls-remote` resolved the ref - the repository exists, nothing more |
| `SHARED_HISTORY` | `git merge-base` found common ancestry with the ARCoS commit |
| `CONTENT_MATCH_APPROVED` | no shared history, but a person approved a content match |

| Status | Meaning |
|---|---|
| `VERIFIED` | canonical upstream identified **and** relationship proven (`SHARED_HISTORY` or `CONTENT_MATCH_APPROVED`, no blocking reason) |
| `NEEDS_REVIEW` | a candidate exists but proof is missing or contradictory |
| `NO_UPSTREAM` | evidence that there is genuinely nothing external to track |
| `FAILED` | the resolver could not finish - a probe, fetch or network error |

`REF_EXISTS` alone is never `VERIFIED`. The reason is also a code
(`review_reasons`): `NO_SHARED_HISTORY`, `CURATED_CONFLICT`, `INVALID_REF`,
`ANCESTRY_NOT_PROBED`, `CONTENT_MATCH_UNAPPROVED`, `NO_CANDIDATE`, and the error
codes `PROBE_FAILED`, `NETWORK_ERROR`, `ARCOS_UNREACHABLE` that make a row
`FAILED`. An error is never turned into a finding: an unreachable candidate is
not "no shared history", and a failed `rev-list` is not "0 behind".

## Counts

`Upstream Commits Not In ARCoS (raw)` is `git rev-list --count --no-merges
ARCOS..UPSTREAM` against the selected ref, and `ARCoS Commits Not In Upstream
(raw)` the reverse. They are head-based, so criss-cross history and repeated
merges do not inflate them, and they are labelled as raw: backports are not
excluded. The real backlog - present, missing, critical - comes from a
comparison (`apm compare`), and `debian/upstream.md` states it only when a
comparison exists for exactly the same two commits.

## Plausibility

Suspicious results are flagged, never rejected (`warnings`, and the Needs
Review sheet): thousands of commits behind a release ref, thousands of ARCoS-only
commits, a merge base years older than the tip, a ref naming another series than
Debian's version, ARCoS not containing the tag for Debian's version, and a
development branch used for a stable Debian release.

## No shared history

Some forks were imported from tarballs. They are `NEEDS_REVIEW` /
`NO_SHARED_HISTORY` with no count. `ContentBaseService` then compares the ARCoS
root import commit (and the shipped commit) with the upstream release tags of
the matching series - by blob id, so no file contents are downloaded - and with
Debian's orig tarball, and records the closest match (`content_match`: tag,
score, files and lines differing, method). It is used only after a person
approves it:

```bash
apm approve-content-base --release bookworm --package babeltrace \
    --tag v1.5.11 --verified-by "Somil Rathore" --confirm babeltrace
apm resolve-release --release bookworm --package babeltrace --no-discover
```

The approval is stored with its provenance (who, when, method, tag, tag SHA,
score) in `out/approvals.yaml` (or a `content_base:` block in
`config/overrides.yaml`). The row becomes `CONTENT_MATCH_APPROVED`, and every
count says it was synthesized from a content match.

## Caching

Probe results are keyed by the ARCoS commit and every candidate ref's commit as
`ls-remote` reports them, so a moved upstream is probed again; a TTL is the
backstop. Failures are never cached.

## Manual resolution

Manual does not mean unchecked. A user-supplied repository and ref are
`REF_EXISTS` after `ls-remote`, `VERIFIED` only if they share history with the
fork, `NEEDS_REVIEW` / `NO_SHARED_HISTORY` if not, and `FAILED` if the check
could not run.

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
