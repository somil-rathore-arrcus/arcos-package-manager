# Operations

All commands run on the server, from the checkout directory
(production: `/home/lakshya/somil/arcos-package-manager`).

## Daily commands

| Task | Command |
|---|---|
| Status and health | `docker compose ps` |
| Follow the logs | `docker compose logs -f app` |
| Last 100 log lines | `docker compose logs --tail 100 app` |
| Liveness (any machine) | `curl -s http://<server-ip>:8080/health` |
| Capabilities (any machine) | `curl -s http://<server-ip>:8080/api/health` |
| Full self-check | `docker compose exec app apm doctor --repo Arrcus/mstpd` |
| Start | `docker compose up -d` |
| Stop (keeps all data) | `docker compose down` |
| Restart the process | `docker compose restart` |
| Apply a `.env` change | `docker compose up -d --force-recreate` |
| Run a CLI command | `docker compose exec app apm <command>` |

`docker compose stop` / `down` send `SIGTERM`; uvicorn shuts down gracefully
within seconds. Docker reports exit code 143 for that, which is normal.

## Updating to new code

```bash
git pull --ff-only
docker compose up -d --build
docker compose ps                    # wait for (healthy)
```

Rebuilding replaces the image only; `out/`, the workspace and the cache stay.

## Rebuilding from scratch

```bash
docker compose build --no-cache
docker compose up -d
```

## After a reboot

The container has `restart: unless-stopped`: it starts with the Docker daemon
unless it was stopped on purpose. Check with `docker compose ps`.

## Refreshing the data

The mapping is not recomputed per request; it is what the last run wrote.
To refresh a release:

```bash
docker compose exec app apm resolve-release --release bookworm
docker compose exec app apm compare --release bookworm --package <p>      # optional, per package
docker compose exec app apm generate-upstream-md --release bookworm --branch aminor
docker compose exec app python scripts/verify_upstream_md.py bookworm
docker compose exec app apm publish-upstream-md --release bookworm --all  # dry run
```

Order matters: comparisons before generation, generation (without
`--package`) before publishing.

## Logs

The startup lines state the user, port, git backend, whether an SSH key and a
token are configured (never their values), and where the workspace, output
and mapping are. Request lines follow (`GET /health ... 200`). No log line
contains a secret.

## What to back up

| What | Where | Back up? |
|---|---|---|
| Results ledger - the record of every branch and PR the tool created | `out/upstream-md-pr-results-*.{json,md}` | **Yes - never edit or delete** |
| Content-base approvals | `out/approvals.yaml` | **Yes** |
| Mapping, plan, generated files, snapshots | rest of `out/` | Useful; can be regenerated |
| `.env` | checkout directory | Keep a private copy of the non-secret values; the token can be re-issued |
| Git clones | workspace directory | No - regenerable |
| Downloads and caches | `apm-cache` volume | No - regenerable |

```bash
tar czf ~/apm-out-$(date +%Y%m%d).tar.gz out/
```

## Disk space

The workspace grows to several GB (the kernel and openssl). Check it with
`du -sh <workspace>`. To reclaim it, stop the container, empty the workspace
directory (not `out/`), and start again; the next comparison of each package
fetches its history again. The cache volume can be dropped with
`docker compose down -v` (the next runs download the Debian data again).

## Removing old images

After several rebuilds, unused images accumulate:

```bash
docker image ls | grep -E 'arcos-package-manager|apm-'
docker image prune            # dangling layers only
```

## The development runtime

Never run `scripts/server.sh` (the venv server) and the container on the same
port at the same time. `scripts/server.sh status` shows whether it is running;
`scripts/server.sh stop` stops it.
