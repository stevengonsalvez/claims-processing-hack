import { useEffect, useRef, useState } from 'react'
import './App.css'

const API = import.meta.env.VITE_API ?? 'http://localhost:8000'

type Status = 'idle' | 'running' | 'done' | 'error'
type AgentState = { label: string; status: Status; text: string; ms?: number; data?: any; error?: string; extra?: any }
type Agents = Record<string, AgentState>
type Sample = { name: string; photo: string; statements: string[] }

const ORDER = ['ocr', 'structure', 'adjuster', 'fraud', 'policy', 'arbiter']
const LABELS: Record<string, string> = {
  ocr: 'OCR · Mistral Document AI', structure: 'Structuring · gpt-4.1-mini',
  adjuster: 'Adjuster', fraud: 'Fraud Investigator', policy: 'Policy Analyst', arbiter: 'Arbiter',
}
const fresh = (): Agents => Object.fromEntries(ORDER.map(a => [a, { label: LABELS[a], status: 'idle', text: '' }]))
const usd = (n: number | null | undefined) => n == null ? '–' : `$${Number(n).toLocaleString()}`

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
  const [claimId, setClaimId] = useState('')
  const [claim, setClaim] = useState<any>(null)
  const [evidence, setEvidence] = useState<any>(null)
  const [verdict, setVerdict] = useState<any>(null)
  const [running, setRunning] = useState(false)
  const [decision, setDecision] = useState<string>('')
  const [override, setOverride] = useState('')

  useEffect(() => { fetch(`${API}/samples`).then(r => r.json()).then(s => { setSamples(s); setSample(s[0]?.name ?? '') }) }, [])

  const patch = (a: string, p: Partial<AgentState>) =>
    setAgents(prev => ({ ...prev, [a]: { ...prev[a], ...p } }))

  async function run() {
    setAgents(fresh()); setClaim(null); setEvidence(null); setVerdict(null); setDecision(''); setRunning(true)
    const form = new FormData()
    if (files.statements.length) { files.statements.forEach(f => form.append('statements', f)); if (files.photo) form.append('photo', files.photo) }
    else form.append('sample', sample)
    try {
      const res = await fetch(`${API}/adjudicate`, { method: 'POST', body: form })
      await readSSE(res, (ev, d) => {
        switch (ev) {
          case 'claim.start': setClaimId(d.claim_id); break
          case 'agent.start': patch(d.agent, { status: 'running', text: '' }); break
          case 'agent.token': setAgents(prev => ({ ...prev, [d.agent]: { ...prev[d.agent], text: prev[d.agent].text + d.text } })); break
          case 'agent.done': patch(d.agent, { status: 'done', ms: d.ms, data: d.data, extra: d, text: d.prose ?? '' }); break
          case 'claim': setClaim(d); break
          case 'evidence': setEvidence(d); break
          case 'verdict': setVerdict(d); break
          case 'error': patch(d.agent in LABELS ? d.agent : 'arbiter', { status: 'error', error: d.message }); break
        }
      })
    } finally { setRunning(false) }
  }

  async function record(kind: string) {
    const res = await fetch(`${API}/decision`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ claim_id: claimId, human_decision: kind, reason: override, tribunal_decision: verdict?.decision, net: verdict?.payout?.net, fraud: verdict?.fraud?.score }) })
    setDecision(res.ok ? kind : `${kind} (API error ${res.status})`)
  }

  const photoUrl = files.photo ? URL.createObjectURL(files.photo) : sample ? `${API}${samples.find(s => s.name === sample)?.photo}` : ''

  return (
    <div className="app">
      <header>
        <div className="brand"><span className="gavel">⚖</span> Claims Tribunal</div>
        <div className="sub">four agents argue every claim · gpt-4.1-mini · mistral-document-ai · text-embedding-3-large · Azure AI Search · Foundry</div>
      </header>

      <section className="intake">
        <div className="pick">
          <label>Sample claim</label>
          <select value={sample} disabled={running || files.statements.length > 0} onChange={e => setSample(e.target.value)}>
            {samples.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
          <label>or upload</label>
          <input type="file" accept="image/*" multiple disabled={running} onChange={e => setFiles(f => ({ ...f, statements: Array.from(e.target.files ?? []) }))} title="statement pages" />
          <input type="file" accept="image/*" disabled={running} onChange={e => setFiles(f => ({ ...f, photo: e.target.files?.[0] }))} title="damage photo" />
          <button className="go" onClick={run} disabled={running || (!sample && !files.statements.length)}>{running ? 'In session…' : 'Convene tribunal'}</button>
          {claimId && <span className="claimid">{claimId}</span>}
        </div>
        {photoUrl && <img className="photo" src={photoUrl} alt="damage" />}
      </section>

      <main>
        <section className="bench">
          {ORDER.map(a => <AgentCard key={a} id={a} s={agents[a]} />)}
        </section>
        <aside>
          {claim && <Panel title="Claim record">
            <dl>{['policy_number', 'policy_holder_name', 'vehicle_year_make_model', 'vehicle_vin', 'incident_date', 'claim_request'].map(k =>
              <div key={k}><dt>{k.replaceAll('_', ' ')}</dt><dd>{String(claim[k] ?? '–')}</dd></div>)}</dl>
          </Panel>}
          {evidence && <Panel title="Evidence retrieved (AI Search)">
            <ul className="ev">
              {evidence.prior_claims?.map((p: any) => <li key={p.id} className={p.similarity > 0.75 ? 'hot' : ''}>
                <b>{p.claim_id}</b> sim {p.similarity} · {p.holder} · {p.vehicle} · {p.outcome} {usd(p.paid_amount)}</li>)}
              {evidence.policy_chunks?.map((c: any, i: number) => <li key={i} className="pol">{c.title}</li>)}
            </ul>
          </Panel>}
          {verdict && <Verdict v={verdict} decision={decision} override={override} setOverride={setOverride} record={record} />}
        </aside>
      </main>
    </div>
  )
}

function AgentCard({ id, s }: { id: string; s: AgentState }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { ref.current?.scrollTo({ top: ref.current.scrollHeight }) }, [s.text])
  const badge = id === 'fraud' && s.data?.fraud_score != null ? `risk ${Number(s.data.fraud_score).toFixed(2)}`
    : id === 'adjuster' && s.data?.estimated_total != null ? `est ${usd(s.data.estimated_total)}`
    : id === 'policy' && s.data?.coverage_decision ? s.data.coverage_decision
    : id === 'arbiter' && s.data?.decision ? s.data.decision.toUpperCase()
    : id === 'ocr' && s.extra?.chars ? `${s.extra.chars} chars · ${s.extra.pages} pages`
    : id === 'structure' && s.data ? `${Object.values(s.data).filter(v => v != null).length} fields` : ''
  const body = id === 'ocr' ? (s.extra?.preview ?? '') : id === 'structure' ? (s.data ? JSON.stringify(s.data, null, 1) : s.text) : s.text
  return (
    <div className={`agent ${s.status} ${id}`}>
      <div className="head"><span className="dot" /><span className="name">{s.label}</span>
        {badge && <span className="badge">{badge}</span>}
        {s.ms != null && <span className="ms">{(s.ms / 1000).toFixed(1)}s</span>}</div>
      <div className="stream" ref={ref}>{s.error ? <span className="err">{s.error}</span> : body || (s.status === 'running' ? '…' : '')}</div>
    </div>
  )
}

function Panel({ title, children }: { title: string; children: any }) {
  return <div className="panel"><h3>{title}</h3>{children}</div>
}

function Verdict({ v, decision, override, setOverride, record }: any) {
  const p = v.payout ?? {}
  return (
    <div className={`verdict ${v.decision}`}>
      <div className="vhead"><span className="vdec">{v.decision}</span><span className="vconf">confidence {Number(v.confidence).toFixed(2)}</span></div>
      <p className="rationale">{v.rationale}</p>
      {v.referral_reason && <p className="refer">Referral: {v.referral_reason}</p>}
      {v.disagreements?.length > 0 && <ul className="dis">{v.disagreements.map((d: any, i: number) => <li key={i}><b>{d.between}</b>: {d.resolution}</li>)}</ul>}
      <div className="money">
        <div><span>claimed</span>{usd(p.claimed)}</div><div><span>covered</span>{usd(p.covered)}</div>
        <div><span>deductible</span>{usd(p.deductible)}</div><div className="net"><span>net payout</span>{usd(p.net)}</div>
      </div>
      <div className="fraudbar"><span>fraud risk</span><div className="bar"><i style={{ width: `${Math.round((v.fraud?.score ?? 0) * 100)}%` }} /></div><b>{Number(v.fraud?.score ?? 0).toFixed(2)}</b></div>
      {v.fraud?.evidence?.length > 0 && <ul className="ev">{v.fraud.evidence.map((e: any, i: number) => <li key={i}><b>{e.type}</b> {e.detail}{e.ref ? ` (${e.ref})` : ''}</li>)}</ul>}
      {v.policy?.citations?.length > 0 && <ul className="cite">{v.policy.citations.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>}
      <details><summary>Letter to claimant</summary><pre>{v.letter}</pre></details>
      <div className="human">
        {decision ? <span className="recorded">Human decision recorded: <b>{decision}</b></span> : <>
          <button className="ok" onClick={() => record('approve')}>Approve</button>
          <button className="no" onClick={() => record('deny')}>Deny</button>
          <input placeholder="override reason" value={override} onChange={e => setOverride(e.target.value)} />
          <button className="ov" onClick={() => record('override')} disabled={!override}>Override</button>
        </>}
      </div>
    </div>
  )
}
