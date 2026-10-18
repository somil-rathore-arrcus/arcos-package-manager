# Production handoff

## Current state (2026-10-06)

| | |
|---|---|
| Host | Lakshya's machine |
| Checkout | `/home/lakshya/somil/arcos-package-manager`, branch `main` |
| Runtime | Docker Compose, service `app`, container `arcos-package-manager-app-1`, healthy |
| Dashboard | `http://<server-ip>:8080/` |
| Git access | `APM_GIT_BACKEND=local` with the host's own SSH key, mounted read-only; authenticates to GitHub as `lakshya-arrcus`; read and push (dry-run) verified on all 52 Arrcus repositories in use |
| GitHub token | **Not set** - the tool cannot open a pull request itself |
| Commit author | `APM_COMMITTER_NAME` / `APM_COMMITTER_EMAIL` in `.env` (Somil Rathore) |
| Data | `out/` in the checkout; clones in `/var/tmp/arcos-package-manager`; cache in volume `apm-cache` |
| Tests | backend 438 passed, frontend 34 passed (in Docker) |

### Since then (not yet deployed on the host)

- `debian/upstream.md` holds only `Upstream Source` and
  `Current Upstream Branch` ([upstream-md.md](upstream-md.md)). The plan and
  the files in the host's `out/` are in the old format: until they are
  regenerated, `verify_upstream_md.py` fails and the publisher reports
  `DRIFT`.
- `publish-upstream-md --push-only` pushes the branches with the SSH key and
  prints a link per package for a person to open the pull request. No token is
  needed ([upstream-md-publishing.md](upstream-md-publishing.md#push-only-mode-no-token)).
- The five `CURATED_CONFLICT` packages are in `upstream_md_publish.exclude`,
  so they stay out even if a later resolution marks them `VERIFIED`.

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

### What the files will say

From the host's mapping. Confirm each one in the regenerated plan
(`out/upstream-md-plan-bookworm.md`, **Flagged for review**).

| `Current Upstream Branch` | Packages | Flags expected (at least) |
|---|---|---|
| `master` or `main` | `cre2`, `hsflowd`, `ifupdown2`, `libdict`, `mstpd`, `oom`, `python-netfilterqueue`, `rtrlib`, `tacacs_plus`, `ydk-cpp`, `zenoh`, `zenoh-c` | `ifupdown2`: `FALLBACK_REF` - `master` is a fallback |
| Another branch | `linux` → `linux-6.1.y`, `openssl` → `openssl-3.0`, `libyang1` → `libyang1` | `linux`, `openssl`: `MAPPING_WARNINGS` (a large gap to Debian's release) |
| `N/A - no upstream branch verified; upstream tag <tag>` | `ethtool` `v6.1`, `iproute2` `v6.1.0`, `iputils` `20221126`, `libnl3` `libnl3_7_0`, `pyrad` `2.1`, `libpam-radius-auth` `release_2_0_0` | `BRANCH_NOT_VERIFIED`, `TAG_ONLY` |
| ... followed by ` (the ARCoS fork does not contain this tag)` | `libpam-radius-auth` | also `BASE_TAG_NOT_IN_FORK` |

For the six tag-only packages the file names the tag and claims no branch.
That is the recorded fact; the tool does not guess a branch.

### Waiting for a person (never published until resolved)

| Package | Reason | Decision needed |
|---|---|---|
| `liburcu`, `lldpd`, `lttng-tools`, `lttng-ust`, `net-snmp` | `CURATED_CONFLICT`; also in `upstream_md_publish.exclude` | The curated GitHub repository shares no history; the Debian packaging repository on salsa, which the fork came from, does. Which should the file name? |
| `babeltrace`, `ctypesgen`, `rsyslog` | `NO_SHARED_HISTORY`, `CONTENT_MATCH_UNAPPROVED` | Tarball imports. Approve the content match (`apm approve-content-base`) if it is right |

Before the batch, also decide:

- `ifupdown2` (`FALLBACK_REF`) and `libpam-radius-auth`
  (`BASE_TAG_NOT_IN_FORK`): in this batch, or held back with `--exclude`?
- Whether a Jira id is needed in the titles (`--title-prefix "BR-NNN: "`); the
  `mstpd` PR will show it.

`openssl` and `linux` are eligible, but their plausibility warnings deserve a
careful review of their PRs.

## Who does what

| Role | Does |
|---|---|
| Lakshya | Opens each pull request from its link (push-only); his account can see the package repositories. Or creates the GitHub token, enters it in `.env` on the server and recreates the container - the token never leaves the server |
| Operator | Runs the dry runs and the `--apply` commands, watches the ledger and the links file |
| Package reviewers | Review and merge each PR on GitHub |
| Maintainer of this tool | Changes code and configuration through PRs to `arcos-package-manager` |

## Handoff checklist

```bash
cd /home/lakshya/somil/arcos-package-manager
git status                     # clean, on main
docker compose ps              # (healthy)
docker compose exec app apm doctor --repo Arrcus/mstpd   # FAILURES: none (token: WARN until set)
docker compose exec app python scripts/verify_output.py
docker compose exec app python scripts/verify_upstream_md.py bookworm   # fails until regenerated
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
| Title | `mstpd: add debian/upstream.md with verified upstream` |

The file:

```text
Upstream Source: https://github.com/mstpd/mstpd.git

Current Upstream Branch: master
```

The first PR is successful when:

- the PR's **Files changed** shows exactly one new file, `debian/upstream.md`,
  with those two lines;
- the ARCoS CI description check passes (the body has `Problem Description :`,
  `Root Cause :`, `Fix Details :`);
- the Jira check passes. If it wants a Jira id, decide it before the batch:
  `--title-prefix "BR-NNN: "` puts it in front of both the commit title and
  the PR title of every later package. A branch already pushed keeps its
  commit title;
- reviewers agree with the recorded upstream, and it is merged.

If anything about it is wrong, stop: do not run the other packages until the
cause is understood.

## Publishing the remaining packages safely

- Only `VERIFIED` packages are ever proposed; the code enforces it.
- Dry-run the whole set first; every eligible package must say `DRY_RUN_OK`.
- Publish in small batches, and check each batch's PRs and CI before the next.
- `--confirm` / `--confirm-count` must equal what the dry run says it will
  push - a mismatch pushes nothing. This holds for `--push-only` too.
- With `--push-only`, `--all --limit N` moves on batch by batch: a push-only
  run leaves `BRANCH_PUSHED` packages alone and does not count them. A run
  with a token does count them - it opens their pull requests.
- `openssl` and `linux` are processed last by design.
- Three consecutive failures stop the run; fix the cause before re-running.
- Never delete `out/upstream-md-pr-results-bookworm.json` - it is the record
  of what exists, and what lets a later run recognise a pushed branch as its
  own.

---

## What happens next

Everything up to here is done: the deployment runs and the mapping is
verified. The steps below are what remains, on the push-only route: the host
pushes each branch with its SSH key, and Lakshya opens each pull request from
its link. No token is needed. (With a token, see
[the token route](#alternative-the-token-route) at the end.)

Run them on the server, in the checkout directory:

```bash
cd /home/lakshya/somil/arcos-package-manager
```

### 1. Deploy the new version

After the change is merged to `main`:

```bash
git pull --ff-only
docker compose up -d --build
docker compose ps                    # (healthy)
```

### 2. Regenerate the files and the plan

The files in `out/` are in the old format; generate them again (the full
plan, not `--package`):

```bash
docker compose exec app apm generate-upstream-md --release bookworm --branch aminor
docker compose exec app python scripts/verify_upstream_md.py bookworm   # FAILURES: none
```

The output ends with `Flagged for review (N) - check before merging; a flag does not block publishing:`
and one line of codes per flagged package.

If the mapping is old, refresh it first
(`apm resolve-release --release bookworm`), then generate.

### 3. Review the plan

```bash
cat out/upstream-md-plan-bookworm.md
cat out/upstream-md/mstpd/debian/upstream.md
```

Check the table (Upstream Source, Current Upstream Branch, Flags) and the
**Flagged for review** section against
[What the files will say](#what-the-files-will-say), using the
[review checklist](upstream-md.md#review-checklist). The `mstpd` row:
`Arrcus/mstpd`, `aminor`, `upstream-metadata/bookworm/mstpd`, `CREATED`,
`https://github.com/mstpd/mstpd.git`, `master`, no flags.

### 4. Dry-run `mstpd` in push-only mode

```bash
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --push-only --show-diff
```

Expect `DRY RUN (push only) - nothing will be pushed; showing the links an apply would give`,
a row
`| mstpd | DRY_RUN_OK | upstream-metadata/bookworm/mstpd | <commit> | https://github.com/Arrcus/mstpd/compare/aminor...upstream-metadata/bookworm/mstpd?quick_pull=1 | |`,
the line `Arrcus/mstpd aminor @ <sha> -> upstream-metadata/bookworm/mstpd @ <commit>`,
the diff (one new file, the two lines), and
`Nothing was pushed. No pull request was created.`

If it reports `DRIFT` (the mapping or the upstream changed since the plan),
refresh and generate again, then repeat this step:

```bash
docker compose exec app apm resolve-release --release bookworm --package mstpd --no-discover
docker compose exec app apm generate-upstream-md --release bookworm --branch aminor
```

### 5. Review the title and the PR body

```bash
cat out/upstream-md-pr-links-dryrun-bookworm.md
```

The `mstpd` section has the title and the description that the link will
fill in: the three CI sections, the file in a fenced block, then the table -
`Upstream Source` `https://github.com/mstpd/mstpd.git`,
`Current Upstream Branch` `master`, status `VERIFIED`, verification level
`SHARED_HISTORY`. Decide now whether a Jira id is needed
(`--title-prefix "BR-NNN: "`); add it to the next command if so.

### 6. Push the `mstpd` branch

```bash
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --apply --push-only --confirm mstpd
```

Expect `APPLY (push only) - branches will be pushed; no pull request will be opened`,
a row `| mstpd | BRANCH_PUSHED | upstream-metadata/bookworm/mstpd | <commit> | https://github.com/Arrcus/mstpd/compare/aminor...upstream-metadata/bookworm/mstpd?quick_pull=1 | |`,
and `Branches were pushed. No pull request was created. Open each one with its link in .../upstream-md-pr-links-bookworm.md; ...`.
Then:

```bash
docker compose exec app python scripts/verify_pr_ledger.py bookworm   # FAILURES: none
```

A `PUSH_FAILED` here means the SSH key's account cannot push to
`Arrcus/mstpd`; nothing else was changed.

### 7. Lakshya opens the pull request

```bash
cat out/upstream-md-pr-links-bookworm.md
```

Lakshya, signed in to GitHub, opens the `mstpd` **Link**. GitHub's
**Open a pull request** form appears with base `aminor`, the branch, the title
and the description filled in. Check **Files changed** shows one file, then
click **Create pull request** (or **Create draft pull request** from the
button's menu). If the form has no description, paste it from the links file.

### 8. Review and merge it

On GitHub: one file changed, CI green (description and Jira checks),
reviewers agree with the recorded upstream. Merge it on GitHub - the tool
never merges. ARCoS's merge workflow may then cherry-pick it to other release
branches.

Without a token the tool cannot see the PR, so the ledger keeps `mstpd` as
`BRANCH_PUSHED`. That is expected. A later run with a token records it
(`PR_EXISTS` or `PR_MERGED`; `NO_CHANGE` once GitHub has deleted the branch
after the merge, because `aminor` then has the file) without pushing again. If
upstream `master` gained a commit in the meantime, that run reports `DRIFT`
instead and the ledger keeps `BRANCH_PUSHED`: re-run `resolve-release` and
`generate-upstream-md` (the file comes out the same), review, and run again.

### 9. The remaining eligible packages

Only after `mstpd` is merged and nothing about it needed changing.

```bash
# Dry run of everything else: all DRY_RUN_OK. mstpd is not processed: a
# push-only run leaves a BRANCH_PUSHED package alone ("pushed, waiting for a PR 1").
docker compose exec app apm publish-upstream-md --release bookworm --all --push-only

# A first batch of 5. --confirm-count must equal the batch; mstpd is not counted.
docker compose exec app apm publish-upstream-md --release bookworm --all --limit 5 \
  --push-only --apply --confirm-count 5
```

Lakshya opens each link in `out/upstream-md-pr-links-bookworm.md`, which lists
every branch still waiting for a pull request, earlier batches included. After
that batch's PRs and CI look right, run the same command again: the next five
are the next packages not pushed yet. Add `--title-prefix "BR-NNN: "` if step 8 showed a Jira id is
required, and `--exclude` for any package held back
([Waiting for a person](#waiting-for-a-person-never-published-until-resolved)).

### 10. Check the result

```bash
cat out/upstream-md-pr-results-bookworm.md       # every package: status, branch, link
docker compose exec app python scripts/verify_pr_ledger.py bookworm
```

- `BRANCH_PUSHED`: open its link (if not done yet), review and merge on
  GitHub.
- `PUSH_FAILED` / `ERROR`: fix the cause, then
  `docker compose exec app apm publish-upstream-md --release bookworm --all --retry-failed --push-only --apply --confirm-count N`.
- `DRIFT`, `CONFLICT`, `BRANCH_EXISTS`, `PR_CLOSED`: stop for a person - see
  [upstream-md-publishing.md](upstream-md-publishing.md#statuses).

### Alternative: the token route

If Lakshya creates a token instead, the tool opens the PRs itself:

1. On GitHub, as the account that should open the PRs: a fine-grained token -
   resource owner **Arrcus**, repository access to the ARCoS package
   repositories, permissions **Pull requests: Read and write** and
   **Metadata: Read**, with an expiry date. If Arrcus enforces SAML SSO,
   authorise it.
2. On the server, with an editor - never `echo`, `export`, chat or a ticket:
   `nano .env` (set `APM_GITHUB_TOKEN=<the token>`), `chmod 600 .env`, then
   `docker compose up -d --force-recreate` (`restart` is not enough).
3. `docker compose exec app apm doctor --repo Arrcus/mstpd` - expect
   `PASS  APM_GITHUB_TOKEN is set`, `PASS  token can see Arrcus/mstpd` and
   `FAILURES: none`.
4. The steps above without `--push-only`: the dry run, then
   `apm publish-upstream-md --release bookworm --package mstpd --apply --confirm mstpd`,
   which ends `PR_OPENED` with `https://github.com/Arrcus/mstpd/pull/<n>`.
   Batches can then use `--all --limit 5 --apply --confirm-count 5`.

A package already `BRANCH_PUSHED` from the push-only route is picked up by
the first token run, and counted in its `--confirm-count`: its branch is not
pushed again, and its PR is opened, or found (`PR_EXISTS`, `PR_MERGED`,
`PR_CLOSED`; `NO_CHANGE` once GitHub has deleted a merged branch). If its
upstream branch moved while it waited, that run reports `DRIFT` and the ledger
keeps `BRANCH_PUSHED`; re-run `resolve-release` and `generate-upstream-md`,
review, and run again.

### Later

- The 8 `NEEDS_REVIEW` packages become eligible only after a person resolves
  them (override decision or content-match approval), followed by
  `resolve-release --package <p> --no-discover`, `generate-upstream-md`, a dry
  run and a review. The five `CURATED_CONFLICT` ones must also be removed from
  `upstream_md_publish.exclude` ([configuration.md](configuration.md#upstream_md_publishexclude)).
- Trixie runs through the same commands with `--release trixie` and
  `--branch aminor-trixie`.
- With a token: rotate it before it expires (`.env`, then `--force-recreate`).
