import { useEffect, useState } from 'react'
import { api, RequestFailed } from '../lib/api'
import type { Capabilities, DeviceRow, HiddenAreaReport } from '../lib/api'
import { bytes, exactBytes } from '../lib/format'
import { Chip, Empty, ErrorNotice, Limitations, Panel } from '../components/widgets'

/**
 * The capability badge.
 *
 * It states what the drive can actually deliver, derived from what was probed
 * rather than from what was requested. "Clear only" is not a downgrade the UI
 * chose - it is what the hardware reported, and saying "Purge" over a device
 * that cannot purge is the single most dangerous thing this screen could do.
 */
export function capabilityBadge(caps: Capabilities | null): {
  label: string
  tone: 'high' | 'medium' | 'low' | 'accent' | 'muted'
  why: string
} {
  if (!caps) {
    return {
      label: 'Not probed',
      tone: 'muted',
      why: 'The capability probe did not complete, so nothing is claimed.',
    }
  }
  if (caps.is_sed_opal) {
    // Opal supports a cryptographic erase; Pyrite is the same command set
    // *without* it, and calling both "SED" would imply a capability half of
    // them do not have.
    const cryptoCapable =
      caps.achievable_levels.includes('PURGE') ||
      caps.ata_sanitize_ops.includes('CRYPTO_SCRAMBLE_EXT')
    return cryptoCapable
      ? {
          label: 'SED (Opal)',
          tone: 'low',
          why: 'Self-encrypting drive with a usable cryptographic erase.',
        }
      : {
          label: 'SED (Pyrite — no crypto erase)',
          tone: 'medium',
          why:
            'Pyrite implements the Opal command set without media encryption, ' +
            'so there is no key to destroy and a crypto erase would erase nothing.',
        }
  }
  if (caps.achievable_levels.includes('PURGE')) {
    return {
      label: 'Purge available',
      tone: 'low',
      why: `Firmware sanitize reported: ${caps.ata_sanitize_ops.join(', ') || 'NVMe SANICAP'}.`,
    }
  }
  return {
    label: 'Clear only',
    tone: 'medium',
    why:
      'No firmware sanitize or cryptographic erase was reported, so a host ' +
      'overwrite is the strongest available result. On flash media that ' +
      'leaves remapped and over-provisioned blocks untouched.',
  }
}

function HiddenAreaChip({ report }: { report: HiddenAreaReport | null }) {
  if (!report) return <Chip tone="muted">HPA/DCO not probed</Chip>
  if (report.hidden_bytes <= 0) {
    return <Chip tone="muted">no hidden areas</Chip>
  }
  return (
    <Chip
      tone="high"
      title={
        `${exactBytes(report.hidden_bytes)} lie beyond the accessible max ` +
        `(${report.accessible_sectors} of ${report.native_max_sectors} sectors). ` +
        'An overwrite does not reach them unless the native max is unlocked first.'
      }
    >
      {report.hpa_present ? 'HPA' : 'DCO'} hides {bytes(report.hidden_bytes)}
    </Chip>
  )
}

export default function Devices({
  onSelect,
}: {
  onSelect: (row: DeviceRow) => void
}) {
  const [rows, setRows] = useState<DeviceRow[]>([])
  const [limitations, setLimitations] = useState<string[]>([])
  const [error, setError] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)
  const [loading, setLoading] = useState(true)

  async function refresh() {
    setLoading(true)
    try {
      const answer = await api.devices(true)
      setRows(answer.devices)
      setLimitations(answer.limitations)
      setError(null)
    } catch (exc) {
      const failure = exc as RequestFailed
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  return (
    <>
      <div className="screen-head">
        <h1>Devices</h1>
        <p>Enumerated from the host, capability probed, hidden areas measured.</p>
        <div className="grow" />
        <button className="btn" onClick={() => void refresh()} disabled={loading}>
          {loading ? 'Probing…' : 'Rescan'}
        </button>
      </div>
      <div className="screen-body">
        <ErrorNotice error={error} />
        <Limitations items={limitations} />

        <Panel title={`Block devices (${rows.length})`} tight>
          {rows.length === 0 ? (
            <Empty>
              {loading
                ? 'Probing the host…'
                : 'No block devices were reported. The privileged helper may not be running.'}
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: 26 }} />
                  <th>Path</th>
                  <th>Model</th>
                  <th>Serial</th>
                  <th>Size</th>
                  <th>Transport</th>
                  <th>Capability</th>
                  <th>Hidden areas</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const badge = capabilityBadge(row.capabilities)
                  const locked =
                    row.device.is_system_disk || row.device.mounted_at.length > 0
                  const reason = row.device.is_system_disk
                    ? 'Refused: this device hosts the running root filesystem. ' +
                      'Boot from separate media and run the erase against it as ' +
                      'a non-system disk.'
                    : `Refused: mounted at ${row.device.mounted_at.join(', ')}. ` +
                      'Unmount every filesystem on the device and retry.'
                  return (
                    <tr
                      key={row.device.path}
                      className={locked ? '' : 'clickable'}
                      onClick={() => !locked && onSelect(row)}
                    >
                      <td>
                        {locked ? (
                          <span
                            title={reason}
                            style={{ color: 'var(--high)', cursor: 'help' }}
                            aria-label="locked"
                          >
                            {/* A padlock drawn inline: no icon font, no sprite
                                fetched from anywhere. */}
                            <svg width="11" height="13" viewBox="0 0 11 13">
                              <rect
                                x="0.5"
                                y="5.5"
                                width="10"
                                height="7"
                                fill="none"
                                stroke="currentColor"
                              />
                              <path
                                d="M2.5 5.5 V3.5 a3 3 0 0 1 6 0 V5.5"
                                fill="none"
                                stroke="currentColor"
                              />
                            </svg>
                          </span>
                        ) : null}
                      </td>
                      <td className="path">{row.device.path}</td>
                      <td>{row.device.model}</td>
                      <td className="serial">{row.device.serial}</td>
                      <td title={exactBytes(row.device.size_bytes)}>
                        {bytes(row.device.size_bytes)}
                      </td>
                      <td>
                        {row.device.transport}
                        {row.device.rotational ? '' : ' · flash'}
                      </td>
                      <td>
                        <Chip tone={badge.tone} title={badge.why}>
                          {badge.label}
                        </Chip>
                      </td>
                      <td>
                        <HiddenAreaChip report={row.hidden_areas} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </Panel>

        {rows.some((row) => row.capability_error || row.hidden_area_error) && (
          <Panel title="Probes that did not complete">
            <ul className="limitations">
              {rows.flatMap((row) =>
                [row.capability_error, row.hidden_area_error]
                  .filter(Boolean)
                  .map((message, index) => (
                    <li key={`${row.device.path}-${index}`}>
                      <span className="path">{row.device.path}</span>: {message}
                    </li>
                  )),
              )}
            </ul>
          </Panel>
        )}
      </div>
    </>
  )
}
