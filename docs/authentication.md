# Authentication: the SSH key, `APM_ALLOW_PUSH` and the GitHub token

Two credentials, for two different jobs. Neither can stand in for the other.

| | SSH key | `APM_GITHUB_TOKEN` |
|---|---|---|
| Used for | git: `ls-remote`, fetch, clone, **push** | GitHub REST API, **read only**: look up a repository, find a pull request a person opened, read its CI checks |
| Code | `src/apm/gitio/transport.py` | `src/apm/services/github_service.py` |
| Needed for | everything that reads repositories, and pushing branches | only those lookups - never for a push, and never to open a pull request: the tool opens none |
| Without it | private repositories cannot be read; nothing works for the ARCoS forks | everything works; a pull request opened from a pushed branch is still found from git refs, but not its state or CI |
| Lives | on the host; `.env` names its path; mounted read-only | in `.env` on the host; passed to the container's environment |

A third setting is not a credential but decides whether this machine may push
at all: [`APM_ALLOW_PUSH`](#apm_allow_push-the-one-machine-that-pushes).

A pull request therefore involves three identities:

| Identity | Comes from | Does |
|---|---|---|
| Commit author/committer | `APM_COMMITTER_NAME`, `APM_COMMITTER_EMAIL` | written into the `debian/upstream.md` commit |
| Branch pusher | the GitHub account of the SSH key | pushes `debian-upstream-tracking` (earlier `upstream-metadata/<release>/<package>`) |
| PR author | whoever opens the link - never the tool, whatever token is set | opens the pull request |

## SSH: git access

### How it works in Docker

1. `.env` names the key's **host** path:
   `APM_GIT_SSH_KEY_FILE=/home/<user>/.ssh/id_ed25519`.
2. Compose mounts that one file read-only at `/home/apm/.ssh/id_git` and sets
   `APM_GIT_SSH_IDENTITY_FILE` to it.
3. With `APM_GIT_BACKEND=local`, git runs in the container with
   `GIT_SSH_COMMAND="ssh -i /home/apm/.ssh/id_git -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=..."`.
4. GitHub's host keys are built into the image (`/etc/ssh/ssh_known_hosts`,
   fetched from `https://api.github.com/meta` over HTTPS at build time), so
   strict host key checking works without mounting a `known_hosts`.

The key is never copied into the image or the repository, and nobody pastes it
anywhere.

### Requirements

- Readable by `APM_UID` (normally: owned by the same user, mode `600`).
- **No passphrase** (`BatchMode=yes` cannot prompt). Use a key made for this,
  such as a machine or deploy key, or one already used non-interactively on the
  host.
- Its GitHub account can read `Arrcus/arrcus_rel` and every ARCoS package
  repository, and - for publishing - **push** to the package repositories.

### Check it

```bash
docker compose exec app apm doctor --repo Arrcus/mstpd
```

The git section should show:

```
PASS  git backend local  -> ssh bridge not configured, local ssh key set, ssh password not set
PASS  workspace /var/tmp/arcos-package-manager on the local git host  -> git version ...
PASS  private repository access: ssh://git@github.com/Arrcus/arrcus_rel.git aminor  -> <sha>
PASS  git access to Arrcus/mstpd
PASS  public upstream access (github.com)
PASS  GitHub SSH identity for pushes  -> authenticated as <github-account>
```

`doctor` proves read access. Push access is proven at push time; the
publisher's dry run does not push. The `GitHub SSH identity for pushes` line
names the account branches will be pushed as (on the production host,
`lakshya-arrcus`).

### Changing the key

Edit `APM_GIT_SSH_KEY_FILE` in `.env`, then
`docker compose up -d --force-recreate`. If the new key belongs to another
host user, set `APM_UID`/`APM_GID` to that user and use `--build`.

### Optional: SSH bridge

For a deployment whose own key cannot reach the repositories, git commands can
run on another host that can (`APM_GIT_BACKEND=ssh`, or `auto` for only the
repositories matching `APM_PRIVATE_REPOSITORY_PATTERNS`). Set `APM_SSH_HOST`,
`APM_SSH_USER` and `APM_SSH_IDENTITY_FILE` (a host path, mounted read-only).
`APM_SSH_PASSWORD` exists for hosts that permit nothing better and is a secret.
The standard deployment does not use a bridge.

## `APM_ALLOW_PUSH`: the one machine that pushes

Pushing is off unless `APM_ALLOW_PUSH` is `1`, `true`, `yes` or `on`; anything
else, including unset, is off. It is set only in the `.env` of the Docker
Compose deployment on Lakshya's machine - the one that holds the ARCoS git
access. A copy of the application started anywhere else compares, previews and
dry-runs, and refuses every push - `debian/upstream.md` branch or patch branch,
CLI, dashboard or API - with `PUSH_DISABLED` (HTTP 403) before anything is
pushed.

```bash
nano .env                               # APM_ALLOW_PUSH=1, on that machine only
docker compose up -d --force-recreate
docker compose logs app | grep pushing
#   apm: pushing enabled on this machine; pull requests are never opened by this application
docker compose exec app apm doctor --repo Arrcus/mstpd
#   PASS  APM_ALLOW_PUSH is set  -> this machine pushes branches; no pull request is ever opened
```

`/api/health` then reports `"push_enabled": true` and `"read_only": false`.
Without it: `"push_enabled": false`, `"read_only": true`, the dashboard shows
**Pushing is disabled on this deployment** and offers no push button, and
`doctor` reports `WARN APM_ALLOW_PUSH is not set`. Setting it does not grant
push access: the SSH key's account still needs it on each repository.

## GitHub token: read-only lookups

The application never creates, opens, submits or merges a pull request, with
or without a token. The token only lets it read.

### What it is used for

- `GET /repos/{owner}/{repo}` - `apm doctor`: can the token see the repository?
- `GET /repos/{owner}/{repo}/pulls` - is there a pull request for this branch -
  opened by a person from the link - and is it open, merged or closed?
- `GET /repos/{owner}/{repo}/pulls/{number}` - look one up by number.
- `GET /repos/{owner}/{repo}/commits/{sha}/check-runs` and `.../status` - read
  CI for a pushed branch.

Nothing else. Every request is a `GET`: the client refuses any other method
with `PULL_REQUESTS_DISABLED`, and there is no code path that writes to
GitHub. Branches are pushed with the SSH key, never with the token.

### Permissions

- **Fine-grained token**: resource owner the organisation (`Arrcus`);
  repository access to the ARCoS package repositories; permissions
  **Pull requests: Read** and **Metadata: Read**, plus **Checks: Read** and
  **Commit statuses: Read** to show CI. No write permission is needed or used.
- **Classic token**: scope `repo` (the classic scope that reads private
  repositories; the tool still only reads).
- If the organisation enforces SAML SSO, authorise the token for it.
- Give it an expiry date. Once it expires, each package that reaches the pull
  request lookup ends `ERROR` (`GitHub rejected the token. ...`) before its
  push; replace the token or remove it.

### Putting it on the server

On the server only, by the token's owner:

```bash
cd ~/somil/arcos-package-manager        # the checkout
nano .env                               # or vi: set APM_GITHUB_TOKEN=<token>
chmod 600 .env
docker compose up -d --force-recreate   # the container reads .env only when created
```

- Use an editor. `echo ... >> .env` or `export` would leave the token in the
  shell history, and an exported variable does not reach the container anyway
  (Compose passes `.env`, not the shell).
- Never paste the token into chat, tickets, documentation, commit messages or
  command lines.

### Check it

```bash
docker compose logs app | grep "github token"
#   apm: git backend=local, git ssh key=mounted read-only, github token=set (lookups only)

docker compose exec app apm doctor --repo Arrcus/mstpd
#   PASS  APM_GITHUB_TOKEN is set  -> value not shown
#   PASS  GitHub API reachable at https://api.github.com  -> HTTP 200, <n> requests left of <limit>
#   PASS  token can see Arrcus/mstpd  -> default branch ..., permissions [...]
```

And from any machine: `curl -s http://<server-ip>:8080/api/health` shows
`"read_checks": true`. `"create_pull_requests"` stays `false` and
`"pull_requests"` stays `"never"` whatever token is set, and `"read_only"`
depends only on [`APM_ALLOW_PUSH`](#apm_allow_push-the-one-machine-that-pushes).

`token can see Arrcus/mstpd` proves the token can see the repository. The
`permissions` listed are the account's role on it, not the token's own
permissions; its read access to pull requests and checks shows in the
dashboard's pull request and CI panel.

### Without a token

- Everything works - discovery, resolution, comparison, preview, generation,
  dry runs and, where `APM_ALLOW_PUSH` is set, pushing. `doctor` reports
  `PASS APM_GITHUB_TOKEN is not set` (not needed).
- A pull request opened from a pushed branch is still found - number and
  link - from the fork's `refs/pull/<n>/head` over SSH. Its state and its CI
  checks are not: they need the API, so `/api/health` reports
  `"read_checks": false` and `/api/publish-status` reports CI as
  `UNAVAILABLE` with that reason.
- The publisher cannot ask GitHub whether a branch's pull request was merged
  or closed, so it relies on the results ledger
  ([upstream-md-publishing.md](upstream-md-publishing.md#the-links-a-person-opens-the-pull-request)).

### Pushing: SSH push access is enough

A push - `apm publish-upstream-md --apply`, **Push branch (no PR)** and
**Push patch branch (no PR)** in the dashboard, or the API - needs only what a
push needs: the SSH key, push access for its GitHub account to each package
repository, and `APM_ALLOW_PUSH` on this machine. It needs no token. Without a
token it makes no GitHub API call at all, and relies on the results ledger to
know which packages already have a pull request. If a token is set, it is used
for at most one read per package - the lookup for an earlier pull request - so
a GitHub outage or an expired token can still stop a package with `ERROR`
before its push. The pull request is always opened by a person: GitHub's
pre-filled form, from the link the tool prints, in a browser.

| Identity | When the tool pushes |
|---|---|
| Commit author/committer | `APM_COMMITTER_NAME`, `APM_COMMITTER_EMAIL`, as always |
| Branch pusher | the SSH key's GitHub account |
| PR author | whoever opens the link - it must be someone who can open pull requests on that repository |

The link carries no credential; it only fills in the form. Someone without
access to the repository gets GitHub's 404 page. Details:
[upstream-md-publishing.md → The links](upstream-md-publishing.md#the-links-a-person-opens-the-pull-request).

### Rotating or removing it

Replace (or blank) the value in `.env`, then
`docker compose up -d --force-recreate`. Revoke the old token on GitHub.

### Token errors

| Message | Meaning |
|---|---|
| `GitHub rejected the token. Check it is valid and not expired.` | HTTP 401: wrong, revoked or expired |
| `The token is not SSO-authorised for Arrcus. Authorise it for the organisation, then retry.` | SAML SSO not granted |
| `The token cannot see Arrcus/<repo>. ...` | No repository access (for a private repository, GitHub answers 404/403) |
| `PULL_REQUESTS_DISABLED` | Something asked the tool to open a pull request, or to write to GitHub. It never does, whatever the token allows; open the PR from the branch's link |

## Secret handling rules

- `.env` is gitignored and excluded from the Docker build context; the image
  contains no `.env`, key or token (`tests/test_docker_contract.py` checks
  the Dockerfile and Compose file for this).
- The entrypoint, `doctor` and `/api/health` report only *set* / *not set*.
- No log line, API response or ledger contains the token.
- The key is mounted read-only and only into the container's own `~/.ssh`.
- Keep `.env` at mode `600`.
