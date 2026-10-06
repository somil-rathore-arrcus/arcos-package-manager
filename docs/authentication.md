# Authentication: the SSH key and the GitHub token

Two credentials, for two different jobs. Neither can stand in for the other.

| | SSH key | `APM_GITHUB_TOKEN` |
|---|---|---|
| Used for | git: `ls-remote`, fetch, clone, **push** | GitHub REST API: look up a repository, find and **open pull requests** |
| Code | `src/apm/gitio/transport.py` | `src/apm/services/github_service.py` |
| Needed for | everything that reads repositories, and pushing branches | only opening (and looking up) pull requests |
| Without it | private repositories cannot be read; nothing works for the ARCoS forks | read-only: everything works except opening PRs |
| Lives | on the host; `.env` names its path; mounted read-only | in `.env` on the host; passed to the container's environment |

A pull request therefore involves three identities:

| Identity | Comes from | Does |
|---|---|---|
| Commit author/committer | `APM_COMMITTER_NAME`, `APM_COMMITTER_EMAIL` | written into the `debian/upstream.md` commit |
| Branch pusher | the GitHub account of the SSH key | pushes `upstream-metadata/<release>/<package>` |
| PR author | the owner of the token | opens the pull request |

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
publisher's dry run does not push.

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

## GitHub token: pull requests

### What it is used for

- `GET /repos/{owner}/{repo}` - pre-flight: can the token see the repository?
- `GET /repos/{owner}/{repo}/pulls` - is there already a PR for this branch?
- `POST /repos/{owner}/{repo}/pulls` - open the PR.

Nothing else. Branches are pushed with the SSH key, never with the token.

### Permissions

Create it on GitHub by the account that should appear as PR author:

- **Fine-grained token**: resource owner the organisation (`Arrcus`);
  repository access to the ARCoS package repositories; permissions
  **Pull requests: Read and write** and **Metadata: Read**. `Contents: write`
  is not needed.
- **Classic token**: scope `repo`.
- If the organisation enforces SAML SSO, authorise the token for it.
- Give it an expiry date. When it expires, the publisher stops with
  `AUTH_REQUIRED` before pushing anything.

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
#   apm: git backend=local, git ssh key=mounted read-only, github token=set

docker compose exec app apm doctor --repo Arrcus/mstpd
#   PASS  APM_GITHUB_TOKEN is set  -> value not shown
#   PASS  GitHub API reachable at https://api.github.com  -> HTTP 200, <n> requests left of <limit>
#   PASS  token can see Arrcus/mstpd  -> default branch ..., permissions [...]
```

And from any machine: `curl -s http://<server-ip>:8080/api/health` shows
`"create_pull_requests": true` and `"read_only": false`; the dashboard no
longer shows the read-only badge.

`token can see Arrcus/mstpd` proves the token can see the repository. The
`permissions` listed are the account's role on it, not the token's own
permissions; the right to open a PR is proven by the first real PR.

### Without a token

- `/api/health` reports `"read_only": true`; the dashboard says so and disables
  the buttons that would push or open a PR.
- `apm publish-upstream-md --apply` stops before pushing:
  `APM_GITHUB_TOKEN is not set, so no pull request could be opened. Nothing was pushed.`
- Everything else - discovery, resolution, comparison, preview, generation,
  dry runs - works.

### Rotating or removing it

Replace (or blank) the value in `.env`, then
`docker compose up -d --force-recreate`. Revoke the old token on GitHub.

### Token errors

| Message | Meaning |
|---|---|
| `GitHub rejected the token. Check it is valid and not expired.` | HTTP 401: wrong, revoked or expired |
| `The token is not SSO-authorised for Arrcus. Authorise it for the organisation, then retry.` | SAML SSO not granted |
| `The token cannot see Arrcus/<repo>. ...` | No repository access (for a private repository, GitHub answers 404/403) |
| `The token is not permitted to open pull requests on Arrcus/<repo>.` | Missing **Pull requests: write** |

## Secret handling rules

- `.env` is gitignored and excluded from the Docker build context; the image
  contains no `.env`, key or token (`tests/test_docker_contract.py` checks
  the Dockerfile and Compose file for this).
- The entrypoint, `doctor` and `/api/health` report only *set* / *not set*.
- No log line, API response or ledger contains the token.
- The key is mounted read-only and only into the container's own `~/.ssh`.
- Keep `.env` at mode `600`.
