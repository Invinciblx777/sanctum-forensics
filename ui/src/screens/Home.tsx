import { useEffect, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  Blocks,
  FileSearch,
  FileX2,
  FolderKanban,
  Link2,
  ScanSearch,
  ScrollText,
  ShieldX,
} from 'lucide-react'
import { api } from '../lib/api'
import type {
  CaseDetail,
  CaseSummary,
  LedgerEntry,
  OperationCapability,
  PlatformStatus,
} from '../lib/api'
import { useCase } from '../lib/caseContext'
import { statusWord } from '../lib/platform'
import { NOT_PHYSICALLY_VALIDATED, judgeSummary } from '../lib/summary'
import { operationKind, operationLabel, shortTime } from '../lib/ledger'
import type { OperationKind } from '../lib/ledger'
import { ChainStrip } from '../components/chain'
import { Panel, Verdict } from '../components/widgets'

/**
 * The overview: the chain of custody first, then the three modules, then
 * what the open case - or every case - holds so far.
 *
 * Every figure and status here is read from the server. A dry run is a
 * simulation and is never counted as an erasure; a capability is the platform
 * probe's word for this host, never a hopeful one. The chain block is the one
 * bold element on the screen because it is the claim everything else rests on:
 * each operation is sealed into an entry that carries the SHA-256 of the one
 * before it.
 */

export type WorkflowTarget = 'recovery' | 'sanitize' | 'files' | 'audit' | 'devices' | 'cases'

/** How many chain entries the overview draws. */
const CHAIN_SHOWN = 9

function capability(
  platform: PlatformStatus | null,
  operation: string,
): OperationCapability | undefined {
  return platform?.operations.find((row) => row.operation === operation)
}

function CapabilityMark({ row }: { row: OperationCapability | undefined }) {
  if (!row) return <span className="state-mark is-unknown">not probed</span>
  const { word, tone } = statusWord(row.status)
  return (
    <span className={`state-mark is-${tone}`} title={row.reason}>
      {word}
    </span>
  )
}

const KIND_ICON: Record<OperationKind, LucideIcon> = {
  erase: ShieldX,
  recover: ScanSearch,
  report: ScrollText,
  case: FolderKanban,
  chain: Link2,
  job: Blocks,
  other: Blocks,
}

function chainTone(status: string): 'seal' | 'destructive' | 'warning' | 'unknown' {
  if (status === 'VALID') return 'seal'
  if (status === 'INCONCLUSIVE_TAIL' || status === 'INCOMPLETE_TAIL') return 'warning'
  if (status === 'UNREAD' || status === 'EMPTY') return 'unknown'
  return 'destructive'
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

interface Figure {
  label: string
  value: string
  foot: string
}

function figuresFor(
  detail: CaseDetail | null,
  cases: CaseSummary[],
  entryCount: number | null,
  chainStatus: string,
): Figure[] {
  const sealed: Figure = {
    label: 'Operations sealed in the chain',
    value: entryCount === null ? '—' : String(entryCount),
    foot: `Chain ${chainStatus.toLowerCase()}`,
  }
  if (detail) {
    const carves = detail.operations.filter((op) => op.type === 'carve' && op.status === 'complete')
    const recovered = carves.reduce((sum, op) => sum + (op.recovered_artifacts || 0), 0)
    const signed = detail.reports.filter((report) => report.signed).length
    const erased = detail.operations.filter(
      (op) =>
        ['erase-drive', 'erase-files', 'wipe-free-space'].includes(op.type) &&
        op.params?.dry_run === false &&
        op.status === 'complete',
    ).length
    return [
      sealed,
      { label: 'Evidence items', value: String(detail.evidence.length), foot: `In ${detail.case.case_id}` },
      { label: 'Recovered artifacts', value: String(recovered), foot: `From ${plural(carves.length, 'completed recovery run')}` },
      { label: 'Signed reports', value: String(signed), foot: `${plural(erased, 'completed erasure')} in this case` },
    ]
  }
  const sum = (pick: (item: CaseSummary) => number) =>
    cases.reduce((total, item) => total + (pick(item) || 0), 0)
  return [
    sealed,
    { label: 'Cases', value: String(cases.length), foot: plural(sum((c) => c.evidence_count), 'evidence item') + ' registered' },
    {
      label: 'Recovered artifacts',
      value: String(sum((c) => c.recovered_artifact_count)),
      foot: 'Across every case',
    },
    { label: 'Reports', value: String(sum((c) => c.report_count)), foot: 'Across every case' },
  ]
}

export default function Home({ onOpen }: { onOpen: (target: WorkflowTarget) => void }) {
  const { openCase } = useCase()
  const [platform, setPlatform] = useState<PlatformStatus | null>(null)
  const [chain, setChain] = useState<{
    status: string
    entry_count: number
    first_broken_seq: number | null
  }>({ status: 'UNREAD', entry_count: 0, first_broken_seq: null })
  const [entries, setEntries] = useState<LedgerEntry[]>([])
  const [cases, setCases] = useState<CaseSummary[]>([])
  // Keyed by case id, so a stale answer for a previous case is never shown.
  const [fetched, setFetched] = useState<{ id: string; body: CaseDetail } | null>(null)

  useEffect(() => {
    void api.platform().then(setPlatform).catch(() => setPlatform(null))
    void api
      .ledgerVerify()
      .then((answer) =>
        setChain({
          status: answer.status,
          entry_count: answer.entry_count,
          first_broken_seq: answer.first_broken_seq ?? null,
        }),
      )
      .catch(() => setChain({ status: 'UNREAD', entry_count: 0, first_broken_seq: null }))
    void api
      .ledgerEntries(CHAIN_SHOWN)
      .then((answer) => setEntries(answer.entries))
      .catch(() => setEntries([]))
    void api
      .cases()
      .then((answer) => setCases(answer.cases))
      .catch(() => setCases([]))
  }, [])

  useEffect(() => {
    if (!openCase) return
    const id = openCase.case_id
    void api
      .case(id)
      .then((body) => setFetched({ id, body }))
      .catch(() => setFetched(null))
  }, [openCase])

  const detail = openCase && fetched?.id === openCase.case_id ? fetched.body : null
  const summary = judgeSummary(detail, platform, chain.status)
  const oldestFirst = [...entries].reverse()
  const head = entries[0]
  const figures = figuresFor(
    detail,
    cases,
    chain.status === 'UNREAD' ? null : chain.entry_count,
    chain.status,
  )

  const modules: {
    target: WorkflowTarget
    icon: LucideIcon
    title: string
    level: string
    does: string
    status: React.ReactNode
  }[] = [
    {
      target: 'devices',
      icon: ShieldX,
      title: 'Drive eraser',
      level: 'NIST SP 800-88 Clear or Purge; Destroy recorded',
      does:
        'Erases a whole HDD, SSD, USB drive or card with the strongest method the drive itself reports, then reads it back. A physical destruction is recorded as its witnesses attest it.',
      status: <CapabilityMark row={capability(platform, 'whole_drive_clear')} />,
    },
    {
      target: 'files',
      icon: FileX2,
      title: 'File & folder eraser',
      level: 'Files, folders, free space, metadata',
      does:
        'Overwrites the files you choose, strips document and photo metadata, removes the thumbnails, recent entries and Trash copies the desktop kept, and says what the filesystem may still hold.',
      status: <CapabilityMark row={capability(platform, 'file_erase')} />,
    },
    {
      target: 'recovery',
      icon: ScanSearch,
      title: 'Recovery',
      level: 'Media map, then signature, structure and fragment carving',
      does:
        'Maps where an image holds data, recovers files from formatted or damaged images without a filesystem, and scores each one with the evidence behind the score.',
      status: <span className="state-mark is-success">read-only</span>,
    },
  ]

  return (
    <>
      <div className="screen-head">
        <h1>Overview</h1>
        <p>Sanitize drives and files, recover evidence, and prove every step.</p>
      </div>
      <div className="screen-body">
        <section className="custody" data-testid="custody" aria-labelledby="custody-title">
          <div className="custody-head">
            <div>
              <h2 className="custody-title" id="custody-title">
                Chain of custody
              </h2>
              <p className="custody-sub">
                Every operation is sealed into an entry that carries the SHA-256 of
                the entry before it. Change one byte of any entry and every later
                link breaks.
              </p>
            </div>
            <div className="custody-verdict">
              <Verdict
                level={chain.status === 'VALID' ? 'Valid' : chain.status.toLowerCase()}
                basis={
                  head
                    ? `${chain.entry_count} entries, head ${head.entry_hash.slice(0, 16)}`
                    : `${chain.entry_count} entries`
                }
                tone={chainTone(chain.status)}
              />
            </div>
          </div>
          {oldestFirst.length ? (
            <ChainStrip
              entries={oldestFirst}
              firstBrokenSeq={chain.first_broken_seq}
              label="The most recent entries in the chain, oldest first"
            />
          ) : (
            <p className="note">
              Nothing has been sealed yet. The first operation starts the chain.
            </p>
          )}
          <div className="row wrap">
            <button className="btn primary" onClick={() => onOpen('audit')}>
              <Blocks className="icon" size={16} aria-hidden />
              Open the audit trail
            </button>
            <button className="btn" onClick={() => onOpen('audit')}>
              <FileSearch className="icon" size={16} aria-hidden />
              Verify a signed report
            </button>
            <span className="note-faint">
              The tamper test on the Audit screen breaks a copy of this chain and
              shows the verifier catching it.
            </span>
          </div>
        </section>

        <div className="modules" data-testid="modules">
          {modules.map((module) => {
            const Icon = module.icon
            return (
              <button
                key={module.target}
                className="module"
                onClick={() => onOpen(module.target)}
              >
                <span className="module-top">
                  <span className="module-icon">
                    <Icon className="icon" size={20} aria-hidden />
                  </span>
                  <span className="col" style={{ gap: 0 }}>
                    <span className="module-title">{module.title}</span>
                    <span className="module-level">{module.level}</span>
                  </span>
                </span>
                <p className="note">{module.does}</p>
                <span className="module-foot">
                  {module.status}
                  <span className="note-faint">Open</span>
                </span>
              </button>
            )
          })}
        </div>

        <div className="figures" data-testid="figures">
          {figures.map((figure) => (
            <div key={figure.label} className="figure">
              <span className="figure-label">{figure.label}</span>
              <span className="figure-value">{figure.value}</span>
              <span className="figure-foot">{figure.foot}</span>
            </div>
          ))}
        </div>

        <div className="overview-grid">
          <Panel
            title={openCase ? `What case ${openCase.case_id} shows` : 'What this host shows'}
            subtitle={
              openCase
                ? 'Counted from the case record, the chain and the platform probe'
                : 'No case open: host capability, chain and design facts only'
            }
          >
            <div className="summary-grid" data-testid="executive-summary">
              {(
                [
                  ['Secure erasure', summary.erasure],
                  ['Evidence recovery', summary.recovery],
                  ['Verification', summary.verification],
                  ['Safety', summary.safety],
                ] as const
              ).map(([title, lines]) => (
                <div key={title} className="col tight">
                  <span className="summary-title">{title}</span>
                  {lines.map((line) => (
                    <span key={line} className="note">
                      {line}
                    </span>
                  ))}
                </div>
              ))}
            </div>
          </Panel>

          <div className="col">
            <Panel title="Recent activity" subtitle="Newest first, from the chain">
              {entries.length ? (
                <ul className="activity">
                  {entries.slice(0, 6).map((entry) => {
                    const Icon = KIND_ICON[operationKind(entry.operation)]
                    return (
                      <li key={entry.seq}>
                        <span className="activity-icon">
                          <Icon className="icon" size={15} aria-hidden />
                        </span>
                        <span className="activity-what" title={entry.operation}>
                          {operationLabel(entry.operation)}
                        </span>
                        <span className="activity-when">
                          #{entry.seq} {shortTime(entry.ts_utc)}
                        </span>
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <p className="note">No operations recorded yet.</p>
              )}
            </Panel>

            <Panel title="Not yet proven on hardware">
              <ul className="limitations">
                {NOT_PHYSICALLY_VALIDATED.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </Panel>
          </div>
        </div>
      </div>
    </>
  )
}
