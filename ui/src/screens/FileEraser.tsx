import { Fragment, useEffect, useRef, useState } from 'react'
import { api, RequestFailed, streamJob } from '../lib/api'
import type { FileEraseRecord, JobStatus, Progress, ResidualFinding } from '../lib/api'
import { bytes } from '../lib/format'
import {
  Empty,
  ErrorNotice,
  FilePath,
  Notice,
  Panel,
  ProgressView,
  Railed,
  Verdict,
} from '../components/widgets'
import type { Tone } from '../components/widgets'

function worstSeverity(findings: ResidualFinding[]): string | null {
  const order = ['HIGH', 'MEDIUM', 'LOW']
  for (const level of order) {
    if (findings.some((item) => item.severity === level)) return level
  }
  return null
}

/**
 * Severity to tone.
 *
 * Three severities and three colours do not line up, so the word carries the
 * difference between LOW and MEDIUM and the colour only separates "something
 * survived" from "the worst kind of thing survived". Read in greyscale nothing
 * is lost, because the word was always the payload.
 */
function severityTone(severity: string | null): Tone {
  if (severity === null) return 'success'
  return severity === 'HIGH' ? 'destructive' : 'warning'
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
              <div className="col">
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
                  <div className="note-faint" style={{ marginTop: 'var(--space-1)' }}>
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
                        <FilePath value={path} />
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
              <Panel
                title={`Results (${records.length})`}
                subtitle="What survived is the finding. Click a row for the detail."
                tight
              >
                <table className="itable">
                  <colgroup>
                    <col style={{ width: 'var(--gutter)' }} />
                    <col />
                    <col style={{ width: 112 }} />
                    <col style={{ width: 142 }} />
                    <col style={{ width: 196 }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th className="rail" />
                      <th>Path</th>
                      <th>Status</th>
                      <th>Overwritten</th>
                      <th>Residual findings</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((record) => {
                      // The rail carries the worst finding on the row, so a
                      // file that kept a HIGH residual is picked out in one
                      // vertical scan rather than by reading every cell.
                      const worst = worstSeverity(record.findings)
                      // A refused path has no findings because nothing ran.
                      // Rendering that as NONE in success green would report
                      // the absence of an attempt as a clean result.
                      const tone = record.ok ? severityTone(worst) : 'unknown'
                      const open = expanded === record.path
                      return (
                        <Fragment key={record.path}>
                          <tr
                            className={
                              worst === 'HIGH'
                                ? 'irow is-compact is-openable is-bad'
                                : 'irow is-compact is-openable'
                            }
                            onClick={() => setExpanded(open ? null : record.path)}
                          >
                            <td className={`rail is-${tone}`} aria-hidden>
                              <i />
                            </td>
                            <td>
                              <FilePath value={record.path} />
                            </td>
                            <td>
                              {record.ok ? (
                                <span
                                  className={
                                    record.dry_run
                                      ? 'state-mark is-muted'
                                      : 'state-mark is-success'
                                  }
                                >
                                  {record.dry_run ? 'simulated' : 'erased'}
                                </span>
                              ) : (
                                <span
                                  className="state-mark is-destructive"
                                  title={record.error ?? ''}
                                >
                                  {record.error_kind ?? 'failed'}
                                </span>
                              )}
                            </td>
                            <td className="mono">{bytes(record.bytes_overwritten)}</td>
                            <td>
                              <Verdict
                                tight
                                level={record.ok ? (worst ?? 'NONE') : 'NOT RUN'}
                                basis={
                                  record.ok
                                    ? `${record.findings.length} finding${record.findings.length === 1 ? '' : 's'}`
                                    : 'not attempted'
                                }
                                tone={tone}
                              />
                            </td>
                          </tr>
                          {open && (
                            <tr className="subrow">
                              <td className={`rail is-${tone}`} aria-hidden>
                                <i />
                              </td>
                              <td colSpan={4}>
                                <div className="col">
                                  {record.ok && record.findings.length === 0 && (
                                    <span className="note">
                                      No residual finding was raised for this
                                      path. That is the absence of a known
                                      survivor, not a guarantee that nothing
                                      survived.
                                    </span>
                                  )}
                                  {!record.ok && (
                                    <Railed tone="destructive">
                                      <span className="state-mark is-destructive">
                                        {record.error_kind ?? 'failed'}
                                      </span>
                                      <span className="note">{record.error}</span>
                                    </Railed>
                                  )}
                                  {record.findings.map((finding) => (
                                    <Railed
                                      key={finding.kind}
                                      tone={severityTone(finding.severity)}
                                    >
                                      <span className="row" style={{ gap: 'var(--space-2)' }}>
                                        <span
                                          className={`state-mark is-${severityTone(finding.severity)}`}
                                        >
                                          {finding.severity}
                                        </span>
                                        <strong className="mono">{finding.kind}</strong>
                                        <span className="note-faint">
                                          {finding.addressable
                                            ? 'you can address this'
                                            : 'not addressable'}
                                        </span>
                                      </span>
                                      <span className="note">{finding.explanation}</span>
                                    </Railed>
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
                          )}
                        </Fragment>
                      )
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
            <div className="col">
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
                <span>Confirm — the second gate, required when dry run is off</span>
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
                {dryRun
                  ? `Simulate ${paths.length} path(s)`
                  : `Erase ${paths.length} path(s)`}
              </button>

              {paths.length === 0 && <Empty>Queue is empty.</Empty>}
            </div>
          </Panel>
        </div>
      </div>
    </>
  )
}
