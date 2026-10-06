# Git comparison and backport detection

What, exactly, does the ARCoS fork lack from its upstream - and what does it
already have under a different SHA?

## Run it

```bash
docker compose exec app apm compare --release bookworm --package mstpd
docker compose exec app apm compare --release bookworm --package mstpd --refresh   # re-fetch both sides
```

In the dashboard: **Resolve upstream**, then **Compare** on the resolution
card. Comparing needs a proven relationship: `SHARED_HISTORY`, or an approved
content match.

API: `POST /api/comparison` or `GET /api/comparison/{package}?release=bookworm`
([api.md](api.md)).

Every comparison records a snapshot of its headline numbers in
`out/comparisons/`. The snapshot is what lets `debian/upstream.md` state a real
backlog instead of a raw count.

## Version strings are not used

A Debian version says what the packaging claims, not what the branch contains;
two forks at the same version can differ by hundreds of commits. Every number
here comes from the commit graph.

## The sets

Given the ARCoS commit `A` and the selected upstream ref `U` (the release's
maintenance branch or tag - see [upstream-resolution.md](upstream-resolution.md)):

```
relevant upstream   U ^A   upstream commits the ARCoS commit cannot reach
  already present     ... whose change is in ARCoS anyway
  missing             ... whose change is not
  unknown             ... where the evidence does not decide
ARCoS-specific      A ^U   ARCoS's own commits, minus those carrying upstream changes
```

Both are head-based. `git merge-base --all` is still computed, but only to
prove the histories are related: with criss-cross history there are several
merge bases, and counting "everything after one of them" would report ARCoS's
own commits as missing once upstream had merged them.

Worked example - upstream `A→B→C→D→E`, ARCoS `A→B→C→X→Y`:

```
merge base        C
relevant upstream D, E
ARCoS-specific    X, Y
```

Merges are excluded throughout (`--no-merges`): a merge commit cannot be
cherry-picked. When the upstream ref has a base tag (the tag for Debian's
version), each missing commit is also marked `in_base_release` - part of the
release Debian ships, as opposed to a later fix in the series.

## Backport detection, in tiers

A backport's SHA always differs, and an adapted backport's diff does too, so no
single test is enough. Each relevant upstream commit gets the strongest answer
the evidence supports (`src/apm/services/backport_service.py`):

| Presence | Evidence |
|---|---|
| `DEFINITELY_PRESENT` | an ARCoS commit carries `(cherry picked from commit X)` (or the kernel's `commit X upstream`, `[ upstream commit X ]`, `backport of X`), or an identical `git patch-id --stable` |
| `PROBABLY_PRESENT` | `git apply --check -R` succeeds against the ARCoS tree, or - for an adapted backport whose context differs - every line it adds is already in each file it touches |
| `MISSING` | `git apply --check` succeeds and the reverse does not; or none of its lines are present; or ARCoS applied it and later reverted it |
| `UNKNOWN` | partially present (some files only), empty, beyond the content-check limit, or undecidable |

In plain words: **missing** means "not in ARCoS and it would apply";
**already present** means "ARCoS has this change, perhaps under another SHA";
**unknown** means "the tool could not tell - look at it".

Reverts are paired: an upstream commit reverted later in the same range nets
to nothing and is counted as `reverted_upstream`, not missing.

The content checks run in one shell call against a private index built from
the ARCoS commit; nothing is checked out or written.

## Debian patches

The quilt patches of the shipped Debian version (`debian/patches/series`) are
checked against the ARCoS tree too: carried in ARCoS's own series
(`DEFINITELY_PRESENT`), already applied to the source (`PROBABLY_PRESENT`),
applying cleanly (`MISSING`), or neither (`UNKNOWN`). They are reported beside
the commit lists and never counted among them: they are packaging, not
upstream commits.

## Security evidence

Each commit also gets a criticality from evidence - see
[criticality-and-security.md](criticality-and-security.md).

## No common ancestor

A fork imported from a tarball has none. The comparison refuses
(`NO_COMMON_ANCESTOR`, HTTP 422) rather than inventing a number - unless a
person approved a content match, in which case the ranges are
`U ^<approved tag>` and `A ^<matched ARCoS tree>`, and the result says
`synthesized_ancestry: true` in the summary, the warnings and the basis.

## Reading the result

The dashboard's **Comparison** card shows:

| Figure | Meaning |
|---|---|
| Relevant upstream commits | `U ^A` |
| Already present (N definite) | `DEFINITELY_PRESENT` + `PROBABLY_PRESENT` |
| Missing | `MISSING` |
| Unknown | `UNKNOWN` |
| Critical/security missing | missing commits with `CRITICAL` evidence |
| Stable-nominated | missing commits with `Cc: stable` |
| ARCoS-specific | `A ^U` minus commits carrying upstream changes |

Filters: **Missing upstream**, **Critical**, **Stable**, **Normal**,
**Unknown**, **Already backported**, **ARCoS-specific**.

`apm compare` prints the same summary:

```
mstpd (bookworm)
  ARCoS commit        <sha>
  upstream            https://github.com/mstpd/mstpd.git @ master (<sha>)
  upstream base tag   ...   series ...
  basis               ...
  relevant upstream   N
    already present   N (definitely N, probably N)
    missing           N
    unknown           N
    reverted upstream N
  critical missing    N   stable N
  ARCoS-specific      N
  backport detection  ...
  Debian patches      ...
  security source     debian: N finding(s)
  security source     osv: N finding(s) (queried N base commit(s))
```

## Stale results

The comparison always fetches both refs. If the upstream ref has moved since
the mapping was made, it says so. Snapshots are stored per exact pair of
commits under `out/comparisons/<release>/<package>/`, and are handed to the
dashboard, the mapping and `debian/upstream.md` only while a resolution still
names exactly those commits.

## Fetching: blobless for the graph, blobs only when diffing

Both sides are fetched with `--filter=blob:none`: walking the commit graph
needs no file contents. `patch-id` and the content checks do need them, so
blobs are backfilled in one pass - and only when the range is small enough for
backport detection to be worth running (5000 commits). Beyond that the
comparison skips both the download and the diffing and reports backport
detection as unavailable rather than pretending none exist.
`comparison.content_check_limit` (1000) caps the commits checked by content;
beyond it they are `UNKNOWN`, never assumed missing.

## Cost

Workspaces are keyed by package and release (`repos/<package>__<release>` in
the workspace directory) and reused, so the expensive part is paid once.
Measured examples:

| | cold | warm |
|---|---|---|
| `pyrad` (109 + 19 commits) | ~13s | ~12s |
| `iputils` (142 + 272) | ~15s | ~12s |
| `linux` (24,158 + 97) | ~574s | ~11s |

`APM_GIT_LONG_TIMEOUT` (900s by default; `.env.example` sets 1800) bounds a
stall without cutting off a legitimate first clone, and the dashboard waits
longer still so the backend's explicit `TIMEOUT` surfaces instead of a generic
client abort.

## Truncation

Commit lists are capped at 500. When the cap applies, the summary still
reports the true total, a warning says what was shown, and unassessed commits
are counted as unknown.
