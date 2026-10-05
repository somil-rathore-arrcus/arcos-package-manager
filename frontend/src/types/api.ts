// GENERATED from the FastAPI OpenAPI schema - do not edit by hand.
// Regenerate: ./.venv/bin/python scripts/generate_frontend_types.py

export interface Branch {
  name: string
  sha: string | null
  is_default: boolean
  /** Named by the release manifest for this package. */
  is_configured: boolean
  web_url: string | null
}

export interface CherryPickPreview {
  outcome: PreviewOutcome
  package: string
  arcos_branch: string
  applied: string[]
  failed_sha: string | null
  conflicts: string[]
  files_changed: FileChange[]
  order: string[]
  warnings: string[]
  message: string | null
  workspace_removed: boolean
  /** The target branch tip this preview applied onto. Pass it to cherry-pick as expected_base_sha. */
  base_sha: string | null
  upstream_sha: string | null
  /** The target branch is not the commit the comparison was computed against. */
  base_moved: boolean
  /** Every selected commit was re-checked against the current target tip and upstream ref. */
  validated: boolean
}

export interface CherryPickRequest {
  package: string
  release: string
  arcos_branch: string
  upstream_repository: string
  upstream_ref: string
  shas?: string[]
  /** The ARCoS commit the comparison was computed against. */
  comparison_arcos_commit?: string | null
  comparison_upstream_commit?: string | null
  /** Commits deliberately selected from outside the current missing set. */
  approved_shas?: string[]
  branch_name?: string | null
  /** Push the new branch. Never happens without this. */
  push?: boolean
  /** base_sha from the preview. Required: the cherry-pick is refused if the target branch has moved since. */
  expected_base_sha?: string | null
}

export interface CherryPickResult {
  outcome: PreviewOutcome
  package: string
  base_branch: string
  new_branch: string
  applied: string[]
  base_sha: string | null
  head_sha: string | null
  pushed: boolean
  conflicts: string[]
  failed_sha: string | null
  message: string | null
}

export type CommitClass = "MISSING_UPSTREAM" | "ARCOS_ONLY" | "ALREADY_BACKPORTED"

export interface CommitInfo {
  sha: string
  short_sha: string
  subject: string
  author_name: string
  author_email: string
  authored_at: string | null
  body: string
  classification: CommitClass
  criticality: CriticalityAssessment
  patch: PatchInfo
  /** Upstream commits only: is the change already in ARCoS? */
  presence: Presence | null
  presence_evidence: string[]
  /** Reachable from the upstream tag for the shipped Debian version, i.e. part of the release itself. */
  in_base_release: boolean | null
  reverts: string | null
  reverted_by: string | null
  web_url: string | null
}

export interface ComparisonRequest {
  package: string
  release: string
  arcos_branch?: string | null
  /** Set together with upstream_ref to compare a manual upstream. */
  upstream_repository?: string | null
  upstream_ref?: string | null
  refresh?: boolean
}

export interface ComparisonResult {
  package: string
  debian_release: string
  arcos_repository: string
  arcos_branch: string
  arcos_commit: string | null
  upstream_repository: string
  upstream_ref: string
  upstream_commit: string | null
  summary: ComparisonSummary
  missing_upstream: CommitInfo[]
  arcos_only: CommitInfo[]
  already_backported: CommitInfo[]
  debian_patches: DebianPatchReport | null
  security: SecurityFinding[]
  /** External sources consulted, and whether each answered. */
  security_sources: string[]
  computed_at: string | null
  warnings: string[]
}

export interface ComparisonSnapshot {
  arcos_commit: string
  upstream_repository: string
  upstream_ref: string
  upstream_commit: string
  computed_at: string | null
  base_tag: string | null
  series: string | null
  synthesized_ancestry: boolean
  relevant_upstream: number
  definitely_present: number
  probably_present: number
  missing: number
  unknown_presence: number
  reverted_upstream: number
  critical_missing: number
  stable_missing: number
  arcos_only: number
  backport_detection: string
  truncated: boolean
}

export interface ComparisonSummary {
  merge_base: string | null
  has_common_ancestor: boolean
  missing_upstream: number
  arcos_only: number
  already_backported: number
  critical: number
  stable_relevant: number
  normal: number
  unknown_criticality: number
  truncated: boolean
  merge_bases: string[]
  base_tag: string | null
  series: string | null
  upstream_commit_date: string | null
  counts_basis: string
  packaging_commits_included: boolean
  synthesized_ancestry: boolean
  synthetic_base: string | null
  relevant_upstream: number
  definitely_present: number
  probably_present: number
  missing: number
  unknown_presence: number
  reverted_upstream: number
  /** Missing commits that are part of the shipped release tag. */
  missing_in_base_release: number | null
  backport_detection: string
}

export interface ContentBaseApprovalRequest {
  package: string
  release: string
  /** Default: the best content match. */
  tag?: string | null
  /** The person accountable for the decision. */
  verified_by: string
  note?: string | null
  /** Must be true. */
  confirm?: boolean
}

export interface ContentMatch {
  method: string
  arcos_tree: string | null
  arcos_tree_label: string
  base_tag: string | null
  base_sha: string | null
  score: number | null
  files_compared: number | null
  files_differing: number | null
  lines_differing: number | null
  candidates: ContentMatchCandidate[]
  approved: boolean
  verified_by: string | null
  verified_at: string | null
  approval_source: string | null
  notes: string[]
}

export interface ContentMatchCandidate {
  tag: string
  sha: string | null
  method: string
  /** The ARCoS commit whose tree was compared. */
  arcos_tree: string | null
  files_compared: number
  files_differing: number
  lines_added: number | null
  lines_removed: number | null
  /** 1.0 is an identical tree. */
  score: number
}

export type Criticality = "CRITICAL" | "STABLE_RELEVANT" | "NORMAL" | "UNKNOWN"

export interface CriticalityAssessment {
  level: Criticality
  evidence: string[]
  cve_ids: string[]
  fixes: string[]
  cc_stable: boolean
  /** Which kinds of evidence contributed: commit-message, debian-patch, osv. */
  sources: string[]
  external: SecurityEvidence[]
}

export interface CuratedUpstream {
  repository: string | null
  ref: string | null
  reason: string
  /** Set when another repository shares history and the curated one does not. */
  conflict: string | null
  conflicting_repository: string | null
  conflicting_ref: string | null
  replacement_allowed: boolean
}

export interface DebianPatch {
  name: string
  subject: string
  origin: string | null
  /** From DEP-3 Origin:, when it names an upstream commit. */
  upstream_commit: string | null
  cve_ids: string[]
  bugs: string[]
  forwarded: string | null
  /** security | upstream_backport | debian_specific | unknown */
  category: string
  /** Whether the ARCoS tree already carries it; set by a comparison. */
  presence: Presence | null
  presence_evidence: string[]
}

export interface DebianPatchReport {
  source_package: string
  version: string
  release: string
  available: boolean
  reason: string | null
  format: string | null
  /** Uploads of this upstream version, newest first. */
  uploads: DebianUpload[]
  cve_ids: string[]
  patches: DebianPatch[]
}

export interface DebianRelease {
  id: string
  name: string
  suite: string
  version: string
}

export interface DebianUpload {
  version: string
  distribution: string
  urgency: string
  date: string | null
  cve_ids: string[]
  security: boolean
}

export interface FileChange {
  path: string
  status: string
  insertions: number
  deletions: number
}

export interface HealthResponse {
  status: string
  version: string
  capabilities: Record<string, unknown>
}

export interface ManualUpstreamRequest {
  package: string
  release: string
  /** Upstream git repository URL */
  repository: string
  /** A branch or tag in that repository */
  ref: string
  arcos_branch?: string | null
}

export interface Package {
  name: string
  arcos_repository: string
  github_repository: string
  submodule_path: string
  releases: string[]
  branches: Record<string, string>
  /** Commit each release's manifest pins. Authoritative: the branch field in .gitmodules can be stale. */
  pinned_commits: Record<string, string>
  category: PackageCategory | null
}

export type PackageCategory = "debian_upstream" | "debian_no_upstream" | "third_party" | "arrcus_native" | "vendor"

export interface PackageSource {
  source_package: string
  debian_release: string
  version: string
  suite: string
  directory: string
  vcs_git: string | null
  /** The branch Vcs-Git names inline ('<url> -b debian/master'). It is a branch of the PACKAGING repository, never of the upstream. */
  vcs_branch: string | null
  vcs_browser: string | null
  homepage: string | null
  dep12_repository: string | null
  watch_urls: string[]
  web_url: string | null
}

export interface PatchInfo {
  patch_id: string | null
  /** The ARCoS commit carrying the same change. */
  equivalent_sha: string | null
  equivalent_subject: string | null
}

export interface PatchRequest {
  package: string
  release: string
  arcos_branch: string
  upstream_repository: string
  upstream_ref: string
  shas?: string[]
  /** The ARCoS commit the comparison was computed against. */
  comparison_arcos_commit?: string | null
  comparison_upstream_commit?: string | null
  /** Commits deliberately selected from outside the current missing set. */
  approved_shas?: string[]
}

export type Presence = "DEFINITELY_PRESENT" | "PROBABLY_PRESENT" | "MISSING" | "UNKNOWN"

export type PreviewOutcome = "CLEAN" | "CONFLICT" | "EMPTY" | "FAILED"

export interface PublishResult {
  package: string
  release: string
  status: PublishStatus
  /** owner/name on GitHub */
  repository: string
  base_branch: string
  branch: string
  /** The commit the release manifest pins. */
  pinned_commit: string | null
  /** The target branch tip the commit was built on. */
  base_sha: string | null
  /** The target branch has moved past the pinned commit. */
  base_moved: boolean
  outcome: UpstreamMdOutcome | null
  commit: string | null
  pushed: boolean
  title: string
  pull_request: PullRequest | null
  error: string | null
  diff: string | null
  updated_at: string | null
}

export type PublishStatus = "DRY_RUN_OK" | "PR_OPENED" | "PR_EXISTS" | "PR_MERGED" | "PR_CLOSED" | "NO_CHANGE" | "SKIPPED_NO_UPSTREAM" | "SKIPPED_NEEDS_REVIEW" | "SKIPPED_EXCLUDED" | "CONFLICT" | "DRIFT" | "BRANCH_EXISTS" | "BASE_UNREADABLE" | "PUSH_FAILED" | "PR_FAILED" | "ERROR"

export interface PullRequest {
  number?: number | null
  url?: string | null
  state?: string | null
  title?: string
  body?: string
  base?: string
  head?: string
  draft?: boolean
  already_existed?: boolean
}

export interface PullRequestRequest {
  package: string
  release: string
  arcos_branch: string
  head_branch: string
  title?: string | null
  body?: string | null
  draft?: boolean
  applied?: string[]
}

export interface RefSelection {
  ref: string
  /** branch | tag */
  kind: string
  strategy: RefStrategy
  sha: string | null
  reason: string
  /** The Debian version with epoch, revision and repack suffixes removed. */
  debian_upstream_version: string | null
  /** major.minor, e.g. 6.1 */
  series: string | null
  /** The upstream tag for the shipped Debian version. */
  base_tag: string | null
  base_sha: string | null
  arcos_contains_base: boolean | null
  is_fallback: boolean
}

export type RefStrategy = "exact_tag" | "maintenance_branch" | "kernel_series" | "packaging_tag" | "packaging_branch" | "curated" | "manual" | "packaging_fallback" | "default_branch"

export interface ReportFile {
  name: string
  size_bytes: number
  modified_at: number
  release: string
  download_url: string
}

export interface Repository {
  url: string
  host: string | null
  owner: string | null
  name: string | null
  web_url: string | null
  /** True when this is a Debian packaging repository rather than the project's own. Pulling from one brings packaging changes, from the other it brings upstream code. */
  is_packaging: boolean
}

export interface ResolutionEvidence {
  /** dep12 | watch | homepage | git | ancestry | curated */
  kind: string
  detail: string
  url: string | null
}

export type ResolutionMethod = "ancestry" | "curated" | "kernel_series" | "dep12" | "watch_forge" | "homepage_forge" | "manual" | "unresolved" | "not_applicable"

export type ResolutionMode = "AUTO" | "MANUAL"

export type ResolutionStatus = "VERIFIED" | "PARTIAL" | "NEEDS_REVIEW" | "NO_UPSTREAM" | "FAILED"

export interface ResolveRequest {
  package: string
  release: string
  arcos_branch?: string | null
  refresh?: boolean
}

export type ReviewReason = "NO_CANDIDATE" | "NO_SHARED_HISTORY" | "CURATED_CONFLICT" | "INVALID_REF" | "ANCESTRY_NOT_PROBED" | "CONTENT_MATCH_UNAPPROVED" | "PROBE_FAILED" | "NETWORK_ERROR" | "ARCOS_UNREACHABLE"

export interface SecurityEvidence {
  /** commit-message | debian-patch | debian-changelog | osv */
  source: string
  identifier: string
  detail: string
  url: string | null
  commit: string | null
}

export interface SecurityFinding {
  identifier: string
  aliases: string[]
  sources: string[]
  summary: string
  fix_commits: string[]
  /** missing | present | not_in_range | unknown - of the fix commits, relative to ARCoS */
  status: string
  url: string | null
}

export interface UpstreamCandidate {
  repository: string
  ref: string | null
  source: ResolutionMethod
  accepted: boolean
  shares_history: boolean | null
  rejected_reason: string | null
  strategy: RefStrategy | null
  /** branch | tag */
  kind: string | null
  sha: string | null
  /** git rev-list --count --no-merges ARCOS..CANDIDATE */
  behind: number | null
  /** git rev-list --count --no-merges CANDIDATE..ARCOS */
  arcos_only: number | null
  /** The candidate commit is an ancestor of the ARCoS commit. */
  in_arcos: boolean | null
  error: string | null
}

export interface UpstreamMdDocument {
  package: string
  debian_release: string
  path: string
  content: string
  outcome: UpstreamMdOutcome
  existing_content: string | null
  diff: string | null
  local_path: string | null
}

export type UpstreamMdOutcome = "CREATED" | "UPDATED" | "NO_CHANGE" | "CONFLICT"

export interface UpstreamMdPrRequest {
  package: string
  release: string
  arcos_branch?: string | null
  /** Must be under upstream-metadata/ and not the target branch. */
  branch_name?: string | null
  draft?: boolean
  /** Build and check the exact commit, then stop: nothing is pushed and no pull request is opened. */
  dry_run?: boolean
  /** Must be true. Opening a pull request is an explicit act, never a side effect of generating the file. */
  confirm?: boolean
}

export interface UpstreamMdRequest {
  package: string
  release: string
  arcos_branch?: string | null
  /** Also write the file under the local output directory. */
  write_local?: boolean
}

export interface UpstreamResolution {
  package: string
  debian_release: string
  status: ResolutionStatus
  mode: ResolutionMode
  method: ResolutionMethod
  category: PackageCategory | null
  confidence: string
  arcos_repository: string
  github_repository: string
  arcos_branch: string
  arcos_commit: string | null
  /** Where the package sits in the release manifest. */
  arcos_path: string
  /** The manifest branch this package list came from. */
  arcos_release: string
  debian: PackageSource | null
  upstream_repository: Repository | null
  upstream_ref: string | null
  upstream_branch: string | null
  upstream_tag: string | null
  upstream_commit: string | null
  /** project | debian_packaging */
  origin_kind: string | null
  merge_base: string | null
  /** git merge-base --all */
  merge_bases: string[]
  behind: number | null
  arcos_only: number | null
  counts_basis: string
  upstream_commit_date: string | null
  verification_level: VerificationLevel
  review_reasons: ReviewReason[]
  /** Plausibility checks that did not block the answer but deserve a look. */
  warnings: string[]
  ref_selection: RefSelection | null
  curated: CuratedUpstream | null
  content_match: ContentMatch | null
  debian_patches: DebianPatchReport | null
  /** From a comparison run against exactly these commits. */
  comparison: ComparisonSnapshot | null
  reason: string | null
  /** What was read to reach this answer. */
  evidence_source: string
  /** Where that can be read again. */
  evidence_url: string
  /** How the answer was checked, in words. */
  verification: string
  evidence: ResolutionEvidence[]
  candidates: UpstreamCandidate[]
  notes: string[]
  resolved_at: string | null
}

export type VerificationLevel = "NONE" | "REF_EXISTS" | "SHARED_HISTORY" | "CONTENT_MATCH_APPROVED"
