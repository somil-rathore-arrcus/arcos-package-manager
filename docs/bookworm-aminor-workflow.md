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
- new branch: `upstream-metadata/<package>`
- commit message: `docs: add upstream metadata`
- the diff, where there is one

Nothing is pushed, no branch is created and no pull request is opened. The
plan's JSON says so in `"pushed": false` and `"pull_requests_created": false`.
No GitHub token is needed for any of this, and the dashboard shows **Read-only
mode — GitHub write operations disabled** until one is configured.

When write operations are enabled later, `POST /api/upstream-md/pr` performs the
push and the PR for a single package, and requires `confirm: true`. The target
branch is never committed to: the file always lands on a new branch.

## 5. The dashboard

`GET /api/reports` lists the generated files and the dashboard offers them for
download. The resolution card shows ARCoS repository, path, release and branch;
the Debian source, version, VCS repository and VCS branch; the upstream
repository with its branch or tag; and the evidence source, URL and verification
behind the answer. Every repository URL is a link.

The dashboard reads `out/upstream-mapping.csv` — the same file the workbook is
rendered from in the same run — so the two cannot disagree.

## What this workflow does not do

- It does not push, open pull requests or modify any remote repository.
- It does not write into the real package repositories; generated files stay
  under `out/`.
- It does not ask for a GitHub token.
- It does not guess a branch, hard-code a package list or carry a hand-written
  upstream mapping. Curated entries live in `config/overrides.yaml`, are
  declared as curated, and are still checked by the ancestry probe.
