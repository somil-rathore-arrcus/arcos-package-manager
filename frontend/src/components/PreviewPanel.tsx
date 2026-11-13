import type { CherryPickPreview, CherryPickResult, ValidationReport } from '../types/api'
import { PublishStatusPanel } from './PublishStatusPanel'
import { Card, ExternalLink } from './ui'

function Validation({ report }: { report: ValidationReport | null | undefined }) {
  if (!report) return null
  if (!report.configured) {
    return (
      <p className="small" style={{ marginTop: 6 }}>
        <strong>Validation:</strong> {report.message}
      </p>
    )
  }
  return (
    <div style={{ marginTop: 6 }}>
      <p className="small"><strong>Validation:</strong> {report.message}</p>
      <ul className="small">
        {report.runs.map((run) => (
          <li key={run.command}>
            <code>{run.command}</code> —{' '}
            {run.timed_out ? 'timed out' : `exit ${run.exit_code ?? '?'}`} ({run.duration_seconds}s)
            {run.exit_code !== 0 && run.output && (
              <pre className="diff" style={{ marginTop: 4, maxHeight: 200 }}>{run.output}</pre>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

// Pushes the patch branch and stops: the tool never opens the pull request. A
// person opens it from the pre-filled link, and PublishStatusPanel reports one
// once GitHub does.
export function PreviewPanel({
  preview, result, onCancel, onApply, applying, pushEnabled,
}: {
  preview: CherryPickPreview
  result: CherryPickResult | null
  onCancel: () => void
  onApply: () => void
  applying: boolean
  /** The deployment allows pushing; without it the server refuses with PUSH_DISABLED. */
  pushEnabled: boolean
}) {
  const clean = preview.outcome === 'CLEAN'
  const pushed = result?.outcome === 'CLEAN' && result.pushed

  return (
    <Card title="Patch PR · preview">
      <p className="small muted" style={{ marginTop: 0 }}>
        Cherry-picks only the selected upstream commits onto a new branch from the
        target. The target branch is never written to.
      </p>
      {preview.outcome === 'CONFLICT' && (
        <div className="alert error">
          <h3>Conflict</h3>
          <p>{preview.message}</p>
          <p style={{ marginTop: 6 }}>
            Conflicting files: {preview.conflicts.map((c) => <code key={c}>{c} </code>)}
          </p>
          <p className="small" style={{ marginTop: 6 }}>
            Nothing was changed. Conflicts are never resolved automatically: deselect
            the conflicting commit, or select the commits it depends on, and preview
            again.
          </p>
        </div>
      )}
      {preview.outcome === 'VALIDATION_FAILED' && (
        <div className="alert error">
          <h3>Validation failed</h3>
          <p>{preview.message}</p>
          <Validation report={preview.validation} />
          <p className="small" style={{ marginTop: 6 }}>
            Nothing can be pushed until validation passes.
          </p>
        </div>
      )}
      {clean && (
        <div className="alert info">
          <h3>Applies cleanly</h3>
          <p>{preview.message}</p>
          <Validation report={preview.validation} />
          <p className="small" style={{ marginTop: 6 }}>
            This ran in a temporary workspace that has been discarded. Your target
            branch was not touched.
          </p>
        </div>
      )}
      {preview.outcome === 'FAILED' && (
        <div className="alert error"><h3>Preview failed</h3><p>{preview.message}</p></div>
      )}
      {preview.warnings.length > 0 && (
        <div className="alert warn">
          <ul className="small">{preview.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}

      {preview.order.length > 0 && (
        <>
          <h3 className="small" style={{ margin: '4px 0 6px' }}>
            Apply order (oldest first)
          </h3>
          <ol className="evidence">
            {preview.order.map((sha) => (
              <li key={sha}>
                <code>{sha.slice(0, 12)}</code>
                {preview.failed_sha === sha && <span className="badge tone-bad"> conflicted</span>}
                {preview.applied.includes(sha) && <span className="badge tone-good"> applied</span>}
              </li>
            ))}
          </ol>
        </>
      )}

      {preview.files_changed.length > 0 && (
        <>
          <h3 className="small" style={{ margin: '12px 0 6px' }}>
            Files changed ({preview.files_changed.length})
          </h3>
          <div className="scroll" style={{ maxHeight: 220 }}>
            <table>
              <thead><tr><th>Path</th><th className="shrink">+</th><th className="shrink">−</th></tr></thead>
              <tbody>
                {preview.files_changed.map((f) => (
                  <tr key={f.path}>
                    <td className="mono small">{f.path}</td>
                    <td className="shrink small">{f.insertions}</td>
                    <td className="shrink small">{f.deletions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {result && (
        <div className={`alert ${result.outcome === 'CLEAN' ? 'info' : 'error'}`} style={{ marginTop: 14 }}>
          <h3>
            {result.outcome === 'CLEAN' ? 'Patch branch created'
              : result.outcome === 'VALIDATION_FAILED' ? 'Validation failed — nothing pushed'
                : 'Cherry-pick failed'}
          </h3>
          <p>{result.message}</p>
          {result.outcome !== 'CLEAN' && <Validation report={result.validation} />}
          {result.outcome === 'CLEAN' && (
            <p className="small" style={{ marginTop: 6 }}>
              Branch <code>{result.new_branch}</code> from <code>{result.base_branch}</code>
              {result.head_sha && <> at <code>{result.head_sha.slice(0, 12)}</code></>}
              {result.pushed ? ' — pushed.' : ' — not pushed.'}
            </p>
          )}
          {pushed && result.pr_create_url && (
            <p style={{ marginTop: 6 }}>
              <ExternalLink href={result.pr_create_url}>Open the pull request on GitHub</ExternalLink>
              <span className="small">
                {' — no pull request was created; a person with access to the '
                  + 'repository opens it from this pre-filled form.'}
              </span>
            </p>
          )}
        </div>
      )}

      {pushed && result && (
        <PublishStatusPanel pkg={result.package} branch={result.new_branch} base={result.base_branch} />
      )}

      {!pushEnabled && clean && !result && (
        <div className="alert info" style={{ marginTop: 14 }}>
          <h3>Pushing is disabled</h3>
          <p>
            This deployment does not push branches, so the patch branch cannot be
            created from here. The preview above is complete; nothing was written.
          </p>
        </div>
      )}

      <div className="actions" style={{ marginTop: 14 }}>
        <button onClick={onCancel} disabled={applying}>Cancel</button>
        {pushEnabled && clean && !result && (
          <button className="primary" onClick={onApply} disabled={applying}>
            {applying ? 'Pushing…' : 'Push patch branch (no PR)'}
          </button>
        )}
      </div>
    </Card>
  )
}
