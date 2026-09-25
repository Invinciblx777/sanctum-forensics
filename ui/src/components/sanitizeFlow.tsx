import type { DeviceAssessment, NormalizedDevice, SanitizeOption } from '../lib/api'
import { bytes } from '../lib/format'
import {
  checkWord,
  deviceKind,
  deviceName,
  headlineTone,
  STEPS,
  statusWord,
} from '../lib/platform'
import { BACKUP_NOTE } from '../lib/workflowState'
import type { SanitizeWorkflow } from '../lib/workflowState'
import { Limitations, Notice, Panel, Railed } from './widgets'

/** The eight-step tracker. The current step is computed, never clicked. */
export function FlowSteps({
  current,
  stopped = false,
}: {
  current: number
  /** The flow stopped on the current step: refused, blocked or failed there. */
  stopped?: boolean
}) {
  return (
    <ol className="flow-steps" aria-label="Sanitization steps">
      {STEPS.map((label, index) => (
        <li
          key={label}
          className={
            index === current
              ? stopped
                ? 'is-current is-stopped'
                : 'is-current'
              : index < current
                ? 'is-done'
                : ''
          }
          aria-current={index === current ? 'step' : undefined}
        >
          {label}
          {index === current && stopped && <span className="step-stopped">stopped</span>}
        </li>
      ))}
    </ol>
  )
}

function Option({ option }: { option: SanitizeOption }) {
  const { word, tone } = statusWord(option.status)
  return (
    <Railed tone={tone}>
      <span className="row spread">
        <span className="option-title">{option.title}</span>
        <span className={`state-mark is-${tone}`}>{word}</span>
      </span>
      <span className="note">{option.why}</span>
      {option.remediation && <span className="note-faint">{option.remediation}</span>}
    </Railed>
  )
}

/**
 * The three questions, answered before anything else on the screen.
 *
 * 1. What device am I about to operate on?
 * 2. What will happen?
 * 3. Can the application verify it?
 *
 * Everything shown is the server's assessment of the device as re-read from
 * the OS; this component decides nothing.
 */
export function AssessmentSummary({
  device,
  assessment,
  superseded = false,
}: {
  device: NormalizedDevice
  assessment: DeviceAssessment
  /**
   * A later answer - a refusal, a block, a failed job - has replaced this
   * preflight. The panel then says it is the earlier answer, so a READY from
   * before the refusal never sits beside the BLOCKED that replaced it.
   */
  superseded?: boolean
}) {
  const tone = superseded ? 'unknown' : headlineTone(assessment)
  const recommended = assessment.recommended
  const available = assessment.headline === 'READY' || assessment.headline === 'NOT AUTHORIZED'
  const verifyTone = recommended?.verification ? 'success' : 'unknown'
  const title = superseded
    ? 'Preflight, before the erase was stopped'
    : available
      ? 'Ready to sanitize'
      : 'Sanitization not available'

  return (
    <Panel title={title}>
      <div className="col" style={{ gap: 'var(--space-4)' }}>
        <div className="headline-block">
          {superseded && (
            <p className="note" data-testid="assessment-superseded">
              The preflight said {assessment.headline}. The erase was stopped after
              it: the state above is the current answer.
            </p>
          )}
          <p className={`headline-word is-${tone}`} data-testid="assessment-headline">
            {assessment.headline}
          </p>
          <p className="answer-detail">{assessment.reason}</p>
          {assessment.recommended_action && (
            <p className="answer-detail">
              <strong>What to do instead: </strong>
              {assessment.recommended_action}
            </p>
          )}
          {!available && (
            <p className="note-faint">No operation was performed on this device.</p>
          )}
        </div>

        <div className="answers">
          <Railed tone={device.system_device || device.mounted ? 'destructive' : 'unknown'}>
            <p className="answer-q">Device</p>
            <p className="answer-a">{deviceName(device)}</p>
            <p className="answer-detail">
              {bytes(device.capacity_bytes)}, {deviceKind(device)}
            </p>
            <p className="answer-detail">
              Serial {device.serial || 'not reported'}
            </p>
          </Railed>
          <Railed tone={recommended ? statusWord(recommended.status).tone : 'destructive'}>
            <p className="answer-q">What will happen</p>
            <p className="answer-a">
              {recommended ? recommended.title : 'Nothing: no method is available'}
            </p>
            <p className="answer-detail">
              {recommended ? recommended.why : 'The device is left untouched.'}
            </p>
            {recommended && (
              <p className="answer-detail">All data on the device will be destroyed.</p>
            )}
          </Railed>
          <Railed tone={verifyTone}>
            <p className="answer-q">Can it be verified?</p>
            <p className="answer-a">
              {recommended?.verification ? 'Yes' : 'No verification'}
            </p>
            <p className="answer-detail">
              {recommended?.verification || assessment.verification}
            </p>
          </Railed>
        </div>

        <ul className="checks" aria-label="Safety checks">
          {assessment.safety_checks.map((check) => {
            const mark = checkWord(check)
            return (
              <li key={check.key}>
                <span className={`state-mark is-${mark.tone}`}>{mark.word}</span>
                <span>{check.label}</span>
                <span className="check-detail">{check.detail}</span>
              </li>
            )
          })}
        </ul>

        {assessment.flash_limitation && (
          <Notice tone="warn">{assessment.flash_limitation}</Notice>
        )}

        {(assessment.alternatives.length > 0 || assessment.unavailable.length > 0) && (
          <div className="options">
            {assessment.alternatives.length > 0 && <strong>Alternative</strong>}
            {assessment.alternatives.map((option) => (
              <Option key={`alt-${option.level}`} option={option} />
            ))}
            {assessment.unavailable.length > 0 && <strong>Unavailable, and why</strong>}
            {assessment.unavailable.map((option) => (
              <Option key={`na-${option.level}`} option={option} />
            ))}
          </div>
        )}

        {device.limitations.length > 0 && <Limitations items={device.limitations} />}
      </div>
    </Panel>
  )
}

/**
 * Where the job is in the destructive-workflow state machine, and why it
 * cannot move on. The state is derived by `lib/workflowState.ts`; this only
 * draws it. BLOCKED and FAILED list their reasons under WHY BLOCKED.
 */
export function WorkflowStrip({ flow }: { flow: SanitizeWorkflow }) {
  const stopped = flow.state === 'BLOCKED' || flow.state === 'FAILED'
  const index = flow.path.indexOf(flow.state)
  return (
    <section className="workflow-state" data-testid="workflow-state" aria-label="Workflow state">
      <ol className="workflow-path">
        {flow.path.map((state, at) => (
          <li
            key={state}
            className={
              state === flow.state
                ? `is-current${stopped ? ' is-stopped' : ''}`
                : at < index
                  ? 'is-done'
                  : ''
            }
            aria-current={state === flow.state ? 'step' : undefined}
          >
            {state.replace(/_/g, ' ')}
          </li>
        ))}
      </ol>
      <p className={`workflow-headline${stopped ? ' is-stopped' : ''}`}>{flow.headline}</p>
      {flow.whyBlocked.length > 0 && (
        <div className="why-blocked">
          <strong>{flow.state === 'FAILED' ? 'WHY IT FAILED' : 'WHY BLOCKED'}</strong>
          <ul>
            {flow.whyBlocked.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="note">{flow.nextAction}</p>
      <p className="note-faint">{BACKUP_NOTE}</p>
    </section>
  )
}
