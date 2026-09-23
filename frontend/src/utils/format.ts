import type { Criticality, ResolutionStatus } from '../types/api'

export const shortSha = (sha?: string | null) => (sha ? sha.slice(0, 12) : '')

export const formatDate = (value?: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? ''
    : date.toISOString().slice(0, 10)
}

// NO_UPSTREAM is deliberately neutral, not a warning: a package with no upstream
// is a finished answer, not a problem to fix.
export const statusTone: Record<ResolutionStatus, string> = {
  VERIFIED: 'tone-good',
  PARTIAL: 'tone-warn',
  NEEDS_REVIEW: 'tone-warn',
  NO_UPSTREAM: 'tone-neutral',
  FAILED: 'tone-bad',
}

export const criticalityTone: Record<Criticality, string> = {
  CRITICAL: 'tone-bad',
  STABLE_RELEVANT: 'tone-warn',
  NORMAL: 'tone-info',
  UNKNOWN: 'tone-neutral',
}

export const criticalityLabel: Record<Criticality, string> = {
  CRITICAL: 'Critical',
  STABLE_RELEVANT: 'Stable',
  NORMAL: 'Normal',
  UNKNOWN: 'Unknown',
}
