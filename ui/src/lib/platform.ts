// Plain-language words for the platform model. Pure functions, unit-tested in
// ui/tests/platform.test.ts. Every word here is derived from a status the
// server computed; nothing is asserted by this file.

import type {
  CapabilityStatus,
  DeviceAssessment,
  NormalizedDevice,
  PrivilegeState,
  SafetyCheck,
} from './api'
import type { Tone } from '../components/widgets'

/** One word a judge can read, and the tone it is drawn in. */
export function statusWord(status: CapabilityStatus): { word: string; tone: Tone } {
  switch (status) {
    case 'SUPPORTED':
      return { word: 'Supported', tone: 'success' }
    case 'SUPPORTED_WITH_LIMITATIONS':
      return { word: 'Supported with limits', tone: 'warning' }
    case 'NOT_AUTHORIZED':
      return { word: 'Needs privilege', tone: 'warning' }
    case 'NOT_VERIFIABLE':
      return { word: 'Runs, not verifiable', tone: 'warning' }
    case 'UNVERIFIED':
      return { word: 'Unverified', tone: 'unknown' }
    case 'INCONCLUSIVE':
      return { word: 'Inconclusive', tone: 'unknown' }
    case 'UNSUPPORTED':
      return { word: 'Unsupported', tone: 'destructive' }
  }
}

/** Whether an option can actually be started on this host. */
export function runnableStatus(status: CapabilityStatus | undefined): boolean {
  return status === 'SUPPORTED' || status === 'SUPPORTED_WITH_LIMITATIONS'
}

export function privilegeWord(privilege: PrivilegeState | null): string {
  if (!privilege) return 'Unknown'
  if (privilege.helper === 'socket') return 'Privileged helper'
  switch (privilege.level) {
    case 'root':
      return 'Root'
    case 'administrator':
      return 'Administrator'
    case 'standard':
      return 'Standard user'
    default:
      return 'Unknown'
  }
}

const INTERFACE_WORDS: Record<string, string> = {
  usb: 'USB',
  nvme: 'NVMe',
  sata: 'SATA',
  mmc: 'SD / memory card',
  sas: 'SAS',
  scsi: 'SCSI',
  thunderbolt: 'Thunderbolt',
  virtual: 'Virtual disk',
}

const MEDIA_WORDS: Record<string, string> = {
  hdd: 'Hard disk',
  ssd: 'SSD',
  flash: 'Flash',
  unknown: 'Unknown medium',
}

/** "External USB flash", "Internal NVMe SSD" - no storage vocabulary needed. */
export function deviceKind(device: NormalizedDevice): string {
  const place =
    device.removable === true ? 'External' : device.removable === false ? 'Internal' : ''
  const bus = INTERFACE_WORDS[device.interface] ?? ''
  const medium = MEDIA_WORDS[device.media_type] ?? 'Unknown medium'
  return [place, bus, medium].filter(Boolean).join(' ')
}

export function deviceName(device: NormalizedDevice): string {
  return [device.vendor, device.model].filter(Boolean).join(' ') || device.id
}

/** The eight steps, in order. The sequence is real: each gates the next. */
export const STEPS = [
  'Choose target',
  'Analyse',
  'Recommended method',
  'Review warning',
  'Confirm',
  'Sanitize',
  'Verify',
  'Certificate',
] as const

export interface FlowState {
  hasDevice: boolean
  hasAssessment: boolean
  reviewing: boolean
  confirming: boolean
  running: boolean
  finished: boolean
  verified: boolean
  certified: boolean
}

/** Index of the step the operator is on (0-based). */
export function currentStep(state: FlowState): number {
  if (!state.hasDevice) return 0
  if (!state.hasAssessment) return 1
  if (state.certified) return 7
  if (state.finished) return state.verified ? 7 : 6
  if (state.running) return 5
  if (state.confirming) return 4
  if (state.reviewing) return 3
  return 2
}

/** A check that could not be decided is never counted as passed. */
export function checkWord(check: SafetyCheck): { word: string; tone: Tone } {
  if (check.passed === true) return { word: 'yes', tone: 'success' }
  if (check.passed === false) return { word: 'no', tone: 'destructive' }
  return { word: 'unknown', tone: 'unknown' }
}

/** Tone of the whole assessment headline. */
export function headlineTone(assessment: DeviceAssessment | null | undefined): Tone {
  if (!assessment) return 'unknown'
  if (assessment.headline === 'READY') {
    return assessment.status === 'SUPPORTED' ? 'success' : 'warning'
  }
  if (assessment.headline === 'NOT AUTHORIZED') return 'warning'
  return 'destructive'
}
