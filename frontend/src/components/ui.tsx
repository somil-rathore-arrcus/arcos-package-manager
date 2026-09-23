import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'

export function Card({ title, actions, children }: {
  title?: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="card">
      {title && (
        <h2 style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>{title}</span>
          {actions}
        </h2>
      )}
      <div className="body">{children}</div>
    </section>
  )
}

export function Field({ label, htmlFor, hint, children }: {
  label: string
  htmlFor?: string
  hint?: ReactNode
  children: ReactNode
}) {
  // The label element wraps only the label text. Wrapping the hint too would
  // fold it into the control's accessible name, so a screen reader would
  // announce "Package / repository 52 packages ship in bookworm" as the field's
  // name and it would change every time the hint did.
  return (
    <div className="field">
      <label htmlFor={htmlFor}><span>{label}</span></label>
      {children}
      {hint && <div className="small muted" style={{ marginTop: 4 }}>{hint}</div>}
    </div>
  )
}

export function Badge({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>
}

export function ExternalLink({ href, children }: { href?: string | null; children: ReactNode }) {
  if (!href) return <span className="muted">{children}</span>
  return (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  )
}

export function Definition({ term, children }: { term: string; children: ReactNode }) {
  return (
    <>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </>
  )
}

export function Loading({ what, hint }: { what: string; hint?: string }) {
  // A bare spinner cannot be told apart from a frozen page. Counting the
  // seconds shows the request is alive, and naming the slow part explains why
  // it is taking as long as it is.
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setSeconds((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="spinner" role="status">
      Loading {what}… <span className="mono">{seconds}s</span>
      {hint && seconds >= 5 && (
        <div className="small muted" style={{ marginTop: 4 }}>{hint}</div>
      )}
    </div>
  )
}

export function Stat({ value, label, tone }: { value: ReactNode; label: string; tone?: string }) {
  return (
    <div className="stat">
      <div className="value" style={tone ? undefined : undefined}>{value}</div>
      <div className="label">{label}</div>
    </div>
  )
}
