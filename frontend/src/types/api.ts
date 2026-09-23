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
}

export interface CherryPickRequest {
  package: string
  release: string
  arcos_branch: string
  upstream_repository: string
  upstream_ref: string
  shas?: string[]
  branch_name?: string | null
  /** Push the new branch. Never happens without this. */
  push?: boolean
}

export interface CherryPickResult {
  outcome: PreviewOutcome
  package: string
  base_branch: string
  new_branch: string
  applied: string[]
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
  computed_at: string | null
  warnings: string[]
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
}

export type Criticality = "CRITICAL" | "STABLE_RELEVANT" | "NORMAL" | "UNKNOWN"

export interface CriticalityAssessment {
  level: Criticality
  evidence: string[]
  cve_ids: string[]
  fixes: string[]
  cc_stable: boolean
}

export interface DebianRelease {
  id: string
  name: string
  suite: string
  version: string
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
}

export type PreviewOutcome = "CLEAN" | "CONFLICT" | "EMPTY" | "FAILED"

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

export interface UpstreamCandidate {
  repository: string
  ref: string | null
  source: ResolutionMethod
  accepted: boolean
  shares_history: boolean | null
  rejected_reason: string | null
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
  branch_name?: string | null
  draft?: boolean
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
  behind: number | null
  arcos_only: number | null
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
