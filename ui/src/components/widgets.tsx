import type { ReactNode } from 'react'
import { bytes, duration, exactBytes, percent, rate, shortHash } from '../lib/format'
import type { Progress } from '../lib/api'

export function Panel({
  title,
  actions,
  children,
  tight,
}: {
  title?: string
  actions?: ReactNode
  children: ReactNode
  tight?: boolean
}) {
  return (
    <section className="panel">
      {title && (
        <div className="panel-head">
          <h2>{title}</h2>
          {actions}
        </div>
      )}
      <div className={tight ? 'panel-body tight' : 'panel-body'}>{children}</div>
    </section>
  )
}

export function Chip({
  tone = 'muted',
  title,
  children,
}: {
  tone?: 'high' | 'medium' | 'low' | 'accent' | 'muted'
  title?: string
  children: ReactNode
}) {
  return (
    <span className={`chip ${tone}`} title={title}>
      {children}
    </span>
  )
}

export function Notice({
  tone,
  children,
}: {
  tone: 'warn' | 'danger' | 'ok' | 'info'
  children: ReactNode
}) {
  return <div className={`notice ${tone}`}>{children}</div>
}

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  )
}

export function Hash({ value }: { value: string }) {
  return (
    <span className="hash" title={value}>
      {shortHash(value)}
    </span>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

/**
 * Live progress with throughput and ETA.
 *
 * `destructive` switches the bar red. A wipe in flight and a read-only
 * acquisition in flight must not look the same, because glancing at the wrong
 * one and hitting cancel has very different costs.
 */
export function ProgressView({
  progress,
  destructive = false,
}: {
  progress: Progress | null
  destructive?: boolean
}) {
  if (!progress) {
    return <div className="empty">No progress reported yet.</div>
  }
  return (
    <div className="col" style={{ gap: 8 }}>
      <div className={destructive ? 'meter destructive' : 'meter'}>
        <span style={{ width: `${Math.min(progress.pct_bp / 100, 100)}%` }} />
      </div>
      <div className="row wrap" style={{ gap: 22 }}>
        <Stat label="phase" value={progress.phase} />
        <Stat label="complete" value={percent(progress.pct_bp, 2)} />
        <Stat
          label="written"
          value={
            <span title={exactBytes(progress.bytes_done)}>
              {bytes(progress.bytes_done)}
              {progress.bytes_total > 0 && ` / ${bytes(progress.bytes_total)}`}
            </span>
          }
        />
        <Stat label="throughput" value={rate(progress.throughput_bytes_per_sec)} />
        <Stat label="eta" value={duration(progress.eta_seconds)} />
      </div>
      <div className="mono" style={{ color: 'var(--fg-dim)' }}>
        {progress.message}
      </div>
    </div>
  )
}

export function Limitations({ items }: { items: string[] }) {
  if (items.length === 0) return null
  return (
    <div className="notice warn">
      <strong style={{ fontSize: 11, letterSpacing: '0.06em' }}>
        WHAT THIS RUN COULD NOT GUARANTEE
      </strong>
      <ul className="limitations">
        {items.map((item, index) => (
          <li key={index}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

/**
 * An error, with the remediation the core layer wrote.
 *
 * The remediation is rendered as prominently as the error itself: it is the
 * half the operator can act on, and burying it under the failure message
 * turns an instruction into a footnote.
 */
export function ErrorNotice({
  error,
}: {
  error: { message: string; kind?: string; remediation?: string } | null
}) {
  if (!error) return null
  return (
    <div className="notice danger">
      <div className="row spread">
        <strong>{error.message}</strong>
        {error.kind && <span className="chip high">{error.kind}</span>}
      </div>
      {error.remediation && (
        <p style={{ margin: '7px 0 0', color: '#f0c9c5' }}>{error.remediation}</p>
      )}
    </div>
  )
}
