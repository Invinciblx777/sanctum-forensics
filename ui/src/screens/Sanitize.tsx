import { useEffect, useRef, useState } from 'react'
import { api, RequestFailed, streamJob } from '../lib/api'
import type {
  DeviceAssessment,
  DeviceRow,
  EraseVerification,
  EraseWorkflowView,
  JobStatus,
  Level,
  Progress,
  ReportResult,
  ResumeState,
} from '../lib/api'
import { currentStep, runnableStatus } from '../lib/platform'
import { AssessmentSummary, FlowSteps, WorkflowStrip } from '../components/sanitizeFlow'
import { EraseApproval } from '../components/eraseApproval'
import { createEpoch } from '../lib/epoch'
import { refusalFrom, sanitizeWorkflow } from '../lib/workflowState'
import type { ServerRefusal } from '../lib/workflowState'
import { verificationWord } from '../lib/artifacts'
import { useCase } from '../lib/caseContext'
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
  Empty,
  ErrorNotice,
  Evidence,
  Limitations,
  Notice,
  Panel,
  ProgressView,
  Railed,
  SimulationBanner,
  Stat,
  Verdict,
} from '../components/widgets'
import type { Tone } from '../components/widgets'
import { isSimulation } from '../lib/simulation'

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

/**
 * The four outcomes a sanitization verification can have.
 *
 * **Uncertainty is never collapsed into PASS.** The engine reports
 * `passed: true`, `passed: false` or `passed: null`, and the third is not a
 * quiet version of the first: it means the check ran and settled nothing, or
 * could not run at all. A screen that rendered null as a tick would be the
 * interface making a claim the engine refused to make.
 *
 * NOT APPLICABLE is separated from INCONCLUSIVE by whether anything was read:
 * a verification that checked zero bytes did not attempt the measurement, and
 * one that checked bytes and could not conclude did.
 */
function verificationVerdict(
  verification: EraseVerification | null,
  dryRun: boolean,
): { word: string; tone: Tone; note: string } {
  const word = verificationWord(verification, dryRun)
  if (word === 'PASSED') {
    return {
      word,
      tone: 'success',
      note:
        verification?.probability_note || 'Every byte read back as expected.',
    }
  }
  if (word === 'FAILED') {
    const failures = verification?.failed_offsets.length ?? 0
    return {
      word,
      tone: 'destructive',
      note:
        `${failures} sampled offset${failures === 1 ? '' : 's'} did not read ` +
        'back as expected. The medium is not sanitized.',
    }
  }
  if (word === 'INCONCLUSIVE') {
    return {
      word,
      tone: 'warning',
      note:
        'The read-back ran and did not settle the question. This is not a ' +
        'pass: nothing here claims the medium was verified.',
    }
  }
  return {
    word,
    tone: 'unknown',
    note: dryRun
      ? 'This was a dry run. Nothing was written, so there was nothing to ' +
        'verify. A dry run never produces a verification result and never ' +
        'claims one.'
      : !verification
        ? 'No verification was recorded for this run.'
        : 'No read-back was attempted for this method. A firmware sanitize is ' +
          'attested by the drive, not measured by the host; what that attests ' +
          'to is in the residual-risk panel.',
  }
}

/**
 * What the run proved, after it finished.
 *
 * Sampled verification is reported as sampling, with its seed and its detection
 * probability, because "verified" over a sample of a four-terabyte disk and
 * "verified" over every byte of a 64 MB stick are different claims and the
 * engine already distinguishes them.
 */
function VerificationPanel({
  status,
  dryRun,
}: {
  status: JobStatus
  dryRun: boolean
}) {
  const verification =
    (status.result?.verification as EraseVerification | undefined) ?? null
  const verdict = verificationVerdict(verification, dryRun)
  const method =
    (status.result?.plan as { method?: string } | undefined)?.method ?? ''
  const warnings = (status.result?.limitations as string[] | undefined) ?? []

  return (
    <div className="col" data-testid="verification-panel">
      <Railed tone={verdict.tone}>
        <Verdict
          level={`VERIFICATION: ${verdict.word}`}
          basis={verification?.strategy || 'no strategy recorded'}
          tone={verdict.tone}
        />
        <span className="note">{verdict.note}</span>
      </Railed>

      <Evidence
        stacked
        rows={[
          { label: 'Operation', value: status.job_id, kind: 'mono' },
          { label: 'Method run', value: method || 'not recorded', kind: 'mono' },
          { label: 'Started', value: status.started_at },
          { label: 'Finished', value: status.finished_at ?? 'still running' },
          {
            label: 'Strategy',
            value: verification?.strategy || 'none recorded',
          },
          {
            label: 'Attested by the drive',
            value: verification?.hw_attested ? 'yes' : 'no',
          },
        ]}
      />

      {verification && verification.bytes_checked > 0 && (
        <div className="row wrap" style={{ gap: 'var(--space-6)' }}>
          <Stat label="bytes read back" value={bytes(verification.bytes_checked)} />
          <Stat label="samples" value={verification.sample_count} />
          <Stat
            label="detection probability"
            value={`${(verification.confidence_bp / 100).toFixed(2)}%`}
          />
          {verification.sample_seed !== null && (
            <Stat label="sample seed" value={verification.sample_seed} />
          )}
        </div>
      )}

      {verification && verification.probability_note && (
        <Notice tone="info">{verification.probability_note}</Notice>
      )}

      {verification && verification.failed_offsets.length > 0 && (
        <div className="col tight">
          <span className="stat-label">offsets that failed read-back</span>
          <pre className="log">
            {verification.failed_offsets
              .slice(0, 32)
              .map((offset) => `0x${offset.toString(16)}`)
              .join('\n')}
            {verification.failed_offsets.length > 32 &&
              `\n… and ${verification.failed_offsets.length - 32} more`}
          </pre>
        </div>
      )}

      {warnings.length > 0 && <Limitations items={warnings} />}
    </div>
  )
}

/**
 * Whether an interrupted erase can be continued, from the chain's own record.
 *
 * The refusal is the interesting half. An overwrite records a checkpoint every
 * interval and resumes from the recorded offset; a firmware sanitize is one
 * command the drive executes alone and reports no progress for, so there is no
 * offset to continue from. Offering a Resume button there would claim the
 * device told us where it stopped. The reason text is the server's, verbatim.
 */
function ResumePanel({
  state,
  serial,
  onResume,
}: {
  state: ResumeState
  serial: string
  onResume: (dryRun: boolean, typedSerial: string) => void
}) {
  const [typed, setTyped] = useState('')

  if (!state.resumable) {
    return (
      <Railed tone="unknown">
        <Verdict level="RESUME NOT AVAILABLE" basis={state.method || 'no plan recorded'} tone="unknown" />
        <span className="note">{state.reason}</span>
      </Railed>
    )
  }

  return (
    <div className="col">
      <Railed tone="warning">
        <Verdict
          level="RESUME AVAILABLE"
          basis={
            state.checkpoint
              ? `from byte ${state.checkpoint.offset.toLocaleString('en-US')}, pass ${state.checkpoint.pass_index}`
              : ''
          }
          tone="warning"
        />
        <span className="note">{state.reason}</span>
      </Railed>

      <div className="row wrap" style={{ alignItems: 'flex-end' }}>
        <label className="grow">
          Type the device serial to resume for real
          <input
            type="text"
            value={typed}
            spellCheck={false}
            placeholder={serial}
            onChange={(event) => setTyped(event.target.value)}
          />
        </label>
        <button className="btn" onClick={() => onResume(true, '')}>
          Resume (dry run)
        </button>
        <button
          className="btn destructive"
          disabled={typed !== serial || !serial}
          onClick={() => onResume(false, typed)}
        >
          Resume erasure
        </button>
      </div>
      <p className="note">
        A resume writes to the medium, so it keeps both gates of the run it
        continues. The server re-reads the serial from the device itself and
        refuses regardless of what is typed here.
      </p>
    </div>
  )
}

export default function Sanitize({ selected }: { selected: DeviceRow | null }) {
  const [level, setLevel] = useState<Level>(defaultLevel(selected))
  const [dryRun, setDryRun] = useState(true)
  const [typed, setTyped] = useState('')
  const [confirming, setConfirming] = useState(false)
  // The real-erase workflow. Everything here is the server's answer, held so it
  // can be drawn; none of it is a decision made by this screen.
  const [backupImage, setBackupImage] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [view, setView] = useState<EraseWorkflowView | null>(null)
  const [refusal, setRefusal] = useState<ServerRefusal | null>(null)
  const [approvalBusy, setApprovalBusy] = useState(false)
  // A request that failed for a reason that is not a safety refusal: the server
  // errored, the platform cannot do this. Shown as such, never as BLOCKED.
  const [requestFailure, setRequestFailure] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)
  const epoch = useRef(createEpoch()).current
  const [jobId, setJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [status, setStatus] = useState<JobStatus | null>(null)
  const [error, setError] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)
  const [resume, setResume] = useState<ResumeState | null>(null)
  // The assessment the device list carried, then replaced by a fresh one read
  // from the OS when this screen opens and again before the confirmation.
  const [assessment, setAssessment] = useState<DeviceAssessment | null>(
    selected?.assessment ?? null,
  )
  const [reviewing, setReviewing] = useState(false)
  const [report, setReport] = useState<ReportResult | null>(null)
  // Only when the server says the signing key needs one. Held in this
  // component for the one request and never stored anywhere.
  const [needsPassphrase, setNeedsPassphrase] = useState(false)
  const [passphrase, setPassphrase] = useState('')
  const { openCase } = useCase()
  const detach = useRef<(() => void) | null>(null)

  useEffect(() => setLevel(defaultLevel(selected)), [selected])

  function resetWorkflow() {
    epoch.bump()
    setApprovalBusy(false)
    setRequestFailure(null)
    setView(null)
    setRefusal(null)
    setAcknowledged(false)
    setTyped('')
    setConfirming(false)
  }
  // A record belongs to one device, level and mode. Change any and it is gone.
  useEffect(resetWorkflow, [selected, level, dryRun])

  // Step 2, "Analyse": re-read the device now rather than trusting the row the
  // device list loaded. A failure leaves the list's assessment in place.
  useEffect(() => {
    setAssessment(selected?.assessment ?? null)
    setReviewing(false)
    setReport(null)
    const id = selected?.normalized?.id
    if (!id) return
    // A slow answer for a device the operator has since left is dropped.
    let stale = false
    void api
      .assessment(id)
      .then((answer) => {
        if (!stale) setAssessment(answer.assessment)
      })
      .catch(() => undefined)
    return () => {
      stale = true
    }
  }, [selected])
  useEffect(() => () => detach.current?.(), [])

  // Resumability is read from the chain once the job reaches a terminal state:
  // a cancelled or failed overwrite is exactly the case resume exists for, and
  // it is also the case where the operator most needs to be told plainly that
  // a firmware sanitize cannot be continued.
  useEffect(() => {
    if (!jobId || !status || status.state === 'running') return
    void api
      .resumeState(jobId)
      .then(setResume)
      .catch(() => setResume(null))
  }, [jobId, status?.state])

  async function startResume(resumeDryRun: boolean, typedSerial: string) {
    if (!jobId) return
    setError(null)
    try {
      const accepted = await api.resume(jobId, {
        dry_run: resumeDryRun,
        typed_serial: typedSerial,
      })
      setJobId(accepted.job_id)
      setProgress(null)
      setStatus(null)
      setResume(null)
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

  const device = selected?.device
  const hidden = selected?.hidden_areas
  const plan = planFor(selected, level)
  const purge = planFor(selected, 'PURGE')
  const preview = selected?.erase_preview ?? null
  const media = flashOf(selected)
  const canRun = runnable(plan)

  /**
   * Opens the confirmation only after the device has been re-read from the OS
   * and is still available. The helper re-checks identity again when the job
   * starts; this is the check the operator sees.
   */
  async function openConfirmation() {
    const id = selected?.normalized?.id
    if (!id) {
      resetWorkflow()
      setConfirming(true)
      return
    }
    try {
      const fresh = await api.assessment(id)
      setAssessment(fresh.assessment)
      if (fresh.normalized.serial !== (selected?.normalized?.serial ?? '')) {
        setError({
          message:
            'The device identity changed since the list was loaded. Rescan the devices before erasing.',
          kind: 'ConfirmationMismatch',
        })
        return
      }
      if (fresh.assessment.headline !== 'READY') {
        setError({
          message: `Sanitization is ${fresh.assessment.headline.toLowerCase()}: ${fresh.assessment.reason}`,
          kind: 'Refused',
          remediation: fresh.assessment.recommended_action,
        })
        return
      }
      resetWorkflow()
      setConfirming(true)
    } catch (exc) {
      const failure = exc as RequestFailed
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    }
  }

  async function certificate() {
    if (!jobId) return
    try {
      setReport(
        await api.generateReport(jobId, {
          case_id: openCase?.case_id ?? '',
          operator: '',
          key_passphrase: passphrase || undefined,
        }),
      )
      setPassphrase('')
      setNeedsPassphrase(false)
      setError(null)
    } catch (exc) {
      const failure = exc as RequestFailed
      if (failure.kind === 'KeyPassphraseMissing') setNeedsPassphrase(true)
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    }
  }

  async function openWorkflow() {
    if (!device || approvalBusy) return
    const token = epoch.current()
    setApprovalBusy(true)
    setRefusal(null)
    setRequestFailure(null)
    try {
      const opened = await api.openEraseWorkflow({
        path: device.path,
        level,
        backup_image: backupImage.trim(),
      })
      if (epoch.isCurrent(token)) setView(opened)
    } catch (exc) {
      if (epoch.isCurrent(token)) reportFailure(exc as RequestFailed, true)
    } finally {
      if (epoch.isCurrent(token)) setApprovalBusy(false)
    }
  }

  /**
   * A 4xx from the workflow is the server refusing on a safety or precondition
   * ground: BLOCKED, with its reasons. Anything else (a 5xx, an unreachable
   * platform) is a failure of the request itself and is not dressed as a
   * refusal, because "the erase was refused" and "the server broke" call for
   * different operator responses.
   */
  function reportFailure(exc: RequestFailed, noWritePath: boolean) {
    if (exc.status >= 400 && exc.status < 500 && exc.kind) {
      setRefusal(refusalFrom(exc, noWritePath))
    } else {
      setRequestFailure({ message: exc.message, kind: exc.kind, remediation: exc.remediation })
    }
  }

  async function approve() {
    if (!view || approvalBusy) return
    const token = epoch.current()
    setApprovalBusy(true)
    setRefusal(null)
    setRequestFailure(null)
    try {
      const approved = await api.approveErase(view.authorization_id, {
        typed_serial: typed,
        acknowledge_data_destruction: acknowledged,
      })
      if (epoch.isCurrent(token)) {
        setView(approved)
        // The serial is typed again to execute: one entry is not two decisions.
        setTyped('')
      }
    } catch (exc) {
      if (epoch.isCurrent(token)) reportFailure(exc as RequestFailed, true)
    } finally {
      if (epoch.isCurrent(token)) setApprovalBusy(false)
    }
  }

  /**
   * Starts the job. A real erase carries the id the server issued and nothing
   * else can stand in for it; a dry run carries none and never asks for one.
   */
  async function start(authorizationId = '') {
    if (!device || !canRun) return
    if (!dryRun && !authorizationId) return
    if (approvalBusy) return
    const token = epoch.current()
    setApprovalBusy(true)
    setError(null)
    setRefusal(null)
    setRequestFailure(null)
    try {
      const accepted = await api.eraseDrive({
        ...eraseBody(device.path, level, dryRun, typed, authorizationId),
        // Filed against the open case, so the wipe appears on the case screen
        // and its certificate inherits the case id.
        case_id: openCase?.case_id ?? '',
      })
      // The job exists now whatever happened to the dialog meanwhile: the id
      // is the only handle on it, so it is always kept.
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
      if (!epoch.isCurrent(token)) return
      const failed = exc as RequestFailed
      if (!dryRun && failed.status === 409) {
        // A refusal is the server's answer, drawn as such: BLOCKED, why, and
        // whether the device was touched. Never "something went wrong".
        setRefusal(refusalFrom(failed))
        return
      }
      if (!dryRun && failed.status >= 500) {
        // The server broke while a real erase was being requested. Whether the
        // device was touched is not something this response can say.
        setRequestFailure({
          message: failed.message,
          kind: failed.kind,
          remediation:
            failed.remediation +
            ' The device state is unknown until checked: nothing here confirms it was left untouched.',
        })
        return
      }
      setError({
        message: failed.message,
        kind: failed.kind,
        remediation: failed.remediation,
      })
      setConfirming(false)
    } finally {
      if (epoch.isCurrent(token)) setApprovalBusy(false)
    }
  }

  if (!device) {
    return (
      <>
        <div className="screen-head">
          <h1>Secure sanitization</h1>
          <p>Choose a device on the Devices screen to begin.</p>
        </div>
        <div className="screen-body">
          <FlowSteps current={0} />
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
  const normalized = selected?.normalized ?? null
  // A platform with no whole-drive engine sends an assessment and nothing to
  // run; a Linux row sends both, and both must agree before Erase is offered.
  const offered =
    !assessment ||
    (assessment.headline !== 'NOT AVAILABLE' &&
      runnableStatus(assessment.recommended?.status))
  // `settled`, not just terminal: the job's outcome has reached the chain, so
  // the certificate can be built from it.
  const finished = Boolean(
    status && status.state !== 'running' && status.settled !== false,
  )
  const verificationResult =
    (status?.result?.verification as EraseVerification | undefined) ?? null
  const step = currentStep({
    hasDevice: true,
    hasAssessment: Boolean(assessment) || Boolean(preview),
    reviewing,
    confirming,
    running,
    finished,
    verified: Boolean(verificationResult) || dryRun,
    certified: Boolean(report),
  })
  const flow = sanitizeWorkflow({
    assessment,
    offered,
    canRun,
    planRefusal: plan?.refusal ?? '',
    dryRun,
    confirming,
    running,
    phase: progress?.phase ?? null,
    status: finished ? status : null,
    server: view?.workflow ?? null,
    refusal,
  })

  return (
    <>
      <div className="screen-head">
        <h1>Secure sanitization</h1>
        <p className="path">{device.path}</p>
        <Chip tone="muted">{device.model}</Chip>
        <span className="serial" style={{ color: 'var(--text-muted)' }}>
          {device.serial}
        </span>
      </div>

      <div className="screen-body">
        <FlowSteps current={step} />
        {(jobId ? flow.simulation : dryRun) && <SimulationBanner />}
        <WorkflowStrip flow={flow} />
        <ErrorNotice error={error} />

        {normalized && assessment && (
          <AssessmentSummary device={normalized} assessment={assessment} />
        )}

        {offered && canRun && !reviewing && !jobId && (
          <div className="row">
            <button className="btn primary" onClick={() => setReviewing(true)}>
              Review plan
            </button>
            <span className="note">
              Nothing is written until you confirm, and a dry run comes first.
            </span>
          </div>
        )}

        {jobId && finished && (
          <Panel title="Certificate">
            {report ? (
              <div className="col tight">
                <span className="state-mark is-success">Certificate issued</span>
                <span className="note">
                  Signed report {report.json_name}. Open it on the Audit screen to
                  verify the signature and the chain.
                </span>
                <div className="row">
                  <a className="btn" href={report.pdf_url} target="_blank" rel="noreferrer">
                    Open PDF
                  </a>
                  <a className="btn" href={report.json_url} target="_blank" rel="noreferrer">
                    Open signed JSON
                  </a>
                </div>
              </div>
            ) : (
              <div className="row wrap">
                {needsPassphrase && (
                  <label className="grow">
                    Signing-key passphrase (a new key needs 12 or more characters)
                    <input
                      type="password"
                      value={passphrase}
                      autoComplete="off"
                      onChange={(event) => setPassphrase(event.target.value)}
                    />
                  </label>
                )}
                <button className="btn primary" onClick={() => void certificate()}>
                  Get certificate
                </button>
                <span className="note">
                  The certificate records what ran, how it was verified, and what
                  it could not claim{dryRun ? ' - for a dry run, that nothing was written' : ''}.
                </span>
              </div>
            )}
          </Panel>
        )}

        <details className="tech" open={(!normalized || reviewing || Boolean(jobId)) && offered}>
          <summary>Technical details</summary>
          <div className="tech-body">

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

        <Panel
          title="Target"
          subtitle="Re-read from the host by the privileged helper, not remembered by this page."
        >
          <div className="row wrap" style={{ gap: 'var(--space-6)' }}>
            <Stat label="path" value={device.path} />
            <Stat label="model" value={device.model} />
            <Stat label="serial" value={device.serial || 'none reported'} />
            <Stat label="capacity" value={bytes(device.size_bytes)} />
            <Stat label="transport" value={device.transport} />
            <Stat
              label="partitioning"
              value={device.pt_type ?? 'none detected'}
            />
            <Stat
              label="mount state"
              value={
                device.mounted_at.length > 0
                  ? device.mounted_at.join(', ')
                  : 'not mounted'
              }
            />
            <Stat
              label="medium"
              value={
                media.flash === null
                  ? 'not determined'
                  : media.flash
                    ? 'flash'
                    : 'not flash'
              }
            />
          </div>
          <p className="note" style={{ marginTop: 'var(--space-2)' }}>
            {media.reason}
          </p>
        </Panel>

        <Panel
          title="Capability probe"
          subtitle="What the drive itself reported. The method is selected from this and from nothing the operator typed."
        >
          <div className="col">
            <Evidence
              stacked
              rows={[
                {
                  label: 'Levels the drive can reach',
                  value:
                    (selected?.capabilities?.achievable_levels ?? []).join(
                      ', ',
                    ) || 'none established',
                },
                {
                  label: 'Purge pathways reported',
                  value:
                    (preview?.purge_mechanisms ?? []).map(methodLabel).join(', ') ||
                    'none',
                },
                {
                  label: 'ATA security erase',
                  value: selected?.capabilities?.ata_security_erase
                    ? 'supported'
                    : 'not reported',
                },
                {
                  label: 'ATA sanitize operations',
                  value:
                    (selected?.capabilities?.ata_sanitize_ops ?? []).join(', ') ||
                    'none reported',
                },
                {
                  label: 'Security frozen',
                  value: selected?.capabilities?.security_frozen
                    ? 'yes — a frozen drive cannot start a security erase'
                    : 'no',
                },
                {
                  label: 'Self-encrypting (Opal)',
                  value: selected?.capabilities?.is_sed_opal ? 'yes' : 'no',
                },
                {
                  label: 'Hidden areas',
                  value:
                    hidden && hidden.hidden_bytes > 0
                      ? `${bytes(hidden.hidden_bytes)} behind ${hidden.hpa_present ? 'an HPA' : 'a DCO'} (${hidden.accessible_sectors.toLocaleString('en-US')} of ${hidden.native_max_sectors.toLocaleString('en-US')} sectors accessible)`
                      : hidden
                        ? 'none measured'
                        : 'not probed',
                },
              ]}
            />
            {(selected?.capabilities?.limitations ?? []).length > 0 && (
              <Limitations items={selected?.capabilities?.limitations ?? []} />
            )}
            {selected?.capability_error && (
              <Notice tone="warn">
                The capability probe failed: {selected.capability_error} Nothing
                is predicted from a probe that did not complete.
              </Notice>
            )}
          </div>
        </Panel>

        {!canRun && (
          <Panel title="Sanitization plan">
            <Railed tone="destructive">
              <Verdict
                level="SANITIZATION NOT AUTHORIZED"
                basis={`${level} on ${device.path}`}
                tone="destructive"
              />
              <span className="note">
                {plan?.refusal ||
                  'This level is not reachable on this device from what the capability probe established.'}
              </span>
              {plan?.remediation && (
                <span className="note-faint">{plan.remediation}</span>
              )}
            </Railed>
            <p className="note" style={{ marginTop: 'var(--space-2)' }}>
              <strong>No downgrade was performed.</strong> The engine does not
              substitute a weaker method for one the device cannot reach: a
              Clear issued in place of a refused Purge would produce a
              certificate naming a guarantee that was never made.
            </p>
          </Panel>
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
                    disabled={running || !canRun || (!dryRun && !offered)}
                    onClick={() => (dryRun ? void start() : void openConfirmation())}
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

            {jobId && status && (
              <Panel
                title="Verification"
                subtitle="Four outcomes. Uncertainty is never rendered as a pass."
              >
                <VerificationPanel status={status} dryRun={dryRun} />
              </Panel>
            )}

            {jobId && status && (
              <Panel title="Resume">
                {resume ? (
                  <ResumePanel
                    state={resume}
                    serial={device.serial}
                    onResume={(resumeDry, typedSerial) =>
                      void startResume(resumeDry, typedSerial)
                    }
                  />
                ) : (
                  <Empty>Reading the chain for a checkpoint&hellip;</Empty>
                )}
              </Panel>
            )}

            {jobId && isSimulation(status) && <SimulationBanner />}
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
        </details>
      </div>

      {confirming && !dryRun && plan?.method && (
        <EraseApproval
          device={device}
          level={level}
          method={plan.method}
          view={view}
          refusal={refusal}
          failure={requestFailure}
          backupImage={backupImage}
          acknowledged={acknowledged}
          typed={typed}
          busy={approvalBusy}
          onBackupImage={setBackupImage}
          onAcknowledge={setAcknowledged}
          onTyped={setTyped}
          onOpen={() => void openWorkflow()}
          onApprove={() => void approve()}
          onExecute={() => void start(view?.authorization_id ?? '')}
          onCancel={resetWorkflow}
        />
      )}
    </>
  )
}
