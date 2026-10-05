# ARCoS Package Manager

For any ARCoS package, works out where its upstream actually is, how far behind
it the fork has fallen, which of the missing commits carry evidence of mattering,
and — on explicit request — prepares a branch and a pull request pulling them in.

React + TypeScript frontend, FastAPI backend, git and Debian archive metadata
underneath. No database.

## Quick start

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # then edit: see docs/deployment.md

./scripts/dev-backend.sh        # http://127.0.0.1:8000
./scripts/dev-frontend.sh       # http://localhost:5173
```

Or, on a host, as one process with no Docker, Node or root (dashboard and API on
port 8080 - see docs/deployment.md):

```bash
scripts/setup-venv.sh && scripts/build-frontend.sh && scripts/server.sh start
```

Or the whole stack in Docker:

```bash
docker compose up -d --build    # http://localhost:8080
```

The mapping pipeline also runs standalone:

```bash
PYTHONPATH=src ./.venv/bin/python -m apm discover   # -> config/packages.yaml
PYTHONPATH=src ./.venv/bin/python -m apm resolve    # -> out/upstream-mapping.{csv,xlsx}
```

For one release end to end - discovery, resolution, workbook and the
`debian/upstream.md` files - see
[docs/bookworm-aminor-workflow.md](docs/bookworm-aminor-workflow.md):

```bash
PYTHONPATH=src ./.venv/bin/python -m apm.resolve_bookworm
#   -> out/upstream-mapping-bookworm.{csv,xlsx}

PYTHONPATH=src ./.venv/bin/python -m apm.generate_upstream_md \
    --release bookworm --branch aminor
#   -> out/upstream-md/<package>/debian/upstream.md
#   -> out/upstream-md-plan-bookworm.{json,md}
```

Neither pushes, creates a branch or opens a pull request, and neither needs a
GitHub token.

Proposing the reviewed files is a separate, explicit step - a dry run unless
`--apply` is given:

```bash
PYTHONPATH=src ./.venv/bin/python -m apm publish-upstream-md --package mstpd
PYTHONPATH=src ./.venv/bin/python -m apm publish-upstream-md --package mstpd \
    --apply --confirm mstpd
#   -> one branch upstream-metadata/bookworm/<package>, one commit, one PR
#   -> out/upstream-md-pr-results-bookworm.{json,md}
```

## What it does

### 1. Discovery — what ARCoS actually ships

`Arrcus/arrcus_rel` tracks every shipped package as a git submodule, so
`.gitmodules` on the release branch *is* the package list. Two things about it
are easy to get wrong, and both change the answer:

- **There is a separate manifest branch per Debian release.** Bookworm and Trixie
  genuinely differ: Trixie drops eight packages and moves fourteen onto a
  different branch. Reading one manifest and assuming it covers both invents
  packages that do not ship.
- **The `branch = ...` line in `.gitmodules` goes stale.** What ships is the
  commit the superproject pins. The Trixie manifest still says `linux` tracks
  `aminor` while pinning a 6.12 commit that branch does not contain — trusting
  the branch field there pairs a 6.1 fork against `linux-6.12.y` and reports a
  six-figure, meaningless backlog.

### 2. Debian metadata — without apt or Docker

Source metadata comes from the archive's `Sources` index, which is the same data
`apt-cache showsrc` reads. Bookworm carries 34,335 source packages and Trixie
37,643. `-updates` and `-security` are merged over the base suite exactly as apt
would. DEP-12 and `debian/watch` come from the package's `.debian.tar.xz` in the
pool. Everything is cached on disk.

### 3. Upstream resolution — and proof

An ordered chain (curated → kernel series → DEP-12 → watch → homepage) proposes a
candidate; `git merge-base` decides. ARCoS forked the **Debian packaging
repository** for some packages and the **project's own repository** for others,
and nothing in the metadata distinguishes them — so it is measured, not guessed.

See [docs/upstream-resolution.md](docs/upstream-resolution.md).

### 4. Comparison — from the commit graph

Never from version strings. Head-based sets (`UPSTREAM ^ARCOS`, `ARCOS
^UPSTREAM`), so criss-cross history cannot inflate them. Every relevant upstream
commit is classified `DEFINITELY_PRESENT` (cherry-pick trailer or identical
`git patch-id --stable`), `PROBABLY_PRESENT` (`git apply --check -R`, or an
adapted backport whose lines are all there), `MISSING` or `UNKNOWN` (partial,
unchecked) - a differing patch-id alone never means missing. Reverts are paired.
Debian's own patches for the shipped version are checked against the ARCoS tree
and reported beside the commits, never among them.

See [docs/git-comparison.md](docs/git-comparison.md).

### 5. Criticality — evidence only

Commit messages are one signal; Debian security patches (whose DEP-3 `Origin:`
names the upstream commit) and OSV.dev fix commits are others. `CRITICAL` needs a
CVE or a named advisory from any of them. `STABLE_RELEVANT` needs `Cc: stable`.
`NORMAL` needs a `Fixes:` trailer. Everything else is `UNKNOWN` — which is not the
same as safe, only honest. A commit is never called critical because its subject
line sounds alarming.

### 6. Patches — nothing happens by itself

Preview in a throwaway workspace → cherry-pick to a **new** branch → push → pull
request. Four explicit steps. Conflicts stop the series and are never resolved
automatically. The server re-validates the selection against the current target
tip and upstream ref, and refuses a cherry-pick onto a tip the preview did not
use.

See [docs/patch-workflow.md](docs/patch-workflow.md).

### 7. `debian/upstream.md`

Generated from the same verified resolution the dashboard shows, so the committed
record and the tool cannot disagree. Deterministic, so it can be compared with
what is already in the repository: `CREATED`, `UPDATED`, `NO_CHANGE` or
`CONFLICT`. A file this tool did not write is never overwritten.

`apm publish-upstream-md` proposes each file as its own pull request: a commit
built from the target tip's tree plus that one file, pushed to
`upstream-metadata/<release>/<package>` without force, and recorded in a ledger
so re-runs resume. The target branch is never written to.

## Reading the output

| Status | Meaning |
|---|---|
| `VERIFIED` | Upstream identified **and** relationship proven: shared git history, or a content match a person approved |
| `NEEDS_REVIEW` | A candidate exists, but proof is missing or contradictory - `Review Reasons` names it (`NO_SHARED_HISTORY`, `CURATED_CONFLICT`, `INVALID_REF`, ...) |
| `NO_UPSTREAM` | Searched, and there is provably nothing to track |
| `FAILED` | The resolver could not finish (`PROBE_FAILED`, `NETWORK_ERROR`, `ARCOS_UNREACHABLE`) - not a finding |

**Verification level** is separate from status: `REF_EXISTS` (ls-remote
answered - existence only, never enough for VERIFIED), `SHARED_HISTORY`,
`CONTENT_MATCH_APPROVED`. **Confidence** is separate again.

**The ref is chosen for the release**, not taken from the remote's default
branch: the upstream tag for Debian's version is the base, and the maintenance
branch containing it is the target (or the tag itself). `master`, `main` and
`debian/sid` are labelled fallbacks.

**Raw counts are labelled raw.** `Upstream Commits Not In ARCoS (raw)` is
`rev-list ARCOS..UPSTREAM` against that ref - backports not excluded. The
backlog that matters - relevant, already present, missing, critical - comes from
`apm compare` and is what `debian/upstream.md` records.

**Category** is separate, and answers "what kind of package is this":
`debian_upstream`, `debian_no_upstream`, `third_party`, `arrcus_native`,
`vendor`. A vendor blob with no upstream is a finished answer, not a failure.

**Origin kind** says whether the fork descends from the project's own repository
or from Debian's packaging repository — it changes what pulling a patch means.

Other commands: `apm compare --package <p>` (full comparison, recorded for
`debian/upstream.md`), `apm approve-content-base` (no-shared-history forks),
`apm doctor` (storage, git and GitHub checks; prints no secret).

## Directory structure

```
config/          settings.yaml, overrides.yaml (hand-written); packages.yaml (generated)
src/apm/
  api/           FastAPI routes, error mapping, request schemas
  domain/        entities and enums shared by everything
  services/      all behaviour
  gitio/         transports, workspaces, ref verification, ancestry
  debian/        Sources index, deb822, DEP-12 and watch
  upstream/      candidate discovery, URL normalisation, kernel series
  report.py      CSV and XLSX
frontend/src/    React app; types/api.ts is generated
docs/            architecture, resolution, comparison, patch workflow, deployment
scripts/         dev servers, tests, type generation, output invariants
tests/           backend tests, including integration tests on real git repos
```

## API

All under `/api`. Full schema at `/docs` when the backend is running.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health`, `/health/workspace` | Readiness and what this deployment can do |
| GET | `/releases` | Debian releases |
| GET | `/packages`, `/packages/{package}` | Catalogue, filterable by release |
| GET | `/branches` | Real branches of a package's fork |
| GET | `/upstream/{package}` | Resolve (auto) |
| POST | `/upstream/resolve` | Resolve (auto), with options |
| POST | `/upstream/verify` | Resolve from user input, still verified |
| GET | `/upstream/{package}/evidence` | Why that upstream was chosen |
| POST/GET | `/comparison`, `/comparison/{package}` | Git comparison |
| GET | `/commits/{package}` | Commit lists alone |
| POST | `/patches/preview` | Safe preview; writes nothing |
| POST | `/patches/cherry-pick` | New branch; pushes only if asked |
| POST/GET | `/pull-requests`, `/pull-requests/{number}` | Pull requests |
| POST | `/upstream-md/generate` | Render `debian/upstream.md` |
| POST | `/upstream-md/pr` | Propose it; needs `confirm: true` (or `dry_run: true`) |
| GET | `/upstream-md/prs` | What proposing has done so far (the ledger) |
| POST | `/upstream/content-base/approve` | Record a person's approval of a content-matched base |
| GET | `/reports`, `/reports/{name}` | Generated mapping workbooks and CSVs |

## Configuration

Everything is environment-driven; see `.env.example` and
[docs/deployment.md](docs/deployment.md). Nothing names a machine by default.

| File | Generated? | Purpose |
|---|---|---|
| `config/settings.yaml` | no | Releases, archive layout, forge/packaging hosts, kernel series, ancestry |
| `config/packages.yaml` | **yes**, by `discover` | Package catalogue. Do not hand-edit |
| `config/overrides.yaml` | no | Hand-verified mappings. Always wins |

## Testing

```bash
./scripts/test.sh
```

Backend tests run offline on committed fixtures, plus integration tests that
build **real git repositories** and drive the comparison and patch engines
against them — a mock would happily confirm whatever the code already believes.
GitHub is always mocked; no test opens a pull request.

Frontend tests mock `fetch` rather than the API client, so URL construction,
query strings and error decoding are exercised too.

`scripts/verify_output.py` checks invariants against the generated mapping — the
ones that caught real bugs, including a kernel series that contradicts its Debian
version.

## Regenerating frontend types

```bash
PYTHONPATH=src ./.venv/bin/python scripts/generate_frontend_types.py
```

Run this after changing any domain model; a mismatch then becomes a TypeScript
error rather than an `undefined` at runtime.

## Safety properties

Guaranteed by construction and covered by tests:

- Upstream repositories and branches are never guessed; every one is contacted.
- Version differences are never used as a measure of git lag.
- ARCoS-specific commits are never counted as missing upstream commits.
- Commits already present under a different SHA are never shown as missing.
- No commit is called critical without evidence.
- The target branch is never cherry-picked into, and never written to.
- Nothing is pushed, and no pull request is opened, without an explicit action.
- An existing `debian/upstream.md` this tool did not write is never overwritten.
- No secret is committed or logged.
- Backport detection either runs or says it could not. A search that failed is
  never reported as a search that found nothing.
