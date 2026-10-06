import type { Branch, DebianRelease, Package } from '../types/api'
import { Card, Field, Loading } from './ui'

export interface SelectionState {
  release: string
  pkg: string
  branch: string
  mode: 'AUTO' | 'MANUAL'
  manualRepository: string
  manualRef: string
}

export function SelectionPanel({
  releases, packages, branches, value, onChange,
  loadingPackages, loadingBranches, branchError, onResolve, resolving,
}: {
  releases: DebianRelease[]
  packages: Package[]
  branches: Branch[]
  value: SelectionState
  onChange: (next: Partial<SelectionState>) => void
  loadingPackages: boolean
  loadingBranches: boolean
  branchError?: string | null
  onResolve: () => void
  resolving: boolean
}) {
  const configured = branches.find((b) => b.is_configured)
  const typed = value.branch.trim()
  const known = !typed || branches.some((b) => b.name === typed)

  return (
    <Card title="Selection">
      <div className="grid cols-4">
        <Field label="Debian release" htmlFor="release">
          <select
            id="release"
            value={value.release}
            onChange={(e) => onChange({ release: e.target.value, pkg: '', branch: '' })}
          >
            {releases.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        </Field>

        <Field
          label="Package / repository"
          htmlFor="package"
          hint={
            loadingPackages
              ? 'Loading…'
              : `${packages.length} packages ship in ${value.release}`
          }
        >
          <select
            id="package"
            value={value.pkg}
            disabled={loadingPackages || packages.length === 0}
            onChange={(e) => onChange({ pkg: e.target.value, branch: '' })}
          >
            <option value="">Select a package…</option>
            {packages.map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
        </Field>

        <Field
          label="ARCoS target branch"
          htmlFor="branch"
          hint={
            branchError
              ? `${branchError} Type the branch name instead.`
              : loadingBranches
                ? 'Reading branches from the fork…'
                : !known
                  ? `"${typed}" is not in the fork's branch list; it is checked against the fork when you resolve.`
                  : configured
                    ? `${configured.name} is the branch this release ships. Pick another or type one.`
                    : 'Pick a branch from the fork, or type one.'
          }
        >
          <input
            id="branch"
            type="text"
            list="branch-options"
            autoComplete="off"
            spellCheck={false}
            placeholder={loadingBranches ? 'Loading…' : 'branch name'}
            value={value.branch}
            disabled={!value.pkg}
            onChange={(e) => onChange({ branch: e.target.value })}
          />
          <datalist id="branch-options">
            {branches.map((b) => (
              <option key={b.name} value={b.name}>
                {b.is_configured ? 'release default' : b.is_default ? 'repository default' : ''}
              </option>
            ))}
          </datalist>
        </Field>

        <Field label="Upstream resolution" htmlFor="upstream-mode">
          <fieldset id="upstream-mode">
            <legend>Source</legend>
            <div className="row">
              <label className="row" style={{ gap: 5 }}>
                <input
                  type="radio" name="mode" value="AUTO"
                  checked={value.mode === 'AUTO'}
                  onChange={() => onChange({ mode: 'AUTO' })}
                />
                Auto
              </label>
              <label className="row" style={{ gap: 5 }}>
                <input
                  type="radio" name="mode" value="MANUAL"
                  checked={value.mode === 'MANUAL'}
                  onChange={() => onChange({ mode: 'MANUAL' })}
                />
                Manual
              </label>
            </div>
          </fieldset>
        </Field>
      </div>

      {value.mode === 'MANUAL' && (
        <div className="grid cols-4" style={{ marginTop: 14 }}>
          <Field
            label="Upstream repository"
            htmlFor="manual-repo"
            hint="Checked the same way a discovered one is."
          >
            <input
              id="manual-repo" type="text" placeholder="https://github.com/owner/project.git"
              value={value.manualRepository}
              onChange={(e) => onChange({ manualRepository: e.target.value })}
            />
          </Field>
          <Field label="Branch or tag" htmlFor="manual-ref">
            <input
              id="manual-ref" type="text" placeholder="master"
              value={value.manualRef}
              onChange={(e) => onChange({ manualRef: e.target.value })}
            />
          </Field>
        </div>
      )}

      <div className="actions" style={{ marginTop: 16 }}>
        <button
          className="primary"
          onClick={onResolve}
          disabled={
            !value.pkg || resolving || !typed ||
            (value.mode === 'MANUAL' && (!value.manualRepository || !value.manualRef))
          }
        >
          {resolving ? 'Resolving…' : 'Resolve upstream'}
        </button>
        {resolving && <Loading what="upstream" />}
      </div>
    </Card>
  )
}
