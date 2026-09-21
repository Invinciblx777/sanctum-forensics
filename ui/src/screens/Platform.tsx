import { useEffect, useState } from 'react'
import { api, RequestFailed } from '../lib/api'
import type { CapabilityStatus, PlatformStatus } from '../lib/api'
import { privilegeWord, statusWord } from '../lib/platform'
import { Empty, ErrorNotice, Evidence, Limitations, Panel } from '../components/widgets'

/**
 * Platform & device capabilities.
 *
 * Every row on this screen is a row the server computed from a probe, and the
 * probe is printed under it. There is no checkmark in this file: a status the
 * server did not send cannot appear, and UNVERIFIED is drawn as unverified.
 */

function Status({ status }: { status: CapabilityStatus }) {
  const { word, tone } = statusWord(status)
  return (
    <span className={`state-mark is-${tone}`} data-status={status}>
      {word}
    </span>
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

  return (
    <>
      <div className="screen-head">
        <h1>Platform &amp; device capabilities</h1>
        <p>What this computer can do, and how each answer was established.</p>
      </div>
      <div className="screen-body">
        <ErrorNotice error={error} />
        {!status && !error && <Empty>Asking the platform adapter&hellip;</Empty>}
        {status && (
          <>
            <Panel title="This computer">
              <Evidence
                stacked
                rows={[
                  { label: 'Platform', value: status.platform.os_name },
                  {
                    label: 'Build',
                    value: `${status.platform.os_build || status.platform.os_version} (${status.platform.machine})`,
                  },
                  {
                    label: 'App version',
                    value: `${status.platform.app_version}${status.platform.packaged ? ' (installed package)' : ' (source checkout)'}`,
                  },
                  {
                    label: 'Privileges',
                    value: `${privilegeWord(status.privilege)} — ${status.privilege.basis}`,
                  },
                  { label: 'Adapter', value: status.adapter, kind: 'mono' },
                ]}
              />
            </Panel>

            <Panel
              title="Operations"
              subtitle="Status, why, and the probe or code path it came from."
            >
              <table className="itable cap-table">
                <thead>
                  <tr>
                    <th style={{ width: '24%' }}>Operation</th>
                    <th style={{ width: '20%' }}>Status</th>
                    <th>Why</th>
                  </tr>
                </thead>
                <tbody>
                  {status.operations.map((row) => (
                    <tr key={row.operation} data-operation={row.operation}>
                      <td>{row.label}</td>
                      <td>
                        <Status status={row.status} />
                      </td>
                      <td className="cap-why">
                        <span>{row.reason}</span>
                        <span className="note-faint mono">Source: {row.source}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>

            <Panel
              title="Storage support"
              subtitle="By kind of device. The count is what discovery found on this computer now."
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
              <p className="note" style={{ marginTop: 'var(--space-2)' }}>
                {status.media_classes[0]?.reason}
              </p>
            </Panel>

            <Panel
              title="Filesystems"
              subtitle="Detecting a filesystem is not supporting it. Each operation is its own row."
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
                <table className="itable cap-table" style={{ marginTop: 'var(--space-3)' }}>
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
                            <Status status={fs.cells[r.key]?.[p.key] ?? 'UNSUPPORTED'} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Panel>

            <Panel title="What this computer cannot do">
              <Limitations items={status.restrictions} />
            </Panel>
          </>
        )}
      </div>
    </>
  )
}
