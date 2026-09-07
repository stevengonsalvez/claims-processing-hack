# UI track — the courtroom

`tribunal/ui/src/App.tsx` + `App.css` + `index.html`. React 19 / Vite, **no new dependencies**
(no motion library, no icon set, no webfont — the seal is inline SVG, the fonts are system stacks).

## Identity

A court of record, not a dashboard. Three decisions carry it:

1. **Six named role colours.** Every member of the court owns a hue and keeps it everywhere —
   the roster, the card dot and badge, the waterfall lane. You can follow one agent across
   the whole page without reading a label. `--c-ocr` steel, `--c-structure` violet,
   `--c-adjuster` teal, `--c-fraud` clay, `--c-policy` brass, `--c-arbiter` bone.
2. **Typography on a contrast axis.** Serif (Iowan Old Style → Georgia) for the institution's
   voice: the masthead, the verdict stamp, the rationale, the letter. System sans for UI
   chrome. Mono for anything the machine produced — transcripts, docket numbers, money.
3. **One sheet of real paper.** The claimant letter is rendered as a document on `--paper`
   (#f4f0e6) with a letterhead, serif body and a signature rule, inverted out of the dark
   chamber. It is the only light surface on the page, because it is the only artefact that
   leaves the building.

The dark palette family was kept, deepened (`--bg #0a0c11`) and given a brass rather than
gold accent. Brass is used as authority — rules, the seal, the net-payout cell, the docket
number — never as decoration.

## The three things worth demoing

**Session waterfall.** Each agent's start and finish, plotted on one clock, ticking live at
100 ms. The window in which all three specialists are speaking at once is drawn as a labelled
band across *their* lanes only. This is the parallel fan-out made visible instead of asserted;
it is the same shape a judge will see in the App Insights trace.

**The bench laid out as it runs.** Two intake cards across the top, then the three specialists
side by side in one row, then the arbiter across the full width. The arbiter row is `1fr`, so
it absorbs whatever slack the record column leaves — the ruling always gets the room.

**Verdict reveal.** The decision word is a stamp: serif, uppercase, ruled box, rotated -1.6°,
landing from scale 1.5 / -8° with a blur wipe in 500 ms. The panel border takes the decision's
colour. `role="status" aria-live="polite"` announces "Verdict: approve, confidence 0.95,
net payout $9,300, fraud risk 0.00" to a screen reader.

## Motion

Purposeful, short, all ease-out. Cards rise in on a 45 ms stagger; a running card gets a
travelling highlight on its top edge and a haloed dot; a block caret blinks at the end of the
stream while tokens arrive; a finished card settles (scale 1.012 → 1) and its badge drops in.
Bars grow with a 100 ms linear transition matching the tick. Everything collapses to ~0 under
`prefers-reduced-motion: reduce`; every entrance uses `animation-fill-mode: backwards`, never
`forwards`, so a suppressed animation leaves content **visible**, never blank.

While an agent streams, its transcript follows the tokens; the moment it finishes, the pane
scrolls back to the top so its closing argument is read from the beginning. The tail is masked
so a clipped transcript reads as "more below" rather than a sentence that stops.

## Cognitive load and edge states

- **Empty state**: a roster — who sits, on what model, and what they contribute — plus
  "Nothing is decided by the machine alone". No blank cards before the first run.
- **Idle agent cards** show the agent's role instead of an empty box; running-but-silent
  shows "listening" with the caret.
- **Evidence**: prior claims get a similarity bar with a hairline at 0.75 (the strong-match
  line from the README). Every row over 0.75 keeps `hot` (amber id + amber bar), but only the
  single strongest one is tagged `match` — one crash2 run returned five scores in a
  0.752–0.764 cluster and five identical badges destroyed the signal. Policy chunks are
  demoted to chips with the repeated document name stripped.
- **No letter** (arbiter output unparseable): the sheet says so and signs itself
  "Unsigned · nothing will be sent" rather than rendering an empty page.
- **API unreachable / fatal error**: a `role="alert"` notice, and any still-running agent is
  marked errored rather than spinning forever.
- **Fraud gauge**: a flat track with hairlines at 0.40 and 0.70 and a solid fill coloured by
  band. An earlier tinted-gradient track read as a full bar at 0.00 — fixed.

## Keyboard and a11y

`1`–`5` pick a claim, `Enter` convenes, `A` approves, `D` denies; the legend is in the docket
bar. Shortcuts are suppressed while focus is in a field. Skip link to the bench, 2px brass
`:focus-visible` ring everywhere (file inputs are transparent overlays whose focus is
forwarded to the visible face), labelled controls, `aria-live` on the verdict.

Measured contrast on the shipped tokens (see "Proof"): lowest pair is **4.89:1**
(`.cap`, `.roles .how`, `.roster .hint`), everything else 5.2–16.4:1. Nothing is below 4.5.

## Responsive

1440 → two columns (bench 1.52fr / record 0.9fr), specialists three abreast, verdict two
columns. 1180 → single column, record panels reflow to `auto-fit minmax(300px)`, verdict one
column. 1000 → bench single column, money 2×2, citations one column, narrower waterfall
gutters. 680 → shortcut legend hidden, tighter padding. Verified by screenshot at 1440, 1000
and 720.

## DOM contract

`tribunal/validate.cjs` drives this page, so these hooks are load-bearing and were preserved
verbatim: the single `<select>` with sample options, `button.go`, `.claimid`,
`.agent` with `agent <status> <id>` **in that class order** and children `.name` `.badge`
`.ms`, `.verdict` with `.vdec` `.vconf`, `.money` with exactly four un-nested `div` cells in
claimed/covered/deductible/net order, `.fraudbar b`, `.verdict .ev li`, `.panel .ev li.hot`,
`.cite li`, `.dis li`, `.referral`, `.verdict pre`, `.human button.ok/.no/.ov`, `.recorded`.
The waterfall bar is `.wbar`, not `.bar`, because `.bar` belongs to the fraud gauge.
`.ev li.hot`'s "match" tag is a CSS `::after` so it never enters `textContent`.

## Proof

```
cd tribunal/ui && npm run build                                   # clean
PW=$(npm root -g)/expect-cli/node_modules/playwright-core node tribunal/validate.cjs crash2 http://localhost:5802 http://localhost:8423
PW=$(npm root -g)/expect-cli/node_modules/playwright-core node tribunal/validate.cjs crash4 http://localhost:5802 http://localhost:8423
```

Artifacts: `logs/expect-crash2/`, `logs/expect-crash4/` (verdict.json + screenshots + webm),
`logs/ui-states/` (idle at 1440 / 1000 / 720, plus reduced-motion), `logs/ui-keyboard/`
(1000px verdict reached entirely by keyboard: `2` → `Enter` → `A`).

## Known gaps

- The letter `<details>` ships open. Closing it hides the paper, which is the best thing on
  the page, so the collapse is there for keyboard users rather than as a default.
- Uploaded statement pages are not previewed as exhibits; only the damage photo is.
- The transcript panes have fixed max heights; a very long arbiter argument still scrolls.
