import { useEffect, useState } from 'react'
import { api, RequestFailed } from '../lib/api'
import type { CapabilityStatus, OperationCapability, PlatformStatus } from '../lib/api'
import { privilegeWord, statusWord } from '../lib/platform'
import { Empty, ErrorNotice, Evidence, Limitations, Panel } from '../components/widgets'

/**
 * Sanctum platform status.
 *
 * Five lines an examiner reads without knowing what an adapter is: which
 * computer this is, who they are on it, and what it can do to storage. Every
 * row opens a plain-English *Why?*, and the probe behind it sits under
 * Technical details for the judge who asks.
 *
 * There is no checkmark in this file. A status the server did not compute
 * cannot appear, and UNVERIFIED is drawn as unverified.
 */

/** The rows a non-specialist cares about, in the order they matter. */
const HEADLINE_ROWS = [
  'device_discovery',
  'file_erase',
  'folder_erase',
  'file_verification',
  'whole_drive_clear',
  'whole_drive_purge',
]

function Status({ status }: { status: CapabilityStatus }) {
  const { word, tone } = statusWord(status)
  return (
    <span className={`state-mark is-${tone}`} data-status={status}>
      {word}
    </span>
  )
}

function CapabilityRow({ row }: { row: OperationCapability }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <div className="cap-row">
        <span className="cap-row-label">{row.label}</span>
        <Status status={row.status} />
        <button
          className="link-button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {open ? 'Hide' : 'Why?'}
        </button>
      </div>
      {open && (
        <div className="cap-row-why">
          <p className="answer-detail">{row.reason}</p>
          {row.verification && (
            <p className="answer-detail">
              <strong>Verification: </strong>
              {row.verification}
            </p>
          )}
          {row.limitations.length > 0 && <Limitations items={row.limitations} />}
          <p className="note-faint mono">Established by: {row.source}</p>
        </div>
      )}
    </>
  )
}

const FS_ROWS: { key: string; label: string }[] = [
  { key: 'detect', label: 'Detect' },
  { key: 'read', label: 'Recover from image' },
  { key: 'erase_files', label: 'Erase files' },
  { key: 'metadata', label: 'Filesystem metadata' },
  { key: 'free_space', label: 'Free-space wipe' },
  { key: 'whole_drive', label: 'Whole drive' },
]

const PLATFORMS: { key: string; label: string }[] = [
  { key: 'linux', label: 'Linux' },
  { key: 'windows', label: 'Windows' },
  { key: 'macos', label: 'macOS' },
]

export default function Platform() {
  const [status, setStatus] = useState<PlatformStatus | null>(null)
  const [error, setError] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)
  const [filesystem, setFilesystem] = useState('NTFS')

  useEffect(() => {
    void api
      .platform()
      .then(setStatus)
      .catch((exc: RequestFailed) =>
        setError({ message: exc.message, kind: exc.kind, remediation: exc.remediation }),
      )
  }, [])

  const fs = status?.filesystems.find((item) => item.filesystem === filesystem)
  const byOperation = new Map(status?.operations.map((row) => [row.operation, row]))
  const headline = HEADLINE_ROWS.map((name) => byOperation.get(name)).filter(
    (row): row is OperationCapability => Boolean(row),
  )
  const rest = (status?.operations ?? []).filter(
    (row) => !HEADLINE_ROWS.includes(row.operation),
  )
  const build = status?.platform.build ?? {}

  return (
    <>
      <div className="screen-head">
        <h1>Sanctum platform status</h1>
        <p>What this computer can do, and why.</p>
      </div>
      <div className="screen-body">
        <ErrorNotice error={error} />
        {!status && !error && <Empty>Asking this computer&hellip;</Empty>}
        {status && (
          <>
            <Panel title="This computer">
              <div className="row wrap" style={{ gap: 'var(--space-6)' }}>
                <span className="stat">
                  <span className="stat-label">platform</span>
                  <span className="stat-value">{status.platform.os_name}</span>
                </span>
                <span className="stat">
                  <span className="stat-label">application</span>
                  <span className="stat-value">
                    Sanctum {status.platform.app_version}
                    {status.platform.packaged ? '' : ' (from source)'}
                  </span>
                </span>
                <span className="stat">
                  <span className="stat-label">privilege</span>
                  <span className="stat-value">{privilegeWord(status.privilege)}</span>
                </span>
              </div>
            </Panel>

            <Panel title="Device support">
              <div className="cap-rows">
                {headline.map((row) => (
                  <CapabilityRow key={row.operation} row={row} />
                ))}
              </div>
            </Panel>

            <details className="tech">
              <summary>Technical details</summary>
              <div className="tech-body">
                <Panel title="Every operation">
                  <div className="cap-rows">
                    {rest.map((row) => (
                      <CapabilityRow key={row.operation} row={row} />
                    ))}
                  </div>
                </Panel>

                <Panel title="How this build was made">
                  <Evidence
                    stacked
                    rows={[
                      {
                        label: 'Build',
                        value: build.version
                          ? `${build.version} (${build.platform} ${build.architecture})`
                          : 'built from a source checkout',
                      },
                      { label: 'Commit', value: build.commit || 'not recorded', kind: 'mono' },
                      { label: 'Built', value: build.build_date || 'not recorded' },
                      { label: 'Built by', value: build.builder || 'not recorded' },
                      {
                        label: 'Runtime',
                        value: build.python ? `Python ${build.python}` : 'not recorded',
                      },
                      { label: 'Signed', value: build.signed === 'yes' ? 'yes' : 'no' },
                      { label: 'Privilege basis', value: status.privilege.basis },
                      { label: 'Adapter', value: status.adapter, kind: 'mono' },
                      {
                        label: 'OS build',
                        value: `${status.platform.os_build || status.platform.os_version} (${status.platform.machine})`,
                      },
                    ]}
                  />
                </Panel>

                <Panel
                  title="Storage support"
                  subtitle="By kind of device. The count is what this computer has now."
                >
                  <table className="itable cap-table">
                    <thead>
                      <tr>
                        <th style={{ width: '22%' }}>Storage</th>
                        <th>Found now</th>
                        <th>Discovery</th>
                        <th>File erase</th>
                        <th>Whole drive</th>
                      </tr>
                    </thead>
                    <tbody>
                      {status.media_classes.map((row) => (
                        <tr key={row.media_class} title={row.reason}>
                          <td>{row.media_class}</td>
                          <td className="mono">{row.detected_now}</td>
                          <td>
                            <Status status={row.discovery} />
                          </td>
                          <td>
                            <Status status={row.file_erase} />
                          </td>
                          <td>
                            <Status status={row.whole_drive} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Panel>

                <Panel
                  title="Filesystems"
                  subtitle="Detecting a filesystem is not supporting it."
                >
                  <div className="row wrap" role="tablist" aria-label="Filesystem">
                    {status.filesystems.map((item) => (
                      <button
                        key={item.filesystem}
                        role="tab"
                        aria-selected={item.filesystem === filesystem}
                        className={item.filesystem === filesystem ? 'btn primary' : 'btn'}
                        onClick={() => setFilesystem(item.filesystem)}
                      >
                        {item.filesystem}
                      </button>
                    ))}
                  </div>
                  {fs && (
                    <table
                      className="itable cap-table"
                      style={{ marginTop: 'var(--space-3)' }}
                    >
                      <thead>
                        <tr>
                          <th style={{ width: '22%' }}>{fs.filesystem}</th>
                          {PLATFORMS.map((p) => (
                            <th key={p.key}>
                              {p.label}
                              {p.key === status.platform.family ? ' (this computer)' : ''}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {FS_ROWS.map((r) => (
                          <tr key={r.key} title={fs.notes[r.key]}>
                            <td>{r.label}</td>
                            {PLATFORMS.map((p) => (
                              <td key={p.key}>
                                <Status
                                  status={fs.cells[r.key]?.[p.key] ?? 'UNSUPPORTED'}
                                />
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </Panel>
              </div>
            </details>

            <Panel title="What this computer cannot do">
              <Limitations items={status.restrictions} />
            </Panel>
          </>
        )}
      </div>
    </>
  )
}
