import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { CaseDetail, OperationCapability, PlatformStatus } from '../lib/api'
import { useCase } from '../lib/caseContext'
import { statusWord } from '../lib/platform'
import { executiveSummary } from '../lib/summary'
import { Panel } from '../components/widgets'

/**
 * The landing screen: what Sanctum is, the four things it does, and what the
 * open case shows so far.
 *
 * Every status on this screen is read from the server. The workflow cards say
 * what each one guarantees and what it does not, in one line each, and the
 * Secure Erase card shows this host's whole-drive capability as the platform
 * probe computed it - "Unsupported" on a host where it is, never a hopeful
 * word. The summary counts only what the case record holds; a dry run is a
 * simulation and is never counted as an erasure.
 */

export type WorkflowTarget = 'recovery' | 'sanitize' | 'files' | 'audit'

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

function Column({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="col tight summary-column">
      <span className="summary-title">{title}</span>
      {lines.map((line) => (
        <span key={line} className="note">
          {line}
        </span>
      ))}
    </div>
  )
}

export default function Home({ onOpen }: { onOpen: (target: WorkflowTarget) => void }) {
  const { openCase } = useCase()
  const [platform, setPlatform] = useState<PlatformStatus | null>(null)
  const [chain, setChain] = useState<string>('UNREAD')
  // Keyed by case id, so a stale answer for a previous case is never shown and
  // no effect has to clear it synchronously.
  const [fetched, setFetched] = useState<{ id: string; body: CaseDetail } | null>(null)

  useEffect(() => {
    void api.platform().then(setPlatform).catch(() => setPlatform(null))
    void api
      .ledgerVerify()
      .then((answer) => setChain(answer.status))
      .catch(() => setChain('UNREAD'))
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
  const summary = executiveSummary(detail, platform)

  const cards: {
    target: WorkflowTarget
    title: string
    does: string
    doesNot: string
    status: React.ReactNode
  }[] = [
    {
      target: 'recovery',
      title: 'Recover Evidence',
      does: 'Carve and undelete from an image. Evidence is opened read-only.',
      doesNot: 'Scores are evidence scores, not probabilities of correctness.',
      status: <span className="state-mark is-success">read-only path</span>,
    },
    {
      target: 'sanitize',
      title: 'Secure Erase',
      does: 'Whole-drive Clear or Purge, chosen from what the device reports it can do.',
      doesNot: 'Overwrite cannot reach remapped or over-provisioned flash.',
      status: <CapabilityMark row={capability(platform, 'whole_drive_clear')} />,
    },
    {
      target: 'files',
      title: 'File / Folder Erase',
      does: 'Overwrite named files and folders, cleanse document metadata.',
      doesNot: 'Copy-on-write filesystems are reported NOT VERIFIABLE, never passed.',
      status: <CapabilityMark row={capability(platform, 'file_erase')} />,
    },
    {
      target: 'audit',
      title: 'Verify Report',
      does: 'Five independent checks and a graded verdict over a signed report.',
      doesNot: 'An embedded key proves consistency, not who signed.',
      status: (
        <span
          className={`state-mark is-${chain === 'VALID' ? 'success' : chain === 'UNREAD' ? 'unknown' : 'warning'}`}
        >
          chain {chain}
        </span>
      ),
    },
  ]

  return (
    <>
      <div className="screen-head">
        <h1>Sanctum</h1>
        <p>Forensic Recovery + Secure Sanitization</p>
      </div>
      <div className="screen-body">
        <div className="workflows">
          {cards.map((card) => (
            <button
              key={card.target}
              className="workflow-card"
              onClick={() => onOpen(card.target)}
            >
              <span className="workflow-title">{card.title}</span>
              {card.status}
              <span className="note">{card.does}</span>
              <span className="note-faint">{card.doesNot}</span>
            </button>
          ))}
        </div>

        <Panel
          title={openCase ? `Case ${openCase.case_id}` : 'No case open'}
          subtitle="Counted from the case record, the chain and the platform probe"
        >
          <div className="summary-grid">
            <Column title="WHAT WAS FOUND" lines={summary.found} />
            <Column title="WHAT WAS ERASED" lines={summary.erased} />
            <Column title="HOW IT WAS VERIFIED" lines={summary.verified} />
            <Column title="WHAT REMAINS UNVERIFIED" lines={summary.unverified} />
          </div>
        </Panel>
      </div>
    </>
  )
}
