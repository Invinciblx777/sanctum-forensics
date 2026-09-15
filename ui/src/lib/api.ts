// The only module that talks to the server.
//
// Every URL here is same-origin and relative. There is no base URL constant to
// point somewhere else, no configurable host, and no fetch of anything the
// server did not serve - which is what makes the "no external origin" test a
// grep rather than an audit.

export interface Progress {
  job_id: string
  phase: string
  pct_bp: number
  bytes_done: number
  bytes_total: number
  throughput_bytes_per_sec: number
  eta_seconds: number
  message: string
}

export interface ApiError {
  error: string
  kind: string
  // Written by whoever implemented the operation and passed through verbatim.
  // Never reworded by the API or by this client.
  remediation: string
}

export class RequestFailed extends Error {
  readonly status: number
  readonly kind: string
  readonly remediation: string

  constructor(status: number, detail: ApiError) {
    super(detail.error)
    this.status = status
    this.kind = detail.kind
    this.remediation = detail.remediation
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    let detail: ApiError = {
      error: `${response.status} ${response.statusText}`,
      kind: 'HttpError',
      remediation: '',
    }
    try {
      const body = await response.json()
      detail = body.detail ?? body
    } catch {
      // A non-JSON error body is still an error; the status line stands in.
    }
    throw new RequestFailed(response.status, detail)
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<Record<string, unknown>>('/health'),

  devices: (includeVirtual = false) =>
    request<{ devices: DeviceRow[]; limitations: string[] }>(
      `/devices?include_virtual=${includeVirtual}`,
    ),

  eraseDrive: (body: EraseDriveBody) =>
    request<JobAccepted>('/jobs/erase-drive', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  eraseFiles: (body: EraseFilesBody) =>
    request<JobAccepted>('/jobs/erase-files', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  wipeFreeSpace: (body: WipeFreeSpaceBody) =>
    request<JobAccepted>('/jobs/wipe-free-space', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  acquire: (body: AcquireBody) =>
    request<JobAccepted>('/jobs/acquire', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  carve: (body: CarveBody) =>
    request<JobAccepted>('/jobs/carve', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  job: (jobId: string) => request<JobStatus>(`/jobs/${jobId}`),

  cancel: (jobId: string) =>
    request<JobStatus>(`/jobs/${jobId}/cancel`, { method: 'POST' }),

  ledgerVerify: () => request<LedgerVerification>('/ledger/verify'),

  ledgerEntries: (limit = 200) =>
    request<{ entries: LedgerEntry[]; entry_count: number }>(
      `/ledger/entries?limit=${limit}`,
    ),

  generateReport: (jobId: string, body: { case_id: string; operator: string }) =>
    request<ReportResult>(`/reports/${jobId}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  verifyReport: (jobId: string) =>
    request<ReportVerification>(`/reports/${jobId}/verify`),
}

// ---------------------------------------------------------------------------
// Live progress
// ---------------------------------------------------------------------------

export interface StreamHandlers {
  onProgress: (progress: Progress) => void
  onState: (state: JobStatus) => void
  onError?: (message: string) => void
}

/**
 * Attach to a job's SSE stream.
 *
 * The server replays the whole buffered run before following it live, so this
 * is also how a page reload recovers: reconnecting by job_id shows the progress
 * that happened while the browser was away rather than picking up blind from
 * wherever it reattached. Nothing here needs to know whether it is the first
 * connection or the third.
 */
export function streamJob(jobId: string, handlers: StreamHandlers): () => void {
  const source = new EventSource(`/jobs/${jobId}/stream`)

  source.addEventListener('progress', (event) => {
    handlers.onProgress(JSON.parse((event as MessageEvent).data) as Progress)
  })
  source.addEventListener('state', (event) => {
    const state = JSON.parse((event as MessageEvent).data) as JobStatus
    handlers.onState(state)
    // The server always ends with a state event, so closing here means the
    // browser never retries a finished job - EventSource would otherwise
    // reconnect on its own and re-replay the whole run forever.
    source.close()
  })
  source.addEventListener('error', () => {
    // EventSource reconnects by itself, so this is a notification and not a
    // failure. Only a job the server has forgotten ends the stream for good,
    // and that arrives as a state event first.
    handlers.onError?.('stream interrupted; the browser will retry')
  })

  return () => source.close()
}

// ---------------------------------------------------------------------------
// Shapes
// ---------------------------------------------------------------------------

export interface Device {
  path: string
  model: string
  serial: string
  size_bytes: number
  rotational: boolean
  transport: string
  is_system_disk: boolean
  mounted_at: string[]
  pt_type: string | null
  by_id_path: string | null
}

export interface Capabilities {
  ata_security_erase: boolean
  ata_enhanced_erase: boolean
  ata_sanitize_ops: string[]
  nvme_sanicap: Record<string, unknown>
  is_sed_opal: boolean
  security_frozen: boolean
  est_erase_seconds: number
  achievable_levels: string[]
  limitations: string[]
}

export interface HiddenAreaReport {
  hpa_present: boolean
  dco_present: boolean
  native_max_sectors: number
  accessible_sectors: number
  hidden_bytes: number
}

export interface DeviceRow {
  device: Device
  capabilities: Capabilities | null
  hidden_areas: HiddenAreaReport | null
  capability_error?: string
  hidden_area_error?: string
}

export interface JobAccepted {
  job_id: string
  kind: string
  state: string
  dry_run: boolean
  stream_url: string
}

export interface JobStatus {
  job_id: string
  kind: string
  state: string
  params: Record<string, unknown>
  progress_count: number
  dropped_progress: number
  latest: Progress | null
  result: Record<string, unknown> | null
  error: string | null
  error_kind: string | null
  remediation: string
  started_at: string
  finished_at: string | null
  cancel_requested: boolean
  ledger_entries?: LedgerEntry[]
}

export interface LedgerEntry {
  seq: number
  ts_utc: string
  actor: string
  operation: string
  params_hash: string
  result_hash: string
  prev_entry_hash: string
  entry_hash: string
}

export interface LedgerVerification {
  status: string
  entry_count: number
  explanation: string
  /** Absent, not null, when the ledger could not be read at all. */
  first_broken_seq?: number | null
  /** The ledger store directory. Not a Merkle root, and not a hash. */
  root?: string
  entries: LedgerEntry[]
}

export interface ReportResult {
  job_id: string
  json_path: string
  pdf_path: string
  pubkey_fingerprint: string
  /** SHA-256 of the JSON artifact, as recorded in the ledger at generation. */
  sha256: string
  bytes: number
}

export interface ReportCheck {
  name: string
  passed: boolean
  /**
   * False when the check could not run at all - no ledger store to compare
   * against, no excerpt to walk. An inapplicable check is not a passing one,
   * and the server sets `passed` true for these so that `passed` on the
   * verification means "nothing that could be checked failed". The screen has
   * to render the difference or it overstates what was confirmed.
   */
  applicable: boolean
  detail: string
}

export interface ReportVerification {
  report: string
  passed: boolean
  fingerprint: string
  /** The identity caveat, written by core and passed through verbatim. */
  caveat: string
  /** Digest the chain recorded when this report was generated. */
  ledger_digest: string
  /** Whether the file on disk is still those bytes. */
  ledger_digest_matches: boolean
  checks: ReportCheck[]
}

export interface CarveFlags {
  has_exif_gps: boolean
  is_password_protected: boolean
  is_encrypted: boolean
  contains_macros: boolean
  has_embedded_files: boolean
  is_signed: boolean
  inspected: boolean
}

export interface CarveFragment {
  offset: number
  length: number
}

export interface CarveCandidate {
  offset: number
  length: number
  ext: string
  mime: string
  source: string
  validation: string
  confidence_bp: number
  bucket: string
  sha256: string
  original_name: string | null
  possibly_fragmented: boolean
  /**
   * Image-absolute runs the object's content was reassembled from, in file
   * order. Empty for the ordinary case, where offset..offset+length is the
   * object. When it is not empty the digest is of these runs concatenated,
   * and offset+length spans a gap holding somebody else's bytes.
   */
  fragments: CarveFragment[]
  validation_detail: string
  entropy_millibits_per_byte: number | null
  high_entropy_windows_bp: number | null
  score_components: Record<string, number>
  overlapped: boolean
  overlaps_with: number | null
  category: string
  flags: CarveFlags
  duplicate_offsets: number[]
  fs_type: string
  contiguity_assumed: boolean
  contiguity_contradicted: boolean
}

export interface ResidualFinding {
  kind: string
  severity: string
  explanation: string
  addressable: boolean
  detail: Record<string, unknown>
}

export interface FileEraseRecord {
  path: string
  ok: boolean
  dry_run: boolean
  bytes_overwritten: number
  streams_removed: string[]
  xattrs_removed: string[]
  rename_chain: string[]
  unlinked: boolean
  is_directory: boolean
  findings: ResidualFinding[]
  limitations: string[]
  error: string | null
  error_kind: string | null
  verification: { passed: boolean | null; strategy: string; reason: string } | null
}

export interface EraseDriveBody {
  path: string
  level: string
  dry_run: boolean
  typed_serial: string
  case_id?: string
  operator?: string
}

export interface EraseFilesBody {
  paths: string[]
  dry_run: boolean
  confirm: boolean
  cleanse_metadata: boolean
  break_hardlinks: boolean
  recursive: boolean
}

export interface WipeFreeSpaceBody {
  mount_point: string
  dry_run: boolean
  typed_identifier: string
}

export interface FreeSpaceWipeResult {
  job_id: string
  dry_run: boolean
  volume: {
    mount_point: string
    fs_type: string
    source: string
    fs_uuid: string | null
    identifier: string
    trim_likely: boolean | null
  }
  fill_byte: number
  bytes_written: number
  filler_files: number
  free_bytes_before: number
  free_blocks_bytes_before: number
  free_bytes_at_full: number
  free_blocks_bytes_at_full: number
  free_bytes_after: number
  stopped_by: string
  filler_removed: boolean
  not_reached: string[]
  limitations: string[]
  verified: boolean | null
}

export interface AcquireBody {
  source: string
  dest: string
  fmt: string
  compression: string
}

export interface CarveBody {
  image: string
  undelete: boolean
  carve_signatures: boolean
  out_dir: string | null
}
