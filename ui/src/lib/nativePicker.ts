/**
 * The desktop window's native file and folder chooser.
 *
 * A browser file input never reveals where a file lives, so the desktop shell
 * (api/native_picker.py) injects `window.pywebview.api.pick`. It returns the
 * absolute paths the operator chose and does nothing else; the caller puts
 * them in a text field, where they meet the same validation as typed ones.
 * Outside the desktop window - the dev server, a plain browser - there is no
 * bridge and the caller falls back to typing.
 */

export type PickKind = 'file' | 'files' | 'folder'

interface PickerBridge {
  pick: (kind: PickKind) => Promise<unknown>
}

/** Just enough of `window` to find the bridge; a parameter so it can be faked. */
interface PickerHost {
  pywebview?: { api?: Partial<PickerBridge> }
}

export function nativePicker(host: PickerHost = globalThis as PickerHost): PickerBridge | null {
  const pick = host.pywebview?.api?.pick
  return typeof pick === 'function' ? { pick: pick.bind(host.pywebview?.api) } : null
}

/** The chosen paths; empty when cancelled, unavailable, or the dialog failed. */
export async function pickPaths(
  kind: PickKind,
  host: PickerHost = globalThis as PickerHost,
): Promise<string[]> {
  const bridge = nativePicker(host)
  if (!bridge) return []
  try {
    const chosen = await bridge.pick(kind)
    if (!Array.isArray(chosen)) return []
    return chosen.filter((item): item is string => typeof item === 'string' && item !== '')
  } catch {
    return []
  }
}
