import type { ReportFile } from '../types/api'

// A small header-level action, not a workflow step - the dashboard's own
// sequence is selection -> resolution -> comparison -> preview -> PR, and this
// sits outside it. Only the two files someone reviewing the mapping would
// want are offered; the internal plan JSON/MD stay backend-only and are never
// listed here, even though /api/reports itself still serves them.
const WANTED: { name: string; label: string }[] = [
  { name: 'upstream-mapping-bookworm.xlsx', label: 'XLSX' },
  { name: 'upstream-mapping-bookworm.csv', label: 'CSV' },
]

export function DownloadMappingReport({ reports }: { reports: ReportFile[] }) {
  const byName = new Map(reports.map((r) => [r.name, r]))
  const links = WANTED
    .map((w) => byName.get(w.name))
    .filter((r): r is ReportFile => !!r)

  // Nothing asserted when the reports aren't there yet - not "run this
  // command" (that read as a false claim when reports existed but the check
  // itself had failed), just no action offered.
  if (links.length === 0) return null

  return (
    <span className="small muted">
      Mapping report:{' '}
      {links.map((report, i) => (
        <span key={report.name}>
          {i > 0 && ' · '}
          <a href={report.download_url} download>
            {WANTED.find((w) => w.name === report.name)?.label ?? report.name}
          </a>
        </span>
      ))}
    </span>
  )
}
