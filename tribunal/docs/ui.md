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

## Round two — precedent, explanation, and the claimant's side

Three things were added on top of the courtroom, all consuming fields the backend added in the
same round. Every one of them degrades to nothing if its field is absent: the section simply
does not render, and no run is ever blocked by a missing block.

### 1. The evidence board grew two more kinds of memory

**Precedents (`evidence.precedents`)** get their own `<ul class="prec">` under the prior-claims
list: mono claim id in brass, then `REFER → APPROVE` where the arrow and the human's word are
brass because that is where the authority sits, then the adjuster's reason set in quoted serif
italic. The reason is the whole point of a precedent, so it is typeset as prose, not as a field.
A caption states the stakes plainly: *"A human overrode the bench here. The tribunal is told, and
must answer it."*

**Photo matches (`evidence.photo_matches`)** live under **Exhibit A**, not in the evidence panel,
because they are a fact about the exhibit: this photograph has been filed before. A row at
similarity ≥ 0.90 takes `.same` and a red `same photo` tag in `--no` (the strongest fraud signal
on the page) rather than the amber `match` used for a merely-similar prior claim. The forensic
`photo_description` replaces the generic exhibit caption. When the list is empty the panel says
*"No prior photo resembles Exhibit A"* — on crash2 that absence is itself worth showing.

**Similarity bars are zoomed.** They previously mapped 0–1 onto the track, so crash4's cluster of
0.718–0.785 painted five bars all ~74% wide and only the hairline was readable. One helper,
`simPct(x)`, now maps **0.50–1.00** onto 0–100% and the hairline moved to `left: 50%` (= 0.75).
Prior claims, precedents and photo matches share that one scale, and the caption states the
domain. The `.ev li.hot` logic for prior claims is untouched — `validate.cjs` asserts it.

The Fraud Investigator's card grows a second badge, `photo seen · CLM-0412`, when the top photo
match is ≥ 0.85, so the recycled-photo finding is on the card that found it and not only in the
record. Policy chips are capped at six behind a `+N more` toggle; fourteen unranked chips were
pushing the ruling below the fold for no information gain.

### 2. The ruling explains itself, and you can interrogate the arithmetic

**Clauses (`verdict.clauses`)** render as an `<ol class="clauses">` after the rationale — a
deliberately *different* class from `.ev`, which `validate.cjs` reads as fraud evidence. Each
clause is serif prose with its `evidence_refs` below it as chips, glyph-prefixed by kind:
`§` policy, `#` prior claim, `▣` photo, `¶` precedent.

Clicking a chip is the load-bearing interaction. Every citable row carries a `data-ref`
(`policy:<section>` on `.pols li`, `prior_claim:<id>` on the prior `.ev li`, `photo:<id>` on
`.shots li`, `precedent:<precedent_id>` with `data-alt="precedent:<claim_id>"` on `.prec li`),
and the chip scrolls that row to the centre of the viewport, focuses it and lights it with a
brass outline for 1600 ms. Roughly 1,300 px separate the ruling from the record, so a class
toggle alone would have lit something nobody could see. A chip whose target is not on the page
renders as a `<span class="ref flat">`, not a dead button.

**What-if deductible.** `verdict.what_if {deductible, limit, covered}` drives a range slider
placed immediately *after* `.money` — never inside it, because `validate.cjs` requires exactly
four un-nested `div` cells. Net is recomputed entirely locally,
`max(0, min(covered, limit ?? covered) − d)`, so there is no round trip. While the slider is off
the ruling's own value, the deductible and net cells go dashed-brass `.hypothetical`, and a mono
formula line reads `$5,300 covered − $1,000 deductible = $4,300 · WHAT-IF · NOT THE RULING`. The
initial render equals the server's numbers exactly, so the assert on `.money` text is unchanged.
`aria-valuetext` speaks both numbers.

**The header carries the ruling.** `.status.ruled` takes `data-decision` and the pip takes the
verdict colour instead of always green; the text is `Adjourned · REFER · 41.5s`, and after an
appeal `Adjourned · OVERTURNED → APPROVE`.

**The override reason invites a precedent.** It was a single-line input beside two coloured
buttons; "SIU cleared the VIN reuse: bill of sale on file" was never going to be typed into that.
It is now a two-row `<textarea>` placeheld *"Reason for the record. It becomes a precedent the
next tribunal can cite."* `POST /decision` sends the full `verdict` per the new contract, and the
receipt reads `Human decision recorded: override · precedent PREC-…` with the id in mono brass.

### 3. `/claim/<id>` — the claimant's side of the building

`main.tsx` branches on `location.pathname` (`/^\/claim\/([^/?#]+)/`). No router dependency for
one branch. The bench grows a `Claimant view →` link once it has ruled.

The palette inverts: `body.claimant` is the paper ground, serif throughout, 720 px column, brass
rules. This deliberately is **not** the bench with the roster removed — the claimant does not sit
in the chamber, so there are no transcripts, no waterfall, no agent cards. They get a seal, one
status line with the decision as a small unrotated stamp, the letter as a document, and a form.

Appealing streams `POST /appeal` through the same `readSSE`. Instead of transcripts the claimant
sees a six-name list ticking `waiting → hearing⌷ → heard ✓` on `agent.done`, with an elapsed
clock. On `appeal.verdict` a serif sentence states the outcome ("The tribunal overturned its
decision."), the v2 letter replaces v1 with a `Revised <date>` letterhead, and `AppealDiff`
renders **v1 | ribbon | v2**: two mini verdicts in their own decision colours, a vertical brass
ribbon reading OVERTURNED or UPHELD with the appeal quoted beneath it and a `new photo` chip, the
`diff[]` rows as `FIELD  <s>old</s> → new`, and the v2 clauses through the same `Clauses`
component, chips and all. The same component renders on the bench below the verdict when
`GET /claims/{id}` reports an appeal, so both sides see the identical diff. A failed stream is a
`role="alert"`: *"The appeal could not be heard; your claim stays as decided."* A claim with no
file on record renders *"No claim `<id>` is on file"* and a way back.

Bench keyboard shortcuts are not mounted on this route: `App` never renders, so its listener
never binds.

### DOM contract, extended

New hooks, all chosen so the round-one asserts keep passing: `.prec li[data-ref]`,
`.shots li[data-ref]` (`.same` for ≥ 0.90), `.clauses li` + `button.ref`, `.lit`, `.whatif`
(`input[type=range]`, `.formula`, `.reset`), `.money div.hypothetical`, `.precid`,
`.claimant-link`, `.status[data-decision]`, `.more`, and on the claimant route `.cl`,
`.clstatus .vdec`, `form.appeal textarea`, `button.appeal-go`, `.clsteps li`, `.appeal-diff`,
`.appeal-diff .vs`, `.appeal-diff .outcome`, `.diff li`.

Deliberately **not** reused: `.ev` and `.hot`. `validate.cjs` reads `.panel .ev li.hot` as prior
claims and `.verdict .ev li` as fraud evidence; precedents, photo matches and clauses would have
silently corrupted both scrapes.

`validate.cjs` was extended, not replaced. It now also scrapes precedents, photo matches,
clauses, clause chips, the what-if formula and the status pill; clicks the first clause chip and
counts `.lit`; then opens `/claim/<id>`, lodges the appeal *"I bought the Outback from Andrew
Bennett in June 2025, bill of sale attached, this is my first claim"*, waits for `.appeal-diff`,
screenshots `4-claimant.png` and `4-claimant-appeal.png`, and fails hard if the v1 and v2 cards
or the outcome ribbon are missing. The crash2 leg (approve → `decisions.json`) is untouched.

## Round-two proof

```
cd tribunal/ui && npm run build                                   # clean
PW=$(npm root -g)/expect-cli/node_modules/playwright-core node tribunal/validate.cjs crash4 http://localhost:5802 http://localhost:8423
```

Artifacts: `logs/expect-crash4/{verdict.json,2-verdict.png,4-claimant.png,4-claimant-appeal.png,session.webm}`
and the transcript in `logs/ui-r2-validate.log`.

### Round-two results (last run, `logs/expect-crash4/`)

`decision approve` · pill `Adjourned · APPROVE · 42.3s` · **`console_errors: []`** · 3 precedent
rows · 3 photo rows, the top one `.same` (crash4.jpg already filed under CLM-0412) · 1 hot prior
claim · 3 clauses with live ref chips, `.lit` count 1 after clicking `¶ Precedent clearing VIN
reuse` · what-if formula rendered from `verdict.what_if` · claimant appeal `UPHELD`, both v1 and
v2 cards (`$3,850` each), diff row `confidence 0.9 → 0.95`, revised letter re-issued.

Two bugs the screenshots caught and this round fixed:

1. **Every clause chip was dead.** The exact-match resolver found nothing, because the Arbiter
   cites in its own words: `PREC-CLM-20260907-D865E7` for a precedent the record lists as
   `CLM-20260907-D865E7`, `"Coverage Components: Collision Coverage"` for a section titled
   `"Collision Coverage"`. `findRef()` now tries exact, then containment either way, then best
   token overlap (≥2 meaningful words), and a chip with no resolvable target still degrades to a
   flat `<span>`. Chips went from 0 live to live-and-scrolling.
2. **A black band across the claimant's letter.** `App.css` styles the bare element selector
   `header` as the chamber's sticky dark masthead, so the claimant page's own `<header>`
   inherited `position: sticky` + `--bg` and floated over the paper. Scoped out under
   `body.claimant .clhead`.

### Round-two known gaps

- The bench's `GET /claims/{id}` probe (for the appeal diff) fired on the same tick as the
  verdict and 404'd, because the API writes the case file through just after emitting the
  verdict. Harmless — it is caught — but it logged one console error, so the probe is now
  deferred 2.5 s. Re-proven: the recorded run reports `console_errors: []`.
- `appeal.verdict.diff` came back with one row (confidence) on an uphold, so the `<ul class="diff">`
  is proven thin. The overturn shape (four rows, `refer → approve`) is proven only through the
  backend's own `logs/beyond-appeal.log`, not through this browser run.
- A second appeal on the same claim is judged against v2, so the claimant form is hidden once an
  appeal exists rather than offering an appeal-of-an-appeal.
- The what-if slider recomputes net locally and never writes; there is no "adopt this figure"
  path, by design — the ruling is the ruling.
