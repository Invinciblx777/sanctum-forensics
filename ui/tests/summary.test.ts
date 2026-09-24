/**
 * The executive summary counts only what the case record says, and never
 * counts a dry run as an erasure.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'

import type { CaseDetail, OperationRecord, PlatformStatus } from '../src/lib/api.ts'
import { NOT_PHYSICALLY_VALIDATED, executiveSummary, judgeSummary } from '../src/lib/summary.ts'

function op(type: string, status: string, params: Record<string, unknown> = {}, recovered = 0): OperationRecord {
  return {
    operation_id: `${type}-${status}-${Math.random()}`,
    case_id: 'C1',
    evidence_id: '',
    type,
    status,
    started_at: '',
    completed_at: null,
    operator: 'u',
    result_ref: '',
    recovered_artifacts: recovered,
    params,
  }
}

function detail(operations: OperationRecord[], chain = 'VALID', signed = [true]): CaseDetail {
  return {
    case: {} as CaseDetail['case'],
    evidence: [{} as CaseDetail['evidence'][number]],
    operations,
    reports: signed.map((value, index) => ({
      report_id: `r${index}`,
      case_id: 'C1',
      operation_id: '',
      json_name: '',
      pdf_name: '',
      report_hash: '',
      signed: value,
      pubkey_fingerprint: '',
      generated_at: '',
    })),
    audit: { chain_status: chain, chain_explanation: 'why', first_broken_seq: null, entry_count: 5, events: [] },
  }
}

const platform = { limitations: ['Overwrite cannot reach remapped flash.'] } as PlatformStatus

test('a dry run is a simulation, never an erasure', () => {
  const s = executiveSummary(detail([op('erase-drive', 'complete', { dry_run: true })]), platform)
  assert.ok(s.erased.some((line) => line.includes('SIMULATION')))
  assert.ok(!s.erased.some((line) => line.includes('drive sanitization completed')))
  assert.ok(s.unverified.some((line) => line.includes('Dry runs prove the plan')))
})

test('a real completed erase is counted', () => {
  const s = executiveSummary(detail([op('erase-drive', 'complete', { dry_run: false })]), platform)
  assert.ok(s.erased.includes('1 drive sanitization completed'))
})

test('a missing dry_run flag is treated as a simulation', () => {
  const s = executiveSummary(detail([op('erase-files', 'complete', {})]), platform)
  assert.ok(s.erased.some((line) => line.includes('SIMULATION')))
})

test('recovered artifacts are summed over completed carves only', () => {
  const s = executiveSummary(
    detail([op('carve', 'complete', {}, 7), op('carve', 'running', {}, 99), op('carve', 'complete', {}, 3)]),
    platform,
  )
  assert.ok(s.found.includes('10 recovered artifacts from 2 completed recovery runs'))
  assert.ok(s.unverified.some((line) => line.includes('still running')))
})

test('a broken chain and unsigned reports are listed as unverified', () => {
  const s = executiveSummary(detail([], 'BROKEN', [true, false]), platform)
  assert.ok(s.unverified.some((line) => line.includes('BROKEN')))
  assert.ok(s.unverified.includes('1 report not signed'))
  assert.ok(s.verified.includes('1 signed report of 2'))
})

test('platform limitations always reach the unverified column', () => {
  const s = executiveSummary(detail([]), platform)
  assert.ok(s.unverified.includes('Overwrite cannot reach remapped flash.'))
})

test('no open case says so in every column it can', () => {
  const s = executiveSummary(null, platform)
  assert.ok(s.found[0].includes('No case is open'))
  assert.ok(s.erased[0].includes('No case is open'))
})

test('the chain line uses a correct plural', () => {
  const s = executiveSummary(detail([]), platform)
  assert.ok(s.verified.includes('Audit chain VALID over 5 entries'))
})

test('the judge summary answers all six questions even with no case open', () => {
  const summary = judgeSummary(null, null, 'UNREAD')
  for (const key of ['erasure', 'recovery', 'verification', 'integrity', 'safety', 'limitations'] as const) {
    assert.ok(summary[key].length > 0, key)
  }
  assert.ok(summary.integrity.includes('Audit chain: UNREAD.'))
})

test('the limitations always lead with what is not physically validated', () => {
  const summary = judgeSummary(detail([op('carve', 'complete', {}, 3)]), null, 'VALID')
  assert.deepEqual(summary.limitations.slice(0, NOT_PHYSICALLY_VALIDATED.length), [...NOT_PHYSICALLY_VALIDATED])
  assert.ok(summary.limitations.some((line) => line.includes('SYNTHETIC')))
})

test('the judge summary never counts a dry run as an erasure', () => {
  const summary = judgeSummary(detail([op('erase-drive', 'complete', { dry_run: true })]), null, 'VALID')
  assert.ok(!summary.erasure.some((line) => /drive sanitization completed/.test(line)))
  assert.ok(summary.erasure.some((line) => line.includes('SIMULATION')))
})

test('no judge summary line states a percentage', () => {
  const summary = judgeSummary(detail([op('carve', 'complete', {}, 3)]), null, 'VALID')
  const lines = Object.values(summary).flat()
  assert.ok(!lines.some((line) => /\d+(\.\d+)?%/.test(line)))
})
