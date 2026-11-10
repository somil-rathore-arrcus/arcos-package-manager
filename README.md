# ARCoS Package Manager

For every ARCoS package it finds the real upstream project, proves the
relationship with git history, shows which upstream fixes the ARCoS fork is
missing, and records the verified answer in the package repository as
`debian/upstream.md` - on a pushed branch from which a person opens the pull
request. The tool never creates, opens, submits or merges a pull request.

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
| Record | Renders `debian/upstream.md` - `Upstream Source` and `Current Upstream Branch`, nothing else - from the verified mapping | Files + a branch/commit plan with review flags |
| Publish | Dry run, then one pushed branch per eligible package, with its Compare Link and a pre-filled link to open the PR; a person opens it | Branches on the ARCoS package repositories + a results ledger + a Bookworm/Trixie publishing report |

## Quick start (Docker)

Needs Docker with the Compose plugin, and an SSH key on the server that can
reach the Arrcus GitHub repositories.

```bash
git clone https://github.com/somil-rathore-arrcus/arcos-package-manager.git
cd arcos-package-manager
cp .env.example .env
# edit .env: APM_UID/APM_GID (id -u / id -g) and APM_GIT_SSH_KEY_FILE (host path of the key)
# only on the one machine that pushes (Lakshya's): APM_ALLOW_PUSH=1
docker compose up -d --build
docker compose ps                                          # STATUS: healthy
docker compose exec app apm doctor --repo Arrcus/mstpd     # storage, git and GitHub checks
```

Open `http://<server-ip>:8080/`.

Without `APM_ALLOW_PUSH` the application is **read-only**: everything works -
mapping, comparison, `upstream.md` generation, previews, dry runs - except
pushing, which is refused with `PUSH_DISABLED`. It is set (`APM_ALLOW_PUSH=1`)
only in the `.env` of the deployment on Lakshya's machine. There,
`publish-upstream-md --apply` pushes the branches with the SSH key and prints
the links a person opens each PR from. `APM_GITHUB_TOKEN` is optional and only
read: it finds a pull request a person opened, and its CI checks. See
[docs/authentication.md](docs/authentication.md).

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
| Dry-run one package | `docker compose exec app apm publish-upstream-md --release bookworm --package mstpd` |
| Push one branch (a person opens the PR from its link) | `docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --apply --confirm mstpd` |
| Rebuild the Bookworm/Trixie publishing report | `docker compose exec app apm upstream-md-report` (`--verify` also checks every pushed branch) |

## Safety rules (enforced by the code)

- An upstream is never guessed: every repository is contacted, and only shared
  history (or a content match a person approved) makes a package `VERIFIED`.
- `debian/upstream.md` writes only recorded facts. A branch that was not
  verified is written as `N/A` with the reason - for a tag-only upstream,
  `N/A - no upstream branch verified; upstream tag <tag>` - never a guess. A
  tag-only upstream names a branch only when the ancestry probe measured the
  upstream default branch as holding the fork's base, flagged `BRANCH_FROM_TAG`.
- Only `VERIFIED` packages are ever proposed. `NEEDS_REVIEW`, `NO_UPSTREAM` and
  `FAILED` never are.
- Branches go to the **ARCoS package repositories** (e.g. `Arrcus/mstpd`,
  branch `upstream-metadata/bookworm/mstpd` → `aminor`), never to upstream
  projects.
- The tool never creates, opens, submits or merges a pull request; the GitHub
  API is only read. A person opens each one from the branch's Compare Link.
- The target branch is never written to, nothing is force-pushed, and each
  commit changes exactly `debian/upstream.md`.
- Publishing is a dry run unless `--apply` is given with a confirmation that
  names what will be pushed, and pushes only where `APM_ALLOW_PUSH` is set.
- What is pushed - repository, target branch, branch name and bytes - comes
  from the reviewed plan and `settings.yaml`, from the CLI and the dashboard
  alike. A package whose identical file another release's branch already
  proposes to the same target is `COVERED`: no second branch.
- A `debian/upstream.md` the tool does not recognise as its own (anything but
  the two fields, or an older version's file) is never overwritten.
- No secret is stored in the repository or the image, or printed in logs.

## Documentation

Start with [docs/README.md](docs/README.md). The most-used pages:

| Page | For |
|---|---|
| [Overview](docs/overview.md) | What the tool is and the end-to-end workflow |
| [Docker deployment](docs/deployment.md) | Installing it on a server |
| [Operations](docs/operations.md) | Start, stop, update, rebuild, back up |
| [Authentication](docs/authentication.md) | SSH key, `APM_ALLOW_PUSH` and the read-only GitHub token |
| [CLI](docs/cli.md) / [API](docs/api.md) | Every command and endpoint |
| [Publishing `debian/upstream.md`](docs/upstream-md-publishing.md) | How branches are pushed safely, and the links PRs are opened from |
| [Production handoff](docs/production-handoff.md) | Current state, the first `mstpd` PR, and **what happens next** |
| [Troubleshooting](docs/troubleshooting.md) | Symptoms and fixes |

## What happens next

The remaining steps - deploy this version on the machine with
`APM_ALLOW_PUSH`, regenerate the two-field files and review the flagged
packages, push the `mstpd` branch for Lakshya to open its PR from the link,
and after it is reviewed the remaining eligible packages in batches of five -
are written out command by command in
[docs/production-handoff.md → What happens next](docs/production-handoff.md#what-happens-next).
