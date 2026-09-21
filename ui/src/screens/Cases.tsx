import { useEffect, useState } from 'react'
import { api, artifactUrl, RequestFailed } from '../lib/api'
import type { CaseDetail, CaseSummary } from '../lib/api'
import { useCase } from '../lib/caseContext'
import { timestamp } from '../lib/format'
import {
  Empty,
  ErrorNotice,
  Evidence,
  Hash,
  Notice,
  Panel,
  Railed,
  Stat,
  Verdict,
} from '../components/widgets'
import type { Tone } from '../components/widgets'

/**
 * The case screen: one page that accounts for an investigation.
 *
 * What was seized, what was done to it, what came out, and what the chain says
 * about all of it. Before this, those four answers lived on four screens keyed
 * by job ids the operator had to copy between them.
 *
 * One rule runs through every number here: **the counts come from the case
 * document and the integrity verdict comes from the chain**, and the screen
 * says which is which. The case document is an ordinary mutable JSON index and
 * proves nothing; the hash chain is the record. Rendering the document's word
 * for its own integrity would be the tool vouching for itself.
 */

function chainTone(status: string): Tone {
  if (status === 'VALID') return 'success'
  if (status === 'EMPTY' || status === 'UNREADABLE') return 'unknown'
  if (status === 'INCOMPLETE_TAIL') return 'warning'
  return 'destructive'
}

function statusTone(status: string): Tone {
  if (status === 'complete') return 'success'
  if (status === 'running' || status === 'pending') return 'warning'
  if (status === 'cancelled') return 'warning'
  return 'destructive'
}

type TabId =
  | 'overview'
  | 'evidence'
  | 'operations'
  | 'reports'
  | 'audit'

const TABS: { id: TabId; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'operations', label: 'Operations' },
  { id: 'reports', label: 'Reports' },
  { id: 'audit', label: 'Audit' },
]

function NewCase({ onCreated }: { onCreated: (id: string) => void }) {
  const [caseId, setCaseId] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)

  async function create() {
    setError(null)
    try {
      const answer = await api.createCase({
        case_id: caseId,
        title,
        description,
      })
      onCreated(answer.case.case_id)
      setCaseId('')
      setTitle('')
      setDescription('')
    } catch (exc) {
      const failure = exc as RequestFailed
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    }
  }

  return (
    <Panel
      title="Open a case"
      subtitle="The author is the local account that opened it, resolved server-side."
    >
      <div className="col">
        <ErrorNotice error={error} />
        <div className="row wrap" style={{ alignItems: 'flex-end' }}>
          <label>
            Case id
            <input
              type="text"
              value={caseId}
              spellCheck={false}
              placeholder="CASE-2026-001"
              onChange={(event) => setCaseId(event.target.value)}
            />
          </label>
          <label className="grow">
            Title
            <input
              type="text"
              value={title}
              placeholder="Seized laptop, exhibit 4"
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <button
            className="btn primary"
            disabled={!caseId}
            onClick={() => void create()}
          >
            Open case
          </button>
        </div>
        <label>
          Description
          <input
            type="text"
            value={description}
            placeholder="What this case covers"
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <p className="note">
          A case id becomes a filename, so it is checked against a whitelist
          rather than escaped: 1&ndash;64 characters from A&ndash;Z, a&ndash;z,
          0&ndash;9, dot, dash and underscore.
        </p>
      </div>
    </Panel>
  )
}

function RegisterEvidence({
  caseId,
  onRegistered,
}: {
  caseId: string
  onRegistered: () => void
}) {
  const [evidenceId, setEvidenceId] = useState('')
  const [source, setSource] = useState('')
  const [mediaType, setMediaType] = useState('raw image')
  const [sourceHash, setSourceHash] = useState('')
  const [error, setError] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)

  async function register() {
    setError(null)
    try {
      await api.registerEvidence(caseId, {
        evidence_id: evidenceId,
        source,
        media_type: mediaType,
        source_hash: sourceHash,
        state: 'registered',
      })
      setEvidenceId('')
      setSource('')
      setSourceHash('')
      onRegistered()
    } catch (exc) {
      const failure = exc as RequestFailed
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    }
  }

  return (
    <Panel
      title="Register an exhibit"
      subtitle="Records that an exhibit exists. It does not open, read or hash it."
    >
      <div className="col">
        <ErrorNotice error={error} />
        <div className="row wrap" style={{ alignItems: 'flex-end' }}>
          <label>
            Exhibit id
            <input
              type="text"
              value={evidenceId}
              spellCheck={false}
              placeholder="EX-1"
              onChange={(event) => setEvidenceId(event.target.value)}
            />
          </label>
          <label className="grow">
            Source
            <input
              type="text"
              value={source}
              spellCheck={false}
              placeholder="/dev/sdb, or the acquired image path"
              onChange={(event) => setSource(event.target.value)}
            />
          </label>
          <label>
            Media type
            <input
              type="text"
              value={mediaType}
              onChange={(event) => setMediaType(event.target.value)}
            />
          </label>
          <button
            className="btn"
            disabled={!evidenceId}
            onClick={() => void register()}
          >
            Register
          </button>
        </div>
        <label>
          Source hash (optional)
          <input
            type="text"
            value={sourceHash}
            spellCheck={false}
            placeholder="the digest acquisition recorded, if there is one"
            onChange={(event) => setSourceHash(event.target.value)}
          />
        </label>
        <p className="note">
          Acquisition is a job with its own read-only path and its own chain
          entries. A registration endpoint that quietly read a device would be a
          privileged operation wearing a bookkeeping name.
        </p>
      </div>
    </Panel>
  )
}

export default function Cases() {
  const { cases, openCase, select, refresh, loading } = useCase()
  const [detail, setDetail] = useState<CaseDetail | null>(null)
  const [tab, setTab] = useState<TabId>('overview')
  const [error, setError] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)

  async function load(caseId: string) {
    setError(null)
    try {
      setDetail(await api.case(caseId))
    } catch (exc) {
      const failure = exc as RequestFailed
      setDetail(null)
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    }
  }

  useEffect(() => {
    if (openCase) void load(openCase.case_id)
    else setDetail(null)
  }, [openCase?.case_id])

  return (
    <>
      <div className="screen-head">
        <h1>Cases</h1>
        <p>
          Evidence, operations, reports and the audit trail, grouped by
          investigation.
        </p>
        <div className="grow" />
        <button
          className="btn"
          onClick={() => {
            void refresh()
            if (openCase) void load(openCase.case_id)
          }}
        >
          Refresh
        </button>
      </div>

      <div className="screen-body">
        <ErrorNotice error={error} />

        <div className="split">
          <Panel title={`Cases (${cases.length})`} tight>
            {loading ? (
              <Empty>Reading the case list&hellip;</Empty>
            ) : cases.length === 0 ? (
              <Empty>
                No cases yet. Open one below; every operation this tool performs
                can then be filed against it, and the report inherits the case
                id without anyone retyping it.
              </Empty>
            ) : (
              <div className="scroll-y" style={{ maxHeight: '46vh' }}>
                <table className="itable">
                  <colgroup>
                    <col style={{ width: 'var(--gutter)' }} />
                    <col style={{ width: 160 }} />
                    <col />
                    <col style={{ width: 70 }} />
                    <col style={{ width: 70 }} />
                    <col style={{ width: 70 }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th className="rail" />
                      <th>Case</th>
                      <th>Title</th>
                      <th>Exh.</th>
                      <th>Ops</th>
                      <th>Rpts</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cases.map((item: CaseSummary) => {
                      const active = openCase?.case_id === item.case_id
                      return (
                        <tr
                          key={item.case_id}
                          className={
                            active
                              ? 'irow is-compact is-openable is-selected'
                              : 'irow is-compact is-openable'
                          }
                          onClick={() => select(item.case_id)}
                        >
                          <td className="rail" aria-hidden>
                            <i />
                          </td>
                          <td className="mono">{item.case_id}</td>
                          <td title={item.description}>{item.title || '—'}</td>
                          <td className="mono">{item.evidence_count}</td>
                          <td className="mono">{item.operation_count}</td>
                          <td className="mono">{item.report_count}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <NewCase
            onCreated={(id) => {
              void refresh().then(() => select(id))
            }}
          />
        </div>

        {openCase && detail && (
          <>
            <Panel
              title={detail.case.case_id}
              subtitle={detail.case.title || undefined}
              actions={
                <div className="row" style={{ gap: 'var(--space-1)' }}>
                  {TABS.map((item) => (
                    <button
                      key={item.id}
                      className={tab === item.id ? 'btn primary' : 'btn'}
                      onClick={() => setTab(item.id)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              }
            >
              {tab === 'overview' && (
                <div className="col loose">
                  {/* The integrity verdict is the chain's, always. The counts
                      below come from the case document, which is an index. */}
                  <Railed tone={chainTone(detail.audit.chain_status)}>
                    <Verdict
                      level={`INTEGRITY: ${detail.audit.chain_status}`}
                      basis={`${detail.audit.entry_count} chain entries`}
                      tone={chainTone(detail.audit.chain_status)}
                    />
                    <span className="note">
                      {detail.audit.chain_explanation}
                    </span>
                  </Railed>

                  <div className="row wrap" style={{ gap: 'var(--space-6)' }}>
                    <Stat label="Evidence" value={detail.evidence.length} />
                    <Stat label="Operations" value={detail.operations.length} />
                    <Stat
                      label="Recovered artifacts"
                      value={detail.case.recovered_artifact_count.toLocaleString(
                        'en-US',
                      )}
                    />
                    <Stat label="Reports" value={detail.reports.length} />
                    <Stat
                      label="Audit events"
                      value={detail.audit.events.length}
                    />
                  </div>

                  <Evidence
                    stacked
                    rows={[
                      { label: 'Opened by', value: detail.case.created_by },
                      {
                        label: 'Opened at',
                        value: timestamp(detail.case.created_at),
                      },
                      { label: 'Status', value: detail.case.status },
                      {
                        label: 'Description',
                        value: detail.case.description || '—',
                      },
                    ]}
                  />

                  <Notice tone="info">
                    The counts above are read from this case&apos;s index
                    document. The integrity verdict is read from the
                    hash-chained ledger. Deleting the index loses the grouping
                    and loses no evidence; if the two ever disagree, the ledger
                    is right.
                  </Notice>
                </div>
              )}

              {tab === 'evidence' && (
                <div className="col">
                  {detail.evidence.length === 0 ? (
                    <Empty>
                      No exhibit is registered against this case yet. Register
                      one below, or run an acquisition with this case selected.
                    </Empty>
                  ) : (
                    <table className="itable">
                      <thead>
                        <tr>
                          <th>Exhibit</th>
                          <th>Source</th>
                          <th>Type</th>
                          <th>Source hash</th>
                          <th>State</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.evidence.map((item) => (
                          <tr key={item.evidence_id} className="irow is-compact">
                            <td className="mono">{item.evidence_id}</td>
                            <td className="path">{item.source || '—'}</td>
                            <td className="mono">{item.media_type}</td>
                            <td>
                              {item.source_hash ? (
                                <Hash value={item.source_hash} />
                              ) : (
                                <span style={{ color: 'var(--text-muted)' }}>
                                  not recorded
                                </span>
                              )}
                            </td>
                            <td className="mono">{item.state}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}

              {tab === 'operations' && (
                <div className="col">
                  {detail.operations.length === 0 ? (
                    <Empty>
                      No operation has been run under this case. Start a
                      recovery or a sanitization with this case open and it
                      appears here.
                    </Empty>
                  ) : (
                    <table className="itable">
                      <colgroup>
                        <col style={{ width: 'var(--gutter)' }} />
                        <col style={{ width: 220 }} />
                        <col style={{ width: 120 }} />
                        <col style={{ width: 110 }} />
                        <col />
                        <col style={{ width: 90 }} />
                      </colgroup>
                      <thead>
                        <tr>
                          <th className="rail" />
                          <th>Operation</th>
                          <th>Type</th>
                          <th>Status</th>
                          <th>Operator</th>
                          <th>Artifacts</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.operations.map((item) => (
                          <tr
                            key={item.operation_id}
                            className="irow is-compact"
                          >
                            <td
                              className={`rail is-${statusTone(item.status)}`}
                              aria-hidden
                            >
                              <i />
                            </td>
                            <td className="mono">{item.operation_id}</td>
                            <td className="mono">{item.type}</td>
                            <td className="mono">{item.status}</td>
                            <td className="mono">{item.operator}</td>
                            <td className="mono">
                              {item.recovered_artifacts.toLocaleString('en-US')}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}

              {tab === 'reports' && (
                <div className="col">
                  {detail.reports.length === 0 ? (
                    <Empty>
                      No report has been generated for this case. Generate one
                      from the Audit screen using an operation id above.
                    </Empty>
                  ) : (
                    detail.reports.map((item) => (
                      <Railed key={item.report_id} tone="success">
                        <Evidence
                          stacked
                          rows={[
                            { label: 'Operation', value: item.operation_id },
                            {
                              label: 'Generated',
                              value: timestamp(item.generated_at),
                            },
                            {
                              label: 'SHA-256 (JSON)',
                              value: item.report_hash,
                              kind: 'hash',
                            },
                            {
                              label: 'Signed',
                              value: item.signed ? 'yes' : 'no',
                            },
                          ]}
                        />
                        <div className="row" style={{ gap: 'var(--space-2)' }}>
                          <a
                            className="btn"
                            href={artifactUrl('reports', item.pdf_name)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Open PDF
                          </a>
                          <a
                            className="btn"
                            href={artifactUrl('reports', item.pdf_name, {
                              download: true,
                            })}
                          >
                            Download PDF
                          </a>
                          <a
                            className="btn"
                            href={artifactUrl('reports', item.json_name)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            View JSON
                          </a>
                        </div>
                        <span className="note-faint">
                          The JSON is authoritative and the PDF is not: the
                          signature covers the canonical JSON bytes.
                        </span>
                      </Railed>
                    ))
                  )}
                </div>
              )}

              {tab === 'audit' && (
                <div className="col">
                  {detail.audit.events.length === 0 ? (
                    <Empty>
                      The chain carries no entries for this case yet.
                    </Empty>
                  ) : (
                    <div className="scroll-y" style={{ maxHeight: '52vh' }}>
                      <table className="itable">
                        <colgroup>
                          <col style={{ width: 'var(--gutter)' }} />
                          <col style={{ width: 56 }} />
                          <col style={{ width: 176 }} />
                          <col style={{ width: 190 }} />
                          <col />
                          <col style={{ width: 152 }} />
                        </colgroup>
                        <thead>
                          <tr>
                            <th className="rail" />
                            <th>Seq</th>
                            <th>Timestamp</th>
                            <th>Actor</th>
                            <th>Event</th>
                            <th>Entry hash</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.audit.events.map((item) => {
                            const broken =
                              detail.audit.first_broken_seq !== null &&
                              item.seq >= detail.audit.first_broken_seq
                            return (
                              <tr
                                key={item.current_hash}
                                className={
                                  broken
                                    ? 'irow is-compact is-bad'
                                    : 'irow is-compact'
                                }
                              >
                                <td
                                  className={
                                    broken ? 'rail is-destructive' : 'rail'
                                  }
                                  aria-hidden
                                >
                                  <i />
                                </td>
                                <td className="mono">{item.seq}</td>
                                <td className="mono">
                                  {timestamp(item.timestamp)}
                                </td>
                                <td className="mono" title={item.actor}>
                                  {item.actor}
                                </td>
                                <td className="mono">{item.event}</td>
                                <td>
                                  <Hash value={item.current_hash} />
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </Panel>

            {tab === 'evidence' && (
              <RegisterEvidence
                caseId={openCase.case_id}
                onRegistered={() => void load(openCase.case_id)}
              />
            )}
          </>
        )}

        {!openCase && cases.length > 0 && (
          <Panel title="No case open">
            <Empty>
              Select a case above. Every screen then files what it does against
              it, and a report generated later inherits the case id rather than
              having it retyped.
            </Empty>
          </Panel>
        )}
      </div>
    </>
  )
}
