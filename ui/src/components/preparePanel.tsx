import { useState } from 'react'
import { api, RequestFailed } from '../lib/api'
import type { DeviceRow, PrepareDeviceResult } from '../lib/api'
import { preparable } from '../lib/states'
import { ErrorNotice } from './widgets'

/**
 * Unmount (macOS) or take offline (Windows) one disk, as its own step.
 *
 * An erase never unmounts anything: it refuses a mounted disk. This is the
 * separate, explicit action an operator takes first. It always shows the dry
 * run before it offers the real one, and the real one needs the serial typed
 * by hand. It writes nothing to the medium.
 */
export function PreparePanel({ row, onDone }: { row: DeviceRow; onDone: () => void }) {
  const device = row.normalized
  const [plan, setPlan] = useState<PrepareDeviceResult | null>(null)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ message: string; kind?: string; remediation?: string } | null>(
    null,
  )
  if (!device || !preparable(row)) return null
  const word = device.platform === 'windows' ? 'Take offline' : 'Unmount'

  async function run(dryRun: boolean) {
    if (!device) return
    setBusy(true)
    setError(null)
    try {
      const answer = await api.prepareDevice({
        path: device.id,
        dry_run: dryRun,
        typed_serial: dryRun ? '' : typed,
      })
      setPlan(answer)
      if (!dryRun && answer.performed) onDone()
    } catch (exc) {
      if (exc instanceof RequestFailed) {
        setError({ message: exc.message, kind: exc.kind, remediation: exc.remediation })
      } else {
        setError({ message: String(exc) })
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="prepare">
      <p>
        {word} this disk before a whole-drive operation. This is a separate step: an erase
        never unmounts anything, and this step writes nothing to the medium.
      </p>
      <button type="button" disabled={busy} onClick={() => run(true)}>
        Dry run: show what would be {device.platform === 'windows' ? 'taken offline' : 'unmounted'}
      </button>
      {plan && (
        <dl>
          <dt>Action</dt>
          <dd>{plan.action}</dd>
          <dt>Volumes</dt>
          <dd>{(plan.unmounts ?? []).join(', ') || 'none'}</dd>
          <dt>Performed</dt>
          <dd>{plan.performed ? 'yes' : 'no (dry run)'}</dd>
        </dl>
      )}
      {plan && !plan.performed && (
        <div>
          <label>
            Type the serial {device.serial || '(none reported)'} to confirm{' '}
            <input value={typed} onChange={(event) => setTyped(event.target.value)} />
          </label>
          <button type="button" disabled={busy || !typed} onClick={() => run(false)}>
            {word}
          </button>
        </div>
      )}
      <ErrorNotice error={error} />
    </div>
  )
}
