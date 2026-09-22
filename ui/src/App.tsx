import { useEffect, useState } from 'react'
import type { DeviceRow, PlatformStatus } from './lib/api'
import { api } from './lib/api'
import { deviceKind, privilegeWord, statusWord } from './lib/platform'
import { bytes } from './lib/format'
import Platform from './screens/Platform'
import { CaseProvider, useCase } from './lib/caseContext'
import Audit from './screens/Audit'
import Cases from './screens/Cases'
import Devices from './screens/Devices'
import FileEraser from './screens/FileEraser'
import Recovery from './screens/Recovery'
import Sanitize from './screens/Sanitize'

/**
 * Navigation, in the order an investigation happens.
 *
 * Cases first, because everything else files itself against one. The previous
 * order started at Devices, which is the order the *tool* was built in and not
 * the order the work is done in: an examiner opens a case, registers what was
 * seized, recovers from it or sanitizes it, and then reports. A sidebar that
 * opened on a list of block devices invited the operator to start wiping
 * before anything recorded why.
 */
type ScreenId =
  | 'cases'
  | 'devices'
  | 'sanitize'
  | 'files'
  | 'recovery'
  | 'audit'
  | 'platform'

const SCREENS: { id: ScreenId; label: string; hint: string }[] = [
  { id: 'cases', label: 'Cases', hint: 'Evidence, operations, reports, audit' },
  { id: 'devices', label: 'Devices', hint: 'Enumerate and probe' },
  { id: 'recovery', label: 'Recovery', hint: 'Carve and undelete, read-only' },
  { id: 'sanitize', label: 'Sanitization', hint: 'Capability-driven erasure' },
  { id: 'files', label: 'File eraser', hint: 'Files, folders, free space' },
  { id: 'audit', label: 'Audit', hint: 'Chain, reports, verification' },
  { id: 'platform', label: 'Platform', hint: 'What this computer can do, and why' },
]

/**
 * The four claims that have to be true at a glance, at the bottom of every
 * screen.
 *
 * Each one is read from the server rather than asserted by this file:
 * integrity is the chain's verdict, evidence read-only is a property of the
 * carving path that the health endpoint reports as a limitation when it is
 * *not* true, and the helper line says which privilege boundary is actually in
 * force. A status strip that hard-coded "VALID" would be decoration.
 */
function StatusStrip({ selected }: { selected: DeviceRow | null }) {
  const [chain, setChain] = useState<{ status: string; entry_count: number } | null>(
    null,
  )
  const [platform, setPlatform] = useState<PlatformStatus | null>(null)

  useEffect(() => {
    void api
      .ledgerVerify()
      .then((answer) =>
        setChain({ status: answer.status, entry_count: answer.entry_count }),
      )
      .catch(() => setChain(null))
    void api.platform().then(setPlatform).catch(() => setPlatform(null))
  }, [])

  const device = selected?.normalized
  const assessment = selected?.assessment
  const sanitization = assessment
    ? assessment.headline === 'READY'
      ? statusWord(assessment.status)
      : { word: assessment.headline.toLowerCase(), tone: assessment.headline === 'NOT AUTHORIZED' ? 'warning' : 'destructive' }
    : null
  const verification = assessment?.recommended?.verification

  return (
    <div className="status-strip" aria-label="Status">
      <span className="state-mark is-unknown" title={platform?.platform.os_build}>
        PLATFORM: {platform?.platform.os_name ?? 'UNREAD'}
      </span>
      <span
        className={
          platform?.privilege.elevated || platform?.privilege.helper === 'socket'
            ? 'state-mark is-success'
            : 'state-mark is-warning'
        }
        title={platform?.privilege.basis}
      >
        PRIVILEGE: {privilegeWord(platform?.privilege ?? null)}
      </span>
      <span className="state-mark is-unknown" title={device?.path}>
        DEVICE:{' '}
        {device ? `${deviceKind(device)} ${bytes(device.capacity_bytes)}` : 'none selected'}
      </span>
      <span
        className={`state-mark is-${sanitization?.tone ?? 'unknown'}`}
        title={assessment?.reason}
      >
        SANITIZATION: {sanitization?.word ?? 'no device'}
      </span>
      <span
        className={verification ? 'state-mark is-success' : 'state-mark is-unknown'}
        title={verification}
      >
        VERIFICATION: {verification ? 'available' : assessment ? 'not available' : '—'}
      </span>
      <span
        className={
          chain?.status === 'VALID'
            ? 'state-mark is-success'
            : chain?.status === 'INCONCLUSIVE_TAIL' || chain?.status === 'INCOMPLETE_TAIL'
              ? 'state-mark is-warning'
              : chain
                ? 'state-mark is-destructive'
                : 'state-mark is-unknown'
        }
        title={chain ? `${chain.entry_count} chain entries` : 'chain not read'}
      >
        AUDIT: {chain?.status === 'VALID' ? 'READY' : (chain?.status ?? 'UNREAD')}
      </span>
      <span className="state-mark is-success" title="core/carve never opens O_RDWR">
        EVIDENCE: READ ONLY
      </span>
    </div>
  )
}

function Shell() {
  const [screen, setScreen] = useState<ScreenId>('cases')
  const [selected, setSelected] = useState<DeviceRow | null>(null)
  const [health, setHealth] = useState<Record<string, unknown> | null>(null)
  const { openCase } = useCase()

  useEffect(() => {
    void api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand">
          <svg width="17" height="19" viewBox="0 0 32 32" aria-hidden>
            <path
              d="M16 4 L26 8 v9 c0 6-4 9-10 11 C10 26 6 23 6 17 V8 Z"
              fill="none"
              stroke="var(--accent)"
              strokeWidth="2"
            />
            <path d="M11 16 h10 M16 11 v10" stroke="var(--accent)" strokeWidth="2" />
          </svg>
          <div className="col" style={{ gap: 0 }}>
            <span className="brand-name">Sanctum</span>
            <span className="brand-sub">forensics</span>
          </div>
        </div>

        {/* The open case, always visible. Every screen files what it does
            against this, so hiding it on one screen would mean an operator
            could start a wipe without seeing which investigation it lands in. */}
        <button
          className={openCase ? 'case-badge is-open' : 'case-badge'}
          onClick={() => setScreen('cases')}
          title={openCase ? openCase.title : 'No case is open'}
        >
          <span className="case-badge-label">case</span>
          <span className="case-badge-id">
            {openCase ? openCase.case_id : 'none open'}
          </span>
        </button>

        <div className="nav">
          {SCREENS.map((item) => (
            <button
              key={item.id}
              className={screen === item.id ? 'nav-item active' : 'nav-item'}
              onClick={() => setScreen(item.id)}
              title={item.hint}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="sidebar-foot">
          <span>{(health?.tool_version as string) ?? 'offline'}</span>
          <span>127.0.0.1 only</span>
          {health?.launcher === true && (
            <button
              className="btn"
              onClick={() => {
                void api.quit().then(() =>
                  document.body.replaceChildren(
                    Object.assign(document.createElement('p'), {
                      className: 'empty',
                      textContent: 'Sanctum has stopped. You can close this window.',
                    }),
                  ),
                )
              }}
            >
              Quit Sanctum
            </button>
          )}
          {selected && <span title={selected.device.path}>▸ {selected.device.path}</span>}
        </div>
      </nav>

      <main className="main">
        {screen === 'cases' && <Cases />}
        {screen === 'devices' && (
          <Devices
            onSelect={(row) => {
              setSelected(row)
              setScreen('sanitize')
            }}
          />
        )}
        {screen === 'sanitize' && <Sanitize selected={selected} />}
        {screen === 'files' && <FileEraser />}
        {screen === 'recovery' && <Recovery />}
        {screen === 'audit' && <Audit />}
        {screen === 'platform' && <Platform />}
        <StatusStrip selected={selected} />
      </main>
    </div>
  )
}

export default function App() {
  return (
    <CaseProvider>
      <Shell />
    </CaseProvider>
  )
}
