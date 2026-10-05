# Deployment

## Repository access

The machine running this tool does not need GitHub access. What it needs is a
route to one.

```
this host ──SSH──▶ bridge host ──▶ private repositories
```

`APM_GIT_BACKEND` chooses:

| Value | Behaviour |
|---|---|
| `auto` | Private repositories (per `APM_PRIVATE_REPOSITORY_PATTERNS`) go over SSH; everything else runs locally. Right for a laptop. |
| `local` | Everything runs here. Right where this host already has access. |
| `ssh` | Everything runs on the configured host. |

No host name, user or organisation appears in application code. Which
repositories need the bridge is a configurable list of regular expressions.

### SSH configuration

The cleanest setup puts everything in `~/.ssh/config` and names only the alias:

```
Host arcos-bridge
    HostName build-host.example.internal
    User builder
    IdentityFile ~/.ssh/id_arcos
    IdentitiesOnly yes
```

```
APM_GIT_BACKEND=auto
APM_SSH_HOST=arcos-bridge
```

Through a bastion, either put `ProxyJump` in `~/.ssh/config` or set:

```
APM_SSH_PROXY_JUMP=jumpuser@bastion.example.internal
```

Key-based authentication is preferred. `APM_SSH_PASSWORD` exists for hosts that
permit nothing better; it is a secret and must never be committed.

Verify before starting the app:

```bash
ssh arcos-bridge 'git ls-remote ssh://git@github.com/<org>/<repo>.git HEAD | head -1'
```

## Workspace

Comparison and cherry-pick need a real repository. `APM_WORKSPACE_DIR` is a path
**on the host git runs on** — with the `ssh` backend that is the bridge, not this
machine. It must be writable and have room for the repositories involved.

Check it before debugging anything else:

```bash
curl -s localhost:8080/api/health/workspace
```

## GitHub token

Two separate credentials are involved, and they never stand in for each other:

| | Git transport (fetch, push) | GitHub REST API (find/open PRs) |
|---|---|---|
| Code | `gitio/transport.py` (`SshGit`) | `services/github_service.py` |
| Runs on | the SSH bridge; workspaces live there | the backend, directly to `APM_GITHUB_API_URL` |
| Credential | the GitHub SSH key on the bridge host | `APM_GITHUB_TOKEN` |

So branches are pushed with the bridge's key, and pull requests are opened by
whoever owns the token. The backend must be able to reach `api.github.com:443`
itself; the bridge does not carry API traffic.

Without `APM_GITHUB_TOKEN` everything works except opening pull requests. The
dashboard says so up front, and `publish-upstream-md --apply` refuses before any
push - a pre-flight checks the token against every repository first, so a token
that cannot open PRs never leaves a pushed branch behind.

The token needs **Pull requests: Read and write** and **Metadata: Read** on the
package repositories (fine-grained), or `repo` scope (classic). It does not
need Contents: write, because branches are pushed over SSH. For a first test,
access to the one repository being proposed (e.g. `Arrcus/mstpd`) is enough;
pre-flight checks the token against exactly the repositories in the run. If the organisation enforces SAML, the token must be
SSO-authorised; pre-flight reports GitHub's SSO message verbatim. A 404 on a
private repository is what an unauthorised token looks like - check the token
before the spelling.

Commits are authored as `APM_COMMITTER_NAME <APM_COMMITTER_EMAIL>`; set both to
the person accountable for the pull requests.

## Local development (macOS)

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env         # then edit

./scripts/dev-backend.sh     # :8000
./scripts/dev-frontend.sh    # :5173, proxies /api to :8000
```

Vite binds IPv6 localhost, so use `http://localhost:5173`, not `127.0.0.1`.

## Docker

```bash
cp .env.example .env
docker compose up -d --build
```

Only the frontend publishes a port (`APM_PORT`, default 8080); the backend is
reached through it, so there is one origin and no CORS to configure.

To give the container the bridge key:

```
APM_SSH_IDENTITY_FILE=/home/you/.ssh/id_arcos
APM_SSH_KNOWN_HOSTS_FILE=/home/you/.ssh/known_hosts
```

Both mount read-only. `APM_UID` should match the owner of those files.

`./out` is mounted read-write at `/app/out`: the mapping, the generated
`debian/upstream.md` files, the plan and the PR results ledger live there, so a
rebuild never loses them. Create it before the first start and make it writable
by `APM_UID` (setting `APM_UID=$(id -u)` in `.env` is simplest).

`./config` stays mounted **read-only**. Everything generated at runtime goes to
the writable `out/` mount: the package catalogue `discover` (and
`resolve_bookworm`) rewrites is written to `APM_PACKAGES_FILE`
(`/app/out/packages.yaml`), approvals to `APM_APPROVALS_FILE`
(`/app/out/approvals.yaml`), and comparison snapshots to `out/comparisons/`.
Until the first discovery, the committed `config/packages.yaml` is read instead.

`.env` names key and known_hosts files by their **host** paths; the compose
file mounts them read-only and points the container at the mounted copies, so
the container never tries to use a path that only exists on the host.

## On the host that holds repository access (the authoritative environment)

When the machine that runs the tool is also the machine whose SSH key reaches
the private repositories, no bridge is needed: git runs inside the container
with that key mounted read-only. This is the supported setup for development
and testing from now on; nothing depends on a second machine.

```
# .env on that host - values are placeholders
APM_GIT_BACKEND=local
APM_GIT_SSH_KEY_FILE=/home/<user>/.ssh/id_ed25519
APM_SSH_KNOWN_HOSTS_FILE=/home/<user>/.ssh/known_hosts
APM_UID=<id -u>                          # the key's owner, so it is readable
APM_WORKSPACE_HOST_DIR=/var/tmp/arcos-package-manager   # reuse existing clones
APM_GITHUB_TOKEN=<entered on the host by its owner; never in chat or git>
APM_COMMITTER_NAME=<person accountable for the PRs>
APM_COMMITTER_EMAIL=<their address>
```

Pushes use that SSH key; the token is used only for the GitHub REST API (find
and open pull requests). Then:

```bash
mkdir -p out && docker compose up -d --build
docker compose exec backend apm doctor --repo Arrcus/mstpd
```

`apm doctor` checks, without printing any secret: `out/` and the runtime
package catalogue are writable and `config/` is not written to; the workspace
and git version; private access to the release manifest; public upstream
access; the GitHub SSH identity used for pushes; and, with a token, GitHub API
reachability and that the token can see each `--repo`.

The backend test suite runs against the same image:

```bash
docker build --target test -t apm-test . && docker run --rm apm-test
```

## Deploying to a VM

The VM does not need repository access either, as long as it can reach the
bridge. On the VM:

1. Install Docker and clone this repository.
2. Create `.env` — the SSH bridge, `APM_GITHUB_TOKEN` if PRs are wanted, and
   `APM_WORKSPACE_DIR` if the default has too little space.
3. Add the bridge host key to `known_hosts` (or leave
   `APM_SSH_STRICT_HOST_KEY_CHECKING=accept-new` for the first connection).
4. `docker compose up -d --build`
5. Confirm `/api/health` and `/api/health/workspace`.

Updating:

```bash
git pull && docker compose up -d --build
```

## Logs and troubleshooting

```bash
docker compose logs -f backend
```

Tokens and passwords are never logged.

| Symptom | Cause |
|---|---|
| `REPOSITORY_UNAVAILABLE` | Bridge unreachable, or no access to that repository. Test the `ssh` command above. |
| `health/workspace` not writable | `APM_WORKSPACE_DIR` missing or unwritable **on the git host**. |
| `AUTH_REQUIRED` | No `APM_GITHUB_TOKEN`, or not SSO-authorised. |
| `NO_COMMON_ANCESTOR` | Not a configuration problem: the fork was imported, not forked. |
| First comparison very slow | It clones both sides. The workspace is reused; the next one takes seconds. The kernel takes ~10 minutes cold, ~11s warm. |
| `TIMEOUT` on a large package | Raise `APM_GIT_LONG_TIMEOUT` (default 900s). Check the git host is not also running another clone. |
| Workspace growing large | Each package keeps a clone under `APM_WORKSPACE_DIR`. The kernel alone is several GB; delete `repos/<package>__<release>` to reclaim it. |
| Branch list empty | Branch discovery failed; the manifest's branch is offered instead. |
