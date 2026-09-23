# Git comparison

## Version strings are not used

A Debian version says what the packaging claims. It does not say what the branch
contains, and two forks at the same version can differ by hundreds of commits.
Every number this tool reports comes from the commit graph.

## The three sets

Given a merge base `M`, the upstream head `U` and the ARCoS head `A`:

```
missing upstream    M..U, minus anything already present by patch-id
ARCoS-specific      M..A, minus the backports it already carries
already backported  in both, under different SHAs
```

Worked example. Upstream `A→B→C→D→E`, ARCoS `A→B→C→X→Y`:

```
merge base        C
missing upstream  D, E
ARCoS-specific    X, Y
```

`X` and `Y` are local work. Counting them as missing upstream commits would
report work already done as a backlog still to do — the single most misleading
thing this tool could say.

Merges are excluded throughout (`--no-merges`): a merge commit cannot be
cherry-picked, so counting them would report a backlog larger than the number of
patches the tool can actually offer. The ancestry probe used by the mapping uses
the same basis, so "behind" means the same thing in both places.

## No common ancestor

Some ARCoS packages were imported from tarballs rather than forked. `babeltrace`,
`ctypesgen` and `rsyslog` share no commit with any candidate upstream.

The comparison refuses rather than inventing a number, and the API returns
`NO_COMMON_ANCESTOR` (HTTP 422) with an explanation. A backlog computed against a
tree with no shared history is not a smaller truth, it is a fiction.

## Backport detection

A backport is the same change committed again — cherry-picked, rebased, or
applied by hand — so its SHA is necessarily different. Comparing SHAs reports it
as missing and invites someone to apply it twice.

`git patch-id --stable` hashes the diff, ignoring commit metadata, context line
numbers and whitespace-only churn, so the same change hashes the same however it
arrived. `PatchIdService` computes patch ids for both sides and matches them.

Subject-line matching is deliberately not used: it breaks on reworded backports
and produces false matches between unrelated commits that share a subject.

Very long ranges are capped (2000 commits by default) and the cap is reported in
`warnings` rather than applied silently.

## Fetching: blobless for the graph, blobs only when diffing

Both sides are fetched with `--filter=blob:none`. Walking the commit graph needs
no file contents, and for a large repository the saving is enormous.

`patch-id` is the opposite: it materialises every diff. In a partial clone each
one triggers a lazy fetch back to the server, and the cost is not subtle —
measured on `iputils`, a 4 MB workspace spent over four minutes in
`git index-pack --promisor` re-downloading blobs and the request never returned.
A small package like `pyrad` hides this completely, which is what makes it a
trap.

So blobs are backfilled in one pass, and only when the range is small enough for
backport detection to be worth running (`max_total_commits`, default 5000).
Beyond that the comparison skips both the download and the diffing, and reports
backport detection as unavailable rather than pretending none exist.

## Cost

Workspaces are keyed by package and release and reused, so the expensive part is
paid once:

| | cold | warm |
|---|---|---|
| `pyrad` (109 + 19 commits) | ~13s | ~12s |
| `iputils` (142 + 272) | ~15s | ~12s |
| `linux` (24,158 + 97) | ~574s | ~11s |

The kernel is genuinely large; a first comparison clones its history. The git
ceiling (`APM_GIT_LONG_TIMEOUT`, default 900s) bounds a stall without cutting off
a legitimate first clone, and the frontend waits longer still so the backend's
explicit `TIMEOUT` surfaces instead of a generic client abort.

## Truncation

Commit lists are capped at 500 by default. When the cap applies, the summary
still reports the true total and a warning says what was shown.
