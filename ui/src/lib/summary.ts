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
import { statusWord } from './platform.ts'
import { isSafetyRefusal } from './refusal.ts'

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
  requestFailed = '',
): ExecutiveSummary {
  const found: string[] = []
  const erased: string[] = []
  const verified: string[] = []
  const unverified: string[] = []

  if (!detail) {
    // A case that could not be read is not a case with nothing in it.
    const none = requestFailed
      ? `REQUEST FAILED - the open case could not be read (${requestFailed}). Nothing is inferred about it.`
      : 'No case is open. Open one on the Cases screen.'
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
    const done = real.filter(
      (op) => op.type === kind && op.status === 'complete' && op.verification_passed !== false,
    ).length
    if (done) erased.push(`${plural(done, label)} completed`)
  }
  // Four outcomes that must not share a line. A safety refusal wrote nothing;
  // a failed or stopped erase may have written part of the target; a run that
  // ended with a failed read-back is not verified sanitized.
  const verifyFailed = real.filter(
    (op) => op.status === 'complete' && op.verification_passed === false,
  )
  if (verifyFailed.length) {
    erased.push(
      `${plural(verifyFailed.length, 'erase')} ran but read-back verification FAILED; the target is not verified sanitized`,
    )
  }
  const refused = real.filter((op) => op.status === 'failed' && isSafetyRefusal(op.error_kind))
  if (refused.length) {
    erased.push(
      `${plural(refused.length, 'erase')} BLOCKED by a safety refusal before any write; nothing was erased`,
    )
  }
  const failedErase = real.filter((op) => op.status === 'failed' && !isSafetyRefusal(op.error_kind))
  if (failedErase.length) {
    erased.push(
      `${plural(failedErase.length, 'erase')} FAILED; the target may be partly overwritten and is not sanitized`,
    )
  }
  const stopped = real.filter((op) => op.status === 'cancelled')
  if (stopped.length) {
    erased.push(
      `${plural(stopped.length, 'erase')} stopped on request (CANCELLED) before finishing; the target may be partly overwritten and is not sanitized`,
    )
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

/**
 * The six questions a judge asks, answered on one screen.
 *
 * The case-dependent lines come from {@link executiveSummary}, the capability
 * lines from the platform probe and the chain line from the server's own
 * verification. The fixed lines state design facts that the tests and the
 * validation record back, and the limitations always lead with what has not
 * been run on physical hardware: that list does not shrink because a case
 * happens to be empty.
 */
export interface JudgeSummary {
  erasure: string[]
  recovery: string[]
  verification: string[]
  integrity: string[]
  safety: string[]
  limitations: string[]
}

/** Not validated on a physical device, from docs/validation/feature-matrix.md. */
export const NOT_PHYSICALLY_VALIDATED: readonly string[] = [
  'This release: no physical validation. Every physical run on record (2026-09-05, 2026-09-23) used an earlier build.',
  'Registered physical carve benchmark: not run. Benchmark figures are SYNTHETIC (three physical recovery passes are recorded separately).',
  'Firmware Purge (ATA/NVMe sanitize, crypto erase): fixture-tested, never run on a drive.',
  'HPA/DCO unlock: not run on hardware.',
  'Backup restoration before a destructive write: never validated.',
  'Whole-drive sanitization: Linux only.',
]

export const SAFETY_LINES: readonly string[] = [
  'Dry run is the default. Nothing is written unless it is turned off.',
  'A real erase needs a backup image, an approval with the typed serial, and a one-use authorization the server issues; the helper re-checks device, plan and backup before it writes.',
  'The system disk and any device with a mounted filesystem are refused, never unmounted for you.',
  'No automatic sudo, no automatic unmount, and no substitute device when the named one is missing.',
]

function capabilityLine(platform: PlatformStatus | null, operation: string, name: string): string {
  const row = platform?.operations.find((item) => item.operation === operation)
  if (!row) return `${name}: not probed on this host.`
  return `${name}: ${statusWord(row.status).word}.`
}

export function judgeSummary(
  detail: CaseDetail | null,
  platform: PlatformStatus | null,
  chainStatus: string,
  requestFailed = '',
): JudgeSummary {
  const base = executiveSummary(detail, platform, requestFailed)
  const caseLines = detail || requestFailed
  return {
    erasure: [
      capabilityLine(platform, 'whole_drive_clear', 'Whole-drive Clear'),
      capabilityLine(platform, 'whole_drive_purge', 'Whole-drive Purge'),
      'The method is selected from the drive\'s probed capability, never from operator preference.',
      'Physically run on 2026-09-05, on an earlier build and not repeated for this release: overwrite Clear on one 7.76 GB USB flash stick, one clean recorded run after two defective ones.',
      ...(caseLines ? base.erased : []),
    ],
    recovery: [
      ...base.found,
      'Evidence score: a sum of six evidence components, not a probability.',
      'Fragmented reassembly: baseline JPEG and PNG, exactly two runs.',
    ],
    verification: [
      ...(caseLines ? base.verified : []),
      'Erase: read-back of the medium, full read up to 64 GiB, seeded sample above.',
      'Report: five independent checks and a graded verdict.',
    ],
    integrity: [
      `Audit chain: ${chainStatus}.`,
      'Each ledger entry holds the SHA-256 of the one before it.',
      'Reports are Ed25519-signed; changing one field makes verification fail.',
      'The embedded key proves the report was not altered, not who signed it.',
    ],
    safety: [...SAFETY_LINES],
    limitations: [
      ...NOT_PHYSICALLY_VALIDATED,
      ...base.unverified.filter((line) => !NOT_PHYSICALLY_VALIDATED.includes(line) && line !== 'none recorded'),
    ],
  }
}
