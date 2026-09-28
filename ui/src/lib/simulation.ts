/**
 * Whether a job is a dry run, read from the job the server ran.
 *
 * The form's dry-run toggle is not the answer: an operator can flip it after a
 * job starts. The server records the flag it actually used in the job's
 * params, and only an explicit `false` there means the job wrote to storage.
 * A missing flag is a dry run, because every erase endpoint defaults to one.
 */
import type { JobStatus } from './api'

export const DRY_RUN_BANNER = 'DRY RUN / NO PHYSICAL DEVICE MODIFIED'

/** @deprecated Use {@link DRY_RUN_BANNER}. */
export const SIMULATION_BANNER = DRY_RUN_BANNER

export function isDryRun(status: JobStatus | null): boolean {
  if (!status) return false
  return status.params?.dry_run !== false
}

/** @deprecated Use {@link isDryRun}. */
export const isSimulation = isDryRun
