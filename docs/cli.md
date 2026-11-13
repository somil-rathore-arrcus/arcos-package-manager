# CLI reference: `apm`

## Running it

| Where | How |
|---|---|
| Docker (standard) | `docker compose exec app apm <command> [options]` from the checkout directory. Uses the running container's environment and the same `out/` as the dashboard |
| One-off container | `docker compose run --rm app apm <command>` - a fresh container that reads `.env` anew; use it when the server is stopped |
| Development | `scripts/apm <command>` (the project's `.venv`) |

`apm --help` and `apm <command> --help` print the options. `-v` / `--verbose`
before the command adds progress logging: `apm -v compare --package mstpd`.

Options shown as *repeatable* may be given more than once
(`--package a --package b`).

| Command | Purpose | Writes | Remote effect |
|---|---|---|---|
| [`discover`](#discover) | Read the release manifests → package catalogue | `out/packages.yaml` | reads `arrcus_rel` |
| [`resolve-release`](#resolve-release) | Discover + resolve one release + its report | mapping files | reads only |
| [`resolve`](#resolve) | Resolve every release into one sheet (older) | `out/upstream-mapping.{csv,xlsx}` | reads only |
| [`compare`](#compare) | Full comparison; records the snapshot | `out/comparisons/` | reads only |
| [`approve-content-base`](#approve-content-base) | Record a person's approval of a content match | `out/approvals.yaml` | reads only |
| [`generate-upstream-md`](#generate-upstream-md) | Render `debian/upstream.md` + the PR plan | `out/upstream-md-<release>/`, plan | reads only |
| [`publish-upstream-md`](#publish-upstream-md) | Push the reviewed files, one branch per package | ledgers, links file, publishing report | **pushes only with `--apply`, and only where `APM_ALLOW_PUSH` is set; never opens a PR** |
| [`upstream-md-report`](#upstream-md-report) | Rebuild the Bookworm/Trixie publishing report | `out/upstream-md-publishing-report.{md,json}` (+ verification with `--verify`) | reads only |
| [`doctor`](#doctor) | Check storage, git and GitHub access | nothing | reads only |

## discover

```
apm discover [--json] [--release RELEASE]
```

Reads `.gitmodules` and the pinned commits from each release's manifest branch
of `Arrcus/arrcus_rel` and writes the catalogue to `APM_PACKAGES_FILE`
(`out/packages.yaml` in the container).

| Option | Meaning |
|---|---|
| `--json` | Print the packages as JSON (package, repository, path, releases, branches) |
| `--release` | With `--json`, only packages shipping in this release |

```bash
docker compose exec app apm discover
#   discovered 53 packages -> /app/out/packages.yaml
#     bookworm: 53
#     trixie: 44
```

## resolve-release

```
apm resolve-release [--release RELEASE] [--out-dir OUT_DIR] [--package PACKAGE]
                    [--no-discover] [--no-ancestry]
```

Discovers, resolves every package of one release, prints a summary (statuses,
verification levels, review reasons, warnings) and writes
`out/upstream-mapping-<release>.{csv,xlsx}`, `out/upstream-mapping.{csv,xlsx}`
and `out/upstream-resolutions.json`. **The standard way to refresh the
mapping.**

| Option | Default | Meaning |
|---|---|---|
| `--release` | `bookworm` | The release |
| `--package` | all | Only this package; repeatable. The release's other rows are kept |
| `--no-discover` | | Reuse the catalogue on disk instead of re-reading the manifest |
| `--no-ancestry` | | Skip the merge-base probe - faster, but nothing can be `VERIFIED` |
| `--out-dir` | `out/` | Output directory |

```bash
docker compose exec app apm resolve-release --release bookworm
docker compose exec app apm resolve-release --release bookworm --package mstpd --no-discover
```

## resolve

```
apm resolve [--release RELEASE] [--package PACKAGE] [--out-dir OUT_DIR] [--no-ancestry]
```

Resolves the catalogue (all releases, or `--release`, repeatable) into
`out/upstream-mapping.{csv,xlsx}` only. It does not write
`upstream-resolutions.json`, which is what the dashboard reads - prefer
`resolve-release`.

## compare

```
apm compare [--release RELEASE] --package PACKAGE [--refresh]
```

Runs the full comparison for each package and records the snapshot that the
`debian/upstream.md` pull request body uses for its backlog row (the file
itself does not change). Prints the summary
described in [git-comparison.md](git-comparison.md#reading-the-result).
Exit code 1 if any package failed.

| Option | Default | Meaning |
|---|---|---|
| `--release` | `bookworm` | The release |
| `--package` | required | Repeatable |
| `--refresh` | | Discard the workspace and fetch both sides again |

## approve-content-base

```
apm approve-content-base [--release RELEASE] --package PACKAGE [--tag TAG]
                         --verified-by VERIFIED_BY --confirm CONFIRM [--note NOTE]
```

For a fork with no shared history: records that a person approved the content
match, with who, when, tag, tag SHA, method and score, in `out/approvals.yaml`.
Takes effect when the package is next resolved.

| Option | Meaning |
|---|---|
| `--tag` | The tag to approve (default: the best content match) |
| `--verified-by` | The person accountable for the decision |
| `--confirm` | Must repeat the package name |
| `--note` | Free text |

```bash
docker compose exec app apm approve-content-base --release bookworm --package rsyslog \
    --verified-by "Full Name" --confirm rsyslog
docker compose exec app apm resolve-release --release bookworm --package rsyslog --no-discover
```

## generate-upstream-md

```
apm generate-upstream-md [--release RELEASE] [--branch BRANCH] [--package PACKAGE]
                         [--out-dir OUT_DIR] [--include-needs-review] [--offline]
```

Renders `debian/upstream.md` - the two fields `Upstream Source` and
`Current Upstream Branch` - for the release from the mapping, compares it with
each fork (read-only) and writes the files, to
`out/upstream-md-<release>/<package>/debian/upstream.md` (one folder per
release), and the plan. After the summary it
lists the packages with review flags (`Flagged for review (N) ...`, codes only;
the plan has the detail). Nothing remote is written. See
[upstream-md.md](upstream-md.md).

| Option | Default | Meaning |
|---|---|---|
| `--release` | `bookworm` | The release |
| `--branch` | all | Only packages on this ARCoS branch (`aminor`) |
| `--package` | all | Repeatable. **Replaces the plan with only these packages** |
| `--include-needs-review` | | Also write files for `NEEDS_REVIEW` packages; both fields read `N/A - not verified (status NEEDS_REVIEW)` |
| `--offline` | | Do not read the forks; outcomes become guesses |
| `--out-dir` | `out/` | Output directory |

```bash
docker compose exec app apm generate-upstream-md --release bookworm --branch aminor
```

## publish-upstream-md

```
apm publish-upstream-md [--release RELEASE] [--out-dir OUT_DIR] [--plan PLAN]
                        [--package PACKAGE | --all] [--dry-run | --apply]
                        [--confirm CONFIRM] [--confirm-count CONFIRM_COUNT]
                        [--push-only]
                        [--title-prefix TITLE_PREFIX] [--limit LIMIT]
                        [--stop-after-failures STOP_AFTER_FAILURES]
                        [--retry-failed] [--recheck] [--exclude EXCLUDE] [--show-diff]
```

A dry run unless `--apply`. `--apply` pushes one branch per package and
stops: it **never opens a pull request** and needs no token. Each package ends
`BRANCH_PUSHED` with its Compare Link and a pre-filled link to open the PR, or
`COVERED` when another release's branch already proposes the identical file to
the same target. After every apply, `out/upstream-md-pr-links-<release>.md` is
rewritten with the links of every branch still waiting for a pull request, and
the [publishing report](#upstream-md-report) is rebuilt. A later run leaves
`BRANCH_PUSHED` and `COVERED` packages alone, so `--limit` moves on. A dry run
writes the links it would give to
`out/upstream-md-pr-links-dryrun-<release>.md`. `--apply` runs only where
`APM_ALLOW_PUSH` is set; anywhere else it stops before pushing. `--push-only`
is accepted and changes nothing; `--draft` was removed. Every option, the
confirmation rules, the statuses and the ledgers are in
[upstream-md-publishing.md](upstream-md-publishing.md).

```bash
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --show-diff
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --apply --confirm mstpd
```

Exit codes: 0 success; 1 a package failed (or no plan / no packages named);
2 refused before pushing (confirmation mismatch, or pushing disabled on this
machine: `APM_ALLOW_PUSH` is not set).

## upstream-md-report

```
apm upstream-md-report [--out-dir OUT_DIR] [--verify]
```

Rebuilds `out/upstream-md-publishing-report.md` (and `.json`) from the reviewed
plans and the results ledgers: one section per release - Bookworm, Trixie -
with the branches pushed and waiting for a pull request (each with its
Compare Link and the link to its `debian/upstream.md`), the packages another
release's branch covers, and every package not pushed with the reason.
`publish-upstream-md --apply` and a push from the dashboard rebuild it
automatically; run this by hand to check, or after `--verify`. Opens no pull
request.

| Option | Default | Meaning |
|---|---|---|
| `--verify` | | First read every pushed branch back through git (`ls-remote`, then a shallow blobless fetch): at the recorded commit, changing only `debian/upstream.md`, holding the reviewed file. Writes `out/upstream-md-publishing-verification.json`; writes nothing remote |
| `--out-dir` | `out/` | Output directory |

```bash
docker compose exec app apm upstream-md-report --verify
```

Exit code 1 when `--verify` finds a branch not as recorded, else 0. Dated
reports written by hand (`out/upstream-md-branches-<date>.md`) are never
touched. Details:
[upstream-md-publishing.md → The publishing report](upstream-md-publishing.md#the-publishing-report).

## doctor

```
apm doctor [--release RELEASE] [--repo REPO] [--skip-git]
```

Checks, without printing any secret:

- **storage** - `out/`, the runtime catalogue, the approvals file and the cache are writable;
- **git** - backend, workspace, `arrcus_rel` access, each `--repo`, public GitHub access, and the GitHub account the SSH key authenticates as;
- **pushing** - whether `APM_ALLOW_PUSH` lets this machine push (`WARN` if not: comparisons and dry runs only);
- **dashboard API** - whether `APM_API_TOKEN` is set;
- **GitHub API** - whether a token is set (`value not shown`; not needed, as the tool only reads GitHub), the API is reachable, and the token can see each `--repo`.

| Option | Default | Meaning |
|---|---|---|
| `--release` | `bookworm` | Which manifest branch to check |
| `--repo` | | `owner/name` to check git and token access against; repeatable |
| `--skip-git` | | Skip the git checks |

Ends with `FAILURES: none` (exit 0) or the list of failed checks (exit 1). A
missing token is a `PASS` (it is not needed), and a missing `APM_ALLOW_PUSH`
a `WARN` - neither is a failure.

## Helper scripts

Run inside the container with `docker compose exec app python scripts/<name>`:

| Script | Checks |
|---|---|
| `verify_output.py [csv]` | Invariants of the produced mapping (e.g. a kernel series matching its Debian version) |
| `verify_upstream_md.py [release]` | Each generated `debian/upstream.md` in `out/upstream-md-<release>/` is exactly the two fields, its values match the mapping, and the plan records the same values, flags and hashes ([checks](upstream-md.md#what-the-verifier-checks)) |
| `verify_pr_ledger.py [release]` | The results ledger records nothing the workflow may never do: every `BRANCH_PUSHED` entry links to its own branch and records no PR, every `COVERED` entry names its covering branch and pushed nothing, and no entry is `PR_OPENED` |

Development scripts (host, no Docker): `setup-venv.sh`, `build-frontend.sh`,
`server.sh start|stop|restart|status|logs`, `dev-backend.sh`,
`dev-frontend.sh`, `test.sh`, `generate_frontend_types.py`.
