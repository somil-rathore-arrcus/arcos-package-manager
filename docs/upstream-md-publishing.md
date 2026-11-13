# Publishing `debian/upstream.md`

`apm publish-upstream-md` pushes each reviewed file on its own branch to the
**ARCoS package repository** - never to the upstream project - and stops
there:

| | Example: `mstpd`, Bookworm |
|---|---|
| Repository | `Arrcus/mstpd` |
| New branch | `debian-upstream-tracking` ([Branch name](#branch-name)) |
| Target (base) branch | `aminor` |
| Change | one file: `debian/upstream.md` |
| Commit title | `mstpd: add debian/upstream.md with verified upstream` |
| Pull request | opened by a person, from the branch's Compare Link - never by the tool |

**The tool never creates, opens, submits or merges a pull request** - from the
CLI, the dashboard or the API, with or without a GitHub token. Every pushed
branch comes with its Compare Link and a pre-filled GitHub "open a pull
request" link; a person with access to the repository opens the pull request
from there.

It is a **dry run unless `--apply` is given**, and `--apply` also needs a
confirmation naming what will be pushed.

| | |
|---|---|
| Pushes the branch | yes, with the SSH key, never forced |
| Opens the pull request | no - prints its links; a person opens it in the browser |
| Needs | SSH push access, a machine where pushing is allowed (`APM_ALLOW_PUSH`), and the confirmation. No GitHub token |

Pushing is allowed only where `APM_ALLOW_PUSH` is set - the Docker Compose
deployment on Lakshya's machine. Anywhere else an apply stops with
`PUSH_DISABLED` before anything is pushed; dry runs work everywhere.
`--push-only` is still accepted, for older command lines, and changes nothing:
every apply is push-only. `--draft` no longer exists.

## Before you start

1. A mapping: `apm resolve-release --release bookworm`.
2. Optionally comparisons, so the pull request body carries the backlog:
   `apm compare --package <p>`. They do not change the file.
3. The files and the plan: `apm generate-upstream-md --release bookworm --branch aminor`.
   The files go to `out/upstream-md-<release>/` - one folder per release.
4. Review them ([upstream-md.md](upstream-md.md#review-checklist)).
5. For `--apply`: the machine where `APM_ALLOW_PUSH` is set, and the SSH key's
   account able to push to the package repositories
   ([authentication.md](authentication.md)). No token is needed.

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

# The rest. N is the "to process" figure a dry run prints, less any it says
# another release's branch covers.
docker compose exec app apm publish-upstream-md --release bookworm --all --apply --confirm-count N
```

Every apply pushes and stops; see [The links](#the-links-a-person-opens-the-pull-request).

### Options

| Option | Meaning |
|---|---|
| `--release` | Debian release (default `bookworm`) |
| `--package P` | Propose this package; repeatable. Exclusive with `--all` |
| `--all` | Every eligible package in the plan |
| `--dry-run` | The default: build and check, push nothing; writes the links an apply would give |
| `--apply` | Push branches. Never opens a pull request |
| `--push-only` | Accepted for older command lines; changes nothing - every apply pushes and opens no pull request |
| `--confirm P` | With `--apply`: the package(s) you expect to be pushed; repeatable; must match exactly |
| `--confirm-count N` | With `--apply`: how many packages you expect to be pushed; must match exactly |
| `--title-prefix TEXT` | Prefix for commit and PR titles, e.g. `"BR-123: "` for a Jira id |
| `--limit N` | Process at most N packages |
| `--stop-after-failures N` | Stop after N consecutive failures (default 3; 0 = never) |
| `--retry-failed` | Only packages whose last result was a failure (`PUSH_FAILED`, `ERROR`, or an earlier version's `PR_FAILED`) |
| `--recheck` | Also revisit packages kept from earlier runs: branches already pushed (`BRANCH_PUSHED`) and `COVERED` packages. A package with a pull request keeps its record even then |
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
- Packages another release's branch covers
  ([below](#no-duplicate-branches-across-releases)) are never pushed, so they
  are neither counted nor named.
- Before the first push of an `--apply` run, a pre-flight checks that this
  machine may push. If `APM_ALLOW_PUSH` is not set, the run stops with
  `Pushing is disabled on this machine (APM_ALLOW_PUSH is not set). ...` and
  nothing is pushed. No token is checked: none is needed.

## What happens to each package

1. **Eligibility.** Only `VERIFIED` packages whose verification level proves
   the relationship. Packages in `upstream_md_publish.exclude`
   (`config/settings.yaml`) or `--exclude` are skipped with the reason.
2. **The reviewed bytes.** The file in
   `out/upstream-md-<release>/<package>/debian/upstream.md` must still hash to
   the plan's SHA-256 and still be exactly what today's mapping renders.
   Otherwise: `DRIFT`.
3. **Live reads.** The current tip of the target branch; whether the upstream
   ref still points at the commit the mapping recorded (if it moved: `DRIFT`);
   whether the package's branch ([Branch name](#branch-name)) already exists. A
   branch this tool pushed earlier must still hold the reviewed file (if it
   holds an older one: `DRIFT`).
4. **The existing file.** The tip is fetched (depth 1) into a scratch
   workspace and its `debian/upstream.md` compared: `NO_CHANGE`, `CONFLICT`
   for a file the tool does not recognise as its own
   ([upstream-md.md](upstream-md.md#outcomes)), otherwise `CREATED` or
   `UPDATED`.
5. **Another release's branch.** If another release's branch already proposes
   a file to the same repository and target branch, the package gets no branch
   of its own: `COVERED` when that branch holds exactly this file, otherwise
   `BRANCH_EXISTS` for a person
   ([No duplicate branches](#no-duplicate-branches-across-releases)).
6. **An earlier pull request.** With a token, whether a pull request for the
   branch already exists: open (`PR_EXISTS`), merged (`PR_MERGED`) or closed
   (`PR_CLOSED`). Each settles the package; nothing is pushed.
7. **The commit.** Built from objects: the tip's tree plus exactly one entry,
   with the tip as parent. Checks: the changed paths are exactly
   `debian/upstream.md`, `git diff --check` is clean, the parent is the tip.
   **A dry run stops here** with `DRY_RUN_OK`.
8. **Push.** `<commit>:refs/heads/<branch>`, never forced, and verified
   afterwards with `ls-remote`.
9. **Links, no pull request.** The package ends `BRANCH_PUSHED` with its
   Compare Link (`compare_url`), a link to the file on the branch
   (`file_url`) and GitHub's pre-filled form (`pr_create_url`). No pull
   request is opened.
10. **Ledger.** The result is written before the next package starts.

If the target branch moved past the pinned commit, the commit is still built
on the current tip, and the dry run notes
`aminor has moved past the pinned commit <sha>`.

Packages are processed in plan order with `upstream_md_publish.defer`
(`openssl`, `linux` - the largest repositories) last. One package's failure is
recorded and the run continues; `--stop-after-failures` consecutive failures
stop it, because that usually means the key or the network, not the package.

After every apply, `out/upstream-md-pr-links-<release>.md` and the
[publishing report](#the-publishing-report) are rewritten.

## Branch name

`upstream_md_publish.branch_template` in `config/settings.yaml` names the
branch. It is `debian-upstream-tracking`, the name Lakshya asked for
(2026-10-29): one branch in each package repository, pushed for review.
`upstream_md_publish.release_branch_templates` gives a release its own name:
trixie uses `debian-upstream-tracking-trixie` (decided 2026-11-10), so a
repository carries its aminor and aminor-trixie proposals side by side.

| Convention | Example | Scope |
|---|---|---|
| `debian-upstream-tracking` (current, bookworm) | `debian-upstream-tracking` | the whole repository |
| `debian-upstream-tracking-<release>` (current, trixie) | `debian-upstream-tracking-trixie` | the whole repository, one release |
| `upstream-metadata/{release}/{package}` (earlier) | `upstream-metadata/bookworm/mstpd` | one release and package |

The publisher refuses any branch outside these conventions - also
`debian-upstream-tracking/...` and a suffix of fewer than three lowercase
characters - and any name equal to the target, whatever the settings say; a
bad template stops the service at start-up. The branch is never the caller's
choice: the command line has no option for it, and the API refuses a request
that names one.

- **Branches already pushed keep their name.** A package whose ledger records
  a pushed branch or a pull request goes on using that branch, so changing the
  template never leaves a second branch with the same file beside the first.
  `mstpd`'s `upstream-metadata/bookworm/mstpd` is one, and its trixie
  proposal, pushed before the trixie name existed, stays on
  `debian-upstream-tracking`.
- **One proposal per repository at a time.** `debian-upstream-tracking` names
  neither the release nor the package. When the branch already holds another
  release's or package's proposal, the package ends `BRANCH_EXISTS` and names
  the owner (`... holds this tool's proposal for mstpd (bookworm) ...`);
  nothing is overwritten. The exception is another release's proposal of the
  identical file to the same target branch: the package is `COVERED`
  ([below](#no-duplicate-branches-across-releases)). Two packages built from
  one repository in the same run (`ONL-standalone`, `ONL-xc`): the first in
  plan order takes the branch, the others are refused before the confirmation
  count.
- **Pull requests found by name.** GitHub finds a pull request by its head
  branch's name, which this one shares with every proposal in the repository,
  so an earlier merged or closed pull request counts only if its head is the
  commit this package's record holds.
- Git cannot hold both `debian-upstream-tracking` and
  `debian-upstream-tracking/<x>` in one repository; mixing the two templates
  ends in `PUSH_FAILED` with git's message.

## No duplicate branches across releases

One target branch takes one `debian/upstream.md`. When another release's
branch already proposes a file to the same repository and the same target
branch - a Trixie package whose fork builds from `aminor`, like Bookworm's -
the package gets no branch of its own, and nothing is pushed for it:

| The other release's branch | Result |
|---|---|
| At its recorded commit, holding exactly this file | `COVERED`. The result names that branch and its commit, and `covered_by` names the release; its Compare Link and file link are that branch's. Its pull request serves both releases |
| Holding a different file | `BRANCH_EXISTS`: which file the target gets is a person's decision |
| Moved or gone since it was recorded, or its pull request closed without merging | `BRANCH_EXISTS`, with the reason |
| Its pull request merged | Not counted: the target branch has that file, so the package is `NO_CHANGE` if it is the same one |

Covering is read from the results ledgers of every release before the run
starts, so a `COVERED` package is never counted in `--confirm-count` or named
in `--confirm`; the banner shows them as
`of which another release's branch covers N: never pushed`. A dry run reports
`COVERED` the same way. A later run keeps a `COVERED` record unless
`--recheck`. A package that already has its own pushed branch is never
`COVERED`.

## Statuses

| Status | Meaning | What to do |
|---|---|---|
| `DRY_RUN_OK` | The commit was built and checked; nothing pushed | Review, then `--apply` |
| `BRANCH_PUSHED` | The branch was pushed and its links generated; no pull request was opened | A person opens the pull request from its Compare Link |
| `COVERED` | Another release's branch already proposes this exact file to the same repository and target; nothing pushed | Nothing: open (or review) that branch's pull request |
| `PR_EXISTS` | A pull request for this branch is open - opened by a person, found with a token | Review it |
| `PR_MERGED` | An earlier PR for this branch was merged | Nothing |
| `PR_CLOSED` | An earlier PR was closed without merging | A person decides; it is not re-proposed |
| `PR_OPENED` | Never produced: this tool opens no pull request. `verify_pr_ledger.py` fails on a record with it | - |
| `NO_CHANGE` | The fork already has this exact file | Nothing |
| `SKIPPED_NO_UPSTREAM` | Nothing to track | Nothing |
| `SKIPPED_NEEDS_REVIEW` | Not `VERIFIED` (or not proven) | Resolve the review reason first |
| `SKIPPED_EXCLUDED` | In `exclude` (settings or `--exclude`) | Nothing, or decide and edit settings |
| `CONFLICT` | The fork has a `debian/upstream.md` the tool does not recognise as its own | Compare by hand; never replaced automatically |
| `DRIFT` | The file, the mapping or the upstream ref changed since the plan - or a branch this tool pushed earlier holds an older file | Re-run `resolve-release` (if the upstream moved) and `generate-upstream-md`, review, dry-run again. For an older pushed file: delete that branch on the fork, closing any PR opened from it, then run again |
| `BRANCH_EXISTS` | The branch exists and this tool has no record of creating it - or another release's branch for the same target holds a different file | Find out who created it, or decide which file the target gets; nothing is overwritten |
| `BASE_UNREADABLE` | The existing file could not be read | Retry; check access |
| `PUSH_FAILED` | The push was rejected | Check the SSH key's write access; `--retry-failed` |
| `PR_FAILED` | Only in records from an earlier version, which opened pull requests: pushed, but the PR could not be opened | `--retry-failed` gives it `BRANCH_PUSHED` and its links, without pushing again |
| `ERROR` | Anything else, with the message | Read the error; `--retry-failed` |

`BRANCH_PUSHED` is neither done nor a failure. A later run, and
`--retry-failed`, leave it alone; `--recheck` revisits it (below).

## The links: a person opens the pull request

Every apply makes the same eligibility, drift and conflict checks, builds the
commit on the branch, needs the confirmation, and then stops at the push.
Instead of opening the pull request, it hands back three links per branch:

| Link | Example (`mstpd`) |
|---|---|
| Compare Link (`compare_url`) | `https://github.com/Arrcus/mstpd/compare/aminor...upstream-metadata/bookworm/mstpd` |
| The file on the branch (`file_url`) | `https://github.com/Arrcus/mstpd/blob/upstream-metadata/bookworm/mstpd/debian/upstream.md` |
| GitHub's pre-filled form (`pr_create_url`) | the Compare Link with `?quick_pull=1&title=...&body=...` |

A person with access to the repository opens one and creates the pull request
in the browser. `compare_url` and `file_url` are worked out from the record
(repository, target, branch) whenever the branch is on GitHub - pushed, or the
covering branch of a `COVERED` package - so they are never stale.

Without a token the tool cannot ask GitHub whether a pull request for the
branch was already merged or closed, so it relies on the ledger instead: a
package the ledger records with a pull request - `PR_EXISTS`, `PR_MERGED`,
`PR_CLOSED`, or an earlier version's `PR_OPENED` - is never pushed again: not
with `--recheck`, not through the API. If a token is set, it is used only to
read: an open pull request ends the package as `PR_EXISTS` instead of handing
out a second link, a merged one `PR_MERGED`, a closed one `PR_CLOSED`.

```bash
# Dry run: pushes nothing; shows, and writes, the link each push would give
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --show-diff

# Push mstpd's branch. No pull request is opened.
docker compose exec app apm publish-upstream-md --release bookworm --package mstpd --apply --confirm mstpd

# Later, batches of 5. Branches already pushed are not taken again or counted.
docker compose exec app apm publish-upstream-md --release bookworm --all --limit 5 \
    --apply --confirm-count 5
```

| | Dry run | Apply (`--apply`) |
|---|---|---|
| Banner | `DRY RUN - nothing will be pushed; showing the links an apply would give` | `APPLY - branches will be pushed; no pull request will be opened (this tool never opens one)` |
| Status per package | `DRY_RUN_OK` (or `COVERED`) | `BRANCH_PUSHED` (or `COVERED`) |
| PR URL column | the short link (compare page with `?quick_pull=1`, no title or body) | the same |
| Links file | `out/upstream-md-pr-links-dryrun-<release>.md` | `out/upstream-md-pr-links-<release>.md` |
| Publishing report | not touched | rebuilt (`report  <path>` in the summary) |
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
get nothing pushed and no link. A package the ledger records with a pull
request keeps that record, as described above.

A link cannot open a draft. If a draft is wanted, the person opening the form
chooses **Create draft pull request** there.

### The link

```
https://github.com/Arrcus/mstpd/compare/aminor...upstream-metadata/bookworm/mstpd?quick_pull=1&title=...&body=...
```

It opens GitHub's **Open a pull request** form with the base (`aminor`), the
branch, the title and the body already filled in - the title and body this
tool writes for the pull request, including the three CI sections. The
Compare Link is the same page without the form filled in. The person opening
it:

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
| `out/upstream-md-pr-links-<release>.md` | every `--apply`, and every push from the dashboard or the API |
| `out/upstream-md-pr-links-dryrun-<release>.md` | a dry run. Its links work only once an apply has pushed the branches |

Each rewrite of `upstream-md-pr-links-<release>.md` lists **every** package
the results ledger has as `BRANCH_PUSHED` - branches pushed by this run, by
earlier runs and from the dashboard alike - so a link nobody has used yet is
never dropped. A package leaves the file once its record is no longer
`BRANCH_PUSHED`: a `--recheck` with a token found its pull request, the merge
made it `NO_CHANGE`, or it became `DRIFT`. A dry run's file lists the links
its pushes would give and, after them, the branches already waiting.

One section per package: repository, base branch, branch, commit,
`Pushed: yes - waiting for a pull request` (or, in a dry run,
`Pushed: no - the link works once an apply pushes the branch`), the full link
(with `The description is too long for the link: paste it from below.` when
the body was left out), for a pushed branch its `Compare Link:` and
`debian/upstream.md:` links, then the title and the description, each in a
fenced block for copy and paste. The description is the one the link fills
in; when the link could not hold it, it is rebuilt from the current plan. The
summary at the end of the run prints the file's path (`links   <path>`).

### Resuming after `BRANCH_PUSHED`

The ledger records the pushed commit, and a later result that pushed nothing -
`DRIFT`, an exclusion, an error, a package no longer eligible - never replaces
that record (see [Ledgers and re-running](#ledgers-and-re-running)). A branch
whose tip is still that commit is therefore recognised as this tool's: it is
never pushed twice and never reported as `BRANCH_EXISTS`.

| Next run | Result |
|---|---|
| Any run without `--recheck` (dry run or apply) | The package is kept, not processed: nothing pushed, nothing to confirm, and its link stays in the links file - even if the reviewed file changed since (see below) |
| `--recheck`, dry run | `DRY_RUN_OK` (`branch already pushed; an apply would give its link again`), or `DRIFT` if the branch holds an older file |
| `--apply --recheck` without a token | Nothing pushed; `BRANCH_PUSHED` with its links again |
| `--apply --recheck` with a token | Nothing pushed; the pull request opened from the link is found (`PR_EXISTS`), or a merged one (`PR_MERGED`) or a closed one (`PR_CLOSED`); none found: `BRANCH_PUSHED` again. Once GitHub has deleted the branch after a merge, the target already has the file: `NO_CHANGE`, and the link leaves the links file |
| `--apply --recheck`, after the upstream branch moved | `DRIFT`: the description's upstream commit and counts are out of date. The ledger keeps `BRANCH_PUSHED` and the link. Re-run `resolve-release` and `generate-upstream-md` (the file comes out the same), review, run again |
| `--apply --recheck`, after the reviewed file changed | `DRIFT`: the pushed branch holds the older file, and nothing is overwritten. The ledger records the `DRIFT` with the pushed commit, and the link leaves the links file. Every later run stops there too. Delete the branch on the fork, closing any PR opened from it, then run again: it is pushed with the reviewed file |

A plain re-run does not look at a waiting branch, so it does not notice a
regenerated file: the links file keeps the old link, whose description matches
the older file the branch still holds. After regenerating the plan, check the
waiting branches before anyone opens a link. A `--recheck` dry run reports
each one that holds an older file as `DRIFT`, and touches neither the results
ledger nor the apply's links file.
`--apply --recheck --package <name> --confirm <name>` then records that
`DRIFT` and takes the link out of the links file. Without `--package`,
`--recheck` also processes - and pushes - every other eligible package.

A run does not take a `BRANCH_PUSHED` package again, so `--limit N` moves on
to packages not pushed yet. A `--recheck` run does, and counts it in
`--confirm-count`.

Without a token the tool cannot see whether anyone used the link, so the
ledger keeps saying `BRANCH_PUSHED` until a `--recheck` run with a token looks.
Nor can it see a pull request a reviewer closed. A package the ledger records
as `PR_CLOSED` is safe. But a `BRANCH_PUSHED` package whose PR was closed and
whose branch was then deleted looks, without a token, exactly like a branch
deleted to be pushed again: `--apply --recheck`, or a push from the dashboard
or the API, pushes it again and hands out a new link. Use them only for a
branch you deleted on purpose, or run with a token first so the closed PR is
recorded. A merged PR whose branch was deleted is safe either way: the target
already has the file, so the result is `NO_CHANGE`.

## The pull request

What the link fills in, for the person who opens it:

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
- Opened by whoever opens the link, from a branch pushed with the SSH key; the
  commit's author is `APM_COMMITTER_NAME <APM_COMMITTER_EMAIL>`.
- A draft is chosen on GitHub's form; `--title-prefix "BR-123: "` adds a Jira
  id if the repository's Jira check requires one.

The tool never opens, submits or merges a pull request. Opening, review and
merge happen on GitHub.

## Ledgers and re-running

| File | Written by |
|---|---|
| `out/upstream-md-pr-dryrun-<release>.{json,md}` | dry runs - never overwrites the real record |
| `out/upstream-md-pr-results-<release>.{json,md}` | `--apply` runs and pushes from the dashboard or the API: package, status, branch, commit, Compare Link, PR URL (or PR link), error |
| `out/upstream-md-publishing-report.{md,json}` | rebuilt after each of those ([The publishing report](#the-publishing-report)) |

The Markdown ledger is titled `debian/upstream.md publishing records - <release>`.
Its Compare Link column shows `compare_url` for every pushed or `COVERED`
package. For a `BRANCH_PUSHED` package, the PR URL column shows a short link
(the compare URL with `?quick_pull=1`, without title and body); the JSON keeps
the full link as `pr_create_url`, beside `compare_url`, `file_url` and, for a
`COVERED` package, `covered_by`. PR bodies are never stored in the ledger -
they are in the links file.

The results ledger is rewritten atomically after every package and is the
record of which branches exist and which pull requests were found. Keep it;
never edit or delete it. Re-running resumes from it:

- packages with `PR_EXISTS` or `PR_MERGED` (or an earlier version's
  `PR_OPENED`) are left alone, and so are `BRANCH_PUSHED`, `COVERED` and
  `PR_CLOSED`. `--recheck` revisits `BRANCH_PUSHED` and `COVERED`; a package
  with a pull request keeps its record even then;
- a branch this tool pushed (`BRANCH_PUSHED`, or an earlier version's
  `PR_FAILED`) is never pushed again: a run that reaches it hands out its
  links again or, with a token, records the pull request found for it;
- `--retry-failed` re-runs only failures;
- a branch it has no record of is `BRANCH_EXISTS`, and a closed PR is
  `PR_CLOSED` - neither is touched.

The record of a pushed branch (`BRANCH_PUSHED`) is never replaced by a result
that only says what one run did: it gives way only to a pull request answer, a
result that carries a pushed commit, or `NO_CHANGE`. The run's own output and
summary still show what happened - `DRIFT`, `SKIPPED_EXCLUDED`, `ERROR` - but
the ledger keeps the commit, so the next run still recognises the branch.
Every other record is replaced by the package's next result - except that a
package no longer eligible in the plan never replaces an open or merged pull
request's record.

Check the ledger's invariants after a run:

```bash
docker compose exec app python scripts/verify_pr_ledger.py bookworm
```

It checks that every branch is one this workflow creates, none equals its
target, every open PR has a pushed commit and a URL, every pushed entry names
a full commit SHA, and every `BRANCH_PUSHED` entry was pushed, has a full
40-character commit and a `pr_create_url` starting with
`https://github.com/<repository>/compare/<base>...<branch>`, and records no
pull request. Every `COVERED` entry must name the release that covers it
(`covered_by`), that branch's full commit and a branch this workflow creates,
and must have pushed nothing and carry no link of its own. No entry may be
`PR_OPENED`: this tool never opens a pull request.

## The publishing report

```bash
docker compose exec app apm upstream-md-report            # rebuild from the plans and the records
docker compose exec app apm upstream-md-report --verify   # and check every pushed branch first
```

`out/upstream-md-publishing-report.md` (with a `.json` beside it) is the one
place that shows Bookworm and Trixie together. It is rebuilt automatically
after every apply and every push from the dashboard, from the reviewed plans
and the results ledgers, so it never needs editing by hand. It starts with a
summary table - one row per release: pushed, covered by another release, not
pushed, verified on GitHub - then one section per release:

| Section | Lists |
|---|---|
| Pushed - waiting for a pull request | each branch with its target, commit, status (and pull request, if one was found), the link to its `debian/upstream.md`, its Compare Link and whether it was verified |
| Covered by another release's branch | each `COVERED` package with the release and branch that cover it, and that branch's links |
| Not pushed | every other package of the plan, with its status and the reason (`NOT_PUSHED` for one eligible but not pushed yet) |

`--verify` first reads every pushed branch back through git - `ls-remote`,
then a shallow, blobless fetch - and checks that it is at the recorded
commit, that the commit changes only `debian/upstream.md`, and that the file
is the reviewed one (the plan's SHA-256). Nothing is written to any remote.
The results go to `out/upstream-md-publishing-verification.json` and show in
the report's **Verified on GitHub** column until a branch's commit changes.
The command exits 1 if any branch is not as recorded.

Dated reports a person wrote, such as `out/upstream-md-branches-2026-11-10.md`,
are never read or rewritten.

## What it never does

Write to the target branch, force-push, merge, commit a second file, replace a
file it does not recognise as its own, propose a `NEEDS_REVIEW`, `NO_UPSTREAM`
or `FAILED` package, push without `--apply` and a matching confirmation, push
on a machine without `APM_ALLOW_PUSH`, push a second branch for a file another
release's branch already proposes to the same target, let a request choose the
repository, target or branch, or create, open, submit or merge a pull request -
in any mode, with or without a token.

## From the dashboard or the API

After **Generate**, the dashboard's **debian/upstream.md** card shows the file,
its outcome and its review flags, and - unless the outcome is `NO_CHANGE` -
these buttons, all through the same reviewed plan and publisher as the CLI:

| Button | Shown | What it does |
|---|---|---|
| **Dry run** | always | `dry_run: true`: every read and check, then stops. Shows the repository, target branch (from the reviewed plan), branch, base commit and title - or, for a `COVERED` package, the branch that covers it. Nothing is pushed |
| **Push branch (no PR)** | after a `DRY_RUN_OK` dry run, where the deployment allows pushing | `confirm: true`: pushes the branch and shows **Open the pull request on GitHub** (the pre-filled form), the **Compare Link** and **debian/upstream.md on the branch**. No PR is created |

There is no button that creates a pull request. Where pushing is disabled
(`push_enabled: false` in `/api/health`), the header says
**Pushing is disabled on this deployment** and the push button is not offered.
A pull request found for the branch is shown, never acted on.

The dashboard publishes exactly what the CLI would: the repository, the target
branch and the bytes come from `out/upstream-md-plan-<release>.json` and its
reviewed files (the same SHA-256 check), and the branch name from
`settings.yaml` and the publishing records. The ARCoS branch chosen under
**Selection** steers the comparison only. A push is recorded in the results
ledger and rewrites the links file and the publishing report.

```bash
# dry run over the API: nothing is pushed
curl -s -X POST http://<server-ip>:8080/api/upstream-md/pr -H 'Content-Type: application/json' \
  -d '{"package": "mstpd", "release": "bookworm", "dry_run": true}'

# push over the API: pushes the branch, opens no PR, needs no GitHub token
curl -s -X POST http://<server-ip>:8080/api/upstream-md/pr -H 'Content-Type: application/json' \
  -d '{"package": "mstpd", "release": "bookworm", "confirm": true}'

# the results ledger
curl -s "http://<server-ip>:8080/api/upstream-md/prs?release=bookworm"
```

The request names only `package`, `release`, `dry_run`, `confirm` and
`push_only` (default `true`). The API refuses:

- `push_only: false` - 403 `PULL_REQUESTS_DISABLED`, dry run or not;
- `arcos_branch`, `branch_name`, `draft` or any other field - 422;
- neither `dry_run` nor `confirm` - 400;
- a push on a machine without `APM_ALLOW_PUSH` - 403 `PUSH_DISABLED`; a dry
  run still works;
- a release with no reviewed plan - 404; a package not in it - 400.

A package the plan settles - excluded, not `VERIFIED`, or a reviewed file
changed since review (`DRIFT`) - comes back as that result: nothing is pushed
and nothing recorded. A push result is `BRANCH_PUSHED` with `compare_url`,
`file_url` and `pr_create_url`, or `COVERED` with the covering branch's
`compare_url` and `file_url` and `covered_by`. A push request for a package the
ledger records with a pull request returns that record unchanged and pushes
nothing.
