# Deployment

**Docker Compose is the standard deployment.** It needs only Docker on the
target machine; the image carries Python, the dependencies (pinned in
`constraints.txt`), git, ssh and the built dashboard. The quick version is the
README's "Docker Deployment" section; this page explains the moving parts.

```
Browser
   │  http://SERVER_IP:8080
   ▼
Docker Compose ── app container (unprivileged, read-only filesystem)
                    ├─ uvicorn + FastAPI: /api, /health, and the dashboard at /
                    ├─ /app/out                        ← host ./out            (read-write)
                    ├─ /var/tmp/arcos-package-manager  ← host workspace dir    (read-write)
                    ├─ /var/cache/arcos-package-manager← Docker volume         (read-write)
                    ├─ /app/config                     ← host ./config         (read-only)
                    └─ /home/apm/.ssh/id_git           ← host SSH key          (read-only)
                              │
                              ▼
                    GitHub (Arrcus repositories) over SSH; Debian, upstream
                    projects and OSV over HTTPS
```

## The image

`Dockerfile` has four stages:

| Stage | What it is |
|---|---|
| `frontend-build` | Node 22 runs `npm ci` and `npm run build` (typecheck + Vite). Node exists only here. |
| `base` | `python:3.12-slim-bookworm` + git, openssh-client, sshpass; `pip install -r requirements.txt -c constraints.txt`; the application; GitHub's SSH host keys. |
| `test` | `base` + pytest and the tests: `docker build --target test .` |
| `runtime` | `base` + the built dashboard. The default target and what Compose runs. |

Nothing secret or machine-specific is copied in: `.dockerignore` excludes
`.env`, keys, `.git`, `out/`, the workspace and local environments, and
`tests/test_docker_contract.py` holds the Dockerfile and Compose file to that.

GitHub's SSH host keys are written to `/etc/ssh/ssh_known_hosts` at build time
from `https://api.github.com/meta` (HTTPS), so `github.com` is verified with
`StrictHostKeyChecking=yes` and no `known_hosts` has to be mounted or trusted
on first use. The build therefore needs to reach api.github.com.

## The container

- Runs as user `apm` with uid/gid `APM_UID`/`APM_GID` (build arguments), not
  root. Set them to the owner of the host directories and of the SSH key.
  Application files are root-owned and read-only to it.
- `read_only: true`: only the mounts and a `/tmp` tmpfs are writable.
- `cap_drop: [ALL]`, `no-new-privileges`, not privileged, no Docker socket.
- `init: true`: a minimal init is PID 1, forwards `SIGTERM` to uvicorn (which
  shuts down gracefully, 20s) and reaps the git processes the app starts.
- `restart: unless-stopped`: it comes back with the Docker daemon after a reboot.
- Healthcheck: `GET /health` every 30s. It does no git, no GitHub call and no
  disk read, so a slow remote never marks a working container unhealthy.
  `/api/health` is the detailed capability report.
- The entrypoint checks that `/app/out`, the workspace and the cache are
  writable and the mounted key is readable, and stops with an explanation if
  not. It then logs the git backend, workspace, output location and whether a
  key and a token are configured - never their values.

## Repository access: SSH

Git runs inside the container (`APM_GIT_BACKEND=local`) with the host's SSH
key, which `.env` names by its host path:

```
APM_GIT_SSH_KEY_FILE=/home/<user>/.ssh/id_ed25519
```

Compose mounts that file read-only at `/home/apm/.ssh/id_git` and points git
at it (`GIT_SSH_COMMAND` with `IdentitiesOnly=yes`, `BatchMode=yes`). The key
never enters the image or the repository, and nobody pastes it anywhere. It
must be readable by `APM_UID` and must not need a passphrase.

Check it from inside the container:

```bash
docker compose exec app apm doctor --repo Arrcus/mstpd
```

### Optional: an SSH bridge

A machine without repository access can send git commands to one that has it
(`APM_GIT_BACKEND=auto` or `ssh`): set `APM_SSH_HOST` (an ssh_config alias
works), `APM_SSH_USER`, and `APM_SSH_IDENTITY_FILE` (a host path, mounted
read-only). `auto` sends only repositories matching
`APM_PRIVATE_REPOSITORY_PATTERNS` over the bridge. `APM_SSH_PASSWORD` exists
for hosts that permit nothing better; it is a secret.

## GitHub API: the token

Two credentials, never standing in for each other:

| | Git (fetch, clone, push) | GitHub REST API (find/open PRs) |
|---|---|---|
| Code | `gitio/transport.py` | `services/github_service.py` |
| Credential | the SSH key above | `APM_GITHUB_TOKEN` |

So branches are pushed with the SSH key, and pull requests are opened by
whoever owns the token. Without a token everything except opening pull
requests works, and the dashboard says it is read-only;
`publish-upstream-md --apply` refuses before any push. The token needs
**Pull requests: Read and write** and **Metadata: Read** on the package
repositories (fine-grained) or `repo` (classic), and SSO authorisation if the
organisation enforces SAML. It is read from `.env` at container start, is not
in the image, and is never logged.

After editing `.env`: `docker compose up -d` (recreates the container with the
new environment).

## Persistent data

| What | Host | Container |
|---|---|---|
| Mapping (`upstream-resolutions.json`, CSV, XLSX), `upstream.md` files, PR plans and ledgers, comparison snapshots, approvals, runtime `packages.yaml` | `./out` or `APM_OUT_HOST_DIR` | `/app/out` |
| Git clones (`repos/`), ancestry probe workdirs, scratch | `./workspace` or `APM_WORKSPACE_HOST_DIR` | `/var/tmp/arcos-package-manager` |
| Debian indices and tarballs, OSV answers, ancestry cache | volume `apm-cache` or `APM_CACHE_HOST_DIR` | `/var/cache/arcos-package-manager` |
| Shipped configuration (`settings.yaml`, `overrides.yaml`, committed `packages.yaml`) | `./config`, read-only | `/app/config` |

`./out` and `./workspace` are tracked as empty directories, so a fresh clone has
them owned by the cloning user (a missing bind-mount source would be created by
Docker as root, which the unprivileged container could not write). The
workspace grows to several GB with the kernel and openssl; point
`APM_WORKSPACE_HOST_DIR` at a disk with room.

`config/` is never written: `discover` writes the package catalogue to
`APM_PACKAGES_FILE` (`/app/out/packages.yaml`), and until the first discovery
the committed `config/packages.yaml` is read. Nothing is regenerated per
request; the dashboard reads what `resolve-release` last wrote.

## Operating it

```bash
docker compose up -d --build                  # start (or apply new code / .env)
docker compose ps                             # health
docker compose logs -f                        # logs
docker compose exec app apm <command>         # the CLI, same data
docker compose down                           # stop; out/ and the workspace stay
docker compose build --no-cache && docker compose up -d   # clean rebuild
```

Updating to new code: `git pull --ff-only && docker compose up -d --build`.

## Tests

```bash
docker build --target test -t apm-test . && docker run --rm apm-test
docker build --target frontend-build -t apm-frontend-build . \
  && docker run --rm apm-frontend-build npx vitest run
# include the real generated mapping in the backend run:
docker run --rm -v "$PWD/out:/app/out:ro" apm-test
```

## Development and fallback runtime (no Docker)

For working on the code, or on a host where Docker is not available: one
uvicorn process in the project's own virtualenv serves the dashboard and API
on `0.0.0.0:8080`, with git using the host's own `~/.ssh`.

```bash
scripts/setup-venv.sh --dev      # .venv (no root; pip bootstrapped if needed)
scripts/build-frontend.sh        # frontend/dist via npm, or the Dockerfile's node stage
scripts/server.sh start          # status | logs | restart | stop
scripts/apm doctor               # the CLI
```

`scripts/env.sh` keeps runtime state in `out/` here too. Live-reload
development: `scripts/dev-backend.sh` (API on :8000) and `scripts/dev-frontend.sh`
(Vite on :5173, proxying `/api`).

## Troubleshooting

| Symptom | Cause |
|---|---|
| Container exits: `... is not writable by uid N` | `APM_UID`/`APM_GID` do not own the host directory. Set them (`id -u`, `id -g`), then `docker compose up -d --build`. |
| `the SSH key mounted at ... is not readable` | The key is owned by another uid, or `APM_GIT_SSH_KEY_FILE` points at a missing file. |
| `doctor`: private repository access FAIL | The key cannot reach the repository, needs a passphrase, or `APM_GIT_SSH_KEY_FILE` is unset. |
| `Host key verification failed` for a non-GitHub host | Mount a `known_hosts` with `APM_SSH_KNOWN_HOSTS_FILE`. |
| `AUTH_REQUIRED` | No `APM_GITHUB_TOKEN`, or not SSO-authorised. |
| `NO_COMMON_ANCESTOR` | Not a configuration problem: the fork was imported, not forked. |
| First comparison very slow | It fetches both histories into the workspace; the next one takes seconds. |
| Build fails fetching GitHub host keys | The build host cannot reach api.github.com. |
