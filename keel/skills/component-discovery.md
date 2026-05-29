---
name: component-discovery
description: |
  Find the right Keel component(s) for a concept the user is reaching for.
  Walks the catalog by semantic search, then surfaces the matching
  components' shapes, params, and composition partners. Used standalone
  (research) or as the first step inside strategy-creation.
trigger: |
  Use when the user asks "what component does X", "find a component for
  Y", "is there a way to Z", "what's available for ...", or names a
  trading concept and wants to know what implements it. Do NOT use to
  actually compose a strategy (use `strategy-creation`); do NOT use to
  read a single named component (just hit `keel://components/<name>/schema`).
knowledge:
  - composition_mechanics
  - component_versioning
  - tool_usage
  - dsl_syntax
tools:
  - keel_components_search
  - keel_components_compose_help
  - keel_components_detail_batch
---

# Workflow

## Step 1: Translate the user's words into a search query

The user said "beta hedge" or "trailing stop" or "regime detection". Search for the **concept**, not just the literal word. The catalog has components named things like `BetaHedgeAllocator`, `TrailingStopExit`, `FundingLevelRegime` — these don't always appear in pattern docs (mistake M-28).

If the user used a name from another platform ("ATR-trailing stop", "Kalman filter"), translate to the Keel equivalent — the search is semantic.

## Step 2: Call `keel_components_search`

Call with the concept query. The response includes:

- Matching component names + categories
- One-line descriptions
- Their input/output shapes (which slots they consume + produce)

Surface the top 3-5 matches, not the full list. If nothing matches well, *say so* — don't fabricate a fit.

## Step 3: For the top candidates, batch-fetch full schemas

When the user is comparing options (3-5 candidates from search), call `keel_components_detail_batch(names=[...])` once instead of N separate `keel_components_compose_help` calls. One round-trip returns the full param schema + types + slot reads/writes + examples for every candidate, so you can compare them side-by-side before recommending.

For a single component the user named directly, `keel_components_compose_help(name=<name>)` is still cheaper (one component, no batch overhead).

## Step 4: Show composition partners

Call `keel_components_compose_help(name=<name>)` to see:

- What can precede this component (`before <name>`)
- What can follow this component (`after <name>`)
- Common pipelines that use it

This tells the user not just "here's a component" but "here's how you'd actually wire it in".

## Step 5: Hand off if appropriate

If the user wants to *use* this component (not just learn about it), suggest they invoke `strategy-creation` (new strategy) or `strategy-fork-and-iterate` (modify existing). This skill terminates at discovery.

# Common mistakes

- **Pattern-matching the word rather than the concept** (M-28). "Trailing stop" → search "trailing stop exit", not "stop" alone.
- **Suggesting a component without checking its current version.** Components evolve; the catalog is the truth, not your training data (component_versioning).
- **Fabricating a fit when nothing matches.** If the user's concept doesn't have a clean implementation, say "the closest is X but it doesn't cover Y" — don't pretend.
- **Dumping the full catalog.** Show the top 3-5 matches, ranked.
- **Forgetting the upstream/downstream context.** A component in isolation is half the answer; what feeds it and consumes it is the other half.

# Expected output shape

1. One-sentence interpretation of the user's concept ("You're asking about beta-hedging — sizing one leg to offset another's market exposure").
2. Top 3-5 candidate components: name × one-line description × category.
3. For the leading candidate: param schema highlights + 1-2 example usages.
4. Composition partners (before / after).
5. (Optional) "If you want to actually use this, I can run `strategy-creation` next."

# When NOT to use this skill

- **The user wants to build a strategy** → use `strategy-creation`. Don't get stuck in research mode.
- **The user asked for a specific named component** → read `keel://components/<name>/schema` directly, no skill needed.
- **The user is debugging a failing strategy** → use `recover-from-error`. Component discovery won't fix a validation error.
- **Universe / asset filtering specifically** → fold into this skill (no separate universe-selection skill in the 8-skill set — universe questions search the catalog like anything else).

# Test prompts

1. "Is there a component for beta hedging in Keel?"
2. "How do I add a trailing stop to my strategy?"
3. "What components handle regime detection on funding rates?"
