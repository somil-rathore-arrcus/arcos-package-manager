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

const PROVEN = ['SHARED_HISTORY', 'CONTENT_MATCH_APPROVED']

export function ResolutionCard({
  resolution, onCompare, comparing, onApproveContentBase, approving,
}: {
  resolution: UpstreamResolution
  onCompare: () => void
  comparing: boolean
  onApproveContentBase?: (tag: string, verifiedBy: string) => void
  approving?: boolean
}) {
  const [showEvidence, setShowEvidence] = useState(false)
  const [approver, setApprover] = useState('')
  const upstream = resolution.upstream_repository
  const selection = resolution.ref_selection
  const match = resolution.content_match
  const canCompare =
    (resolution.status === 'VERIFIED' || resolution.status === 'PARTIAL') &&
    PROVEN.includes(resolution.verification_level) &&
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
        <Definition term="Verification level">
          <Badge tone={PROVEN.includes(resolution.verification_level) ? 'tone-good' : 'tone-warn'}>
            {resolution.verification_level}
          </Badge>
          {resolution.review_reasons.length > 0 && (
            <> {resolution.review_reasons.map((r) => (
              <Badge key={r} tone={['PROBE_FAILED', 'NETWORK_ERROR', 'ARCOS_UNREACHABLE'].includes(r) ? 'tone-bad' : 'tone-warn'}>{r}</Badge>
            ))}</>
          )}
        </Definition>
        {selection && (
          <Definition term="Release reference">
            <code>{selection.ref}</code> ({selection.kind}, {selection.strategy.replace(/_/g, ' ')})
            {selection.is_fallback && <> <Badge tone="tone-warn">fallback</Badge></>}
            {selection.base_tag && (
              <> · base <code>{selection.base_tag}</code>
                {selection.arcos_contains_base === false && <span className="small"> (not in ARCoS)</span>}
              </>
            )}
            {selection.series && <> · series <code>{selection.series}</code></>}
            <div className="muted small">{selection.reason}</div>
          </Definition>
        )}
        {resolution.curated && (resolution.curated.repository || resolution.curated.conflict) && (
          <Definition term="Curated mapping">
            {resolution.curated.repository
              ? <><code>{resolution.curated.repository}</code> @ <code>{resolution.curated.ref ?? 'HEAD'}</code></>
              : 'no upstream'}
            {resolution.curated.conflict && (
              <div className="small"><Badge tone="tone-warn">conflict</Badge> {resolution.curated.conflict}</div>
            )}
          </Definition>
        )}
        {resolution.merge_base && (
          <Definition term={resolution.merge_bases.length > 1 ? 'Merge bases' : 'Common ancestor'}>
            {(resolution.merge_bases.length ? resolution.merge_bases : [resolution.merge_base]).map((m) => (
              <code key={m} style={{ marginRight: 6 }}>{m.slice(0, 12)}</code>
            ))}
          </Definition>
        )}
        {resolution.behind != null && (
          <Definition term="Raw commit counts">
            {resolution.behind} upstream commit(s) not in ARCoS · {resolution.arcos_only ?? 0} ARCoS commit(s) not upstream
            <div className="muted small">
              {resolution.counts_basis}. Not a list of missing fixes — compare to see what is present.
            </div>
          </Definition>
        )}
        {resolution.comparison && (
          <Definition term="Backlog (last comparison)">
            {resolution.comparison.relevant_upstream} relevant ·{' '}
            {resolution.comparison.definitely_present + resolution.comparison.probably_present} present ·{' '}
            {resolution.comparison.missing} missing · {resolution.comparison.unknown_presence} unknown ·{' '}
            {resolution.comparison.critical_missing} critical
          </Definition>
        )}
        {resolution.debian_patches?.available && (
          <Definition term="Debian patches">
            {resolution.debian_patches.patches.length} patch(es),{' '}
            {resolution.debian_patches.patches.filter((p) => p.category === 'security').length} security
            {resolution.debian_patches.cve_ids.length > 0 && <> · {resolution.debian_patches.cve_ids.join(', ')}</>}
          </Definition>
        )}
      </dl>

      {resolution.warnings.length > 0 && (
        <div className="alert warn" style={{ marginTop: 14 }}>
          <h3>Marked for review</h3>
          {resolution.warnings.map((w, i) => <p key={i}>{w}</p>)}
        </div>
      )}

      {match && match.base_tag && (
        <div className={`alert ${match.approved ? 'info' : 'warn'}`} style={{ marginTop: 14 }}>
          <h3>Content match — no shared git history</h3>
          <p>
            Closest upstream release by content: <code>{match.base_tag}</code> (score{' '}
            {(match.score ?? 0).toFixed(3)}, {match.files_differing} of {match.files_compared} files
            differ, compared against the {match.arcos_tree_label || 'ARCoS tree'}).
          </p>
          {match.approved
            ? <p>Approved by {match.verified_by} on {match.verified_at}. Counts are synthesized from it.</p>
            : onApproveContentBase && (
              <div className="row" style={{ gap: 8 }}>
                <input
                  type="text" placeholder="Your name (recorded as approver)"
                  aria-label="Approver name" value={approver}
                  onChange={(e) => setApprover(e.target.value)}
                />
                <button
                  disabled={!approver.trim() || approving}
                  onClick={() => onApproveContentBase(match.base_tag!, approver.trim())}
                >
                  {approving ? 'Recording…' : `Approve ${match.base_tag} as the base`}
                </button>
                <span className="muted small">Takes effect when the package is next resolved.</span>
              </div>
            )}
        </div>
      )}

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
            A candidate upstream exists, but its relationship to the fork is not
            proven or is contradicted ({resolution.review_reasons.join(', ') || 'see the reason'}).
            No upstream has been invented and no count is reported as fact.
          </p>
        </div>
      )}

      {resolution.status === 'FAILED' && (
        <div className="alert error" style={{ marginTop: 14 }}>
          <h3>Resolution could not complete</h3>
          <p>
            {resolution.review_reasons.join(', ')}: this is a failure to measure,
            not a finding about the package. Resolve again once the cause is fixed.
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
              : 'A proven relationship (shared history or an approved content match) is needed before comparing.'}
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
                    {c.strategy && <span className="muted small"> ({c.strategy.replace(/_/g, ' ')})</span>}
                    {c.behind != null && <span className="small"> · {c.behind} not in ARCoS</span>}
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
