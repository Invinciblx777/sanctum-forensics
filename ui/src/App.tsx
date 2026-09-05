import { useEffect, useState } from 'react'
import type { DeviceRow } from './lib/api'
import { api } from './lib/api'
import Audit from './screens/Audit'
import Devices from './screens/Devices'
import FileEraser from './screens/FileEraser'
import Recovery from './screens/Recovery'
import Sanitize from './screens/Sanitize'

type ScreenId = 'devices' | 'sanitize' | 'files' | 'recovery' | 'audit'

const SCREENS: { id: ScreenId; label: string }[] = [
  { id: 'devices', label: 'Devices' },
  { id: 'sanitize', label: 'Sanitize' },
  { id: 'files', label: 'File eraser' },
  { id: 'recovery', label: 'Recovery' },
  { id: 'audit', label: 'Audit' },
]

export default function App() {
  const [screen, setScreen] = useState<ScreenId>('devices')
  const [selected, setSelected] = useState<DeviceRow | null>(null)
  const [health, setHealth] = useState<Record<string, unknown> | null>(null)

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

        <div className="nav">
          {SCREENS.map((item) => (
            <button
              key={item.id}
              className={screen === item.id ? 'nav-item active' : 'nav-item'}
              onClick={() => setScreen(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="sidebar-foot">
          <span>{(health?.tool_version as string) ?? 'offline'}</span>
          <span>127.0.0.1 only</span>
          {selected && <span title={selected.device.path}>▸ {selected.device.path}</span>}
        </div>
      </nav>

      <main className="main">
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
      </main>
    </div>
  )
}
