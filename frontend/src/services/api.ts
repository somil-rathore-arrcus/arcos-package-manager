// The single place the frontend talks to the backend.
//
// Errors carry the backend's ErrorCode so the UI can react to the specific
// problem - a missing branch, a missing token, no common ancestor - instead of
// showing one generic failure for all of them.

import type {
  Branch, CherryPickPreview, CherryPickRequest, CherryPickResult,
  ComparisonRequest, ComparisonResult, DebianRelease, HealthResponse,
  ManualUpstreamRequest, Package, PublishResult, PullRequest, PullRequestRequest,
  ReportFile, ResolutionEvidence, UpstreamMdDocument, UpstreamMdPrRequest,
  UpstreamMdRequest, UpstreamResolution,
} from '../types/api'

const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

// Every request is bounded. Without this a stalled git or SSH operation leaves
// the UI spinning with no way to tell "slow" from "never going to finish".
// Comparison legitimately takes minutes on a first run, so it gets its own
// budget rather than forcing the quick calls to wait as long.
const QUICK_TIMEOUT_MS = 30_000
// Deliberately longer than the backend's own git ceiling (APM_GIT_LONG_TIMEOUT,
// 900s). If the client gave up first the user would see a generic client-side
// timeout instead of the backend's explicit TIMEOUT and its explanation.
const SLOW_TIMEOUT_MS = 20 * 60_000

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
    response = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
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
  health: () => request<HealthResponse>('/health'),

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

  createPullRequest: (body: PullRequestRequest) =>
    post<PullRequest>('/pull-requests', body),

  generateUpstreamMd: (body: UpstreamMdRequest) =>
    post<UpstreamMdDocument>('/upstream-md/generate', body),

  // Returns what the publisher did; `pull_request` is set only when a PR is
  // open. Anything else (NO_CHANGE, CONFLICT, BRANCH_EXISTS...) is a result for
  // a person to read, not an HTTP error.
  createUpstreamMdPr: (body: UpstreamMdPrRequest) =>
    post<PublishResult>('/upstream-md/pr', body, SLOW_TIMEOUT_MS),

  reports: () => request<ReportFile[]>('/reports'),
}
