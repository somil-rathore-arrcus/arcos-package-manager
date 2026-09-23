import type { PullRequest, UpstreamMdDocument } from '../types/api'
import { Badge, Card, ExternalLink } from './ui'

const TONE: Record<string, string> = {
  CREATED: 'tone-good', UPDATED: 'tone-info',
  NO_CHANGE: 'tone-neutral', CONFLICT: 'tone-bad',
}

export function UpstreamMdPanel({
  document, pullRequest, onGenerate, generating, onCreatePr, creatingPr, canCreatePr,
}: {
  document: UpstreamMdDocument | null
  pullRequest: PullRequest | null
  onGenerate: () => void
  generating: boolean
  onCreatePr: () => void
  creatingPr: boolean
  canCreatePr: boolean
}) {
  return (
    <Card
      title="debian/upstream.md"
      actions={document && <Badge tone={TONE[document.outcome]}>{document.outcome}</Badge>}
    >
      <p className="small muted" style={{ marginTop: 0 }}>
        Generated from the verified resolution above, so the committed record and
        this dashboard cannot disagree. Generating writes nothing to the repository.
      </p>

      <div className="actions">
        <button onClick={onGenerate} disabled={generating}>
          {generating ? 'Generating…' : 'Generate'}
        </button>
        {document && document.outcome !== 'NO_CHANGE' && (
          <button className="primary" onClick={onCreatePr} disabled={creatingPr || !canCreatePr}>
            {creatingPr ? 'Opening…' : 'Create pull request'}
          </button>
        )}
        {document?.outcome === 'NO_CHANGE' && (
          <span className="muted small">Already up to date — nothing to propose.</span>
        )}
      </div>

      {document?.outcome === 'CONFLICT' && (
        <div className="alert warn" style={{ marginTop: 12 }}>
          <h3>Existing file was not written by this tool</h3>
          <p>It may hold information this tool does not know. Review before replacing.</p>
        </div>
      )}

      {document && (
        <pre className="diff" style={{ marginTop: 12 }}>{document.diff ?? document.content}</pre>
      )}

      {pullRequest?.url && (
        <div className="alert info" style={{ marginTop: 12 }}>
          <p><ExternalLink href={pullRequest.url}>{pullRequest.url}</ExternalLink></p>
        </div>
      )}
    </Card>
  )
}
