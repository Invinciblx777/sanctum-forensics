import { test } from 'node:test'
import assert from 'node:assert/strict'

import type { TraceRecord, TraceSweep } from '../src/lib/api.ts'
import { traceKind, traceOutcome, traceSummary } from '../src/lib/traces.ts'

function trace(overrides: Partial<TraceRecord> = {}): TraceRecord {
  return {
    kind: 'THUMBNAIL',
    target: '/home/asha/plan.jpg',
    location: '/home/asha/.cache/thumbnails/normal/0f3a.png',
    evidence: 'Named by the MD5 of file:///home/asha/plan.jpg.',
    content_copy: true,
    exact: true,
    action: '',
    removed: false,
    bytes_overwritten: 0,
    error: '',
    ...overrides,
  }
}

function sweep(traces: TraceRecord[]): TraceSweep {
  return { searched: ['a', 'b', 'c'], not_searched: [], traces, notes: [] }
}

test('kinds read as words, and an unknown kind keeps its name', () => {
  assert.equal(traceKind('TRASH_COPY'), 'Copy in the Trash')
  assert.equal(traceKind('SOMETHING_NEW'), 'SOMETHING_NEW')
})

test('a removed trace says how it was removed', () => {
  assert.deepEqual(traceOutcome(trace({ removed: true, action: 'erased' }), false), {
    word: 'erased',
    tone: 'success',
  })
})

test('an inexact match is never called removable, even in a dry run', () => {
  assert.equal(traceOutcome(trace({ exact: false }), true).word, 'left for you to judge')
})

test('a dry run promises only what a real run would do', () => {
  assert.deepEqual(traceOutcome(trace(), true), { word: 'would be removed', tone: 'unknown' })
})

test('a failure is shown as one', () => {
  assert.deepEqual(traceOutcome(trace({ error: 'EACCES' }), false), {
    word: 'not removed',
    tone: 'destructive',
  })
})

test('the summary counts what was found, removed and left', () => {
  assert.equal(traceSummary(sweep([]), false), 'Nothing found in the 3 places searched.')
  assert.equal(
    traceSummary(sweep([trace({ removed: true }), trace({ exact: false })]), false),
    '2 found, 1 removed, 1 left.',
  )
  assert.match(traceSummary(sweep([trace(), trace({ exact: false })]), true), /removes the 1 tied/)
})
