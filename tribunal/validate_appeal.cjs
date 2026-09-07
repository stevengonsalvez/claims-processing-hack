// End-to-end proof of the "beyond the spec" slice, driven entirely through the API
// (no browser: node's fetch + a small SSE parser). Adjudicate -> record the human
// override as a precedent -> read the case file -> appeal -> print the JSON.
//
//   node tribunal/validate_appeal.cjs [sample] [api-url]
//
// Artifacts: logs/appeal-<sample>/{run.log,adjudicate.sse,appeal.sse,result.json}
const fs = require('fs');
const path = require('path');
const [sample = 'crash4', api = 'http://localhost:8423'] = process.argv.slice(2);
const APPEAL_TEXT = 'I bought the Outback from Andrew Bennett in June 2025, bill of sale attached, this is my first claim';
const REASON = 'SIU cleared VIN reuse: bill of sale June 2025';
const out = path.resolve('logs', `appeal-${sample}`);
fs.mkdirSync(out, { recursive: true });
const log = (m) => { const l = `--- ${new Date().toISOString().slice(11, 19)} ${m}`; console.log(l); fs.appendFileSync(path.join(out, 'run.log'), l + '\n'); };

// Consume a text/event-stream, returning every {event, data} frame it carried.
async function sse(url, body, file) {
  const res = await fetch(url, { method: 'POST', body });
  if (!res.ok) throw new Error(`${url} -> ${res.status} ${await res.text()}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  const events = [];
  let buf = '';
  const raw = fs.createWriteStream(path.join(out, file));
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const chunk = dec.decode(value, { stream: true });
    raw.write(chunk);
    buf += chunk;
    let i;
    while ((i = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const m = /^event: (\S+)\ndata: ([\s\S]*)$/.exec(frame);
      if (m) { try { events.push({ event: m[1], data: JSON.parse(m[2]) }); } catch { /* partial frame */ } }
    }
  }
  raw.end();
  return events;
}
const last = (evs, name) => evs.filter(e => e.event === name).at(-1)?.data;

(async () => {
  log(`adjudicate sample=${sample}`);
  const t0 = Date.now();
  const evs = await sse(`${api}/adjudicate`, new URLSearchParams({ sample }), 'adjudicate.sse');
  const evidence = last(evs, 'evidence');
  const verdict = last(evs, 'verdict');
  if (!verdict) throw new Error('no verdict event');
  const claimId = verdict.claim_id;
  log(`v1 ${verdict.decision} for ${claimId} in ${((Date.now() - t0) / 1000).toFixed(1)}s`);

  const recycled = (evidence.photo_matches || []).filter(m => m.similarity > 0.85);
  log(`precedents=${(evidence.precedents || []).length} photo_matches>0.85=${recycled.length} clauses=${(verdict.clauses || []).length}`);

  log('record the human override as a precedent');
  const dec = await (await fetch(`${api}/decision`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ claim_id: claimId, human_decision: 'override', reason: REASON, verdict }),
  })).json();
  log(`decision recorded: ${JSON.stringify(dec)}`);

  const caseBefore = await (await fetch(`${api}/claims/${claimId}`)).json();
  log(`case file: verdict=${caseBefore.verdict.decision} decisions=${caseBefore.decisions.length} appeals=${caseBefore.appeals.length}`);

  log('appeal');
  const form = new FormData();
  form.set('claim_id', claimId);
  form.set('appeal_text', APPEAL_TEXT);
  const aevs = await sse(`${api}/appeal`, form, 'appeal.sse');
  const appeal = last(aevs, 'appeal.verdict');
  if (!appeal) throw new Error('no appeal.verdict event');
  log(`appeal ${appeal.outcome}: ${appeal.v1.decision} -> ${appeal.v2.decision}, ${appeal.diff.length} field(s) changed`);

  const caseAfter = await (await fetch(`${api}/claims/${claimId}`)).json();
  const result = {
    claim_id: claimId,
    v1: {
      decision: verdict.decision, net: verdict.payout.net, what_if: verdict.what_if,
      clauses: verdict.clauses,
    },
    evidence: {
      precedents: evidence.precedents,
      photo_description_chars: (evidence.photo_description || '').length,
      photo_matches: evidence.photo_matches,
      recycled_photo: recycled.map(m => `${m.file_name} already filed under ${m.claim_id} (similarity ${m.similarity})`),
    },
    decision: dec,
    appeal: {
      text: APPEAL_TEXT, outcome: appeal.outcome, diff: appeal.diff,
      v2_decision: appeal.v2.decision, v2_net: appeal.v2.payout.net,
      clauses: appeal.v2.appeal?.clauses || [],
      letter_words: (appeal.v2.letter || '').split(/\s+/).length,
    },
    case_file: { decisions: caseAfter.decisions.length, appeals: caseAfter.appeals.length },
    checks: {
      precedent_cited: (evidence.precedents || []).length > 0,
      recycled_photo_detected: recycled.length > 0,
      verdict_has_clauses: (verdict.clauses || []).length > 0,
      what_if_present: Boolean(verdict.what_if),
      precedent_recorded: Boolean(dec.precedent_id),
      appeal_ruled: ['uphold', 'overturn'].includes(appeal.outcome),
      appeal_stored: caseAfter.appeals.length > 0,
    },
  };
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 1));
  console.log(JSON.stringify(result, null, 1));
  const failed = Object.entries(result.checks).filter(([, ok]) => !ok).map(([k]) => k);
  log(failed.length ? `FAILED checks: ${failed.join(', ')}` : 'all checks passed');
  process.exit(failed.length ? 1 : 0);
})().catch(e => { log('FAILED ' + e.message); process.exit(1); });
