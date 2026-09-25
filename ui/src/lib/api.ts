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
  // Present on a workflow-gate refusal (api/authorization.py:GateRefused).
  verdict?: string
  workflow_state?: string
  'WHY BLOCKED'?: string[]
  physical_device_modified?: boolean
}

export class RequestFailed extends Error {
  readonly status: number
  readonly kind: string
  readonly remediation: string
  readonly verdict: string
  readonly workflowState: string
  readonly whyBlocked: string[]
  /** Only an explicit `false` from the server means nothing was written. */
  readonly physicalDeviceModified: boolean | null

  constructor(status: number, detail: ApiError) {
    super(detail.error)
    this.status = status
    this.kind = detail.kind
    this.remediation = detail.remediation
    this.verdict = detail.verdict ?? ''
    this.workflowState = detail.workflow_state ?? ''
    this.whyBlocked = detail['WHY BLOCKED'] ?? []
    this.physicalDeviceModified = detail.physical_device_modified ?? null
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

  platform: () => request<PlatformStatus>('/platform'),

  /** Stops a launcher-started app; the server refuses it otherwise. */
  quit: () => request<{ quitting: boolean }>('/app/quit', { method: 'POST' }),

  /** Re-reads one device from the OS now; never a cached row. */
  assessment: (deviceId: string) =>
    request<{ normalized: NormalizedDevice; assessment: DeviceAssessment }>(
      `/platform/devices/${encodeURIComponent(deviceId)}/assessment`,
    ),

  devices: (includeVirtual = false) =>
    request<{ devices: DeviceRow[]; limitations: string[] }>(
      `/devices?include_virtual=${includeVirtual}`,
    ),

  eraseDrive: (body: EraseDriveBody) =>
    request<JobAccepted>('/jobs/erase-drive', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** Opens the authorization record: fresh probe, backup image hashed read-only. */
  openEraseWorkflow: (body: OpenEraseWorkflowBody) =>
    request<EraseWorkflowView>('/workflow/erase-drive', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  eraseWorkflow: (authorizationId: string) =>
    request<EraseWorkflowView>(
      `/workflow/erase-drive/${encodeURIComponent(authorizationId)}`,
    ),

  /** Records a person's approval. The only call that can create one. */
  approveErase: (authorizationId: string, body: ApproveEraseBody) =>
    request<EraseWorkflowView>(
      `/workflow/erase-drive/${encodeURIComponent(authorizationId)}/approve`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

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

  generateReport: (
    jobId: string,
    body: { case_id: string; operator: string; key_passphrase?: string },
  ) =>
    request<ReportResult>(`/reports/${jobId}`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  verifyReport: (jobId: string) =>
    request<ReportVerification>(`/reports/${jobId}/verify`),

  // -- cases ----------------------------------------------------------------

  cases: () => request<{ cases: CaseSummary[] }>('/cases'),

  createCase: (body: { case_id: string; title: string; description: string }) =>
    request<{ case: CaseSummary }>('/cases', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  case: (caseId: string) =>
    request<CaseDetail>(`/cases/${encodeURIComponent(caseId)}`),

  registerEvidence: (caseId: string, body: EvidenceBody) =>
    request<{ evidence: EvidenceRecord }>(
      `/cases/${encodeURIComponent(caseId)}/evidence`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  // -- artifacts ------------------------------------------------------------

  artifacts: (root: 'recovered' | 'reports') =>
    request<{ root: string; count: number; artifacts: ArtifactRef[] }>(
      `/artifacts/${root}`,
    ),

  // -- audit ----------------------------------------------------------------

  tamperDemo: (seq?: number) =>
    request<TamperDemo>('/ledger/tamper-demo', {
      method: 'POST',
      body: JSON.stringify(seq === undefined ? {} : { seq }),
    }),

  // -- resume ---------------------------------------------------------------

  resumeState: (jobId: string) =>
    request<ResumeState>(`/jobs/${jobId}/resume`),

  resume: (jobId: string, body: ResumeBody) =>
    request<JobAccepted>(`/jobs/${jobId}/resume`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}

/**
 * The URL an artifact is fetched from.
 *
 * The only place a `/artifacts` URL is built, so a screen never concatenates a
 * name into a URL by hand and the encoding happens once.
 *
 * A name containing an absolute path or a `..` component **throws** rather
 * than being encoded and sent. The server refuses those regardless - that is
 * where the boundary lives and it is tested there - but every name this
 * function is given came out of a server listing, so one that walks upward is
 * a bug in this client and not a request to forward. `encodeURIComponent`
 * leaves a dot alone, so encoding would have emitted the traversal intact and
 * relied entirely on the far end.
 */
export function artifactUrl(
  root: 'recovered' | 'reports',
  name: string,
  options: { download?: boolean } = {},
): string {
  const parts = name.split('/')
  if (name.startsWith('/') || parts.some((part) => part === '..')) {
    throw new Error(
      `refusing to build an artifact URL for ${name}: an artifact is named ` +
        'relative to its root, and this name leaves it',
    )
  }
  const path = parts.map((part) => encodeURIComponent(part)).join('/')
  return `/artifacts/${root}/${path}${options.download ? '?download=true' : ''}`
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

export type Level = 'CLEAR' | 'PURGE'

/** The engine's flash determination (core/device/media.py:is_flash). */
export interface MediaDetermination {
  flash: boolean
  /** The signal that decided it, as a sentence. */
  reason: string
}

/** What the engine would run for one level (core/models.py:PlannedErase). */
export interface PlannedErase {
  level: Level
  reachable: boolean
  method: string | null
  justification: string
  evidence: string[]
  executable: boolean
  not_executable_reason: string
  limitations: string[]
  refusal: string
  remediation: string
}

/** core/models.py:ErasePreview, computed by core/erase/drive.py:preview. */
export interface ErasePreview {
  flash: boolean
  flash_reason: string
  purge_mechanisms: string[]
  purge_requires: string
  plans: PlannedErase[]
}

// ---------------------------------------------------------------------------
// Platform model (core/platform/model.py). The UI reads only these shapes; it
// never learns how a device was discovered on a given OS.
// ---------------------------------------------------------------------------

export type CapabilityStatus =
  | 'SUPPORTED'
  | 'SUPPORTED_WITH_LIMITATIONS'
  | 'NOT_AUTHORIZED'
  | 'NOT_VERIFIABLE'
  | 'UNVERIFIED'
  | 'INCONCLUSIVE'
  | 'UNSUPPORTED'

export interface PlatformInfo {
  family: 'linux' | 'windows' | 'macos' | 'other'
  os_name: string
  os_version: string
  os_build: string
  machine: string
  app_version: string
  packaged: boolean
  /** What the build recorded about itself; empty from a source checkout. */
  build: Record<string, string>
  sys_platform: string
}

export interface PrivilegeState {
  level: 'root' | 'administrator' | 'standard' | 'unknown'
  elevated: boolean | null
  basis: string
  helper: 'socket' | 'in-process' | 'none'
  helper_basis: string
}

export interface OperationCapability {
  operation: string
  label: string
  status: CapabilityStatus
  reason: string
  source: string
  verification: string
  limitations: string[]
  requires_privilege: boolean
}

export interface MediaClassSupport {
  media_class: string
  discovery: CapabilityStatus
  file_erase: CapabilityStatus
  whole_drive: CapabilityStatus
  reason: string
  detected_now: number
}

export interface FilesystemSupport {
  filesystem: string
  cells: Record<string, Record<string, CapabilityStatus>>
  notes: Record<string, string>
}

export interface PlatformStatus {
  platform: PlatformInfo
  privilege: PrivilegeState
  operations: OperationCapability[]
  media_classes: MediaClassSupport[]
  filesystems: FilesystemSupport[]
  restrictions: string[]
  adapter: string
  limitations: string[]
}

export interface PartitionInfo {
  id: string
  size_bytes: number
  filesystem: string
  label: string
  mount_points: string[]
}

export interface NormalizedDevice {
  id: string
  platform: string
  path: string
  vendor: string
  model: string
  serial: string
  capacity_bytes: number
  interface: string
  media_type: 'hdd' | 'ssd' | 'flash' | 'unknown'
  media_basis: string
  removable: boolean | null
  mounted: boolean
  mount_points: string[]
  system_device: boolean
  system_reasons: string[]
  filesystems: string[]
  partitions: PartitionInfo[]
  stable_id: string
  limitations: string[]
}

export interface SafetyCheck {
  key: string
  label: string
  passed: boolean | null
  detail: string
}

export interface SanitizeOption {
  level: Level
  title: string
  status: CapabilityStatus
  method: string | null
  why: string
  technical: string[]
  verification: string
  remediation: string
}

export interface DeviceAssessment {
  device_id: string
  platform: string
  status: CapabilityStatus
  headline: 'READY' | 'NOT AVAILABLE' | 'NOT AUTHORIZED' | string
  reason: string
  recommended_action: string
  recommended: SanitizeOption | null
  alternatives: SanitizeOption[]
  unavailable: SanitizeOption[]
  verification: string
  safety_checks: SafetyCheck[]
  flash_limitation: string
}

export interface DeviceRow {
  /** Cross-platform shape; absent only from a helper older than the adapters. */
  normalized?: NormalizedDevice
  assessment?: DeviceAssessment
  device: Device
  capabilities: Capabilities | null
  /** Absent from a helper older than the preview; treated as unknown. */
  media?: MediaDetermination
  /** Null when the capability probe failed, so nothing can be predicted. */
  erase_preview?: ErasePreview | null
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
  /** SIMULATION / NO PHYSICAL DEVICE MODIFIED on a dry run, else empty. */
  notice?: string
}

export interface JobStatus {
  job_id: string
  kind: string
  state: string
  /**
   * Terminal *and* written to the chain. `state` flips a moment earlier, so a
   * certificate asked for on `state` alone can race the ledger append and be
   * refused as an unknown job. Absent from an older server: treated as settled.
   */
  settled?: boolean
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
  /** The trusted actor. Resolved server-side; never what a client claimed. */
  actor?: string
  actor_basis?: string
  /** True when this status was rebuilt from the chain after a restart. */
  reconstructed?: boolean
  reconstructed_from_seq?: number
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
  case_id: string
  /** Host paths. The UI addresses reports by the artifact URLs below instead. */
  json_path: string
  pdf_path: string
  json_name: string
  pdf_name: string
  json_url: string
  pdf_url: string
  generated_at: string
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
  report_name: string
  json_url: string
  pdf_url: string
  passed: boolean
  /** The graded verdict core computed, and why it is not VERIFIED. */
  verdict: string
  verdict_reasons: string[]
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

/**
 * Identity and financial identifier counts for one object (core/models.py
 * PiiFindings). Kinds and counts only: the server never sends a value, a part
 * of one, or where it was.
 */
export interface PiiFindings {
  inspected: boolean
  basis: string
  counts: Record<string, number>
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
  /** Absent from a result produced before PII triage existed. */
  pii?: PiiFindings
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
  /** Real erase only: the id the server returned from /workflow/erase-drive. */
  authorization_id?: string
  case_id?: string
  operator?: string
}

export interface OpenEraseWorkflowBody {
  path: string
  level: string
  backup_image: string
}

export interface ApproveEraseBody {
  typed_serial: string
  acknowledge_data_destruction: boolean
}

/** api/routes/workflow.py:_view. core/workflow.py:WorkflowStatus.as_dict inside. */
export interface EraseWorkflowView {
  authorization_id: string
  path: string
  level: string
  workflow: {
    state: string
    why_blocked: string[]
    next_action: string
    allowed_next: string[]
  }
  approved: boolean
  approved_by: string
  backup: { path: string; sha256: string; size_bytes: number }
  backup_limitation: string
  plan: {
    level: string
    achievable_levels: string[]
    estimated_seconds: number | null
    limitations: string[]
    blocking: string[]
  }
  spent: boolean
}

/** What a sanitization verification concluded. Four outcomes, never three. */
export interface EraseVerification {
  strategy: string
  /** null is INCONCLUSIVE: the check ran and settled nothing. */
  passed: boolean | null
  bytes_checked: number
  sample_count: number
  sample_seed: number | null
  confidence_bp: number
  failed_offsets: number[]
  probability_note: string
  hw_attested: boolean
}

export interface EraseFilesBody {
  paths: string[]
  dry_run: boolean
  confirm: boolean
  cleanse_metadata: boolean
  break_hardlinks: boolean
  recursive: boolean
  /** Find and remove the thumbnails, recent entries and Trash copies of these files. */
  sweep_traces: boolean
}

/** One trace the desktop kept of an erased file. See core/erase/traces.py. */
export interface TraceRecord {
  kind: string
  /** The erased path this trace belongs to. */
  target: string
  /** Where the trace is: a file, or the list that holds an entry. */
  location: string
  /** Why it matches, in words. */
  evidence: string
  /** A copy of the content, not only a mention of the path. */
  content_copy: boolean
  /** Tied to the erased path on evidence. Only exact traces are removed. */
  exact: boolean
  action: string
  removed: boolean
  bytes_overwritten: number
  error: string
}

export interface TraceSweep {
  searched: string[]
  not_searched: string[]
  traces: TraceRecord[]
  /** Places that were present but could not be read, and why. */
  notes: string[]
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
  case_id?: string
  operator?: string
}

export interface CarveBody {
  image: string
  undelete: boolean
  carve_signatures: boolean
  pii_triage: boolean
  out_dir: string | null
  case_id?: string
  operator?: string
}

// ---------------------------------------------------------------------------
// Cases
// ---------------------------------------------------------------------------

export interface CaseSummary {
  case_id: string
  title: string
  description: string
  status: string
  created_at: string
  created_by: string
  updated_at: string
  evidence_count: number
  operation_count: number
  report_count: number
  recovered_artifact_count: number
  /** Present on the detail response only. The chain's verdict, not the file's. */
  audit_event_count?: number
  integrity?: string
}

export interface EvidenceRecord {
  evidence_id: string
  case_id: string
  source: string
  media_type: string
  acquired_at: string
  source_hash: string
  verification_hash: string
  state: string
  detail: Record<string, unknown>
}

export interface OperationRecord {
  operation_id: string
  case_id: string
  evidence_id: string
  type: string
  status: string
  started_at: string
  completed_at: string | null
  operator: string
  result_ref: string
  recovered_artifacts: number
  params: Record<string, unknown>
}

export interface CaseReportRecord {
  report_id: string
  case_id: string
  operation_id: string
  json_name: string
  pdf_name: string
  report_hash: string
  signed: boolean
  pubkey_fingerprint: string
  generated_at: string
}

export interface AuditEvent {
  seq: number
  case_id: string
  operation_id: string
  sequence: number
  actor: string
  event: string
  timestamp: string
  previous_hash: string
  current_hash: string
}

export interface CaseDetail {
  case: CaseSummary
  evidence: EvidenceRecord[]
  operations: OperationRecord[]
  reports: CaseReportRecord[]
  audit: {
    chain_status: string
    chain_explanation: string
    first_broken_seq: number | null
    entry_count: number
    events: AuditEvent[]
  }
}

export interface EvidenceBody {
  evidence_id: string
  source: string
  media_type: string
  acquired_at?: string
  source_hash?: string
  verification_hash?: string
  state?: string
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

export interface ArtifactRef {
  name: string
  size: number
  /** From the server's closed extension table. Never sniffed. */
  content_type: string
  /** `inline` only for raster images, and for reports this tool generated. */
  disposition: 'inline' | 'attachment'
  url: string
}

// ---------------------------------------------------------------------------
// Tamper demonstration
// ---------------------------------------------------------------------------

export interface ChainVerdict {
  status: string
  explanation: string
  entry_count: number
  first_broken_seq: number | null
  failure_kind?: string | null
  verified_through?: number | null
  unverifiable_count?: number
}

export interface TamperDemo {
  demonstration: boolean
  /** Always false. The live chain is copied, never opened for writing. */
  production_ledger_modified: boolean
  production_ledger_root: string
  tampered_seq: number
  field: string
  original_value: string
  modified_value: string
  before: ChainVerdict
  after: ChainVerdict
  note: string
}

// ---------------------------------------------------------------------------
// Resume
// ---------------------------------------------------------------------------

export interface ResumeState {
  found: boolean
  method: string
  level: string
  path: string
  serial: string
  checkpoint: { offset: number; pass_index: number; seq: number } | null
  resumable: boolean
  /** Written by the server. Rendered verbatim; never reworded by a screen. */
  reason: string
}

export interface ResumeBody {
  dry_run: boolean
  typed_serial: string
}
