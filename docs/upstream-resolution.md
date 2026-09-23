# Upstream resolution

## The problem

Debian does not record where a package's code lives in one reliable field, and
ARCoS forks are not all forks of the same kind of thing. Some track the upstream
project; others track the Debian packaging repository; a few were imported from
tarballs and share no history with anything.

Guessing is worse than saying nothing: a confident but wrong upstream produces a
diff that looks authoritative and means nothing.

## The chain

Ordered. First hit wins, and every step records why.

| Method | Source | Confidence |
|---|---|---|
| `curated` | `config/overrides.yaml` | as declared |
| `kernel_series` | Debian kernel version → `linux-X.Y.y` | high |
| `dep12` | `debian/upstream/metadata` `Repository:` | high |
| `watch_forge` | `debian/watch`, only when it names a git forge | medium |
| `homepage_forge` | `Homepage:` normalised to a forge repo | medium |

`Vcs-Git` is never treated as upstream by the chain — it is the Debian
*packaging* repository, carried in its own column instead.

A URL only becomes a repository when the conversion is justified.
`https://lldpd.github.io` is refused: the organisation is knowable, the
repository is not, and inferring `lldpd/lldpd` from it would be a coincidence.

## Ancestry decides

Metadata gives a plausible upstream. Shared history proves it.

```
lttng-tools   github.com/lttng/lttng-tools        NO SHARED HISTORY
              salsa.debian.org/debian/ltt-control merge-base 59bc0559, 21 behind
```

ARCoS forked the **Debian packaging repository** for `lttng-tools`, `liburcu`,
`lttng-ust` and `lldpd`, and the **project's own repository** for `openssl`,
`pyrad`, `iproute2` and most others. Nothing in the metadata distinguishes the
two, so `AncestryChecker` measures it with `git merge-base` against every
candidate — including the packaging repository — and the one that shares history
wins.

Where several refs of the winning repository share history, the one fewest
commits behind wins. That is how `openssl` lands on `openssl-3.0` (10,265
behind) rather than `master` (18,423).

Repository order is respected before commit counts, so a fork that really does
descend from the project's own repository is not reassigned to the packaging
repository merely because that happens to be closer.

## Status

| Status | Meaning |
|---|---|
| `VERIFIED` | Upstream contacted, ref resolved, history shared where probed |
| `PARTIAL` | Resolved, but not fully confirmed |
| `NEEDS_REVIEW` | Exists, but the ref was inferred or no candidate shared history |
| `NO_UPSTREAM` | Searched, and there is provably nothing to track |
| `FAILED` | Resolution errored |

`VERIFIED` is the requirement's `RESOLVED`. The name is kept because it says
something stronger and true: the upstream was not merely selected, it was
checked.

**Category** is separate from status and answers "what kind of package is this":
`debian_upstream`, `debian_no_upstream`, `third_party`, `arrcus_native`,
`vendor`. A vendor blob with no upstream is a finished answer, not a failure —
collapsing the two into one column is what once buried the two genuinely
unresolved packages among twenty-two that never had an upstream to find.

## Manual resolution

Manual does not mean unchecked. A user-supplied repository and ref go through
the same verification: the repository must be reachable, the ref must exist, and
the ancestry check still runs. If it shares no history with the fork, the
resolution comes back `NEEDS_REVIEW` saying so, rather than being accepted
silently.

## Adding an override

```yaml
packages:
  net-snmp:
    repository: https://github.com/net-snmp/net-snmp.git
    ref: master
    confidence: high
    reason: >-
      Debian's watch file points at SourceForge, where releases are published
      rather than developed.
```

Keys: `repository`, `ref`, `confidence`, `debian_source_package` (or `null` for
"deliberately not a Debian package"), `no_upstream: true`, `category`, and a
`releases:` block for per-release values.

An override is still checked. Ancestry runs against it too and will correct it —
which is how `lldpd` moved from a hand-written GitHub guess to the salsa
repository it actually descends from.
