import { test } from 'node:test'
import assert from 'node:assert/strict'

import type { DeviceAssessment, JobStatus, SafetyCheck } from '../src/lib/api.ts'
import { REAL_ERASE_PATH, SANITIZE_PATH, refusalFrom, sanitizeWorkflow } from '../src/lib/workflowState.ts'
import type { SanitizeFacts } from '../src/lib/workflowState.ts'

function check(key: string, label: string, passed: boolean | null, detail: string): SafetyCheck {
  return { key, label, passed, detail } as SafetyCheck
}

function assessment(overrides: Partial<DeviceAssessment> = {}): DeviceAssessment {
  return {
    device_id: 'usb-stick',
    platform: 'linux',
    status: 'SUPPORTED',
    headline: 'READY',
    reason: 'Overwrite is available.',
    recommended_action: '',
    recommended: null,
    alternatives: [],
    unavailable: [],
    verification: '',
    safety_checks: [
      check('not_mounted', 'No mounted filesystem', true, 'Nothing on this device is mounted.'),
      check('privilege', 'Privilege available', true, 'helper socket'),
    ],
    flash_limitation: '',
    ...overrides,
  } as DeviceAssessment
}

function facts(overrides: Partial<SanitizeFacts> = {}): SanitizeFacts {
  return {
    assessment: assessment(),
    offered: true,
    canRun: true,
    planRefusal: '',
    dryRun: false,
    confirming: false,
    running: false,
    phase: null,
    status: null,
    ...overrides,
  }
}

function job(state: string, dryRun: boolean, error: string | null = null): JobStatus {
  return { state, params: { dry_run: dryRun }, error } as JobStatus
}

test('the state names are the ones core/workflow.py defines', () => {
  assert.deepEqual(SANITIZE_PATH, [
    'DISCOVERED',
    'PREFLIGHT',
    'HUMAN_APPROVAL_REQUIRED',
    'EXECUTING',
    'VERIFYING',
    'COMPLETE',
  ])
})

test('no assessment yet is DISCOVERED, not a pass', () => {
  assert.equal(sanitizeWorkflow(facts({ assessment: null })).state, 'DISCOVERED')
})

test('a mounted device is BLOCKED with the reason and the human remedy', () => {
  const flow = sanitizeWorkflow(
    facts({
      offered: false,
      canRun: false,
      assessment: assessment({
        headline: 'NOT AVAILABLE',
        reason: 'A filesystem on this device is in use (/run/media/stick).',
        recommended_action: 'Unmount or eject every volume on this device, then rescan.',
        safety_checks: [
          check('not_mounted', 'No mounted filesystem', false, 'Mounted at /run/media/stick.'),
        ],
      }),
    }),
  )
  assert.equal(flow.state, 'BLOCKED')
  assert.ok(flow.whyBlocked.some((line) => line.includes('in use')))
  assert.ok(flow.whyBlocked.includes('No mounted filesystem: Mounted at /run/media/stick.'))
  assert.match(flow.nextAction, /Unmount/)
  assert.deepEqual(flow.path, ['DISCOVERED', 'PREFLIGHT', 'BLOCKED'])
})

test('a blocked dry run is still blocked when the method is unreachable', () => {
  const flow = sanitizeWorkflow(
    facts({ dryRun: true, canRun: false, planRefusal: 'Purge is not reachable.' }),
  )
  assert.equal(flow.state, 'BLOCKED')
  assert.deepEqual(flow.whyBlocked, ['Purge is not reachable.'])
})

test('a real erase waits for HUMAN APPROVAL and shows no execution', () => {
  const flow = sanitizeWorkflow(facts())
  assert.equal(flow.state, 'HUMAN_APPROVAL_REQUIRED')
  assert.equal(flow.headline, 'HUMAN APPROVAL REQUIRED')
  assert.equal(flow.simulation, false)
  const open = sanitizeWorkflow(facts({ confirming: true }))
  assert.equal(open.state, 'HUMAN_APPROVAL_REQUIRED')
  assert.match(open.nextAction, /serial/)
  assert.match(open.nextAction, /backup/)
})

test('a dry run before it starts is PREFLIGHT and labelled SIMULATION', () => {
  const flow = sanitizeWorkflow(facts({ dryRun: true }))
  assert.equal(flow.state, 'PREFLIGHT')
  assert.equal(flow.simulation, true)
  assert.match(flow.headline, /SIMULATION/)
})

test('NOT AUTHORIZED blocks a real erase but not a dry run', () => {
  const denied = assessment({
    headline: 'NOT AUTHORIZED',
    reason: 'This process does not have the privilege.',
    recommended_action: 'Start the privileged helper, then rescan.',
    safety_checks: [check('privilege', 'Privilege available', false, 'Not elevated.')],
  })
  assert.equal(sanitizeWorkflow(facts({ assessment: denied })).state, 'BLOCKED')
  assert.equal(sanitizeWorkflow(facts({ assessment: denied, dryRun: true })).state, 'PREFLIGHT')
})

test('EXECUTING is only derived from a running job', () => {
  assert.notEqual(sanitizeWorkflow(facts({ confirming: true })).state, 'EXECUTING')
  const real = sanitizeWorkflow(facts({ running: true, phase: 'ERASE' }))
  assert.equal(real.state, 'EXECUTING')
  assert.equal(real.simulation, false)
  const dry = sanitizeWorkflow(facts({ running: true, dryRun: true }))
  assert.equal(dry.headline, 'EXECUTING (SIMULATION)')
})

test('the VERIFY phase is VERIFYING', () => {
  assert.equal(sanitizeWorkflow(facts({ running: true, phase: 'VERIFY' })).state, 'VERIFYING')
})

test('a completed dry run is COMPLETE (SIMULATION), read from the job not the toggle', () => {
  const flow = sanitizeWorkflow(facts({ dryRun: false, status: job('complete', true) }))
  assert.equal(flow.state, 'COMPLETE')
  assert.equal(flow.simulation, true)
  assert.match(flow.nextAction, /Nothing was written/)
})

test('a failed real erase is FAILED with the reason and an unknown device state', () => {
  const flow = sanitizeWorkflow(facts({ status: job('failed', false, 'device went away') }))
  assert.equal(flow.state, 'FAILED')
  assert.deepEqual(flow.whyBlocked, ['device went away'])
  assert.match(flow.nextAction, /unknown state/)
})

const server = (state: string, why: string[] = []) => ({
  state,
  why_blocked: why,
  next_action: `server says ${state}`,
})

test('a real erase shows the state the server derived, in its words', () => {
  for (const state of ['HUMAN_APPROVAL_REQUIRED', 'PLAN_READY', 'BACKUP_VERIFIED']) {
    const flow = sanitizeWorkflow(facts({ server: server(state, ['no person approved']) }))
    assert.equal(flow.state, state)
    assert.equal(flow.nextAction, `server says ${state}`)
    assert.deepEqual(flow.whyBlocked, [], `${state} is not a block`)
    assert.deepEqual(flow.path, [...REAL_ERASE_PATH])
  }
  const blocked = sanitizeWorkflow(facts({ server: server('BLOCKED', ['device identity changed']) }))
  assert.equal(blocked.state, 'BLOCKED')
  assert.deepEqual(blocked.whyBlocked, ['device identity changed'])
  const noBackup = sanitizeWorkflow(facts({ server: server('BACKUP_REQUIRED', ['backup gone']) }))
  assert.deepEqual(noBackup.path, ['DISCOVERED', 'PREFLIGHT', 'BACKUP_REQUIRED'])
})

test('an unknown server state is never invented into a screen state', () => {
  const flow = sanitizeWorkflow(facts({ server: server('SOMETHING_NEW') }))
  assert.equal(flow.state, 'HUMAN_APPROVAL_REQUIRED')
})

test('a server refusal is BLOCKED with its WHY BLOCKED, not a generic failure', () => {
  const refusal = refusalFrom({
    message: 'REFUSED: nothing was erased',
    remediation: 'open the workflow',
    whyBlocked: ['authorization auth-1 was already used'],
    workflowState: 'BLOCKED',
    physicalDeviceModified: false,
  })
  const flow = sanitizeWorkflow(facts({ refusal, server: server('PLAN_READY') }))
  assert.equal(flow.state, 'BLOCKED')
  assert.deepEqual(flow.whyBlocked, ['authorization auth-1 was already used'])
  assert.match(flow.nextAction, /PHYSICAL DEVICE MODIFIED: FALSE/)
})

test('a refusal never says the device is untouched unless the server said so', () => {
  const unknown = refusalFrom({
    message: 'x',
    remediation: '',
    whyBlocked: [],
    workflowState: '',
    physicalDeviceModified: null,
  })
  assert.equal(unknown.physicalDeviceModified, null)
  assert.deepEqual(unknown.whyBlocked, ['x'])
  assert.equal(
    refusalFrom({ ...unknown, whyBlocked: [] }, true).physicalDeviceModified,
    false,
    'open and approve have no write path',
  )
})

test('a dry run ignores any server record and never leaves SIMULATION', () => {
  const flow = sanitizeWorkflow(facts({ dryRun: true, server: server('PLAN_READY') }))
  assert.equal(flow.state, 'PREFLIGHT')
  assert.equal(flow.simulation, true)
})

function realJob(verification: unknown): JobStatus {
  return { state: 'complete', params: { dry_run: false }, result: { verification }, error: null } as unknown as JobStatus
}

test('a finished real job whose read-back FAILED is FAILED, never COMPLETE', () => {
  const flow = sanitizeWorkflow(
    facts({ status: realJob({ passed: false, failed_offsets: [0, 4096] }) }),
  )
  assert.equal(flow.state, 'FAILED')
  assert.match(flow.whyBlocked[0], /verification FAILED at 2/)
  assert.match(flow.nextAction, /NOT sanitized/)
})

test('COMPLETE on a real job only claims what verification supports', () => {
  const passed = sanitizeWorkflow(facts({ status: realJob({ passed: true, failed_offsets: [] }) }))
  assert.equal(passed.state, 'COMPLETE')
  assert.doesNotMatch(passed.nextAction, /Do not treat/)
  for (const verification of [{ passed: null, failed_offsets: [] }, undefined]) {
    const flow = sanitizeWorkflow(facts({ status: realJob(verification) }))
    assert.equal(flow.state, 'COMPLETE')
    assert.match(flow.nextAction, /Do not treat the medium as verified/)
  }
})

test('a simulation is never shown as a physical completion', () => {
  const flow = sanitizeWorkflow(facts({ status: job('complete', true) }))
  assert.equal(flow.simulation, true)
  assert.equal(flow.headline, 'COMPLETE (SIMULATION)')
  assert.match(flow.nextAction, /Nothing was written/)
})

test('a helper refusal at the write seam is BLOCKED, not a failed erase', () => {
  const status = {
    state: 'failed',
    params: { dry_run: false },
    error: 'REFUSED at the write seam: model changed. Nothing was erased.',
    error_kind: 'WorkflowGateRefused',
  } as unknown as JobStatus
  const flow = sanitizeWorkflow(facts({ status }))
  assert.equal(flow.state, 'BLOCKED')
  assert.match(flow.whyBlocked[0], /model changed/)
  assert.match(flow.nextAction, /new workflow/)
  const other = { ...status, error_kind: 'OverwriteIncomplete' } as unknown as JobStatus
  assert.equal(sanitizeWorkflow(facts({ status: other })).state, 'FAILED')
})
