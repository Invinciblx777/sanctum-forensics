/**
 * Whether a job is a simulation, read from the job the server ran.
 *
 * The form's dry-run toggle is not the answer: an operator can flip it after a
 * job starts. The server records the flag it actually used in the job's
 * params, and only an explicit `false` there means the job wrote to storage.
 * A missing flag is a simulation, because every erase endpoint defaults to one.
 */
import type { JobStatus } from './api'

export const SIMULATION_BANNER = 'SIMULATION / NO PHYSICAL DEVICE MODIFIED'

export function isSimulation(status: JobStatus | null): boolean {
  if (!status) return false
  return status.params?.dry_run !== false
}
