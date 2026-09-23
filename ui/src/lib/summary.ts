/**
 * The one-screen executive summary of an open case, built only from what the
 * server recorded.
 *
 * Four questions, in the order a reviewer asks them. Every line is a count or a
 * status read from the case record, the chain, or the platform probe. A dry run
 * is never counted as an erasure: it is listed separately as a simulation that
 * wrote nothing. When nothing is recorded the line says so rather than
 * disappearing, because a missing line reads as "nothing to report".
 */
import type { CaseDetail, PlatformStatus } from './api'

export interface ExecutiveSummary {
  found: string[]
  erased: string[]
  verified: string[]
  unverified: string[]
}

const ERASE_KINDS: Record<string, string> = {
  'erase-drive': 'drive sanitization',
  'erase-files': 'file or folder erase',
  'wipe-free-space': 'free-space wipe',
}

function plural(count: number, noun: string): string {
  if (count === 1) return `${count} ${noun}`
  return noun.endsWith('y') ? `${count} ${noun.slice(0, -1)}ies` : `${count} ${noun}s`
}

export function executiveSummary(
  detail: CaseDetail | null,
  platform: PlatformStatus | null,
): ExecutiveSummary {
  const found: string[] = []
  const erased: string[] = []
  const verified: string[] = []
  const unverified: string[] = []

  if (!detail) {
    const none = 'No case is open. Open one on the Cases screen.'
    return { found: [none], erased: [none], verified: [none], unverified: platform?.limitations.slice(0, 3) ?? [] }
  }

  const ops = detail.operations
  found.push(plural(detail.evidence.length, 'evidence item') + ' registered')
  const carves = ops.filter((op) => op.type === 'carve' && op.status === 'complete')
  const recovered = carves.reduce((sum, op) => sum + (op.recovered_artifacts || 0), 0)
  if (carves.length) {
    found.push(
      `${plural(recovered, 'recovered artifact')} from ${plural(carves.length, 'completed recovery run')}`,
    )
  } else {
    found.push('No recovery run has completed in this case.')
  }

  const eraseOps = ops.filter((op) => op.type in ERASE_KINDS)
  const real = eraseOps.filter((op) => op.params?.dry_run === false)
  const simulated = eraseOps.filter((op) => op.params?.dry_run !== false)
  for (const [kind, label] of Object.entries(ERASE_KINDS)) {
    const done = real.filter((op) => op.type === kind && op.status === 'complete').length
    if (done) erased.push(`${plural(done, label)} completed`)
  }
  const failedErase = real.filter((op) => op.status === 'failed' || op.status === 'cancelled')
  if (failedErase.length) {
    erased.push(`${plural(failedErase.length, 'erase')} failed or cancelled; the target is partially sanitized`)
  }
  if (simulated.length) {
    erased.push(
      `${plural(simulated.length, 'dry run')}: SIMULATION, nothing was written`,
    )
  }
  if (!erased.length) erased.push('Nothing has been erased in this case.')

  const signed = detail.reports.filter((report) => report.signed).length
  verified.push(`${plural(signed, 'signed report')} of ${detail.reports.length}`)
  verified.push(
    `Audit chain ${detail.audit.chain_status} over ${plural(detail.audit.entry_count, 'entry')}`,
  )

  const unsigned = detail.reports.length - signed
  if (unsigned) unverified.push(`${plural(unsigned, 'report')} not signed`)
  if (detail.audit.chain_status !== 'VALID') {
    unverified.push(`The audit chain reads ${detail.audit.chain_status}: ${detail.audit.chain_explanation}`)
  }
  const running = ops.filter((op) => op.status === 'running').length
  if (running) unverified.push(`${plural(running, 'operation')} still running; its result is not in yet`)
  if (simulated.length) unverified.push('Dry runs prove the plan, not the erasure.')
  for (const item of platform?.limitations ?? []) unverified.push(item)
  if (!unverified.length) unverified.push('none recorded')

  return { found, erased, verified, unverified }
}
