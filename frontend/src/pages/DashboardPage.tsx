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
import type { CherryPickResult, PullRequest } from '../types/api'

export function DashboardPage() {
  const [selection, setSelection] = useState<SelectionState>({
    release: '', pkg: '', branch: '', mode: 'AUTO',
    manualRepository: '', manualRef: '',
  })
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [cherryPick, setCherryPick] = useState<CherryPickResult | null>(null)
  const [patchPr, setPatchPr] = useState<PullRequest | null>(null)
  const [mdPr, setMdPr] = useState<PullRequest | null>(null)

  const health = useAsyncData(() => api.health(), [])
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

  // Default to the branch the release manifest names, never to a fixed name.
  useEffect(() => {
    if (branches.data?.length && !selection.branch) {
      const configured = branches.data.find((b) => b.is_configured)
      setSelection((s) => ({ ...s, branch: configured?.name ?? branches.data![0].name }))
    }
  }, [branches.data, selection.branch])

  const resolution = useAsyncAction(async () => {
    if (selection.mode === 'MANUAL') {
      return api.verifyManualUpstream({
        package: selection.pkg, release: selection.release,
        repository: selection.manualRepository, ref: selection.manualRef,
        arcos_branch: selection.branch || undefined,
      })
    }
    return api.upstream(selection.pkg, selection.release, selection.branch || undefined)
  })

  const comparison = useAsyncAction(async () => {
    const current = resolution.data
    if (!current) return null
    return api.compare({
      package: selection.pkg, release: selection.release,
      arcos_branch: selection.branch || undefined,
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
      arcos_branch: selection.branch,
      upstream_repository: current.upstream_repository.url,
      upstream_ref: current.upstream_ref,
      shas: Array.from(selected),
    }
  }, [resolution.data, selection, selected])

  const preview = useAsyncAction(async () => {
    if (!patchRequest) return null
    return api.preview(patchRequest)
  })

  const apply = useAsyncAction(async () => {
    if (!patchRequest) return null
    const result = await api.cherryPick({ ...patchRequest, push: true })
    setCherryPick(result)
    return result
  })

  const createPr = useAsyncAction(async () => {
    if (!cherryPick) return null
    const pr = await api.createPullRequest({
      package: selection.pkg, release: selection.release,
      arcos_branch: selection.branch, head_branch: cherryPick.new_branch,
      applied: cherryPick.applied,
    })
    setPatchPr(pr)
    return pr
  })

  const upstreamMd = useAsyncAction(async () =>
    api.generateUpstreamMd({
      package: selection.pkg, release: selection.release,
      arcos_branch: selection.branch || undefined,
    }),
  )

  const createMdPr = useAsyncAction(async () => {
    const pr = await api.createUpstreamMdPr({
      package: selection.pkg, release: selection.release,
      arcos_branch: selection.branch || undefined, confirm: true,
    })
    setMdPr(pr)
    return pr
  })

  const resetDownstream = () => {
    resolution.reset(); comparison.reset(); preview.reset(); apply.reset()
    upstreamMd.reset(); createPr.reset(); createMdPr.reset()
    setSelected(new Set()); setCherryPick(null); setPatchPr(null); setMdPr(null)
  }

  const canCreatePr = !!health.data?.capabilities?.create_pull_requests
  const mapping = health.data?.capabilities?.mapping as
    { rows?: number; generated_at?: string | null } | undefined
  const mappingRows = mapping?.rows ?? null

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
          {health.data && !canCreatePr && (
            <span className="badge tone-neutral" title="No GitHub token is configured.">
              Read-only mode — GitHub write operations disabled
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

        {resolution.data && (
          <ResolutionCard
            resolution={resolution.data}
            onCompare={() => comparison.run()}
            comparing={comparison.loading}
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
            onSelectedChange={setSelected}
            onPreview={() => preview.run()}
            previewing={preview.loading}
          />
        )}

        <ErrorAlert error={preview.error} />
        <ErrorAlert error={apply.error} />
        <ErrorAlert error={createPr.error} />

        {preview.data && (
          <PreviewPanel
            preview={preview.data}
            result={cherryPick}
            pullRequest={patchPr}
            onCancel={() => { preview.reset(); apply.reset(); setCherryPick(null); setPatchPr(null) }}
            onApply={() => apply.run()}
            applying={apply.loading}
            onCreatePr={() => createPr.run()}
            creatingPr={createPr.loading}
            canCreatePr={canCreatePr}
            readOnly={!canCreatePr}
          />
        )}

        {resolution.data && resolution.data.status !== 'NO_UPSTREAM' && (
          <>
            <ErrorAlert error={upstreamMd.error} />
            <ErrorAlert error={createMdPr.error} />
            <UpstreamMdPanel
              document={upstreamMd.data}
              pullRequest={mdPr}
              onGenerate={() => upstreamMd.run()}
              generating={upstreamMd.loading}
              onCreatePr={() => createMdPr.run()}
              creatingPr={createMdPr.loading}
              canCreatePr={canCreatePr}
            />
          </>
        )}
      </main>
    </>
  )
}
