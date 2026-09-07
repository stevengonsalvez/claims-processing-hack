// Deterministic browser walkthrough of one demo claim with expect-cli's bundled
// playwright-core (the expect daemon wedges on pages holding an SSE stream open).
// Artifacts: logs/expect-<claim>/{run.log,verdict.json,1-streaming.png,2-verdict.png,3-recorded.png,session.webm}
//   PW=$(npm root -g)/expect-cli/node_modules/playwright-core node tribunal/validate.cjs crash2 [ui-url] [api-url]
const pw = require(process.env.PW || 'playwright-core');
const fs = require('fs');
const path = require('path');
const [claim = 'crash2', url = 'http://localhost:5802', api = 'http://localhost:8423'] = process.argv.slice(2);
const out = path.resolve('logs', `expect-${claim}`);
fs.mkdirSync(out, { recursive: true });
const log = (m) => { const l = `--- ${new Date().toISOString().slice(11, 19)} ${m}`; console.log(l); fs.appendFileSync(path.join(out, 'run.log'), l + '\n'); };

(async () => {
  const browser = await pw.chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, recordVideo: { dir: out, size: { width: 1440, height: 1000 } } });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  log(`open ${url}`);
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForSelector('select option', { state: 'attached' });
  await page.selectOption('select', claim);
  await page.click('button.go');
  log(`convene ${claim}`);
  await page.waitForSelector('.agent.running', { timeout: 20000 });
  const t0 = Date.now();
  await page.waitForTimeout(20000);
  await page.screenshot({ path: path.join(out, '1-streaming.png') });
  log('streaming screenshot');
  await page.waitForSelector('.verdict', { timeout: 170000 });
  const seconds = ((Date.now() - t0) / 1000).toFixed(1);
  await page.waitForTimeout(800);
  const v = await page.evaluate(() => ({
    claim: document.querySelector('.claimid')?.textContent,
    decision: document.querySelector('.vdec')?.textContent,
    confidence: document.querySelector('.vconf')?.textContent,
    money: [...document.querySelectorAll('.money div')].map(d => d.textContent),
    fraud: document.querySelector('.fraudbar b')?.textContent,
    fraud_evidence: [...document.querySelectorAll('.verdict .ev li')].map(e => e.textContent),
    hot_prior_claims: [...document.querySelectorAll('.panel .ev li.hot')].map(e => e.textContent),
    citations: [...document.querySelectorAll('.cite li')].map(e => e.textContent),
    disagreements: [...document.querySelectorAll('.dis li')].map(e => e.textContent),
    referral: document.querySelector('.referral')?.textContent,
    letter_words: (document.querySelector('.verdict pre')?.textContent || '').split(/\s+/).length,
    precedents: [...document.querySelectorAll('.prec li')].map(e => e.textContent),
    photo_matches: [...document.querySelectorAll('.shots li')].map(e => ({ text: e.textContent, same: e.classList.contains('same') })),
    clauses: [...document.querySelectorAll('.clauses li')].map(e => e.textContent),
    clause_refs: [...document.querySelectorAll('.clauses button.ref')].map(e => e.textContent),
    whatif: document.querySelector('.whatif .formula')?.textContent,
    status_pill: document.querySelector('.status')?.textContent,
    agents: [...document.querySelectorAll('.agent')].map(a => ({ name: a.querySelector('.name')?.textContent, status: a.className.split(' ')[1], badge: a.querySelector('.badge')?.textContent, s: a.querySelector('.ms')?.textContent })),
  }));
  v.seconds_to_verdict = Number(seconds);
  v.console_errors = consoleErrors;
  log(`verdict ${v.decision} in ${seconds}s`);
  await page.screenshot({ path: path.join(out, '2-verdict.png'), fullPage: true });
  if (claim === 'crash2') {
    await page.click('.human button.ok');
    await page.waitForSelector('.recorded', { timeout: 10000 });
    log('approve: ' + await page.textContent('.recorded'));
    await page.screenshot({ path: path.join(out, '3-recorded.png') });
    await page.waitForTimeout(1500);  // the UI posts the decision asynchronously
    const d = await (await fetch(`${api}/decisions`)).json();
    v.decisions_json = { entries: d.length, last: d.at(-1) };
    log(`decisions.json entries=${d.length} last=${JSON.stringify(d.at(-1))}`);
  }
  // ── the claimant's side: open /claim/<id>, appeal, wait for the second ruling ──
  if (claim !== 'crash2' && v.claim) {
    const chip = await page.$('.clauses button.ref');
    if (chip) {
      await chip.click();
      await page.waitForTimeout(700);
      v.lit_after_clause_click = await page.evaluate(() => document.querySelectorAll('.lit').length);
    }
    log(`claimant view /claim/${v.claim}`);
    await page.goto(`${url}/claim/${v.claim}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('form.appeal textarea', { timeout: 20000 });
    await page.screenshot({ path: path.join(out, '4-claimant.png'), fullPage: true });
    await page.fill('form.appeal textarea', 'I bought the Outback from Andrew Bennett in June 2025, bill of sale attached, this is my first claim');
    await page.click('button.appeal-go');
    log('appeal lodged');
    await page.waitForSelector('.appeal-diff', { timeout: 200000 });
    await page.waitForTimeout(1000);
    v.claimant = await page.evaluate(() => ({
      decision_stamp: document.querySelector('.clstatus .vdec')?.textContent,
      outcome: document.querySelector('.appeal-diff .outcome')?.textContent,
      sides: [...document.querySelectorAll('.appeal-diff .vs')].map(e => ({
        tag: e.querySelector('.tag')?.textContent,
        decision: e.querySelector('.vdec')?.textContent,
        net: e.querySelector('.vnet')?.textContent,
      })),
      diff: [...document.querySelectorAll('.diff li')].map(e => e.textContent),
      letter_words: (document.querySelector('.sheet pre')?.textContent || '').split(/\s+/).length,
    }));
    await page.screenshot({ path: path.join(out, '4-claimant-appeal.png'), fullPage: true });
    log(`appeal ${v.claimant.outcome} · sides ${v.claimant.sides.length} · diff ${v.claimant.diff.length}`);
    if (v.claimant.sides.length < 2) throw new Error('claimant page: v1 and v2 cards not both present');
    if (!v.claimant.outcome) throw new Error('claimant page: no appeal outcome rendered');
  }
  v.console_errors = consoleErrors;
  fs.writeFileSync(path.join(out, 'verdict.json'), JSON.stringify(v, null, 1));
  await ctx.close();
  await browser.close();
  const vid = fs.readdirSync(out).find(f => f.endsWith('.webm'));
  if (vid) fs.renameSync(path.join(out, vid), path.join(out, 'session.webm'));
  log('done');
})().catch(e => { log('FAILED ' + e.message); process.exit(1); });
