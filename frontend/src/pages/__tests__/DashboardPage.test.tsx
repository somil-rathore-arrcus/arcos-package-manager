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
    const options = Array.from(document.querySelectorAll('#branch-options option'))
      .map((o) => (o as HTMLOptionElement).value)
    expect(options).toEqual(['aminor', 'main'])
  })

  it('accepts a typed branch and resolves against it', async () => {
    const recorded = await openDashboard()
    await selectPackage()
    const branch = screen.getByLabelText('ARCoS target branch')
    await waitFor(() => expect(branch).toHaveValue('aminor'))
    await user.clear(branch)
    expect(branch).toHaveValue('')
    expect(screen.getByRole('button', { name: /resolve upstream/i })).toBeDisabled()
    await user.type(branch, 'aminor-hotfix')
    expect(screen.getByText(/not in the fork's branch list/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /resolve upstream/i }))
    await screen.findByText('Resolved upstream')
    const call = recorded.calls.find((c) => c.path.startsWith('/upstream/pyrad'))!
    expect(call.path).toContain('arcos_branch=aminor-hotfix')
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

  it('shows the verification level apart from status, and labels raw counts', async () => {
    await openDashboard()
    await resolve()
    expect(screen.getByText('SHARED_HISTORY')).toBeInTheDocument()
    expect(screen.getByText(/109 upstream commit\(s\) not in ARCoS/)).toBeInTheDocument()
    expect(screen.getByText(/not a list of missing fixes/i)).toBeInTheDocument()
    expect(screen.getByText('fallback')).toBeInTheDocument()
  })

  it('does not offer comparison for an upstream that only exists', async () => {
    await openDashboard({ upstream: { ...fx.resolution, verification_level: 'REF_EXISTS' } })
    await resolve()
    expect(screen.getByRole('button', { name: /^compare$/i })).toBeDisabled()
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
  it('states the backlog by presence, not as a raw count', async () => {
    await openDashboard()
    await compare()
    expect(screen.getByText('Relevant upstream commits')).toBeInTheDocument()
    expect(screen.getByText('Critical/security missing')).toBeInTheDocument()
    expect(screen.queryByText(/since ancestor/i)).toBeNull()
  })

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
    expect(within(plain).getByText(/unknown, not safe/i)).toBeInTheDocument()
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
    await screen.findByText('Patch PR · preview')
    return recorded
  }

  const pushPatch = () =>
    user.click(screen.getByRole('button', { name: /push patch branch \(no pr\)/i }))

  it('previews cleanly and says the target branch was untouched', async () => {
    await selectAndPreview()
    expect(screen.getByText(/applies cleanly/i)).toBeInTheDocument()
    expect(screen.getByText(/your target\s+branch was not touched/i)).toBeInTheDocument()
  })

  it('shows conflicts and offers no way to force through them', async () => {
    await selectAndPreview({ preview: fx.conflictPreview })
    expect(screen.getByText('Conflict')).toBeInTheDocument()
    expect(screen.getByText(/never resolved automatically/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /push patch branch/i })).toBeNull()
  })

  it('pushes the patch branch only when asked, links the PR form, and opens no PR', async () => {
    const recorded = await selectAndPreview()
    // The preview ran, and nothing was written yet.
    expect(recorded.calls.some((c) => c.path === '/patches/preview')).toBe(true)
    expect(recorded.calls.some((c) => c.path === '/patches/cherry-pick')).toBe(false)

    await pushPatch()
    await screen.findByText(/patch branch created/i)
    const link = screen.getByRole('link', { name: /open the pull request on github/i })
    expect(link).toHaveAttribute('href', fx.cherryPickResult.pr_create_url)
    expect(screen.getByText(/no pull request was created/i)).toBeInTheDocument()
    const pick = recorded.calls.find((c) => c.path === '/patches/cherry-pick')!
    expect(pick.body).toMatchObject({ push: true, expected_base_sha: fx.cleanPreview.base_sha })
    expect(recorded.calls.some((c) => c.path === '/pull-requests')).toBe(false)
    expect(screen.queryByRole('button', { name: /create pull request/i })).toBeNull()
  })

  it('applies only onto the tip the preview ran on, and says what it compared', async () => {
    const recorded = await selectAndPreview()
    const previewCall = recorded.calls.find((c) => c.path === '/patches/preview')!
    expect(previewCall.body).toMatchObject({
      comparison_arcos_commit: fx.comparison.arcos_commit,
      comparison_upstream_commit: fx.comparison.upstream_commit,
    })

    await pushPatch()
    await screen.findByText(/patch branch created/i)
    const pick = recorded.calls.find((c) => c.path === '/patches/cherry-pick')!
    expect(pick.body).toMatchObject({ expected_base_sha: fx.cleanPreview.base_sha })
  })

  it('a different selection needs its own preview before anything is pushed', async () => {
    const recorded = await selectAndPreview()

    await user.click(screen.getByLabelText(`Select ${'d'.repeat(12)}`))

    expect(screen.queryByText('Patch PR · preview')).toBeNull()
    expect(screen.queryByRole('button', { name: /push patch branch/i })).toBeNull()
    expect(recorded.calls.some((c) => c.path === '/patches/cherry-pick')).toBe(false)
  })

  it('pushes exactly the commits that were previewed', async () => {
    const recorded = await selectAndPreview()
    await pushPatch()
    await screen.findByText(/patch branch created/i)
    const pick = recorded.calls.find((c) => c.path === '/patches/cherry-pick')!
    expect(pick.body).toMatchObject({ shas: fx.cleanPreview.order })
  })

  it('offers no push where the deployment disables pushing, and says why', async () => {
    const recorded = await selectAndPreview({ health: fx.healthPushDisabled })
    expect(screen.getByText(/applies cleanly/i)).toBeInTheDocument()
    expect(screen.getByText('Pushing is disabled')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /push patch branch/i })).toBeNull()
    expect(recorded.calls.some((c) => c.path === '/patches/cherry-pick')).toBe(false)
  })

  it('says plainly when nothing was built or tested', async () => {
    await selectAndPreview()
    expect(screen.getByText(/nothing was built or tested/i)).toBeInTheDocument()
  })

  it('blocks the push when validation fails, and shows why', async () => {
    const recorded = await selectAndPreview({ preview: fx.validationFailedPreview })
    expect(screen.getByText('Validation failed')).toBeInTheDocument()
    expect(screen.getByText(/exited with status 2/)).toBeInTheDocument()
    expect(screen.getByText('make: *** [build] Error 2')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /push patch branch/i })).toBeNull()
    expect(recorded.calls.some((c) => c.path === '/patches/cherry-pick')).toBe(false)
  })

  it('after a push, reports the branch, no PR yet, and CI as unavailable - never passing', async () => {
    const recorded = await selectAndPreview()
    await pushPatch()
    expect(await screen.findByText('CI status unavailable')).toBeInTheDocument()
    expect(screen.getByText(/no pull request has been opened from this branch yet/i))
      .toBeInTheDocument()
    expect(screen.queryByText('CI passed')).toBeNull()
    const status = recorded.calls.find((c) => c.path.startsWith('/publish-status'))!
    expect(status.path).toContain('branch=upstream%2Fpyrad%2F20260923-000000')
    expect(status.path).toContain('base=aminor')
  })

  it('shows a PR found from git refs and each failing check', async () => {
    await selectAndPreview({
      publishStatus: {
        ...fx.publishStatusNoToken,
        pull_requests: [{ number: 9, url: 'https://github.com/Arrcus/pyrad/pull/9' }],
        api_access: true,
        ci: {
          state: 'FAILURE', sha: 'b'.repeat(40), reason: '',
          checks: [
            { name: 'description', status: 'completed', conclusion: 'failure',
              url: 'https://github.com/Arrcus/pyrad/runs/1', summary: 'Missing Root Cause' },
            { name: 'build', status: 'completed', conclusion: 'success', url: null, summary: '' },
          ],
        },
      },
    })
    await pushPatch()
    expect(await screen.findByText('CI failed')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '#9' }))
      .toHaveAttribute('href', 'https://github.com/Arrcus/pyrad/pull/9')
    expect(screen.getByRole('link', { name: 'description' }))
      .toHaveAttribute('href', 'https://github.com/Arrcus/pyrad/runs/1')
    expect(screen.getByText(/missing root cause/i)).toBeInTheDocument()
  })

  it('refreshes the status only when asked, without pushing again', async () => {
    const recorded = await selectAndPreview()
    await pushPatch()
    await screen.findByText('CI status unavailable')
    const count = () => recorded.calls.filter((c) => c.path.startsWith('/publish-status')).length
    const before = count()
    await user.click(screen.getByRole('button', { name: /refresh status/i }))
    await waitFor(() => expect(count()).toBe(before + 1))
    expect(recorded.calls.filter((c) => c.path === '/patches/cherry-pick')).toHaveLength(1)
  })
})

describe('pull requests and pushing', () => {
  it('always says the tool never opens pull requests', async () => {
    await openDashboard()
    await screen.findByText(/v0\.1\.0/)
    expect(screen.getByText(
      /never opens pull requests — a person opens each one from its compare link/i,
    )).toBeInTheDocument()
    expect(screen.queryByText(/pushing is disabled on this deployment/i)).toBeNull()
    expect(screen.queryByText(/read-only mode/i)).toBeNull()
  })

  it('announces in the header when the deployment disables pushing', async () => {
    await openDashboard({ health: fx.healthPushDisabled })
    expect(await screen.findByText(/pushing is disabled on this deployment/i))
      .toBeInTheDocument()
    expect(screen.getByText(/never opens pull requests/i)).toBeInTheDocument()
  })

  it('never offers to create a pull request, even if capabilities claim it can', async () => {
    const recorded = await openDashboard({
      health: {
        ...fx.health,
        capabilities: { ...fx.health.capabilities, create_pull_requests: true },
      },
    })
    await compare()
    await user.click(screen.getByRole('button', { name: /select critical/i }))
    await user.click(screen.getByRole('button', { name: /preview 1 selected/i }))
    await user.click(await screen.findByRole('button', { name: /push patch branch \(no pr\)/i }))
    await screen.findByText(/patch branch created/i)

    await user.click(screen.getByRole('button', { name: /^generate$/i }))
    await screen.findByText('CREATED')
    await user.click(screen.getByRole('button', { name: /^dry run$/i }))
    await user.click(await screen.findByRole('button', { name: /push branch \(no pr\)/i }))
    await screen.findByText('Branch pushed')

    expect(screen.queryAllByRole('button', { name: /pull request/i })).toHaveLength(0)
    expect(recorded.calls.some((c) => c.path.startsWith('/pull-requests'))).toBe(false)
  })
})

describe('upstream.md', () => {
  const mdCalls = (recorded: Recorded) =>
    recorded.calls.filter((c) => c.path === '/upstream-md/pr')

  async function generate() {
    await user.click(screen.getByRole('button', { name: /^generate$/i }))
    await screen.findByText('CREATED')
  }

  async function dryRun(status = 'DRY_RUN_OK') {
    await generate()
    await user.click(screen.getByRole('button', { name: /^dry run$/i }))
    await screen.findByText(status)
  }

  const pushMd = () => user.click(screen.getByRole('button', { name: /push branch \(no pr\)/i }))

  it('generates the document without proposing it', async () => {
    const recorded = await openDashboard()
    await resolve()
    await generate()
    expect(mdCalls(recorded)).toHaveLength(0)
  })

  it('dry-runs without pushing, and shows where the reviewed plan sends it', async () => {
    const recorded = await openDashboard()
    await resolve()
    await dryRun()

    expect(screen.getByText(/nothing was pushed/i)).toBeInTheDocument()
    const details = screen.getByText('Target branch (reviewed plan)').closest('dl')!
    expect(within(details).getByText('aminor')).toBeInTheDocument()
    expect(within(details).getByText('debian-upstream-tracking')).toBeInTheDocument()
    expect(screen.getByText(
      /affects the comparison only,\s+never where this metadata is published/i,
    )).toBeInTheDocument()
    // Nothing is on GitHub yet, so there is no link that would lead nowhere.
    expect(screen.queryByRole('link', { name: 'Compare Link' })).toBeNull()
    expect(mdCalls(recorded)).toHaveLength(1)
    expect(recorded.calls.some((c) => c.path.startsWith('/publish-status'))).toBe(false)
  })

  it('publishes from the reviewed plan alone: no target or branch name is sent', async () => {
    const recorded = await openDashboard()
    await selectPackage()
    const branch = screen.getByLabelText('ARCoS target branch')
    await waitFor(() => expect(branch).toHaveValue('aminor'))
    await user.clear(branch)
    await user.type(branch, 'aminor-hotfix')
    await user.click(screen.getByRole('button', { name: /resolve upstream/i }))
    await screen.findByText('Resolved upstream')
    await dryRun()
    await pushMd()
    await screen.findByText('Branch pushed')

    const [dry, push] = mdCalls(recorded)
    for (const call of [dry, push]) {
      expect(call.body).toMatchObject({ package: 'pyrad', release: 'bookworm', push_only: true })
      expect(call.body).not.toHaveProperty('arcos_branch')
      expect(call.body).not.toHaveProperty('branch_name')
      expect(call.body).not.toHaveProperty('draft')
    }
    expect(dry.body).toMatchObject({ dry_run: true })
    expect(dry.body).not.toHaveProperty('confirm')
    expect(push.body).toMatchObject({ confirm: true })
    expect(push.body).not.toHaveProperty('dry_run')
    // The chosen branch still steers the comparison.
    const resolveCall = recorded.calls.find((c) => c.path.startsWith('/upstream/pyrad'))!
    expect(resolveCall.path).toContain('arcos_branch=aminor-hotfix')
  })

  it('pushes only after a dry run, once, and links the PR form', async () => {
    const recorded = await openDashboard()
    await resolve()
    await generate()
    expect(screen.queryByRole('button', { name: /push branch/i })).toBeNull()

    await user.click(screen.getByRole('button', { name: /^dry run$/i }))
    await screen.findByText('DRY_RUN_OK')
    expect(mdCalls(recorded)).toHaveLength(1)

    await pushMd()
    const link = await screen.findByRole('link', { name: /open the pull request on github/i })
    expect(link).toHaveAttribute('href', fx.upstreamMdPushed.pr_create_url)
    expect(mdCalls(recorded)).toHaveLength(2)
    expect(screen.getByText(/no pull request was created/i)).toBeInTheDocument()
    // Nothing more to push in this view.
    expect(screen.queryByRole('button', { name: /push branch/i })).toBeNull()
  })

  it('links the Compare Link and the file on the branch after a push', async () => {
    await openDashboard()
    await resolve()
    await dryRun()
    await pushMd()

    expect(await screen.findByRole('link', { name: 'Compare Link' }))
      .toHaveAttribute('href', 'https://github.com/Arrcus/pyrad/compare/aminor...debian-upstream-tracking')
    expect(screen.getByRole('link', { name: 'debian/upstream.md on the branch' }))
      .toHaveAttribute('href', fx.upstreamMdPushed.file_url)
    expect(screen.getByRole('link', { name: /open the pull request on github/i }))
      .toBeInTheDocument()
  })

  it('shows a covered package as covered, with its links and nothing to push', async () => {
    const recorded = await openDashboard({ upstreamMdPr: fx.upstreamMdCovered })
    await user.selectOptions(screen.getByLabelText('Debian release'), 'trixie')
    await resolve()
    await dryRun('COVERED')

    expect(screen.getByText('COVERED')).toHaveClass('tone-neutral')
    expect(screen.getByText(/covered by the bookworm branch/i))
      .toHaveTextContent(/debian-upstream-tracking — nothing to push/)
    expect(screen.getByRole('link', { name: 'Compare Link' }))
      .toHaveAttribute('href', fx.upstreamMdCovered.compare_url)
    expect(screen.getByRole('link', { name: 'debian/upstream.md on the branch' }))
      .toHaveAttribute('href', fx.upstreamMdCovered.file_url)
    expect(screen.queryByRole('button', { name: /push branch/i })).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(mdCalls(recorded)[0].body).toMatchObject({ release: 'trixie', dry_run: true })
  })

  it('shows a push that found the file covered as an answer, not an error', async () => {
    await openDashboard({
      upstreamMdPr: (body: { dry_run?: boolean }) =>
        body.dry_run ? fx.upstreamMdDryRun : fx.upstreamMdCovered,
    })
    await resolve()
    await dryRun()
    await pushMd()

    expect(await screen.findByText('Nothing pushed')).toBeInTheDocument()
    expect(screen.getByText(/covered by the bookworm branch/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Compare Link' })).toBeInTheDocument()
    expect(screen.queryByText('Branch pushed')).toBeNull()
    expect(screen.queryByRole('button', { name: /push branch/i })).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows a pull request the server found, without acting on it', async () => {
    await openDashboard({
      upstreamMdPr: (body: { dry_run?: boolean }) =>
        body.dry_run ? fx.upstreamMdDryRun : fx.upstreamMdPrFound,
    })
    await resolve()
    await dryRun()
    await pushMd()

    expect(await screen.findByRole('link', { name: 'https://github.com/Arrcus/pyrad/pull/43' }))
      .toHaveAttribute('href', 'https://github.com/Arrcus/pyrad/pull/43')
    expect(screen.getByText(/did not open it and changes nothing on it/i)).toBeInTheDocument()
    expect(screen.queryAllByRole('button', { name: /pull request/i })).toHaveLength(0)
  })

  it('reports a dry run the server refused, with its reason and no push', async () => {
    await openDashboard({
      upstreamMdPr: {
        ...fx.upstreamMdDryRun, status: 'DRIFT',
        error: 'the file no longer matches the reviewed plan',
      },
    })
    await resolve()
    await dryRun('DRIFT')

    expect(screen.getByText('DRIFT')).toHaveClass('tone-bad')
    expect(screen.getByText('the file no longer matches the reviewed plan')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /push branch/i })).toBeNull()
  })

  it('reports a push that stopped short as an error, not a link', async () => {
    await openDashboard({
      upstreamMdPr: (body: { dry_run?: boolean }) => body.dry_run ? fx.upstreamMdDryRun : {
        ...fx.upstreamMdDryRun, status: 'BRANCH_EXISTS',
        error: 'debian-upstream-tracking already exists on the fork',
      },
    })
    await resolve()
    await dryRun()
    await pushMd()

    expect(await screen.findByText(/BRANCH_EXISTS: debian-upstream-tracking already exists/))
      .toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /open the pull request on github/i })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Compare Link' })).toBeNull()
  })

  it('offers no push where the deployment disables pushing, and says why', async () => {
    const recorded = await openDashboard({ health: fx.healthPushDisabled })
    await resolve()
    await dryRun()

    expect(screen.queryByRole('button', { name: /push branch/i })).toBeNull()
    expect(screen.getByText(/pushing is disabled on this deployment, so this branch/i))
      .toBeInTheDocument()
    expect(mdCalls(recorded)).toHaveLength(1)
  })

  it('explains a push the server refuses because pushing is disabled', async () => {
    await openDashboard({
      upstreamMdPr: (body: { dry_run?: boolean }) => body.dry_run
        ? fx.upstreamMdDryRun
        : errorResponse('PUSH_DISABLED', 'This deployment does not push.', 403),
    })
    await resolve()
    await dryRun()
    await pushMd()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Pushing is disabled on this deployment')
    expect(alert).toHaveTextContent(/previews and dry runs still work/i)
  })

  it('shows what a reviewer should check before merging', async () => {
    await openDashboard({
      upstreamMd: {
        ...fx.upstreamMd,
        content: 'Upstream Source: https://git.kernel.org/pub/scm/network/iproute2/iproute2.git\n\n'
          + 'Current Upstream Branch: N/A - no upstream branch verified; upstream tag v6.1.0\n',
        review_flags: [
          { code: 'BRANCH_NOT_VERIFIED', detail: 'the mapping records upstream tag v6.1.0, not a branch' },
          { code: 'TAG_ONLY', detail: 'upstream tag v6.1.0 is the only ref recorded' },
        ],
      },
    })
    await resolve()
    await user.click(screen.getByRole('button', { name: /^generate$/i }))

    expect(await screen.findByText(/check before merging/i)).toBeInTheDocument()
    expect(screen.getByText('TAG_ONLY')).toBeInTheDocument()
    expect(screen.getByText('BRANCH_NOT_VERIFIED')).toBeInTheDocument()
  })

  it('tracks the pushed branch\'s PR and CI after a push', async () => {
    const recorded = await openDashboard({
      publishStatus: {
        ...fx.publishStatusNoToken, head_branch: 'debian-upstream-tracking',
        pull_requests: [{ number: 35, url: 'https://github.com/Arrcus/pyrad/pull/35' }],
      },
    })
    await resolve()
    await dryRun()
    // Nothing is looked up before there is a pushed branch.
    expect(recorded.calls.some((c) => c.path.startsWith('/publish-status'))).toBe(false)
    await pushMd()
    expect(await screen.findByRole('link', { name: '#35' }))
      .toHaveAttribute('href', 'https://github.com/Arrcus/pyrad/pull/35')
    expect(screen.getByText('CI status unavailable')).toBeInTheDocument()
    const status = recorded.calls.find((c) => c.path.startsWith('/publish-status'))!
    expect(status.path).toContain('branch=debian-upstream-tracking')
  })

  it('labels the metadata PR apart from the patch PR', async () => {
    await openDashboard()
    await resolve()
    expect(screen.getByText('Metadata PR · debian/upstream.md')).toBeInTheDocument()
    expect(screen.getByText(/no code changes/i)).toBeInTheDocument()
  })

  it('is hidden for a package with no upstream', async () => {
    await openDashboard({ upstream: fx.noUpstream })
    await resolve()
    expect(screen.queryByText(/metadata pr/i)).toBeNull()
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
