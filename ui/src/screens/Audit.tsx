import { useEffect, useState } from 'react'
import { api, RequestFailed } from '../lib/api'
import type {
  LedgerVerification,
  ReportCheck,
  ReportResult,
  ReportVerification,
} from '../lib/api'
import { timestamp } from '../lib/format'
import {
  Empty,
  ErrorNotice,
  Evidence,
  Hash,
  Notice,
  Panel,
  Railed,
  Verdict,
} from '../components/widgets'
import type { Tone } from '../components/widgets'

/**
 * What each report check actually proves, and what it does not.
 *
 * Keyed by the value core/report/verify_report.py's `CheckName` emits. Getting
 * a key wrong here is silent - the lookup misses and the line renders empty -
 * so the five names below are the five members of that enum and nothing else.
 */
const CHECK_MEANING: Record<string, string> = {
  signature:
    'The signature verifies against the public key embedded in the report, so the canonical JSON bytes have not changed since signing.',
  fingerprint_matches_genesis:
    'The signing key is the key recorded in the ledger genesis entry. A mismatch is not proof of tampering — it means the report was signed by a different key than the one this chain was opened with.',
  chain_integrity:
    'The ledger excerpt carried inside the report is internally consistent: each entry hashes to what it records, and each links to the one before it.',
  chain_store:
    'The whole chain was re-verified from this host’s ledger store, independently of the excerpt the report carries. Without it, a reader is taking the report’s own word for the property the report exists to evidence.',
  blobs_available:
    'Every parameter and result blob referenced by the excerpt is present in the store and hashes to the value the entry records.',
}

/** The three outcomes a check can have. Two of them are not failures. */
function checkVerdict(check: ReportCheck): { word: string; tone: Tone } {
  if (!check.applicable) return { word: 'NOT CHECKED', tone: 'unknown' }
  return check.passed
    ? { word: 'PASS', tone: 'success' }
    : { word: 'FAIL', tone: 'destructive' }
}

/**
 * The chain's own status.
 *
 * INCOMPLETE_TAIL is not BROKEN: it means the last append did not finish, so
 * everything before it still verifies. Colouring it like a break would tell an
 * examiner to distrust a chain that is intact up to its final entry.
 */
function chainTone(status: string): Tone {
  if (status === 'VALID') return 'success'
  if (status === 'EMPTY') return 'unknown'
  if (status === 'INCOMPLETE_TAIL') return 'warning'
  return 'destructive'
}

/** The headline over the five checks: a count, not a boolean. */
function summarise(verification: ReportVerification): {
  word: string
  tone: Tone
  note: string
} {
  const total = verification.checks.length
  const applicable = verification.checks.filter((item) => item.applicable)
  const failed = applicable.filter((item) => !item.passed).length
  const skipped = total - applicable.length
  const passed = applicable.length - failed
  const word = `${passed} OF ${total} PASSED`
  if (failed > 0) {
    // Both counts, when there are both. A reader who is told only about the
    // failure is left to work out for themselves where the fifth check went.
    const skippedClause = skipped
      ? `, and ${skipped} could not run on this host`
      : ''
    return {
      word,
      tone: 'destructive',
      note: `${failed} check${failed === 1 ? '' : 's'} did not pass${skippedClause}. Read the failing one before treating this report as evidence.`,
    }
  }
  if (skipped > 0) {
    return {
      word,
      tone: 'warning',
      note: `${skipped} check${skipped === 1 ? ' was' : 's were'} not applicable on this host. Nothing failed, and nothing was established about the part that could not run.`,
    }
  }
  return { word, tone: 'success', note: 'Every check ran and every check passed.' }
}

export default function Audit() {
  const [chain, setChain] = useState<LedgerVerification | null>(null)
  const [jobId, setJobId] = useState('')
  const [caseId, setCaseId] = useState('')
  const [operator, setOperator] = useState('sanctum')
  const [report, setReport] = useState<ReportResult | null>(null)
  const [verification, setVerification] = useState<ReportVerification | null>(null)
  const [error, setError] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)

  async function refresh() {
    try {
      setChain(await api.ledgerVerify())
      setError(null)
    } catch (exc) {
      const failure = exc as RequestFailed
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  async function generate() {
    setError(null)
    try {
      setReport(await api.generateReport(jobId, { case_id: caseId, operator }))
      setVerification(null)
    } catch (exc) {
      const failure = exc as RequestFailed
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    }
  }

  async function verify() {
    setError(null)
    try {
      setVerification(await api.verifyReport(jobId))
    } catch (exc) {
      const failure = exc as RequestFailed
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    }
  }

  const broken = chain?.first_broken_seq ?? null
  const summary = verification ? summarise(verification) : null

  return (
    <>
      <div className="screen-head">
        <h1>Audit</h1>
        <p>Hash-chained ledger, signed reports, and independent verification.</p>
        <div className="grow" />
        <button className="btn" onClick={() => void refresh()}>
          Re-verify chain
        </button>
      </div>

      <div className="screen-body">
        <ErrorNotice error={error} />

        {/* The chain's status is the one claim on this screen that has to
            carry to the back of the room, so it is a verdict and not a line
            inside a notice. */}
        {chain && (
          <Panel title="Chain">
            <div className="col">
              <Railed tone={chainTone(chain.status)}>
                <Verdict
                  level={chain.status}
                  basis={`${chain.entry_count} ${chain.entry_count === 1 ? 'entry' : 'entries'}`}
                  tone={chainTone(chain.status)}
                />
              </Railed>
              <p className="note">{chain.explanation}</p>
              {broken !== null && (
                <p className="note">
                  The first break is at entry <strong>{broken}</strong>.
                  Everything before it is still internally consistent;
                  everything after it is not.
                </p>
              )}
              {chain.root && (
                <Evidence rows={[{ label: 'Store', value: chain.root, kind: 'path' }]} />
              )}
            </div>
          </Panel>
        )}

        {/* The ledger is six columns of hashes and timestamps and it does not
            fit beside anything. Squeezed into half the width it truncated the
            operation name and the timestamp - the two columns an auditor reads
            first - so it takes the whole width and the report controls sit
            under it. */}
        <Panel
          title={`Ledger (${chain?.entries.length ?? 0} shown, newest first)`}
          subtitle="Entry N carries the SHA-256 of entry N-1."
          tight
        >
          {!chain || chain.entries.length === 0 ? (
            <Empty>No ledger entries yet.</Empty>
          ) : (
            <div className="scroll-y" style={{ maxHeight: '38vh' }}>
              <table className="itable">
                <colgroup>
                  <col style={{ width: 'var(--gutter)' }} />
                  <col style={{ width: 56 }} />
                  <col style={{ width: 176 }} />
                  <col style={{ width: 122 }} />
                  <col />
                  <col style={{ width: 152 }} />
                  <col style={{ width: 152 }} />
                </colgroup>
                <thead>
                  <tr>
                    <th className="rail" />
                    <th>Seq</th>
                    <th>Timestamp</th>
                    <th>Actor</th>
                    <th>Operation</th>
                    <th>Entry hash</th>
                    <th>Prev hash</th>
                  </tr>
                </thead>
                <tbody>
                  {chain.entries.map((entry) => {
                    // The rail marks the span the break invalidated and nothing
                    // else. A rail on every row is decoration, and it stops
                    // meaning anything on the row that counts.
                    const after = broken !== null && entry.seq >= broken
                    return (
                      <tr
                        key={entry.entry_hash}
                        className={
                          after ? 'irow is-compact is-bad' : 'irow is-compact'
                        }
                      >
                        <td
                          className={after ? 'rail is-destructive' : 'rail'}
                          aria-hidden
                        >
                          <i />
                        </td>
                        <td className="mono">{entry.seq}</td>
                        <td className="mono">{timestamp(entry.ts_utc)}</td>
                        <td className="mono">{entry.actor}</td>
                        <td className="mono">{entry.operation}</td>
                        <td>
                          <Hash value={entry.entry_hash} />
                        </td>
                        <td>
                          <Hash value={entry.prev_entry_hash} />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <div className="split">
          {verification && summary ? (
            <Panel
              title="Report verification"
              subtitle="Five checks, each reported on its own."
            >
              <div className="col">
                <Railed tone={summary.tone}>
                  <Verdict
                    level={summary.word}
                    basis={verification.fingerprint}
                    tone={summary.tone}
                  />
                </Railed>
                <p className="note">{summary.note}</p>

                {/* Each check reported on its own. Reducing them to one boolean
                    would hide the difference between "the bytes changed" and
                    "the key was never published anywhere I can reach", and only
                    the first is a reason to distrust the report. NOT CHECKED is
                    the third outcome and is neither of the other two: a check
                    that could not run is not a check that ran and held. */}
                {verification.checks.map((check) => {
                  const outcome = checkVerdict(check)
                  return (
                    <Railed key={check.name} tone={outcome.tone}>
                      <Verdict
                        level={outcome.word}
                        basis={check.name}
                        tone={outcome.tone}
                      />
                      <span className="note">{check.detail}</span>
                      <span className="note-faint">
                        {CHECK_MEANING[check.name] ?? ''}
                      </span>
                    </Railed>
                  )
                })}

                <Notice tone="info">{verification.caveat}</Notice>
              </div>
            </Panel>
          ) : (
            <Panel title="Report verification">
              <Empty>
                Verify a report to see the five checks, each reported on its own.
              </Empty>
            </Panel>
          )}

          <Panel title="Report generator">
            <div className="col">
              <label>
                Job id
                <input
                  type="text"
                  value={jobId}
                  spellCheck={false}
                  placeholder="erase-drive-…"
                  onChange={(event) => setJobId(event.target.value)}
                />
              </label>
              <label>
                Case id
                <input
                  type="text"
                  value={caseId}
                  spellCheck={false}
                  onChange={(event) => setCaseId(event.target.value)}
                />
              </label>
              <label>
                Operator
                <input
                  type="text"
                  value={operator}
                  spellCheck={false}
                  onChange={(event) => setOperator(event.target.value)}
                />
              </label>
              <div className="row">
                <button
                  className="btn primary"
                  disabled={!jobId}
                  onClick={() => void generate()}
                >
                  Generate signed report
                </button>
                <button className="btn" disabled={!jobId} onClick={() => void verify()}>
                  Verify
                </button>
              </div>

              {report && (
                <div className="col tight">
                  <Evidence
                    stacked
                    rows={[
                      {
                        label: 'JSON (authoritative)',
                        value: report.json_path,
                        kind: 'path',
                      },
                      {
                        label: 'PDF (rendering)',
                        value: report.pdf_path,
                        kind: 'path',
                      },
                      {
                        label: 'Fingerprint',
                        value: report.pubkey_fingerprint,
                        kind: 'hash',
                      },
                    ]}
                  />
                  <Notice tone="info">
                    The JSON is authoritative and the PDF is not: the signature
                    covers the canonical JSON bytes, and the PDF is a rendering
                    for a human.
                  </Notice>
                </div>
              )}
            </div>
          </Panel>
        </div>
      </div>
    </>
  )
}
