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
        <dt>Merge base{summary.merge_bases.length > 1 ? 's' : ''}</dt>
        <dd>
          {summary.merge_bases.length > 0
            ? summary.merge_bases.map((m) => <code key={m} style={{ marginRight: 6 }}>{m.slice(0, 12)}</code>)
            : <span className="muted">{summary.synthesized_ancestry ? 'none - synthesized from a content match' : 'none'}</span>}
        </dd>
        {(summary.base_tag || summary.series) && (
          <>
            <dt>Release</dt>
            <dd>
              upstream base <code>{summary.base_tag ?? '—'}</code>
              {summary.series && <> · series <code>{summary.series}</code></>}
            </dd>
          </>
        )}
        <dt>Basis</dt>
        <dd className="small">{summary.counts_basis}</dd>
      </dl>

      <div className="stat-row" style={{ marginBottom: 16 }}>
        <Stat value={summary.relevant_upstream} label="Relevant upstream commits" />
        <Stat
          value={summary.definitely_present + summary.probably_present}
          label={`Already present (${summary.definitely_present} definite)`}
        />
        <Stat value={summary.missing} label="Missing" />
        <Stat value={summary.unknown_presence} label="Unknown" />
        <Stat value={summary.critical} label="Critical/security missing" />
        <Stat value={summary.stable_relevant} label="Stable-nominated" />
        <Stat value={summary.arcos_only} label="ARCoS-specific" />
      </div>
      <p className="small muted" style={{ marginTop: -6 }}>
        Relevant upstream commits are the commits on the selected release ref that
        the ARCoS commit cannot reach. Each is checked for presence by{' '}
        {summary.backport_detection || 'the available evidence'}; a differing
        patch-id alone is never taken to mean missing, and no evidence of
        security is shown as Unknown, not safe.
        {summary.reverted_upstream > 0 && ` ${summary.reverted_upstream} commit(s) were reverted upstream and net to nothing.`}
        {summary.packaging_commits_included && ' The upstream here is the Debian packaging repository, so these are packaging commits.'}
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
              <th className="shrink">Presence</th>
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
                  {commit.presence
                    ? <Badge tone={PRESENCE_TONE[commit.presence]}>{commit.presence.replace(/_/g, ' ').toLowerCase()}</Badge>
                    : <span className="muted small">—</span>}
                  {commit.in_base_release && <div className="small muted">in the shipped release</div>}
                  {commit.reverted_by && <div className="small muted">reverted upstream</div>}
                </td>
                <td className="shrink">
                  <Badge tone={criticalityTone[commit.criticality.level]}>
                    {criticalityLabel[commit.criticality.level]}
                  </Badge>
                </td>
                <td className="small">
                  {commit.criticality.evidence.length > 0
                    ? commit.criticality.evidence.join('; ')
                    : <span className="muted">No security evidence found (unknown, not safe)</span>}
                  {commit.presence_evidence.length > 0 && (
                    <div className="muted">{commit.presence_evidence.join('; ')}</div>
                  )}
                </td>
                <td className="shrink small">{commit.author_name}</td>
                <td className="shrink small mono">{formatDate(commit.authored_at)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={8} className="muted" style={{ padding: 18 }}>
                Nothing in this category.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {comparison.debian_patches && (
        <div style={{ marginTop: 18 }}>
          <h3 className="small">
            Debian patches for {comparison.debian_patches.version}
            <span className="muted"> — packaging, never counted as upstream commits</span>
          </h3>
          {!comparison.debian_patches.available
            ? <p className="muted small">{comparison.debian_patches.reason}</p>
            : (
              <ul className="evidence">
                {comparison.debian_patches.patches.map((p) => (
                  <li key={p.name}>
                    <Badge tone={p.category === 'security' ? 'tone-bad' : 'tone-neutral'}>{p.category}</Badge>{' '}
                    <code>{p.name}</code> {p.subject}
                    {p.cve_ids.length > 0 && <> · {p.cve_ids.join(', ')}</>}
                    {p.presence && <> · <strong>{p.presence.replace(/_/g, ' ').toLowerCase()}</strong></>}
                    {p.presence_evidence.length > 0 && (
                      <div className="muted small">{p.presence_evidence.join('; ')}</div>
                    )}
                  </li>
                ))}
                {comparison.debian_patches.patches.length === 0 && (
                  <li className="muted">No quilt patches.</li>
                )}
              </ul>
            )}
        </div>
      )}

      {(comparison.security.length > 0 || comparison.security_sources.length > 0) && (
        <div style={{ marginTop: 18 }}>
          <h3 className="small">External security evidence</h3>
          <p className="muted small">{comparison.security_sources.join(' · ')}</p>
          <ul className="evidence">
            {comparison.security.map((f) => (
              <li key={`${f.identifier}-${f.sources.join()}`}>
                <Badge tone={f.status === 'missing' ? 'tone-bad' : 'tone-neutral'}>{f.status}</Badge>{' '}
                <ExternalLink href={f.url ?? undefined}>{f.identifier}</ExternalLink>
                {f.aliases.length > 0 && <> ({f.aliases.join(', ')})</>} · {f.sources.join(', ')}
                {f.summary && <div className="muted small">{f.summary}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

const PRESENCE_TONE: Record<string, string> = {
  DEFINITELY_PRESENT: 'tone-good',
  PROBABLY_PRESENT: 'tone-good',
  MISSING: 'tone-warn',
  UNKNOWN: 'tone-neutral',
}
