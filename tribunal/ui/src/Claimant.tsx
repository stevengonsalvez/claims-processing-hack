import { useEffect, useRef, useState } from 'react'
import { AGENTS, API, AppealDiff, ORDER, Seal, readSSE, usd } from './App'

type Step = 'waiting' | 'running' | 'heard'

/** The claimant's world. Same tribunal, opposite side of the bench: paper ground,
 *  serif throughout, one column, no transcripts. What arrives in the post. */
export default function Claimant({ id }: { id: string }) {
  const [state, setState] = useState<'loading' | 'ok' | 'missing'>('loading')
  const [file, setFile] = useState<any>(null)
  const [text, setText] = useState('')
  const [photo, setPhoto] = useState<File | undefined>()
  const [steps, setSteps] = useState<Record<string, Step>>({})
  const [running, setRunning] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [appeal, setAppeal] = useState<any>(null)
  const [err, setErr] = useState('')
  const t0 = useRef(0)

  useEffect(() => {
    document.body.classList.add('claimant')
    return () => { document.body.classList.remove('claimant') }
  }, [])

  useEffect(() => {
    fetch(`${API}/claims/${id}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error('404')))
      .then(c => {
        if (!c?.verdict) { setState('missing'); return }
        setFile(c); setState('ok')
        if (c.appeals?.length) setAppeal(c.appeals[c.appeals.length - 1])
      })
      .catch(() => setState('missing'))
  }, [id])

  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setElapsed(Date.now() - t0.current), 100)
    return () => clearInterval(t)
  }, [running])

  async function lodge(e: React.FormEvent) {
    e.preventDefault()
    if (running || !text.trim()) return
    t0.current = Date.now()
    setRunning(true); setErr(''); setAppeal(null); setElapsed(0)
    setSteps(Object.fromEntries(ORDER.map(a => [a, 'waiting' as Step])))
    const form = new FormData()
    form.append('claim_id', id)
    form.append('appeal_text', text)
    if (photo) form.append('photo', photo)
    try {
      const res = await fetch(`${API}/appeal`, { method: 'POST', body: form })
      if (!res.ok || !res.body) throw new Error(`the tribunal replied ${res.status}`)
      let v2: any = null
      await readSSE(res, (ev, d) => {
        if (ev === 'agent.start') setSteps(s => ({ ...s, [d.agent]: 'running' }))
        else if (ev === 'agent.done') setSteps(s => ({ ...s, [d.agent]: 'heard' }))
        else if (ev === 'verdict') v2 = d
        else if (ev === 'appeal.verdict') setAppeal({ ...d, appeal_text: text, new_photo: !!photo })
        else if (ev === 'error' && !d.agent) setErr(d.message || 'The appeal could not be heard.')
      })
      setAppeal((a: any) => a ?? (v2 ? { v1: file?.verdict, v2, outcome: 'uphold', diff: [], appeal_text: text, new_photo: !!photo } : a))
    } catch (e: any) {
      setErr(`The appeal could not be heard; your claim stays as decided.${e?.message ? ` (${e.message})` : ''}`)
    } finally {
      setElapsed(Date.now() - t0.current)
      setRunning(false)
    }
  }

  if (state === 'loading') return <div className="cl"><p className="clnote">Fetching your claim file…</p></div>
  if (state === 'missing') return (
    <div className="cl">
      <p className="clnote" role="alert">No claim <code>{id}</code> is on file.</p>
      <p><a className="back" href="/">← Back to the tribunal</a></p>
    </div>
  )

  const v1 = file.verdict
  const current = appeal?.v2 ?? v1
  const overturned = appeal && String(appeal.outcome ?? '').toLowerCase().startsWith('overturn')

  return (
    <div className="cl">
      <header className="clhead">
        <Seal />
        <div>
          <h1>Claims Tribunal</h1>
          <p className="clref">Claim {id}</p>
        </div>
      </header>

      <p className="clstatus" role="status" aria-live="polite">
        {running
          ? <>Your appeal is being heard · <b>{(elapsed / 1000).toFixed(1)}s</b></>
          : <>Your claim has been decided.</>}
        <span className={`vdec small ${current.decision}`}>{current.decision}</span>
      </p>

      <div className="sheet">
        <div className="letterhead">
          <span className="lh-name">Claims Tribunal</span>
          <span className="lh-meta">{appeal ? `Revised ${new Date().toISOString().slice(0, 10)}` : id}</span>
        </div>
        <pre>{current.letter || 'Your claim is with a human adjuster. You will hear from us directly.'}</pre>
        <div className="sig">
          {appeal ? 'Re-issued after appeal · Claims Tribunal' : 'Adjudicated by the Claims Tribunal'}
        </div>
      </div>

      {running && <ol className="clsteps" aria-label="Appeal progress">
        {ORDER.map(a => (
          <li key={a} className={steps[a] ?? 'waiting'}>
            <span className="who">{AGENTS[a].short}</span>
            <span className="st">{steps[a] === 'heard' ? 'heard ✓' : steps[a] === 'running' ? <>hearing<i className="caret" /></> : 'waiting'}</span>
          </li>
        ))}
      </ol>}

      {err && <p className="clnote" role="alert">{err}</p>}

      {appeal && <>
        <p className={`outcome ${overturned ? 'overturn' : 'uphold'}`}>
          {overturned
            ? 'The tribunal overturned its decision.'
            : 'Your appeal was heard, and the original decision stands.'}
        </p>
        <AppealDiff a={{ ...appeal, v1: appeal.v1 ?? v1 }} v1={v1} />
      </>}

      {!appeal && !running && <form className="appeal" onSubmit={lodge}>
        <h2>Appeal this decision</h2>
        <label htmlFor="atext">Tell the tribunal what it missed</label>
        <textarea id="atext" name="appeal_text" rows={5} required value={text}
          onChange={e => setText(e.target.value)}
          placeholder="Anything the file did not show: a bill of sale, a repair invoice, a date the tribunal read wrongly." />
        <div className="arow">
          <label className="file">
            <input type="file" accept="image/*" onChange={e => setPhoto(e.target.files?.[0])} />
            <span>{photo ? photo.name.slice(0, 26) : 'Attach a new photograph'}</span>
          </label>
          <button className="appeal-go" type="submit" disabled={!text.trim()}>Lodge appeal</button>
        </div>
        <p className="clfine">The same six members sit again, with your words and anything you attach in front of them.</p>
      </form>}

      <p className="clback"><a className="back" href="/">← The bench</a></p>
    </div>
  )
}

export { usd }
