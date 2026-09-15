import { Fragment, useEffect, useState } from 'react'
import { api, RequestFailed } from '../lib/api'
import type { Capabilities, DeviceRow, HiddenAreaReport } from '../lib/api'
import { bytes, exactBytes } from '../lib/format'
import { flashOf } from '../lib/erasePlan'
import { Empty, ErrorNotice, Limitations, Panel, Verdict } from '../components/widgets'
import type { Tone } from '../components/widgets'

/**
 * The capability verdict.
 *
 * It states what the drive can actually deliver, derived from what was probed
 * rather than from what was requested. "Clear only" is not a downgrade the UI
 * chose - it is what the hardware reported, and saying "Purge" over a device
 * that cannot purge is the single most dangerous thing this screen could do.
 *
 * Three strings, not one: the word, the probe result it was derived from, and
 * the full sentence the operator opens when a judge asks. `Verdict` renders
 * the first two and the sub-row carries the third; see the component's note in
 * components/widgets.tsx for why none of it lives behind a hover.
 */
export function capabilityBadge(caps: Capabilities | null): {
  label: string
  tone: Tone
  basis: string
  why: string
} {
  if (!caps) {
    return {
      label: 'Not probed',
      tone: 'unknown',
      basis: 'probe did not complete',
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
          label: 'SED · OPAL',
          tone: 'success',
          basis: 'crypto erase available',
          why: 'Self-encrypting drive with a usable cryptographic erase.',
        }
      : {
          label: 'SED · PYRITE',
          tone: 'warning',
          basis: 'no crypto erase',
          why:
            'Pyrite implements the Opal command set without media encryption, ' +
            'so there is no key to destroy and a crypto erase would erase nothing.',
        }
  }
  if (caps.achievable_levels.includes('PURGE')) {
    const ops = caps.ata_sanitize_ops.join(', ') || 'NVMe SANICAP'
    return {
      label: 'PURGE AVAILABLE',
      tone: 'success',
      basis: ops,
      why: `Firmware sanitize reported: ${ops}.`,
    }
  }
  return {
    label: 'CLEAR ONLY',
    tone: 'warning',
    basis: 'no firmware sanitize reported',
    why:
      'No firmware sanitize or cryptographic erase was reported, so a host ' +
      'overwrite is the strongest available result. On flash media that ' +
      'leaves remapped and over-provisioned blocks untouched.',
  }
}

/**
 * Hidden areas.
 *
 * The word carries the state and the colour reinforces it. "none", "not
 * probed" and "HPA 1.05 GB" are already distinct read as text alone, so this
 * column loses nothing in greyscale and needs no glyph beside it.
 */
function HiddenAreas({ report }: { report: HiddenAreaReport | null }) {
  if (!report) return <span className="state-mark is-muted">not probed</span>
  if (report.hidden_bytes <= 0) {
    return <span className="state-mark is-success">none</span>
  }
  return (
    <span className="state-mark is-destructive">
      {report.hpa_present ? 'HPA' : 'DCO'} {bytes(report.hidden_bytes)}
    </span>
  )
}

/** A shackle. Drawn inline: no icon font, no sprite fetched from anywhere. */
function Shackle() {
  return (
    <svg width="13" height="15" viewBox="0 0 13 15" aria-hidden>
      <rect
        x="0.75"
        y="6.25"
        width="11.5"
        height="8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M3 6.25 V4 a3.5 3.5 0 0 1 7 0 V6.25"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <rect x="5.75" y="9" width="1.5" height="3" fill="currentColor" />
    </svg>
  )
}

export default function Devices({
  onSelect,
}: {
  onSelect: (row: DeviceRow) => void
}) {
  const [rows, setRows] = useState<DeviceRow[]>([])
  const [limitations, setLimitations] = useState<string[]>([])
  const [openPath, setOpenPath] = useState<string | null>(null)
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

        <Panel
          title={`Block devices (${rows.length})`}
          subtitle="Capability is what the probe reported, not what was requested."
          tight
        >
          {rows.length === 0 ? (
            <Empty>
              {loading
                ? 'Probing the host…'
                : 'No block devices were reported. The privileged helper may not be running.'}
            </Empty>
          ) : (
            <table className="itable">
              {/* Fixed widths so the columns line up down the table and the
                  header never truncates mid-word. Capability takes what is
                  left, and its basis line ellipsises rather than wrapping -
                  the full sentence is one click away on the sub-row. */}
              {/* Widths are sized to the longest real value each column
                  holds, not divided evenly. A serial is typed character by
                  character into the confirm dialog, so it gets the 24
                  monospace characters it needs and never ellipsises; so do
                  size and bus, which are short by nature. Model is the only
                  column that can lose its tail without costing anything, so
                  Model is the one that flexes. */}
              <colgroup>
                <col style={{ width: 'var(--gutter)' }} />
                <col style={{ width: 140 }} />
                <col />
                <col style={{ width: 186 }} />
                <col style={{ width: 92 }} />
                <col style={{ width: 104 }} />
                <col style={{ width: 238 }} />
                <col style={{ width: 140 }} />
              </colgroup>
              <thead>
                <tr>
                  <th className="rail" />
                  <th>Path</th>
                  <th>Model</th>
                  <th>Serial</th>
                  <th>Size</th>
                  <th>Bus</th>
                  <th>Capability</th>
                  <th>Hidden areas</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const badge = capabilityBadge(row.capabilities)
                  const barred =
                    row.device.is_system_disk || row.device.mounted_at.length > 0
                  const reason = row.device.is_system_disk
                    ? 'Locked: this device hosts the running root filesystem. ' +
                      'Boot from separate media and run the erase against it as ' +
                      'a non-system disk.'
                    : `Locked: mounted at ${row.device.mounted_at.join(', ')}. ` +
                      'Unmount every filesystem on the device and retry.'
                  const open = openPath === row.device.path
                  return (
                    <Fragment key={row.device.path}>
                      <tr
                        className={
                          barred ? 'irow is-barred' : 'irow is-openable'
                        }
                        onClick={() => !barred && onSelect(row)}
                      >
                        <td
                          className={barred ? 'rail is-destructive' : 'rail'}
                          aria-hidden
                        >
                          <i />
                        </td>
                        {/* Path and a shackle. Nothing else: the refusal is a
                            sentence and belongs on a row wide enough to hold
                            one. */}
                        <td className="path">
                          {barred ? (
                            <span className="lockup">
                              <Shackle />
                              <span className="path">{row.device.path}</span>
                            </span>
                          ) : (
                            row.device.path
                          )}
                        </td>
                        <td title={row.device.model}>{row.device.model}</td>
                        <td className="serial">{row.device.serial}</td>
                        <td className="mono" title={exactBytes(row.device.size_bytes)}>
                          {bytes(row.device.size_bytes)}
                        </td>
                        {/* The engine's determination, not `rotational`: a USB
                            bridge leaves that flag set on a flash stick. */}
                        <td className="mono" title={flashOf(row).reason}>
                          {row.device.transport}
                          {flashOf(row).flash ? ' flash' : ''}
                        </td>
                        <td>
                          <Verdict
                            level={badge.label}
                            basis={badge.basis}
                            tone={badge.tone}
                            open={open}
                            onToggle={() =>
                              setOpenPath(open ? null : row.device.path)
                            }
                          />
                        </td>
                        <td>
                          <HiddenAreas report={row.hidden_areas} />
                        </td>
                      </tr>
                      {barred && (
                        <tr className="subrow is-locked">
                          <td className="rail is-destructive" aria-hidden>
                            <i />
                          </td>
                          <td colSpan={7}>
                            <span className="lock-reason">{reason}</span>
                          </td>
                        </tr>
                      )}
                      {open && (
                        <tr className="subrow">
                          <td className="rail" aria-hidden>
                            <i />
                          </td>
                          <td colSpan={7}>
                            <dl className="evidence">
                              <dt>Claim</dt>
                              <dd>{badge.label}</dd>
                              <dt>Because</dt>
                              <dd>{badge.why}</dd>
                              <dt>Levels</dt>
                              <dd className="mono">
                                {row.capabilities?.achievable_levels.join(', ') ||
                                  'none reported'}
                              </dd>
                              <dt>Sanitize ops</dt>
                              <dd className="mono">
                                {row.capabilities?.ata_sanitize_ops.join(', ') ||
                                  'none reported'}
                              </dd>
                              {row.hidden_areas && row.hidden_areas.hidden_bytes > 0 && (
                                <>
                                  <dt>Hidden</dt>
                                  <dd>
                                    {exactBytes(row.hidden_areas.hidden_bytes)} lie beyond
                                    the accessible max (
                                    {row.hidden_areas.accessible_sectors} of{' '}
                                    {row.hidden_areas.native_max_sectors} sectors). An
                                    overwrite does not reach them unless the native max is
                                    unlocked first.
                                  </dd>
                                </>
                              )}
                            </dl>
                          </td>
                        </tr>
                      )}
                    </Fragment>
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
