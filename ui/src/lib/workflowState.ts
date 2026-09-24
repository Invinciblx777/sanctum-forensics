/**
 * The Sanitize screen's position in the destructive-workflow state machine.
 *
 * The names are the ones `core/workflow.py` defines, so the screen, the plan
 * JSON and the documentation use one word for one state. This function derives
 * the state from what the screen already holds - the server's assessment, the
 * engine's plan, the confirmation dialog and the job's own status - and never
 * from a guess. It decides nothing: the server re-checks every gate when a job
 * starts, whatever this returns.
 *
 * Two rules keep it honest:
 *
 * - EXECUTING and later are only derived from a job that exists. A dialog that
 *   is open, or a button that is enabled, is never shown as execution.
 * - A dry run is labelled a simulation at every state it reaches, including
 *   COMPLETE, because a completed dry run wrote nothing.
 *
 * BACKUP_REQUIRED and BACKUP_VERIFIED are not on this path. Whole-drive
 * sanitization destroys the data by intent, so the drive engine has no backup
 * gate; the backup states belong to the physical benchmark write in
 * `scripts/media_benchmark.py`, which enforces them. The screen says so rather
 * than drawing a gate that nothing enforces.
 */
import type { DeviceAssessment, JobStatus } from './api'

export type WorkflowStateName =
  | 'DISCOVERED'
  | 'PREFLIGHT'
  | 'BLOCKED'
  | 'BACKUP_REQUIRED'
  | 'BACKUP_VERIFIED'
  | 'PLAN_READY'
  | 'HUMAN_APPROVAL_REQUIRED'
  | 'EXECUTING'
  | 'VERIFYING'
  | 'COMPLETE'
  | 'FAILED'

/** The happy path of a whole-drive sanitization, in order. */
export const SANITIZE_PATH: readonly WorkflowStateName[] = [
  'DISCOVERED',
  'PREFLIGHT',
  'HUMAN_APPROVAL_REQUIRED',
  'EXECUTING',
  'VERIFYING',
  'COMPLETE',
]

export const BACKUP_NOTE =
  'No backup gate: whole-drive sanitization destroys the data by intent. ' +
  'BACKUP_REQUIRED and BACKUP_VERIFIED are enforced by the physical benchmark ' +
  'write (scripts/media_benchmark.py), not by this screen.'

export interface SanitizeFacts {
  assessment: DeviceAssessment | null
  /** The assessment and the engine agree a method is runnable. */
  offered: boolean
  /** The engine's plan for the chosen level is runnable. */
  canRun: boolean
  /** The engine's refusal for the chosen level, when it has one. */
  planRefusal: string
  dryRun: boolean
  /** The serial-confirmation dialog is open. */
  confirming: boolean
  /** A job id exists and no terminal status has arrived. */
  running: boolean
  /** The latest progress phase the job reported, if any. */
  phase: string | null
  /** The job's terminal status, once it has one. */
  status: JobStatus | null
}

export interface SanitizeWorkflow {
  state: WorkflowStateName
  /** The label shown for the state. */
  headline: string
  /** Empty unless the state is BLOCKED or FAILED. */
  whyBlocked: string[]
  nextAction: string
  simulation: boolean
  /** The states drawn in the strip, with BLOCKED or FAILED placed where reached. */
  path: WorkflowStateName[]
}

function label(state: WorkflowStateName): string {
  return state.replace(/_/g, ' ')
}

function pathFor(state: WorkflowStateName): WorkflowStateName[] {
  if (state === 'BLOCKED') return ['DISCOVERED', 'PREFLIGHT', 'BLOCKED']
  if (state === 'FAILED') return ['DISCOVERED', 'PREFLIGHT', 'HUMAN_APPROVAL_REQUIRED', 'EXECUTING', 'FAILED']
  return [...SANITIZE_PATH]
}

function result(
  state: WorkflowStateName,
  simulation: boolean,
  nextAction: string,
  whyBlocked: string[] = [],
): SanitizeWorkflow {
  return {
    state,
    headline: simulation ? `${label(state)} (SIMULATION)` : label(state),
    whyBlocked,
    nextAction,
    simulation,
    path: pathFor(state),
  }
}

/** Every reason the preflight gives for refusing, in the server's own words. */
function refusals(facts: SanitizeFacts): string[] {
  const reasons: string[] = []
  const assessment = facts.assessment
  if (assessment && assessment.headline === 'NOT AVAILABLE') {
    reasons.push(assessment.reason)
  }
  for (const check of assessment?.safety_checks ?? []) {
    if (check.passed === false) reasons.push(`${check.label}: ${check.detail}`)
  }
  if (!facts.offered || !facts.canRun) {
    reasons.push(facts.planRefusal || 'No sanitization method is reachable on this device.')
  }
  return [...new Set(reasons.filter(Boolean))]
}

export function sanitizeWorkflow(facts: SanitizeFacts): SanitizeWorkflow {
  const status = facts.status
  // A job's own record decides the simulation label once one exists.
  const simulation = status ? status.params?.dry_run !== false : facts.dryRun

  if (status && status.state !== 'running') {
    if (status.state === 'complete') {
      return result(
        'COMPLETE',
        simulation,
        simulation
          ? 'Nothing was written. Get the certificate to record the dry run.'
          : 'Read the verification and the residual risk before relying on the result.',
      )
    }
    const why = status.error || `The job ended ${status.state}.`
    return result(
      'FAILED',
      simulation,
      simulation
        ? 'The dry run stopped. Nothing was written.'
        : 'The device is in an unknown state until checked. Read the failure.',
      [why],
    )
  }

  if (facts.running) {
    if (facts.phase === 'VERIFY') {
      return result('VERIFYING', simulation, 'Wait for read-back verification to finish.')
    }
    return result(
      'EXECUTING',
      simulation,
      simulation
        ? 'The plan is being run without writing to the device.'
        : 'Wait for the operation to finish. Do not disconnect the device.',
    )
  }

  if (!facts.assessment) {
    return result('DISCOVERED', facts.dryRun, 'Waiting for the server preflight of this device.')
  }

  const reasons = refusals(facts)
  // NOT AUTHORIZED blocks a real erase only: a dry run needs no raw access.
  const privilegeOnly =
    facts.assessment.headline === 'NOT AUTHORIZED' && reasons.every((r) => r.startsWith('Privilege'))
  if (reasons.length && !(facts.dryRun && privilegeOnly)) {
    return result(
      'BLOCKED',
      false,
      facts.assessment.recommended_action ||
        'Resolve every reason listed by hand, then rescan. Nothing here unmounts, elevates or retries on its own.',
      reasons,
    )
  }
  if (facts.assessment.headline === 'NOT AUTHORIZED' && !facts.dryRun) {
    return result('BLOCKED', false, facts.assessment.recommended_action, [facts.assessment.reason])
  }

  if (facts.dryRun) {
    return result(
      'PREFLIGHT',
      true,
      'Preflight passed. A dry run writes nothing and needs no approval. A real erase needs HUMAN APPROVAL: the typed device serial.',
    )
  }
  return result(
    'HUMAN_APPROVAL_REQUIRED',
    false,
    facts.confirming
      ? 'Type the device serial. The server re-reads it from the device and refuses a mismatch.'
      : 'Nothing is written until a person confirms by typing the device serial.',
  )
}
