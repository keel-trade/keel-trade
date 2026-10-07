---
name: component-discovery
description: >
  Find the Keel component(s) that implement a concept the user names: search
  by concept, batch the candidates' schemas, show what precedes and follows the
  best one. Use when the user asks "what
  component does X", "is there a way to Z", "find a component for Y", or names
  a trading concept and wants what implements it. Not for composing a strategy
  (strategy-creation) or for one already-named component
  (keel_components_get).
tools:
  - keel_components_search
  - keel_components_get_many
  - keel_components_get
references:
  - step: 2
    topic: types
    why: "the type universe, when a filtered typed search proves absence (reference/)"
  - step: 4
    topic: composition_mechanics
    why: "how the candidate wires: slot-reading components, Parallel partners, the two mask systems"
  - step: 4
    topic: slots
    why: "slot reads/writes semantics for a slot-reading candidate (reference/)"
  - step: 4
    topic: strategy_patterns
    why: "the pattern a candidate usually lives in"
---

# Method

## Step 1 — Translate words into a concept

"Beta hedge", "trailing stop", "regime detection", "Kalman filter" — search
for the concept, not the literal word; the search is semantic and the catalog
holds names (`BetaHedgeAllocator`, `TrailingStop`, `FundingLevelRegime`)
that no pattern doc lists. A name from another platform is translated to the
Keel concept first.

## Step 2 — `keel_components_search`

Query the concept; narrow with `category`, `input_type` / `output_type`
(a filtered typed search is exhaustive — an empty one is the evidence for "no
component does X"; a thin keyword search is not), or `clock_direction`.
Surface the top 3–5, ranked, with one line each. Nothing fits → say "the
closest is X but it does not cover Y"; never fabricate a fit.

## Step 3 — Batch the candidates

`keel_components_get_many(names=[...])` once for the 3–5 candidates: full
params, types, slot reads/writes, examples, pitfalls, version — compared side
by side. A single component the user already named →
`keel_components_get(name=<name>)` is cheaper. Note when a locked version
differs from the latest; changing a pinned version is the user's call.

## Step 4 — Show how it wires

`keel_components_search(after=<name>)` lists what can follow the candidate;
`(before=<name>)` what can precede it. A component alone is half the answer —
what feeds it and what consumes it is the other half. For a candidate that
reads slots (`TradeManager(entries=, prices=)`, `AtEntry(slot=)`), say which
earlier `Store` it needs.

## Step 5 — Where discovery ends

This workflow ends at discovery. Building a new strategy with the component is
the `strategy-creation` workflow; adding it to an existing one is
`strategy-fork-and-iterate`.

# Decision points

- Universe / asset-filter questions are discovery like any other; there is no
  separate universe skill.
- The catalog is the truth, not training data: a component you have not seen
  in a result this session is not asserted to exist — or not to.

# Output shape

1. One-sentence interpretation of the concept.
2. Top 3–5 candidates: name × one line × category.
3. For the leading candidate: param highlights and 1–2 example usages.
4. What precedes and follows it.
5. If the user wants to build with it: `strategy-creation` (new strategy) or
   `strategy-fork-and-iterate` (existing strategy).

# When NOT to use

- Building a strategy → `strategy-creation`.
- One named component → `keel_components_get` (or
  `keel://components/<name>/schema` where the host browses resources).
- A failing strategy → `recover-from-error`; discovery fixes no validation
  error.

# Test prompts

1. "Is there a component for beta hedging in Keel?"
2. "How do I add a trailing stop to my strategy?"
3. "What components handle regime detection on funding rates?"
