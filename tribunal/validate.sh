#!/bin/bash
# Scripted browser walkthrough of one demo claim via expect-cli (Playwright, headless).
# Every expect call is bounded with `timeout`: a combined click+wait step hangs the daemon.
# Artifacts: logs/expect-<claim>/{run.log,verdict.json,*.png,*.webm}
#   API_PORT=8423 tribunal/validate.sh crash2 [http://localhost:5802]
set -uo pipefail
cd "$(dirname "$0")/.."
C=${1:-crash2}; URL=${2:-http://localhost:5802}; OUT=logs/expect-$C; mkdir -p "$OUT"; LOG=$OUT/run.log; : > "$LOG"
step() { echo "--- $(date +%T) $1" | tee -a "$LOG"; }
pw() { timeout "${3:-40}" expect-cli playwright "$1" --description "$2" 2>&1 | tee -a "$LOG"; }
shot() { p=$(timeout 40 expect-cli screenshot ${2:-} 2>&1 | tee -a "$LOG" | grep -oE '/[^ "]+\.png' | head -1); [ -n "$p" ] && cp -f "$p" "$OUT/$1.png"; }

step "open $URL"; timeout 60 expect-cli open "$URL" --browser chromium --wait-until networkidle 2>&1 | tee -a "$LOG"
step "select $C, convene"
pw "await page.waitForSelector('select', {state: 'attached'}); await page.selectOption('select', '$C'); await page.click('button.go'); return 'clicked'" "convene $C"
sleep 20; step "mid-run screenshot (streaming)"; shot 1-streaming
step "poll for verdict"
for i in $(seq 1 16); do
  st=$(pw "return await page.evaluate(() => ({verdict: !!document.querySelector('.verdict'), running: [...document.querySelectorAll('.agent.running .name')].map(e => e.textContent)}))" "poll $i" 30 | tr -d '\n ')
  echo "$st" | grep -q '"verdict":true' && break
  sleep 10
done
pw "return await page.evaluate(() => ({
      claim: document.querySelector('.claimid')?.textContent,
      decision: document.querySelector('.vdec')?.textContent,
      confidence: document.querySelector('.vconf')?.textContent,
      money: [...document.querySelectorAll('.money div')].map(d => d.textContent),
      fraud: document.querySelector('.fraud b')?.textContent,
      fraud_evidence: [...document.querySelectorAll('.verdict .ev li')].map(e => e.textContent),
      hot_prior_claims: [...document.querySelectorAll('.panel .ev li.hot')].map(e => e.textContent),
      citations: [...document.querySelectorAll('.cite li')].map(e => e.textContent),
      disagreements: [...document.querySelectorAll('.dis li')].map(e => e.textContent),
      referral: document.querySelector('.refer')?.textContent,
      letter_words: (document.querySelector('.verdict pre')?.textContent || '').split(/\s+/).length,
      agents: [...document.querySelectorAll('.agent')].map(a => ({name: a.querySelector('.name')?.textContent,
        status: a.className.split(' ')[1], badge: a.querySelector('.badge')?.textContent, s: a.querySelector('.ms')?.textContent}))
    }))" "verdict $C" | tee "$OUT/verdict.json"
step "verdict screenshot"; shot 2-verdict --full-page
if [ "$C" = crash2 ]; then
  step "human approve"
  pw "await page.click('.human button.ok'); await page.waitForSelector('.recorded', {timeout: 10000}); return await page.evaluate(() => document.querySelector('.recorded')?.textContent)" "approve $C"
  shot 3-recorded
  curl -s "http://localhost:${API_PORT:-8423}/decisions" | tail -c 400 | tee -a "$LOG"; echo
fi
step "console logs"; timeout 30 expect-cli console_logs 2>&1 | tee -a "$LOG" | tail -5
timeout 30 expect-cli close 2>&1 | tee -a "$LOG"
cp -f "$(ls -t /tmp/expect-artifacts/session-*.webm 2>/dev/null | head -1)" "$OUT/session.webm" 2>/dev/null
step "artifacts"; ls -la "$OUT" | tee -a "$LOG"
