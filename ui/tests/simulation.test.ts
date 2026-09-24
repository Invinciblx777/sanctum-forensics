import { test } from 'node:test'
import assert from 'node:assert/strict'

import type { JobStatus } from '../src/lib/api.ts'
import { SIMULATION_BANNER, isSimulation } from '../src/lib/simulation.ts'

function job(params: Record<string, unknown>): JobStatus {
  return { params } as JobStatus
}

test('only an explicit dry_run false is a real operation', () => {
  assert.equal(isSimulation(job({ dry_run: false })), false)
  assert.equal(isSimulation(job({ dry_run: true })), true)
  assert.equal(isSimulation(job({})), true)
})

test('no job is not a simulation banner', () => {
  assert.equal(isSimulation(null), false)
})

test('the banner says no physical device was modified', () => {
  assert.equal(SIMULATION_BANNER, 'SIMULATION / NO PHYSICAL DEVICE MODIFIED')
})
