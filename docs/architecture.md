# Architecture

## The pieces

```
Browser (any machine)
   │  http://<server-ip>:8080        no credentials, no git
   ▼
Host machine ─ Docker Compose ─ app container
                                  uvicorn + FastAPI
                                    ├─ /           the built React dashboard (static files)
                                    ├─ /api/...    the API
                                    └─ /health     liveness for the healthcheck
                                  services        all behaviour (same code as the CLI)
                                  git + ssh       run inside the container
                                       │
             ┌─────────────────────────┼──────────────────────────────┐
             ▼                         ▼                              ▼
   GitHub over SSH              HTTPS: Debian archive,         HTTPS: GitHub REST API
   (Arrcus forks, push)         upstream projects, OSV.dev     (only to find/open PRs; token)
```

One container, one process. The dashboard and the API share one origin and one
port, so no web server or second container is needed.

## What runs where

Seven things are easy to confuse. They are separate on purpose.

| | What it is | Where it lives | Changed by | Must never |
|---|---|---|---|---|
| **Docker runtime** | The `app` container, built from image `arcos-package-manager:latest`: Python, the application, git, ssh and the built dashboard. Runs as an unprivileged user with a read-only filesystem | On the host, managed by Docker Compose | `docker compose up -d --build` (rebuild) | contain a secret; be edited in place |
| **Host machine** | The Linux server that runs Docker and holds the checkout, `.env`, the SSH key and the data directories | Production: `/home/lakshya/somil/arcos-package-manager` | `git pull`, editing `.env` | run a second copy of the server on the same port |
| **Browser / client** | Whoever opens the dashboard or calls the API | Any machine that can reach `<server-ip>:8080` | - | need git, a key or a token: the server does all git and GitHub work |
| **SSH authentication** | The host's private key. Git uses it to fetch, clone, `ls-remote` and push | Host path in `APM_GIT_SSH_KEY_FILE`, mounted read-only at `/home/apm/.ssh/id_git` | Editing `.env`, then recreating the container | be copied into the image or the repository |
| **GitHub API authentication** | `APM_GITHUB_TOKEN`. Used only for the REST API: look up a repository, find or open a pull request | `.env` on the host → the container's environment | Editing `.env`, then recreating the container | be printed, logged, committed or pasted anywhere |
| **Generated runtime data** | Mapping, reports, `upstream.md` files, PR plan and ledgers, comparison snapshots, approvals, runtime `packages.yaml`; git clones; caches | `out/`, the workspace directory, the `apm-cache` volume | Every run | be committed (they are gitignored) |
| **Source and configuration** | Code, `Dockerfile`, `docker-compose.yml`, `config/*.yaml`, docs | This git repository | Pull requests to `arcos-package-manager` | contain a secret or a machine-specific value |

The SSH key and the token are different credentials for different jobs; neither
can stand in for the other. See [authentication.md](authentication.md).

## Code layers

```
frontend/src/         React + TypeScript + Vite; types generated from the API schema
src/apm/api/          FastAPI routes: validate, call one service, return a domain model
src/apm/services/     all behaviour
src/apm/domain/       entities and enums - the shared vocabulary
src/apm/gitio/        where git runs (transports), workspaces, ref checks, ancestry probe
src/apm/debian/       Debian archive metadata: Sources index, deb822, DEP-12, watch, patches
src/apm/upstream/     candidate discovery, URL normalisation, release refs, kernel series
src/apm/discovery/    reading the arrcus_rel manifests
src/apm/cli.py        the `apm` command
```

The CLI, the API and the tests build the same object graph from
`services/container.py`, so there is no second place where behaviour is wired
up differently.

**Routes contain no logic.** Every route resolves dependencies, calls one
service and returns a domain model. That is why the CLI and the dashboard give
identical answers.

**The domain model is the contract.** Responses are the domain objects
themselves, and `scripts/generate_frontend_types.py` emits TypeScript from the
schema FastAPI serves, so a changed field is a compile error in the frontend
rather than an `undefined` at runtime.

**Git access is a transport.** `gitio/transport.py` decides where each git
command runs: in the container (`local`, the standard deployment) or on a
separate bridge host (`ssh`, or `auto` for private repositories only). Nothing
above that layer knows a host name.

## One mapping, several views

There is exactly one resolution per (package, release). Everything else is a
view of it:

```
Resolver.resolve_one()  →  Resolution
    ├─ out/upstream-mapping-<release>.{csv,xlsx}   the release workbook
    ├─ out/upstream-mapping.{csv,xlsx}             all releases
    ├─ out/upstream-resolutions.json               full fidelity - read by the dashboard and the API
    └─ UpstreamMdService                           debian/upstream.md
```

The workbook, the dashboard and the committed `debian/upstream.md` cannot
disagree, because none of them keeps its own copy of the mapping.

## Request paths

Resolving:

```
GET /api/upstream/{package}
  → UpstreamService.resolve        the stored mapping, or a live resolution with refresh=true
      → Resolver
          → Debian Sources index, DEP-12, watch
          → candidate chain        curated → kernel series → DEP-12 → watch → homepage
          → upstream/refs          release tag, maintenance branch, packaging refs
          → AncestryChecker        merge-base --all and head-based counts per ref
          → ContentBaseService     only when there is no shared history
```

Comparing:

```
POST /api/comparison
  → UpstreamService.resolve (or resolve_manual)
  → ComparisonService.compare
      → workspace per package and release (reused)
      → fetch both sides, merge-base --all, UPSTREAM ^ARCOS, ARCOS ^UPSTREAM
      → PatchIdService + BackportDetector    presence, in tiers
      → Debian patches                       checked against the ARCoS tree
      → CriticalityService + SecurityService per commit, from evidence
      → ComparisonStore                      snapshot keyed by both commits
```

Applying patches and publishing:

```
POST /api/patches/preview        throwaway workspace, destroyed afterwards
POST /api/patches/cherry-pick    a NEW branch from the target; pushes only with push=true
POST /api/pull-requests          a separate, explicit call
POST /api/upstream-md/pr         the same publisher as `apm publish-upstream-md`
```

## Where state lives

No database. The durable files:

| What | Host | In the container |
|---|---|---|
| Release mapping (CSV, XLSX), all-release mapping | `out/upstream-mapping*.{csv,xlsx}` | `/app/out/` |
| Full-fidelity mapping (read by the dashboard) | `out/upstream-resolutions.json` | `/app/out/` |
| Comparison snapshots | `out/comparisons/<release>/<package>/<key>.json` | `/app/out/comparisons/` |
| Generated `debian/upstream.md` files | `out/upstream-md/<package>/debian/upstream.md` | `/app/out/upstream-md/` |
| PR plan | `out/upstream-md-plan-<release>.{json,md}` | `/app/out/` |
| PR ledgers (real / dry run) | `out/upstream-md-pr-results-<release>.*`, `out/upstream-md-pr-dryrun-<release>.*` | `/app/out/` |
| Content-base approvals | `out/approvals.yaml` | `APM_APPROVALS_FILE` |
| Runtime package catalogue | `out/packages.yaml` | `APM_PACKAGES_FILE` |
| Git clones, probe workdirs | the workspace directory (`APM_WORKSPACE_HOST_DIR`) | `/var/tmp/arcos-package-manager` |
| Debian indices, tarballs, OSV answers, ancestry cache | Docker volume `apm-cache` | `/var/cache/arcos-package-manager` |
| Curated mappings, settings, committed catalogue | `config/*.yaml` (read-only in the container) | `/app/config/` |

Live resolutions are cached in memory for 15 minutes. Comparisons are always
recomputed, but the workspace is reused, which takes a repeat comparison from
minutes to seconds.
