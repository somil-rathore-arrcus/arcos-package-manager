import { ApiError } from '../services/api'

// Each backend error code gets an explanation of what to actually do about it.
// "Request failed" tells the user nothing they can act on.
const GUIDANCE: Record<string, string> = {
  AUTH_REQUIRED:
    'Set APM_GITHUB_TOKEN in the backend environment. The token needs repo scope, and SSO authorisation if the organisation uses SAML.',
  REPOSITORY_UNAVAILABLE:
    'The repository could not be reached. Check the SSH bridge configuration; for a private repository this is also what missing access looks like.',
  BRANCH_NOT_FOUND:
    'Check the branch or tag name. The branch list on this page comes from the repository itself.',
  NO_COMMON_ANCESTOR:
    'This fork shares no commits with the upstream, so it was imported rather than forked. A commit comparison is not meaningful until a real origin is identified.',
  UPSTREAM_NOT_RESOLVED:
    'Resolve a verified upstream first, or supply one manually.',
  CONFLICT:
    'The existing file was not written by this tool and may hold information it does not know. Review it before replacing it.',
  NETWORK:
    'The API did not respond. Check the backend is running.',
  TIMEOUT:
    'The operation ran out of time. A first comparison clones both repositories, which can take minutes on a large package; the workspace is reused, so a retry is usually much faster.',
}

export function ErrorAlert({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null
  const api = error instanceof ApiError ? error : null
  const code = api?.code ?? 'INTERNAL'
  const message = error instanceof Error ? error.message : String(error)

  return (
    <div className="alert error" role="alert">
      <h3>{code.replace(/_/g, ' ')}</h3>
      <p>{message}</p>
      {GUIDANCE[code] && <p style={{ marginTop: 6 }}>{GUIDANCE[code]}</p>}
      {api?.detail && (
        <details style={{ marginTop: 6 }}>
          <summary className="small">Technical detail</summary>
          <pre className="diff" style={{ marginTop: 6 }}>{api.detail}</pre>
        </details>
      )}
      {onRetry && (
        <div className="actions" style={{ marginTop: 8 }}>
          <button onClick={onRetry}>Try again</button>
        </div>
      )}
    </div>
  )
}
