import { Fragment, useState } from 'react'
import type { ReactNode } from 'react'
import { bytes, duration, exactBytes, percent, rate, shortHash } from '../lib/format'
import type { Progress } from '../lib/api'

export function Panel({
  title,
  subtitle,
  actions,
  children,
  tight,
}: {
  title?: string
  /** What a column header would have to truncate to say. Goes here instead. */
  subtitle?: string
  actions?: ReactNode
  children: ReactNode
  tight?: boolean
}) {
  return (
    <section className="panel">
      {title && (
        <div className="panel-head">
          <h2>{title}</h2>
          {subtitle && <span className="panel-sub">{subtitle}</span>}
          <div className="grow" />
          {actions}
        </div>
      )}
      <div className={tight ? 'panel-body tight' : 'panel-body'}>{children}</div>
    </section>
  )
}

/**
 * The three states any claim in this interface can be in, plus "not probed".
 *
 * Shared rather than per-screen: a capability level, a report check and a
 * carve confidence are different judgements, and they are all rendered with
 * the same three colours and the same left-edge rail so an operator learns the
 * vocabulary once.
 */
export type Tone = 'destructive' | 'warning' | 'success' | 'unknown'

/**
 * A judgement the tool has made, and the thing it was derived from.
 *
 * `level` is the word - it carries the meaning on its own, so the component
 * survives greyscale and a projector with the reds crushed out. `basis` is
 * printed under it always, never behind a hover: a tooltip is invisible to a
 * room. Pass `onToggle` where there is a fuller explanation, and render it in
 * a sub-row rather than growing this cell.
 */
export function Verdict({
  level,
  basis,
  tone = 'unknown',
  tight = false,
  open,
  onToggle,
}: {
  level: string
  basis?: string
  tone?: Tone
  /** One line instead of two. Only for tables that run to hundreds of rows. */
  tight?: boolean
  open?: boolean
  onToggle?: () => void
}) {
  const className = `verdict is-${tone}${tight ? ' is-tight' : ''}`
  const body = (
    <>
      <span className="verdict-level">{level}</span>
      {basis && (
        <span className="verdict-basis">
          {onToggle && (open ? '\u2212 ' : '+ ')}
          {basis}
        </span>
      )}
    </>
  )
  if (!onToggle) return <span className={className}>{body}</span>
  return (
    <button
      type="button"
      className={className}
      aria-expanded={open}
      onClick={(event) => {
        event.stopPropagation()
        onToggle()
      }}
    >
      {body}
    </button>
  )
}

/**
 * The instrument table's state gutter, for the things that are not tables.
 *
 * A report check and a residual-risk panel carry state the same way a device
 * row does, and putting it anywhere but the left edge means it has to be
 * hunted for.
 */
export function Railed({
  tone = 'unknown',
  children,
}: {
  tone?: Tone
  children: ReactNode
}) {
  return (
    <div className={`railed is-${tone}`}>
      <i aria-hidden />
      <div className="railed-body">{children}</div>
    </div>
  )
}

/**
 * One label and one value.
 *
 * `kind` picks the monospace treatment the value needs - a path, a hash, a
 * serial - so a caller never has to hand-build a span and the same value type
 * is rendered the same way on every screen.
 */
export interface EvidenceRow {
  label: string
  value: ReactNode
  kind?: 'path' | 'hash' | 'serial' | 'mono'
  /** The exact value, where the visible one is rounded or elided. */
  title?: string
}

/**
 * Label/value evidence. `stacked` for a side panel or a dialog, where two
 * columns leave the label ellipsised into uselessness.
 */
export function Evidence({
  rows,
  stacked = false,
}: {
  rows: EvidenceRow[]
  stacked?: boolean
}) {
  return (
    <dl className={stacked ? 'evidence stacked' : 'evidence'}>
      {rows.map((row) => (
        <Fragment key={row.label}>
          <dt>{row.label}</dt>
          <dd className={row.kind} title={row.title}>
            {row.value}
          </dd>
        </Fragment>
      ))}
    </dl>
  )
}

/**
 * A filesystem path in a fixed-width cell.
 *
 * A cell that ellipsises loses its tail, and the tail of a path is the
 * filename - the one part that identifies the row. This drops the directory
 * instead: it is dimmed, it shrinks, and it ellipsises, while the basename is
 * never allowed to lose a character.
 */
export function FilePath({ value }: { value: string }) {
  const cut = value.lastIndexOf('/')
  const directory = cut > 0 ? value.slice(0, cut + 1) : ''
  const name = cut > 0 ? value.slice(cut + 1) : value
  return (
    <span className="filepath" title={value}>
      {directory && <span className="filepath-dir">{directory}</span>}
      <span className="filepath-base">{name}</span>
    </span>
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

/**
 * A job id, selectable in one click and copyable in another.
 *
 * The Audit screen asks for this id to generate a report. Retyping a
 * `carve-<hex>` string by hand in front of an audience is how a report ends up
 * generated for the wrong job, or for none.
 */
export function JobId({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className="row" style={{ gap: 'var(--space-2)', alignItems: 'center' }}>
      <span className="mono" style={{ userSelect: 'all' }}>
        {value}
      </span>
      <button className="btn" onClick={() => void copy()}>
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
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
    <div className="col tight">
      <div className={destructive ? 'meter destructive' : 'meter'}>
        <span style={{ width: `${Math.min(progress.pct_bp / 100, 100)}%` }} />
      </div>
      <div className="row wrap" style={{ gap: 'var(--space-6)' }}>
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
      <div className="mono" style={{ color: 'var(--text-secondary)' }}>
        {progress.message}
      </div>
    </div>
  )
}

export function Limitations({ items }: { items: string[] }) {
  if (items.length === 0) return null
  return (
    <div className="notice warn">
      <strong
        style={{ fontSize: 'var(--type-sm)', letterSpacing: '0.06em' }}
      >
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
        <p style={{ margin: 'var(--space-2) 0 0', color: '#f0c9c5' }}>
          {error.remediation}
        </p>
      )}
    </div>
  )
}

/**
 * Shown on every screen that displays a simulated job. The words are fixed so a
 * screenshot of a dry run can never be mistaken for a destructive run.
 */
export function SimulationBanner() {
  return (
    <div className="simulation-banner" role="status">
      SIMULATION / NO PHYSICAL DEVICE MODIFIED
    </div>
  )
}
