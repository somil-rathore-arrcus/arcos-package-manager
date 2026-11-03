import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ErrorAlert } from '../ErrorAlert'
import { ApiError, api, setApiToken } from '../../services/api'

const user = userEvent.setup()

afterEach(() => {
  cleanup()
  setApiToken('')
  vi.unstubAllGlobals()
})

function stubFetch() {
  const fetch = vi.fn(async () => new Response(JSON.stringify({ version: 'x' }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const sentHeaders = (fetch: ReturnType<typeof stubFetch>) =>
  (fetch.mock.calls[0] as unknown as [string, RequestInit])[1].headers as Record<string, string>

describe('the deployment API token', () => {
  it('is not sent until a person enters it', async () => {
    const fetch = stubFetch()
    await api.health()
    expect(sentHeaders(fetch).Authorization).toBeUndefined()
  })

  it('is asked for when the server requires it, then sent with every request', async () => {
    render(<ErrorAlert error={new ApiError(
      'This deployment requires its API token.', 'API_TOKEN_REQUIRED', 401)} />)
    expect(screen.getByText(/kept for this browser tab only/)).toBeInTheDocument()

    await user.type(screen.getByLabelText(/API token/), 's3cret')
    await user.click(screen.getByRole('button', { name: 'Use for this tab' }))

    expect(screen.getByText(/Saved\. Repeat the action\./)).toBeInTheDocument()
    expect(screen.getByLabelText(/API token/)).toHaveValue('')
    const fetch = stubFetch()
    await api.health()
    expect(sentHeaders(fetch).Authorization).toBe('Bearer s3cret')
  })

  it('is not offered for a GitHub credential problem', () => {
    render(<ErrorAlert error={new ApiError('GitHub refused.', 'AUTH_REQUIRED', 401)} />)
    expect(screen.queryByLabelText(/API token/)).toBeNull()
    expect(screen.getByText(/push only and open the pull request from its link/))
      .toBeInTheDocument()
  })
})
