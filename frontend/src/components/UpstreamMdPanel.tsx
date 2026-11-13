import type { PublishResult, UpstreamMdDocument } from '../types/api'
import { PublishStatusPanel } from './PublishStatusPanel'
import { Badge, Card, Definition, ExternalLink } from './ui'

const TONE: Record<string, string> = {
  CREATED: 'tone-good', UPDATED: 'tone-info',
  NO_CHANGE: 'tone-neutral', CONFLICT: 'tone-bad',
}

// Anything not listed is a refusal for a person to read. COVERED is neither a
// success nor a failure: another release's branch already carries the file.
const RESULT_TONE: Record<string, string> = {
  DRY_RUN_OK: 'tone-good', BRANCH_PUSHED: 'tone-info', COVERED: 'tone-neutral',
  PR_EXISTS: 'tone-info', PR_MERGED: 'tone-good',
}

// A link longer than GitHub accepts is built without the description, which
// then has to be pasted on the form; ARCoS CI rejects a PR without it.
const hasBody = (link: string) => link.includes('&body=')

/** The branch on GitHub: the Compare Link a person opens the PR from, and the file. */
function BranchLinks({ result }: { result: PublishResult }) {
  if (!result.compare_url && !result.file_url) return null
  return (
    <p className="small" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 6 }}>
      {result.compare_url && <ExternalLink href={result.compare_url}>Compare Link</ExternalLink>}
      {result.file_url && (
        <ExternalLink href={result.file_url}>debian/upstream.md on the branch</ExternalLink>
      )}
    </p>
  )
}

// One target branch takes one debian/upstream.md: a second branch carrying the
// same bytes would only duplicate the first, so nothing is pushed.
function Covered({ result }: { result: PublishResult }) {
  return (
    <p className="small">
      Covered by {result.covered_by ? `the ${result.covered_by}` : "another release's"} branch{' '}
      <code>{result.branch}</code> — nothing to push. It already proposes this exact
      file to {result.base_branch}.
    </p>
  )
}

export function UpstreamMdPanel({
  document, onGenerate, generating, dryRun, onDryRun, dryRunning,
  pushed, onPush, pushing, pushEnabled,
}: {
  document: UpstreamMdDocument | null
  onGenerate: () => void
  generating: boolean
  dryRun: PublishResult | null
  onDryRun: () => void
  dryRunning: boolean
  /** What the push returned: BRANCH_PUSHED, COVERED, or a pull request it found. */
  pushed: PublishResult | null
  onPush: () => void
  pushing: boolean
  /** The deployment allows pushing; without it the server refuses with PUSH_DISABLED. */
  pushEnabled: boolean
}) {
  const proposable = !!document && document.outcome !== 'NO_CHANGE'
  // Pushed only after a dry run has shown exactly where it would go, and once
  // per view. The tool never opens the pull request: a person does, from the link.
  const canPush = pushEnabled && dryRun?.status === 'DRY_RUN_OK' && !pushed
  const flags = document?.review_flags ?? []
  // A pull request the server found for the branch - shown, never acted on.
  const latest = pushed ?? dryRun
  const found = latest?.pull_request?.url ? latest : null
  // The branch whose pull request and CI are worth following from here.
  const tracked = pushed ?? found

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
      <p className="small muted" style={{ marginTop: 6 }}>
        The repository, target branch and branch name come from the reviewed plan.
        The ARCoS target branch chosen under Selection affects the comparison only,
        never where this metadata is published.
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
          {dryRun.status === 'COVERED' && <Covered result={dryRun} />}
          <dl className="small">
            <Definition term="Repository">{dryRun.repository}</Definition>
            <Definition term="Target branch (reviewed plan)">{dryRun.base_branch}</Definition>
            <Definition term="Branch">{dryRun.branch}</Definition>
            {dryRun.base_sha && <Definition term="Built on">{dryRun.base_sha.slice(0, 12)}</Definition>}
            {dryRun.title && <Definition term="Title">{dryRun.title}</Definition>}
            {dryRun.error && <Definition term="Note">{dryRun.error}</Definition>}
          </dl>
          <BranchLinks result={dryRun} />
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
          {!pushEnabled && dryRun.status === 'DRY_RUN_OK' && (
            <p className="small" style={{ marginTop: 6 }}>
              Pushing is disabled on this deployment, so this branch cannot be pushed from here.
            </p>
          )}
        </div>
      )}

      {pushed?.status === 'BRANCH_PUSHED' && (
        <div className="alert info" style={{ marginTop: 12 }}>
          <h3>
            Branch pushed <Badge tone="tone-info">{pushed.status}</Badge>
          </h3>
          <p className="small">
            <code>{pushed.branch}</code> is on {pushed.repository}
            {pushed.commit && <> at {pushed.commit.slice(0, 12)}</>}, targeting{' '}
            {pushed.base_branch} from the reviewed plan. No pull request was created.
          </p>
          {pushed.pr_create_url && (
            <p style={{ marginTop: 6 }}>
              <ExternalLink href={pushed.pr_create_url}>Open the pull request on GitHub</ExternalLink>
            </p>
          )}
          <BranchLinks result={pushed} />
          {pushed.pr_create_url && !hasBody(pushed.pr_create_url) && (
            <p className="small">
              The description was too long for the link. A dry run
              (<code>apm publish-upstream-md --release {pushed.release} --package{' '}
              {pushed.package}</code>) writes it to{' '}
              <code>out/upstream-md-pr-links-dryrun-{pushed.release}.md</code>; paste it
              on the form before creating the pull request.
            </p>
          )}
        </div>
      )}

      {pushed?.status === 'COVERED' && (
        <div className="alert neutral" style={{ marginTop: 12 }}>
          <h3>
            Nothing pushed <Badge tone="tone-neutral">{pushed.status}</Badge>
          </h3>
          <Covered result={pushed} />
          <BranchLinks result={pushed} />
        </div>
      )}

      {found?.pull_request?.url && (
        <div className="alert info" style={{ marginTop: 12 }}>
          <h3>
            Pull request found <Badge tone={RESULT_TONE[found.status] ?? 'tone-neutral'}>{found.status}</Badge>
          </h3>
          <p className="small">
            GitHub reports this pull request for <code>{found.branch}</code>. This tool
            did not open it and changes nothing on it.
          </p>
          <p><ExternalLink href={found.pull_request.url}>{found.pull_request.url}</ExternalLink></p>
          {pushed?.error && <p className="small" style={{ marginTop: 6 }}>{pushed.error}</p>}
        </div>
      )}

      {tracked && (
        <PublishStatusPanel pkg={tracked.package} branch={tracked.branch} base={tracked.base_branch} />
      )}
    </Card>
  )
}
