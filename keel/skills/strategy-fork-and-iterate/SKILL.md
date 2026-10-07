---
name: strategy-fork-and-iterate
description: >
  Change an existing strategy with one focused edit: the user's own strategy
  takes the change as its next version (earlier versions and their backtests
  stay in its history); a shared link or a Keel Library entry is forked first.
  Read the whole source, choose the smallest edit that meets the intent,
  dry-run, save once per user-visible step, explain the change view. Use when
  the user names a strategy and asks for a change, or says "fork", "based on",
  "like that but ...", "import this share link", "start from the library".
  Not for authoring from scratch (strategy-creation) or for merely reading a
  strategy (keel_strategy_get).
tools:
  - keel_strategy_search
  - keel_strategy_get
  - keel_strategy_fork
  - keel_library_list
  - keel_library_fork
  - keel_strategy_compose
  - keel_strategy_diff
  - keel_strategy_history
  - keel_strategy_checkout
  - keel_strategy_status
  - keel_strategy_push
  - keel_strategy_restore
  - keel_backtest_run
references:
  - step: 3
    topic: dsl_syntax
    why: "'Strategy Template & Iterative Changes' — which declarations never move without being asked; scope-to-request examples"
  - step: 3
    topic: composition_mechanics
    why: "adding a branch: Parallel semantics, branch isolation, the two mask systems"
  - step: 3
    topic: reasoning_principles
    why: "invariants a swap must preserve: terminal WeightSeries, one clock, each transform once"
  - step: 4
    topic: "rule:<CODE>"
    why: "the lesson behind a validation issue code the dry run returns"
  - step: 4
    topic: mistakes
    why: "structural mistakes to flag (not fix) when you meet them in the existing source"
---

# Method

## Step 1 — Resolve the source

- A strategy id the user owns → go to Step 2. Its variants (a 10D vs 30D
  lookback, a buffer sweep) are VERSIONS of that strategy — one compose save
  each, `message` naming the change — not forks, the same as the Keel app,
  which saves every edit as a version; there are no unsaved variants. A sweep
  is small (2–4 versions): one backtest each, `keel_backtest_compare` once,
  then `keel_strategy_restore(ref=<n>)` makes the chosen version HEAD — HEAD
  is the pick, not whichever version was saved last. Earlier versions and
  their backtests stay in the strategy's history, so a before/after
  comparison needs no fork, and the reply names the save by its version
  (v2, v3 …), never as a fork. Fork only to copy someone else's, a share link or a
  Library strategy, when the user asks for a separate copy, or when the work
  heads in an entirely new direction (Step 3's structural rewrite).
- A share link / share id → `keel_strategy_fork(source=<id>)` → a new owned
  `strategy_id`.
- A Keel Library entry → `keel_library_list` for the slug, then
  `keel_library_fork(slug=...)` → a new owned `strategy_id`.
- "That strategy from last week" → `keel_strategy_search(query=..., limit=5)`,
  confirm which.

## Step 2 — Read the whole source

`keel_strategy_get(strategy_id=<id>, include_source=true)` and read it end to end
before proposing anything — the request often presupposes structure that is
or is not there. (Hosts that browse resources may read
`keel://strategy/<id>/source` instead.)

<!-- profile: full -->

On the CLI / local MCP, `keel_strategy_checkout(strategy_id=<id>)` writes
`strategy.py` + `.keel-meta.json` into a workspace (project-local when cwd has
`.keel/workspace.yaml`, else `~/.keel/workspace/<id>/`); the rest of the
method then runs through that file.

<!-- /profile -->

## Step 3 — Choose the smallest edit

For each requested change, classify it:

- parameter tweak (lookback, threshold) → edit that one value;
- component swap (`EqualWeightSizer` → `VolWeightSizer`) → one component,
  then check the downstream type and any slot it now needs;
- new branch (a filter, a confirmation) → a `Parallel` with the existing
  computation as one branch, not a serial Store/Load chain;
- structural rewrite → push back: "this is a new strategy; want me to draft
  it from scratch?"

Never change the architecture (discrete ↔ continuous, single ↔ multi-signal)
without asking. Never remove or alter anything unrelated to the request; a bug
you notice is a separate question, not a silent fix. `Execution(...)` stays as
it is unless the user asks to change it.

## Step 4 — Dry run, then save once per user-visible step

1. `keel_strategy_compose(strategy_id=<id>, source=<edited>, dry_run=True)` — fix
   what it names (`keel_help(topic="rule:<CODE>")` for the lesson).
2. `keel_strategy_compose(strategy_id=<id>, source=<edited>, message=<the change>)`
   — the next version, visible in `keel_strategy_history` and the web app under
   that message (derived from the change when omitted). A dry run is the
   agent's own check: the user sees one "Draft check" row, so while drafting
   the reply says the draft is being revised rather than quoting parser or
   validator text, unless the user asked for the detail. Save once per
   user-visible step; the save's `view` — its card, which carries the name,
   the change and the links — is what the reply explains, by name. When the
   question is what differs between two strategies or two versions,
   `keel_strategy_diff` answers it (two strategies: pass each one's source)
   and draws the change as a card; the reply names the change in a line
   rather than pasting either source.

<!-- profile: full -->

With a checkout, edit `strategy.py` in place, then `keel_strategy_status`
(confirms `ahead`) and `keel_strategy_push(strategy_id=<id>, message=...)`. A
`local_ahead` refusal from `keel_backtest_run` means the push is missing —
push, or re-run with `auto_push=True`.

<!-- /profile -->

## Step 5 — Undo when the user changes their mind

`keel_strategy_history` for the prior version; then
`keel_strategy_restore(strategy_id=<id>, ref=<n>)` — a forward commit that makes
that version HEAD, history preserved (the same as reading it with
`keel_strategy_get(version=<n>, include_source=true)` and saving it again).

<!-- profile: full -->

With a checkout, pull the workspace afterwards.

<!-- /profile -->

# Decision points

- When the user asks for improvements: 1–2 that are useful and easy to
  understand (parameter, buffering, smoothing, a second signal) — never a list
  of ten.
- "Get more trades" → thresholds / filters / hold periods, not a path change;
  "too much turnover" → smooth the signal or `buffered`; "this period was bad"
  → diagnose the mechanism, no rearchitecture.
- A larger change is proposed and waits for the user's approval.

# Output shape

1. The save's `view`: where the host draws it the user already sees it —
   explain it in your own words; elsewhere its markdown stands in.
2. One sentence naming the change ("Swapped equal-weight sizing for
   vol-targeted sizing at 20% annualized").
3. The new `version` and `hero_url`.
4. If the user wants the change tested: a backtest of the new version,
   compared against the previous version's run.

# When NOT to use

- A brand-new strategy → `strategy-creation` (do not fork a nominal template
  for a structurally different idea).
- Just reading a strategy → `keel_strategy_get(include_source=true)`.
- Running the backtest → `backtest-and-analyze`.

# Test prompts

1. "Fork str_K9p2Lz and change the lookback from 20 to 30."
2. "Based on the shared strategy gDXjURKqWPs8CZ4eXdqAI, can you add a BTC
   beta hedge?"
3. "Take my funding-carry strategy and switch it to vol-targeted sizing."
