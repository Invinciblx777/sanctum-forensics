// Formatting rules the whole interface shares.
//
// Byte counts are shown with their exact value alongside the rounded one
// wherever an operator might act on the number. "1.4 GB" is readable and
// "1502576640 bytes" is checkable, and a forensic report needs both.

export function bytes(count: number): string {
  if (!Number.isFinite(count)) return '—'
  if (count < 1024) return `${count} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB', 'PiB']
  let value = count / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 ? 2 : 1)} ${units[unit]}`
}

export function exactBytes(count: number): string {
  return `${count.toLocaleString('en-US')} bytes`
}

export function rate(bytesPerSecond: number): string {
  return bytesPerSecond > 0 ? `${bytes(bytesPerSecond)}/s` : '—'
}

export function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '—'
  const whole = Math.floor(seconds)
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor((whole % 3600) / 60)
  const rest = whole % 60
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`
  if (minutes > 0) return `${minutes}m ${String(rest).padStart(2, '0')}s`
  return `${rest}s`
}

/** Basis points to a percentage. 10000 bp is 100.00%. */
export function percent(basisPoints: number, decimals = 1): string {
  return `${(basisPoints / 100).toFixed(decimals)}%`
}

export function hex(offset: number): string {
  return `0x${offset.toString(16)}`
}

/** Middle-elided so both ends stay comparable; hashes are checked end-first. */
export function shortHash(value: string, head = 10, tail = 6): string {
  if (!value || value.length <= head + tail + 1) return value
  return `${value.slice(0, head)}…${value.slice(-tail)}`
}

export function timestamp(iso: string | null): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').replace(/\.\d+/, '').replace('+00:00', 'Z')
}

export type Severity = 'HIGH' | 'MEDIUM' | 'LOW'

export function severityClass(severity: string): string {
  return severity.toLowerCase()
}
