import { vi } from 'vitest'
import * as fx from './fixtures'

export interface Overrides {
  health?: unknown
  packages?: (release: string) => unknown
  branches?: unknown
  upstream?: unknown
  verify?: unknown
  compare?: unknown
  preview?: unknown
  cherryPick?: unknown
  upstreamMd?: unknown
  /** A value, or a function of the request body (dry run and push differ). */
  upstreamMdPr?: unknown
  publishStatus?: unknown
  reports?: unknown
}

export interface Recorded {
  calls: { method: string; path: string; body?: unknown }[]
}

// Fields the upstream.md endpoint refuses: the reviewed plan alone decides the
// target and the branch name, and no pull request is ever opened.
const REMOVED_MD_FIELDS = ['arcos_branch', 'branch_name', 'draft']

/**
 * Stand in for the API at the fetch boundary.
 *
 * Mocking fetch rather than the api module keeps the real client in the test,
 * so URL construction, query strings and error decoding are all exercised. The
 * server's contract refusals are mirrored, so a request it would refuse fails
 * here too.
 */
export function mockApi(overrides: Overrides = {}) {
  const recorded: Recorded = { calls: [] }

  const respond = (value: unknown, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(value), {
      status, headers: { 'Content-Type': 'application/json' },
    }))

  const handler = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const path = url.replace(/^.*\/api/, '')
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    recorded.calls.push({ method: init?.method ?? 'GET', path, body })

    const pick = (value: unknown, fallback: unknown) => {
      if (value === undefined) return respond(fallback)
      if (value instanceof Response) return Promise.resolve(value.clone())
      return respond(value)
    }

    if (path.startsWith('/health')) return pick(overrides.health, fx.health)
    if (path.startsWith('/releases')) return respond(fx.releases)
    if (path.startsWith('/packages')) {
      const release = new URL(url, 'http://x').searchParams.get('release') ?? ''
      if (overrides.packages) return respond(overrides.packages(release))
      return respond(fx.packages.filter((p) => p.releases.includes(release)))
    }
    if (path.startsWith('/branches')) return pick(overrides.branches, fx.branches)
    if (path.startsWith('/upstream/') && path.includes('/evidence')) {
      return respond(fx.resolution.evidence)
    }
    if (path.startsWith('/upstream/verify')) return pick(overrides.verify, fx.resolution)
    if (path.startsWith('/upstream/')) return pick(overrides.upstream, fx.resolution)
    if (path.startsWith('/comparison')) return pick(overrides.compare, fx.comparison)
    if (path.startsWith('/patches/preview')) return pick(overrides.preview, fx.cleanPreview)
    if (path.startsWith('/patches/cherry-pick')) {
      return pick(overrides.cherryPick, fx.cherryPickResult)
    }
    if (path.startsWith('/pull-requests')) {
      return respond({
        code: 'PULL_REQUESTS_DISABLED', message: 'This tool never opens pull requests.',
      }, 403)
    }
    if (path.startsWith('/upstream-md/generate')) {
      return pick(overrides.upstreamMd, fx.upstreamMd)
    }
    if (path.startsWith('/upstream-md/pr')) {
      const sent = REMOVED_MD_FIELDS.filter((field) => field in (body ?? {}))
      if (sent.length || body?.push_only === false) {
        return respond({
          code: 'INVALID_REQUEST', message: `refused: ${sent.join(', ') || 'push_only=false'}`,
        }, 422)
      }
      const value = overrides.upstreamMdPr
      const fallback = body?.dry_run ? fx.upstreamMdDryRun : fx.upstreamMdPushed
      return pick(typeof value === 'function' ? value(body) : value, fallback)
    }
    if (path.startsWith('/publish-status')) {
      return pick(overrides.publishStatus, fx.publishStatusNoToken)
    }
    if (path.startsWith('/reports')) return pick(overrides.reports, fx.reports)
    return respond({ code: 'NOT_FOUND', message: `no stub for ${path}` }, 404)
  })

  vi.stubGlobal('fetch', handler)
  return recorded
}

export const errorResponse = (code: string, message: string, status = 400) =>
  new Response(JSON.stringify({ code, message }), {
    status, headers: { 'Content-Type': 'application/json' },
  })
