# Overview

## The problem

ARCoS ships Debian packages from its own forks: one GitHub repository per
package (`Arrcus/mstpd`, `Arrcus/iproute2`, ...), pinned by the release
manifest in `Arrcus/arrcus_rel`. For each fork, someone eventually needs to
know:

1. Which upstream project and which branch or tag it actually tracks.
2. Whether that is proven, or only plausible.
3. Which upstream fixes the fork is missing - and which of those matter.
4. How to pull a fix in without breaking the branch.

That knowledge used to live in people's heads. This tool works it out from
evidence, shows it in a dashboard, and writes it into each package repository
as `debian/upstream.md`, so the answer travels with the code.

## The workflow

```
ARCoS package
 → Debian metadata                     (Sources index, DEP-12, debian/watch)
 → upstream candidates                 (curated, kernel series, DEP-12, watch, homepage)
 → repository / ref verification       (git ls-remote: does it exist?)
 → release-aware upstream selection    (the tag for Debian's version, its maintenance branch)
 → git relationship verification       (git merge-base: shared history?)
 → comparison                          (UPSTREAM ^ARCOS and ARCOS ^UPSTREAM)
 → missing / backported / unknown / critical analysis
 → debian/upstream.md                  (rendered from the verified mapping)
 → dry run                             (builds and checks the exact commit, pushes nothing)
 → create branch → push branch → create GitHub PR    (only with --apply and a confirmation)
 → review → merge                      (people, on GitHub - never the tool)
```

| Stage | Dashboard | CLI (`docker compose exec app apm ...`) | Details |
|---|---|---|---|
| Discover packages | package list | `discover`, `resolve-release` | [upstream-resolution.md](upstream-resolution.md#discovery) |
| Resolve and verify the upstream | **Resolve upstream** | `resolve-release` | [upstream-resolution.md](upstream-resolution.md) |
| Compare | **Compare** | `compare` | [git-comparison.md](git-comparison.md) |
| Critical commits | **Critical** filter, **Select critical** | `compare` | [criticality-and-security.md](criticality-and-security.md) |
| Preview patches | **Preview N selected** | - | [patch-workflow.md](patch-workflow.md) |
| `debian/upstream.md` | **Generate** | `generate-upstream-md` | [upstream-md.md](upstream-md.md) |
| Pull requests | **Create pull request** | `publish-upstream-md` | [upstream-md-publishing.md](upstream-md-publishing.md) |

## The vocabulary

### Resolution status - how completely the upstream is known

| Status | Meaning | Published? |
|---|---|---|
| `VERIFIED` | Upstream identified **and** the relationship proven (shared git history, or a content match a person approved) | Yes, if eligible |
| `NEEDS_REVIEW` | A candidate exists, but proof is missing or contradictory; `review_reasons` says why | Never |
| `NO_UPSTREAM` | There is provably nothing external to track (vendor code, Arrcus-native, ...) | Never |
| `FAILED` | The resolver could not finish (probe, fetch or network error). Not a finding about the package | Never |

### Verification level - how strong the proof is

| Level | Meaning |
|---|---|
| `NONE` | Nothing contacted, or the ref does not exist |
| `REF_EXISTS` | `git ls-remote` found the ref. The repository exists - nothing more. Never enough for `VERIFIED` |
| `SHARED_HISTORY` | `git merge-base` found common ancestry with the ARCoS commit |
| `CONTENT_MATCH_APPROVED` | No shared history, but a person approved a content match against a release tag |

### Presence - is an upstream change already in ARCoS?

| Presence | Plain meaning |
|---|---|
| `DEFINITELY_PRESENT` | ARCoS has it: a cherry-pick trailer or an identical patch |
| `PROBABLY_PRESENT` | Its content is already in the ARCoS tree (an adapted backport) |
| `MISSING` | It is not there and would apply |
| `UNKNOWN` | The evidence does not decide (partly present, not checked, or too large to check) |

**ARCoS-specific** commits are ARCoS's own work: in the fork, not upstream.
They are never counted as missing.

### Criticality - does a missing commit matter?

| Level | Needs |
|---|---|
| `CRITICAL` | A CVE or advisory in the message, an explicit security trailer, or a Debian security patch / OSV record naming the commit as a fix |
| `STABLE_RELEVANT` | `Cc: stable@...` - upstream asked for it in maintenance releases |
| `NORMAL` | A `Fixes:` trailer - a real bug fix, no security evidence |
| `UNKNOWN` | No evidence either way. **Not the same as safe** |

## What the tool never does by itself

- guess an upstream, a branch, or a package list
- call a commit critical because its subject sounds alarming
- count ARCoS's own commits, or commits already backported, as missing
- write to a target branch, force-push, merge, or resolve a conflict
- push or open a pull request without an explicit, confirmed action
- overwrite a `debian/upstream.md` it did not write
- print or store a secret

## Two ways to use it

- **Dashboard** at `http://<server-ip>:8080/` - pick a release, a package and
  the ARCoS branch; resolve, compare, preview, generate.
- **CLI** inside the container - `docker compose exec app apm <command>` - for
  whole-release runs and for publishing. See [cli.md](cli.md).

Both call the same services and read and write the same `out/` directory, so
they always agree.
