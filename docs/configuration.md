# Configuration

## Where configuration comes from

| Source | Holds | Committed? | Read |
|---|---|---|---|
| `.env` (from `.env.example`) | Everything machine-specific: user ids, paths, the key's path, the token, timeouts | **No** - gitignored, never in the image | By Compose when the container is **created** |
| `docker-compose.yml` | Container paths, mounts, hardening; maps `.env` host paths to container paths | Yes | By Compose |
| `config/settings.yaml` | Static behaviour: manifest, releases, archive, hosts, ancestry, publish rules | Yes | At process start |
| `config/overrides.yaml` | Hand-verified upstream mappings. Always wins | Yes | At process start |
| `config/packages.yaml` | The package catalogue as committed (a fallback) | Yes | Until `discover` writes `out/packages.yaml` |

Precedence for package data: `overrides.yaml` > the generated catalogue >
`settings.yaml`. Regenerating the catalogue never discards a human decision.

### When a change takes effect

| You changed | Run |
|---|---|
| `.env` (any value, including the token) | `docker compose up -d --force-recreate` |
| `APM_UID` / `APM_GID` | `docker compose up -d --build` |
| `config/settings.yaml` or `config/overrides.yaml` | `docker compose restart`, then re-resolve the affected packages |
| Code (`git pull`) | `docker compose up -d --build` |

`docker compose restart` does **not** re-read `.env`: the environment is fixed
when the container is created.

## `.env` variables

Defaults are the code's own defaults; `.env.example` sets some to different
values, shown in the last column.

### Docker deployment

| Variable | Default | Meaning |
|---|---|---|
| `APM_UID`, `APM_GID` | `1000` | The container user. Set to the owner of the host directories and of the SSH key (`id -u`, `id -g`). Build arguments: change → rebuild |
| `APM_PORT` | `8080` | Host port the dashboard is published on. The container always listens on 8080 |
| `APM_BIND_ADDRESS` | `0.0.0.0` | Host address the port is published on. `127.0.0.1` keeps it local to the host |
| `APM_OUT_HOST_DIR` | `./out` | Host directory mounted at `/app/out` |
| `APM_WORKSPACE_HOST_DIR` | `./workspace` | Host directory for git clones, mounted at `/var/tmp/arcos-package-manager`. Needs several GB |
| `APM_CACHE_HOST_DIR` | volume `apm-cache` | Host directory (or volume) for downloaded data, mounted at `/var/cache/arcos-package-manager` |

### Repository access (git)

| Variable | Default | `.env.example` | Meaning |
|---|---|---|---|
| `APM_GIT_BACKEND` | `auto` | `local` | `local`: git runs in the container with the key below. `ssh`: every git command runs on a bridge host. `auto`: only repositories matching the private patterns go over the bridge |
| `APM_GIT_SSH_KEY_FILE` | unset | commented | **Host path** of the private key git uses. Mounted read-only at `/home/apm/.ssh/id_git` |
| `APM_SSH_KNOWN_HOSTS_FILE` | unset | commented | Host path of a `known_hosts` to mount read-only. Not needed for github.com (its keys are in the image) |
| `APM_SSH_STRICT_HOST_KEY_CHECKING` | `accept-new` | `yes` | ssh `StrictHostKeyChecking` for git and the bridge |
| `APM_PRIVATE_REPOSITORY_PATTERNS` | from `settings.yaml` | commented | Comma-separated regular expressions on the URL; which repositories `auto` sends over the bridge |

### GitHub API (pull requests only)

| Variable | Default | Meaning |
|---|---|---|
| `APM_GITHUB_TOKEN` | empty | Token for the GitHub REST API. Empty = read-only. **Secret.** See [authentication.md](authentication.md) |
| `APM_GITHUB_API_URL` | `https://api.github.com` | API base URL |

### Commits the tool creates

| Variable | Default | Meaning |
|---|---|---|
| `APM_COMMITTER_NAME` | `ARCoS Package Manager` | Author and committer name of `debian/upstream.md` commits and patch branches |
| `APM_COMMITTER_EMAIL` | `arcos-package-manager@localhost` | Their e-mail |

### Timeouts and caching (seconds)

| Variable | Default | `.env.example` | Meaning |
|---|---|---|---|
| `APM_CACHE_TTL` | `86400` | `86400` | Lifetime of cached HTTP downloads (Debian indices, tarballs) |
| `APM_HTTP_TIMEOUT` | `120` | `120` | HTTP requests, including the GitHub API |
| `APM_GIT_TIMEOUT` | `60` | `90` | Ordinary git commands (`ls-remote`, small fetches) |
| `APM_GIT_LONG_TIMEOUT` | `900` | `1800` | Long git work: a first fetch of the kernel, the ancestry probe, backport checks |

### Optional SSH bridge (`APM_GIT_BACKEND=auto` or `ssh`)

Only for a deployment whose own key cannot reach the repositories.

| Variable | Default | Meaning |
|---|---|---|
| `APM_SSH_HOST` | unset | The bridge host (an ssh_config alias works) |
| `APM_SSH_USER` | unset | User on it |
| `APM_SSH_PORT` | ssh default | Port |
| `APM_SSH_IDENTITY_FILE` | unset | Host path of the key for the bridge; mounted read-only at `/home/apm/.ssh/id_bridge` |
| `APM_SSH_PROXY_JUMP` | unset | `-J` jump host(s) |
| `APM_SSH_CONNECT_TIMEOUT` | `30` | ssh `ConnectTimeout` |
| `APM_SSH_OPTIONS` | unset | Extra ssh `-o` options, comma-separated |
| `APM_SSH_PASSWORD` | unset | Last resort for hosts that allow nothing better. **Secret** |

### Set by Compose - do not set them in `.env`

`docker-compose.yml` sets these for the container; its values win over
`.env`.

| Variable | Container value |
|---|---|
| `APM_CACHE_DIR` | `/var/cache/arcos-package-manager` |
| `APM_WORKSPACE_DIR` | `/var/tmp/arcos-package-manager` |
| `APM_PACKAGES_FILE` | `/app/out/packages.yaml` |
| `APM_APPROVALS_FILE` | `/app/out/approvals.yaml` |
| `APM_GIT_SSH_IDENTITY_FILE` | `/home/apm/.ssh/id_git` when `APM_GIT_SSH_KEY_FILE` is set |
| `APM_GIT_KNOWN_HOSTS_FILE`, `APM_SSH_KNOWN_HOSTS` | `/home/apm/.ssh/known_hosts` when `APM_SSH_KNOWN_HOSTS_FILE` is set |
| `APM_SSH_IDENTITY_FILE` | `/home/apm/.ssh/id_bridge` when set in `.env` |

### Development runtime only (no Docker)

| Variable | Default | Meaning |
|---|---|---|
| `APM_HOST` | `0.0.0.0` | Listen address of `scripts/server.sh` / `scripts/dev-backend.sh` |
| `APM_PORT` | `8080` | Listen port of `scripts/server.sh` (here it is the server's own port) |
| `APM_CACHE_DIR` | `~/.cache/arcos-package-manager` | Download cache |
| `APM_WORKSPACE_DIR` | `/var/tmp/arcos-package-manager` | Git clones |
| `APM_PACKAGES_FILE` | `out/packages.yaml` (set by `scripts/env.sh`) | Runtime catalogue |
| `APM_APPROVALS_FILE` | `out/approvals.yaml` | Content-base approvals |
| `APM_FRONTEND_DIST` | `frontend/dist` | Built dashboard to serve |
| `APM_CORS_ORIGINS` | unset | Comma-separated origins allowed by CORS (only for a dashboard served from another origin) |
| `VITE_API_BASE_URL` | `/api` | API base the dashboard calls |
| `VITE_PORT`, `VITE_API_TARGET` | `5173`, `http://127.0.0.1:8000` | Vite dev server and the API it proxies to |

## `config/settings.yaml`

| Key | What it controls |
|---|---|
| `manifest` | The release manifest: `ssh://git@github.com/Arrcus/arrcus_rel.git`, file `.gitmodules`, and the branch per release (`bookworm: aminor`, `trixie: aminor-trixie`) |
| `excluded_submodules` | Submodules that are not packages (`arrcus_docs`) |
| `releases` | Debian releases: `bookworm` (12), `trixie` (13) |
| `archive` | Debian mirrors and suites; `-updates` and `-security` override the base suite |
| `packaging_hosts` | Hosts of Debian packaging repositories (salsa, ...) - never treated as the project's upstream |
| `forge_hosts` | Hosts whose URLs can be normalised into a git repository |
| `classification.vendor_patterns` | Package names classified as vendor code |
| `kernel` | The kernel's upstream repository and the expected series per release |
| `ancestry` | The merge-base probe: packaging refs, packages to skip, `primary_only`, `allow_curated_replacement`, cache TTL, `max_candidates` |
| `plausibility` | Thresholds for warnings (commits behind, ARCoS-only commits, merge-base age) |
| `content_base` | Content matching for forks without shared history: `max_tags`, `min_score` (0.6) |
| `comparison.content_check_limit` | Upstream commits checked by content per comparison (1000); beyond it they are `UNKNOWN` |
| `security.osv` | OSV.dev lookups: on/off, URL, timeout, cache TTL |
| `private_repository_patterns` | Default private patterns for the `auto` backend |
| `upstream_md_publish` | Branch template `upstream-metadata/{release}/{package}`, `exclude` (packages never proposed, with the reason), `defer` (processed last: `openssl`, `linux`) |

## `config/overrides.yaml`

Hand-verified decisions. Each entry is still probed: a curated repository that
shares no history with the fork becomes `NEEDS_REVIEW` / `CURATED_CONFLICT`,
never silently trusted or silently replaced.

```yaml
packages:
  net-snmp:
    repository: https://github.com/net-snmp/net-snmp.git
    ref: master              # a candidate; release refs are still preferred
    confidence: high         # high | medium (medium reports NEEDS_REVIEW)
    pin_ref: false           # true: use exactly this ref
    allow_ancestry_replacement: false
    reason: >-
      Debian's watch file points at SourceForge, where releases are published
      rather than developed.
```

Other keys: `debian_source_package` (or `null`), `no_upstream: true`,
`category`, `content_base:` (a permanent content-match approval) and a
`releases:` block for per-release values. Edit it through a pull request to
this repository, then `docker compose restart` and re-resolve the package.

## The package catalogue

`apm discover` (and `apm resolve-release`, which runs discovery first) reads
`.gitmodules` from each release's manifest branch and writes the catalogue to
`APM_PACKAGES_FILE` - `out/packages.yaml` in the container. `config/` is
mounted read-only and is never written. Until the first discovery the
committed `config/packages.yaml` is used, so a fresh deployment still has a
package list.
