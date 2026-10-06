# Production handoff

## Current state (2026-10-06)

| | |
|---|---|
| Host | Lakshya's machine |
| Checkout | `/home/lakshya/somil/arcos-package-manager`, branch `main` |
| Runtime | Docker Compose, service `app`, container `arcos-package-manager-app-1`, healthy |
| Dashboard | `http://<server-ip>:8080/` |
| Git access | `APM_GIT_BACKEND=local` with the host's own SSH key, mounted read-only; authenticates to GitHub as `lakshya-arrcus`; read and push (dry-run) verified on all 52 Arrcus repositories in use |
| GitHub token | **Not set** - the deployment is read-only |
| Commit author | `APM_COMMITTER_NAME` / `APM_COMMITTER_EMAIL` in `.env` (Somil Rathore) |
| Data | `out/` in the checkout; clones in `/var/tmp/arcos-package-manager`; cache in volume `apm-cache` |
| Tests | backend 438 passed, frontend 34 passed (in Docker) |

### Bookworm results

| | Count |
|---|---|
| Packages in the Bookworm manifest | 53 |
| `VERIFIED` | 23 |
| `NEEDS_REVIEW` | 8 |
| `NO_UPSTREAM` | 22 |
| `FAILED` | 0 |
| In the PR plan (`VERIFIED`, on `aminor`) | 22 |
| Excluded in `settings.yaml` (`ONL-standalone`: its repository has no `debian/`) | 1 |
| **Eligible for a PR - dry run `DRY_RUN_OK`** | **21** |
| Real PRs created | 0 |

Eligible: `cre2`, `ethtool`, `hsflowd`, `ifupdown2`, `iproute2`, `iputils`,
`libdict`, `libnl3`, `libpam-radius-auth`, `libyang1`, `linux`, `mstpd`,
`oom`, `openssl`, `pyrad`, `python-netfilterqueue`, `rtrlib`, `tacacs_plus`,
`ydk-cpp`, `zenoh`, `zenoh-c`.

### Waiting for a person (never published until resolved)

| Package | Reason | Decision needed |
|---|---|---|
| `liburcu`, `lldpd`, `lttng-tools`, `lttng-ust`, `net-snmp` | `CURATED_CONFLICT` | The curated repository shares no history; the Debian packaging repository does. Which is the right upstream? |
| `babeltrace`, `ctypesgen`, `rsyslog` | `NO_SHARED_HISTORY`, `CONTENT_MATCH_UNAPPROVED` | Tarball imports. Approve the content match (`apm approve-content-base`) if it is right |

`openssl` and `linux` are eligible, but their plausibility warnings (a large
gap to Debian's release) deserve a careful review of their PRs.

## Who does what

| Role | Does |
|---|---|
| Token owner (Lakshya) | Creates the GitHub token, enters it in `.env` on the server, recreates the container. The token never leaves the server |
| Operator | Runs the dry runs and the `--apply` commands, watches the ledger |
| Package reviewers | Review and merge each PR on GitHub |
| Maintainer of this tool | Changes code and configuration through PRs to `arcos-package-manager` |

## Handoff checklist

```bash
cd /home/lakshya/somil/arcos-package-manager
git status                     # clean, on main
docker compose ps              # (healthy)
docker compose exec app apm doctor --repo Arrcus/mstpd   # FAILURES: none (token: WARN until set)
docker compose exec app python scripts/verify_output.py
docker compose exec app python scripts/verify_upstream_md.py bookworm
```

## The first PR: `mstpd`

Why `mstpd` first: small repository, `VERIFIED` with `SHARED_HISTORY`,
outcome `CREATED` (no existing file), so one PR shows the whole path end to
end before 20 more follow.

| | |
|---|---|
| Repository | `Arrcus/mstpd` |
| Branch | `upstream-metadata/bookworm/mstpd` |
| Target | `aminor` |
| File | `debian/upstream.md` (the only change) |
| Upstream recorded | `https://github.com/mstpd/mstpd.git`, branch `master` |
| Title | `mstpd: add debian/upstream.md with verified upstream` |

The first PR is successful when:

- the PR's **Files changed** shows exactly one new file, `debian/upstream.md`;
- the ARCoS CI description check passes (the body has `Problem Description :`,
  `Root Cause :`, `Fix Details :`);
- the Jira check passes. If it wants a Jira id, decide it before step 8:
  `--title-prefix "BR-NNN: "` puts it in front of both the commit title and
  the PR title of every later package;
- reviewers agree with the recorded upstream, and it is merged.

If anything about it is wrong, stop: do not run the other packages until the
cause is understood.

## Publishing the remaining packages safely

- Only `VERIFIED` packages are ever proposed; the code enforces it.
- Dry-run the whole set first; every eligible package must say `DRY_RUN_OK`.
- Publish in small batches (`--limit`), and check each batch's PRs and CI
  before the next.
- `--confirm-count` must equal what the dry run says it will push - a
  mismatch pushes nothing.
- `openssl` and `linux` are processed last by design.
- Three consecutive failures stop the run; fix the cause before re-running.
- Never delete `out/upstream-md-pr-results-bookworm.json` - it is the record
  of what exists.

---

## What happens next

Everything up to here is done: the deployment runs, the mapping is verified,
and the dry run for all 21 eligible packages passes. The steps below are what
remains. Run them on the server, in the checkout directory:

```bash
cd /home/lakshya/somil/arcos-package-manager
```

### 1. Lakshya adds `APM_GITHUB_TOKEN`

On GitHub, as the account that should open the PRs: create a fine-grained
token - resource owner **Arrcus**, repository access to the ARCoS package
repositories, permissions **Pull requests: Read and write** and **Metadata:
Read**, with an expiry date. If Arrcus enforces SAML SSO, authorise it.

On the server, with an editor - never `echo`, `export`, chat or a ticket:

```bash
nano .env              # set:  APM_GITHUB_TOKEN=<the token>
chmod 600 .env
```

### 2. Recreate the application so the token is loaded

```bash
docker compose up -d --force-recreate
docker compose ps                              # (healthy)
docker compose logs app | grep "github token"  # ... github token=set
```

`docker compose restart` is not enough: `.env` is read only when the container
is created.

### 3. Verify GitHub API access

```bash
docker compose exec app apm doctor --repo Arrcus/mstpd
```

Expect:

```
PASS  APM_GITHUB_TOKEN is set  -> value not shown
PASS  GitHub API reachable at https://api.github.com  -> HTTP 200, ... requests left of ...
PASS  token can see Arrcus/mstpd  -> default branch ..., permissions [...]
...
FAILURES: none
```

`curl -s http://<server-ip>:8080/api/health` now shows
`"create_pull_requests": true` and `"read_only": false`.

### 4. Run `mstpd` in dry-run mode

```bash
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --show-diff
```

Expect `DRY RUN - nothing will be pushed`, a row
`| mstpd | DRY_RUN_OK | upstream-metadata/bookworm/mstpd | <commit> | | |`,
the line `Arrcus/mstpd aminor @ <sha> -> upstream-metadata/bookworm/mstpd @ <commit>`,
the diff, and `Nothing was pushed. No pull request was created.`

If it reports `DRIFT` (the mapping or the upstream changed since the plan),
refresh and generate again, then repeat this step:

```bash
docker compose exec app apm resolve-release --release bookworm --package mstpd --no-discover
docker compose exec app apm generate-upstream-md --release bookworm --branch aminor
```

### 5. Review the branch, commit and PR plan

```bash
cat out/upstream-md/mstpd/debian/upstream.md
grep mstpd out/upstream-md-plan-bookworm.md
cat out/upstream-md-pr-dryrun-bookworm.md
```

Check: repository `Arrcus/mstpd`, base `aminor`, branch
`upstream-metadata/bookworm/mstpd`, outcome `CREATED`, upstream
`https://github.com/mstpd/mstpd.git` on `master`, `Status: VERIFIED`,
`Verification level: SHARED_HISTORY`, only `debian/upstream.md` changes.
Decide whether a Jira id is needed in the title (`--title-prefix "BR-NNN: "`).

### 6. Create the real `mstpd` PR

```bash
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --apply --confirm mstpd
```

Expect `APPLY - branches will be pushed and PRs opened` and
`| mstpd | PR_OPENED | upstream-metadata/bookworm/mstpd | <commit> | https://github.com/Arrcus/mstpd/pull/<n> | |`.
Then:

```bash
docker compose exec app python scripts/verify_pr_ledger.py bookworm   # FAILURES: none
```

### 7. Review and merge it

On GitHub: one file changed, CI green (description and Jira checks),
reviewers agree with the recorded upstream. Merge it on GitHub - the tool
never merges. ARCoS's merge workflow may then cherry-pick it to other release
branches.

### 8. Run the publisher for the remaining eligible packages

Only after `mstpd` is merged and nothing about it needed changing.

```bash
# Refresh the full plan (not --package) and check it
docker compose exec app apm generate-upstream-md --release bookworm --branch aminor
docker compose exec app python scripts/verify_upstream_md.py bookworm

# Dry run of everything: expect "already done 1" (mstpd) and "to process 20", all DRY_RUN_OK
docker compose exec app apm publish-upstream-md --release bookworm --all

# A first batch of 5
docker compose exec app apm publish-upstream-md --release bookworm --all --limit 5 --apply --confirm-count 5

# After that batch's PRs look right: the rest. N = the "to process" number a new dry run prints
docker compose exec app apm publish-upstream-md --release bookworm --all
docker compose exec app apm publish-upstream-md --release bookworm --all --apply --confirm-count N
```

Add `--title-prefix "BR-NNN: "` to the `--apply` commands if step 7 showed a
Jira id is required, and `--draft` to open drafts.

### 9. Review and merge the generated PRs

```bash
cat out/upstream-md-pr-results-bookworm.md       # every package: status, branch, PR URL
docker compose exec app python scripts/verify_pr_ledger.py bookworm
```

- `PR_OPENED`: review and merge on GitHub, one by one.
- `PR_FAILED` / `PUSH_FAILED` / `ERROR`: fix the cause, then
  `docker compose exec app apm publish-upstream-md --release bookworm --all --retry-failed --apply --confirm-count N`.
- `DRIFT`, `CONFLICT`, `BRANCH_EXISTS`, `PR_CLOSED`: stop for a person - see
  [upstream-md-publishing.md](upstream-md-publishing.md#statuses).

### Later

- The 8 `NEEDS_REVIEW` packages become eligible only after a person resolves
  them (override decision or content-match approval), followed by
  `resolve-release --package <p> --no-discover`, `generate-upstream-md`, a dry
  run and a review.
- Trixie runs through the same commands with `--release trixie` and
  `--branch aminor-trixie`.
- Rotate the token before it expires (`.env`, then `--force-recreate`).
