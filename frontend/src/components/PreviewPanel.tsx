import type { CherryPickPreview, CherryPickResult, PullRequest } from '../types/api'
import { Card, ExternalLink } from './ui'

export function PreviewPanel({
  preview, result, pullRequest, onCancel, onApply, applying, onCreatePr, creatingPr,
  canCreatePr, readOnly,
}: {
  preview: CherryPickPreview
  result: CherryPickResult | null
  pullRequest: PullRequest | null
  onCancel: () => void
  onApply: () => void
  applying: boolean
  onCreatePr: () => void
  creatingPr: boolean
  canCreatePr: boolean
  readOnly: boolean
}) {
  const clean = preview.outcome === 'CLEAN'

  return (
    <Card title="Preview">
      {preview.outcome === 'CONFLICT' && (
        <div className="alert error">
          <h3>Conflict</h3>
          <p>{preview.message}</p>
          <p style={{ marginTop: 6 }}>
            Conflicting files: {preview.conflicts.map((c) => <code key={c}>{c} </code>)}
          </p>
          <p className="small" style={{ marginTop: 6 }}>
            Nothing was changed. Conflicts are never resolved automatically.
          </p>
        </div>
      )}
      {clean && (
        <div className="alert info">
          <h3>Applies cleanly</h3>
          <p>{preview.message}</p>
          <p className="small" style={{ marginTop: 6 }}>
            This ran in a temporary workspace that has been discarded. Your target
            branch was not touched.
          </p>
        </div>
      )}
      {preview.outcome === 'FAILED' && (
        <div className="alert error"><h3>Preview failed</h3><p>{preview.message}</p></div>
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
          <h3>{result.outcome === 'CLEAN' ? 'Patch branch created' : 'Cherry-pick failed'}</h3>
          <p>{result.message}</p>
          {result.outcome === 'CLEAN' && (
            <p className="small" style={{ marginTop: 6 }}>
              Branch <code>{result.new_branch}</code> from <code>{result.base_branch}</code>
              {result.pushed ? ' — pushed.' : ' — not pushed.'}
            </p>
          )}
        </div>
      )}

      {pullRequest?.url && (
        <div className="alert info" style={{ marginTop: 14 }}>
          <h3>{pullRequest.already_existed ? 'Pull request already open' : 'Pull request created'}</h3>
          <p><ExternalLink href={pullRequest.url}>{pullRequest.url}</ExternalLink></p>
        </div>
      )}

      {readOnly && clean && (
        <div className="alert info" style={{ marginTop: 14 }}>
          <h3>Read-only mode</h3>
          <p>
            This preview ran in a temporary workspace that has been discarded.
            Creating a patch branch, pushing and opening a pull request are
            disabled until a GitHub token is configured.
          </p>
        </div>
      )}

      <div className="actions" style={{ marginTop: 14 }}>
        <button onClick={onCancel}>Cancel</button>
        {clean && !result && !readOnly && (
          <button className="primary" onClick={onApply} disabled={applying}>
            {applying ? 'Creating…' : 'Create patch branch and push'}
          </button>
        )}
        {result?.outcome === 'CLEAN' && result.pushed && !pullRequest && (
          <button className="primary" onClick={onCreatePr} disabled={creatingPr || !canCreatePr}>
            {creatingPr ? 'Opening…' : 'Create pull request'}
          </button>
        )}
        {result?.pushed && !canCreatePr && (
          <span className="muted small">
            A GitHub token is required to open a pull request.
          </span>
        )}
      </div>
    </Card>
  )
}
