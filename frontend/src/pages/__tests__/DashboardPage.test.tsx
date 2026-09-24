import { StrictMode } from 'react'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DashboardPage } from '../DashboardPage'
import * as fx from '../../test/fixtures'
import { errorResponse, mockApi, type Recorded } from '../../test/server'

const user = userEvent.setup()

// Render exactly as main.tsx does. Without StrictMode the tests miss every bug
// that only appears when React double-invokes effects - which is precisely the
// class of bug that left the dashboard stuck on "Resolving...".
const renderDashboard = () =>
  render(<StrictMode><DashboardPage /></StrictMode>)

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

async function openDashboard(overrides = {}): Promise<Recorded> {
  const recorded = mockApi(overrides)
  renderDashboard()
  await screen.findByLabelText('Debian release')
  return recorded
}

async function selectPackage(name = 'pyrad') {
  await waitFor(() =>
    expect(screen.getByLabelText('Package / repository')).not.toBeDisabled(),
  )
  await user.selectOptions(screen.getByLabelText('Package / repository'), name)
}

async function resolve(name = 'pyrad') {
  await selectPackage(name)
  await user.click(screen.getByRole('button', { name: /resolve upstream/i }))
  await screen.findByText('Resolved upstream')
}

async function compare() {
  await resolve()
  await user.click(screen.getByRole('button', { name: /^compare$/i }))
  await screen.findByText('Comparison')
}

describe('mapping report download', () => {
  it('offers the bookworm workbook and CSV as small header links, not a panel', async () => {
    await openDashboard()
    const xlsx = await screen.findByRole('link', { name: 'XLSX' })
    expect(xlsx).toHaveAttribute('href', '/api/reports/upstream-mapping-bookworm.xlsx')
    const csv = await screen.findByRole('link', { name: 'CSV' })
    expect(csv).toHaveAttribute('href', '/api/reports/upstream-mapping-bookworm.csv')

    // Not a file listing, and never the internal plan.
    expect(screen.queryByText(/mapping reports/i)).toBeNull()
    expect(screen.queryByText(/upstream-md-plan/i)).toBeNull()
  })

  it('offers nothing when the reports are not available', async () => {
    await openDashboard({ reports: [] })
    await selectPackage()
    expect(screen.queryByRole('link', { name: 'XLSX' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'CSV' })).toBeNull()
  })

  it('offers nothing when the check fails, without asserting anything false', async () => {
    await openDashboard({ reports: errorResponse('NOT_FOUND', 'no route') })
    await selectPackage()
    expect(screen.queryByRole('link', { name: 'XLSX' })).toBeNull()
    expect(screen.queryByText(/no reports have been generated/i)).toBeNull()
    expect(screen.queryByText(/could not be loaded/i)).toBeNull()
  })
})

describe('selection', () => {
  it('loads releases and defaults to the first', async () => {
    await openDashboard()
    expect(screen.getByLabelText('Debian release')).toHaveValue('bookworm')
  })

  it('shows only the packages that ship in the selected release', async () => {
    await openDashboard()
    const packages = await screen.findByLabelText('Package / repository')
    await waitFor(() => expect(packages).not.toBeDisabled())
    expect(within(packages).getByRole('option', { name: 'zenoh' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Debian release'), 'trixie')
    await waitFor(() =>
      expect(within(packages).queryByRole('option', { name: 'zenoh' })).toBeNull(),
    )
    expect(within(packages).getByRole('option', { name: 'pyrad' })).toBeInTheDocument()
  })

  it('offers discovered branches and preselects the one the release ships', async () => {
    await openDashboard()
    await selectPackage()
    const branch = screen.getByLabelText('ARCoS target branch')
    await waitFor(() => expect(branch).toHaveValue('aminor'))
    expect(within(branch).getByRole('option', { name: /main/ })).toBeInTheDocument()
  })

  it('reports when branches cannot be read instead of assuming one', async () => {
    await openDashboard({
      branches: errorResponse('REPOSITORY_UNAVAILABLE', 'unreachable', 502),
    })
    await selectPackage()
    expect(await screen.findByText(/branches could not be read/i)).toBeInTheDocument()
  })
})

describe('upstream resolution', () => {
  it('shows the resolved upstream with its evidence', async () => {
    await openDashboard()
    await resolve()
    expect(screen.getByText('VERIFIED')).toBeInTheDocument()
    expect(screen.getByText('https://github.com/wichert/pyrad.git')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /view evidence/i }))
    expect(
      screen.getByText(/debian\/upstream\/metadata Repository field/),
    ).toBeInTheDocument()
  })

  it('sends a manual upstream to the verify endpoint, not straight to compare', async () => {
    const recorded = await openDashboard()
    await selectPackage()
    await user.click(screen.getByLabelText('Manual'))
    await user.type(
      screen.getByLabelText('Upstream repository'), 'https://github.com/x/y.git',
    )
    await user.type(screen.getByLabelText('Branch or tag'), 'main')
    await user.click(screen.getByRole('button', { name: /resolve upstream/i }))

    await waitFor(() =>
      expect(recorded.calls.some((c) => c.path.startsWith('/upstream/verify'))).toBe(true),
    )
  })

  it('surfaces a rejected manual upstream with guidance', async () => {
    await openDashboard({
      verify: errorResponse('BRANCH_NOT_FOUND', 'no such ref', 404),
    })
    await selectPackage()
    await user.click(screen.getByLabelText('Manual'))
    await user.type(screen.getByLabelText('Upstream repository'), 'https://x/y.git')
    await user.type(screen.getByLabelText('Branch or tag'), 'nope')
    await user.click(screen.getByRole('button', { name: /resolve upstream/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('BRANCH NOT FOUND')
    expect(screen.getByText(/branch list on this page comes from the repository/i))
      .toBeInTheDocument()
  })

  it('states plainly that a NO_UPSTREAM package has nothing to compare', async () => {
    await openDashboard({ upstream: fx.noUpstream })
    await resolve()
    expect(screen.getByRole('button', { name: /^compare$/i })).toBeDisabled()
    expect(screen.getByText('No verified upstream available')).toBeInTheDocument()
    expect(screen.getByText(/finished answer, not a failure/i)).toBeInTheDocument()
  })

  it('shows a NEEDS_REVIEW package without inventing an upstream', async () => {
    await openDashboard({ upstream: fx.needsReview })
    await resolve()
    expect(screen.getByText('NEEDS_REVIEW')).toBeInTheDocument()
    expect(screen.getByText(/no upstream has been invented/i)).toBeInTheDocument()
    expect(screen.getByText(/imported rather than forked/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /view evidence/i }))
    expect(screen.getByText(/shares no history with the ARCoS fork/i)).toBeInTheDocument()
  })
})

describe('comparison', () => {
  it('separates missing, ARCoS-specific and backported commits', async () => {
    await openDashboard()
    await compare()

    expect(screen.getByText('fix buffer overflow')).toBeInTheDocument()
    expect(screen.queryByText('arcos local change')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'ARCoS-specific' }))
    expect(await screen.findByText('arcos local change')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Already backported' }))
    expect(await screen.findByText('already applied')).toBeInTheDocument()
    expect(screen.getByText(/already present as/i)).toBeInTheDocument()
  })

  it('shows criticality with the evidence behind it', async () => {
    await openDashboard()
    await compare()
    // "Critical" is also a filter button, so scope to the commit row.
    const row = screen.getByText('fix buffer overflow').closest('tr')!
    expect(within(row).getByText('Critical')).toBeInTheDocument()
    expect(within(row).getByText(/references CVE-2026-12345/)).toBeInTheDocument()

    const plain = screen.getByText('refactor parser').closest('tr')!
    expect(within(plain).getByText('Unknown')).toBeInTheDocument()
    expect(within(plain).getByText(/no evidence recorded/i)).toBeInTheDocument()
  })

  it('filters to critical commits only', async () => {
    await openDashboard()
    await compare()
    await user.click(screen.getByRole('button', { name: 'Critical' }))
    expect(screen.getByText('fix buffer overflow')).toBeInTheDocument()
    expect(screen.queryByText('refactor parser')).toBeNull()
  })

  it('does not offer selection for commits that are already present', async () => {
    await openDashboard()
    await compare()
    await user.click(screen.getByRole('button', { name: 'Already backported' }))
    expect(screen.queryByRole('checkbox')).toBeNull()
  })

  it('explains a missing common ancestor rather than failing generically', async () => {
    await openDashboard({
      compare: errorResponse('NO_COMMON_ANCESTOR', 'shares no history', 422),
    })
    await resolve()
    await user.click(screen.getByRole('button', { name: /^compare$/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('NO COMMON ANCESTOR')
    expect(screen.getByText(/imported rather than forked/i)).toBeInTheDocument()
  })
})

describe('patch workflow', () => {
  async function selectAndPreview(overrides = {}): Promise<Recorded> {
    const recorded = await openDashboard(overrides)
    await compare()
    await user.click(screen.getByRole('button', { name: /select critical/i }))
    await user.click(screen.getByRole('button', { name: /preview 1 selected/i }))
    await screen.findByText('Preview')
    return recorded
  }

  it('previews cleanly and says the target branch was untouched', async () => {
    await selectAndPreview()
    expect(screen.getByText(/applies cleanly/i)).toBeInTheDocument()
    expect(screen.getByText(/your target\s+branch was not touched/i)).toBeInTheDocument()
  })

  it('shows conflicts and offers no way to force through them', async () => {
    await selectAndPreview({ preview: fx.conflictPreview })
    expect(screen.getByText('Conflict')).toBeInTheDocument()
    expect(screen.getByText(/never resolved automatically/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /create patch branch/i })).toBeNull()
  })

  it('creates a patch branch only when asked, then a PR only when asked again', async () => {
    const recorded = await selectAndPreview()
    expect(recorded.calls.some((c) => c.path === '/patches/cherry-pick')).toBe(false)

    await user.click(screen.getByRole('button', { name: /create patch branch/i }))
    await screen.findByText(/patch branch created/i)
    expect(recorded.calls.some((c) => c.path === '/pull-requests')).toBe(false)

    await user.click(screen.getByRole('button', { name: /create pull request/i }))
    expect(await screen.findByText('https://github.com/Arrcus/pyrad/pull/42'))
      .toBeInTheDocument()
  })

  it('offers no way to push or open a PR in read-only mode', async () => {
    const recorded = await selectAndPreview({ health: fx.healthNoToken })

    expect(screen.getByText('Read-only mode')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /create patch branch/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /create pull request/i })).toBeNull()

    // The preview itself still ran, and nothing was written.
    expect(recorded.calls.some((c) => c.path === '/patches/preview')).toBe(true)
    expect(recorded.calls.some((c) => c.path === '/patches/cherry-pick')).toBe(false)
    expect(recorded.calls.some((c) => c.path === '/pull-requests')).toBe(false)
  })

  it('announces read-only mode in the header', async () => {
    await openDashboard({ health: fx.healthNoToken })
    expect(
      await screen.findByText(/read-only mode — github write operations disabled/i),
    ).toBeInTheDocument()
  })
})

describe('upstream.md', () => {
  it('generates the document without proposing it', async () => {
    const recorded = await openDashboard()
    await resolve()
    await user.click(screen.getByRole('button', { name: /^generate$/i }))
    await screen.findByText('CREATED')
    expect(recorded.calls.some((c) => c.path === '/upstream-md/pr')).toBe(false)
  })

  it('proposes it only when asked, and links the pull request', async () => {
    const recorded = await openDashboard()
    await resolve()
    await user.click(screen.getByRole('button', { name: /^generate$/i }))
    await screen.findByText('CREATED')
    await user.click(screen.getByRole('button', { name: /create pull request/i }))

    expect(await screen.findByText('https://github.com/Arrcus/pyrad/pull/43'))
      .toBeInTheDocument()
    const call = recorded.calls.find((c) => c.path === '/upstream-md/pr')
    expect(call?.body).toMatchObject({ package: 'pyrad', confirm: true })
  })

  it('reports why no pull request was opened', async () => {
    await openDashboard({
      upstreamMdPr: {
        ...fx.upstreamMdPublish, status: 'CONFLICT', pull_request: null,
        pushed: false, error: 'debian/upstream.md was not written by this tool',
      },
    })
    await resolve()
    await user.click(screen.getByRole('button', { name: /^generate$/i }))
    await screen.findByText('CREATED')
    await user.click(screen.getByRole('button', { name: /create pull request/i }))

    expect(await screen.findByText(/CONFLICT: debian\/upstream.md was not written/))
      .toBeInTheDocument()
  })

  it('is hidden for a package with no upstream', async () => {
    await openDashboard({ upstream: fx.noUpstream })
    await resolve()
    expect(screen.queryByText('debian/upstream.md')).toBeNull()
  })
})

describe('loading and failure states', () => {
  it('reports a timeout rather than spinning forever', async () => {
    await openDashboard({
      compare: errorResponse('TIMEOUT', 'Timed out fetching upstream.', 504),
    })
    await resolve()
    await user.click(screen.getByRole('button', { name: /^compare$/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('TIMEOUT')
    expect(screen.getByText(/clones both repositories/i)).toBeInTheDocument()
  })

  it('shows a loading state while comparing', async () => {
    let release!: (value: Response) => void
    const slow = new Promise<Response>((resolve) => { release = resolve })
    await openDashboard({ compare: undefined })
    await resolve()

    const original = globalThis.fetch as typeof fetch
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      String(input).includes('/comparison') ? slow : original(input, init)))

    await user.click(screen.getByRole('button', { name: /^compare$/i }))
    expect(await screen.findByRole('status')).toHaveTextContent(/comparing|git comparison/i)
    release(new Response(JSON.stringify(fx.comparison), {
      headers: { 'Content-Type': 'application/json' },
    }))
    await screen.findByText('Comparison')
  })

  it('reports an unreachable API', async () => {
    // Health and releases both fail, so more than one alert is expected.
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('down'))))
    renderDashboard()
    const alerts = await screen.findAllByRole('alert')
    expect(alerts.length).toBeGreaterThan(0)
    expect(alerts[0]).toHaveTextContent('NETWORK')
    expect(alerts[0]).toHaveTextContent(/backend is running/i)
  })
})
