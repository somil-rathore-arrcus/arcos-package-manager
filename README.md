# ARCoS Package Manager

For every ARCoS package it finds the real upstream project, proves the
relationship with git history, shows which upstream fixes the ARCoS fork is
missing, and records the verified answer in the package repository as
`debian/upstream.md` - through pull requests that a person reviews and merges.

FastAPI + React dashboard and a command-line tool (`apm`), with git and the
Debian archive underneath. No database. Runs as one Docker container.

## What it does

| Stage | What happens | Result |
|---|---|---|
| Discover | Reads the ARCoS release manifests (`Arrcus/arrcus_rel`) | Package catalogue |
| Resolve | Debian metadata → upstream candidates → release-appropriate branch/tag → `git merge-base` proof | Mapping workbook (`VERIFIED`, `NEEDS_REVIEW`, `NO_UPSTREAM`, `FAILED`) |
| Compare | ARCoS vs upstream from the commit graph | Missing, already present/backported, unknown, ARCoS-specific commits |
| Assess | Criticality from evidence only (CVE, advisory, Debian security patch, OSV.dev) | `CRITICAL`, `STABLE_RELEVANT`, `NORMAL`, `UNKNOWN` |
| Preview | Tries selected upstream patches in a throwaway workspace | Clean or conflict report; nothing written |
| Record | Renders `debian/upstream.md` from the verified mapping | Files + a branch/commit plan |
| Publish | Dry run, then one branch and one PR per eligible package | PRs against the ARCoS package repositories + a results ledger |

## Quick start (Docker)

Needs Docker with the Compose plugin, and an SSH key on the server that can
reach the Arrcus GitHub repositories.

```bash
git clone https://github.com/somil-rathore-arrcus/arcos-package-manager.git
cd arcos-package-manager
cp .env.example .env
# edit .env: APM_UID/APM_GID (id -u / id -g) and APM_GIT_SSH_KEY_FILE (host path of the key)
docker compose up -d --build
docker compose ps                                          # STATUS: healthy
docker compose exec app apm doctor --repo Arrcus/mstpd     # storage, git and GitHub checks
```

Open `http://<server-ip>:8080/`.

Without `APM_GITHUB_TOKEN` the application is **read-only**: everything works -
mapping, comparison, `upstream.md` generation, dry runs - except opening pull
requests. See [docs/authentication.md](docs/authentication.md).

## Everyday commands

Run from the checkout directory on the server.

| Task | Command |
|---|---|
| Status / health | `docker compose ps` |
| Logs | `docker compose logs -f app` |
| Stop / start | `docker compose down` / `docker compose up -d` |
| After editing `.env` | `docker compose up -d --force-recreate` |
| Update to new code | `git pull --ff-only && docker compose up -d --build` |
| Any CLI command | `docker compose exec app apm <command>` |
| Refresh the Bookworm mapping | `docker compose exec app apm resolve-release --release bookworm` |
| Generate `debian/upstream.md` | `docker compose exec app apm generate-upstream-md --release bookworm --branch aminor` |
| Dry-run one PR | `docker compose exec app apm publish-upstream-md --release bookworm --package mstpd` |

## Safety rules (enforced by the code)

- An upstream is never guessed: every repository is contacted, and only shared
  history (or a content match a person approved) makes a package `VERIFIED`.
- Only `VERIFIED` packages are ever proposed. `NEEDS_REVIEW`, `NO_UPSTREAM` and
  `FAILED` never are.
- PRs go to the **ARCoS package repositories** (e.g. `Arrcus/mstpd`, branch
  `upstream-metadata/bookworm/mstpd` → `aminor`), never to upstream projects.
- The target branch is never written to, nothing is force-pushed, nothing is
  merged by the tool, and each commit changes exactly `debian/upstream.md`.
- Publishing is a dry run unless `--apply` is given with a confirmation that
  names what will be pushed.
- A `debian/upstream.md` this tool did not write is never overwritten.
- No secret is stored in the repository or the image, or printed in logs.

## Documentation

Start with [docs/README.md](docs/README.md). The most-used pages:

| Page | For |
|---|---|
| [Overview](docs/overview.md) | What the tool is and the end-to-end workflow |
| [Docker deployment](docs/deployment.md) | Installing it on a server |
| [Operations](docs/operations.md) | Start, stop, update, rebuild, back up |
| [Authentication](docs/authentication.md) | SSH key vs GitHub token |
| [CLI](docs/cli.md) / [API](docs/api.md) | Every command and endpoint |
| [Publishing `debian/upstream.md`](docs/upstream-md-publishing.md) | How PRs are proposed safely |
| [Production handoff](docs/production-handoff.md) | Current state, the first `mstpd` PR, and **what happens next** |
| [Troubleshooting](docs/troubleshooting.md) | Symptoms and fixes |

## What happens next

The deployment is ready and read-only. The remaining steps - the token owner adds
`APM_GITHUB_TOKEN`, the first real PR is created for `mstpd`, and after it is
reviewed the remaining eligible packages follow - are written out command by
command in
[docs/production-handoff.md → What happens next](docs/production-handoff.md#what-happens-next).
