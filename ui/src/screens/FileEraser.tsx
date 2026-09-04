import { useEffect, useRef, useState } from 'react'
import { api, RequestFailed, streamJob } from '../lib/api'
import type { FileEraseRecord, JobStatus, Progress, ResidualFinding } from '../lib/api'
import { bytes } from '../lib/format'
import { Chip, Empty, ErrorNotice, Notice, Panel, ProgressView } from '../components/widgets'

function worstSeverity(findings: ResidualFinding[]): string | null {
  const order = ['HIGH', 'MEDIUM', 'LOW']
  for (const level of order) {
    if (findings.some((item) => item.severity === level)) return level
  }
  return null
}

export default function FileEraser() {
  const [paths, setPaths] = useState<string[]>([])
  const [entry, setEntry] = useState('')
  const [over, setOver] = useState(false)
  const [dryRun, setDryRun] = useState(true)
  const [confirm, setConfirm] = useState(false)
  const [cleanse, setCleanse] = useState(true)
  const [breakLinks, setBreakLinks] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [status, setStatus] = useState<JobStatus | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [error, setError] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)
  const detach = useRef<(() => void) | null>(null)

  useEffect(() => () => detach.current?.(), [])

  function add(values: string[]) {
    setPaths((current) => [
      ...current,
      ...values.filter((item) => item && !current.includes(item)),
    ])
  }

  async function start() {
    setError(null)
    try {
      const accepted = await api.eraseFiles({
        paths,
        dry_run: dryRun,
        confirm: dryRun ? false : confirm,
        cleanse_metadata: cleanse,
        break_hardlinks: breakLinks,
        recursive: true,
      })
      setJobId(accepted.job_id)
      setProgress(null)
      setStatus(null)
      detach.current?.()
      detach.current = streamJob(accepted.job_id, {
        onProgress: setProgress,
        onState: setStatus,
      })
    } catch (exc) {
      const failure = exc as RequestFailed
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    }
  }

  const records = (status?.result?.records ?? []) as FileEraseRecord[]

  return (
    <>
      <div className="screen-head">
        <h1>File eraser</h1>
        <p>
          Best-effort destruction, plus an enumeration of everything it could not
          guarantee.
        </p>
      </div>

      <div className="screen-body">
        <ErrorNotice error={error} />

        <Notice tone="info">
          Overwriting a file through the filesystem does not reliably destroy it.
          Journals, copy-on-write, resident data, slack, snapshots and TRIM all
          keep copies the OS will not hand back. The residual findings column is
          the deliverable — not the overwrite.
        </Notice>

        <div className="split">
          <div className="col">
            <Panel title="Queue">
              <div className="col" style={{ gap: 10 }}>
                <div
                  className={over ? 'dropzone over' : 'dropzone'}
                  onDragOver={(event) => {
                    event.preventDefault()
                    setOver(true)
                  }}
                  onDragLeave={() => setOver(false)}
                  onDrop={(event) => {
                    event.preventDefault()
                    setOver(false)
                    // The browser gives a name, never a filesystem path — a
                    // page cannot learn where a dropped file lives. The names
                    // are queued and the operator completes the path, which is
                    // also why the text field below exists at all.
                    const names = Array.from(event.dataTransfer.files).map(
                      (file) => file.name,
                    )
                    add(names)
                  }}
                >
                  Drop files to queue their names, then complete each path below.
                  <div style={{ marginTop: 5, fontSize: 11, color: 'var(--fg-faint)' }}>
                    A browser is not told where a dropped file lives; only its
                    name crosses into the page.
                  </div>
                </div>

                <div className="row">
                  <input
                    type="text"
                    className="grow"
                    placeholder="/absolute/path/to/file-or-directory"
                    value={entry}
                    spellCheck={false}
                    onChange={(event) => setEntry(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && entry.trim()) {
                        add([entry.trim()])
                        setEntry('')
                      }
                    }}
                  />
                  <button
                    className="btn"
                    disabled={!entry.trim()}
                    onClick={() => {
                      add([entry.trim()])
                      setEntry('')
                    }}
                  >
                    Add
                  </button>
                </div>

                {paths.length > 0 && (
                  <ul
                    className="limitations mono"
                    style={{ listStyle: 'none', paddingLeft: 0 }}
                  >
                    {paths.map((path) => (
                      <li key={path} className="row spread">
                        <span className="path">{path}</span>
                        <button
                          className="btn"
                          style={{ padding: '1px 7px' }}
                          onClick={() =>
                            setPaths((current) =>
                              current.filter((item) => item !== path),
                            )
                          }
                        >
                          remove
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </Panel>

            {records.length > 0 && (
              <Panel title={`Results (${records.length})`} tight>
                <table>
                  <thead>
                    <tr>
                      <th>Path</th>
                      <th>Status</th>
                      <th>Overwritten</th>
                      <th>Residual findings</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((record) => {
                      const worst = worstSeverity(record.findings)
                      // A file carrying a HIGH finding is styled unlike a clean
                      // one: the row is tinted and rule-marked, so it is
                      // distinguishable at a glance rather than on inspection.
                      const rowClass = worst
                        ? `clickable severity-${worst.toLowerCase()}`
                        : 'clickable'
                      return [
                        <tr
                          key={record.path}
                          className={rowClass}
                          onClick={() =>
                            setExpanded(
                              expanded === record.path ? null : record.path,
                            )
                          }
                        >
                          <td className="path">{record.path}</td>
                          <td>
                            {record.ok ? (
                              <Chip tone={record.dry_run ? 'muted' : 'low'}>
                                {record.dry_run ? 'simulated' : 'erased'}
                              </Chip>
                            ) : (
                              <Chip tone="high" title={record.error ?? ''}>
                                {record.error_kind ?? 'failed'}
                              </Chip>
                            )}
                          </td>
                          <td>{bytes(record.bytes_overwritten)}</td>
                          <td>
                            <span className="row wrap" style={{ gap: 4 }}>
                              {record.findings.length === 0 ? (
                                <Chip tone="low">none</Chip>
                              ) : (
                                record.findings.map((finding) => (
                                  <Chip
                                    key={finding.kind}
                                    tone={
                                      finding.severity.toLowerCase() as
                                        | 'high'
                                        | 'medium'
                                        | 'low'
                                    }
                                    title={finding.explanation}
                                  >
                                    {finding.kind}
                                  </Chip>
                                ))
                              )}
                            </span>
                          </td>
                        </tr>,
                        expanded === record.path && (
                          <tr key={`${record.path}-detail`}>
                            <td colSpan={4} style={{ background: 'var(--bg-input)' }}>
                              <div className="col" style={{ gap: 9 }}>
                                {record.findings.map((finding) => (
                                  <div key={finding.kind} className="col" style={{ gap: 2 }}>
                                    <span className="row" style={{ gap: 7 }}>
                                      <Chip
                                        tone={
                                          finding.severity.toLowerCase() as
                                            | 'high'
                                            | 'medium'
                                            | 'low'
                                        }
                                      >
                                        {finding.severity}
                                      </Chip>
                                      <strong style={{ fontSize: 12 }}>
                                        {finding.kind}
                                      </strong>
                                      <Chip tone={finding.addressable ? 'accent' : 'muted'}>
                                        {finding.addressable
                                          ? 'you can address this'
                                          : 'not addressable'}
                                      </Chip>
                                    </span>
                                    <span style={{ color: 'var(--fg-dim)', fontSize: 11 }}>
                                      {finding.explanation}
                                    </span>
                                  </div>
                                ))}
                                {record.verification && (
                                  <Notice
                                    tone={
                                      record.verification.passed === true
                                        ? 'ok'
                                        : 'warn'
                                    }
                                  >
                                    Verification:{' '}
                                    <strong>
                                      {record.verification.passed === null
                                        ? 'nothing claimed'
                                        : record.verification.passed
                                          ? 'confirmed by physical read'
                                          : 'FAILED'}
                                    </strong>{' '}
                                    ({record.verification.strategy}) —{' '}
                                    {record.verification.reason}
                                  </Notice>
                                )}
                                {record.limitations.length > 0 && (
                                  <ul className="limitations">
                                    {record.limitations.map((item, index) => (
                                      <li key={index}>{item}</li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            </td>
                          </tr>
                        ),
                      ]
                    })}
                  </tbody>
                </table>
              </Panel>
            )}

            {jobId && records.length === 0 && (
              <Panel title="Progress">
                <ProgressView progress={progress} destructive={!dryRun} />
              </Panel>
            )}
          </div>

          <Panel title="Options">
            <div className="col" style={{ gap: 11 }}>
              <label className="inline">
                <input
                  type="checkbox"
                  checked={dryRun}
                  onChange={(event) => setDryRun(event.target.checked)}
                />
                <span>Dry run — enumerate what would survive, write nothing</span>
              </label>
              <label className="inline">
                <input
                  type="checkbox"
                  checked={confirm}
                  disabled={dryRun}
                  onChange={(event) => setConfirm(event.target.checked)}
                />
                <span>
                  Confirm — the second gate, required when dry run is off
                </span>
              </label>
              <label className="inline">
                <input
                  type="checkbox"
                  checked={cleanse}
                  onChange={(event) => setCleanse(event.target.checked)}
                />
                <span>Cleanse metadata before overwriting</span>
              </label>
              <label className="inline">
                <input
                  type="checkbox"
                  checked={breakLinks}
                  onChange={(event) => setBreakLinks(event.target.checked)}
                />
                <span>
                  Break hard links — destroys data reachable under names you did
                  not give
                </span>
              </label>

              {!dryRun && (
                <Notice tone="danger">
                  Files will be overwritten, renamed eight times and unlinked.
                  There is no undo.
                </Notice>
              )}

              <button
                className={dryRun ? 'btn primary' : 'btn destructive'}
                disabled={paths.length === 0 || (!dryRun && !confirm)}
                onClick={() => void start()}
              >
                {dryRun ? `Simulate ${paths.length} path(s)` : `Erase ${paths.length} path(s)`}
              </button>

              {paths.length === 0 && <Empty>Queue is empty.</Empty>}
            </div>
          </Panel>
        </div>
      </div>
    </>
  )
}
