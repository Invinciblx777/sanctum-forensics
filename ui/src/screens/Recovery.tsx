import { useEffect, useMemo, useRef, useState } from 'react'
import { api, RequestFailed, streamJob } from '../lib/api'
import type { CarveCandidate, JobStatus, Progress } from '../lib/api'
import { bytes, hex, percent } from '../lib/format'
import {
  matchesPii,
  PII_KINDS,
  PII_LABELS,
  piiSummary,
  piiTotal,
  sortByPii,
} from '../lib/triage'
import {
  Empty,
  ErrorNotice,
  JobId,
  Evidence,
  Notice,
  Panel,
  ProgressView,
  Railed,
  Stat,
  Verdict,
} from '../components/widgets'
import type { Tone } from '../components/widgets'

/**
 * Confidence bucket to tone.
 *
 * Deliberately inverted from the residual-findings palette. There, red is the
 * alarming outcome; here HIGH means high confidence, and colouring it red
 * would read as a warning about the candidate an examiner should trust most.
 */
const BUCKET_TONE: Record<string, Tone> = {
  HIGH: 'success',
  MEDIUM: 'warning',
  LOW: 'destructive',
}

/** What each score component establishes. Shown beside its basis points. */
const COMPONENT_MEANING: Record<string, string> = {
  header: "the format's magic sits exactly where this candidate claims the object starts",
  exact_length:
    'the end was derived — a footer was found, or a parser walked the format’s own length fields — rather than guessed',
  decoder: 'a real decoder read the object',
  entropy: 'the byte distribution matches what this format produces',
  fs_metadata: 'a surviving filesystem record agrees that a file lived here',
  no_overlap: 'no higher-scoring candidate claims the same bytes',
  reassembly:
    'rebuilt from separate runs: where the gap was is inferred, so the total is held below HIGH',
}

function ScoreBreakdown({ candidate }: { candidate: CarveCandidate }) {
  const entries = Object.entries(candidate.score_components)
  const total = Math.min(
    entries.reduce((sum, [, value]) => sum + value, 0),
    10000,
  )
  const tone = BUCKET_TONE[candidate.bucket] ?? 'unknown'

  return (
    <div className="col">
      {/* The number first, at the size the room can read, with the arithmetic
          under it. Every line below is how it was reached. */}
      <Railed tone={tone}>
        <Verdict
          level={candidate.bucket}
          basis={`${percent(candidate.confidence_bp, 2)} · ${total} of 10000 basis points`}
          tone={tone}
        />
        <span className="note-faint">
          HIGH at 8000, MEDIUM at 5000. The total is clamped, never scaled.
        </span>
      </Railed>

      <div className="bar-breakdown">
        {entries.map(([name, value]) => (
          <span
            key={name}
            className={value > 0 ? 'is-on' : 'is-off'}
            title={`${name}: ${value} bp`}
            style={{ width: `${(value / 10000) * 100}%` }}
          />
        ))}
      </div>

      <h3>How this number was produced</h3>

      {/* Six components, each with its own rail. A component that scored zero
          keeps its row and its sentence: zero is a measurement - the check ran
          and did not hold - and rendering it blank would let a reader assume it
          was never attempted. */}
      {entries.map(([name, value]) => (
        <Railed key={name} tone={value > 0 ? 'success' : 'unknown'}>
          <span className="row spread">
            <span className="mono">{name}</span>
            <span
              className="mono"
              style={{
                color: value > 0 ? 'var(--text-primary)' : 'var(--text-muted)',
              }}
            >
              {value}
            </span>
          </span>
          <span className="note">
            {value > 0
              ? COMPONENT_MEANING[name]
              : `not established — ${COMPONENT_MEANING[name]}`}
          </span>
        </Railed>
      ))}

      {candidate.entropy_millibits_per_byte !== null && (
        <div className="row wrap" style={{ gap: 'var(--space-6)' }}>
          <Stat
            label="entropy"
            value={`${(candidate.entropy_millibits_per_byte / 1000).toFixed(2)} bits/byte`}
          />
          {candidate.high_entropy_windows_bp !== null && (
            <Stat
              label="high-entropy windows"
              value={percent(candidate.high_entropy_windows_bp)}
            />
          )}
        </div>
      )}

      {candidate.validation_detail && (
        <div className="col tight">
          <span className="stat-label">decoder said</span>
          <pre className="log">{candidate.validation_detail}</pre>
        </div>
      )}

      {candidate.overlapped && (
        <Notice tone="warn">
          A higher-scoring candidate covers overlapping bytes
          {candidate.overlaps_with !== null && ` (at ${hex(candidate.overlaps_with)})`}
          . This candidate is kept and marked rather than dropped: a suppressed
          candidate that turns out to matter must remain visible.
        </Notice>
      )}
    </div>
  )
}

function PreviewPane({ candidate }: { candidate: CarveCandidate }) {
  // Rendered from the candidate's own recovered bytes only if the carve wrote
  // them out; nothing is fetched from anywhere else. Where there is no local
  // artifact the pane says what it would show rather than showing a broken
  // image icon.
  const isImage = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff'].includes(
    candidate.ext.toLowerCase(),
  )
  const isPdf = candidate.ext.toLowerCase() === 'pdf'

  return (
    <div className="col tight">
      <span className="stat-label">preview</span>
      <div className="preview-pane">
        {isImage || isPdf
          ? `${isPdf ? 'PDF' : 'Image'} preview renders from the recovered file once the carve is run with an output directory. Nothing is fetched from the network.`
          : `No preview for .${candidate.ext}. ${bytes(candidate.length)} at ${hex(candidate.offset)}.`}
      </div>
    </div>
  )
}

export default function Recovery() {
  const [image, setImage] = useState('')
  const [outDir, setOutDir] = useState('')
  const [undelete, setUndelete] = useState(true)
  const [signatures, setSignatures] = useState(true)
  const [piiTriage, setPiiTriage] = useState(true)
  const [jobId, setJobId] = useState<string | null>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [status, setStatus] = useState<JobStatus | null>(null)
  const [selected, setSelected] = useState<CarveCandidate | null>(null)
  const [error, setError] = useState<{
    message: string
    kind?: string
    remediation?: string
  } | null>(null)

  const [filterType, setFilterType] = useState('')
  const [filterBucket, setFilterBucket] = useState('')
  const [filterSource, setFilterSource] = useState('')
  const [filterFlag, setFilterFlag] = useState('')
  const [filterPii, setFilterPii] = useState('')
  const [sortPii, setSortPii] = useState(false)
  const detach = useRef<(() => void) | null>(null)

  useEffect(() => () => detach.current?.(), [])

  const candidates = (status?.result?.candidates ?? []) as CarveCandidate[]

  const filtered = useMemo(() => {
    const kept = candidates.filter((item) => {
      if (filterType && item.ext !== filterType) return false
      if (filterBucket && item.bucket !== filterBucket) return false
      if (filterSource && item.source !== filterSource) return false
      if (filterFlag) {
        const flags = item.flags as unknown as Record<string, boolean>
        if (!flags[filterFlag]) return false
      }
      return matchesPii(item, filterPii)
    })
    return sortPii ? sortByPii(kept) : kept
  }, [candidates, filterType, filterBucket, filterSource, filterFlag, filterPii, sortPii])

  const withIdentifiers = candidates.filter((item) => piiTotal(item) > 0).length

  async function start() {
    setError(null)
    try {
      const accepted = await api.carve({
        image,
        undelete,
        carve_signatures: signatures,
        pii_triage: piiTriage,
        out_dir: outDir || null,
      })
      setJobId(accepted.job_id)
      setProgress(null)
      setStatus(null)
      setSelected(null)
      detach.current?.()
      detach.current = streamJob(accepted.job_id, {
        onProgress: setProgress,
        onState: setStatus,
      })
    } catch (exc) {
      const failure = exc as RequestFailed
      setError({
        message: failure.message,
        kind: failure.kind,
        remediation: failure.remediation,
      })
    }
  }

  const types = [...new Set(candidates.map((item) => item.ext))].sort()
  const sources = [...new Set(candidates.map((item) => item.source))].sort()

  return (
    <>
      <div className="screen-head">
        <h1>Recovery</h1>
        <p>Read-only. Nothing in the carving path opens the evidence for writing.</p>
      </div>

      <div className="screen-body">
        <ErrorNotice error={error} />

        <Panel title="Evidence and scan configuration">
          <div className="row wrap" style={{ alignItems: 'flex-end' }}>
            <label className="grow">
              Evidence image
              <input
                type="text"
                value={image}
                spellCheck={false}
                placeholder="/path/to/case.dd or case.E01"
                onChange={(event) => setImage(event.target.value)}
              />
            </label>
            <label className="grow">
              Output directory (optional)
              <input
                type="text"
                value={outDir}
                spellCheck={false}
                placeholder="leave empty to list candidates without writing"
                onChange={(event) => setOutDir(event.target.value)}
              />
            </label>
            <label className="inline">
              <input
                type="checkbox"
                checked={undelete}
                onChange={(event) => setUndelete(event.target.checked)}
              />
              <span>Undelete from filesystem metadata</span>
            </label>
            <label className="inline">
              <input
                type="checkbox"
                checked={signatures}
                onChange={(event) => setSignatures(event.target.checked)}
              />
              <span>Signature and structure carve</span>
            </label>
            <label
              className="inline"
              title="Counts identifier shapes in documents, databases and unclassified objects. No value is stored."
            >
              <input
                type="checkbox"
                checked={piiTriage}
                onChange={(event) => setPiiTriage(event.target.checked)}
              />
              <span>PII triage (counts only)</span>
            </label>
            <button className="btn primary" disabled={!image} onClick={() => void start()}>
              Scan
            </button>
          </div>
        </Panel>

        {jobId && !status && (
          <Panel title="Progress">
            <ProgressView progress={progress} />
          </Panel>
        )}

        {jobId && (
          <Panel title="Scan job">
            <JobId value={jobId} />
            <p className="note" style={{ marginTop: 'var(--space-2)' }}>
              {status
                ? `Scan ${status.state}. The Audit screen generates this scan's report from this id.`
                : 'Scanning. Once the scan has finished, the Audit screen generates its report from this id.'}
            </p>
          </Panel>
        )}

        {candidates.length > 0 && (
          <div className="split">
            <Panel
              title={`Candidates (${filtered.length} of ${candidates.length})`}
              tight
              actions={
                <div className="row" style={{ gap: 'var(--space-2)' }}>
                  <select value={filterType} onChange={(e) => setFilterType(e.target.value)}>
                    <option value="">all types</option>
                    {types.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                  <select
                    value={filterBucket}
                    onChange={(e) => setFilterBucket(e.target.value)}
                  >
                    <option value="">all confidence</option>
                    <option value="HIGH">HIGH</option>
                    <option value="MEDIUM">MEDIUM</option>
                    <option value="LOW">LOW</option>
                  </select>
                  <select
                    value={filterSource}
                    onChange={(e) => setFilterSource(e.target.value)}
                  >
                    <option value="">all sources</option>
                    {sources.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                  <select value={filterFlag} onChange={(e) => setFilterFlag(e.target.value)}>
                    <option value="">all flags</option>
                    <option value="has_exif_gps">has GPS</option>
                    <option value="is_encrypted">encrypted</option>
                    <option value="is_password_protected">password protected</option>
                    <option value="contains_macros">macros</option>
                    <option value="has_embedded_files">embedded files</option>
                    <option value="is_signed">signed</option>
                  </select>
                  <select
                    value={filterPii}
                    onChange={(e) => setFilterPii(e.target.value)}
                    aria-label="Filter by identifiers found"
                  >
                    <option value="">all identifiers</option>
                    <option value="any">any identifier ({withIdentifiers})</option>
                    {PII_KINDS.map((kind) => (
                      <option key={kind} value={kind}>
                        {PII_LABELS[kind]}
                      </option>
                    ))}
                  </select>
                  <label className="inline">
                    <input
                      type="checkbox"
                      checked={sortPii}
                      onChange={(e) => setSortPii(e.target.checked)}
                    />
                    <span>most identifiers first</span>
                  </label>
                </div>
              }
            >
              {/* This table runs to hundreds of rows on a real image, which is
                  the one place the two-line verdict is the wrong trade. Rows
                  are one line and a fixed 30px, the layout is fixed, and the
                  confidence cell keeps the word and its percentage on a single
                  baseline. Nothing here reflows as the list is filtered. */}
              <div className="scroll-y" style={{ maxHeight: '58vh' }}>
                <table className="itable">
                  <colgroup>
                    <col style={{ width: 'var(--gutter)' }} />
                    <col style={{ width: 96 }} />
                    <col />
                    <col style={{ width: 72 }} />
                    <col style={{ width: 92 }} />
                    <col style={{ width: 100 }} />
                    <col style={{ width: 140 }} />
                    <col style={{ width: 170 }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th className="rail" />
                      <th>Offset</th>
                      <th>Name</th>
                      <th>Type</th>
                      <th>Size</th>
                      <th>Source</th>
                      <th>Confidence</th>
                      <th>Identifiers</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((item) => {
                      const tone = BUCKET_TONE[item.bucket] ?? 'unknown'
                      const isSelected = selected?.offset === item.offset
                      return (
                        <tr
                          key={`${item.offset}-${item.ext}`}
                          className={
                            isSelected
                              ? 'irow is-compact is-openable is-selected'
                              : 'irow is-compact is-openable'
                          }
                          onClick={() => setSelected(item)}
                        >
                          <td className={`rail is-${tone}`} aria-hidden>
                            <i />
                          </td>
                          <td className="offset">{hex(item.offset)}</td>
                          <td title={item.original_name ?? undefined}>
                            {item.original_name ?? (
                              <span style={{ color: 'var(--text-muted)' }}>—</span>
                            )}
                          </td>
                          <td className="mono">{item.ext}</td>
                          <td className="mono">{bytes(item.length)}</td>
                          <td className="mono">{item.source}</td>
                          <td>
                            <Verdict
                              tight
                              level={item.bucket}
                              basis={percent(item.confidence_bp)}
                              tone={tone}
                            />
                          </td>
                          {/* Kinds and counts only: the server sends no value. */}
                          <td
                            className="mono"
                            style={{
                              color:
                                piiTotal(item) > 0
                                  ? 'var(--text-primary)'
                                  : 'var(--text-muted)',
                            }}
                            title={item.pii?.basis}
                          >
                            {piiSummary(item)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </Panel>

            <Panel
              title={selected ? 'Score breakdown' : 'Select a candidate'}
              subtitle={selected ? 'Six components, each with its basis points.' : undefined}
            >
              {selected ? (
                <div className="col loose">
                  <Evidence
                    stacked
                    rows={[
                      {
                        label: 'Offset',
                        value: `${hex(selected.offset)} (${selected.offset})`,
                        kind: 'mono',
                      },
                      {
                        label: 'Length',
                        value: `${selected.length.toLocaleString('en-US')} bytes`,
                        kind: 'mono',
                      },
                      { label: 'SHA-256', value: selected.sha256, kind: 'hash' },
                      { label: 'MIME', value: selected.mime, kind: 'mono' },
                      {
                        label: 'Validation',
                        value: selected.validation,
                        kind: 'mono',
                      },
                      ...(selected.fragments.length > 0
                        ? [
                            {
                              label: 'Fragments',
                              value: selected.fragments
                                .map(
                                  (run) =>
                                    `${hex(run.offset)} + ${run.length.toLocaleString('en-US')}`,
                                )
                                .join('  ·  '),
                              kind: 'mono' as const,
                            },
                          ]
                        : []),
                      ...(selected.fs_type
                        ? [{ label: 'Filesystem', value: selected.fs_type }]
                        : []),
                    ]}
                  />

                  {selected.fragments.length > 0 && (
                    <Notice tone="warn">
                      Reassembled across a gap. This object was not one run on
                      the medium: the digest above covers the fragments listed,
                      in order, and not the span from the offset. A JPEG decoder
                      consumed the reassembled bytes whole, which is the
                      evidence the gap was found correctly.
                    </Notice>
                  )}

                  {selected.contiguity_assumed && (
                    <Notice tone="warn">
                      Contiguity assumed: the cluster chain did not survive
                      deletion, so the layout was inferred rather than read.
                      {selected.contiguity_contradicted &&
                        ' A cluster inside this file’s span belongs to a live file, so it was definitely fragmented.'}
                    </Notice>
                  )}

                  <div className="col tight">
                    <span className="stat-label">identifiers (PII triage)</span>
                    <Evidence
                      stacked
                      rows={[
                        { label: 'Found', value: piiSummary(selected) },
                        { label: 'How read', value: selected.pii?.basis || 'not scanned' },
                      ]}
                    />
                    {piiTotal(selected) > 0 && (
                      <Notice tone="info">
                        A count is a signal to look, not a finding: the detectors
                        match a shape, and a checksum for Aadhaar and card numbers.
                        No value was stored. Open the recovered object to see
                        what was counted.
                      </Notice>
                    )}
                  </div>

                  <ScoreBreakdown candidate={selected} />
                  <PreviewPane candidate={selected} />
                </div>
              ) : (
                <Empty>
                  Click a candidate to see the six score components and their basis
                  points.
                </Empty>
              )}
            </Panel>
          </div>
        )}

        {status?.result?.limitations != null &&
          (status.result.limitations as string[]).length > 0 && (
            <Panel title="Limitations">
              <ul className="limitations">
                {(status.result.limitations as string[]).map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </Panel>
          )}
      </div>
    </>
  )
}
