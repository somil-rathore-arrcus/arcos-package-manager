// The single place the frontend talks to the backend.
//
// Errors carry the backend's ErrorCode so the UI can react to the specific
// problem - a missing branch, a missing token, no common ancestor - instead of
// showing one generic failure for all of them.
//
// Nothing here opens, submits or merges a pull request: the tool pushes
// branches, and a person opens each pull request from its Compare Link.

import type {
  Branch, CherryPickPreview, CherryPickRequest, CherryPickResult,
  ComparisonRequest, ComparisonResult, ContentBaseApprovalRequest,
  DebianRelease, HealthResponse,
  ManualUpstreamRequest, Package, PublishResult, PublishStatusReport,
  ReportFile, ResolutionEvidence, UpstreamMdDocument, UpstreamMdPrRequest,
  UpstreamMdRequest, UpstreamResolution,
} from '../types/api'

const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

// The schema types /health capabilities as a free-form object; these are the
// keys the UI acts on. Anything not stated is treated as not allowed.
export interface Capabilities {
  /** Always "never": the tool pushes branches and a person opens each pull request. */
  pull_requests?: 'never'
  /** False when this deployment refuses every push (PUSH_DISABLED). */
  push_enabled?: boolean
  /** Equals !push_enabled. */
  read_only?: boolean
  /** Always false; kept for older clients and never acted on. */
  create_pull_requests?: boolean
  mapping?: { rows?: number; generated_at?: string | null }
}

export type Health = HealthResponse & { capabilities: Capabilities }

// Every request is bounded. Without this a stalled git or SSH operation leaves
// the UI spinning with no way to tell "slow" from "never going to finish".
// Comparison legitimately takes minutes on a first run, so it gets its own
// budget rather than forcing the quick calls to wait as long.
const QUICK_TIMEOUT_MS = 30_000
// Deliberately longer than the backend's own git ceiling (APM_GIT_LONG_TIMEOUT,
// 900s). If the client gave up first the user would see a generic client-side
// timeout instead of the backend's explicit TIMEOUT and its explanation.
const SLOW_TIMEOUT_MS = 20 * 60_000

// This deployment's own API token (APM_API_TOKEN), typed by the person using
// the dashboard when a write first asks for it. It is kept for this browser tab
// only and sent only to this API; it is never part of the built code.
const TOKEN_KEY = 'apm.apiToken'
let tokenInMemory = ''

export function apiToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? tokenInMemory
  } catch {
    return tokenInMemory
  }
}

export function setApiToken(token: string): void {
  tokenInMemory = token
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // Storage is unavailable here; the in-memory copy serves this page.
  }
}

export class ApiError extends Error {
  code: string
  detail?: string | null
  status: number

  constructor(message: string, code: string, status: number, detail?: string | null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.detail = detail
  }
}

async function request<T>(
  path: string, init?: RequestInit, timeoutMs = QUICK_TIMEOUT_MS,
): Promise<T> {
  let response: Response
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const token = apiToken()
    response = await fetch(`${BASE}${path}`, {
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      signal: controller.signal,
      ...init,
    })
  } catch (cause) {
    const aborted = cause instanceof DOMException && cause.name === 'AbortError'
    throw new ApiError(
      aborted
        ? `The request timed out after ${Math.round(timeoutMs / 1000)}s.`
        : 'Could not reach the ARCoS Package Manager API.',
      aborted ? 'TIMEOUT' : 'NETWORK', 0, String(cause),
    )
  } finally {
    clearTimeout(timer)
  }

  if (!response.ok) {
    let code = 'INTERNAL'
    let message = `Request failed (HTTP ${response.status}).`
    let detail: string | null = null
    try {
      const body = await response.json()
      code = body.code ?? code
      message = body.message ?? body.detail?.message ?? message
      detail = body.detail ?? null
    } catch {
      // A non-JSON body is itself the only detail available.
      detail = await response.text().catch(() => null)
    }
    throw new ApiError(message, code, response.status, detail)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function post<T>(path: string, body: unknown, timeoutMs?: number): Promise<T> {
  return request<T>(
    path, { method: 'POST', body: JSON.stringify(body) }, timeoutMs,
  )
}

const query = (params: Record<string, string | boolean | undefined>) => {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value))
    }
  })
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const api = {
  health: () => request<Health>('/health'),

  releases: () => request<DebianRelease[]>('/releases'),

  packages: (release?: string) =>
    request<Package[]>(`/packages${query({ release })}`),

  // Branch discovery contacts the fork, so it needs more than the quick budget.
  branches: (pkg: string, release: string) =>
    request<Branch[]>(`/branches${query({ package: pkg, release })}`, undefined, 60_000),

  upstream: (pkg: string, release: string, arcosBranch?: string, refresh?: boolean) =>
    request<UpstreamResolution>(
      `/upstream/${encodeURIComponent(pkg)}${query({
        release, arcos_branch: arcosBranch, refresh,
      })}`,
      undefined,
      // A refresh bypasses the mapping and resolves live, which is slow.
      refresh ? SLOW_TIMEOUT_MS : QUICK_TIMEOUT_MS,
    ),

  verifyManualUpstream: (body: ManualUpstreamRequest) =>
    post<UpstreamResolution>('/upstream/verify', body, SLOW_TIMEOUT_MS),

  evidence: (pkg: string, release: string) =>
    request<ResolutionEvidence[]>(
      `/upstream/${encodeURIComponent(pkg)}/evidence${query({ release })}`,
    ),

  compare: (body: ComparisonRequest) =>
    post<ComparisonResult>('/comparison', body, SLOW_TIMEOUT_MS),

  preview: (body: CherryPickRequest) =>
    post<CherryPickPreview>('/patches/preview', body, SLOW_TIMEOUT_MS),

  cherryPick: (body: CherryPickRequest) =>
    post<CherryPickResult>('/patches/cherry-pick', body, SLOW_TIMEOUT_MS),

  generateUpstreamMd: (body: UpstreamMdRequest) =>
    post<UpstreamMdDocument>('/upstream-md/generate', body),

  // Dry-runs or pushes debian/upstream.md exactly as the reviewed plan says:
  // the target and branch name are the plan's, never the client's. Returns
  // what the publisher did; a refusal (DRIFT, BRANCH_EXISTS, CONFLICT...) or
  // COVERED is a result for a person to read, not an HTTP error. The path is
  // historical: no pull request is ever opened.
  publishUpstreamMd: (body: UpstreamMdPrRequest) =>
    post<PublishResult>('/upstream-md/pr', body, SLOW_TIMEOUT_MS),

  // Where a pushed branch stands: on the fork, any PR opened from it (from git
  // refs, no token needed), and its checks (API only). Lists remote refs, so it
  // gets more than the quick budget.
  publishStatus: (pkg: string, branch: string, base?: string) =>
    request<PublishStatusReport>(
      `/publish-status${query({ package: pkg, branch, base })}`, undefined, 120_000,
    ),

  reports: () => request<ReportFile[]>('/reports'),

  approveContentBase: (body: ContentBaseApprovalRequest) =>
    post<Record<string, unknown>>('/upstream/content-base/approve', body),
}
