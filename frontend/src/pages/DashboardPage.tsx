import { useEffect, useMemo, useState } from 'react'

import { CommitTable } from '../components/CommitTable'
import { DownloadMappingReport } from '../components/DownloadMappingReport'
import { ErrorAlert } from '../components/ErrorAlert'
import { PreviewPanel } from '../components/PreviewPanel'
import { ResolutionCard } from '../components/ResolutionCard'
import { SelectionPanel, type SelectionState } from '../components/SelectionPanel'
import { UpstreamMdPanel } from '../components/UpstreamMdPanel'
import { Loading } from '../components/ui'
import { useAsyncAction, useAsyncData } from '../hooks/useApi'
import { api } from '../services/api'
import type { CherryPickResult } from '../types/api'

export function DashboardPage() {
  const [selection, setSelection] = useState<SelectionState>({
    release: '', pkg: '', branch: '', mode: 'AUTO',
    manualRepository: '', manualRef: '',
  })
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [cherryPick, setCherryPick] = useState<CherryPickResult | null>(null)

  const health = useAsyncData(() => api.health(), [])
  // The tool never opens a pull request, whatever create_pull_requests says.
  // Pushing is offered only where the deployment says it is allowed; until
  // health answers, it is not.
  const pushEnabled = health.data?.capabilities?.push_enabled === true
  const releases = useAsyncData(() => api.releases(), [])
  const reports = useAsyncData(() => api.reports(), [])

  useEffect(() => {
    if (!selection.release && releases.data?.length) {
      setSelection((s) => ({ ...s, release: releases.data![0].id }))
    }
  }, [releases.data, selection.release])

  const packages = useAsyncData(
    () => api.packages(selection.release), [selection.release], !!selection.release,
  )
  const branches = useAsyncData(
    () => api.branches(selection.pkg, selection.release),
    [selection.pkg, selection.release],
    !!selection.pkg && !!selection.release,
  )

  // Default to the branch the release manifest names, never to a fixed name -
  // once, when a package's branches load. Not again while the field is being
  // edited, or clearing it to type another name would refill it.
  useEffect(() => {
    if (branches.data?.length) {
      const configured = branches.data.find((b) => b.is_configured)
      setSelection((s) => (s.branch ? s : {
        ...s, branch: configured?.name ?? branches.data![0].name,
      }))
    }
  }, [branches.data])

  const resolution = useAsyncAction(async () => {
    if (selection.mode === 'MANUAL') {
      return api.verifyManualUpstream({
        package: selection.pkg, release: selection.release,
        repository: selection.manualRepository, ref: selection.manualRef,
        arcos_branch: selection.branch.trim() || undefined,
      })
    }
    return api.upstream(selection.pkg, selection.release, selection.branch.trim() || undefined)
  })

  const comparison = useAsyncAction(async () => {
    const current = resolution.data
    if (!current) return null
    return api.compare({
      package: selection.pkg, release: selection.release,
      arcos_branch: selection.branch.trim() || undefined,
      upstream_repository:
        current.mode === 'MANUAL' ? current.upstream_repository?.url : undefined,
      upstream_ref: current.mode === 'MANUAL' ? current.upstream_ref ?? undefined : undefined,
    })
  })

  const patchRequest = useMemo(() => {
    const current = resolution.data
    if (!current?.upstream_repository || !current.upstream_ref) return null
    return {
      package: selection.pkg, release: selection.release,
      arcos_branch: selection.branch.trim(),
      upstream_repository: current.upstream_repository.url,
      upstream_ref: current.upstream_ref,
      shas: Array.from(selected),
      // What the selection was made against; the server re-validates it
      // against the branches as they are now.
      comparison_arcos_commit: comparison.data?.arcos_commit ?? undefined,
      comparison_upstream_commit: comparison.data?.upstream_commit ?? undefined,
    }
  }, [resolution.data, selection, selected, comparison.data])

  const preview = useAsyncAction(async () => {
    if (!patchRequest) return null
    return api.preview(patchRequest)
  })

  const apply = useAsyncAction(async () => {
    if (!patchRequest || !preview.data?.base_sha) return null
    // Applied only onto the exact tip the preview ran on; the server refuses
    // if the target branch has moved since. The commits are the ones that
    // were previewed, whatever is ticked now.
    const result = await api.cherryPick({
      ...patchRequest, shas: preview.data.order, push: true,
      expected_base_sha: preview.data.base_sha,
    })
    setCherryPick(result)
    return result
  })

  const approveBase = useAsyncAction(async (tag: string, verifiedBy: string) => {
    await api.approveContentBase({
      package: selection.pkg, release: selection.release, tag,
      verified_by: verifiedBy, confirm: true,
    })
    return tag
  })

  const upstreamMd = useAsyncAction(async () =>
    api.generateUpstreamMd({
      package: selection.pkg, release: selection.release,
      arcos_branch: selection.branch.trim() || undefined,
    }),
  )

  // Publishing follows the reviewed plan alone: the server picks the target
  // and the branch name, so neither is sent - not even the branch chosen
  // under Selection, which steers the comparison only. Every read and check
  // of a real push, then stop; it shows where the push would go.
  const mdDryRun = useAsyncAction(async () =>
    api.publishUpstreamMd({
      package: selection.pkg, release: selection.release,
      dry_run: true, push_only: true,
    }),
  )

  // Push the branch and hand back the links a person opens the pull request
  // from. Offered only after a clean dry run. COVERED, or a pull request the
  // server found, is an answer to show; any other refusal is an error.
  const pushMdBranch = useAsyncAction(async () => {
    const result = await api.publishUpstreamMd({
      package: selection.pkg, release: selection.release,
      confirm: true, push_only: true,
    })
    const settled = result.status === 'BRANCH_PUSHED' || result.status === 'COVERED'
    if (!settled && !result.pull_request) {
      throw new Error(`${result.status}${result.error ? `: ${result.error}` : ''}`)
    }
    return result
  })

  const resetDownstream = () => {
    resolution.reset(); comparison.reset(); preview.reset(); apply.reset()
    upstreamMd.reset(); mdDryRun.reset(); pushMdBranch.reset()
    setSelected(new Set()); setCherryPick(null)
  }

  const mappingRows = health.data?.capabilities?.mapping?.rows ?? null

  return (
    <>
      <header className="app-header">
        <div className="inner">
          <h1>ARCoS Package Manager</h1>
          <p>Compare ARCoS packages against their verified upstreams and pull the patches that matter.</p>
          {health.data && (
            <span className="small muted">
              v{health.data.version}
              {mappingRows != null && <> · mapping: {mappingRows} rows</>}
            </span>
          )}
          <span
            className="badge tone-neutral"
            title={'Patch and debian/upstream.md branches are pushed; the tool never '
              + 'opens, submits or merges a pull request.'}
          >
            Never opens pull requests — a person opens each one from its Compare Link
          </span>
          {health.data && !pushEnabled && (
            <span
              className="badge tone-warn"
              title={'The server refuses every push here (PUSH_DISABLED). Previews and '
                + 'dry runs still work.'}
            >
              Pushing is disabled on this deployment
            </span>
          )}
          <DownloadMappingReport reports={reports.data ?? []} />
        </div>
      </header>

      <main className="app">
        <ErrorAlert error={health.error} onRetry={health.reload} />
        <ErrorAlert error={releases.error} onRetry={releases.reload} />

        {releases.loading && <Loading what="releases" />}

        {releases.data && (
          <SelectionPanel
            releases={releases.data}
            packages={packages.data ?? []}
            branches={branches.data ?? []}
            value={selection}
            loadingPackages={packages.loading}
            loadingBranches={branches.loading}
            branchError={
              branches.error ? 'Branches could not be read from the fork.' : null
            }
            onChange={(next) => { setSelection((s) => ({ ...s, ...next })); resetDownstream() }}
            onResolve={() => { resetDownstream(); resolution.run() }}
            resolving={resolution.loading}
          />
        )}

        <ErrorAlert error={packages.error} onRetry={packages.reload} />
        <ErrorAlert error={resolution.error} />
        <ErrorAlert error={approveBase.error} />
        {approveBase.data && (
          <div className="alert info"><p>
            Approval of {approveBase.data} recorded. It takes effect when{' '}
            {selection.pkg} is next resolved (apm resolve-release --package {selection.pkg} --no-discover).
          </p></div>
        )}

        {resolution.data && (
          <ResolutionCard
            resolution={resolution.data}
            onCompare={() => comparison.run()}
            comparing={comparison.loading}
            onApproveContentBase={(tag, who) => approveBase.run(tag, who)}
            approving={approveBase.loading}
          />
        )}

        <ErrorAlert error={comparison.error} />
        {comparison.loading && (
          <Loading
            what="git comparison"
            hint={
              'Both sides are being cloned on the git host. A package compared '
              + 'for the first time usually takes 10-30 seconds; a very large '
              + 'repository such as the kernel takes longer.'
            }
          />
        )}

        {comparison.data && (
          <CommitTable
            comparison={comparison.data}
            selected={selected}
            onSelectedChange={(next) => {
              setSelected(next)
              // A preview describes the selection it ran on. Until something
              // is pushed, another selection needs a preview of its own.
              if (!cherryPick) { preview.reset(); apply.reset() }
            }}
            onPreview={() => preview.run()}
            previewing={preview.loading}
          />
        )}

        <ErrorAlert error={preview.error} />
        <ErrorAlert error={apply.error} />

        {preview.data && (
          <PreviewPanel
            preview={preview.data}
            result={cherryPick}
            onCancel={() => { preview.reset(); apply.reset(); setCherryPick(null) }}
            onApply={() => apply.run()}
            applying={apply.loading}
            pushEnabled={pushEnabled}
          />
        )}

        {resolution.data && resolution.data.status !== 'NO_UPSTREAM' && (
          <>
            <ErrorAlert error={upstreamMd.error} />
            <ErrorAlert error={mdDryRun.error} />
            <ErrorAlert error={pushMdBranch.error} />
            <UpstreamMdPanel
              document={upstreamMd.data}
              onGenerate={() => {
                mdDryRun.reset(); pushMdBranch.reset()
                upstreamMd.run()
              }}
              generating={upstreamMd.loading}
              dryRun={mdDryRun.data}
              onDryRun={() => mdDryRun.run()}
              dryRunning={mdDryRun.loading}
              pushed={pushMdBranch.data}
              onPush={() => pushMdBranch.run()}
              pushing={pushMdBranch.loading}
              pushEnabled={pushEnabled}
            />
          </>
        )}
      </main>
    </>
  )
}
