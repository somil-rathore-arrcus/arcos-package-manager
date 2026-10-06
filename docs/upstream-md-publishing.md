# Publishing `debian/upstream.md` as pull requests

`apm publish-upstream-md` proposes each reviewed file as its own pull request
to the **ARCoS package repository** - never to the upstream project:

| | Example: `mstpd`, Bookworm |
|---|---|
| Repository | `Arrcus/mstpd` |
| New branch | `upstream-metadata/bookworm/mstpd` |
| Target (base) branch | `aminor` |
| Change | one file: `debian/upstream.md` |
| Commit title | `mstpd: add debian/upstream.md with verified upstream` |

It is a **dry run unless `--apply` is given**, and `--apply` also needs a
confirmation naming what will be pushed.

## Before you start

1. A mapping: `apm resolve-release --release bookworm`.
2. Optionally comparisons, so files carry the Backlog: `apm compare --package <p>`.
3. The files and the plan: `apm generate-upstream-md --release bookworm --branch aminor`.
4. Review them ([upstream-md.md](upstream-md.md#review-checklist)).
5. For `--apply` only: `APM_GITHUB_TOKEN` configured, and the SSH key's account
   able to push to the package repositories ([authentication.md](authentication.md)).

## Commands

```bash
# Dry run: every read, and the exact commit, but no push. The default.
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --show-diff

# One package for real. --confirm must name exactly what this run would push.
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --apply --confirm mstpd

# Dry run of every eligible package
docker compose exec app apm publish-upstream-md --release bookworm --all

# A first batch of 5, for real. --confirm-count must equal the number to push.
docker compose exec app apm publish-upstream-md --release bookworm --all --limit 5 --apply --confirm-count 5

# The rest. N is the "to process" figure a dry run prints.
docker compose exec app apm publish-upstream-md --release bookworm --all --apply --confirm-count N
```

### Options

| Option | Meaning |
|---|---|
| `--release` | Debian release (default `bookworm`) |
| `--package P` | Propose this package; repeatable. Exclusive with `--all` |
| `--all` | Every eligible package in the plan |
| `--dry-run` | The default: build and check, push nothing |
| `--apply` | Push branches and open pull requests |
| `--confirm P` | With `--apply`: the package(s) you expect to be pushed; repeatable; must match exactly |
| `--confirm-count N` | With `--apply`: how many packages you expect to be pushed; must match exactly |
| `--draft` | Open the pull requests as drafts |
| `--title-prefix TEXT` | Prefix for commit and PR titles, e.g. `"BR-123: "` for a Jira id |
| `--limit N` | Process at most N packages |
| `--stop-after-failures N` | Stop after N consecutive failures (default 3; 0 = never) |
| `--retry-failed` | Only packages whose last result was a failure (`PUSH_FAILED`, `PR_FAILED`, `ERROR`) |
| `--recheck` | Also revisit packages that already have a PR |
| `--exclude P` | Leave this package out; repeatable |
| `--show-diff` | Print each dry-run diff |
| `--plan PATH` | Plan file (default `out/upstream-md-plan-<release>.json`) |
| `--out-dir DIR` | Output directory (default `out/`) |

### Confirmation rules

- `--apply --confirm P ...`: the set of names must equal the set this run would
  push, otherwise:
  `--confirm names ... but this run would push .... Nothing was pushed.`
- `--apply` without `--confirm` needs `--confirm-count` equal to the number to
  push, otherwise:
  `This run would push N package(s); confirm with --confirm-count N. Nothing was pushed.`
- Before the first push, a pre-flight checks the token is set and can see every
  repository in the run. If not, the run stops and nothing is pushed.

## What happens to each package

1. **Eligibility.** Only `VERIFIED` packages whose verification level proves
   the relationship. Packages in `upstream_md_publish.exclude`
   (`config/settings.yaml`) or `--exclude` are skipped with the reason.
2. **The reviewed bytes.** The file must still hash to the plan's SHA-256 and
   still be exactly what today's mapping renders. Otherwise: `DRIFT`.
3. **Live reads.** The current tip of the target branch; whether the upstream
   ref still points at the commit the file records (if it moved: `DRIFT`);
   whether `upstream-metadata/<release>/<package>` already exists.
4. **The existing file.** The tip is fetched (depth 1) into a scratch
   workspace and its `debian/upstream.md` compared: `NO_CHANGE`, `CONFLICT`
   for a file this tool did not write, otherwise `CREATED` or `UPDATED`.
5. **The commit.** Built from objects: the tip's tree plus exactly one entry,
   with the tip as parent. Checks: the changed paths are exactly
   `debian/upstream.md`, `git diff --check` is clean, the parent is the tip.
   **A dry run stops here** with `DRY_RUN_OK`.
6. **Push.** `<commit>:refs/heads/upstream-metadata/<release>/<package>`,
   never forced, and verified afterwards with `ls-remote`.
7. **Pull request** against the target branch (or the one already open for
   that branch).
8. **Ledger.** The result is written before the next package starts.

If the target branch moved past the pinned commit, the commit is still built
on the current tip, and the dry run notes
`aminor has moved past the pinned commit <sha>`.

Packages are processed in plan order with `upstream_md_publish.defer`
(`openssl`, `linux` - the largest repositories) last. One package's failure is
recorded and the run continues; `--stop-after-failures` consecutive failures
stop it, because that usually means the token or the network, not the package.

## Statuses

| Status | Meaning | What to do |
|---|---|---|
| `DRY_RUN_OK` | The commit was built and checked; nothing pushed | Review, then `--apply` |
| `PR_OPENED` | Branch pushed, PR opened | Review and merge on GitHub |
| `PR_EXISTS` | A PR for this branch was already open | Review it |
| `PR_MERGED` | An earlier PR for this branch was merged | Nothing |
| `PR_CLOSED` | An earlier PR was closed without merging | A person decides; it is not re-proposed |
| `NO_CHANGE` | The fork already has this exact file | Nothing |
| `SKIPPED_NO_UPSTREAM` | Nothing to track | Nothing |
| `SKIPPED_NEEDS_REVIEW` | Not `VERIFIED` (or not proven) | Resolve the review reason first |
| `SKIPPED_EXCLUDED` | In `exclude` (settings or `--exclude`) | Nothing, or decide and edit settings |
| `CONFLICT` | The fork has a hand-written `debian/upstream.md` | Compare by hand; never replaced automatically |
| `DRIFT` | The file, the mapping or the upstream ref changed since the plan | Re-run `resolve-release` (if the upstream moved) and `generate-upstream-md`, review, dry-run again |
| `BRANCH_EXISTS` | The branch exists and this tool has no record of creating it | Find out who created it; nothing is overwritten |
| `BASE_UNREADABLE` | The existing file could not be read | Retry; check access |
| `PUSH_FAILED` | The push was rejected | Check the SSH key's write access; `--retry-failed` |
| `PR_FAILED` | Pushed, but the PR could not be opened | Fix the token; `--retry-failed` goes straight to the PR step |
| `ERROR` | Anything else, with the message | Read the error; `--retry-failed` |

## The pull request

- **Title**: `<prefix><package>: add debian/upstream.md with verified upstream`
  (`update` instead of `add` for an `UPDATED` file).
- **Body** starts with the three sections ARCoS CI requires, spelled exactly:
  `Problem Description :`, `Root Cause :`, `Fix Details :`; then a table: ARCoS
  commit and branch, Debian release, upstream repository and ref with commit,
  status / method / confidence, verification level, origin kind, ref selection,
  base tag, merge base, raw counts, the comparison backlog when there is one,
  and any warnings.
- Opened by the token's owner, from a branch pushed with the SSH key; the
  commit's author is `APM_COMMITTER_NAME <APM_COMMITTER_EMAIL>`.
- `--draft` opens drafts; `--title-prefix "BR-123: "` adds a Jira id if the
  repository's Jira check requires one.

The tool never merges. Review and merge happen on GitHub.

## Ledgers and re-running

| File | Written by |
|---|---|
| `out/upstream-md-pr-dryrun-<release>.{json,md}` | dry runs - never overwrites the real record |
| `out/upstream-md-pr-results-<release>.{json,md}` | `--apply` runs: package, status, branch, commit, PR URL, error |

The results ledger is rewritten atomically after every package and is the
record of which branches and PRs exist. Keep it; never edit or delete it.
Re-running resumes from it:

- packages with `PR_OPENED`, `PR_EXISTS` or `PR_MERGED` are left alone (use
  `--recheck` to revisit);
- a branch this tool pushed but could not open a PR for goes straight to the
  PR step;
- `--retry-failed` re-runs only failures;
- a branch it has no record of is `BRANCH_EXISTS`, and a closed PR is
  `PR_CLOSED` - neither is touched.

Check the ledger's invariants after a run (every branch under
`upstream-metadata/`, none equal to its target, every opened PR has a pushed
commit and a URL):

```bash
docker compose exec app python scripts/verify_pr_ledger.py bookworm
```

## What it never does

Write to the target branch, force-push, merge, commit a second file, replace a
hand-written file, propose a `NEEDS_REVIEW`, `NO_UPSTREAM` or `FAILED` package,
or push without `--apply` and a matching confirmation.

## From the dashboard or the API

After **Generate**, the dashboard's **debian/upstream.md** card shows
**Create pull request** unless the outcome is `NO_CHANGE`; the button is
enabled only when a token is configured. It calls `POST /api/upstream-md/pr`
with `confirm: true` - **a real push and PR, at once**, through the same
publisher (which still refuses a `CONFLICT`). The CLI path, with its dry run
and confirmation, is the recommended way.

```bash
# dry run over the API: nothing is pushed
curl -s -X POST http://<server-ip>:8080/api/upstream-md/pr -H 'Content-Type: application/json' \
  -d '{"package": "mstpd", "release": "bookworm", "dry_run": true}'

# the results ledger
curl -s "http://<server-ip>:8080/api/upstream-md/prs?release=bookworm"
```

The API renders from the current mapping rather than from the reviewed plan
file, and refuses anything not `VERIFIED` (HTTP 400). Without `confirm: true`
or `dry_run: true` it refuses.
