# Docker deployment

Docker Compose is the standard way to run the application. The server needs
only Docker: the image carries Python, the pinned dependencies, git, ssh and
the built dashboard.

```
Browser ── http://<server-ip>:8080 ──▶ app container (unprivileged, read-only filesystem)
                                        ├─ uvicorn + FastAPI: /api, /health, dashboard at /
                                        ├─ /app/out                         ← host ./out (read-write)
                                        ├─ /var/tmp/arcos-package-manager   ← host workspace dir (read-write)
                                        ├─ /var/cache/arcos-package-manager ← Docker volume apm-cache (read-write)
                                        ├─ /app/config                      ← host ./config (read-only)
                                        └─ /home/apm/.ssh/id_git            ← host SSH key (read-only)
```

## 1. Prerequisites

- Docker Engine with the Compose plugin: `docker compose version`.
- Git, to clone this repository.
- An SSH key on the server that can read (and, for publishing, push to) the
  Arrcus GitHub repositories, without a passphrase. Check it on the host:
  `ssh -T git@github.com` answers `Hi <account>!`.
- Network access from the server to github.com (SSH and HTTPS),
  deb.debian.org, security.debian.org, api.osv.dev, and - for building -
  Docker Hub, PyPI and the npm registry.

## 2. Get the code and create `.env`

```bash
git clone https://github.com/somil-rathore-arrcus/arcos-package-manager.git
cd arcos-package-manager
cp .env.example .env
chmod 600 .env
```

Edit `.env` with an editor. For a first start, set:

| Variable | Set it to |
|---|---|
| `APM_UID`, `APM_GID` | the output of `id -u` and `id -g` for the user that owns this checkout and the SSH key |
| `APM_GIT_SSH_KEY_FILE` | the **host path** of the private key, e.g. `/home/<user>/.ssh/id_ed25519` |
| `APM_WORKSPACE_HOST_DIR` | a host directory with several GB free for git clones (default `./workspace`) |
| `APM_COMMITTER_NAME`, `APM_COMMITTER_EMAIL` | the person accountable for the commits the tool creates |
| `APM_GITHUB_TOKEN` | leave **empty** for now - the application then runs read-only |

`APM_GIT_BACKEND=local` (already in `.env.example`) makes git run inside the
container with that key. Every variable is described in
[configuration.md](configuration.md); the two credentials in
[authentication.md](authentication.md).

If `APM_WORKSPACE_HOST_DIR` points outside the checkout, create it first as the
same user, so it is not created by Docker as root:

```bash
mkdir -p /var/tmp/arcos-package-manager
```

## 3. Build and start

```bash
docker compose up -d --build
```

The first build takes a minute or two. Then:

```bash
docker compose ps                  # STATUS: Up ... (healthy)
docker compose logs app            # the startup lines below
```

```
apm: ARCoS Package Manager starting as uid 1000 on 0.0.0.0:8080
apm: git backend=local, git ssh key=mounted read-only, github token=not set (read-only)
apm: workspace=/var/tmp/arcos-package-manager, output=/app/out, packages file=/app/out/packages.yaml
apm: mapping=/app/out/upstream-resolutions.json
```

The log says whether a key and a token are configured - never their values.

## 4. Check it

```bash
docker compose exec app apm doctor --repo Arrcus/mstpd
```

Every line should be `PASS`, except `WARN APM_GITHUB_TOKEN is not set` while
the token is empty. The last line reads `FAILURES: none`.

From any machine (or a browser):

```bash
curl -s http://<server-ip>:8080/health        # {"status":"ok","version":"..."}
curl -s http://<server-ip>:8080/api/health    # capabilities: private access, PRs, storage, mapping
```

Open `http://<server-ip>:8080/`.

## 5. Produce the first mapping

A fresh checkout has no mapping yet:

```bash
docker compose exec app apm resolve-release --release bookworm
```

This discovers the packages, resolves every one and writes
`out/upstream-mapping-bookworm.{csv,xlsx}` and `out/upstream-resolutions.json`.
The dashboard picks up the new mapping without a restart (a package already
viewed in the last 15 minutes can show its previous answer until that in-memory
cache expires; `docker compose restart` clears it). The first run fetches a lot
of history (the kernel especially) and takes a while; later runs reuse the
workspace and the cache.

## The image

`Dockerfile` has four stages:

| Stage | Contents |
|---|---|
| `frontend-build` | Node 22: `npm ci` and `npm run build` (typecheck + Vite). Node exists only here |
| `base` | `python:3.12-slim-bookworm` + git, openssh-client, sshpass; `pip install -r requirements.txt -c constraints.txt`; the application; GitHub's SSH host keys |
| `test` | `base` + pytest and the tests: `docker build --target test .` |
| `runtime` | `base` + the built dashboard. The default target, and what Compose runs |

Nothing secret or machine-specific is copied in. `.dockerignore` excludes
`.env`, keys, `.git`, `out/`, the workspace and local environments, and
`tests/test_docker_contract.py` checks the Dockerfile and the Compose file for
that.

GitHub's SSH host keys are written to `/etc/ssh/ssh_known_hosts` at build time
from `https://api.github.com/meta`, so `github.com` is verified with
`StrictHostKeyChecking=yes` and no `known_hosts` has to be mounted. The build
therefore needs to reach api.github.com.

## The container

- Runs as user `apm` with uid/gid `APM_UID`/`APM_GID` (build arguments), not
  root. Application files are root-owned and read-only to it.
- `read_only: true`: only the mounts and a `/tmp` tmpfs are writable.
- `cap_drop: [ALL]`, `no-new-privileges`, not privileged, no Docker socket.
- `init: true`: a minimal init is PID 1, forwards `SIGTERM` to uvicorn (which
  shuts down gracefully) and reaps the git processes the application starts.
- `restart: unless-stopped`: it comes back with the Docker daemon after a
  reboot, unless it was stopped on purpose.
- Healthcheck: `GET /health` every 30 seconds. It does no git, no GitHub call
  and no disk read, so a slow remote never marks a working container unhealthy.
  `/api/health` is the detailed capability report.
- The entrypoint checks that `/app/out`, the workspace and the cache are
  writable and that the mounted key is readable, and stops with an
  explanation if not.

## Persistent data

| What | Host | Container |
|---|---|---|
| Mapping, reports, `upstream.md` files, PR plan and ledgers, comparison snapshots, approvals, runtime `packages.yaml` | `./out` or `APM_OUT_HOST_DIR` | `/app/out` |
| Git clones, ancestry probe workdirs, scratch workspaces | `./workspace` or `APM_WORKSPACE_HOST_DIR` | `/var/tmp/arcos-package-manager` |
| Debian indices and tarballs, OSV answers, ancestry cache | volume `apm-cache` or `APM_CACHE_HOST_DIR` | `/var/cache/arcos-package-manager` |
| Shipped configuration | `./config`, read-only | `/app/config` |

All of it survives `docker compose down`, rebuilds and recreation. `config/`
is never written: `discover` writes the package catalogue to
`/app/out/packages.yaml`, and until the first discovery the committed
`config/packages.yaml` is read.

## Access control

The dashboard and the API have **no login**. Anyone who can reach port 8080
can use them - including, once a token is configured, the endpoints that push
branches and open pull requests. Keep the port reachable only from the
network that should use it:

- `APM_BIND_ADDRESS=127.0.0.1` in `.env` publishes the port on the host's
  loopback only; reach it with an SSH tunnel:
  `ssh -L 8080:127.0.0.1:8080 <user>@<server-ip>`, then open
  `http://localhost:8080/`.
- Or restrict port 8080 with the host's firewall.

## Development and fallback runtime (no Docker)

For working on the code, or on a host without Docker: one uvicorn process in a
project-local virtualenv serves the dashboard and the API on `0.0.0.0:8080`,
with git using the host's own `~/.ssh`.

```bash
scripts/setup-venv.sh --dev      # .venv (no root; pip bootstrapped if needed)
scripts/build-frontend.sh        # frontend/dist via npm, or the Dockerfile's node stage
scripts/server.sh start          # status | logs | restart | stop
scripts/apm doctor               # the CLI
```

`scripts/env.sh` keeps runtime state in `out/` here too. Live reload:
`scripts/dev-backend.sh` (API on :8000) and `scripts/dev-frontend.sh` (Vite on
:5173, proxying `/api`). Never run the venv server and the container on the
same port at the same time.

Next: [operations.md](operations.md) for day-to-day commands.
