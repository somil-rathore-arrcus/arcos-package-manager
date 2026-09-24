# Bookworm / aminor workflow

One release, end to end: discover what ARCoS ships, work out where each package
really comes from, write the mapping out, and render `debian/upstream.md` for
every package whose upstream was proven — without touching a single remote.

Trixie is not special-cased anywhere. It runs through the same commands with
`--release trixie`; only the default differs.

## 1. Discover

```bash
PYTHONPATH=src python -m apm discover --json --release bookworm
```

The package set is read at runtime from `.gitmodules` on the `aminor` branch of
`Arrcus/arrcus_rel`, together with the commit the superproject pins for each
submodule. Nothing is hard-coded: the count is whatever the manifest holds.

```json
[
  {
    "package": "iputils",
    "repository": "Arrcus/iputils",
    "path": "packages/iputils",
    "releases": ["bookworm", "trixie"],
    "branches": {"bookworm": "aminor", "trixie": "aminor"}
  }
]
```

**Not every package lives under `packages/`.** Five of them — `linux`,
`ONL-standalone`, `ONL-xc`, `arcos-upgrade` and `medusa-bootstrap` — are
submodules at the top level of the manifest. They ship in the release like any
other package, so discovery takes every submodule and reports the path rather
than filtering on it. Restricting discovery to `aminor/packages/` would silently
drop the kernel.

## 2. Resolve and report

```bash
PYTHONPATH=src python -m apm.resolve_bookworm
```

Prints progress per package, then:

```
Resolution summary:

  VERIFIED       28
  NEEDS_REVIEW    3
  NO_UPSTREAM    21

  TOTAL          52

Generated:

  out/upstream-mapping-bookworm.xlsx
  out/upstream-mapping-bookworm.csv
  out/upstream-mapping.csv   (all releases, canonical)
  out/upstream-mapping.xlsx  (all releases, canonical)
```

Resolution itself is unchanged and documented in
[upstream-resolution.md](upstream-resolution.md): an ordered metadata chain
proposes a candidate, `git merge-base` decides, and every upstream is contacted
with `ls-remote` before it is reported. No branch is assumed — not `main`, not
`master`, not `aminor`, not the Debian branch. Where nothing can be established
the row is `NEEDS_REVIEW` with the reason recorded; where there is provably
nothing to track it is `NO_UPSTREAM`, which is a finished answer rather than a
failure.

The release workbook has three sheets — **Mapping**, **Summary**, **Needs
Review** — and its first twenty columns are the ones the requirement asks for,
in order:

| # | Column | # | Column |
|---|---|---|---|
| 1 | Package | 11 | Upstream Repository |
| 2 | ARCoS Repository | 12 | Upstream Branch |
| 3 | ARCoS Path | 13 | Upstream Tag |
| 4 | ARCoS Release | 14 | Resolution Mode |
| 5 | ARCoS Branch | 15 | Resolution Status |
| 6 | Debian Release | 16 | Resolution Reason |
| 7 | Debian Source Package | 17 | Evidence Source |
| 8 | Debian Version | 18 | Evidence URL |
| 9 | Debian VCS Repository | 19 | Verification Method |
| 10 | Debian VCS Branch | 20 | Common Ancestor |

Repository, evidence and link columns are clickable. Anything unknown is left
empty — an empty cell is a fact, and filling it with a plausible value is not.

**Upstream branch and upstream tag are separate columns.** A tag is a fixed
point and a branch keeps moving; which one a package tracks comes from the same
`ls-remote` that proved the ref exists, so it is recorded rather than guessed.

**Debian VCS repository is never the upstream.** It is the packaging repository,
carried in its own two columns. The only exception is a package whose fork
genuinely descends from it, and that is proven by `merge-base` and labelled
`origin_kind = debian_packaging`.

Then check the invariants:

```bash
./.venv/bin/python scripts/verify_output.py
```

## 3. Generate debian/upstream.md

```bash
PYTHONPATH=src python -m apm.generate_upstream_md --release bookworm --branch aminor
```

Writes one file per package under:

```
out/upstream-md/<package>/debian/upstream.md
```

rendered from the same resolution the mapping and the dashboard use. There is no
second mapping to keep in step.

Before writing, the file already in each fork is read (read-only) and the
outcome reported:

| Outcome | Meaning |
|---|---|
| `CREATED` | The fork has no `debian/upstream.md` |
| `UPDATED` | The fork has one this tool wrote, and it has changed |
| `NO_CHANGE` | Identical to what is already committed |
| `CONFLICT` | A file this tool did not write — never replaced silently |

A fork that could not be read is listed separately: its outcome is what *would*
happen if no file is there, which is not the same as knowing none is.

By default only `VERIFIED` packages get a file. `--include-needs-review` also
writes one for unresolved packages; it records the status and the reason and
never invents an upstream. `NO_UPSTREAM` packages get no file at all.

## 4. The PR plan — a description, not an action

The same command writes `out/upstream-md-plan-bookworm.json` and a readable
`.md` beside it:

- base branch: the branch the release manifest names (`aminor`)
- new branch: `upstream-metadata/bookworm/<package>`
- commit message: `<package>: add debian/upstream.md with verified upstream`
- the SHA-256 and size of each generated file, and the diff where there is one

Generating pushes nothing, creates no branch and opens no pull request; the
plan's JSON says so in `"pushed": false` and `"pull_requests_created": false`.

## 4a. Proposing the files — `apm publish-upstream-md`

Once the plan and the files are reviewed:

```bash
# Dry run (the default): every read, and the exact commit, but no push.
apm publish-upstream-md --release bookworm --package mstpd --show-diff

# One package for real.
apm publish-upstream-md --release bookworm --package mstpd --apply --confirm mstpd

# The rest, after the first PR has been reviewed.
apm publish-upstream-md --release bookworm --all --apply --confirm-count 26
```

For each package the publisher:

1. Checks that the file still hashes to the plan's SHA-256 and still renders
   from today's mapping. If not, the package is `DRIFT`: regenerate and review.
2. Reads the live tip of the target branch, and whether
   `upstream-metadata/<release>/<package>` already exists.
3. Fetches that tip (depth 1, blobless) into a scratch workspace on the bridge
   and compares the existing `debian/upstream.md`: `NO_CHANGE`, `CONFLICT` for
   a file this tool did not write, otherwise `CREATED` or `UPDATED`.
4. Builds the commit from objects: the tip's tree plus exactly one entry. No
   branch is checked out, and nothing else can reach the commit. It checks
   that the changed paths are exactly `debian/upstream.md`, that `git diff
   --check` is clean, and that the parent is the tip. **A dry run stops here.**
5. Pushes `<commit>:refs/heads/upstream-metadata/<release>/<package>`, never
   forced.
6. Opens the pull request against the target branch. The body carries the
   `Problem Description :`, `Root Cause :` and `Fix Details :` sections that
   ARCoS CI requires.
7. Records the result in `out/upstream-md-pr-results-bookworm.json` and `.md`
   before moving to the next package.

What it never does: write to the target branch, force-push, merge, commit a
second file, replace a hand-written file, or propose a `NEEDS_REVIEW` or
`NO_UPSTREAM` package. Packages listed under `upstream_md_publish.exclude` in
`config/settings.yaml` (currently the two ONL packages, whose repository has no
`debian/`) are recorded as `SKIPPED_EXCLUDED`.

A failure in one package is recorded and the run continues; three consecutive
failures stop it, because that usually means the token or the bridge.
Re-running resumes from the ledger: packages with an open PR are left alone,
`--retry-failed` re-runs only failures, and a branch this run pushed but could
not open a PR for goes straight to the PR step. A branch this tool has no record
of creating is `BRANCH_EXISTS` and is left alone, and a closed PR is `PR_CLOSED`
and is not re-proposed.

`scripts/verify_pr_ledger.py` checks the ledger afterwards.
`POST /api/upstream-md/pr` runs the same publisher for one package, with
`confirm: true`, or `dry_run: true`. `GET /api/upstream-md/prs` returns the
ledger.

## 5. The dashboard

`GET /api/reports` lists the generated files and the dashboard offers them for
download. The resolution card shows ARCoS repository, path, release and branch;
the Debian source, version, VCS repository and VCS branch; the upstream
repository with its branch or tag; and the evidence source, URL and verification
behind the answer. Every repository URL is a link.

The dashboard reads `out/upstream-mapping.csv` — the same file the workbook is
rendered from in the same run — so the two cannot disagree.

## What this workflow does not do

- Generation does not push, open pull requests or modify any remote repository,
  and does not ask for a GitHub token. Only `publish-upstream-md --apply` (or
  the confirmed API call) writes, and only to `upstream-metadata/` branches.
- It never commits to a target branch, force-pushes or merges.
- It does not guess a branch, hard-code a package list or carry a hand-written
  upstream mapping. Curated entries live in `config/overrides.yaml`, are
  declared as curated, and are still checked by the ancestry probe.
