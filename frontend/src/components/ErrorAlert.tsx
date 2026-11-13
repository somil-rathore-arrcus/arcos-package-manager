import { useState } from 'react'

import { ApiError, setApiToken } from '../services/api'

// Each backend error code gets an explanation of what to actually do about it.
// "Request failed" tells the user nothing they can act on.
const GUIDANCE: Record<string, string> = {
  AUTH_REQUIRED:
    'GitHub refused, or no GitHub credential is configured. If APM_GITHUB_TOKEN is empty, set it in the backend environment; if it is set, check it has not expired, can reach this repository with "Pull requests: Read and write", and is SSO-authorised. Without a token, push only and open the pull request from its link.',
  API_TOKEN_REQUIRED:
    'This deployment requires its own API token (APM_API_TOKEN) for actions that push a branch or record an approval. Ask whoever runs it for the token and enter it below; it is kept for this browser tab only.',
  RATE_LIMITED:
    "GitHub's API quota is used up for now. Nothing is wrong with the token or the repository and nothing was changed; retry after the time given.",
  NOT_FOUND:
    'Not found. For a private GitHub repository this is also what a token without access to it sees, so check the token before the spelling.',
  GIT_ERROR:
    'git failed; its own message is under Technical detail.',
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
  PUSH_DISABLED:
    'This deployment does not push branches, so nothing was pushed. Previews and dry runs still work. To push, ask whoever runs it to enable pushing, or use a deployment that allows it.',
  PULL_REQUESTS_DISABLED:
    'This tool never opens, submits or merges a pull request. It pushes the branch; a person with access to the repository opens the pull request from its Compare Link.',
}

// A code is a fine heading for most errors. These two are a deployment's
// policy rather than a failure, so they say so.
const TITLE: Record<string, string> = {
  PUSH_DISABLED: 'Pushing is disabled on this deployment',
  PULL_REQUESTS_DISABLED: 'This tool never opens pull requests',
}

export function ErrorAlert({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null
  const api = error instanceof ApiError ? error : null
  const code = api?.code ?? 'INTERNAL'
  const message = error instanceof Error ? error.message : String(error)

  return (
    <div className="alert error" role="alert">
      <h3>{TITLE[code] ?? code.replace(/_/g, ' ')}</h3>
      <p>{message}</p>
      {GUIDANCE[code] && <p style={{ marginTop: 6 }}>{GUIDANCE[code]}</p>}
      {api?.detail && (
        <details style={{ marginTop: 6 }}>
          <summary className="small">Technical detail</summary>
          <pre className="diff" style={{ marginTop: 6 }}>{api.detail}</pre>
        </details>
      )}
      {code === 'API_TOKEN_REQUIRED' && <ApiTokenForm />}
      {onRetry && (
        <div className="actions" style={{ marginTop: 8 }}>
          <button onClick={onRetry}>Try again</button>
        </div>
      )}
    </div>
  )
}

function ApiTokenForm() {
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(false)
  return (
    <form
      className="actions" style={{ marginTop: 8 }}
      onSubmit={(event) => {
        event.preventDefault()
        setApiToken(value.trim())
        setValue('')
        setSaved(true)
      }}
    >
      <label>
        API token{' '}
        <input
          type="password" autoComplete="off" value={value}
          onChange={(event) => { setValue(event.target.value); setSaved(false) }}
        />
      </label>
      <button type="submit" disabled={!value.trim()}>Use for this tab</button>
      {saved && <span className="small">Saved. Repeat the action.</span>}
    </form>
  )
}
