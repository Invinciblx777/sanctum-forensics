// Design preview entry. Dev-only.
//
// vite build has index.html as its single input, so nothing under this
// directory reaches dist/. It exists so a change to the shared base can be
// looked at on every screen at the projector's resolution before it is
// believed, rather than described in a commit message and hoped for.
//
//   npm run dev  ->  http://127.0.0.1:5173/preview.html?screen=sanitize
//
// The screens are the real screens. Nothing is re-implemented here: the server
// is replaced at the fetch and EventSource boundary, and the interesting state
// is reached by driving the real controls, so a preview that renders proves
// the screen works and not merely that a mock does.

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from '../App'
import '../index.css'
import {
  CANDIDATES,
  DEVICES,
  FILE_RECORDS,
  LEDGER,
  PLATFORM,
  REPORT_VERIFICATION,
  SANITIZE_TARGET,
  WINDOWS_ROW,
} from './fixtures'

const screen = new URLSearchParams(window.location.search).get('screen') ?? 'devices'

// ---------------------------------------------------------------------------
// The server, replaced
// ---------------------------------------------------------------------------

const JOB_RESULTS: Record<string, unknown> = {
  'carve-preview': {
    candidates: CANDIDATES,
    limitations: [
      'The image was carved without a filesystem for 41% of its span, so ' +
        'candidates in that region have no metadata to corroborate them.',
    ],
  },
  'erase-files-preview': { records: FILE_RECORDS },
  'erase-drive-preview': {
    residual_risk: {
      factors: [
        'The USB bridge does not pass ATA pass-through, so no firmware ' +
          'sanitize could be issued and the result is a Clear.',
      ],
    },
  },
}

function jobStatus(jobId: string): unknown {
  return {
    job_id: jobId,
    kind: jobId.replace('-preview', ''),
    state: 'complete',
    params: {},
    progress_count: 128,
    dropped_progress: 0,
    latest: null,
    result: JOB_RESULTS[jobId] ?? {},
    error: null,
    error_kind: null,
    remediation: '',
    started_at: '2026-09-05T18:04:11+00:00',
    finished_at: '2026-09-05T18:39:02+00:00',
    cancel_requested: false,
  }
}

function respond(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

window.fetch = (input: RequestInfo | URL): Promise<Response> => {
  const url = String(typeof input === 'string' ? input : (input as Request).url)
  if (url.startsWith('/health')) {
    return Promise.resolve(respond({ tool_version: 'sanctum 0.9.0' }))
  }
  if (url.startsWith('/platform/devices/')) {
    const row = url.includes('PhysicalDrive') ? WINDOWS_ROW : SANITIZE_TARGET
    return Promise.resolve(
      respond({ normalized: row.normalized, assessment: row.assessment }),
    )
  }
  if (url.startsWith('/platform')) return Promise.resolve(respond(PLATFORM))
  // The app now opens on Cases; an empty list keeps that screen quiet.
  if (url.startsWith('/cases')) return Promise.resolve(respond({ cases: [] }))
  if (url.startsWith('/devices')) {
    return Promise.resolve(
      respond({
        devices: screen === 'unavailable' ? [...DEVICES, WINDOWS_ROW] : DEVICES,
        limitations: [
          'Two devices are behind a USB bridge that does not pass ATA ' +
            'pass-through, so their capability was probed through SCSI only.',
        ],
      }),
    )
  }
  if (url.startsWith('/ledger/verify')) return Promise.resolve(respond(LEDGER))
  if (url.includes('/verify')) return Promise.resolve(respond(REPORT_VERIFICATION))
  if (url.startsWith('/jobs/carve')) {
    return Promise.resolve(respond({ job_id: 'carve-preview', kind: 'carve' }))
  }
  if (url.startsWith('/jobs/erase-files')) {
    return Promise.resolve(
      respond({ job_id: 'erase-files-preview', kind: 'erase-files' }),
    )
  }
  if (url.startsWith('/jobs/erase-drive')) {
    return Promise.resolve(
      respond({ job_id: 'erase-drive-preview', kind: 'erase-drive' }),
    )
  }
  return Promise.resolve(respond({}))
}

class FixtureEventSource {
  private handlers: Record<string, ((event: { data: string }) => void)[]> = {}

  constructor(url: string) {
    const jobId = url.split('/')[2]
    // One state event and no progress: every preview shows a finished job,
    // because a half-drawn progress bar says nothing about the layout that has
    // to hold once the results arrive.
    setTimeout(() => {
      for (const handler of this.handlers.state ?? []) {
        handler({ data: JSON.stringify(jobStatus(jobId)) })
      }
    }, 0)
  }

  addEventListener(name: string, handler: (event: { data: string }) => void): void {
    ;(this.handlers[name] ??= []).push(handler)
  }

  close(): void {}
}

window.EventSource = FixtureEventSource as unknown as typeof EventSource

// ---------------------------------------------------------------------------
// Driving the real controls
// ---------------------------------------------------------------------------

const settle = () => new Promise((resolve) => setTimeout(resolve, 30))

function find<T extends Element>(selector: string, text?: string): T {
  const matches = [...document.querySelectorAll<T>(selector)]
  const hit = text
    ? matches.find((node) => (node.textContent ?? '').includes(text))
    : matches[0]
  if (!hit) throw new Error(`preview: no ${selector}${text ? ` containing ${text}` : ''}`)
  return hit
}

function click(selector: string, text?: string): void {
  find<HTMLElement>(selector, text).click()
}

/** React listens for the native input event, not for a value assignment. */
function type(element: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value',
  )?.set
  setter?.call(element, value)
  element.dispatchEvent(new Event('input', { bubbles: true }))
}

async function openDevices(): Promise<void> {
  click('.nav-item', 'Devices')
  await settle()
  await settle()
}

async function driveSanitize(): Promise<void> {
  // The real path onto this screen: pick the stick on Devices.
  await openDevices()
  click('tr.is-openable', '/dev/sda')
  await settle()
  click('button.primary', 'Review plan')
  await settle()
  const dryRun = find<HTMLInputElement>('input[type="checkbox"]')
  dryRun.click()
  await settle()
  click('button.destructive')
  await settle()
  type(find<HTMLInputElement>('.modal input[type="text"]'), '4C53000112080')
}

async function driveFiles(): Promise<void> {
  click('.nav-item', 'File eraser')
  await settle()
  for (const path of [
    '/home/analyst/case-2149/interview-notes.docx',
    '/home/analyst/case-2149/exhibits/DSC_0491.NEF',
    '/home/analyst/case-2149/index.sqlite',
    '/home/analyst/case-2149/transcript.txt',
    '/var/log/journal/sanctum',
  ]) {
    type(find<HTMLInputElement>('input[placeholder^="/absolute"]'), path)
    await settle()
    click('button.btn', 'Add')
    await settle()
  }
  click('button.primary')
  await settle()
  await settle()
  click('tr.is-openable', 'DSC_0491.NEF')
}

async function driveRecovery(): Promise<void> {
  click('.nav-item', 'Recovery')
  await settle()
  type(
    find<HTMLInputElement>('input[placeholder^="/path/to/case"]'),
    '/evidence/case-2149/recovery-fat32.dd',
  )
  await settle()
  click('button.primary', 'Scan')
  await settle()
  await settle()
  const rows = document.querySelectorAll<HTMLElement>('tr.is-openable')
  rows[2]?.click()
}

async function driveAudit(): Promise<void> {
  click('.nav-item', 'Audit')
  await settle()
  type(find<HTMLInputElement>('input[placeholder^="erase-drive"]'), 'erase-drive-3f9c2a')
  await settle()
  click('button.btn', 'Verify')
}

async function driveUnavailable(): Promise<void> {
  await openDevices()
  click('tr.is-openable', 'PhysicalDrive2')
  await settle()
  await settle()
}

async function drivePlatform(): Promise<void> {
  click('.nav-item', 'Platform')
  await settle()
}

const DRIVERS: Record<string, () => Promise<void>> = {
  devices: openDevices,
  unavailable: driveUnavailable,
  platform: drivePlatform,
  sanitize: driveSanitize,
  files: driveFiles,
  recovery: driveRecovery,
  audit: driveAudit,
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

void (async () => {
  await settle()
  await (DRIVERS[screen] ?? DRIVERS.devices)()
  await settle()
  // The screenshot driver polls for this, so it never captures a half-driven
  // screen and never has to guess at a fixed sleep.
  document.documentElement.setAttribute('data-preview-ready', screen)
})()
