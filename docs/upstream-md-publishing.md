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

There are two ways to apply:

| Mode | Pushes the branch | Opens the pull request | Needs |
|---|---|---|---|
| `--apply` | yes, with the SSH key | yes, through the GitHub API | SSH push access **and** `APM_GITHUB_TOKEN` |
| `--apply --push-only` | yes, with the SSH key | no - prints a link; a person opens the PR in the browser | SSH push access only |

## Before you start

1. A mapping: `apm resolve-release --release bookworm`.
2. Optionally comparisons, so the pull request body carries the backlog:
   `apm compare --package <p>`. They do not change the file.
3. The files and the plan: `apm generate-upstream-md --release bookworm --branch aminor`.
4. Review them ([upstream-md.md](upstream-md.md#review-checklist)).
5. For `--apply`: the SSH key's account able to push to the package
   repositories, and `APM_GITHUB_TOKEN` configured
   ([authentication.md](authentication.md)). For `--apply --push-only`: the
   push access only.

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

Without a token, add `--push-only` to the same commands - see
[Push-only mode](#push-only-mode-no-token).

### Options

| Option | Meaning |
|---|---|
| `--release` | Debian release (default `bookworm`) |
| `--package P` | Propose this package; repeatable. Exclusive with `--all` |
| `--all` | Every eligible package in the plan |
| `--dry-run` | The default: build and check, push nothing |
| `--apply` | Push branches and open pull requests |
| `--push-only` | With `--apply`: push branches but do not open pull requests; print a GitHub link to open each one. Needs no `APM_GITHUB_TOKEN`. Without `--apply`, the dry run shows the links it would give |
| `--confirm P` | With `--apply`: the package(s) you expect to be pushed; repeatable; must match exactly |
| `--confirm-count N` | With `--apply`: how many packages you expect to be pushed; must match exactly |
| `--draft` | Open the pull requests as drafts. With `--push-only`, the tool cannot: it prints a note that GitHub's form has a **Create draft pull request** option |
| `--title-prefix TEXT` | Prefix for commit and PR titles, e.g. `"BR-123: "` for a Jira id |
| `--limit N` | Process at most N packages |
| `--stop-after-failures N` | Stop after N consecutive failures (default 3; 0 = never) |
| `--retry-failed` | Only packages whose last result was a failure (`PUSH_FAILED`, `PR_FAILED`, `ERROR`) |
| `--recheck` | Also revisit packages that already have a PR - and, with `--push-only`, branches already pushed (`BRANCH_PUSHED`) |
| `--exclude P` | Leave this package out; repeatable |
| `--show-diff` | Print each dry-run diff |
| `--plan PATH` | Plan file (default `out/upstream-md-plan-<release>.json`) |
| `--out-dir DIR` | Output directory (default `out/`) |

### Confirmation rules

The same for `--apply` and `--apply --push-only`: push-only is a real push.

- `--apply --confirm P ...`: the set of names must equal the set this run would
  push, otherwise:
  `--confirm names ... but this run would push .... Nothing was pushed.`
- `--apply` without `--confirm` needs `--confirm-count` equal to the number to
  push, otherwise:
  `This run would push N package(s); confirm with --confirm-count N. Nothing was pushed.`
- Before the first push of an `--apply` run, a pre-flight checks the token is
  set and can see every repository in the run. If not, the run stops and
  nothing is pushed. `--push-only` needs no token, so it skips this
  pre-flight; push access is proven by the first push.

## What happens to each package

1. **Eligibility.** Only `VERIFIED` packages whose verification level proves
   the relationship. Packages in `upstream_md_publish.exclude`
   (`config/settings.yaml`) or `--exclude` are skipped with the reason.
2. **The reviewed bytes.** The file must still hash to the plan's SHA-256 and
   still be exactly what today's mapping renders. Otherwise: `DRIFT`.
3. **Live reads.** The current tip of the target branch; whether the upstream
   ref still points at the commit the mapping recorded (if it moved: `DRIFT`);
   whether `upstream-metadata/<release>/<package>` already exists. A branch
   this tool pushed earlier must still hold the reviewed file (if it holds an
   older one: `DRIFT`). With a token, whether a pull request for the branch was
   already merged or closed (`PR_MERGED`, `PR_CLOSED`) - asked before the file
   is read, so a merged branch that GitHub deleted is not mistaken for
   `NO_CHANGE`.
4. **The existing file.** The tip is fetched (depth 1) into a scratch
   workspace and its `debian/upstream.md` compared: `NO_CHANGE`, `CONFLICT`
   for a file the tool does not recognise as its own
   ([upstream-md.md](upstream-md.md#outcomes)), otherwise `CREATED` or
   `UPDATED`.
5. **The commit.** Built from objects: the tip's tree plus exactly one entry,
   with the tip as parent. Checks: the changed paths are exactly
   `debian/upstream.md`, `git diff --check` is clean, the parent is the tip.
   **A dry run stops here** with `DRY_RUN_OK`.
6. **Push.** `<commit>:refs/heads/upstream-metadata/<release>/<package>`,
   never forced, and verified afterwards with `ls-remote`.
7. **Pull request** against the target branch (or the one already open for
   that branch). With `--push-only` no pull request is opened: the package
   ends as `BRANCH_PUSHED`, with a link to open the PR.
8. **Ledger.** The result is written before the next package starts.

If the target branch moved past the pinned commit, the commit is still built
on the current tip, and the dry run notes
`aminor has moved past the pinned commit <sha>`.

Packages are processed in plan order with `upstream_md_publish.defer`
(`openssl`, `linux` - the largest repositories) last. One package's failure is
recorded and the run continues; `--stop-after-failures` consecutive failures
stop it, because that usually means the token, the key or the network, not
the package.

## Statuses

| Status | Meaning | What to do |
|---|---|---|
| `DRY_RUN_OK` | The commit was built and checked; nothing pushed | Review, then `--apply` |
| `BRANCH_PUSHED` | `--push-only`: the branch was pushed and a PR link generated; no PR was opened | A person opens the link; a later run resumes it (below) |
| `PR_OPENED` | Branch pushed, PR opened | Review and merge on GitHub |
| `PR_EXISTS` | A PR for this branch was already open | Review it |
| `PR_MERGED` | An earlier PR for this branch was merged | Nothing |
| `PR_CLOSED` | An earlier PR was closed without merging | A person decides; it is not re-proposed |
| `NO_CHANGE` | The fork already has this exact file | Nothing |
| `SKIPPED_NO_UPSTREAM` | Nothing to track | Nothing |
| `SKIPPED_NEEDS_REVIEW` | Not `VERIFIED` (or not proven) | Resolve the review reason first |
| `SKIPPED_EXCLUDED` | In `exclude` (settings or `--exclude`) | Nothing, or decide and edit settings |
| `CONFLICT` | The fork has a `debian/upstream.md` the tool does not recognise as its own | Compare by hand; never replaced automatically |
| `DRIFT` | The file, the mapping or the upstream ref changed since the plan - or a branch a push-only run pushed earlier holds an older file | Re-run `resolve-release` (if the upstream moved) and `generate-upstream-md`, review, dry-run again. For an older pushed file: delete that branch on the fork, closing any PR opened from it, then run again |
| `BRANCH_EXISTS` | The branch exists and this tool has no record of creating it | Find out who created it; nothing is overwritten |
| `BASE_UNREADABLE` | The existing file could not be read | Retry; check access |
| `PUSH_FAILED` | The push was rejected | Check the SSH key's write access; `--retry-failed` |
| `PR_FAILED` | Pushed, but the PR could not be opened | Fix the token; `--retry-failed` goes straight to the PR step |
| `ERROR` | Anything else, with the message | Read the error; `--retry-failed` |

`BRANCH_PUSHED` is neither done nor a failure. A later run with a token picks
it up again, to open its pull request; a later `--push-only` run and
`--retry-failed` leave it alone.

## Push-only mode (no token)

For a deployment that can push over SSH but has no GitHub token. The run makes
the same eligibility, drift and conflict checks as `--apply`, builds the same
commit on the same branch and needs the same confirmation, and then stops at
the push. Instead of opening the pull request, it builds GitHub's pre-filled
"open a pull request" link for the branch. A person with access to the
repository opens the link and creates the PR in the browser.

One check differs. Without a token the tool cannot ask GitHub whether a pull
request for the branch was already merged or closed, so push-only relies on the
ledger instead: a package the ledger records as `PR_OPENED`, `PR_EXISTS`,
`PR_MERGED` or `PR_CLOSED` is never pushed by it - not with `--recheck`, not
through the API. If a token is set, push-only still asks GitHub, as `--apply`
does, and an open pull request ends the package as `PR_EXISTS` instead of
handing out a second link.

```bash
# Dry run: pushes nothing; shows, and writes, the link each push would give
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --push-only --show-diff

# Push mstpd's branch. No pull request is opened.
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --apply --push-only --confirm mstpd

# Later, batches of 5. Branches already pushed are not taken again or counted.
docker compose exec app apm publish-upstream-md --release bookworm --all --limit 5 \
    --apply --push-only --confirm-count 5
```

| | Dry run (`--push-only`) | Apply (`--apply --push-only`) |
|---|---|---|
| Banner | `DRY RUN (push only) - nothing will be pushed; showing the links an apply would give` | `APPLY (push only) - branches will be pushed; no pull request will be opened` |
| Status per package | `DRY_RUN_OK` | `BRANCH_PUSHED` |
| PR URL column | the short link (compare page, no title or body) | the same |
| Links file | `out/upstream-md-pr-links-dryrun-<release>.md` | `out/upstream-md-pr-links-<release>.md` |
| Ends with | `Nothing was pushed. No pull request was created.` | the message below |

```
Branches were pushed. No pull request was created. Open each one with its link in out/upstream-md-pr-links-bookworm.md; the person opening it needs access to the repository.
```

If nothing in the run was pushed but branches pushed earlier are still
waiting, it ends with
`No branch was pushed by this run, and no pull request was created. N branch(es) pushed earlier are waiting for a pull request: open each one with its link in <file>; ...`;
if none is waiting,
`No branch is waiting for a pull request. No pull request was created.`
The banner counts waiting branches separately, as
`pushed, waiting for a PR N`. Under each package, as in any dry run, the
output repeats the title, `<repository> <base> @ <sha> -> <branch> @ <commit>`,
and one `review: <CODE> - <detail>` line per review flag.

`NEEDS_REVIEW`, excluded, `CONFLICT`, `DRIFT` and `BRANCH_EXISTS` packages
behave exactly as in a normal run: nothing is pushed for them and they get no
link. A package the ledger records with a pull request keeps that record, as
described above.

`--draft` cannot make a link open a draft. With `--push-only` the run prints
`note: a link cannot open a draft. Choose "Create draft pull request" on GitHub's form instead.`

### The link

```
https://github.com/Arrcus/mstpd/compare/aminor...upstream-metadata/bookworm/mstpd?quick_pull=1&title=...&body=...
```

It opens GitHub's **Open a pull request** form with the base (`aminor`), the
branch, the title and the body already filled in - the same title and body
`--apply` would send through the API, including the three CI sections. The
person opening it:

1. checks **Files changed** shows one file, `debian/upstream.md`;
2. clicks **Create pull request** - or, from that button's menu,
   **Create draft pull request**.

The PR's author is whoever clicks, so it must be someone who can open pull
requests on that repository. Anyone else - including someone not signed in to
GitHub - gets GitHub's 404 page for a private repository.

If the full link would be longer than 8000 characters, the body is left out of
it whole - never cut short - and the form opens with the title only. Paste
the body from the links file, keeping its `Problem Description :`,
`Root Cause :` and `Fix Details :` sections: ARCoS CI checks for them.

### The links file

| File | Written by |
|---|---|
| `out/upstream-md-pr-links-<release>.md` | `--apply --push-only` |
| `out/upstream-md-pr-links-dryrun-<release>.md` | a `--push-only` dry run. Its links work only once an apply has pushed the branches |

Every `--apply --push-only` run rewrites `upstream-md-pr-links-<release>.md`
with **every** package the results ledger has as `BRANCH_PUSHED` - branches
pushed by this run, by earlier runs and through the API alike - so a link
nobody has used yet is never dropped. A package leaves the file once a run with
a token records its pull request. A dry run's file lists the links its pushes
would give and, after them, the branches already waiting.

One section per package: repository, base branch, branch, commit,
`Pushed: yes - waiting for a pull request` (or, in a dry run,
`Pushed: no - the link works once an apply pushes the branch`), the full link
(with `The description is too long for the link: paste it from below.` when
the body was left out), then the title and the description, each in a fenced
block for copy and paste. The description is the one the link fills in; when
the link could not hold it, it is rebuilt from the current plan. The summary
at the end of the run prints the file's path (`links   <path>`).

### Resuming after `BRANCH_PUSHED`

The ledger records the pushed commit, and a later result that pushed nothing -
`DRIFT`, an exclusion, an error, a package no longer eligible - never replaces
that record (see [Ledgers and re-running](#ledgers-and-re-running)). A branch
whose tip is still that commit is therefore recognised as this tool's: it is
never pushed twice and never reported as `BRANCH_EXISTS`.

| Next run | Result |
|---|---|
| `--push-only` again (dry run or apply) | The package is kept, not processed: nothing pushed, nothing to confirm, and its link stays in the links file - even if the reviewed file changed since (see below). With `--recheck`: `BRANCH_PUSHED` (or `DRY_RUN_OK`) with the link again |
| `--apply` with a token | Nothing pushed; the PR is opened on the existing branch (`PR_OPENED`), or the one opened from the link is found (`PR_EXISTS`); a merged one is `PR_MERGED`, and a closed one `PR_CLOSED`. Once GitHub has deleted the branch after a merge, the target already has the file: `NO_CHANGE`, and the link leaves the links file |
| `--apply` with a token, after the upstream branch moved | `DRIFT`: the description's upstream commit and counts are out of date. The ledger keeps `BRANCH_PUSHED` and the link. Re-run `resolve-release` and `generate-upstream-md` (the file comes out the same), review, run again |
| `--apply` with a token, or `--apply --push-only --recheck`, after the reviewed file changed | `DRIFT`: the pushed branch holds the older file, and nothing is overwritten. The ledger records the `DRIFT` with the pushed commit, and the link leaves the links file. Every later run stops there too. Delete the branch on the fork, closing any PR opened from it, then run again: it is pushed with the reviewed file |

A plain `--push-only` re-run does not look at a waiting branch, so it does not
notice a regenerated file: the links file keeps the old link, whose
description matches the older file the branch still holds. After regenerating
the plan, check the waiting branches before anyone opens a link. A
`--push-only --recheck` dry run reports each one that holds an older file as
`DRIFT`, and touches neither the results ledger nor the links file.
`--apply --push-only --recheck --package <name> --confirm <name>` then records
that `DRIFT` and takes the link out of the links file. Without `--package`,
`--recheck` also processes - and pushes - every other eligible package.

A token run counts a `BRANCH_PUSHED` package in "to process" and in
`--confirm-count`, because it has work to do for it. A push-only run does not:
`--limit N` moves on to packages not pushed yet.

Without a token the tool cannot see whether anyone used the link, so the
ledger keeps saying `BRANCH_PUSHED` until a run with a token looks. Nor can it
see a pull request a reviewer closed. A package the ledger records as
`PR_CLOSED` is safe. But a `BRANCH_PUSHED` package whose PR was closed and
whose branch was then deleted looks, without a token, exactly like a branch
deleted to be pushed again: `--push-only --recheck`, or a push-only API
request, pushes it again and hands out a new link. Use them only for a branch
you deleted on purpose, or run with a token first so the closed PR is
recorded. A merged PR whose branch was deleted is safe either way: the target
already has the file, so the result is `NO_CHANGE`.

## The pull request

- **Title**: `<prefix><package>: add debian/upstream.md with verified upstream`
  (`update` instead of `add` for an `UPDATED` file).
- **Body** starts with the three sections ARCoS CI requires, spelled exactly
  `Problem Description :`, `Root Cause :` and `Fix Details :`, each with text
  after it. `Fix Details :` says the file records the upstream source and the
  current upstream branch. Then:
  - the file's content, in a fenced block;
  - a table: `Upstream Source` and `Current Upstream Branch` as written, then
    the evidence - ARCoS commit and branch, Debian release, upstream
    repository and ref with commit, status / method / confidence,
    verification level, origin kind, ref selection, base tag, merge base, raw
    counts, and the comparison backlog when there is one;
  - when the package has review flags, `Review notes :` with each code and
    its detail ([upstream-md.md](upstream-md.md#review-flags)).
- Opened by the token's owner (or, with `--push-only`, by whoever opens the
  link), from a branch pushed with the SSH key; the commit's author is
  `APM_COMMITTER_NAME <APM_COMMITTER_EMAIL>`.
- `--draft` opens drafts; `--title-prefix "BR-123: "` adds a Jira id if the
  repository's Jira check requires one.

The tool never merges. Review and merge happen on GitHub.

## Ledgers and re-running

| File | Written by |
|---|---|
| `out/upstream-md-pr-dryrun-<release>.{json,md}` | dry runs - never overwrites the real record |
| `out/upstream-md-pr-results-<release>.{json,md}` | `--apply` runs, with or without `--push-only`: package, status, branch, commit, PR URL (or PR link), error |

For a `BRANCH_PUSHED` package, the Markdown ledger's PR URL column shows a
short link (the compare URL with `?quick_pull=1`, without title and body); the
JSON keeps the full link as `pr_create_url`. PR bodies are never stored in the
ledger - they are in the links file.

The results ledger is rewritten atomically after every package and is the
record of which branches and PRs exist. Keep it; never edit or delete it.
Re-running resumes from it:

- packages with `PR_OPENED`, `PR_EXISTS` or `PR_MERGED` are left alone (use
  `--recheck` to revisit); a `--push-only` run also leaves `BRANCH_PUSHED` and
  `PR_CLOSED` alone;
- a branch this tool pushed but could not open a PR for (`PR_FAILED`, or
  `BRANCH_PUSHED` from a push-only run) goes straight to the PR step in a run
  with a token;
- `--retry-failed` re-runs only failures;
- a branch it has no record of is `BRANCH_EXISTS`, and a closed PR is
  `PR_CLOSED` - neither is touched.

The record of a branch a push-only run pushed (`BRANCH_PUSHED`) is never
replaced by a result that only says what one run did: it gives way only to a
pull request answer, a result that carries a pushed commit, or `NO_CHANGE`.
The run's own output and summary still show what happened - `DRIFT`,
`SKIPPED_EXCLUDED`, `ERROR` - but the ledger keeps the commit, so the next run
still recognises the branch. Every other record is replaced by the package's
next result, as before push-only existed - except that a package no longer
eligible in the plan never replaces an open or merged pull request's record.

Check the ledger's invariants after a run:

```bash
docker compose exec app python scripts/verify_pr_ledger.py bookworm
```

It checks that every branch is under `upstream-metadata/`, none equals its
target, every opened PR has a pushed commit and a URL, every pushed entry names
a full commit SHA, and every `BRANCH_PUSHED` entry was pushed, has a full
40-character commit and a `pr_create_url` starting with
`https://github.com/<repository>/compare/<base>...<branch>`, and records no
pull request.

## What it never does

Write to the target branch, force-push, merge, commit a second file, replace a
file it does not recognise as its own, propose a `NEEDS_REVIEW`, `NO_UPSTREAM`
or `FAILED` package, push without `--apply` and a matching confirmation, or
open a pull request in `--push-only` mode.

## From the dashboard or the API

After **Generate**, the dashboard's **debian/upstream.md** card shows the file,
its outcome and its review flags, and - unless the outcome is `NO_CHANGE` -
these buttons, all through the same publisher as the CLI (which still refuses
a `CONFLICT`, an exclusion and anything not `VERIFIED`):

| Button | Shown | What it does |
|---|---|---|
| **Dry run** | always | `dry_run: true` (and `push_only: true` without a token): every read and check, then stops. Shows the repository, target branch, branch, base commit and title. Nothing is pushed |
| **Create pull request** | with a token | `confirm: true` - **a real push and PR, at once** |
| **Push branch (no PR)** | without a token, after a `DRY_RUN_OK` dry run | `confirm: true, push_only: true`: pushes the branch over SSH and shows **Open the pull request on GitHub**, the pre-filled form a person with access opens it from. No PR is created |

The dashboard works on the live resolution, not on the reviewed plan, and
does not write the links file. For a batch, and for the reviewed-plan
guarantee, use the CLI.

```bash
# dry run over the API: nothing is pushed
curl -s -X POST http://<server-ip>:8080/api/upstream-md/pr -H 'Content-Type: application/json' \
  -d '{"package": "mstpd", "release": "bookworm", "dry_run": true}'

# push-only over the API: pushes the branch, opens no PR, needs no token
curl -s -X POST http://<server-ip>:8080/api/upstream-md/pr -H 'Content-Type: application/json' \
  -d '{"package": "mstpd", "release": "bookworm", "confirm": true, "push_only": true}'

# the results ledger
curl -s "http://<server-ip>:8080/api/upstream-md/prs?release=bookworm"
```

The API renders from the current mapping rather than from the reviewed plan
file, and refuses anything not `VERIFIED` (HTTP 400). Without `confirm: true`
or `dry_run: true` it refuses. A push-only result has status `BRANCH_PUSHED`
and the link in `pr_create_url`; the API writes no links file, but the next
CLI push-only apply lists the branch in it. A push-only request for a package
the ledger records with a pull request returns that record unchanged and
pushes nothing.
