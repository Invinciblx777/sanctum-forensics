import { test } from 'node:test'
import assert from 'node:assert/strict'

import { nativePicker, pickPaths } from '../src/lib/nativePicker.ts'

function host(pick: (kind: string) => Promise<unknown>) {
  return { pywebview: { api: { pick } } }
}

test('there is no picker outside the desktop window', () => {
  assert.equal(nativePicker({}), null)
  assert.equal(nativePicker({ pywebview: {} }), null)
  assert.equal(nativePicker({ pywebview: { api: {} } }), null)
})

test('the bridge is found once the desktop window has injected it', () => {
  assert.notEqual(
    nativePicker(host(async () => [])),
    null,
  )
})

test('the chosen paths come back, and the kind is passed through', async () => {
  const seen: string[] = []
  const win = host(async (kind) => {
    seen.push(kind)
    return ['/a/one', '/a/two']
  })
  assert.deepEqual(await pickPaths('files', win), ['/a/one', '/a/two'])
  assert.deepEqual(seen, ['files'])
})

test('a missing bridge, a cancelled dialog and a failed call all give no paths', async () => {
  assert.deepEqual(await pickPaths('file', {}), [])
  assert.deepEqual(await pickPaths('file', host(async () => [])), [])
  assert.deepEqual(await pickPaths('file', host(async () => null)), [])
  assert.deepEqual(
    await pickPaths('folder', host(async () => Promise.reject(new Error('no display')))),
    [],
  )
})

test('anything that is not a non-empty string is dropped', async () => {
  assert.deepEqual(
    await pickPaths('files', host(async () => ['/ok', '', 7, null, '/also'])),
    ['/ok', '/also'],
  )
})
