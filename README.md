# ARCoS Package Manager

For any ARCoS package, works out where its upstream actually is, how far behind
it the fork has fallen, which of the missing commits carry evidence of mattering,
and — on explicit request — prepares a branch and a pull request pulling them in.

React + TypeScript frontend, FastAPI backend, git and Debian archive metadata
underneath. No database.

## Docker Deployment (recommended)

Docker Compose is the standard way to run the application. One container holds
everything - FastAPI/uvicorn, the built React dashboard, git and ssh - and
serves the dashboard at `/` and the API at `/api` on port **8080**. The target
machine needs no Python, no Node and no build step of its own.

```
Browser ── http://SERVER_IP:8080 ──▶ Docker Compose ──▶ app container
                                                        ├─ FastAPI + uvicorn + dashboard
                                                        ├─ ./out           (reports, mapping, state)
                                                        ├─ workspace dir   (git clones)
                                                        └─ SSH key, read-only ──▶ GitHub / Arrcus repos
```

### Prerequisites

- Docker Engine with the Compose plugin (`docker compose version`)
- Git, to clone this repository
- For repository operations: an SSH key on the server that can reach the
  Arrcus GitHub repositories (`ssh -T git@github.com` answers with a name)

### Setup

```bash
git clone https://github.com/somil-rathore-arrcus/arcos-package-manager.git
cd arcos-package-manager
cp .env.example .env
```

Then edit `.env` (every variable is described in `.env.example`):

| Variable | Set it to |
|---|---|
| `APM_UID`, `APM_GID` | `id -u` and `id -g` of the user that owns this checkout and the SSH key |
| `APM_GIT_SSH_KEY_FILE` | the **host path** of the SSH private key, e.g. `/home/<user>/.ssh/id_ed25519` |
| `APM_WORKSPACE_HOST_DIR` | a host directory with room for clones (default `./workspace`) |
| `APM_GITHUB_TOKEN` | leave empty until pull requests are wanted (see below) |
| `APM_COMMITTER_NAME`, `APM_COMMITTER_EMAIL` | the person accountable for the PRs |

**SSH (git fetch / clone / push).** Nobody pastes or copies a private key
anywhere: `.env` only names the key's path on the host, and Compose mounts that
file **read-only** into the container. It is never copied into the image. The
key must be readable by `APM_UID` and must not need a passphrase. GitHub's SSH
host keys are built into the image (taken from GitHub's API over HTTPS), so
`github.com` is verified without extra setup; `APM_SSH_KNOWN_HOSTS_FILE` can
mount your own `known_hosts` as well.

**GitHub token (pull requests only).** The SSH key pushes branches; the token
only calls the GitHub REST API to open and look up pull requests. Without it
everything else works - mapping, comparison, `upstream.md` generation, dry
runs - and the dashboard says it is read-only. The token lives only in `.env`
on the server: it is not in the image, not in git and never printed. A
fine-grained token needs **Pull requests: Read and write** and **Metadata:
Read** on the package repositories.

### Start

```bash
docker compose build
docker compose up -d
```

### Check

```bash
docker compose ps                       # STATUS shows "healthy"
curl -s http://localhost:8080/health    # {"status":"ok",...}
docker compose exec app apm doctor --repo Arrcus/mstpd   # storage, git and GitHub checks
docker compose logs -f
```

### Access

```
http://SERVER_IP:8080/
```

### Stop

```bash
docker compose down        # stops and removes the container; out/ and the workspace stay
```

### Rebuild

```bash
docker compose build --no-cache
docker compose up -d
```

### Updating

```bash
git pull --ff-only
docker compose up -d --build
```

### Running the CLI

Every `apm` command runs inside the container, against the same data:

```bash
docker compose exec app apm resolve-release --release bookworm
docker compose exec app apm generate-upstream-md --release bookworm --branch aminor
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd   # dry run
```

### Where data lives

| What | Host | Container |
|---|---|---|
| Mapping, reports, `upstream.md` files, PR plans and ledgers, comparison snapshots, approvals, runtime `packages.yaml` | `./out` (`APM_OUT_HOST_DIR`) | `/app/out` |
| Git clones and probe workdirs | `./workspace` (`APM_WORKSPACE_HOST_DIR`) | `/var/tmp/arcos-package-manager` |
| Debian/OSV downloads, ancestry cache | Docker volume `apm-cache` (`APM_CACHE_HOST_DIR`) | `/var/cache/arcos-package-manager` |
| Shipped configuration | `./config`, read-only | `/app/config` |

The container's own filesystem is read-only; it runs as an unprivileged user
with no Linux capabilities, and nothing above is lost when it is rebuilt or
recreated. It restarts with the Docker daemon after a reboot.

## Development (without Docker)

For working on the code, or as a fallback on a host where Docker is not
available:

```bash
scripts/setup-venv.sh --dev      # .venv with the dependencies (no root needed)
scripts/build-frontend.sh        # dashboard -> frontend/dist (npm, or Docker's node stage)
scripts/server.sh start          # dashboard + API on 0.0.0.0:8080 (status|logs|stop|restart)
scripts/apm doctor               # the CLI
```

Or with live reload:

```bash
./scripts/dev-backend.sh        # API on http://127.0.0.1:8000
./scripts/dev-frontend.sh       # dashboard on http://localhost:5173, proxying /api
```

The mapping pipeline also runs standalone (in the container: `docker compose
exec app apm ...`):

```bash
scripts/apm discover        # -> out/packages.yaml
scripts/apm resolve         # -> out/upstream-mapping.{csv,xlsx}
```

For one release end to end - discovery, resolution, workbook and the
`debian/upstream.md` files - see
[docs/bookworm-aminor-workflow.md](docs/bookworm-aminor-workflow.md):

```bash
scripts/apm resolve-release --release bookworm
#   -> out/upstream-mapping-bookworm.{csv,xlsx}

scripts/apm generate-upstream-md --release bookworm --branch aminor
#   -> out/upstream-md/<package>/debian/upstream.md
#   -> out/upstream-md-plan-bookworm.{json,md}
```

Neither pushes, creates a branch or opens a pull request, and neither needs a
GitHub token.

Proposing the reviewed files is a separate, explicit step - a dry run unless
`--apply` is given:

```bash
scripts/apm publish-upstream-md --package mstpd
scripts/apm publish-upstream-md --package mstpd --apply --confirm mstpd
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

In Docker, against the same base image the application runs on:

```bash
docker build --target test -t apm-test . && docker run --rm apm-test     # backend
docker build --target frontend-build -t apm-frontend-build . \
  && docker run --rm apm-frontend-build npx vitest run                    # frontend
```

Or in the development venv:

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
