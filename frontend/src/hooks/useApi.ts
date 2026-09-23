import { useCallback, useEffect, useRef, useState } from 'react'

export interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: unknown
}

/** Run an async call on demand, keeping loading and error state together. */
export function useAsyncAction<Args extends unknown[], T>(
  fn: (...args: Args) => Promise<T>,
) {
  const [state, setState] = useState<AsyncState<T>>({
    data: null, loading: false, error: null,
  })
  const mounted = useRef(true)
  useEffect(() => {
    // Set on every mount, not just declared once. React 18 StrictMode runs
    // effects mount -> cleanup -> mount in development, so a cleanup that only
    // ever sets this false leaves it false for the rest of the session: the
    // request still completes, but every setState after the await is skipped
    // and the UI stays on its loading state forever.
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const run = useCallback(async (...args: Args) => {
    setState((s) => ({ ...s, loading: true, error: null }))
    try {
      const data = await fn(...args)
      if (mounted.current) setState({ data, loading: false, error: null })
      return data
    } catch (error) {
      if (mounted.current) setState({ data: null, loading: false, error })
      return null
    }
  }, [fn])

  const reset = useCallback(
    () => setState({ data: null, loading: false, error: null }), [],
  )

  return { ...state, run, reset, setData: (data: T | null) => setState((s) => ({ ...s, data })) }
}

/** Fetch whenever the dependencies change. */
export function useAsyncData<T>(
  fn: () => Promise<T>, deps: unknown[], enabled = true,
): AsyncState<T> & { reload: () => void } {
  const [state, setState] = useState<AsyncState<T>>({
    data: null, loading: enabled, error: null,
  })
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    if (!enabled) {
      setState({ data: null, loading: false, error: null })
      return
    }
    setState((s) => ({ ...s, loading: true, error: null }))
    fn()
      .then((data) => { if (!cancelled) setState({ data, loading: false, error: null }) })
      .catch((error) => { if (!cancelled) setState({ data: null, loading: false, error }) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, nonce])

  return { ...state, reload: () => setNonce((n) => n + 1) }
}
