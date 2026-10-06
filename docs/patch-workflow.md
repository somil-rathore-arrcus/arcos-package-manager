# Patch preview workflow

Pulling selected upstream commits into an ARCoS fork, safely. Four steps, each
an explicit act. Nothing advances on its own.

```
select → preview → cherry-pick to a new branch (+ push) → pull request
```

This is separate from the `debian/upstream.md` pull requests
([upstream-md-publishing.md](upstream-md-publishing.md)): those add one
metadata file; these carry source changes.

## In the dashboard

1. **Resolve upstream**, then **Compare**.
2. Tick commits in the commit table, or use **Select critical (N)**.
   Commits already present cannot be selected: an ARCoS-specific commit is
   already there, and a backported one is already applied.
3. **Preview N selected**. The Preview card shows **Applies cleanly** (with the
   apply order and the files that would change) or **Conflict** (the commit
   that failed and its conflicting paths), or **Preview failed**.
4. **Create patch branch and push** - only after a clean preview, and only
   when the deployment has a token (in read-only mode the button is not
   shown). This creates the branch and **pushes it**.
5. **Create pull request** - a separate click.

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

A separate call. The body carries the package, release, target branch,
upstream repository and ref, the resolution, the merge base, and every applied
commit with its criticality and evidence. If a PR is already open for the
branch it is returned instead of a duplicate.

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
# → outcome CLEAN | CONFLICT | EMPTY | FAILED, base_sha, order, files_changed

# 2. cherry-pick onto a new branch; push only when asked
curl -s -X POST $API/patches/cherry-pick -H 'Content-Type: application/json' -d '{
  ...same fields..., "expected_base_sha": "<base_sha from the preview>", "push": true
}'
# → new_branch, applied, head_sha, pushed

# 3. pull request (needs the token)
curl -s -X POST $API/pull-requests -H 'Content-Type: application/json' -d '{
  "package": "mstpd", "release": "bookworm", "arcos_branch": "aminor",
  "head_branch": "<new_branch>", "applied": ["<sha1>", "<sha2>"]
}'
```

## What never happens automatically

- cherry-picking into the target branch
- pushing
- opening a pull request
- resolving a conflict
- selecting patches beyond an explicit click (or **Select critical**)
- merging
