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
  fs.writeFileSync(path.join(out, 'verdict.json'), JSON.stringify(v, null, 1));
  await ctx.close();
  await browser.close();
  const vid = fs.readdirSync(out).find(f => f.endsWith('.webm'));
  if (vid) fs.renameSync(path.join(out, vid), path.join(out, 'session.webm'));
  log('done');
})().catch(e => { log('FAILED ' + e.message); process.exit(1); });
