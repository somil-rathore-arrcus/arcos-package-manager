import { useMemo, useState } from 'react'
import type { CommitInfo, ComparisonResult } from '../types/api'
import { criticalityLabel, criticalityTone, formatDate } from '../utils/format'
import { Badge, Card, ExternalLink, Stat } from './ui'

const webUrl = (repository: string) => {
  let url = repository
  for (const prefix of ['ssh://git@', 'git@', 'git://']) {
    if (url.startsWith(prefix)) {
      url = 'https://' + url.slice(prefix.length).replace(':', '/')
      break
    }
  }
  return url.endsWith('.git') ? url.slice(0, -4) : url
}

type Filter = 'all' | 'critical' | 'stable' | 'normal' | 'unknown' | 'backported' | 'arcos'

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'Missing upstream' },
  { id: 'critical', label: 'Critical' },
  { id: 'stable', label: 'Stable' },
  { id: 'normal', label: 'Normal' },
  { id: 'unknown', label: 'Unknown' },
  { id: 'backported', label: 'Already backported' },
  { id: 'arcos', label: 'ARCoS-specific' },
]

export function CommitTable({
  comparison, selected, onSelectedChange, onPreview, previewing,
}: {
  comparison: ComparisonResult
  selected: Set<string>
  onSelectedChange: (next: Set<string>) => void
  onPreview: () => void
  previewing: boolean
}) {
  const [filter, setFilter] = useState<Filter>('all')
  const summary = comparison.summary
  const upstreamWeb = webUrl(comparison.upstream_repository)

  const rows = useMemo<CommitInfo[]>(() => {
    switch (filter) {
      case 'critical':
        return comparison.missing_upstream.filter((c) => c.criticality.level === 'CRITICAL')
      case 'stable':
        return comparison.missing_upstream.filter((c) => c.criticality.level === 'STABLE_RELEVANT')
      case 'normal':
        return comparison.missing_upstream.filter((c) => c.criticality.level === 'NORMAL')
      case 'unknown':
        return comparison.missing_upstream.filter((c) => c.criticality.level === 'UNKNOWN')
      case 'backported':
        return comparison.already_backported
      case 'arcos':
        return comparison.arcos_only
      default:
        return comparison.missing_upstream
    }
  }, [comparison, filter])

  // Only commits that are genuinely missing can be pulled: an ARCoS-specific
  // commit is already there, and a backported one is already applied.
  const selectable = filter !== 'backported' && filter !== 'arcos'

  const toggle = (sha: string) => {
    const next = new Set(selected)
    next.has(sha) ? next.delete(sha) : next.add(sha)
    onSelectedChange(next)
  }

  const selectCritical = () => {
    const next = new Set(selected)
    comparison.missing_upstream
      .filter((c) => c.criticality.level === 'CRITICAL')
      .forEach((c) => next.add(c.sha))
    onSelectedChange(next)
  }

  return (
    <Card title="Comparison">
      <dl className="definitions" style={{ marginBottom: 14 }}>
        <dt>Upstream</dt>
        <dd>
          <ExternalLink href={upstreamWeb}>{comparison.upstream_repository}</ExternalLink>
          {' @ '}<code>{comparison.upstream_ref}</code>
          {comparison.upstream_commit && (
            <> · <code className="muted">{comparison.upstream_commit.slice(0, 12)}</code></>
          )}
        </dd>
        <dt>ARCoS</dt>
        <dd>
          <code>{comparison.arcos_branch}</code>
          {comparison.arcos_commit && (
            <> · <code className="muted">{comparison.arcos_commit.slice(0, 12)}</code></>
          )}
        </dd>
        <dt>Common ancestor</dt>
        <dd>
          {summary.merge_base
            ? <code>{summary.merge_base.slice(0, 12)}</code>
            : <span className="muted">none</span>}
        </dd>
      </dl>

      <div className="stat-row" style={{ marginBottom: 16 }}>
        <Stat
          value={summary.missing_upstream + summary.already_backported}
          label="Upstream commits since ancestor"
        />
        <Stat value={summary.missing_upstream} label="Missing upstream" />
        <Stat value={summary.arcos_only} label="ARCoS-specific" />
        <Stat value={summary.already_backported} label="Already backported" />
        <Stat value={summary.critical} label="Critical" />
        <Stat value={summary.stable_relevant} label="Stable" />
        <Stat value={summary.normal} label="Normal" />
        <Stat value={summary.unknown_criticality} label="Unknown" />
      </div>
      <p className="small muted" style={{ marginTop: -6 }}>
        Counted from the commit graph, never from version strings. ARCoS-specific
        commits and commits already present under a different SHA are excluded
        from the missing count.
      </p>

      {comparison.warnings.map((w, i) => (
        <div className="alert warn" key={i}><p>{w}</p></div>
      ))}

      <div className="row" style={{ marginBottom: 12 }}>
        {FILTERS.map((f) => (
          <button
            key={f.id}
            className={filter === f.id ? 'primary' : undefined}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="actions" style={{ marginBottom: 12 }}>
        <button onClick={selectCritical} disabled={summary.critical === 0}>
          Select critical ({summary.critical})
        </button>
        <button onClick={() => onSelectedChange(new Set())} disabled={selected.size === 0}>
          Clear
        </button>
        <button className="primary" onClick={onPreview} disabled={selected.size === 0 || previewing}>
          {previewing ? 'Previewing…' : `Preview ${selected.size} selected`}
        </button>
        <span className="muted small">
          Selection is never automatic beyond what you choose here.
        </span>
      </div>

      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th className="shrink" />
              <th className="shrink">Commit</th>
              <th>Subject</th>
              <th className="shrink">Criticality</th>
              <th>Evidence</th>
              <th className="shrink">Author</th>
              <th className="shrink">Date</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((commit) => (
              <tr key={commit.sha}>
                <td className="shrink">
                  {selectable && (
                    <input
                      type="checkbox"
                      aria-label={`Select ${commit.short_sha}`}
                      checked={selected.has(commit.sha)}
                      onChange={() => toggle(commit.sha)}
                    />
                  )}
                </td>
                <td className="shrink mono">
                  <ExternalLink href={commit.web_url}>{commit.short_sha}</ExternalLink>
                  <div className="small muted" title={commit.sha}>
                    {commit.patch.patch_id
                      ? `patch-id ${commit.patch.patch_id.slice(0, 8)}`
                      : 'patch-id n/a'}
                  </div>
                </td>
                <td>
                  {commit.subject}
                  {commit.classification === 'ALREADY_BACKPORTED' && commit.patch.equivalent_sha && (
                    <div className="small muted">
                      already present as <code>{commit.patch.equivalent_sha.slice(0, 12)}</code>
                    </div>
                  )}
                </td>
                <td className="shrink">
                  <Badge tone={criticalityTone[commit.criticality.level]}>
                    {criticalityLabel[commit.criticality.level]}
                  </Badge>
                </td>
                <td className="small">
                  {commit.criticality.evidence.length > 0
                    ? commit.criticality.evidence.join('; ')
                    : <span className="muted">No evidence recorded</span>}
                </td>
                <td className="shrink small">{commit.author_name}</td>
                <td className="shrink small mono">{formatDate(commit.authored_at)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={7} className="muted" style={{ padding: 18 }}>
                Nothing in this category.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
