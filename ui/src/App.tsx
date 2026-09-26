import { useEffect, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  Blocks,
  Cpu,
  FileX2,
  FolderKanban,
  HardDrive,
  LayoutDashboard,
  ScanSearch,
  ShieldX,
} from 'lucide-react'
import type { DeviceRow, PlatformStatus } from './lib/api'
import { api } from './lib/api'
import { deviceKind, privilegeWord, statusWord } from './lib/platform'
import { bytes } from './lib/format'
import Platform from './screens/Platform'
import { CaseProvider, useCase } from './lib/caseContext'
import Audit from './screens/Audit'
import Cases from './screens/Cases'
import Devices from './screens/Devices'
import Home from './screens/Home'
import FileEraser from './screens/FileEraser'
import Recovery from './screens/Recovery'
import Sanitize from './screens/Sanitize'

/**
 * Navigation, in the order an investigation happens.
 *
 * Overview first: the four workflows and the open case's summary, so the tool
 * explains itself in one screen. Then Cases, because everything else files
 * itself against one. The previous
 * order started at Devices, which is the order the *tool* was built in and not
 * the order the work is done in: an examiner opens a case, registers what was
 * seized, recovers from it or sanitizes it, and then reports. A sidebar that
 * opened on a list of block devices invited the operator to start wiping
 * before anything recorded why.
 */
type ScreenId =
  | 'home'
  | 'cases'
  | 'devices'
  | 'sanitize'
  | 'files'
  | 'recovery'
  | 'audit'
  | 'platform'

interface NavEntry {
  id: ScreenId
  label: string
  hint: string
  icon: LucideIcon
}

/**
 * Grouped by the three things the tool does - sanitize, recover, prove - in
 * the order an investigation happens. A group label names the verb, not a
 * section of the codebase.
 */
const NAV: { group: string; items: NavEntry[] }[] = [
  {
    group: '',
    items: [
      { id: 'home', label: 'Overview', hint: 'The chain of custody, the three modules, the open case', icon: LayoutDashboard },
      { id: 'cases', label: 'Cases', hint: 'Evidence, operations, reports, audit', icon: FolderKanban },
    ],
  },
  {
    group: 'Sanitize',
    items: [
      { id: 'devices', label: 'Devices', hint: 'Enumerate and probe, read-only', icon: HardDrive },
      { id: 'sanitize', label: 'Drive eraser', hint: 'Capability-driven Clear or Purge of a whole drive', icon: ShieldX },
      { id: 'files', label: 'File & folder eraser', hint: 'Files, folders, free space, metadata', icon: FileX2 },
    ],
  },
  {
    group: 'Recover',
    items: [
      { id: 'recovery', label: 'Recovery', hint: 'Carve and undelete, read-only', icon: ScanSearch },
    ],
  },
  {
    group: 'Prove',
    items: [
      { id: 'audit', label: 'Audit', hint: 'Chain, signed reports, verification', icon: Blocks },
      { id: 'platform', label: 'Platform', hint: 'What this computer can do, and why', icon: Cpu },
    ],
  },
]

/** Two blocks and the link between them: one open, one sealed. */
function BrandMark() {
  return (
    <svg className="brand-mark" width="34" height="34" viewBox="0 0 32 32" aria-hidden>
      <rect x="1" y="1" width="30" height="30" rx="8" fill="var(--seal-surface)" stroke="var(--seal-rule)" />
      <rect x="5.5" y="11.5" width="8" height="8" rx="2" fill="none" stroke="var(--seal)" strokeWidth="1.8" />
      <rect x="18.5" y="11.5" width="8" height="8" rx="2" fill="var(--seal)" />
      <path d="M13.5 15.5 H18.5" stroke="var(--seal)" strokeWidth="1.8" />
    </svg>
  )
}

/** One claim in the status bar: a muted label, and the value in its state. */
function Pill({
  label,
  value,
  tone,
  title,
}: {
  label: string
  value: string
  tone: string
  title?: string
}) {
  return (
    <span className={`state-mark is-${tone}`} title={title ?? `${label}: ${value}`}>
      <span className="pill-label">{label}</span>
      {value}
    </span>
  )
}

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
function StatusStrip({
  selected,
  refreshKey,
}: {
  selected: DeviceRow | null
  /** Changes on every navigation, so the chain line is re-read, not remembered. */
  refreshKey: string
}) {
  const [chain, setChain] = useState<{ status: string; entry_count: number } | null>(
    null,
  )
  const [platform, setPlatform] = useState<PlatformStatus | null>(null)

  useEffect(() => {
    void api.platform().then(setPlatform).catch(() => setPlatform(null))
  }, [])
  useEffect(() => {
    void api
      .ledgerVerify()
      .then((answer) =>
        setChain({ status: answer.status, entry_count: answer.entry_count }),
      )
      .catch(() => setChain(null))
  }, [refreshKey])

  const device = selected?.normalized
  const assessment = selected?.assessment
  const sanitization = assessment
    ? assessment.headline === 'READY'
      ? statusWord(assessment.status)
      : { word: assessment.headline.toLowerCase(), tone: assessment.headline === 'NOT AUTHORIZED' ? 'warning' : 'destructive' }
    : null
  const verification = assessment?.recommended?.verification

  const chainTone =
    chain?.status === 'VALID'
      ? 'seal'
      : chain?.status === 'INCONCLUSIVE_TAIL' || chain?.status === 'INCOMPLETE_TAIL'
        ? 'warning'
        : chain
          ? 'destructive'
          : 'unknown'
  const osName = (platform?.platform.os_name ?? 'not read').replace(/\s*\(.*\)$/, '')

  return (
    <div className="status-strip" aria-label="Status">
      <Pill label="Platform" value={osName} tone="unknown" title={platform?.platform.os_build} />
      <Pill
        label="Privilege"
        value={privilegeWord(platform?.privilege ?? null)}
        tone={
          platform?.privilege.elevated || platform?.privilege.helper === 'socket'
            ? 'success'
            : 'warning'
        }
        title={platform?.privilege.basis}
      />
      <Pill
        label="Device"
        value={device ? `${deviceKind(device)} ${bytes(device.capacity_bytes)}` : 'none'}
        tone="unknown"
        title={device?.path}
      />
      <Pill
        label="Sanitization"
        value={sanitization?.word ?? 'no device'}
        tone={sanitization?.tone ?? 'unknown'}
        title={assessment?.reason}
      />
      <Pill
        label="Verification"
        value={verification ? 'available' : assessment ? 'not available' : 'no device'}
        tone={verification ? 'success' : 'unknown'}
        title={verification}
      />
      <Pill
        label="Chain"
        value={chain?.status === 'VALID' ? `valid (${chain.entry_count})` : (chain?.status.toLowerCase() ?? 'not read')}
        tone={chainTone}
        title={chain ? `${chain.entry_count} chain entries` : 'chain not read'}
      />
      <Pill label="Evidence" value="read-only" tone="success" title="core/carve never opens O_RDWR" />
    </div>
  )
}

function Shell() {
  const [screen, setScreen] = useState<ScreenId>('home')
  const [selected, setSelected] = useState<DeviceRow | null>(null)
  const [health, setHealth] = useState<Record<string, unknown> | null>(null)
  const { openCase } = useCase()

  useEffect(() => {
    void api.health().then(setHealth).catch(() => setHealth(null))
  }, [])
  // Each screen opens at its top. The scroll container is shared, so without
  // this a screen opened from the bottom of another started half-way down.
  useEffect(() => {
    document.querySelector('.main')?.scrollTo(0, 0)
  }, [screen])
  const buildCommit = String(
    (health?.build as { commit?: string } | undefined)?.commit ?? '',
  )

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Sanctum">
        <div className="brand">
          <BrandMark />
          <div className="col" style={{ gap: 2 }}>
            <span className="brand-name">Sanctum</span>
            <span className="brand-sub">Sanitize, recover, prove</span>
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
          <span className="case-badge-label">Case</span>
          <span className="case-badge-id">
            {openCase ? openCase.case_id : 'none open'}
          </span>
        </button>

        <div className="nav">
          {NAV.map((section) => (
            <div key={section.group || 'top'} className="col" style={{ gap: 2 }}>
              {section.group && <span className="nav-group">{section.group}</span>}
              {section.items.map((item) => {
                const Icon = item.icon
                return (
                  <button
                    key={item.id}
                    className={screen === item.id ? 'nav-item active' : 'nav-item'}
                    aria-current={screen === item.id ? 'page' : undefined}
                    onClick={() => setScreen(item.id)}
                    title={item.hint}
                  >
                    <Icon className="icon" size={18} aria-hidden />
                    {item.label}
                  </button>
                )
              })}
            </div>
          ))}
        </div>

        <div className="sidebar-foot">
          <span>{(health?.tool_version as string) ?? 'offline'}</span>
          {buildCommit && (
            <span className="mono" title={buildCommit}>
              build {buildCommit.slice(0, 12)}
            </span>
          )}
          <span>Listening on 127.0.0.1 only</span>
          {selected && <span className="mono" title={selected.device.path}>Selected {selected.device.path}</span>}
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
        </div>
      </nav>

      <main className="main">
        {screen === 'home' && <Home onOpen={(target) => setScreen(target)} />}
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
        <StatusStrip selected={selected} refreshKey={screen} />
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
