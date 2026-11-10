# Generating `debian/upstream.md`

`debian/upstream.md` records two facts inside each ARCoS package repository:
the upstream project the fork comes from, and the upstream branch the fork
tracks. Nothing else. It is rendered from the same verified resolution the
dashboard and the workbook show, so the committed record and the tool cannot
disagree.

The evidence behind the two values - verification level, ref selection,
merge base, counts, backlog - goes into the pull request body. A reviewer reads
it once; the package does not carry it forever. The plan carries less: each
package's status, reason and [review flags](#review-flags) beside the two
values. To read the full body before anything is pushed, run a `--push-only`
dry run: its links file has each package's description
([upstream-md-publishing.md](upstream-md-publishing.md#the-links-file)).

Generating writes files **locally** under `out/`. It pushes nothing, creates no
branch, opens no pull request and needs no GitHub token. Proposing the files is
a separate step: [upstream-md-publishing.md](upstream-md-publishing.md).

## What the file contains

Two fields, one blank line between them, and nothing else - no heading, no
comment, no other line. `mstpd`:

```text
Upstream Source: https://github.com/mstpd/mstpd.git

Current Upstream Branch: master
```

The blank line makes GitHub show the fields as two lines instead of one
paragraph. The file ends with exactly one newline and has no trailing
whitespace or carriage return, so it passes `git diff --check`. Generation is
deterministic: the same mapping always gives the same bytes.

A package whose mapping records a tag but no branch, `ethtool`, where the
ancestry probe also measured the upstream default branch against the same ARCoS
commit:

```text
Upstream Source: https://git.kernel.org/pub/scm/network/ethtool/ethtool.git

Current Upstream Branch: master
```

A tag is a fixed point, not something a fork can track, so it is never written
as if it were a branch. The probe measures the repository's default branch (and
the branch `config/overrides.yaml` names, if any) with the same ARCoS commit as
the tag. When that branch shares history with the fork
and the fork has no more commits outside it than outside the tag (ethtool: 14
outside `master`, 116 outside `v6.1`), the branch holds the fork's base: the
file names it, and the `BRANCH_FROM_TAG` review flag says it was derived from a
tag and gives both counts. A fork taken from a side branch has commits the
default branch lacks, so it does not qualify. If two branches qualify, the one
with fewer commits outside it wins, then the one `overrides.yaml` names. Without
that measurement the file names the tag and says no branch was verified:

```text
Current Upstream Branch: N/A - no upstream branch verified; upstream tag v6.1.0
```

Values are never guessed. Every value that was not established is written as
fixed text starting with `N/A`, so the plan, the verifier and a reader can tell
a value from a gap at a glance.

### Upstream Source

| The mapping | Written |
|---|---|
| `VERIFIED`, with an upstream repository | Its URL, e.g. `https://github.com/mstpd/mstpd.git` |
| `VERIFIED`, no repository URL | `N/A` |
| Not `VERIFIED` (only with `--include-needs-review`) | `N/A - not verified (status NEEDS_REVIEW)` |

An unverified candidate URL is never written: in the package it would read as
fact.

### Current Upstream Branch

The first row that applies:

| The mapping records | Written | Branch verified |
|---|---|---|
| A status other than `VERIFIED` | `N/A - not verified (status <STATUS>)` | no |
| An upstream branch | The branch, e.g. `master`, `main`, `linux-6.1.y` | yes |
| An upstream tag and no branch, and the probe measured the upstream default branch, or the branch `overrides.yaml` names, against the same ARCoS commit as sharing history, with no more ARCoS commits outside it than outside the tag | That branch, e.g. `master` (flagged `BRANCH_FROM_TAG`) | yes |
| An upstream tag and no branch, otherwise | `N/A - no upstream branch verified; upstream tag <tag>` | no |
| ... and the ARCoS fork does not contain that tag | The same, followed by ` (the ARCoS fork does not contain this tag)` | no |
| Only a ref selection whose `ls-remote` kind is a branch (an older mapping) | That ref | yes |
| A ref, but not whether it is a branch or a tag (an older mapping) | `N/A - no upstream branch verified; ref <ref> is not recorded as a branch or a tag` | no |
| No upstream ref | `N/A - no upstream branch verified` | no |

"The fork does not contain that tag" means the ref selection's base tag is the
same tag and the selection recorded that ARCoS does not contain it.
Whitespace inside a mapping value is collapsed to single spaces, so each value
is always one line.

### Review flags

Some answers are recorded correctly but still need a person to look at them: a
tag where a branch was expected, a fallback ref, a plausibility warning. The
tool reports these as review flags, each a code and a detail. A flag never
stops a package from being generated or published. It says what to check
before the pull request is merged.

Flags appear in:

- the output of `generate-upstream-md`, after the summary:
  `Flagged for review (N) - check before merging; a flag does not block publishing:`,
  one line of codes per package;
- the plan: `flags` in the JSON, the **Flags** column and the
  **Flagged for review** section (each code with its detail) in the Markdown;
- the output of a `publish-upstream-md` dry run (and of a push-only apply),
  as `review: <CODE> - <detail>` under each package;
- the pull request body, under `Review notes :`.

Always in this order:

| Code | Raised when | Detail |
|---|---|---|
| `BRANCH_NOT_VERIFIED` | The branch field is an `N/A` value | Why: the mapping has only a tag, a ref of unrecorded kind, no ref, or is not `VERIFIED` |
| `TAG_ONLY` | The mapping records a tag and no branch, and the file says N/A | The tag, then the ref selection's reason |
| `BRANCH_FROM_TAG` | The mapping records a tag and no branch, and the file names the default branch the probe measured (in `TAG_ONLY`'s place) | The tag, the branch, both commit counts, then the ref selection's reason |
| `BASE_TAG_NOT_IN_FORK` | The ARCoS fork does not contain the upstream tag for Debian's version | The base tag and Debian's upstream version |
| `FALLBACK_REF` | The ref was chosen as a fallback, not as the release's own ref | The ref selection's reason |
| `MAPPING_WARNINGS` | The mapping carries plausibility warnings | The warnings, separated by `; ` |

`BRANCH_NOT_VERIFIED` is raised exactly when the branch field starts with
`N/A`, and `BRANCH_FROM_TAG` exactly when the plan records a tag and the field
names a branch. The verifier checks both.

## Generate for a release

```bash
docker compose exec app apm generate-upstream-md --release bookworm --branch aminor
```

| Option | Meaning |
|---|---|
| `--release` | Debian release (default `bookworm`) |
| `--branch` | Only packages whose ARCoS branch is this (`aminor` for Bookworm). Others are listed as left out; a package is never re-targeted onto a branch its release does not ship |
| `--package` | Only this package; repeatable. **Rewrites the plan with only these packages** - see below |
| `--include-needs-review` | Also write files for `NEEDS_REVIEW` packages. Both fields then read `N/A - not verified (status NEEDS_REVIEW)`; the candidate upstream is never written. They are still never published |
| `--offline` | Do not read the existing file from each fork; every outcome then reads `CREATED`, which is a guess |
| `--out-dir` | Output directory (default `out/`) |

It needs a mapping first (`apm resolve-release --release bookworm`).
Comparisons do not change the file. The backlog they produce goes into the
pull request body, which is built when publishing.

### What it writes

| File | Contents |
|---|---|
| `out/upstream-md/<package>/debian/upstream.md` | The file, exactly as it would be committed |
| `out/upstream-md-plan-<release>.json` | The plan: one entry per package (keys below), plus the skipped packages and why. `"pushed": false`, `"pull_requests_created": false` |
| `out/upstream-md-plan-<release>.md` | The same plan as a table - Package, ARCoS repository, Base branch, Branch, Outcome, Upstream Source, Current Upstream Branch, Flags (codes) - then **Flagged for review** (each flagged package with each code and detail, or `No package is flagged.`), then the packages not generated |

Each plan entry:

| Key | Holds |
|---|---|
| `package`, `release`, `status` | The package and its resolution status |
| `arcos_repository`, `base_branch`, `branch` | Where the change goes: `Arrcus/mstpd`, `aminor`, `upstream-metadata/bookworm/mstpd` |
| `commit_message`, `outcome`, `diff` | The commit title, `CREATED` / `UPDATED` / `NO_CHANGE` / `CONFLICT`, and the diff against the fork |
| `upstream_source`, `current_upstream_branch` | The two values exactly as the file writes them |
| `upstream_branch` | The verified branch, or `""` |
| `upstream_tag` | The tag, when a tag is all the mapping has, or `""` |
| `branch_verified` | `true` only when the branch field is a recorded branch |
| `flags` | `[{"code": "...", "detail": "..."}]`, in the order above |
| `upstream_repository`, `upstream_ref` | The mapping's raw values |
| `reason`, `existing_unknown` | The resolution's reason; `true` when the fork could not be read |
| `path`, `local_path`, `sha256`, `bytes` | The reviewed file and its **SHA-256** and size |

The publisher commits only a file whose bytes still match the plan's SHA-256,
so **the plan is what you review**.

> **Caution:** `--package` writes a plan containing only those packages,
> replacing the full plan. Before publishing several packages (`--all`), run
> the command without `--package` so the plan lists all of them again.

> **After updating the tool:** a plan or files generated by a version before
> the two-field format no longer match what the mapping renders. The verifier
> reports `the plan predates the two-field file; regenerate`, and the publisher
> reports `DRIFT`. Run `generate-upstream-md` again and review the new plan.

### Which packages get a file

| Status | File? |
|---|---|
| `VERIFIED` | Yes |
| `NEEDS_REVIEW` | Only with `--include-needs-review`, with both fields `N/A - not verified (status NEEDS_REVIEW)`. Never published |
| `NO_UPSTREAM` | No - a file recording an upstream would be fiction |
| `FAILED` | No |

`upstream_md_publish.exclude` in `config/settings.yaml` does not change what is
generated: an excluded package that is `VERIFIED` still gets a file and a plan
entry, and the publisher skips it as `SKIPPED_EXCLUDED`. The five Bookworm
`CURATED_CONFLICT` packages are listed there
([configuration.md](configuration.md#upstream_md_publishexclude)).

### Outcomes

Before writing, the file already in each fork (at the pinned commit) is read,
read-only, and compared:

| Outcome | Meaning |
|---|---|
| `CREATED` | The fork has no `debian/upstream.md` |
| `UPDATED` | The fork has a file the tool recognises as its own (below), and it differs |
| `NO_CHANGE` | Identical to what is already committed (ignoring trailing whitespace and line endings) |
| `CONFLICT` | Any other file - it is never replaced silently |

A fork that could not be read is listed separately: its outcome is what
*would* happen if no file is there, which is not the same as knowing none is.

The tool recognises a file as its own when it is one of:

- **The two-field format.** Every non-blank line is `Upstream Source: ...` or
  `Current Upstream Branch: ...`, each exactly once. A space before the colon
  (`Current Upstream Branch : main`), Windows line endings, extra blank lines
  and either order are accepted. Such a file holds nothing the new one would
  lose, so it is `UPDATED` and the diff shows the change.
- **A file from an older version of this tool.** Those carried the marker
  `Generated by the ARCoS Package Manager` (in an HTML comment at the top). The
  new file replaces the whole report.

Anything else - a heading, a note, a third field, an indented line - is
`CONFLICT`. It may hold something the tool does not know about, and replacing
it is a person's decision.

## Review checklist

Open `out/upstream-md-plan-<release>.md` and, per package,
`out/upstream-md/<package>/debian/upstream.md`:

- ARCoS repository and base branch are the package's fork and release branch
  (`Arrcus/mstpd`, `aminor`); the branch is `upstream-metadata/<release>/<package>`.
- The file is the two lines and nothing else.
- `Upstream Source` is the project you expect.
- `Current Upstream Branch` is the branch you expect the fork to follow
  (`master`, `main`, a maintenance branch such as `linux-6.1.y`), or an `N/A`
  value you accept as the honest answer.
- Every package under **Flagged for review** has been looked at:
  - `BRANCH_FROM_TAG`: the mapping selected a tag and the file names the
    default branch the probe measured as holding the fork's base. Check the
    counts in the detail and that the fork does follow that branch.
  - `TAG_ONLY` with `BRANCH_NOT_VERIFIED`: the file names the tag and claims
    no branch. Accept that, or establish the branch in the mapping
    ([upstream-resolution.md](upstream-resolution.md)) and regenerate. Never
    edit the file by hand: it would no longer match the plan's hash.
  - `BASE_TAG_NOT_IN_FORK`: the fork does not contain the tag for Debian's
    version. Check the fork is really based on this upstream before merging.
  - `FALLBACK_REF`: no release-specific ref was found. Check the fallback is
    what the fork follows.
  - `MAPPING_WARNINGS`: read each warning; it may point at a wrong series or
    fork base.
- The plan's `status` is `VERIFIED`. The verification level - in the dashboard,
  the workbook or the pull request body - is `SHARED_HISTORY`, or
  `CONTENT_MATCH_APPROVED` with a named approver.
- Outcome `CREATED` or `UPDATED`. `CONFLICT` needs a person to compare the
  existing file first; `NO_CHANGE` needs nothing.

Then check that the files still match the mapping and the plan:

```bash
docker compose exec app python scripts/verify_upstream_md.py bookworm
```

### What the verifier checks

`scripts/verify_upstream_md.py` is standalone - it does not import the tool, so
a bug in the renderer cannot also be a bug in the check. It reads
`out/upstream-mapping-<release>.csv`, the files and the plan:

| Check | Passes when |
|---|---|
| every generated file has a mapping row | |
| no file was generated for a NO_UPSTREAM package | |
| every file has exactly the two fields and nothing else | Each file parses as the two fields and is laid out byte for byte as generated: one blank line between them, one trailing newline |
| every field matches the mapping row | `Upstream Source` is the row's `Upstream Repository` (`N/A` if empty). If the row has an `Upstream Branch`, the branch field is that branch. Else if it has an `Upstream Tag` and the row's probe measurements in `out/upstream-resolutions.json` qualify a branch (restated from the renderer: the upstream default branch or the branch `overrides.yaml` names, same repository, shares history, no more ARCoS commits outside it than outside the tag; fewest outside wins, then the named branch), the branch field is exactly that branch. Else if it has an `Upstream Tag`, the branch field is exactly `N/A - no upstream branch verified; upstream tag <tag>`, followed by ` (the ARCoS fork does not contain this tag)` exactly when the row's `ARCoS Contains Base Tag` is `no` and its `Upstream Base Tag` is that tag (a CSV without those two columns accepts either form). Else it is exactly `N/A - no upstream branch verified`, or `N/A - no upstream branch verified; ref <ref> is not recorded as a branch or a tag`. Every comparison is of the whole value, so a longer tag, an added branch or a guess after `N/A` fails. For a row that is not `VERIFIED`, both fields are `N/A - not verified (status <STATUS>)` |
| the plan pushed nothing / the plan created no pull request | `"pushed": false`, `"pull_requests_created": false` |
| the plan covers exactly the generated files | |
| no planned branch is the branch it targets | |
| every planned branch is under upstream-metadata/ | |
| the plan records each file's values and flags every unverified branch | `upstream_source` and `current_upstream_branch` equal the file's values, and `BRANCH_NOT_VERIFIED` is in `flags` exactly when the branch field starts with `N/A` |
| every generated file still matches the plan's hash | |

It ends with `FAILURES: none` when everything passes.

## One package, from the dashboard or the API

The dashboard's **debian/upstream.md** card - **Generate** - renders the file
for the selected package and shows the diff against the fork, the outcome
badge and, under **Check before merging**, the package's review flags (the same
ones the plan lists). It does not write the plan.

```bash
curl -s -X POST http://<server-ip>:8080/api/upstream-md/generate \
  -H 'Content-Type: application/json' \
  -d '{"package": "mstpd", "release": "bookworm"}'
```

Returns the content (the two-field file), the outcome and the diff.
`"write_local": true` also saves it as
`out/upstream-md/<package>__<release>.md`. A `NO_UPSTREAM` package is refused
(HTTP 400).
