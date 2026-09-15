import { useEffect, useRef, useState } from 'react'
import { api, RequestFailed, streamJob } from '../lib/api'
import type { DeviceRow, JobStatus, Level, Progress } from '../lib/api'
import {
  contradiction,
  defaultLevel,
  eraseBody,
  flashOf,
  methodLabel,
  planFor,
  runnable,
} from '../lib/erasePlan'
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
function levelTone(level: Level): Tone {
  return level === 'PURGE' ? 'success' : 'warning'
}

/*
 * There is no method chooser on this screen, on purpose.
 *
 * The engine selects the mechanism from probed capability
 * (core/erase/drive.py:select_method), and the request carries a level only.
 * A radio group naming methods let an operator pick DoD 5220.22-M, confirm
 * "DoD", and receive a single-pass certificate (audit F6). What is shown here
 * instead is the engine's own answer for this device - computed by
 * core/erase/drive.py:preview from the same selection call the job makes - so
 * the operator commits to the method that will run, and the evidence for it.
 */

export default function Sanitize({ selected }: { selected: DeviceRow | null }) {
  const [level, setLevel] = useState<Level>(defaultLevel(selected))
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

  useEffect(() => setLevel(defaultLevel(selected)), [selected])
  useEffect(() => () => detach.current?.(), [])

  const device = selected?.device
  const hidden = selected?.hidden_areas
  const plan = planFor(selected, level)
  const purge = planFor(selected, 'PURGE')
  const preview = selected?.erase_preview ?? null
  const media = flashOf(selected)
  const canRun = runnable(plan)

  async function start() {
    if (!device || !canRun) return
    setError(null)
    try {
      const accepted = await api.eraseDrive(
        eraseBody(device.path, level, dryRun, typed),
      )
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
  const mismatch = contradiction(plan, status?.result)
  const basis = plan?.method ? methodLabel(plan.method) : 'not reachable'

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
            <Panel
              title="Level"
              subtitle="You choose the level. The engine chooses the method from what the device reported."
            >
              <div className="col">
                {(['PURGE', 'CLEAR'] as const).map((item) => {
                  const itemPlan = planFor(selected, item)
                  const available = runnable(itemPlan)
                  return (
                    <label
                      key={item}
                      className="inline"
                      data-level={item}
                      style={{
                        alignItems: 'flex-start',
                        opacity: available ? 1 : 0.45,
                        cursor: available ? 'pointer' : 'not-allowed',
                      }}
                    >
                      <input
                        type="radio"
                        name="level"
                        value={item}
                        checked={level === item}
                        disabled={!available || running}
                        onChange={() => setLevel(item)}
                        style={{ marginTop: 3 }}
                      />
                      <span className="col tight">
                        <span className={`state-mark is-${levelTone(item)}`}>
                          {item}
                        </span>
                        <span className="note">
                          {itemPlan?.method
                            ? `Engine would run: ${methodLabel(itemPlan.method)}`
                            : itemPlan
                              ? 'Not reachable on this device.'
                              : 'No plan was received for this level.'}
                        </span>
                      </span>
                    </label>
                  )
                })}
              </div>
            </Panel>

            <Panel
              title="What the engine will run"
              subtitle="Computed from the capability probe by the same selection the job makes."
            >
              {!preview ? (
                <Notice tone="warn">
                  No erase plan was received for this device
                  {selected?.capability_error
                    ? `: the capability probe failed (${selected.capability_error})`
                    : ''}
                  . Nothing is predicted, and no erase can be started from this
                  screen until the device is rescanned.
                </Notice>
              ) : plan && plan.reachable && plan.method ? (
                <div className="col" data-testid="engine-plan">
                  <Evidence
                    stacked
                    rows={[
                      { label: 'Level', value: plan.level },
                      {
                        label: 'Method',
                        value: `${methodLabel(plan.method)} (${plan.method})`,
                      },
                      { label: 'Why', value: plan.justification },
                      {
                        label: 'Medium',
                        value: `${preview.flash ? 'Flash' : 'Not flash'}: ${preview.flash_reason}`,
                      },
                    ]}
                  />
                  <div className="col tight">
                    <strong>Probed evidence</strong>
                    <ul className="limitations">
                      {plan.evidence.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  </div>
                  {preview.purge_mechanisms.length > 1 && level === 'PURGE' && (
                    <p className="note">
                      Other Purge mechanisms the device reported, in the
                      engine&apos;s order:{' '}
                      {preview.purge_mechanisms.slice(1).map(methodLabel).join(', ')}
                      . The first is the one that runs.
                    </p>
                  )}
                  {!plan.executable && (
                    <Notice tone="danger">
                      <strong>This build cannot issue this method.</strong>{' '}
                      {plan.not_executable_reason}
                    </Notice>
                  )}
                </div>
              ) : (
                <Notice tone="warn">
                  {plan?.refusal || 'This level is not reachable on this device.'}{' '}
                  {plan?.remediation}
                </Notice>
              )}

              {preview && purge && !purge.reachable && (
                <div className="col tight" style={{ marginTop: 'var(--space-3)' }}>
                  <strong>For Purge this device would need</strong>
                  <span className="note">{preview.purge_requires}</span>
                </div>
              )}
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
                    disabled={running || !canRun}
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
                    {mismatch && (
                      <div style={{ marginTop: 'var(--space-2)' }}>
                        <Notice tone="danger">{mismatch}</Notice>
                      </div>
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
              <Railed tone={levelTone(level)}>
                <Verdict level={level} basis={basis} tone={levelTone(level)} />
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

              {/* The engine's flash determination, not `!rotational`: a USB
                  bridge leaves that flag set on a flash stick (audit F5). */}
              {level === 'CLEAR' && media.flash === true && (
                <Notice tone="warn">
                  This is flash media ({media.reason}) and Clear is a host
                  overwrite. Blocks the FTL has remapped, over-provisioned
                  capacity and anything still in the write cache are not
                  reachable by any write pattern. The result is a{' '}
                  <strong>Clear</strong>, not a Purge.
                </Notice>
              )}
              {level === 'CLEAR' && media.flash === null && (
                <Notice tone="warn">
                  Whether this device is flash was not determined. If it is, a
                  host overwrite leaves remapped and over-provisioned blocks
                  untouched.
                </Notice>
              )}

              {hidden && hidden.hidden_bytes > 0 && level === 'CLEAR' && (
                <Notice tone="warn">
                  {bytes(hidden.hidden_bytes)} behind the HPA/DCO are only
                  covered if the unlock succeeds. If it fails, that region is
                  not erased and the report says so.
                </Notice>
              )}

              {(plan?.limitations ?? selected?.capabilities?.limitations ?? [])
                .length > 0 && (
                <Limitations
                  items={
                    plan?.limitations ?? selected?.capabilities?.limitations ?? []
                  }
                />
              )}

              {residualFactors.length > 0 && (
                <Limitations items={residualFactors} />
              )}
            </div>
          </Panel>
        </div>
      </div>

      {confirming && plan?.method && (
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
              <p className="modal-lead">
                Destroy every byte on{' '}
                <span className="path">{device.path}</span>
              </p>

              {/* The method named here is the engine's plan, the same value
                  the certificate will record. It used to be the radio the
                  operator clicked, which the request never carried. */}
              <Railed tone={levelTone(level)}>
                <Verdict level={level} basis={basis} tone={levelTone(level)} />
              </Railed>

              <Evidence
                stacked
                rows={[
                  { label: 'Method', value: plan.method, kind: 'mono' },
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
