import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  caseFacts,
  isSimulation,
  operationStatus,
  operationType,
  reportsByOperation,
} from '../src/lib/cases.ts'
import type { CaseDetail, CaseReportRecord, OperationRecord } from '../src/lib/api.ts'

function op(over: Partial<OperationRecord>): OperationRecord {
  return {
    operation_id: 'carve-1',
    case_id: 'C',
    evidence_id: '',
    type: 'carve',
    status: 'complete',
    started_at: '2026-09-25T10:00:00Z',
    completed_at: null,
    operator: 'alice (uid 1000)',
    result_ref: '',
    recovered_artifacts: 0,
    params: {},
    ...over,
  }
}

function report(over: Partial<CaseReportRecord>): CaseReportRecord {
  return {
    report_id: 'r',
    case_id: 'C',
    operation_id: 'carve-1',
    json_name: 'a.json',
    pdf_name: 'a.pdf',
    report_hash: 'ab',
    signed: true,
    pubkey_fingerprint: '',
    generated_at: '2026-09-25T10:00:00Z',
    ...over,
  }
}

function detail(over: Partial<CaseDetail>): CaseDetail {
  return {
    case: {
      case_id: 'C',
      title: '',
      description: '',
      status: 'open',
      created_at: '',
      created_by: '',
      updated_at: '',
      evidence_count: 0,
      operation_count: 0,
      report_count: 0,
      recovered_artifact_count: 0,
    },
    evidence: [],
    operations: [],
    reports: [],
    audit: {
      chain_status: 'VALID',
      chain_explanation: '',
      first_broken_seq: null,
      entry_count: 8,
      events: [],
    },
    ...over,
  }
}

test('job kinds read as words, and an unknown kind keeps its name', () => {
  assert.equal(operationType('carve'), 'Recovery (carve)')
  assert.equal(operationType('erase-drive'), 'Drive sanitize')
  assert.equal(operationType('destroy-record'), 'Destruction record')
  assert.equal(operationType('something-new'), 'something-new')
})

test('job states keep the registry word; failure is never inferred', () => {
  assert.deepEqual(operationStatus('complete'), { word: 'COMPLETE', tone: 'success' })
  assert.deepEqual(operationStatus('failed'), { word: 'FAILED', tone: 'destructive' })
  assert.equal(operationStatus('cancelled').word, 'CANCELLED')
  assert.deepEqual(operationStatus('odd'), { word: 'ODD', tone: 'unknown' })
  assert.deepEqual(operationStatus(''), { word: 'UNKNOWN', tone: 'unknown' })
})

test('only an explicit dry run is a simulation', () => {
  assert.equal(isSimulation(op({ params: { dry_run: true } })), true)
  assert.equal(isSimulation(op({ params: { dry_run: false } })), false)
  assert.equal(isSimulation(op({ params: {} })), false)
  assert.equal(isSimulation(op({ params: { dry_run: 'true' } })), false)
})

test('the latest report stands for its operation', () => {
  const byOp = reportsByOperation([
    report({ report_id: 'old', generated_at: '2026-09-25T10:00:00Z' }),
    report({ report_id: 'new', generated_at: '2026-09-25T11:00:00Z' }),
    report({ report_id: 'other', operation_id: 'erase-2' }),
  ])
  assert.equal(byOp.get('carve-1')?.report_id, 'new')
  assert.equal(byOp.get('erase-2')?.report_id, 'other')
})

test('the overview lines count what the case holds and say so when empty', () => {
  const empty = caseFacts(detail({}))
  assert.equal(empty.evidence, 'none registered')
  assert.equal(empty.operations, 'none run')
  assert.equal(empty.reports, 'none generated')
  assert.equal(empty.audit, 'of 8 entries in the chain')

  const full = caseFacts(
    detail({
      operations: [
        op({}),
        op({ operation_id: 'e', type: 'erase-drive', status: 'failed' }),
        op({ operation_id: 's', type: 'erase-drive', params: { dry_run: true } }),
      ],
      reports: [report({}), report({ operation_id: 'e', signed: false })],
    }),
  )
  assert.equal(full.operations, '2 complete · 1 failed — 1 of 3 simulated')
  assert.equal(full.reports, '1 signed · 1 unsigned')

  const rehearsal = caseFacts(
    detail({ operations: [op({ params: { dry_run: true } })] }),
  )
  assert.equal(rehearsal.operations, '1 complete — all simulated')
})
