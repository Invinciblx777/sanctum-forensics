import { useEffect, useMemo, useRef, useState } from 'react'
import { api, RequestFailed, streamJob } from '../lib/api'
import type { Capabilities, DeviceRow, JobStatus, Progress } from '../lib/api'
import { bytes, duration, exactBytes } from '../lib/format'
import {
  Chip,
  ErrorNotice,
  Evidence,
  Limitations,
  Notice,
  Panel,
  ProgressView,
  Railed,
  Verdict,
} from '../components/widgets'
import type { Tone } from '../components/widgets'

/**
 * The tone of a NIST level.
 *
 * Purge is the strongest result the vocabulary has, so it is the only one that
 * reads as success. Clear is not a failure and is not coloured like one - it
 * is a warning, because it is a real erasure with a real limit, and an
 * operator who reads it as equivalent to Purge has been misled by the
 * interface rather than by the drive.
 */
function levelTone(level: 'CLEAR' | 'PURGE'): Tone {
  return level === 'PURGE' ? 'success' : 'warning'
}

interface MethodOption {
  id: string
  label: string
  level: 'CLEAR' | 'PURGE'
  available: boolean
  legacy?: boolean
  /**
   * Set when the *product* cannot run this method, whatever the drive reports.
   *
   * Distinct from `available: false` on its own, which means the hardware said
   * no. An operator needs to be able to tell "your drive does not support this"
   * from "this build does not implement it", because only the second is our
   * fault and only the first is a fact about the device in their hand.
   */
  unsupported?: string
  /** Why this method is or is not available, quoting what was probed. */
  evidence: string
}

/**
 * Build the method list from the capability report.
 *
 * Every entry carries the *evidence* for its own availability, not just the
 * conclusion. "Purge available" tells an operator what the tool decided;
 * "hdparm reported BLOCK_ERASE_EXT in the SANITIZE feature set" tells them why,
 * and only the second can be checked by someone who doubts it.
 */
export function methodsFor(caps: Capabilities | null): MethodOption[] {
  const ops = caps?.ata_sanitize_ops ?? []
  const nvme = (caps?.nvme_sanicap ?? {}) as Record<string, unknown>
  const frozen = caps?.security_frozen ?? false
  const purgeReachable = (caps?.achievable_levels ?? []).includes('PURGE')

  return [
    {
      id: 'ATA_SANITIZE_BLOCK_ERASE',
      label: 'ATA SANITIZE — block erase',
      level: 'PURGE',
      available: ops.includes('BLOCK_ERASE_EXT'),
      evidence: ops.includes('BLOCK_ERASE_EXT')
        ? 'hdparm -I reported BLOCK_ERASE_EXT in the SANITIZE feature set. The ' +
          'drive erases every block internally, including remapped and ' +
          'over-provisioned ones a host overwrite cannot address.'
        : 'BLOCK_ERASE_EXT was not present in the SANITIZE feature set.',
    },
    {
      id: 'ATA_SANITIZE_CRYPTO_SCRAMBLE',
      label: 'ATA SANITIZE — cryptographic scramble',
      level: 'PURGE',
      available: ops.includes('CRYPTO_SCRAMBLE_EXT'),
      evidence: ops.includes('CRYPTO_SCRAMBLE_EXT')
        ? 'hdparm -I reported CRYPTO_SCRAMBLE_EXT. The media encryption key is ' +
          'destroyed, which renders every block unreadable at once.'
        : 'CRYPTO_SCRAMBLE_EXT was not present in the SANITIZE feature set.',
    },
    {
      id: 'NVME_SANITIZE_BLOCK',
      label: 'NVMe SANITIZE — block erase',
      level: 'PURGE',
      available: Boolean(nvme.block_erase) || Boolean(nvme.crypto_erase),
      evidence:
        Boolean(nvme.block_erase) || Boolean(nvme.crypto_erase)
          ? 'Identify Controller SANICAP reported sanitize support. Note that ' +
            'NVMe sanitize acts at controller scope: it destroys every ' +
            'namespace, not only the one named.'
          : 'SANICAP reported no sanitize support.',
    },
    {
      id: 'SED_CRYPTO_ERASE',
      label: 'SED cryptographic erase (Opal)',
      level: 'PURGE',
      // Never selectable, on any drive. `core/erase/drive.py` needs the PSID
      // printed on the drive's own label to issue a REVERT, and no PSID is
      // carried by the request model, the API or the helper - so an Opal drive
      // that reached this method raised instead of erasing. Offering it while
      // that is true would be a control that cannot do what it says.
      available: false,
      unsupported:
        'PSID required. The PSID is printed on the drive label and this ' +
        'build has no way to accept it, so the REVERT cannot be issued.',
      evidence: caps?.is_sed_opal
        ? 'sedutil-cli reported an Opal SSC, so the drive itself supports a ' +
          'cryptographic erase: the data encryption key would be replaced and ' +
          'the ciphertext on the media would become undecryptable. This build ' +
          'cannot issue it. Use ATA SANITIZE or NVMe SANITIZE if the drive ' +
          'reports one; otherwise a single-pass overwrite achieves Clear, not ' +
          'Purge, and the report will say so.'
        : 'No Opal self-encrypting drive was reported, and this build could ' +
          'not issue an Opal revert in any case.',
    },
    {
      id: 'ATA_SECURITY_ERASE_ENHANCED',
      label: 'ATA SECURITY ERASE (enhanced)',
      level: 'PURGE',
      // The engine decides whether this counts as Purge, because only it knows
      // whether the medium is flash. When it does not reach PURGE the drive's
      // support is shown as evidence but the option is not offered.
      available:
        Boolean(caps?.ata_enhanced_erase) &&
        !frozen &&
        purgeReachable,
      evidence: frozen
        ? 'The drive reports ATA security as frozen, so no SECURITY command can ' +
          'be issued. Power-cycle the drive or issue an S3 sleep/wake to clear it.'
        : !caps?.ata_enhanced_erase
          ? 'Enhanced erase was not reported.'
          : purgeReachable
            ? 'hdparm -I reported enhanced erase support and security is not ' +
              'frozen. It counts as Purge on magnetic media only; on flash it is ' +
              'a Clear (NIST SP 800-88r1 Table A-8), and the engine chooses a ' +
              'sanitize or cryptographic erase there instead.'
            : 'hdparm -I reported enhanced erase support, but the engine did not ' +
              'count it as Purge for this device: on flash it is a Clear only ' +
              '(NIST SP 800-88r1 Table A-8). See the limitations below.',
    },
    {
      id: 'SINGLE_PASS_OVERWRITE',
      label: 'Single-pass overwrite',
      level: 'CLEAR',
      available: true,
      evidence:
        'Always available: the host writes a pattern over every addressable ' +
        'LBA. It cannot reach blocks the flash translation layer has remapped, ' +
        'over-provisioned capacity, or anything behind an unopened HPA/DCO, so ' +
        'the result is a Clear and never a Purge.',
    },
    {
      id: 'DOD_5220_22_M_3PASS',
      label: 'DoD 5220.22-M — 3 pass',
      level: 'CLEAR',
      available: true,
      legacy: true,
      evidence:
        'LEGACY. NIST SP 800-88r2 states that multi-pass overwrite is not needed ' +
        'for clear, and calls the DoD 5220.22-M pass-count language obsolete. ' +
        'Offered only because operators are sometimes contractually required to ' +
        'name it. On flash media it is actively harmful: every extra pass burns ' +
        'program/erase cycles without reaching a single remapped block.',
    },
  ]
}

function autoSelect(options: MethodOption[]): MethodOption {
  // The same preference order core/device/capabilities.py uses, so the
  // preselection matches what the engine would choose on its own.
  return (
    options.find((item) => item.available && item.level === 'PURGE') ??
    options.find((item) => item.id === 'SINGLE_PASS_OVERWRITE')!
  )
}

export default function Sanitize({ selected }: { selected: DeviceRow | null }) {
  const options = useMemo(
    () => methodsFor(selected?.capabilities ?? null),
    [selected],
  )
  const auto = useMemo(() => autoSelect(options), [options])
  const [chosen, setChosen] = useState(auto.id)
  const [dryRun, setDryRun] = useState(true)
  const [typed, setTyped] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [status, setStatus] = useState<JobStatus | null>(null)
  const [error, setError] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)
  const detach = useRef<(() => void) | null>(null)

  useEffect(() => setChosen(auto.id), [auto.id])
  useEffect(() => () => detach.current?.(), [])

  const option = options.find((item) => item.id === chosen) ?? auto
  const device = selected?.device
  const hidden = selected?.hidden_areas

  async function start() {
    if (!device) return
    setError(null)
    try {
      const accepted = await api.eraseDrive({
        path: device.path,
        level: option.level,
        dry_run: dryRun,
        typed_serial: dryRun ? '' : typed,
      })
      setJobId(accepted.job_id)
      setProgress(null)
      setStatus(null)
      setConfirming(false)
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
      setConfirming(false)
    }
  }

  if (!device) {
    return (
      <>
        <div className="screen-head">
          <h1>Sanitize</h1>
          <p>Select a device on the Devices screen first.</p>
        </div>
        <div className="screen-body">
          <Notice tone="info">
            No device selected. A locked device — the system disk, or one with a
            mounted filesystem — cannot be selected at all.
          </Notice>
        </div>
      </>
    )
  }

  const running = Boolean(jobId) && !status
  const residualFactors =
    (status?.result?.residual_risk as { factors?: string[] } | undefined)
      ?.factors ?? []

  return (
    <>
      <div className="screen-head">
        <h1>Sanitize</h1>
        <p className="path">{device.path}</p>
        <Chip tone="muted">{device.model}</Chip>
        <span className="serial" style={{ color: 'var(--text-muted)' }}>
          {device.serial}
        </span>
      </div>

      <div className="screen-body">
        <ErrorNotice error={error} />

        {hidden && hidden.hidden_bytes > 0 && (
          <Notice tone="warn">
            <strong>{bytes(hidden.hidden_bytes)}</strong> are hidden behind an{' '}
            {hidden.hpa_present ? 'HPA' : 'DCO'} (
            {hidden.accessible_sectors.toLocaleString('en-US')} of{' '}
            {hidden.native_max_sectors.toLocaleString('en-US')} sectors are
            accessible). A host overwrite does not reach them unless the native
            max is unlocked first; a firmware sanitize covers the full media by
            design.
          </Notice>
        )}

        <div className="split">
          <div className="col">
            <Panel title="Method">
              <div className="col">
                {options.map((item) => (
                  <label
                    key={item.id}
                    className="inline"
                    style={{
                      alignItems: 'flex-start',
                      opacity: item.available ? 1 : 0.45,
                      cursor: item.available ? 'pointer' : 'not-allowed',
                    }}
                  >
                    <input
                      type="radio"
                      name="method"
                      value={item.id}
                      checked={chosen === item.id}
                      disabled={!item.available || running}
                      onChange={() => setChosen(item.id)}
                      style={{ marginTop: 3 }}
                    />
                    <span className="col tight">
                      <span className="row" style={{ gap: 'var(--space-2)' }}>
                        <strong style={{ color: 'var(--text-primary)' }}>
                          {item.label}
                        </strong>
                        {/* The level is state, so it is a word in a state
                            colour rather than a chip: the same mark the
                            Devices screen uses for hidden areas. */}
                        <span
                          className={`state-mark is-${levelTone(item.level)}`}
                        >
                          {item.level}
                        </span>
                        {item.unsupported && (
                          <Chip tone="medium" title={item.unsupported}>
                            NOT IN THIS BUILD
                          </Chip>
                        )}
                        {item.legacy && (
                          <Chip tone="high" title="Multi-pass overwrite is not needed for clear: NIST SP 800-88r2">
                            LEGACY
                          </Chip>
                        )}
                        {item.id === auto.id && (
                          <Chip tone="accent">auto-selected</Chip>
                        )}
                      </span>
                      {/* The evidence, not the conclusion. */}
                      <span className="note">{item.evidence}</span>
                      {/* Visible, not only in the chip's tooltip: a limitation
                          an operator has to hover to find is one they will
                          discover from the failure instead. */}
                      {item.unsupported && (
                        <span className="note">
                          <strong>Not supported in this build:</strong>{' '}
                          {item.unsupported}
                        </span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
            </Panel>

            <Panel title="Run">
              <div className="col">
                <label className="inline">
                  <input
                    type="checkbox"
                    checked={dryRun}
                    disabled={running}
                    onChange={(event) => setDryRun(event.target.checked)}
                  />
                  <span>
                    Dry run — plan and report only, nothing is written
                  </span>
                </label>

                {!dryRun && (
                  <Notice tone="danger">
                    This will <strong>permanently destroy</strong> every byte on{' '}
                    <span className="path">{device.path}</span> (
                    {bytes(device.size_bytes)}). There is no undo. The device
                    serial must be typed to confirm.
                  </Notice>
                )}

                <div className="row">
                  <button
                    className={dryRun ? 'btn primary' : 'btn destructive'}
                    disabled={running}
                    onClick={() => (dryRun ? void start() : setConfirming(true))}
                  >
                    {dryRun ? 'Run dry run' : 'Erase this device'}
                  </button>
                  {jobId && !status && (
                    <button
                      className="btn"
                      onClick={() => void api.cancel(jobId)}
                    >
                      Cancel
                    </button>
                  )}
                  {jobId && (
                    <span className="mono" style={{ color: 'var(--text-muted)' }}>
                      {jobId}
                    </span>
                  )}
                </div>
              </div>
            </Panel>

            {jobId && (
              <Panel title="Progress">
                <ProgressView progress={progress} destructive={!dryRun} />
                {status && (
                  <div style={{ marginTop: 'var(--space-3)' }}>
                    <Notice tone={status.state === 'complete' ? 'ok' : 'warn'}>
                      Job {status.state}
                      {status.error ? `: ${status.error}` : ''}
                    </Notice>
                    {status.remediation && (
                      <p className="note" style={{ marginTop: 'var(--space-2)' }}>
                        {status.remediation}
                      </p>
                    )}
                  </div>
                )}
              </Panel>
            )}
          </div>

          {/* Residual risk is shown during the run, not only after it. An
              operator deciding whether to let a wipe finish needs to know what
              it will not have covered while there is still a decision to make. */}
          <Panel
            title="Residual risk"
            subtitle="What this run can claim, and what it cannot."
          >
            <div className="col">
              {/* The claim first, at the size the room can read, with the
                  method it rests on printed under it. Everything below is the
                  qualification - and a qualification only means something once
                  the reader knows what is being qualified. */}
              <Railed tone={levelTone(option.level)}>
                <Verdict
                  level={option.level}
                  basis={option.label}
                  tone={levelTone(option.level)}
                />
              </Railed>

              <Evidence
                stacked
                rows={[
                  {
                    label: 'Capacity',
                    value: bytes(device.size_bytes),
                    title: exactBytes(device.size_bytes),
                  },
                  {
                    label: 'Estimated',
                    value: duration(selected?.capabilities?.est_erase_seconds ?? 0),
                  },
                  {
                    label: 'Hidden areas',
                    value:
                      hidden && hidden.hidden_bytes > 0
                        ? `${bytes(hidden.hidden_bytes)} behind ${hidden.hpa_present ? 'an HPA' : 'a DCO'}`
                        : 'none measured',
                  },
                ]}
              />

              {option.level === 'CLEAR' && !device.rotational && (
                <Notice tone="warn">
                  This is flash media and the selected method is a host
                  overwrite. Blocks the FTL has remapped, over-provisioned
                  capacity and anything still in the write cache are not
                  reachable by any write pattern. The result is a{' '}
                  <strong>Clear</strong>, not a Purge.
                </Notice>
              )}

              {hidden && hidden.hidden_bytes > 0 && option.level === 'CLEAR' && (
                <Notice tone="warn">
                  {bytes(hidden.hidden_bytes)} behind the HPA/DCO are only
                  covered if the unlock succeeds. If it fails, that region is
                  not erased and the report says so.
                </Notice>
              )}

              {(selected?.capabilities?.limitations ?? []).length > 0 && (
                <Limitations items={selected!.capabilities!.limitations} />
              )}

              {residualFactors.length > 0 && (
                <Limitations items={residualFactors} />
              )}
            </div>
          </Panel>
        </div>
      </div>

      {confirming && (
        <div className="modal-backdrop" onClick={() => setConfirming(false)}>
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-head" id="confirm-title">
              Confirm irreversible erasure
            </div>
            <div className="modal-body">
              {/* The dialog leads with the act and the target, at a size a
                  second person standing behind the operator can read. The
                  prose that used to carry this was a paragraph, and a
                  paragraph is what an operator skips. */}
              <p className="modal-lead">
                Destroy every byte on{' '}
                <span className="path">{device.path}</span>
              </p>

              {/* The level the method delivers, rendered exactly as the
                  Devices screen renders a capability and the residual panel
                  renders its claim. This is the last screen on which it can
                  still be wrong for free. */}
              <Railed tone={levelTone(option.level)}>
                <Verdict
                  level={option.level}
                  basis={option.label}
                  tone={levelTone(option.level)}
                />
              </Railed>

              <Evidence
                stacked
                rows={[
                  { label: 'Model', value: device.model },
                  { label: 'Capacity', value: exactBytes(device.size_bytes) },
                  { label: 'Serial', value: device.serial, kind: 'serial' },
                ]}
              />

              <label>
                Type the device serial to confirm
                <input
                  type="text"
                  value={typed}
                  autoFocus
                  spellCheck={false}
                  placeholder={device.serial}
                  onChange={(event) => setTyped(event.target.value)}
                />
              </label>

              {/* Match state is a word in a state colour, not a red sentence.
                  The sentence stays underneath, because what it says - that
                  the server checks the device itself - is the reason this gate
                  is not security theatre. */}
              {typed && (
                <div className="col tight">
                  <span
                    className={
                      typed === device.serial
                        ? 'state-mark is-success'
                        : 'state-mark is-destructive'
                    }
                  >
                    {typed === device.serial
                      ? 'serial matches'
                      : 'serial does not match'}
                  </span>
                  {typed !== device.serial && (
                    <span className="note">
                      The server re-reads the serial from the device itself and
                      will refuse regardless of what is typed here.
                    </span>
                  )}
                </div>
              )}
            </div>
            <div className="modal-foot">
              <button className="btn" onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button
                className="btn destructive"
                disabled={typed !== device.serial}
                onClick={() => void start()}
              >
                Erase {device.path}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
