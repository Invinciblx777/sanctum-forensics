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
import { Limitations, Notice, Panel, Railed } from './widgets'

/** The eight-step tracker. The current step is computed, never clicked. */
export function FlowSteps({ current }: { current: number }) {
  return (
    <ol className="flow-steps" aria-label="Sanitization steps">
      {STEPS.map((label, index) => (
        <li
          key={label}
          className={
            index === current ? 'is-current' : index < current ? 'is-done' : ''
          }
          aria-current={index === current ? 'step' : undefined}
        >
          {label}
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
}: {
  device: NormalizedDevice
  assessment: DeviceAssessment
}) {
  const tone = headlineTone(assessment)
  const recommended = assessment.recommended
  const available = assessment.headline === 'READY' || assessment.headline === 'NOT AUTHORIZED'
  const verifyTone = recommended?.verification ? 'success' : 'unknown'

  return (
    <Panel title={available ? 'Ready to sanitize' : 'Sanitization not available'}>
      <div className="col" style={{ gap: 'var(--space-4)' }}>
        <div className="headline-block">
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
