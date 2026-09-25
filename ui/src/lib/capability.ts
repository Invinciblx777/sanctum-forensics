// The per-device capability verdict on the Devices screen. Pure, and free of
// React, so `node --test` runs it: see ui/tests/capability.test.ts.

import type { Capabilities, DeviceAssessment } from './api'
import type { Tone } from '../components/widgets'
import { runnableStatus } from './platform.ts'

/**
 * The capability verdict.
 *
 * It states what the drive can actually deliver, derived from what was probed
 * rather than from what was requested. "Clear only" is not a downgrade the UI
 * chose - it is what the hardware reported, and saying "Purge" over a device
 * that cannot purge is the single most dangerous thing this screen could do.
 *
 * Three strings, not one: the word, the probe result it was derived from, and
 * the full sentence the operator opens when a judge asks. `Verdict` renders
 * the first two and the sub-row carries the third; see the component's note in
 * components/widgets.tsx for why none of it lives behind a hover.
 */
export function capabilityBadge(
  caps: Capabilities | null,
  assessment?: DeviceAssessment | null,
): {
  label: string
  tone: Tone
  basis: string
  why: string
} {
  // The drive reporting a firmware command is not evidence the Purge path
  // works. Only the server's option status carries that: anything but a
  // runnable status - including no assessment at all - reads UNVERIFIED.
  const purge = [
    assessment?.recommended,
    ...(assessment?.alternatives ?? []),
    ...(assessment?.unavailable ?? []),
  ].find((option) => option?.level === 'PURGE')
  const purgeVerified = runnableStatus(purge?.status)
  const unverified =
    ' UNVERIFIED: no firmware sanitize has been run on a physical drive by ' +
    'this project; the path is fixture-tested only.'
  if (!caps) {
    return {
      label: 'Not probed',
      tone: 'unknown',
      basis: 'probe did not complete',
      why: 'The capability probe did not complete, so nothing is claimed.',
    }
  }
  if (caps.is_sed_opal) {
    // Opal supports a cryptographic erase; Pyrite is the same command set
    // *without* it, and calling both "SED" would imply a capability half of
    // them do not have.
    const cryptoCapable =
      caps.achievable_levels.includes('PURGE') ||
      caps.ata_sanitize_ops.includes('CRYPTO_SCRAMBLE_EXT')
    return cryptoCapable
      ? purgeVerified
        ? {
            label: 'SED · OPAL',
            tone: 'success',
            basis: 'crypto erase available',
            why: 'Self-encrypting drive with a usable cryptographic erase.',
          }
        : {
            label: 'SED · OPAL · UNVERIFIED',
            tone: 'unknown',
            basis: 'crypto erase reported · not hardware-validated',
            why: 'Self-encrypting drive that reports a cryptographic erase.' + unverified,
          }
      : {
          label: 'SED · PYRITE',
          tone: 'warning',
          basis: 'no crypto erase',
          why:
            'Pyrite implements the Opal command set without media encryption, ' +
            'so there is no key to destroy and a crypto erase would erase nothing.',
        }
  }
  if (caps.achievable_levels.includes('PURGE')) {
    const ops = caps.ata_sanitize_ops.join(', ') || 'NVMe SANICAP'
    return purgeVerified
      ? {
          label: 'PURGE AVAILABLE',
          tone: 'success',
          basis: ops,
          why: `Firmware sanitize reported: ${ops}.`,
        }
      : {
          label: 'PURGE · UNVERIFIED',
          tone: 'unknown',
          basis: `${ops} reported · not hardware-validated`,
          why: `Firmware sanitize reported: ${ops}.` + unverified,
        }
  }
  return {
    label: 'CLEAR ONLY',
    tone: 'warning',
    basis: 'no firmware sanitize reported',
    why:
      'No firmware sanitize or cryptographic erase was reported, so a host ' +
      'overwrite is the strongest available result. On flash media that ' +
      'leaves remapped and over-provisioned blocks untouched.',
  }
}
