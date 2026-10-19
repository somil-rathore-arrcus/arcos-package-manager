import type { PublishResult, PullRequest, UpstreamMdDocument } from '../types/api'
import { PublishStatusPanel } from './PublishStatusPanel'
import { Badge, Card, Definition, ExternalLink } from './ui'

const TONE: Record<string, string> = {
  CREATED: 'tone-good', UPDATED: 'tone-info',
  NO_CHANGE: 'tone-neutral', CONFLICT: 'tone-bad',
}

const RESULT_TONE: Record<string, string> = {
  DRY_RUN_OK: 'tone-good', BRANCH_PUSHED: 'tone-info',
}

// A link longer than GitHub accepts is built without the description, which
// then has to be pasted on the form; ARCoS CI rejects a PR without it.
const hasBody = (link: string) => link.includes('&body=')

export function UpstreamMdPanel({
  document, pullRequest, onGenerate, generating, onCreatePr, creatingPr, canCreatePr,
  dryRun, onDryRun, dryRunning, pushed, onPush, pushing,
}: {
  document: UpstreamMdDocument | null
  pullRequest: PullRequest | null
  onGenerate: () => void
  generating: boolean
  onCreatePr: () => void
  creatingPr: boolean
  canCreatePr: boolean
  dryRun: PublishResult | null
  onDryRun: () => void
  dryRunning: boolean
  pushed: PublishResult | null
  onPush: () => void
  pushing: boolean
}) {
  const proposable = !!document && document.outcome !== 'NO_CHANGE'
  // Without a token the branch is pushed over SSH and opened from a link, and
  // only after a dry run has shown exactly where it would go.
  const canPush = !canCreatePr && dryRun?.status === 'DRY_RUN_OK' && !pushed
  const flags = document?.review_flags ?? []

  return (
    <Card
      title="Metadata PR · debian/upstream.md"
      actions={document && <Badge tone={TONE[document.outcome]}>{document.outcome}</Badge>}
    >
      <p className="small muted" style={{ marginTop: 0 }}>
        Proposes one file, <code>debian/upstream.md</code>, recording the verified
        upstream - no code changes. Generated from the verified resolution above,
        so the committed record and this dashboard cannot disagree. Generating and
        dry runs write nothing to the repository.
      </p>

      <div className="actions">
        <button onClick={onGenerate} disabled={generating}>
          {generating ? 'Generating…' : 'Generate'}
        </button>
        {proposable && (
          <button onClick={onDryRun} disabled={dryRunning}>
            {dryRunning ? 'Checking…' : 'Dry run'}
          </button>
        )}
        {proposable && canCreatePr && (
          <button className="primary" onClick={onCreatePr} disabled={creatingPr}>
            {creatingPr ? 'Opening…' : 'Create pull request'}
          </button>
        )}
        {document?.outcome === 'NO_CHANGE' && (
          <span className="muted small">Already up to date — nothing to propose.</span>
        )}
      </div>

      {document?.outcome === 'CONFLICT' && (
        <div className="alert warn" style={{ marginTop: 12 }}>
          <h3>Existing file was not written by this tool</h3>
          <p>It may hold information this tool does not know. Review before replacing.</p>
        </div>
      )}

      {document && (
        <pre className="diff" style={{ marginTop: 12 }}>{document.diff ?? document.content}</pre>
      )}

      {flags.length > 0 && (
        <div className="alert warn" style={{ marginTop: 12 }}>
          <h3>Check before merging</h3>
          <p className="small">A flag does not block publishing; the pull request repeats it.</p>
          <ul className="small">
            {flags.map((f) => <li key={f.code}><strong>{f.code}</strong>: {f.detail}</li>)}
          </ul>
        </div>
      )}

      {dryRun && !pushed && (
        <div className="alert info" style={{ marginTop: 12 }}>
          <h3>
            Dry run <Badge tone={RESULT_TONE[dryRun.status] ?? 'tone-bad'}>{dryRun.status}</Badge>
          </h3>
          <p className="small">Nothing was pushed. No pull request was opened.</p>
          <dl className="small">
            <Definition term="Repository">{dryRun.repository}</Definition>
            <Definition term="Target branch">{dryRun.base_branch}</Definition>
            <Definition term="Branch">{dryRun.branch}</Definition>
            {dryRun.base_sha && <Definition term="Built on">{dryRun.base_sha.slice(0, 12)}</Definition>}
            {dryRun.title && <Definition term="Title">{dryRun.title}</Definition>}
            {dryRun.error && <Definition term="Note">{dryRun.error}</Definition>}
          </dl>
          {canPush && (
            <div className="actions">
              <span className="small">
                Push <code>{dryRun.branch}</code> to {dryRun.repository}? No pull request is
                opened: you open it from the link, with access to the repository.
              </span>
              <button className="primary" onClick={onPush} disabled={pushing}>
                {pushing ? 'Pushing…' : 'Push branch (no PR)'}
              </button>
            </div>
          )}
        </div>
      )}

      {pushed?.pr_create_url && (
        <div className="alert info" style={{ marginTop: 12 }}>
          <h3>
            Branch pushed <Badge tone="tone-info">{pushed.status}</Badge>
          </h3>
          <p className="small">
            <code>{pushed.branch}</code> is on {pushed.repository}
            {pushed.commit && <> at {pushed.commit.slice(0, 12)}</>}. No pull request was created.
          </p>
          <p>
            <ExternalLink href={pushed.pr_create_url}>Open the pull request on GitHub</ExternalLink>
          </p>
          {!hasBody(pushed.pr_create_url) && (
            <p className="small">
              The description was too long for the link. A push-only dry run
              (<code>apm publish-upstream-md --release {pushed.release} --package{' '}
              {pushed.package} --push-only</code>) writes it to{' '}
              <code>out/upstream-md-pr-links-dryrun-{pushed.release}.md</code>; paste it
              on the form before creating the pull request.
            </p>
          )}
        </div>
      )}

      {pullRequest?.url && (
        <div className="alert info" style={{ marginTop: 12 }}>
          <p><ExternalLink href={pullRequest.url}>{pullRequest.url}</ExternalLink></p>
        </div>
      )}

      {pushed && (
        <PublishStatusPanel pkg={pushed.package} branch={pushed.branch} base={pushed.base_branch} />
      )}
      {!pushed && document && pullRequest?.head && (
        <PublishStatusPanel pkg={document.package} branch={pullRequest.head} base={pullRequest.base} />
      )}
    </Card>
  )
}
