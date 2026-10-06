# API reference

Base URL: `http://<server-ip>:8080/api`. Interactive documentation, generated
from the code, is at `http://<server-ip>:8080/docs`; the schema at
`/openapi.json`.

The API has **no authentication of its own** - see
[deployment.md → Access control](deployment.md#access-control). Endpoints that
push or open pull requests need `APM_GITHUB_TOKEN` on the server and an
explicit flag in the request.

## Errors

Every handled error is JSON with a code the dashboard renders differently:

```json
{"code": "STALE_SELECTION", "message": "what went wrong", "detail": "more, or null"}
```

| Code | HTTP | | Code | HTTP |
|---|---|---|---|---|
| `INVALID_REQUEST` | 400 | | `UPSTREAM_NOT_RESOLVED` | 409 |
| `AUTH_REQUIRED` | 401 | | `NO_COMMON_ANCESTOR` | 422 |
| `NOT_FOUND`, `BRANCH_NOT_FOUND`, `INVALID_REF` | 404 | | `INTERNAL` | 500 |
| `CONFLICT`, `STALE_SELECTION`, `BASE_MOVED` | 409 | | `REPOSITORY_UNAVAILABLE`, `GIT_ERROR`, `PROBE_FAILED`, `NETWORK_ERROR` | 502 |
| | | | `TIMEOUT` | 504 |

## Endpoints

| Method | Path | Purpose | Writes |
|---|---|---|---|
| GET | `/health` (no `/api`) | Liveness for the container healthcheck | - |
| GET | `/api/health` | What this deployment can do | - |
| GET | `/api/health/workspace` | Git host reachable, workspace writable | - |
| GET | `/api/releases` | Debian releases | - |
| GET | `/api/packages` | Package catalogue | - |
| GET | `/api/packages/{package}` | One package | - |
| GET | `/api/branches` | The fork's real branches | - |
| GET | `/api/upstream/{package}` | Resolution (mapping, or live with `refresh`) | - |
| POST | `/api/upstream/resolve` | Same, as a POST body | - |
| POST | `/api/upstream/verify` | Check a user-supplied upstream | - |
| GET | `/api/upstream/{package}/evidence` | The evidence behind a resolution | - |
| POST | `/api/upstream/content-base/approve` | Record a content-match approval | `out/approvals.yaml` |
| POST | `/api/comparison` | Full comparison | snapshot in `out/comparisons/` |
| GET | `/api/comparison/{package}` | Same, as a GET | snapshot |
| GET | `/api/commits/{package}` | The commit lists only | snapshot |
| POST | `/api/patches/preview` | Try a selection in a throwaway workspace | - |
| POST | `/api/patches/cherry-pick` | Apply onto a new branch | **pushes with `push: true`** |
| POST | `/api/pull-requests` | Open a PR for a pushed patch branch | **opens a PR** |
| GET | `/api/pull-requests/{number}` | Look up a PR | - |
| POST | `/api/upstream-md/generate` | Render `debian/upstream.md` | optional local file |
| POST | `/api/upstream-md/pr` | Propose it (dry run or real) | **pushes and opens a PR with `confirm: true`** |
| GET | `/api/upstream-md/prs` | The publish results ledger | - |
| GET | `/api/reports` | Generated reports on disk | - |
| GET | `/api/reports/{name}` | Download one | - |

## Health and catalogue

**`GET /health`** → `{"status": "ok", "version": "..."}`. Touches no git, no
GitHub, no disk.

**`GET /api/health`** →

```json
{
  "status": "ok", "version": "0.1.0",
  "capabilities": {
    "read_private_repositories": true,
    "create_pull_requests": false,
    "read_only": true,
    "git": {"backend": "local", "local_git_ssh_key": true, "ssh_configured": false, "...": "..."},
    "workspace_root": "/var/tmp/arcos-package-manager",
    "mapping": {"available": true, "path": "/app/out/upstream-resolutions.json", "rows": 53, "generated_at": "...", "statuses": {"VERIFIED": 23, "...": 0}},
    "storage": {"out_dir": "/app/out", "out_writable": true, "packages_file": "/app/out/packages.yaml", "...": "..."}
  }
}
```

`read_private_repositories` is measured (an `ls-remote` of `arrcus_rel`,
cached for 10 minutes); `create_pull_requests` is true when a token is set.

**`GET /api/health/workspace`** → the workspace root, its transport, whether it
is writable, and the git version.

**`GET /api/releases`**, **`GET /api/packages?release=bookworm`**,
**`GET /api/packages/{package}`**,
**`GET /api/branches?package=mstpd&release=bookworm`** (the fork's branches,
the shipped one flagged).

## Upstream resolution

**`GET /api/upstream/{package}?release=bookworm[&arcos_branch=...][&refresh=true]`**
→ the resolution. Served from the mapping; `refresh=true` resolves live
(slow). `arcos_branch` re-targets to another branch of the fork (404
`BRANCH_NOT_FOUND` if it does not exist).

**`POST /api/upstream/resolve`**

```json
{"package": "mstpd", "release": "bookworm", "arcos_branch": null, "refresh": false}
```

**`POST /api/upstream/verify`** - a manual upstream, still verified:

```json
{"package": "mstpd", "release": "bookworm",
 "repository": "https://github.com/mstpd/mstpd.git", "ref": "master", "arcos_branch": null}
```

**`GET /api/upstream/{package}/evidence?release=bookworm`** → the evidence
list.

**`POST /api/upstream/content-base/approve`**

```json
{"package": "rsyslog", "release": "bookworm", "tag": null,
 "verified_by": "Full Name", "note": null, "confirm": true}
```

`confirm` must be true; `tag` defaults to the best match. Takes effect at the
next resolution.

## Comparison

**`POST /api/comparison`**

```json
{"package": "mstpd", "release": "bookworm", "arcos_branch": null,
 "upstream_repository": null, "upstream_ref": null, "refresh": false}
```

Set `upstream_repository` and `upstream_ref` together to compare against a
manual upstream. Returns the summary, `missing_upstream`, `arcos_only`,
`already_backported`, Debian patches, security sources and warnings. 422
`NO_COMMON_ANCESTOR` for a fork without shared history and no approved
content match.

**`GET /api/comparison/{package}?release=bookworm[&arcos_branch=][&refresh=]`**
- the same.

**`GET /api/commits/{package}?release=bookworm&kind=missing`** - `kind`:
`missing`, `arcos_only`, `already_backported` or `all`.

## Patches

See [patch-workflow.md](patch-workflow.md) for the sequence.

**`POST /api/patches/preview`**

```json
{"package": "mstpd", "release": "bookworm", "arcos_branch": "aminor",
 "upstream_repository": "https://github.com/mstpd/mstpd.git", "upstream_ref": "master",
 "shas": ["<sha>"], "comparison_arcos_commit": "<sha>",
 "comparison_upstream_commit": "<sha>", "approved_shas": []}
```

→ `outcome` (`CLEAN`, `CONFLICT`, `EMPTY`, `FAILED`), `order`, `applied`,
`files_changed`, `conflicts`, `base_sha`, `base_moved`, `warnings`. 409
`STALE_SELECTION` if a commit is no longer in the missing set.

**`POST /api/patches/cherry-pick`** - the preview body plus:

```json
{"expected_base_sha": "<base_sha from the preview>", "branch_name": null, "push": false}
```

`expected_base_sha` is required (400 without it; 409 `BASE_MOVED` if the target
moved). The branch defaults to `upstream/<package>/<timestamp>` and may not
equal the target. Pushes only with `push: true`.

**`POST /api/pull-requests`** (token required)

```json
{"package": "mstpd", "release": "bookworm", "arcos_branch": "aminor",
 "head_branch": "upstream/mstpd/20261006-120000", "applied": ["<sha>"],
 "title": null, "body": null, "draft": false}
```

Returns the PR, or the one already open for that branch.

**`GET /api/pull-requests/{number}?package=mstpd&release=bookworm`**

## `debian/upstream.md`

**`POST /api/upstream-md/generate`**

```json
{"package": "mstpd", "release": "bookworm", "arcos_branch": null, "write_local": false}
```

→ `content`, `outcome` (`CREATED`, `UPDATED`, `NO_CHANGE`, `CONFLICT`),
`diff`, `existing_content`. 400 for a `NO_UPSTREAM` package.

**`POST /api/upstream-md/pr`**

```json
{"package": "mstpd", "release": "bookworm", "arcos_branch": null,
 "branch_name": null, "draft": false, "dry_run": true, "confirm": false}
```

- `dry_run: true` - builds and checks the exact commit; nothing is pushed.
- `confirm: true` (and `dry_run: false`) - pushes the branch and opens the PR.
  Pre-flight first: 401 `AUTH_REQUIRED` without a usable token.
- Neither - refused (400). A package that is not `VERIFIED` - refused (400).
- `branch_name` must be under `upstream-metadata/` and differ from the target.

Returns one publish result (status, branch, commit, base SHA, PR, error). A
real run is recorded in `out/upstream-md-pr-results-<release>.json`; a dry run
over the API is not recorded.

**`GET /api/upstream-md/prs?release=bookworm`** → the results ledger entries.

## Reports

**`GET /api/reports`** → the generated files available for download:
`upstream-mapping*.csv`, `upstream-mapping*.xlsx`, `upstream-resolutions.json`,
`upstream-md-plan-*.json`, `upstream-md-plan-*.md` - with size, modification
time, release and `download_url`.

**`GET /api/reports/{name}`** → the file. Only names in that listing are served.

## Examples

```bash
API=http://<server-ip>:8080/api
curl -s $API/health
curl -s "$API/packages?release=bookworm"
curl -s "$API/upstream/mstpd?release=bookworm"
curl -s "$API/comparison/mstpd?release=bookworm"
curl -s -X POST $API/upstream-md/pr -H 'Content-Type: application/json' \
     -d '{"package":"mstpd","release":"bookworm","dry_run":true}'
curl -s -OJ $API/reports/upstream-mapping-bookworm.xlsx
```
