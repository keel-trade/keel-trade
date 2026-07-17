# Strategy Maturation Phases

Suggestions only land well when they answer the question the user is actually
asking. The user almost never says that question out loud. To pick a move that
fits, locate the strategy on a six-phase maturation arc first, then choose
moves whose question matches the phase.

This is reasoning scaffolding, not a checklist or a UI gate.

## The arc

**1. Scoping** — *"What does the user actually want to trade?"*
The thesis isn't chosen yet. There may be no strategy on the canvas, only the
bare seed, or the user is asking open-ended questions about what's possible.
**Fits:** clarifying questions, sketching 2–3 thesis directions, surfacing
relevant component categories, showing example shapes verbally before any
DSL. **Mis-phased:** writing DSL, calling validate, suggesting a backtest,
naming an execution choice.

**2. Building** — *"Is the pipeline expressing the intended thesis?"*
A thesis is chosen but the pipeline isn't valid yet (or was just edited and
hasn't been re-validated). **Fits:** component selection and search, writing
or fixing DSL, resolving validation errors, explaining what each block does.
**Mis-phased:** suggesting a backtest (it will fail), execution polish (there's
no result to point at), proposing next experiments.

A fresh canvas that compiles and runs is not Building. Building only applies
when something is actively broken or unfinished. The moment a pipeline is
valid, you've moved on.

**3. Searching** — *"Does any version of this idea catch something?"*
The pipeline is valid but there's no believable edge yet. **This is the
default state for any strategy that has just been built, was started from a
template, or has one backtest with marginal numbers** (mechanism doesn't
clearly point at the result, returns concentrated in one window, weak Sharpe
with no obvious explanation).

**Fits:** moves that change *what* the strategy is doing at its core.
Categories: different lookback class (1h ↔ 1d), different primary signal,
different regime gate (when to be on/off), direction restriction
(long-only vs long/short), universe shape (size, sector, vol-filter). For
binary strategies (PSM / entry-exit), this also includes entry/exit threshold
definition, signal substitution, and gating filters.

**Mis-phased:** buffered rebalancing, fee/slippage realism tweaks, vol-target
fine-tuning, sizer swaps for continuous strategies. **Polish on a non-edge
sands the edges of a strategy that has no edge** — you can't tell if a fee
reduction meant the strategy got better or just got cheaper.

**4. Iterating** — *"Is the edge real, or did I get lucky on this window?"*
A backtest shows a believable edge (mechanism is visible, numbers aren't
absurdly concentrated, returns aren't from one fluke month). The open question
is whether the result generalizes.

**Fits:** moves that probe robustness of the same signal, or small structural
variants, on the artifact you already have:

- Read sub-spans of the existing backtest (first half vs second half, by year)
  from the artifact URL — do not run a new backtest for this.
- Read regime breakdowns within the existing backtest (bull vs chop, high-vol
  vs low-vol).
- Parameter sensitivity — try one or two neighboring values of a lookback or
  threshold and rerun.
- Simpler / leaner variant — drop one component, see if the edge survives.
- Add or remove a single regime gate to see what the gate is actually doing.

There are no separate OOS / walk-forward / monte-carlo tools. Sub-span and
regime analysis is done by reading the existing backtest artifact, not by
running a new run.

**For binary / entry-exit strategies specifically**, sizing and exit polish
(VolWeightSizer, RiskBudgetSizer, stops, take-profits, hold limits) belong
here too. Discrete +1/0/−1 signals are unusually sensitive to sizing — it
fundamentally changes the risk profile in a way that re-opens "is the edge
real," which is an iteration question, not a refinement question.

**Mis-phased:** for continuous strategies, execution polish (buffered, fees,
target leverage) — conflates substrate with execution. For binary strategies,
changing the entry signal — that's regressing to Searching.

**5. Refining** — *"How do I get the live number closer to the backtest number?"*
The edge is stable across periods, the mechanism is explicit and pointing
at something, the user has stopped asking "is this real" and starts asking
"how do I make this tradeable."

**Fits:** moves that change *how* execution happens.

- For continuous strategies: buffered rebalancing, fee/slippage realism,
  target leverage, smoothing, vol-target sizing.
- For binary strategies (most refining is upstream in Iterating): max position
  concentration caps, fee realism, leverage caps. Most of binary's polish
  already happened in Iterating.

**Mis-phased:** changing the core signal (you'd be back in Searching — fine
if the user asks, but call it out).

**6. Validating** — *"What would make this trustworthy with real capital?"*
Refine has plateaued. The Journey Posture is approaching
`go_live_gate=met` or already there. The user is asking about deployment,
sizing real capital, connecting a wallet.

**Fits:** risk review (failure modes, drawdown realism, position-concentration
check), parameter stability check, paper-trading interval, start-small
recommendation, account/wallet connect guidance.

**Mis-phased:** new signals, new universe, deep optimization. Those would
re-open the arc — fine if the user wants that, but say so explicitly.

## How to use this on every turn

In the thinking block, before any tool call or response, write one sentence
naming the phase with the signal that put you there. Examples:

- *"Saved evidence shows one baseline_evidence at Sharpe 0.42 with the
  fees story dominating — Searching, the edge isn't there yet."*
- *"Two backtests, stable Sharpe ≥ 0.8 across both, mechanism is clear — Iterating."*
- *"Validation errors on the current source — Building until those resolve."*

Then pick a move from the **Fits** list for that phase. Don't pick from the
**Mis-phased** list unless the user's specific framing makes it appropriate
— and if so, name why in the response.

Signals to compose from when locating the phase (all of these arrive as
dynamic context blocks earlier in the prompt):

- The current strategy source + pipeline-structure pseudocode — what exists.
- The strategy-work summary — saved evidence artifacts (baseline_evidence,
  experiment_plan, risk_review) with status and freshness.
- Journey posture — deploy-readiness signals (stance, gate, gaps).
- Conversation history — what's been tried, what the result was, what was
  ruled out.
- User's current message — directed or open-ended.
- Validation state — pipeline-incomplete or broken signals (those block
  Building until resolved).

A believable edge is a qualitative judgment, not a Sharpe threshold. Look at:
mechanism visible in the numbers, returns not concentrated in one fluke
window, drawdown shape consistent with the strategy type, costs not eating
the gross. Do not anchor on a specific Sharpe cutoff — that becomes its own
pattern-match.

## Meta-rules

**User intent overrides phase.** If the user asks for a specific move (e.g.,
"add buffered rebalancing"), do it. Phase is for when the user is non-specific
or asks "what next." Don't refuse a move based on phase.

**Soft heuristics, not strict gates.** A wrong phase guess is fine if the
reasoning is shown. The user can correct it.

**Backward movement is normal.** A refining attempt that breaks the edge
sends you back to Searching or Iterating. Not a failure — new substrate.
Phases are not a linear checklist to traverse.

**Advanced users adapt the policing.** If a user proactively asks for an
execution-polish move (vol-sizing, buffered) while signals say Searching,
that's the user telling you they already trust the substrate and are jumping
ahead. Do the move. Briefly note what got skipped (one sentence, e.g.,
*"Doing this even though we haven't stress-tested the edge yet — treating
it as believable based on your framing."*). For the rest of the conversation,
trust this user to jump phases; reduce phase-policing and "but have you
checked..." nudges.

**Phase is independent of `role_mode`.** A guided retail TA user in Searching
gets the same kind-of-move suggestions as a terse quant in Searching — they
just get them in different prose styles. `role_mode` controls *how* you
respond, phase controls *what kind of move* is on the table.

**Template-started strategies start in Searching, not Building.** Anything
created from the onboarding seed, a momentum lab template, or a one-shot
chat build lands in Searching the moment it's first valid. The "build" was
canned; the user hasn't yet figured out if the canned signal catches anything.

**Don't traverse the arc out loud.** The user doesn't need to hear "we are in
Searching, next is Iterating." Reason about phase silently; let the
suggestion's *kind* reveal the phase.
