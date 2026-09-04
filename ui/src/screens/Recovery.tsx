import { useEffect, useMemo, useRef, useState } from 'react'
import { api, RequestFailed, streamJob } from '../lib/api'
import type { CarveCandidate, JobStatus, Progress } from '../lib/api'
import { bytes, hex, percent } from '../lib/format'
import {
  Chip,
  Empty,
  ErrorNotice,
  Notice,
  Panel,
  ProgressView,
  Stat,
} from '../components/widgets'

const BUCKET_TONE: Record<string, 'high' | 'medium' | 'low' | 'muted'> = {
  // Green is the trustworthy bucket. Deliberately inverted from the residual
  // findings palette, where red is the alarming one: here HIGH means high
  // confidence, and using red for it would read as a warning.
  HIGH: 'low',
  MEDIUM: 'medium',
  LOW: 'high',
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
}

function ScoreBreakdown({ candidate }: { candidate: CarveCandidate }) {
  const entries = Object.entries(candidate.score_components)
  const total = entries.reduce((sum, [, value]) => sum + value, 0)
  return (
    <div className="col" style={{ gap: 9 }}>
      <div className="row spread">
        <h3>How this number was produced</h3>
        <Chip tone={BUCKET_TONE[candidate.bucket] ?? 'muted'}>
          {percent(candidate.confidence_bp, 2)} · {candidate.bucket}
        </Chip>
      </div>

      <div className="bar-breakdown">
        {entries.map(([name, value]) => (
          <span
            key={name}
            title={`${name}: ${value} bp`}
            style={{
              width: `${(value / 10000) * 100}%`,
              background: value > 0 ? 'var(--accent)' : 'transparent',
              borderRight: value > 0 ? '1px solid var(--bg-panel)' : 'none',
            }}
          />
        ))}
      </div>

      <table>
        <thead>
          <tr>
            <th>Component</th>
            <th style={{ width: 70 }}>Basis pts</th>
            <th>What it establishes</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, value]) => (
            <tr key={name}>
              <td className="mono">{name}</td>
              <td
                className="mono"
                style={{ color: value > 0 ? 'var(--accent)' : 'var(--fg-faint)' }}
              >
                {value}
              </td>
              <td style={{ color: 'var(--fg-dim)', fontSize: 11 }}>
                {value > 0
                  ? COMPONENT_MEANING[name]
                  : /* Zero is a measurement, not an absence: the check ran and
                       did not hold. Rendering it as blank would let a reader
                       assume it was never attempted. */
                    `not established — ${COMPONENT_MEANING[name]}`}
              </td>
            </tr>
          ))}
          <tr>
            <td className="mono">
              <strong>total</strong>
            </td>
            <td className="mono">
              <strong>{Math.min(total, 10000)}</strong>
            </td>
            <td style={{ color: 'var(--fg-dim)', fontSize: 11 }}>
              clamped to 10000; HIGH at 8000, MEDIUM at 5000
            </td>
          </tr>
        </tbody>
      </table>

      {candidate.entropy_millibits_per_byte !== null && (
        <div className="row wrap" style={{ gap: 20 }}>
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
        <div className="col" style={{ gap: 3 }}>
          <span className="stat-label">decoder said</span>
          <pre className="log">{candidate.validation_detail}</pre>
        </div>
      )}

      {candidate.overlapped && (
        <Notice tone="warn">
          A higher-scoring candidate covers overlapping bytes
          {candidate.overlaps_with !== null &&
            ` (at ${hex(candidate.overlaps_with)})`}
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
    <div className="col" style={{ gap: 7 }}>
      <span className="stat-label">preview</span>
      <div
        style={{
          border: '1px solid var(--line)',
          background: 'var(--bg-input)',
          minHeight: 130,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--fg-faint)',
          fontSize: 11,
          padding: 12,
          textAlign: 'center',
        }}
      >
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
  const detach = useRef<(() => void) | null>(null)

  useEffect(() => () => detach.current?.(), [])

  const candidates = (status?.result?.candidates ?? []) as CarveCandidate[]

  const filtered = useMemo(
    () =>
      candidates.filter((item) => {
        if (filterType && item.ext !== filterType) return false
        if (filterBucket && item.bucket !== filterBucket) return false
        if (filterSource && item.source !== filterSource) return false
        if (filterFlag) {
          const flags = item.flags as unknown as Record<string, boolean>
          if (!flags[filterFlag]) return false
        }
        return true
      }),
    [candidates, filterType, filterBucket, filterSource, filterFlag],
  )

  async function start() {
    setError(null)
    try {
      const accepted = await api.carve({
        image,
        undelete,
        carve_signatures: signatures,
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
        <p>
          Read-only. Nothing in the carving path opens the evidence for writing.
        </p>
      </div>

      <div className="screen-body">
        <ErrorNotice error={error} />

        <Panel title="Evidence and scan configuration">
          <div className="row wrap" style={{ gap: 12, alignItems: 'flex-end' }}>
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

        {candidates.length > 0 && (
          <div className="split">
            <Panel
              title={`Candidates (${filtered.length} of ${candidates.length})`}
              tight
              actions={
                <div className="row" style={{ gap: 6 }}>
                  <select value={filterType} onChange={(e) => setFilterType(e.target.value)}>
                    <option value="">all types</option>
                    {types.map((item) => (
                      <option key={item} value={item}>{item}</option>
                    ))}
                  </select>
                  <select value={filterBucket} onChange={(e) => setFilterBucket(e.target.value)}>
                    <option value="">all confidence</option>
                    <option value="HIGH">HIGH</option>
                    <option value="MEDIUM">MEDIUM</option>
                    <option value="LOW">LOW</option>
                  </select>
                  <select value={filterSource} onChange={(e) => setFilterSource(e.target.value)}>
                    <option value="">all sources</option>
                    {sources.map((item) => (
                      <option key={item} value={item}>{item}</option>
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
                </div>
              }
            >
              <div className="scroll-y" style={{ maxHeight: '58vh' }}>
                <table>
                  <thead>
                    <tr>
                      <th>Offset</th>
                      <th>Name</th>
                      <th>Type</th>
                      <th>Size</th>
                      <th>Source</th>
                      <th>Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((item) => (
                      <tr
                        key={`${item.offset}-${item.ext}`}
                        className={
                          selected?.offset === item.offset
                            ? 'clickable selected'
                            : 'clickable'
                        }
                        onClick={() => setSelected(item)}
                      >
                        <td className="offset">{hex(item.offset)}</td>
                        <td>{item.original_name ?? <span style={{ color: 'var(--fg-faint)' }}>—</span>}</td>
                        <td className="mono">{item.ext}</td>
                        <td>{bytes(item.length)}</td>
                        <td className="mono" style={{ fontSize: 11 }}>{item.source}</td>
                        <td>
                          <Chip tone={BUCKET_TONE[item.bucket] ?? 'muted'}>
                            {percent(item.confidence_bp)} {item.bucket}
                          </Chip>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>

            <Panel title={selected ? 'Score breakdown' : 'Select a candidate'}>
              {selected ? (
                <div className="col" style={{ gap: 13 }}>
                  <dl className="kv">
                    <dt>offset</dt>
                    <dd>{hex(selected.offset)} ({selected.offset})</dd>
                    <dt>length</dt>
                    <dd>{selected.length.toLocaleString('en-US')} bytes</dd>
                    <dt>sha-256</dt>
                    <dd>{selected.sha256}</dd>
                    <dt>mime</dt>
                    <dd>{selected.mime}</dd>
                    <dt>validation</dt>
                    <dd>{selected.validation}</dd>
                    {selected.fs_type && (
                      <>
                        <dt>filesystem</dt>
                        <dd>{selected.fs_type}</dd>
                      </>
                    )}
                  </dl>

                  {selected.contiguity_assumed && (
                    <Notice tone="warn">
                      Contiguity assumed: the cluster chain did not survive
                      deletion, so the layout was inferred rather than read.
                      {selected.contiguity_contradicted &&
                        ' A cluster inside this file’s span belongs to a live file, so it was definitely fragmented.'}
                    </Notice>
                  )}

                  <ScoreBreakdown candidate={selected} />
                  <PreviewPane candidate={selected} />
                </div>
              ) : (
                <Empty>
                  Click a candidate to see the six score components and their
                  basis points.
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
