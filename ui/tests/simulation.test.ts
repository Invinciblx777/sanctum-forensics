import { test } from 'node:test'
import assert from 'node:assert/strict'

import type { JobStatus } from '../src/lib/api.ts'
import { DRY_RUN_BANNER, isDryRun } from '../src/lib/simulation.ts'

function job(params: Record<string, unknown>): JobStatus {
  return { params } as JobStatus
}

test('only an explicit dry_run false is a real operation', () => {
  assert.equal(isDryRun(job({ dry_run: false })), false)
  assert.equal(isDryRun(job({ dry_run: true })), true)
  assert.equal(isDryRun(job({})), true)
})

test('no job is not a dry run banner', () => {
  assert.equal(isDryRun(null), false)
})

test('the banner says no physical device was modified', () => {
  assert.equal(DRY_RUN_BANNER, 'DRY RUN / NO PHYSICAL DEVICE MODIFIED')
})
