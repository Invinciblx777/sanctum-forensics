import { useEffect, useState } from 'react'
import { api, RequestFailed } from '../lib/api'
import type { LedgerVerification, ReportResult, ReportVerification } from '../lib/api'
import { timestamp } from '../lib/format'
import { Chip, Empty, ErrorNotice, Hash, Notice, Panel, Stat } from '../components/widgets'

/** What each report check actually proves, and what it does not. */
const CHECK_MEANING: Record<string, string> = {
  signature:
    'The signature verifies against the public key embedded in the report, so the canonical JSON bytes have not changed since signing.',
  fingerprint:
    'The embedded key matches the one this host holds. A mismatch is not proof of tampering — it means the report was signed elsewhere, which is normal for a report you received.',
  chain:
    'The ledger excerpt inside the report is internally consistent: each entry carries the SHA-256 of the one before it.',
  blobs:
    'Every parameter and result blob referenced by the excerpt is present and hashes to the value the entry records.',
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

  const valid = chain?.status === 'VALID'

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

        {chain && (
          <Notice tone={valid ? 'ok' : chain.status === 'EMPTY' ? 'info' : 'danger'}>
            <div className="row spread">
              <strong>
                Chain {chain.status} — {chain.entry_count} entries
              </strong>
              <span className="mono" style={{ fontSize: 11 }}>
                {chain.root}
              </span>
            </div>
            <p style={{ margin: '6px 0 0', fontSize: 12 }}>{chain.explanation}</p>
            {chain.first_broken_seq !== null &&
              chain.first_broken_seq !== undefined && (
                <p style={{ margin: '6px 0 0', fontSize: 12 }}>
                  The first break is at entry {chain.first_broken_seq}. Everything
                  before it is still internally consistent; everything after is
                  not.
                </p>
              )}
          </Notice>
        )}

        <div className="split">
          <Panel title={`Ledger (${chain?.entries.length ?? 0} shown, newest first)`} tight>
            {!chain || chain.entries.length === 0 ? (
              <Empty>No ledger entries yet.</Empty>
            ) : (
              <div className="scroll-y" style={{ maxHeight: '52vh' }}>
                <table>
                  <thead>
                    <tr>
                      <th style={{ width: 54 }}>Seq</th>
                      <th>Timestamp</th>
                      <th>Actor</th>
                      <th>Operation</th>
                      <th>Entry hash</th>
                      <th>Prev hash</th>
                    </tr>
                  </thead>
                  <tbody>
                    {chain.entries.map((entry) => (
                      <tr key={entry.entry_hash}>
                        <td className="mono">{entry.seq}</td>
                        <td className="mono" style={{ fontSize: 11 }}>
                          {timestamp(entry.ts_utc)}
                        </td>
                        <td className="mono" style={{ fontSize: 11 }}>{entry.actor}</td>
                        <td className="mono" style={{ fontSize: 11 }}>{entry.operation}</td>
                        <td><Hash value={entry.entry_hash} /></td>
                        <td><Hash value={entry.prev_entry_hash} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <div className="col">
            <Panel title="Report generator">
              <div className="col" style={{ gap: 9 }}>
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
                  <button className="btn primary" disabled={!jobId} onClick={() => void generate()}>
                    Generate signed report
                  </button>
                  <button className="btn" disabled={!jobId} onClick={() => void verify()}>
                    Verify
                  </button>
                </div>

                {report && (
                  <div className="col" style={{ gap: 5 }}>
                    <Stat label="json (authoritative)" value={<span className="path">{report.json_path}</span>} />
                    <Stat label="pdf (rendering)" value={<span className="path">{report.pdf_path}</span>} />
                    <Stat label="fingerprint" value={<span className="hash">{report.pubkey_fingerprint}</span>} />
                    <Notice tone="info">
                      The JSON is authoritative and the PDF is not: the signature
                      covers the canonical JSON bytes, and the PDF is a rendering
                      for a human.
                    </Notice>
                  </div>
                )}
              </div>
            </Panel>

            {verification && (
              <Panel title="Report verification">
                <div className="col" style={{ gap: 10 }}>
                  <Notice tone={verification.passed ? 'ok' : 'warn'}>
                    <strong>
                      {verification.passed
                        ? 'All four checks passed'
                        : 'At least one check did not pass'}
                    </strong>
                    <div className="hash" style={{ marginTop: 5 }}>
                      {verification.fingerprint}
                    </div>
                  </Notice>

                  {/* Each check reported on its own. Reducing them to one
                      boolean would hide the difference between "the bytes
                      changed" and "the key was never published anywhere I can
                      reach", and only the first is a reason to distrust the
                      report. */}
                  {verification.checks.map((check) => (
                    <div key={check.name} className="col" style={{ gap: 3 }}>
                      <span className="row" style={{ gap: 7 }}>
                        <Chip tone={check.passed ? 'low' : 'high'}>
                          {check.passed ? 'PASS' : 'FAIL'}
                        </Chip>
                        <strong style={{ fontSize: 12 }}>{check.name}</strong>
                      </span>
                      <span style={{ color: 'var(--fg-dim)', fontSize: 11 }}>
                        {check.detail}
                      </span>
                      <span style={{ color: 'var(--fg-faint)', fontSize: 11 }}>
                        {CHECK_MEANING[check.name] ?? ''}
                      </span>
                    </div>
                  ))}

                  <Notice tone="info">
                    An embedded public key proves internal consistency only. It
                    does not prove identity: compare the fingerprint above
                    against a value published out-of-band before treating this
                    signature as evidence of who produced the report.
                  </Notice>
                </div>
              </Panel>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
