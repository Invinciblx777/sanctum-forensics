import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  checkWord,
  currentStep,
  deviceKind,
  headlineTone,
  runnableStatus,
  statusWord,
} from '../src/lib/platform.ts'
import type { DeviceAssessment, NormalizedDevice } from '../src/lib/api.ts'

const base: NormalizedDevice = {
  id: 'PhysicalDrive2',
  platform: 'windows',
  path: '\\\\.\\PhysicalDrive2',
  vendor: 'Kingston',
  model: 'DataTraveler',
  serial: 'S',
  capacity_bytes: 1,
  interface: 'usb',
  media_type: 'flash',
  media_basis: '',
  removable: true,
  mounted: false,
  mount_points: [],
  system_device: false,
  system_reasons: [],
  filesystems: [],
  partitions: [],
  stable_id: '',
  limitations: [],
}

test('only supported statuses are runnable', () => {
  assert.equal(runnableStatus('SUPPORTED'), true)
  assert.equal(runnableStatus('SUPPORTED_WITH_LIMITATIONS'), true)
  for (const s of ['UNSUPPORTED', 'UNVERIFIED', 'NOT_AUTHORIZED', 'INCONCLUSIVE'] as const) {
    assert.equal(runnableStatus(s), false, s)
  }
  assert.equal(runnableStatus(undefined), false)
})

test('unverified is never drawn as success', () => {
  assert.notEqual(statusWord('UNVERIFIED').tone, 'success')
  assert.notEqual(statusWord('SUPPORTED_WITH_LIMITATIONS').tone, 'success')
  assert.equal(statusWord('UNSUPPORTED').tone, 'destructive')
})

test('an undecided safety check is not a pass', () => {
  assert.equal(checkWord({ key: 'k', label: 'l', passed: null, detail: '' }).word, 'unknown')
  assert.equal(checkWord({ key: 'k', label: 'l', passed: false, detail: '' }).tone, 'destructive')
})

test('device kind reads without storage vocabulary', () => {
  assert.equal(deviceKind(base), 'External USB Flash')
  assert.equal(
    deviceKind({ ...base, removable: false, interface: 'nvme', media_type: 'ssd' }),
    'Internal NVMe SSD',
  )
})

test('the step follows the flow', () => {
  const s = {
    hasDevice: true,
    hasAssessment: true,
    reviewing: false,
    confirming: false,
    running: false,
    finished: false,
    verified: false,
    certified: false,
  }
  assert.equal(currentStep({ ...s, hasDevice: false }), 0)
  assert.equal(currentStep(s), 2)
  assert.equal(currentStep({ ...s, reviewing: true }), 3)
  assert.equal(currentStep({ ...s, running: true }), 5)
  assert.equal(currentStep({ ...s, finished: true }), 6)
  assert.equal(currentStep({ ...s, finished: true, verified: true }), 7)
})

test('headline tone', () => {
  const a = { headline: 'NOT AVAILABLE', status: 'UNSUPPORTED' } as DeviceAssessment
  assert.equal(headlineTone(a), 'destructive')
  assert.equal(headlineTone({ ...a, headline: 'READY', status: 'SUPPORTED' }), 'success')
  assert.equal(headlineTone(null), 'unknown')
})
