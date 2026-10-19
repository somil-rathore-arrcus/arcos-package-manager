# Patch preview workflow

Pulling selected upstream commits into an ARCoS fork, safely. Four steps, each
an explicit act. Nothing advances on its own.

```
select → preview (+ validation) → cherry-pick to a new branch (+ validation, push)
       → pull request → CI status
```

This is separate from the `debian/upstream.md` pull requests
([upstream-md-publishing.md](upstream-md-publishing.md)): those add one
metadata file; these carry source changes.

## In the dashboard

1. **Resolve upstream**, then **Compare**.
2. Tick commits in the commit table, or use **Select critical (N)**.
   Commits already present cannot be selected: an ARCoS-specific commit is
   already there, and a backported one is already applied.
3. **Preview N selected**. The **Patch PR · preview** card shows **Applies
   cleanly** (with the apply order, the files that would change and the
   validation result), **Conflict** (the commit that failed and its
   conflicting paths), **Validation failed** (the command, its exit status and
   output), or **Preview failed**.
4. After a clean preview only:
   - with a token, **Create patch branch and push**, then **Create pull
     request** - a separate click;
   - without a token, **Push patch branch (no PR)**: the branch is pushed with
     the SSH key and **Open the pull request on GitHub** links the pre-filled
     form (title and the description below) for a person with access.
5. **Pull request and CI** then shows the branch's commit on the fork, any
   pull request opened from it (found from the fork's `refs/pull/<n>/head`
   over SSH, so it works without a token), and its checks. **Refresh status**
   looks again; nothing polls on its own.

## Selection

The user chooses commits. **Select critical** adds every commit with security
evidence; nothing else is ever selected automatically, because selecting on a
guess about importance is how an unreviewed patch reaches a release.

## Preview

Runs the series in a **throwaway workspace** that is destroyed afterwards.
Nothing is pushed and the target branch is never touched, so a preview is safe
to run on anything.

Commits are applied **oldest first, in topological order** - the order of
clicks is replaced by history order. Applying a later commit before the one it
builds on conflicts for no reason, and ordering by date silently reverses
commits made in the same second.

## Conflicts

A conflict stops the series at the commit that conflicted, reports the
conflicting paths, and aborts. It is never resolved automatically: choosing a
side of a conflict is a decision about the product. There is no force option
anywhere in this path.

## Validation: build or test the patched tree

`config/settings.yaml` `patches.validation_commands` lists commands run, in
order, in the patched working tree - in the preview, and again in the
cherry-pick before anything is pushed. Each must finish within
`patches.validation_timeout_seconds` (900). The first failure stops the run:
the preview reports `VALIDATION_FAILED` and the cherry-pick pushes nothing.
Every run reports the command, its real exit status, how long it took and the
tail of its output.

The list is empty by default, and then the preview, the dashboard and the pull
request description all say **nothing was built or tested** - never that the
patch passed. Commands come only from the settings file, never from a request.

## Re-validation: the selection must still be true

A comparison may have been computed against the pinned commit, or an hour
ago, and branches move. The server re-checks the selection against the
branches as they are **now**, in the preview and again in the cherry-pick:

- every selected commit must be in the current missing set - reachable from
  the upstream ref, not from the current target tip. Anything else is refused
  with `STALE_SELECTION` (HTTP 409), listing each commit (already in the
  target, or no longer on the upstream ref after a rewrite). A commit sent in
  `approved_shas` may come from outside the missing set, but must still be on
  the upstream ref, and is reported in the warnings;
- the preview returns the tip it applied onto (`base_sha`) and whether the
  target moved since the comparison (`base_moved`);
- the cherry-pick must send that `base_sha` back as `expected_base_sha`, and is
  refused with `BASE_MOVED` if the target has moved since the preview. Without
  it the API refuses outright: nothing is applied to a tip nobody previewed.

## Cherry-pick

Applies the series to a **new branch** created from the target:

```
upstream/<package>/<YYYYMMDD-HHMMSS>
```

The target branch is never written to; a branch name equal to the target is
refused. Commits are applied with `git cherry-pick -x`, so each records the
upstream SHA it came from. A commit that turns out to be empty is skipped
rather than failing the series. Pushing happens only with `push: true`; a
conflicting series pushes nothing.

## Pull request

A separate call. The description starts with the three sections ARCoS CI
requires - `Problem Description :`, `Root Cause :` and `Fix Details :` - then
the package, release, target branch, upstream repository and ref, the
resolution, the merge base, every applied commit (linked to upstream, with its
criticality and evidence where the comparison assessed it) and the validation
result as it happened. The cherry-pick returns this title and description
(`pr_title`, `pr_body`) and, once pushed, the pre-filled link
(`pr_create_url`); the dashboard sends the same text when it opens the PR. If
a PR is already open for the branch it is returned instead of a duplicate.

## CI status

`GET /api/publish-status?package=&branch=&base=` reports, for a branch this
tool created (`upstream/...` or `upstream-metadata/...` only):

- whether the branch is on the fork, and its commit;
- pull requests whose head is that commit, from git refs - number and link,
  no token needed, but no state;
- with a token: the pull request's state, and the commit's check runs and
  commit statuses as `SUCCESS`, `FAILURE`, `PENDING` or `NONE` (nothing
  reported yet), each check with its link and summary.

Without a token, or when GitHub cannot be asked, CI is `UNAVAILABLE` with the
reason. That is never shown as a pass.

## The same steps over the API

```bash
API=http://<server-ip>:8080/api

# 1. preview - writes nothing
curl -s -X POST $API/patches/preview -H 'Content-Type: application/json' -d '{
  "package": "mstpd", "release": "bookworm", "arcos_branch": "aminor",
  "upstream_repository": "https://github.com/mstpd/mstpd.git", "upstream_ref": "master",
  "shas": ["<sha1>", "<sha2>"],
  "comparison_arcos_commit": "<arcos_commit from the comparison>",
  "comparison_upstream_commit": "<upstream_commit from the comparison>"
}'
# → outcome CLEAN | CONFLICT | VALIDATION_FAILED | EMPTY | FAILED, base_sha,
#   order, files_changed, validation

# 2. cherry-pick onto a new branch; push only when asked
curl -s -X POST $API/patches/cherry-pick -H 'Content-Type: application/json' -d '{
  ...same fields..., "expected_base_sha": "<base_sha from the preview>", "push": true
}'
# → new_branch, applied, head_sha, pushed, commits, validation, pr_title,
#   pr_body, pr_create_url (once pushed)

# 3. pull request (needs the token) - or open pr_create_url in a browser
curl -s -X POST $API/pull-requests -H 'Content-Type: application/json' -d '{
  "package": "mstpd", "release": "bookworm", "arcos_branch": "aminor",
  "head_branch": "<new_branch>", "applied": ["<sha1>", "<sha2>"],
  "title": "<pr_title>", "body": "<pr_body>"
}'

# 4. where it stands: PR (from refs) and CI (token only)
curl -s "$API/publish-status?package=mstpd&branch=<new_branch>&base=aminor"
```

## What never happens automatically

- cherry-picking into the target branch
- pushing
- opening a pull request
- resolving a conflict
- selecting patches beyond an explicit click (or **Select critical**)
- merging
