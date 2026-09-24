import { test } from 'node:test'
import assert from 'node:assert/strict'

import type { DeviceAssessment, JobStatus, SafetyCheck } from '../src/lib/api.ts'
import { SANITIZE_PATH, sanitizeWorkflow } from '../src/lib/workflowState.ts'
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
