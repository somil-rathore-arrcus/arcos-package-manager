# Patch workflow

Four steps, each an explicit act. Nothing advances on its own.

```
select → preview → cherry-pick → pull request
```

## Selection

The user chooses commits. "Select critical" adds every commit with security
evidence; nothing else is ever selected automatically, because selecting on a
guess about importance is how an unreviewed patch reaches a release.

Commits that are already present cannot be selected: an ARCoS-specific commit is
already there, and a backported one is already applied.

## Preview

Runs the series in a **throwaway workspace** that is destroyed afterwards.
Nothing is pushed and the target branch is never touched, so a preview is safe to
run on anything.

It reports the apply order, which commits applied, the files that would change,
and any conflict.

Commits are applied **oldest first, in topological order**. Applying a later
commit before the one it builds on conflicts for no reason. Topological rather
than by date: commits made in the same second sort arbitrarily by date, which
silently reverses a pair of patches and manufactures a conflict the series does
not contain.

## Conflicts

A conflict stops the series at the commit that conflicted, reports the
conflicting paths, and aborts. It is never resolved automatically — choosing a
side of a conflict is a decision about the product, and guessing it silently is
how wrong code ships.

There is no force option - in the service or in the git layer
(`GitWorkspace.push` has no force parameter).

## Re-validation: the selection must still be true

A comparison may have been computed against the pinned commit, or an hour ago,
and branches move. So the server re-checks the selection against the branches
as they are **now**, in the preview and again in the cherry-pick:

- every selected commit must be in the current missing set - reachable from the
  upstream ref, not reachable from the current target tip. A commit the target
  already has, or one an upstream rewrite removed, is refused with
  `STALE_SELECTION` (HTTP 409), listing each one;
- the preview reports the tip it applied onto (`base_sha`) and whether the
  target moved since the comparison (`base_moved`);
- the cherry-pick must pass that `base_sha` back as `expected_base_sha`, and is
  refused with `BASE_MOVED` if the target has moved since the preview. The API
  refuses a cherry-pick without it: nothing is applied to a tip nobody
  previewed.

## Cherry-pick

Applies the series to a **new branch** created from the target:

```
upstream/<package>/<YYYYMMDD-HHMMSS>
```

The target branch is never written to; the API refuses a request whose branch
name equals the base. Commits are applied with `git cherry-pick -x`, so each
records the upstream SHA it came from.

Pushing happens only when `push: true` is passed. A conflicting series pushes
nothing.

## Pull request

A separate call again. The PR body carries the package, release, target branch,
upstream repository and ref, the merge base, and every applied commit with its
criticality and the evidence behind it.

If a PR is already open for that branch it is returned rather than duplicated.

## What never happens automatically

- cherry-picking into the target branch
- pushing
- opening a pull request
- resolving a conflict
- selecting patches beyond an explicit click
- overwriting a `debian/upstream.md` this tool did not write
