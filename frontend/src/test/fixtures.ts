import type {
  Branch, CherryPickPreview, CherryPickResult, CommitInfo, ComparisonResult,
  DebianRelease, HealthResponse, Package, PullRequest, UpstreamMdDocument,
  UpstreamResolution,
} from '../types/api'

export const releases: DebianRelease[] = [
  { id: 'bookworm', name: 'Debian Bookworm', suite: 'oldstable', version: '12' },
  { id: 'trixie', name: 'Debian Trixie', suite: 'stable', version: '13' },
]

export const packages: Package[] = [
  {
    name: 'pyrad', arcos_repository: 'ssh://git@github.com/Arrcus/pyrad.git',
    github_repository: 'Arrcus/pyrad', submodule_path: 'packages/pyrad',
    releases: ['bookworm', 'trixie'], branches: { bookworm: 'aminor' },
    pinned_commits: {}, category: 'debian_upstream',
  },
  {
    name: 'zenoh', arcos_repository: 'ssh://git@github.com/Arrcus/zenoh.git',
    github_repository: 'Arrcus/zenoh', submodule_path: 'packages/zenoh',
    releases: ['bookworm'], branches: { bookworm: 'aminor' },
    pinned_commits: {}, category: 'third_party',
  },
]

export const branches: Branch[] = [
  { name: 'aminor', sha: null, is_default: false, is_configured: true, web_url: null },
  { name: 'main', sha: null, is_default: true, is_configured: false, web_url: null },
]

export const reports = [
  {
    name: 'upstream-mapping-bookworm.xlsx', size_bytes: 61234,
    modified_at: 1758585600, release: 'bookworm',
    download_url: '/api/reports/upstream-mapping-bookworm.xlsx',
  },
  {
    name: 'upstream-mapping-bookworm.csv', size_bytes: 43376,
    modified_at: 1758585600, release: 'bookworm',
    download_url: '/api/reports/upstream-mapping-bookworm.csv',
  },
  {
    name: 'upstream-mapping.csv', size_bytes: 98765,
    modified_at: 1758585600, release: '',
    download_url: '/api/reports/upstream-mapping.csv',
  },
  // Present in the listing (the backend endpoint still serves it) but must
  // never surface as a link in the dashboard - it is internal, pre-PR state.
  {
    name: 'upstream-md-plan-bookworm.json', size_bytes: 21522,
    modified_at: 1758585600, release: 'bookworm',
    download_url: '/api/reports/upstream-md-plan-bookworm.json',
  },
]

export const health: HealthResponse = {
  status: 'ok', version: '0.1.0',
  capabilities: { create_pull_requests: true, read_private_repositories: true },
}

export const healthNoToken: HealthResponse = {
  ...health, capabilities: { create_pull_requests: false },
}

export const resolution: UpstreamResolution = {
  package: 'pyrad', debian_release: 'bookworm', status: 'VERIFIED', mode: 'AUTO',
  method: 'dep12', category: 'debian_upstream', confidence: 'high',
  arcos_repository: 'ssh://git@github.com/Arrcus/pyrad.git',
  github_repository: 'Arrcus/pyrad', arcos_branch: 'aminor',
  arcos_commit: '3b043f16bb8ed1f0',
  arcos_path: 'packages/pyrad', arcos_release: 'aminor', debian: null,
  upstream_repository: {
    url: 'https://github.com/wichert/pyrad.git', host: null, owner: null,
    name: null, web_url: 'https://github.com/wichert/pyrad', is_packaging: false,
  },
  upstream_ref: 'master', upstream_branch: 'master', upstream_tag: null,
  upstream_commit: '074aa3d339992fc3',
  origin_kind: 'project', merge_base: '984ad177f02d', behind: 109, arcos_only: 19,
  reason: null,
  evidence_source: 'debian/upstream/metadata (DEP-12) Repository:',
  evidence_url: 'https://github.com/wichert/pyrad.git',
  verification: 'git merge-base: the fork shares history with master',
  evidence: [{ kind: 'dep12', detail: 'debian/upstream/metadata Repository field', url: null }],
  candidates: [], notes: [], resolved_at: null,
}

export const needsReview: UpstreamResolution = {
  ...resolution, package: 'babeltrace', status: 'NEEDS_REVIEW',
  confidence: 'low', merge_base: null, behind: null, arcos_only: null,
  notes: [
    'The ARCoS fork has no commit in common with any candidate upstream, so it '
    + 'was imported rather than forked.',
  ],
  evidence: [
    { kind: 'ancestry', detail: 'shares no history with the ARCoS fork', url: null },
  ],
}

export const noUpstream: UpstreamResolution = {
  ...resolution, package: 'bcmsdk', status: 'NO_UPSTREAM', category: 'vendor',
  method: 'not_applicable', upstream_repository: null, upstream_ref: null,
  notes: ['No upstream exists for this package; it is vendor.'],
  evidence: [],
}

const commit = (over: Partial<CommitInfo>): CommitInfo => ({
  sha: 'a'.repeat(40), short_sha: 'a'.repeat(12), subject: 'a commit',
  author_name: 'Ada', author_email: 'ada@example.invalid',
  authored_at: '2026-01-02T03:04:05+00:00', body: '',
  classification: 'MISSING_UPSTREAM',
  criticality: { level: 'UNKNOWN', evidence: [], cve_ids: [], fixes: [], cc_stable: false },
  patch: { patch_id: null, equivalent_sha: null, equivalent_subject: null },
  web_url: null, ...over,
})

export const criticalCommit = commit({
  sha: 'c'.repeat(40), short_sha: 'c'.repeat(12), subject: 'fix buffer overflow',
  criticality: {
    level: 'CRITICAL', evidence: ['references CVE-2026-12345'],
    cve_ids: ['CVE-2026-12345'], fixes: [], cc_stable: false,
  },
})

export const unknownCommit = commit({
  sha: 'd'.repeat(40), short_sha: 'd'.repeat(12), subject: 'refactor parser',
})

export const backportedCommit = commit({
  sha: 'e'.repeat(40), short_sha: 'e'.repeat(12), subject: 'already applied',
  classification: 'ALREADY_BACKPORTED',
  patch: { patch_id: 'p1', equivalent_sha: 'f'.repeat(40), equivalent_subject: 'same change' },
})

export const arcosCommit = commit({
  sha: '9'.repeat(40), short_sha: '9'.repeat(12), subject: 'arcos local change',
  classification: 'ARCOS_ONLY',
})

export const comparison: ComparisonResult = {
  package: 'pyrad', debian_release: 'bookworm',
  arcos_repository: 'ssh://git@github.com/Arrcus/pyrad.git', arcos_branch: 'aminor',
  arcos_commit: '3b043f16bb8e',
  upstream_repository: 'https://github.com/wichert/pyrad.git', upstream_ref: 'master',
  upstream_commit: '074aa3d33999',
  summary: {
    merge_base: '984ad177f02d', has_common_ancestor: true, missing_upstream: 2,
    arcos_only: 1, already_backported: 1, critical: 1, stable_relevant: 0,
    normal: 0, unknown_criticality: 1, truncated: false,
  },
  missing_upstream: [criticalCommit, unknownCommit],
  arcos_only: [arcosCommit],
  already_backported: [backportedCommit],
  computed_at: null, warnings: [],
}

export const cleanPreview: CherryPickPreview = {
  outcome: 'CLEAN', package: 'pyrad', arcos_branch: 'aminor',
  applied: [criticalCommit.sha], failed_sha: null, conflicts: [],
  files_changed: [{ path: 'src/a.c', status: '', insertions: 3, deletions: 1 }],
  order: [criticalCommit.sha], warnings: [],
  message: 'All 1 commits apply cleanly.', workspace_removed: true,
}

export const conflictPreview: CherryPickPreview = {
  ...cleanPreview, outcome: 'CONFLICT', applied: [],
  failed_sha: criticalCommit.sha, conflicts: ['src/a.c'], files_changed: [],
  message: 'cccccccccccc does not apply cleanly onto aminor.',
}

export const cherryPickResult: CherryPickResult = {
  outcome: 'CLEAN', package: 'pyrad', base_branch: 'aminor',
  new_branch: 'upstream/pyrad/20260923-000000', applied: [criticalCommit.sha],
  head_sha: 'b'.repeat(40), pushed: true, conflicts: [], failed_sha: null,
  message: 'Applied 1 commits to upstream/pyrad/20260923-000000.',
}

export const pullRequest: PullRequest = {
  number: 42, url: 'https://github.com/Arrcus/pyrad/pull/42', state: 'open',
  title: 'pyrad: pull 1 upstream commit', body: '', base: 'aminor',
  head: 'upstream/pyrad/20260923-000000', draft: false, already_existed: false,
}

export const upstreamMd: UpstreamMdDocument = {
  package: 'pyrad', debian_release: 'bookworm', path: 'debian/upstream.md',
  content: '# pyrad\n\n## Upstream\n', outcome: 'CREATED',
  existing_content: null, diff: null, local_path: null,
}
