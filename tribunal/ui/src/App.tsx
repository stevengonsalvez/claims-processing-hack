import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

export const API = import.meta.env.VITE_API ?? 'http://localhost:8000'

type Status = 'idle' | 'running' | 'done' | 'error'
type AgentState = { label: string; status: Status; text: string; ms?: number; data?: any; error?: string; extra?: any }
type Agents = Record<string, AgentState>
type Sample = { name: string; photo: string; statements: string[] }
type Span = { start: number; end?: number }

export const ORDER = ['ocr', 'structure', 'adjuster', 'fraud', 'policy', 'arbiter']

/** One place for who each member of the court is: card headers, roster, waterfall lanes. */
export const AGENTS: Record<string, { label: string; short: string; model: string; role: string; phase: string }> = {
  ocr: {
    label: 'OCR · Mistral Document AI', short: 'OCR', model: 'mistral-document-ai-2512', phase: 'Intake',
    role: 'Reads both handwritten statement pages into text.',
  },
  structure: {
    label: 'Structuring · gpt-4.1-mini', short: 'Structuring', model: 'gpt-4.1-mini', phase: 'Intake',
    role: 'Turns loose statement text into a claim record.',
  },
  adjuster: {
    label: 'Adjuster', short: 'Adjuster', model: 'gpt-4.1-mini · vision', phase: 'The bench',
    role: 'Reads the damage photograph and prices the repair.',
  },
  fraud: {
    label: 'Fraud Investigator', short: 'Fraud', model: 'gpt-4.1-mini · vector search', phase: 'The bench',
    role: 'Searches prior claims for duplicates, and the photo for contradictions.',
  },
  policy: {
    label: 'Policy Analyst', short: 'Policy', model: 'gpt-4.1-mini · hybrid search', phase: 'The bench',
    role: 'Rules on coverage strictly from the indexed policy text, and cites it.',
  },
  arbiter: {
    label: 'Arbiter', short: 'Arbiter', model: 'gpt-4.1-mini', phase: 'Ruling',
    role: 'Weighs the three, rules, computes the payout, writes the claimant letter.',
  },
}
const LABELS: Record<string, string> = Object.fromEntries(ORDER.map(a => [a, AGENTS[a].label]))

const fresh = (): Agents => Object.fromEntries(ORDER.map(a => [a, { label: LABELS[a], status: 'idle' as Status, text: '' }]))
export const usd = (n: number | null | undefined) => n == null ? '–' : `$${Number(n).toLocaleString()}`
const clock = (ms: number) => `${String(Math.floor(ms / 60000)).padStart(2, '0')}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`

/** Similarity bars are zoomed: the interesting band is 0.50–1.00, so a cluster of
 *  0.72–0.79 scores separates instead of painting five identical full-width bars.
 *  The hairline in `.simbar::after` sits at 50% of the track, i.e. 0.75. */
export const simPct = (x: any) => `${Math.min(100, Math.max(0, ((Number(x) || 0) - 0.5) / 0.5 * 100))}%`

/** Minimal SSE parser over a fetch body. EventSource cannot POST multipart. */
export async function readSSE(res: Response, onEvent: (ev: string, data: any) => void) {
  const reader = res.body!.getReader(); const dec = new TextDecoder(); let buf = ''
  for (;;) {
    const { value, done } = await reader.read(); if (done) break
    buf += dec.decode(value, { stream: true })
    let i: number
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2)
      let ev = 'message', data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) ev = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      onEvent(ev, data ? JSON.parse(data) : {})
    }
  }
}

export default function App() {
  const [samples, setSamples] = useState<Sample[]>([])
  const [sample, setSample] = useState<string>('')
  const [files, setFiles] = useState<{ statements: File[]; photo?: File }>({ statements: [] })
  const [agents, setAgents] = useState<Agents>(fresh())
  const [timeline, setTimeline] = useState<Record<string, Span>>({})
  const [elapsed, setElapsed] = useState(0)
  const [claimId, setClaimId] = useState('')
  const [claim, setClaim] = useState<any>(null)
  const [evidence, setEvidence] = useState<any>(null)
  const [verdict, setVerdict] = useState<any>(null)
  const [running, setRunning] = useState(false)
  const [convened, setConvened] = useState(false)
  const [fatal, setFatal] = useState('')
  const [decision, setDecision] = useState<string>('')
  const [precedentId, setPrecedentId] = useState<string>('')
  const [override, setOverride] = useState('')
  const [appeals, setAppeals] = useState<any[]>([])
  const t0 = useRef(0)

  useEffect(() => {
    fetch(`${API}/samples`).then(r => r.json())
      .then(s => { setSamples(s); setSample(s[0]?.name ?? '') })
      .catch(() => setFatal(`Cannot reach the tribunal API at ${API}. Start it with tribunal/dev.sh.`))
  }, [])

  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setElapsed(Date.now() - t0.current), 100)
    return () => clearInterval(id)
  }, [running])

  // Once the bench has ruled, ask the case file whether the claimant has appealed.
  // Deferred 2.5s: the API writes the case file through to disk just after it emits the
  // verdict, and probing on the same tick 404s (harmless, but it logs in the console).
  useEffect(() => {
    if (!verdict || !claimId) return
    let live = true
    const t = window.setTimeout(() => {
      fetch(`${API}/claims/${claimId}`).then(r => r.ok ? r.json() : null)
        .then(c => { if (live && c?.appeals?.length) setAppeals(c.appeals) })
        .catch(() => { /* the diff is a bonus; its absence is not an error */ })
    }, 2500)
    return () => { live = false; window.clearTimeout(t) }
  }, [verdict, claimId])

  const patch = (a: string, p: Partial<AgentState>) =>
    setAgents(prev => ({ ...prev, [a]: { ...prev[a], ...p } }))

  const canRun = !running && (!!sample || files.statements.length > 0)

  const run = useCallback(async () => {
    if (running) return
    t0.current = Date.now()
    setAgents(fresh()); setTimeline({}); setElapsed(0); setClaim(null); setEvidence(null)
    setVerdict(null); setDecision(''); setPrecedentId(''); setOverride(''); setFatal('')
    setAppeals([]); setConvened(true); setRunning(true)
    const form = new FormData()
    if (files.statements.length) { files.statements.forEach(f => form.append('statements', f)); if (files.photo) form.append('photo', files.photo) }
    else form.append('sample', sample)
    try {
      const res = await fetch(`${API}/adjudicate`, { method: 'POST', body: form })
      if (!res.ok || !res.body) throw new Error(`API responded ${res.status}`)
      await readSSE(res, (ev, d) => {
        switch (ev) {
          case 'claim.start': setClaimId(d.claim_id); break
          case 'agent.start':
            patch(d.agent, { status: 'running', text: '' })
            setTimeline(prev => ({ ...prev, [d.agent]: { start: Date.now() - t0.current } }))
            break
          case 'agent.token': setAgents(prev => ({ ...prev, [d.agent]: { ...prev[d.agent], text: prev[d.agent].text + d.text } })); break
          case 'agent.done':
            patch(d.agent, { status: 'done', ms: d.ms, data: d.data, extra: d, text: d.prose ?? '' })
            setTimeline(prev => ({
              ...prev,
              [d.agent]: { start: prev[d.agent]?.start ?? Math.max(0, Date.now() - t0.current - (d.ms ?? 0)), end: Date.now() - t0.current },
            }))
            break
          case 'claim': setClaim(d); break
          case 'evidence': setEvidence(d); break
          case 'verdict': setVerdict(d); break
          case 'error':
            if (d.agent in LABELS) patch(d.agent, { status: 'error', error: d.message })
            else {
              setFatal(d.message || 'The tribunal could not sit.')
              setAgents(prev => Object.fromEntries(Object.entries(prev).map(
                ([k, v]) => [k, v.status === 'running' ? { ...v, status: 'error' as Status, error: 'session ended' } : v])))
            }
            break
        }
      })
    } catch (e: any) {
      setFatal(e?.message ? `Session failed: ${e.message}` : 'Session failed.')
    } finally {
      setElapsed(Date.now() - t0.current)
      setRunning(false)
    }
  }, [running, files, sample])

  const record = useCallback(async (kind: string) => {
    if (!verdict || decision) return
    try {
      const res = await fetch(`${API}/decision`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ claim_id: claimId, human_decision: kind, reason: override, verdict }),
      })
      if (!res.ok) { setDecision(`${kind} (API error ${res.status})`); return }
      const body = await res.json().catch(() => ({}))
      if (body?.precedent_id) setPrecedentId(String(body.precedent_id))
      setDecision(kind)
    } catch {
      setDecision(`${kind} (not persisted — API unreachable)`)
    }
  }, [verdict, decision, claimId, override])

  // Keyboard: 1–5 pick a claim, Enter convenes, A / D rule on the verdict.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const el = e.target as HTMLElement | null
      const typing = !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
      if (typing) return
      if (/^[1-5]$/.test(e.key) && samples[Number(e.key) - 1] && !running && !files.statements.length) {
        setSample(samples[Number(e.key) - 1].name); e.preventDefault(); return
      }
      if (e.key === 'Enter' && el?.tagName !== 'BUTTON' && canRun) { run(); e.preventDefault(); return }
      if (verdict && !decision && (e.key === 'a' || e.key === 'A')) { record('approve'); e.preventDefault(); return }
      if (verdict && !decision && (e.key === 'd' || e.key === 'D')) { record('deny'); e.preventDefault() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [samples, running, files.statements.length, canRun, run, verdict, decision, record])

  const photoUrl = useMemo(() => {
    if (files.photo) return URL.createObjectURL(files.photo)
    const s = samples.find(x => x.name === sample)
    return s ? `${API}${s.photo}` : ''
  }, [files.photo, samples, sample])
  useEffect(() => () => { if (files.photo && photoUrl.startsWith('blob:')) URL.revokeObjectURL(photoUrl) }, [files.photo, photoUrl])

  const phase: 'idle' | 'session' | 'ruled' | 'mistrial' =
    fatal ? 'mistrial' : verdict ? 'ruled' : running ? 'session' : 'idle'
  const appealOutcome = appeals.length ? appeals[appeals.length - 1] : null
  const statusText = phase !== 'ruled'
    ? { idle: 'Awaiting docket', session: `In session · ${clock(elapsed)}`, mistrial: 'Session ended' }[phase]
    : appealOutcome
      ? `Adjourned · ${String(appealOutcome.outcome ?? '').toUpperCase() === 'OVERTURN' ? 'OVERTURNED' : 'UPHELD'} → ${String(appealOutcome.v2?.decision ?? verdict.decision).toUpperCase()}`
      : `Adjourned · ${String(verdict.decision).toUpperCase()} · ${(elapsed / 1000).toFixed(1)}s`

  return (
    <div className="app">
      <a className="skip" href="#bench">Skip to the bench</a>

      <header>
        <div className="brand">
          <Seal />
          <div>
            <h1>Claims Tribunal</h1>
            <p>Four agents argue every claim, in the open, before a human signs it off.</p>
          </div>
        </div>
        <div className={`status ${phase}`} data-decision={verdict?.decision ?? ''} aria-live="off">
          <span className="pip" />{statusText}
        </div>
      </header>

      <section className="docket" aria-label="Docket">
        <div className="field">
          <label htmlFor="sample">Cause list</label>
          <select id="sample" value={sample} disabled={running || files.statements.length > 0}
            onChange={e => setSample(e.target.value)}>
            {samples.length === 0 && <option value="">loading…</option>}
            {samples.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
        </div>

        <div className="field">
          <span className="lbl">Or file your own</span>
          <div className="uploads">
            <label className="file">
              <input type="file" accept="image/*" multiple disabled={running}
                onChange={e => setFiles(f => ({ ...f, statements: Array.from(e.target.files ?? []) }))} />
              <span>{files.statements.length ? `${files.statements.length} statement page${files.statements.length > 1 ? 's' : ''}` : 'Statement pages'}</span>
            </label>
            <label className="file">
              <input type="file" accept="image/*" disabled={running}
                onChange={e => setFiles(f => ({ ...f, photo: e.target.files?.[0] }))} />
              <span>{files.photo ? files.photo.name.slice(0, 22) : 'Damage photo'}</span>
            </label>
          </div>
        </div>

        <div className="convene">
          <button className="go" onClick={run} disabled={!canRun}>
            {running ? 'In session…' : 'Convene tribunal'}
          </button>
          {claimId && <span className="docketno"><em>Docket</em><span className="claimid">{claimId}</span></span>}
          {verdict && claimId && <a className="claimant-link" href={`/claim/${claimId}`}>Claimant view →</a>}
        </div>

        <ul className="keys" aria-label="Keyboard shortcuts">
          <li><kbd>1</kbd>–<kbd>5</kbd> claim</li>
          <li><kbd>↵</kbd> convene</li>
          <li><kbd>A</kbd> approve</li>
          <li><kbd>D</kbd> deny</li>
        </ul>
      </section>

      {fatal && <p className="notice" role="alert">{fatal}</p>}

      {convened && <Waterfall timeline={timeline} now={elapsed} running={running} />}

      <main>
        <section className="bench" id="bench" aria-label="The bench">
          {!convened
            ? <Roster />
            : ORDER.map(a => <AgentCard key={a} id={a} s={agents[a]} evidence={evidence} />)}
        </section>

        <aside aria-label="The record">
          {photoUrl && <div className="panel exhibit">
            <h3>Exhibit A · damage photograph</h3>
            <img className="photo" src={photoUrl} alt={`Damage photograph filed with claim ${sample || 'upload'}`} />
            <p className="cap">{evidence?.photo_description
              ? String(evidence.photo_description).slice(0, 260)
              : 'Seen by the Adjuster and the Fraud Investigator.'}</p>
            {evidence && <PhotoMatches e={evidence} />}
          </div>}

          {claim && <div className="panel">
            <h3>Claim record</h3>
            <dl>
              {[
                ['policy_number', 'Policy'], ['policy_holder_name', 'Holder'],
                ['vehicle_year_make_model', 'Vehicle'], ['vehicle_vin', 'VIN'],
                ['incident_date', 'Incident'], ['incident_location', 'Location'],
                ['claim_request', 'Request'],
              ].map(([k, lbl]) => (
                <div key={k} className={k === 'vehicle_vin' ? 'wide vin' : k === 'claim_request' || k === 'incident_location' ? 'wide' : ''}>
                  <dt>{lbl}</dt>
                  <dd>{claim[k] ? String(claim[k]) : <em>not stated</em>}</dd>
                </div>
              ))}
            </dl>
          </div>}

          {evidence && <Evidence e={evidence} />}
        </aside>
      </main>

      {verdict && <Verdict v={verdict} seconds={elapsed / 1000} decision={decision} precedentId={precedentId}
        override={override} setOverride={setOverride} record={record} />}

      {verdict && appeals.length > 0 && <AppealDiff a={appeals[appeals.length - 1]} v1={verdict} />}

      <footer>
        <span>gpt-4.1-mini · mistral-document-ai-2512 · text-embedding-3-large</span>
        <span>Microsoft Foundry agents · Azure AI Search · Application Insights tracing</span>
      </footer>
    </div>
  )
}

export function Seal() {
  return (
    <svg className="seal" viewBox="0 0 44 44" aria-hidden="true" focusable="false">
      <circle cx="22" cy="22" r="20.5" className="ring" />
      <circle cx="22" cy="22" r="17" className="ring thin" />
      <g className="scales">
        <path d="M22 11.5v20M15 32.5h14M11 16.5h22" />
        <path d="M11 16.5 6.4 25a5.2 5.2 0 0 0 9.2 0Z" />
        <path d="M33 16.5 28.4 25a5.2 5.2 0 0 0 9.2 0Z" />
      </g>
      <circle cx="22" cy="11.5" r="2" className="pin" />
    </svg>
  )
}

/** Live waterfall: one lane per member, bars placed on a shared clock so the
 *  three-way parallel fan-out is visible rather than asserted. */
function Waterfall({ timeline, now, running, title, sub }:
  { timeline: Record<string, Span>; now: number; running: boolean; title?: string; sub?: string }) {
  const lanes = ORDER.filter(a => timeline[a])
  if (!lanes.length) return (
    <section className="strip empty" aria-label="Session timeline">
      <p>The session clock starts when the first page is read.</p>
    </section>
  )
  const endOf = (a: string) => timeline[a].end ?? now
  const span = Math.max(8000, running ? now : 0, ...lanes.map(endOf))
  const step = span <= 30000 ? 5000 : span <= 90000 ? 10000 : 20000
  const ticks = Array.from({ length: Math.floor(span / step) }, (_, i) => (i + 1) * step)

  // The band marks the window in which all three specialists are speaking at once.
  // It covers only their lanes: LANE_H + LANE_GAP is the row pitch, LANES_TOP the padding.
  const LANE_H = 20, LANE_GAP = 6, LANES_TOP = 16
  const trio = ['adjuster', 'fraud', 'policy']
  let bracket: { at: number; wide: number; top: number; height: number } | null = null
  if (trio.every(a => timeline[a])) {
    const s = Math.max(...trio.map(a => timeline[a].start))
    const e = Math.min(...trio.map(endOf))
    const row = lanes.indexOf('adjuster')
    if (e > s && row >= 0) bracket = {
      at: s / span, wide: (e - s) / span,
      top: LANES_TOP + row * (LANE_H + LANE_GAP) - 3,
      height: 3 * LANE_H + 2 * LANE_GAP + 6,
    }
  }

  return (
    <section className="strip" aria-label="Session timeline">
      <div className="striphead">
        <h2>{title ?? 'Session'}</h2>
        <p>{sub ?? (running ? 'The bench is sitting.' : 'Every member, on one clock.')}</p>
        <span className="ldur total">{(span / 1000).toFixed(1)}s</span>
      </div>
      <div className="lanes">
        {bracket && <div className="parallel" aria-hidden="true"
          style={{ ['--a' as any]: bracket.at, ['--w' as any]: bracket.wide, top: bracket.top, height: bracket.height }}>
          <span>3 in parallel</span>
        </div>}
        {ticks.map(t => <div key={t} className="tick" aria-hidden="true" style={{ ['--p' as any]: t / span }}>
          <b>{t / 1000}s</b>
        </div>)}
        {lanes.map(a => {
          const { start } = timeline[a]
          const end = endOf(a)
          const live = timeline[a].end == null && running
          return (
            <div className={`lane ${a}`} key={a}>
              <span className="lname">{AGENTS[a].short}</span>
              <div className="track">
                <i className={`wbar ${live ? 'live' : ''}`}
                  style={{ left: `${(start / span) * 100}%`, width: `${Math.max(0.6, ((end - start) / span) * 100)}%` }} />
              </div>
              <span className="ldur">{((end - start) / 1000).toFixed(1)}s</span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function Roster() {
  return (
    <div className="roster">
      <h2>Nobody has been called yet.</h2>
      <p className="lede">
        Pick a claim and convene. Two intake steps read the file, then three specialists deliberate
        at the same time and an arbiter rules. About forty-five seconds, streamed as it happens.
      </p>
      <ol className="roles">
        {ORDER.map((a, i) => (
          <li key={a} className={a}>
            <span className="n">{String(i + 1).padStart(2, '0')}</span>
            <span className="who">{AGENTS[a].short}</span>
            <span className="what">{AGENTS[a].role}</span>
            <span className="how">{AGENTS[a].model}</span>
          </li>
        ))}
      </ol>
      <p className="hint">Nothing is decided by the machine alone: you approve, deny or override at the end.</p>
    </div>
  )
}

function AgentCard({ id, s, evidence }: { id: string; s: AgentState; evidence?: any }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    ref.current.scrollTo({ top: s.status === 'done' ? 0 : ref.current.scrollHeight })
  }, [s.text, s.status])
  const badge = id === 'fraud' && s.data?.fraud_score != null ? `risk ${Number(s.data.fraud_score).toFixed(2)}`
    : id === 'adjuster' && s.data?.estimated_total != null ? `est ${usd(s.data.estimated_total)}`
    : id === 'policy' && s.data?.coverage_decision ? s.data.coverage_decision
    : id === 'arbiter' && s.data?.decision ? s.data.decision.toUpperCase()
    : id === 'ocr' && s.extra?.chars ? `${s.extra.chars} chars · ${s.extra.pages} pages`
    : id === 'structure' && s.data ? `${Object.values(s.data).filter(v => v != null).length} fields` : ''
  // The recycled-photo signal belongs on the Fraud card too, not only in the record.
  const shot = id === 'fraud' ? (evidence?.photo_matches ?? [])[0] : null
  const shotBadge = shot && Number(shot.similarity) >= 0.85 ? `photo seen · ${shot.claim_id}` : ''
  const body = id === 'ocr' ? (s.extra?.preview ?? '') : id === 'structure' ? (s.data ? JSON.stringify(s.data, null, 1) : s.text) : s.text
  return (
    <div className={`agent ${s.status} ${id}`}>
      <div className="head">
        <span className="dot" />
        <span className="name">{s.label}</span>
        {badge && <span className="badge">{badge}</span>}
        {shotBadge && <span className="badge shot">{shotBadge}</span>}
        {s.ms != null && <span className="ms">{(s.ms / 1000).toFixed(1)}s</span>}
      </div>
      <div className="stream" ref={ref}>
        {s.error
          ? <span className="err">{s.error}</span>
          : body
            ? <>{body}{s.status === 'running' && <span className="caret" aria-hidden="true" />}</>
            : s.status === 'running'
              ? <span className="waiting">listening<span className="caret" aria-hidden="true" /></span>
              : <span className="waiting">{AGENTS[id].role}</span>}
      </div>
    </div>
  )
}

/** Photo matches sit under Exhibit A, because they are a fact about the exhibit:
 *  this photograph has been filed before, under someone else's claim. */
function PhotoMatches({ e }: { e: any }) {
  const shots: any[] = e.photo_matches ?? []
  if (!e.photo_description && !shots.length) return null
  if (!shots.length) return <p className="cap none">No prior photo resembles Exhibit A.</p>
  return <>
    <h4>Photo matches · claim-photos index</h4>
    <ul className="shots">
      {shots.map((m: any, i: number) => (
        <li key={`${m.claim_id}-${i}`} className={Number(m.similarity) >= 0.90 ? 'same' : ''}
          data-ref={`photo:${m.claim_id}`}>
          <span className="lead"><b>{m.claim_id}</b> · {m.file_name}{m.filed_at ? ` · filed ${m.filed_at}` : ''}</span>
          <span className="sim">{Number(m.similarity).toFixed(3)}</span>
          {m.description && <span className="desc">{m.description}</span>}
          <i className="simbar" style={{ ['--v' as any]: simPct(m.similarity) }} />
        </li>
      ))}
    </ul>
    <p className="cap">Bars run 0.50 to 1.00; the mark is 0.75.</p>
  </>
}

function Evidence({ e }: { e: any }) {
  const prior: any[] = e.prior_claims ?? []
  const chunks: any[] = e.policy_chunks ?? []
  const precedents: any[] = e.precedents ?? []
  const [allPols, setAllPols] = useState(false)
  // Everything over 0.75 is a strong match and stays `hot` (the documented line),
  // but only the strongest one is tagged — otherwise a tight cluster of scores
  // paints five identical badges and the real signal disappears.
  const topSim = Math.max(0, ...prior.map((x: any) => Number(x.similarity) || 0))
  const doc = chunks[0]?.title?.includes(' · ') ? chunks[0].title.split(' · ')[0] : ''
  const sections = chunks
    .map((c: any) => { const t = String(c.title ?? ''); return t.includes(' · ') ? t.split(' · ').slice(1).join(' · ') : t })
    .filter((s: string) => s && s !== doc)
  const shown = allPols ? sections : sections.slice(0, 6)
  return (
    <div className="panel">
      <h3>Evidence retrieved · Azure AI Search</h3>
      {prior.length > 0 && <>
        <h4>Prior claims · vector search</h4>
        <ul className="ev">
          {prior.map((p: any) => (
            <li key={p.id} data-ref={`prior_claim:${p.claim_id}`}
              className={[p.similarity > 0.75 && 'hot', p.similarity > 0.75 && Number(p.similarity) === topSim && 'top'].filter(Boolean).join(' ')}>
              <span className="lead"><b>{p.claim_id}</b> sim {p.similarity}</span>
              <span className="meta"> · {p.holder} · {p.vehicle} · {p.outcome} {usd(p.paid_amount)}</span>
              <i className="simbar" style={{ ['--v' as any]: simPct(p.similarity) }} />
            </li>
          ))}
        </ul>
        <p className="cap">Bars run 0.50 to 1.00; the mark is 0.75, the strong-match line.</p>
      </>}

      {precedents.length > 0 && <>
        <h4>Precedents · adjuster rulings</h4>
        <ul className="prec">
          {precedents.map((p: any, i: number) => (
            <li key={p.precedent_id ?? `${p.claim_id}-${i}`}
              data-ref={`precedent:${p.precedent_id ?? p.claim_id}`} data-alt={`precedent:${p.claim_id}`}>
              <span className="lead"><b>{p.claim_id}</b></span>
              <span className="ruling">
                <span className="was">{String(p.tribunal_decision ?? '?').toUpperCase()}</span>
                <span className="arrow">→</span>
                <span className="now">{String(p.human_decision ?? '?').toUpperCase()}</span>
              </span>
              <span className="sim">{Number(p.similarity).toFixed(3)}</span>
              {p.reason && <q className="why">{p.reason}</q>}
              <i className="simbar" style={{ ['--v' as any]: simPct(p.similarity) }} />
            </li>
          ))}
        </ul>
        <p className="cap">A human overrode the bench here. The tribunal is told, and must answer it.</p>
      </>}

      {sections.length > 0 && <>
        <h4>Policy sections · hybrid search{doc && <em> · {doc}</em>}</h4>
        <ul className="pols">
          {shown.map((sec: string, i: number) => <li key={i} data-ref={`policy:${sec}`}>{sec}</li>)}
        </ul>
        {sections.length > 6 && <button className="more" onClick={() => setAllPols(v => !v)}>
          {allPols ? 'fewer' : `+${sections.length - 6} more`}
        </button>}
      </>}
    </div>
  )
}

const norm = (s: string) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
const STOP = new Set(['the', 'and', 'for', 'this', 'case', 'section', 'coverage', 'claim'])

/** Resolve a clause's evidence_ref to the row that carries it. The Arbiter cites in its own
 *  words — `PREC-CLM-…` for a precedent the record lists as `CLM-…`, "Coverage Components:
 *  Collision Coverage" for a section titled "Collision Coverage" — so exact match alone leaves
 *  every chip dead. Exact first, then containment either way, then best token overlap. */
export function findRef(type: string, ref: string): HTMLElement | null {
  if (typeof document === 'undefined' || !ref) return null
  const esc = (x: string) => (window as any).CSS?.escape ? (window as any).CSS.escape(x) : x.replace(/"/g, '\\"')
  const key = `${type}:${ref}`
  const exact = document.querySelector(`[data-ref="${esc(key)}"]`) ?? document.querySelector(`[data-alt="${esc(key)}"]`)
  if (exact) return exact as HTMLElement
  const cands = Array.from(document.querySelectorAll<HTMLElement>(`[data-ref^="${esc(type + ':')}"]`))
  if (!cands.length) return null
  const want = norm(ref)
  const tail = (el: HTMLElement) => norm((el.dataset.ref ?? '').slice(type.length + 1))
  for (const el of cands) {
    const have = tail(el)
    if (have && (want.includes(have) || have.includes(want))) return el
  }
  const wt = new Set(want.split(' ').filter(w => w.length > 2 && !STOP.has(w)))
  let best: HTMLElement | null = null, score = 1
  for (const el of cands) {
    const n = tail(el).split(' ').filter(w => w.length > 2 && !STOP.has(w) && wt.has(w)).length
    if (n > score) { score = n; best = el }
  }
  return best
}

/** Scroll a cited exhibit into view and light it, so a clause in the ruling and the
 *  row it rests on are joined even though 1,000px of chamber sits between them. */
function reveal(type: string, ref: string) {
  const el = findRef(type, ref)
  if (!el) return
  el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  el.tabIndex = -1
  el.focus({ preventScroll: true })
  el.classList.add('lit')
  window.setTimeout(() => el.classList.remove('lit'), 1600)
}

const GLYPH: Record<string, string> = { policy: '§', prior_claim: '#', photo: '▣', precedent: '¶' }

export function Clauses({ clauses }: { clauses: any[] }) {
  if (!clauses?.length) return null
  return (
    <>
      <h4>How the ruling was reasoned</h4>
      <ol className="clauses">
        {clauses.map((c: any, i: number) => (
          <li key={i}>
            <span className="ctext">{c.clause}</span>
            {(c.evidence_refs ?? []).length > 0 && <span className="refs">
              {(c.evidence_refs ?? []).map((r: any, j: number) => {
                const kind = String(r.type ?? 'policy')
                const label = r.label || r.ref
                const found = !!findRef(kind, r.ref)
                const inner = <><b aria-hidden="true">{GLYPH[kind] ?? '§'}</b>{label}</>
                return found
                  ? <button key={j} type="button" className={`ref ${kind}`} onClick={() => reveal(kind, r.ref)}
                    title={`Show ${kind.replace('_', ' ')} ${r.ref}`}>{inner}</button>
                  : <span key={j} className={`ref ${kind} flat`}>{inner}</span>
              })}
            </span>}
          </li>
        ))}
      </ol>
    </>
  )
}

function Verdict({ v, seconds, decision, precedentId, override, setOverride, record }: any) {
  const p = v.payout ?? {}
  const score = Number(v.fraud?.score ?? 0)
  const wi = v.what_if ?? null
  const covered = Number(wi?.covered ?? p.covered ?? 0)
  const limit = wi?.limit == null ? null : Number(wi.limit)
  const baseDed = Number(wi?.deductible ?? p.deductible ?? 0)
  const [ded, setDed] = useState<number | null>(null)
  const netOf = (d: number) => Math.max(0, Math.min(covered, limit ?? covered) - d)
  const hypo = ded != null && ded !== baseDed
  const shownNet = hypo ? netOf(ded!) : p.net
  const shownDed = hypo ? ded! : p.deductible

  return (
    <section className={`verdict ${v.decision}`} aria-label="Verdict">
      <p className="sr-only" role="status" aria-live="polite">
        Verdict: {v.decision}, confidence {Number(v.confidence).toFixed(2)}, net payout {usd(p.net)},
        fraud risk {score.toFixed(2)}.
      </p>

      <div className="vmain">
        <div className="vhead">
          <span className="vdec">{v.decision}</span>
          <span className="vconf">confidence {Number(v.confidence).toFixed(2)}</span>
          <span className="vtime">{seconds.toFixed(1)}s to verdict</span>
        </div>

        <p className="rationale">{v.rationale}</p>
        {v.referral_reason && <p className="referral">Referral: {v.referral_reason}</p>}

        <Clauses clauses={v.clauses ?? []} />

        <div className="money">
          <div><span>claimed</span>{usd(p.claimed)}</div>
          <div><span>covered</span>{usd(p.covered)}</div>
          <div className={hypo ? 'hypothetical' : ''}><span>deductible</span>{usd(shownDed)}</div>
          <div className={`net ${hypo ? 'hypothetical' : ''}`}><span>net payout</span>{usd(shownNet)}</div>
        </div>

        {wi && covered > 0 && <div className="whatif">
          <label htmlFor="wi-ded">What if the deductible were <output htmlFor="wi-ded">{usd(hypo ? ded! : baseDed)}</output></label>
          <input id="wi-ded" type="range" min={0} max={covered} step={50} value={hypo ? ded! : baseDed}
            aria-valuetext={`${usd(hypo ? ded! : baseDed)} deductible, net ${usd(shownNet)}`}
            onChange={e => setDed(Number(e.target.value))} />
          {hypo && <button className="reset" onClick={() => setDed(null)}>ruling</button>}
          <p className="formula">
            {usd(covered)} covered − {usd(hypo ? ded! : baseDed)} deductible = {usd(shownNet)}
            {hypo && <em> what-if · not the ruling</em>}
          </p>
        </div>}

        <div className="fraudbar">
          <span>fraud risk</span>
          <div className={`bar ${score >= 0.7 ? 'high' : score >= 0.4 ? 'mid' : 'low'}`}>
            <i style={{ width: `${Math.round(score * 100)}%` }} />
          </div>
          <b>{score.toFixed(2)}</b>
        </div>

        {v.fraud?.evidence?.length > 0 && <ul className="ev">
          {v.fraud.evidence.map((x: any, i: number) => <li key={i}><b>{x.type}</b> {x.detail}{x.ref ? ` (${x.ref})` : ''}</li>)}
        </ul>}

        {v.disagreements?.length > 0 && <>
          <h4>Where they disagreed</h4>
          <ul className="dis">{v.disagreements.map((d: any, i: number) => <li key={i}><b>{d.between}</b>: {d.resolution}</li>)}</ul>
        </>}

        {v.policy?.citations?.length > 0 && <>
          <h4>Cited from the policy</h4>
          <ul className="cite">{v.policy.citations.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>
        </>}

      </div>

      <div className="vside">
        <details className="letter" open>
          <summary>Letter to the claimant</summary>
          <div className="sheet">
            <div className="letterhead">
              <span className="lh-name">Claims Tribunal</span>
              <span className="lh-meta">{v.claim_id ?? ''}</span>
            </div>
            <pre>{v.letter || 'No letter was drafted. The arbiter’s output could not be parsed, so the claim goes to a human before anything is sent.'}</pre>
            <div className="sig">{v.letter ? 'Adjudicated by the Claims Tribunal · pending human sign-off' : 'Unsigned · nothing will be sent'}</div>
          </div>
        </details>

        <div className="gate">
          <h4>Human sign-off</h4>
          <div className="human">
            {decision
              ? <span className="recorded">Human decision recorded: <b>{decision}</b>
                {precedentId && <> · precedent <code className="precid">{precedentId}</code></>}</span>
              : <>
                <div className="rulebtns">
                  <button className="ok" onClick={() => record('approve')}>Approve</button>
                  <button className="no" onClick={() => record('deny')}>Deny</button>
                </div>
                <textarea rows={2} aria-label="reason for the record" value={override}
                  placeholder="Reason for the record. It becomes a precedent the next tribunal can cite."
                  onChange={e => setOverride(e.target.value)} />
                <button className="ov" onClick={() => record('override')} disabled={!override}>Override</button>
              </>}
          </div>
          <p className="cap">Recorded to tribunal/data/decisions.json with the tribunal's own ruling alongside yours. Overrides are indexed as precedents.</p>
        </div>
      </div>
    </section>
  )
}

/** v1 beside v2 with the ribbon between them: what the appeal actually moved. */
export function AppealDiff({ a, v1 }: { a: any; v1?: any }) {
  const one = a.v1 ?? v1 ?? {}
  const two = a.v2 ?? {}
  const overturned = String(a.outcome ?? '').toLowerCase().startsWith('overturn')
  const rows: any[] = a.diff ?? []
  const side = (v: any, tag: string) => (
    <div className={`vs ${v.decision ?? ''}`}>
      <span className="tag">{tag}</span>
      <span className="vdec small">{v.decision ?? '–'}</span>
      <span className="vconf">confidence {v.confidence == null ? '–' : Number(v.confidence).toFixed(2)}</span>
      <span className="vnet">{usd(v.payout?.net)}</span>
      {v.referral_reason && <p className="vref">{v.referral_reason}</p>}
    </div>
  )
  return (
    <section className="appeal-diff" aria-label="Appeal">
      <h3>Appeal</h3>
      <div className="agrid">
        {side(one, 'first ruling')}
        <div className="ribbon">
          <span className={`outcome ${overturned ? 'overturn' : 'uphold'}`}>{overturned ? 'OVERTURNED' : 'UPHELD'}</span>
          {a.appeal_text && <q className="atext">{a.appeal_text}</q>}
          {a.new_photo && <span className="chip">new photo</span>}
        </div>
        {side(two, 'on appeal')}
      </div>
      {rows.length > 0 && <ul className="diff">
        {rows.map((d: any, i: number) => typeof d === 'string'
          ? <li key={i}>{d}</li>
          : <li key={i}>
            <span className="fld">{String(d.field ?? d.key ?? d.name ?? '')}</span>
            <s>{String(d.from ?? d.v1 ?? d.before ?? '–')}</s>
            <span className="arrow">→</span>
            <b className={two.decision ?? ''}>{String(d.to ?? d.v2 ?? d.after ?? '–')}</b>
          </li>)}
      </ul>}
      <Clauses clauses={two.clauses ?? []} />
    </section>
  )
}
