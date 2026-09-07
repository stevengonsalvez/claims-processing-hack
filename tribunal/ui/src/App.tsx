import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

const API = import.meta.env.VITE_API ?? 'http://localhost:8000'

type Status = 'idle' | 'running' | 'done' | 'error'
type AgentState = { label: string; status: Status; text: string; ms?: number; data?: any; error?: string; extra?: any }
type Agents = Record<string, AgentState>
type Sample = { name: string; photo: string; statements: string[] }
type Span = { start: number; end?: number }

const ORDER = ['ocr', 'structure', 'adjuster', 'fraud', 'policy', 'arbiter']

/** One place for who each member of the court is: card headers, roster, waterfall lanes. */
const AGENTS: Record<string, { label: string; short: string; model: string; role: string; phase: string }> = {
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
const usd = (n: number | null | undefined) => n == null ? '–' : `$${Number(n).toLocaleString()}`
const clock = (ms: number) => `${String(Math.floor(ms / 60000)).padStart(2, '0')}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`

/** Minimal SSE parser over a fetch body. EventSource cannot POST multipart. */
async function readSSE(res: Response, onEvent: (ev: string, data: any) => void) {
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
  const [override, setOverride] = useState('')
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

  const patch = (a: string, p: Partial<AgentState>) =>
    setAgents(prev => ({ ...prev, [a]: { ...prev[a], ...p } }))

  const canRun = !running && (!!sample || files.statements.length > 0)

  const run = useCallback(async () => {
    if (running) return
    t0.current = Date.now()
    setAgents(fresh()); setTimeline({}); setElapsed(0); setClaim(null); setEvidence(null)
    setVerdict(null); setDecision(''); setOverride(''); setFatal(''); setConvened(true); setRunning(true)
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
        body: JSON.stringify({
          claim_id: claimId, human_decision: kind, reason: override,
          tribunal_decision: verdict?.decision, net: verdict?.payout?.net, fraud: verdict?.fraud?.score,
        }),
      })
      setDecision(res.ok ? kind : `${kind} (API error ${res.status})`)
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
  const statusText = { idle: 'Awaiting docket', session: `In session · ${clock(elapsed)}`, ruled: `Adjourned · ${(elapsed / 1000).toFixed(1)}s`, mistrial: 'Session ended' }[phase]

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
        <div className={`status ${phase}`} aria-live="off"><span className="pip" />{statusText}</div>
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
            : ORDER.map(a => <AgentCard key={a} id={a} s={agents[a]} />)}
        </section>

        <aside aria-label="The record">
          {photoUrl && <div className="panel exhibit">
            <h3>Exhibit A · damage photograph</h3>
            <img className="photo" src={photoUrl} alt={`Damage photograph filed with claim ${sample || 'upload'}`} />
            <p className="cap">Seen by the Adjuster and the Fraud Investigator.</p>
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

      {verdict && <Verdict v={verdict} seconds={elapsed / 1000} decision={decision}
        override={override} setOverride={setOverride} record={record} />}

      <footer>
        <span>gpt-4.1-mini · mistral-document-ai-2512 · text-embedding-3-large</span>
        <span>Microsoft Foundry agents · Azure AI Search · Application Insights tracing</span>
      </footer>
    </div>
  )
}

function Seal() {
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
function Waterfall({ timeline, now, running }: { timeline: Record<string, Span>; now: number; running: boolean }) {
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
        <h2>Session</h2>
        <p>{running ? 'The bench is sitting.' : 'Every member, on one clock.'}</p>
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

function AgentCard({ id, s }: { id: string; s: AgentState }) {
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
  const body = id === 'ocr' ? (s.extra?.preview ?? '') : id === 'structure' ? (s.data ? JSON.stringify(s.data, null, 1) : s.text) : s.text
  return (
    <div className={`agent ${s.status} ${id}`}>
      <div className="head">
        <span className="dot" />
        <span className="name">{s.label}</span>
        {badge && <span className="badge">{badge}</span>}
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

function Evidence({ e }: { e: any }) {
  const prior: any[] = e.prior_claims ?? []
  const chunks: any[] = e.policy_chunks ?? []
  // Everything over 0.75 is a strong match and stays `hot` (the documented line),
  // but only the strongest one is tagged — otherwise a tight cluster of scores
  // paints five identical badges and the real signal disappears.
  const topSim = Math.max(0, ...prior.map((x: any) => Number(x.similarity) || 0))
  const doc = chunks[0]?.title?.includes(' · ') ? chunks[0].title.split(' · ')[0] : ''
  return (
    <div className="panel">
      <h3>Evidence retrieved · Azure AI Search</h3>
      {prior.length > 0 && <>
        <h4>Prior claims · vector search</h4>
        <ul className="ev">
          {prior.map((p: any) => (
            <li key={p.id} className={[p.similarity > 0.75 && 'hot', p.similarity > 0.75 && Number(p.similarity) === topSim && 'top'].filter(Boolean).join(' ')}>
              <span className="lead"><b>{p.claim_id}</b> sim {p.similarity}</span>
              <span className="meta"> · {p.holder} · {p.vehicle} · {p.outcome} {usd(p.paid_amount)}</span>
              <i className="simbar" style={{ ['--v' as any]: `${Math.min(100, Math.max(0, Number(p.similarity) * 100))}%` }} />
            </li>
          ))}
        </ul>
        <p className="cap">Bars are cosine similarity, 0 to 1. The mark at 0.75 is the strong-match line.</p>
      </>}
      {chunks.length > 0 && <>
        <h4>Policy sections · hybrid search{doc && <em> · {doc}</em>}</h4>
        <ul className="pols">
          {chunks.map((c: any, i: number) => {
            const t = String(c.title ?? '')
            const sec = t.includes(' · ') ? t.split(' · ').slice(1).join(' · ') : t
            if (!sec || sec === doc) return null
            return <li key={i}>{sec}</li>
          })}
        </ul>
      </>}
    </div>
  )
}

function Verdict({ v, seconds, decision, override, setOverride, record }: any) {
  const p = v.payout ?? {}
  const score = Number(v.fraud?.score ?? 0)
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

        <div className="money">
          <div><span>claimed</span>{usd(p.claimed)}</div>
          <div><span>covered</span>{usd(p.covered)}</div>
          <div><span>deductible</span>{usd(p.deductible)}</div>
          <div className="net"><span>net payout</span>{usd(p.net)}</div>
        </div>

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

        <div className="gate">
          <h4>Human sign-off</h4>
          <div className="human">
            {decision
              ? <span className="recorded">Human decision recorded: <b>{decision}</b></span>
              : <>
                <button className="ok" onClick={() => record('approve')}>Approve</button>
                <button className="no" onClick={() => record('deny')}>Deny</button>
                <input placeholder="override reason" aria-label="override reason"
                  value={override} onChange={e => setOverride(e.target.value)} />
                <button className="ov" onClick={() => record('override')} disabled={!override}>Override</button>
              </>}
          </div>
          <p className="cap">Recorded to tribunal/data/decisions.json with the tribunal's own ruling alongside yours.</p>
        </div>
      </div>

      <div className="vside">
        <details className="letter" open>
          <summary>Letter to the claimant</summary>
          <div className="sheet">
            <div className="letterhead">
              <span className="lh-name">Claims Tribunal</span>
              <span className="lh-meta">{v.claim_id ?? ''}</span>
            </div>
            <pre>{v.letter || 'No letter was drafted. The arbiter\u2019s output could not be parsed, so the claim goes to a human before anything is sent.'}</pre>
            <div className="sig">{v.letter ? 'Adjudicated by the Claims Tribunal · pending human sign-off' : 'Unsigned · nothing will be sent'}</div>
          </div>
        </details>

      </div>
    </section>
  )
}
