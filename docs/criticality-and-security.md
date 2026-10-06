# Criticality and security detection

Which missing upstream commits matter? The tool answers only from evidence. A
commit is never called critical because its subject sounds alarming: "fix" and
"crash" appear in ordinary commits constantly, and a keyword heuristic would
bury the commits that matter under noise.

## The levels

| Level | Assigned when | Meaning |
|---|---|---|
| `CRITICAL` | the message names a CVE (`CVE-YYYY-NNNN...`), or an advisory (`DSA-`, `DLA-`, `USN-`, `GHSA-`, `RHSA-`), or carries an explicit trailer `Security:`, `Security-fix:` or `Vulnerability:`; **or** any external source below names the commit as a security fix | Security relevance is established |
| `STABLE_RELEVANT` | a `Cc: ...stable@...` line | Upstream asked for it in maintenance releases |
| `NORMAL` | a `Fixes: <sha>` trailer | A real bug fix, with no security evidence |
| `UNKNOWN` | none of the above | Nobody has established anything. **Not the same as safe** |

The strongest evidence wins. The code is `src/apm/services/criticality_service.py`
(commit messages) and `src/apm/services/security_service.py` (external
sources).

## External evidence

Commit messages are one signal. Two sources are stronger, because they do not
depend on how a commit was written:

### Debian (always on)

From the Debian source package of the shipped version:

- **Security patches**: a quilt patch referencing a CVE. When its DEP-3
  `Origin:` names the upstream commit it came from, that upstream commit
  becomes `CRITICAL` (source `debian-patch`), and the finding says whether the
  fix is missing from ARCoS or present.
- **Changelog**: CVEs named in Debian uploads of this upstream version (source
  `debian-changelog`). These name no commit, so their status is `unknown`.

### OSV.dev (on by default)

`config/settings.yaml` → `security.osv`. The tool asks OSV.dev which
vulnerabilities affect the upstream commits ARCoS is based on (up to three
merge bases, or the approved content base), and collects the fix commits each
record names - from `GIT` ranges in the same repository and from `FIX`
references. A missing commit named as a fix becomes `CRITICAL` (source `osv`).
Answers are cached for a day.

### Finding status

Each external finding says where its fix stands:

| Status | Meaning |
|---|---|
| `missing` | the fix commit is in the missing set |
| `present` | ARCoS already has it |
| `not_in_range` | the fix is not on the compared upstream ref |
| `unknown` | no fix commit named, or it could not be located |

### Unavailable is not "no vulnerabilities"

A source that cannot answer - no Debian source package, OSV unreachable, no
base commit to ask about - is reported as **unavailable**, with the reason.
No finding leaves a commit `UNKNOWN`, never "safe".

## Where it shows up

| Place | What you see |
|---|---|
| Dashboard, Comparison card | **Critical/security missing** and **Stable-nominated** counts; **Critical**, **Stable**, **Normal**, **Unknown** filters; per commit the level and its evidence; **External security evidence** |
| Dashboard | **Select critical (N)** selects every missing commit with security evidence - the only automatic selection there is |
| `apm compare` | `critical missing N   stable N`, and one `security source` line per provider (`N finding(s)` or `unavailable (...)`) |
| `debian/upstream.md` | Backlog: `Critical/security missing`, `Stable-nominated missing`; Debian patches: security patches and CVEs referenced |
| Patch PR body | each applied commit with its level and evidence |

## How to read it

- `CRITICAL` with a `missing` status: review first.
- `STABLE_RELEVANT`: upstream considered it suitable for maintenance releases;
  a good candidate.
- `UNKNOWN`: nothing is known either way. A large number of unknown commits
  means "look", not "fine".
- A security source shown as unavailable means its evidence is absent from the
  result; re-run the comparison when it is reachable.
