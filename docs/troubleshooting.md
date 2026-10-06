# Troubleshooting

First, always:

```bash
docker compose ps                                        # running? healthy?
docker compose logs --tail 100 app                       # startup lines and errors
docker compose exec app apm doctor --repo Arrcus/mstpd   # storage, git, GitHub
```

## The container

| Symptom | Cause | Fix |
|---|---|---|
| Exits at start: `apm: output directory /app/out is not writable by uid N` (or workspace, cache) | `APM_UID`/`APM_GID` do not own the host directory | Set them to `id -u` / `id -g` of the owner, then `docker compose up -d --build`; or fix the directory's ownership |
| Exits at start: `the SSH key mounted at ... is not readable by uid N` | The key belongs to another user, or `APM_GIT_SSH_KEY_FILE` points at a missing file | Fix the path or the ownership; set `APM_UID`/`APM_GID` to the key's owner and rebuild |
| A workspace directory owned by root appeared | It did not exist, so Docker created it | Remove it, `mkdir` it as the right user, `docker compose up -d` |
| `bind: address already in use` on 8080 | Something else listens on 8080 - often the development server | `scripts/server.sh stop`, or set `APM_PORT` |
| `unhealthy` | The process is not answering `/health` | `docker compose logs app`; `docker compose restart` |
| A change to `.env` has no effect | The environment is fixed at container creation | `docker compose up -d --force-recreate` |
| Build fails fetching GitHub host keys | The build host cannot reach api.github.com | Fix network/proxy access, rebuild |
| Stopped with exit code 143 | Normal: uvicorn shut down gracefully on `SIGTERM`, then re-raised it | Nothing |

## Git and SSH

| Symptom | Cause | Fix |
|---|---|---|
| `doctor`: `FAIL private repository access` | The key cannot reach `Arrcus/arrcus_rel`, needs a passphrase, or `APM_GIT_SSH_KEY_FILE` is unset | Check `ssh -T git@github.com` on the host with that key; use a passphrase-less key |
| `doctor`: `git backend auto` and private access fails | `APM_GIT_BACKEND` is missing from `.env` (the code default is `auto`, which expects a bridge) | Set `APM_GIT_BACKEND=local`, recreate |
| `Host key verification failed` for a host other than github.com | Its key is not known | Mount a `known_hosts` with `APM_SSH_KNOWN_HOSTS_FILE` |
| `PUSH_FAILED` | The key's account cannot push to that repository | Grant write access; `--retry-failed` |
| `TIMEOUT` on a first comparison or probe | A large first fetch (the kernel) exceeded `APM_GIT_LONG_TIMEOUT` | Raise it in `.env`, recreate, retry |

## GitHub token

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard says read-only; `create_pull_requests: false` | No `APM_GITHUB_TOKEN`, or the container was not recreated after adding it | Set it in `.env`; `docker compose up -d --force-recreate` |
| `APM_GITHUB_TOKEN is not set, so no pull request could be opened. Nothing was pushed.` | As it says | As above |
| `GitHub rejected the token. Check it is valid and not expired.` | Invalid, revoked or expired | Create a new token; replace it in `.env`; recreate |
| `The token is not SSO-authorised for Arrcus.` | SAML SSO not granted | Authorise the token for the organisation on GitHub |
| `The token cannot see Arrcus/<repo>.` | The token has no access to that repository | Add the repository to the token's access |
| `PR_FAILED` / `The token is not permitted to open pull requests on ...` | Missing **Pull requests: write** | Fix the permission; `--retry-failed` (the pushed branch is reused) |

## Resolution

| Symptom | Meaning | What to do |
|---|---|---|
| `NEEDS_REVIEW` / `CURATED_CONFLICT` | The curated repository shares no history; another candidate does | A person decides; update `config/overrides.yaml` through a PR |
| `NEEDS_REVIEW` / `NO_SHARED_HISTORY`, `CONTENT_MATCH_UNAPPROVED` | A tarball import; a content match is waiting | Review it; `apm approve-content-base` if right |
| `FAILED` / `PROBE_FAILED`, `NETWORK_ERROR`, `ARCOS_UNREACHABLE` | The resolver could not finish - not a finding | Fix access; `resolve-release --package <p> --no-discover` |
| Plausibility warnings (far behind, old merge base, series mismatch) | Suspicious, not rejected | Read the warning; it may point at a wrong series or fork base |
| Dashboard shows an old answer after a new run | 15-minute in-memory cache for packages already viewed | Wait, or `docker compose restart` |
| `no mapping rows for bookworm` | No mapping yet | `apm resolve-release --release bookworm` |

## Comparison

| Symptom | Meaning |
|---|---|
| `NO_COMMON_ANCESTOR` (422) | The fork shares no history with the upstream and no content match is approved. Not a configuration problem |
| "backport detection unavailable" | The range is beyond the 5000-commit limit; nothing is assumed missing |
| Many `UNKNOWN` commits | Beyond `comparison.content_check_limit`, partly present, or undecidable - look at them |
| First comparison very slow | It fetches both histories into the workspace; the next takes seconds |

## Generating and publishing

| Status / message | Meaning | Fix |
|---|---|---|
| `no plan at ...upstream-md-plan-bookworm.json` | Not generated yet | `apm generate-upstream-md --release bookworm --branch aminor` |
| `not in upstream-md-plan-bookworm.json: <p>` | The plan does not list that package (e.g. it was generated with `--package` for another one) | Regenerate without `--package` |
| `name the packages with --package, or pass --all` | No scope given | Add one |
| `This run would push N package(s); confirm with --confirm-count N.` | `--apply` without a matching confirmation | Check N is what you expect; pass it |
| `DRIFT` | The file, the mapping or the upstream ref changed since the plan | `resolve-release` (if upstream moved), `generate-upstream-md`, review, dry run |
| `CONFLICT` | The fork has a `debian/upstream.md` this tool did not write | Compare by hand; never replaced automatically |
| `BRANCH_EXISTS` | The branch exists and this tool did not create it | Find out who did; delete it only if it is known to be unwanted |
| `PR_CLOSED` | A reviewer closed the earlier PR | A person decides whether to propose again |
| Run stopped after 3 failures | Consecutive failures usually mean auth or network | Fix the cause; re-run (or `--retry-failed`) |
| `... exists but cannot be read ... Move it aside deliberately` | The results ledger is corrupt | Do not delete it: it is the record of existing branches and PRs; restore it from a backup |

## Dashboard

| Symptom | Cause |
|---|---|
| Blank page, API works | The image was built without the dashboard - rebuild (`docker compose build --no-cache`) |
| "Resolution could not complete" | A `FAILED` resolution; the review reason says why |
| "A proven relationship ... is needed before comparing" | The package is not `VERIFIED`; Compare is disabled by design |
| Create pull request disabled | No token configured |
