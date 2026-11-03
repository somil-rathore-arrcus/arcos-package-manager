import { useAsyncData } from '../hooks/useApi'
import { api } from '../services/api'
import { ErrorAlert } from './ErrorAlert'
import { Badge, ExternalLink } from './ui'

const CI_TONE: Record<string, string> = {
  SUCCESS: 'tone-good', FAILURE: 'tone-bad', PENDING: 'tone-info',
  NONE: 'tone-neutral', UNAVAILABLE: 'tone-neutral',
}

const CI_LABEL: Record<string, string> = {
  SUCCESS: 'CI passed', FAILURE: 'CI failed', PENDING: 'CI running',
  NONE: 'No checks reported', UNAVAILABLE: 'CI status unavailable',
}

/**
 * Where a branch this tool pushed stands - shared by the metadata and the patch
 * workflow. Read-only: it never pushes or opens anything. A pull request is
 * shown only when GitHub's refs or API report it, and CI only as GitHub reports
 * it; "unavailable" is never shown as a pass.
 */
export function PublishStatusPanel({ pkg, branch, base }: {
  pkg: string
  branch: string
  base?: string
}) {
  // Fetched when the branch changes and when asked, never on a timer: each
  // lookup lists the fork's refs over SSH.
  const status = useAsyncData(() => api.publishStatus(pkg, branch, base), [pkg, branch, base])

  const report = status.data
  return (
    <div className="alert neutral" style={{ marginTop: 12 }} aria-label="Pull request and CI status">
      <h3 style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>Pull request and CI</span>
        <button onClick={() => status.reload()} disabled={status.loading}>
          {status.loading ? 'Checking…' : 'Refresh status'}
        </button>
      </h3>
      <ErrorAlert error={status.error} />
      {report && !report.branch_exists && (
        <p className="small">
          <code>{report.head_branch}</code> is not on {report.repository}.
        </p>
      )}
      {report?.branch_exists && (
        <>
          <p className="small">
            <code>{report.head_branch}</code> is on {report.repository} at{' '}
            <code>{(report.head_sha ?? '').slice(0, 12)}</code>.
          </p>
          {report.pull_requests.length === 0 && !report.pull_request && (
            <p className="small">No pull request has been opened from this branch yet.</p>
          )}
          {report.pull_requests.map((pr) => (
            <p key={pr.number} className="small">
              Pull request{' '}
              <ExternalLink href={pr.url}>#{pr.number}</ExternalLink>{' '}
              was opened from this commit
              {report.pull_request?.number === pr.number && report.pull_request.state
                ? ` (${report.pull_request.state})`
                : ' (its state needs a GitHub token)'}.
            </p>
          ))}
          {report.pull_request?.url && !report.pull_requests.some(
            (p) => p.number === report.pull_request?.number) && (
            <p className="small">
              Pull request{' '}
              <ExternalLink href={report.pull_request.url}>#{report.pull_request.number}</ExternalLink>
              {report.pull_request.state ? ` (${report.pull_request.state})` : ''}.
            </p>
          )}
          <p className="small">
            <Badge tone={CI_TONE[report.ci.state] ?? 'tone-neutral'}>
              {CI_LABEL[report.ci.state] ?? report.ci.state}
            </Badge>
            {report.ci.reason && <> {report.ci.reason}</>}
          </p>
          {report.ci.checks.length > 0 && (
            <ul className="small">
              {report.ci.checks.map((c) => (
                <li key={`${c.name}-${c.url ?? ''}`}>
                  <ExternalLink href={c.url}>{c.name}</ExternalLink>
                  {' — '}{c.conclusion ?? c.status}
                  {c.summary && <span className="muted"> · {c.summary}</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
