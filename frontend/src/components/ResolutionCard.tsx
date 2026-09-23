import { useState } from 'react'
import type { UpstreamResolution } from '../types/api'
import { statusTone } from '../utils/format'
import { Badge, Card, Definition, ExternalLink } from './ui'

const METHOD_LABEL: Record<string, string> = {
  ancestry: 'Proven by shared git history',
  curated: 'Hand-verified mapping',
  kernel_series: 'Kernel stable series, from the Debian version',
  dep12: 'Debian DEP-12 upstream metadata',
  watch_forge: 'debian/watch pointed at a git forge',
  homepage_forge: 'Debian Homepage field',
  manual: 'Supplied by a user and verified',
  unresolved: 'Not resolved',
  not_applicable: 'Not applicable',
}

export function ResolutionCard({
  resolution, onCompare, comparing,
}: {
  resolution: UpstreamResolution
  onCompare: () => void
  comparing: boolean
}) {
  const [showEvidence, setShowEvidence] = useState(false)
  const upstream = resolution.upstream_repository
  const canCompare =
    (resolution.status === 'VERIFIED' || resolution.status === 'PARTIAL') &&
    !!upstream && !!resolution.upstream_ref

  return (
    <Card
      title="Resolved upstream"
      actions={<Badge tone={statusTone[resolution.status]}>{resolution.status}</Badge>}
    >
      <dl className="definitions">
        <Definition term="Package">
          <code>{resolution.package}</code> · {resolution.debian_release}
        </Definition>
        <Definition term="ARCoS repository">
          <ExternalLink href={`https://github.com/${resolution.github_repository}`}>
            {resolution.github_repository || '—'}
          </ExternalLink>
        </Definition>
        <Definition term="ARCoS branch">
          <code>{resolution.arcos_branch}</code>
          {resolution.arcos_release && (
            <> · release <code>{resolution.arcos_release}</code></>
          )}
          {resolution.arcos_commit && (
            <> · <code className="muted">{resolution.arcos_commit.slice(0, 12)}</code></>
          )}
        </Definition>
        {resolution.arcos_path && (
          <Definition term="ARCoS path">
            <code>{resolution.arcos_release
              ? `${resolution.arcos_release}/${resolution.arcos_path}`
              : resolution.arcos_path}</code>
          </Definition>
        )}
        {resolution.debian && (
          <Definition term="Debian source">
            <ExternalLink href={resolution.debian.web_url}>
              {resolution.debian.source_package} {resolution.debian.version}
            </ExternalLink>
          </Definition>
        )}
        {resolution.debian?.vcs_git && (
          <Definition term="Debian VCS repository">
            <ExternalLink href={resolution.debian.vcs_git}>
              {resolution.debian.vcs_git}
            </ExternalLink>
            {resolution.debian.vcs_branch && (
              <> · branch <code>{resolution.debian.vcs_branch}</code></>
            )}
            <div className="muted small">
              Debian packaging, not upstream — it is never used as the upstream.
            </div>
          </Definition>
        )}
        <Definition term="Upstream repository">
          {upstream
            ? <ExternalLink href={upstream.web_url ?? upstream.url}>{upstream.url}</ExternalLink>
            : <span className="muted">None</span>}
        </Definition>
        <Definition term={resolution.upstream_tag ? 'Upstream tag' : 'Upstream branch'}>
          {resolution.upstream_tag || resolution.upstream_branch || resolution.upstream_ref
            ? <code>{resolution.upstream_tag ?? resolution.upstream_branch ?? resolution.upstream_ref}</code>
            : '—'}
          {resolution.upstream_commit && (
            <> · <code className="muted">{resolution.upstream_commit.slice(0, 12)}</code></>
          )}
        </Definition>
        <Definition term="Origin">
          {resolution.origin_kind === 'debian_packaging'
            ? <span>Debian packaging repository <span className="muted small">— patches here are packaging changes</span></span>
            : resolution.origin_kind === 'project'
              ? "The project's own repository"
              : '—'}
        </Definition>
        <Definition term="How">
          {METHOD_LABEL[resolution.method] ?? resolution.method}
          {' '}<span className="muted small">({resolution.mode.toLowerCase()}, {resolution.confidence} confidence)</span>
        </Definition>
        {resolution.evidence_source && (
          <Definition term="Evidence source">
            {resolution.evidence_url
              ? <ExternalLink href={resolution.evidence_url}>{resolution.evidence_source}</ExternalLink>
              : resolution.evidence_source}
          </Definition>
        )}
        {resolution.verification && (
          <Definition term="Verification">
            <span className="small">{resolution.verification}</span>
          </Definition>
        )}
        {resolution.reason && resolution.status !== 'VERIFIED' && (
          <Definition term="Reason">
            <span className="small">{resolution.reason}</span>
          </Definition>
        )}
        {resolution.merge_base && (
          <Definition term="Common ancestor">
            <code>{resolution.merge_base.slice(0, 12)}</code>
            {resolution.behind != null && (
              <> · {resolution.behind} behind · {resolution.arcos_only ?? 0} ARCoS-only</>
            )}
          </Definition>
        )}
      </dl>

      {resolution.status === 'NO_UPSTREAM' && (
        <div className="alert info" style={{ marginTop: 14 }}>
          <h3>No verified upstream available</h3>
          <p>
            This package is {(resolution.category ?? 'unclassified').replace(/_/g, ' ')},
            so there is nothing upstream to compare against. That is a finished
            answer, not a failure to resolve.
          </p>
        </div>
      )}

      {resolution.status === 'NEEDS_REVIEW' && (
        <div className="alert warn" style={{ marginTop: 14 }}>
          <h3>Needs review</h3>
          <p>
            An upstream was found and exists, but it has not been confirmed. No
            upstream has been invented for this package — the evidence below is
            everything that is known.
          </p>
        </div>
      )}

      {resolution.notes.length > 0 && (
        <div className="alert warn" style={{ marginTop: 14 }}>
          {resolution.notes.map((note, i) => <p key={i}>{note}</p>)}
        </div>
      )}

      <div className="actions" style={{ marginTop: 14 }}>
        <button onClick={() => setShowEvidence((v) => !v)}>
          {showEvidence ? 'Hide evidence' : `View evidence (${resolution.evidence.length})`}
        </button>
        <button className="primary" onClick={onCompare} disabled={!canCompare || comparing}>
          {comparing ? 'Comparing…' : 'Compare'}
        </button>
        {!canCompare && (
          <span className="muted small">
            {resolution.status === 'NO_UPSTREAM'
              ? 'No verified upstream available.'
              : 'A verified upstream is needed before comparing.'}
          </span>
        )}
      </div>

      {showEvidence && (
        <div style={{ marginTop: 12 }}>
          <ul className="evidence">
            {resolution.evidence.map((item, i) => (
              <li key={i}>
                <span className="badge tone-neutral">{item.kind}</span>{' '}
                {item.url ? <ExternalLink href={item.url}>{item.detail}</ExternalLink> : item.detail}
              </li>
            ))}
            {resolution.evidence.length === 0 && <li className="muted">No evidence recorded.</li>}
          </ul>
          {resolution.candidates.length > 0 && (
            <>
              <h3 className="small" style={{ margin: '12px 0 6px' }}>Candidates considered</h3>
              <ul className="evidence">
                {resolution.candidates.map((c, i) => (
                  <li key={i}>
                    <Badge tone={c.accepted ? 'tone-good' : 'tone-neutral'}>
                      {c.accepted ? 'accepted' : 'rejected'}
                    </Badge>{' '}
                    <code>{c.repository}</code>{c.ref ? ` @ ${c.ref}` : ''}
                    {c.rejected_reason && <div className="muted small">{c.rejected_reason}</div>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </Card>
  )
}
