---
name: strategy-fork-and-iterate
description: |
  Fork an existing strategy (yours or a shared one) and apply a focused
  modification. Reasons about the diff: what's the minimal change that
  achieves the user's stated intent, while preserving the original
  structure?
trigger: |
  Use when the user says "fork", "based on", "like that strategy but ...",
  "modify the shared strategy", "import this share link", or names a
  strategy_id and asks for a change. Do NOT use to author a strategy
  from scratch (use `strategy-creation`) or to just inspect a strategy
  (read `keel://strategy/<id>/source` directly).
knowledge:
  - reasoning_principles
  - composition_mechanics
  - dsl_syntax
  - collaboration
  - mistakes
tools:
  - keel_strategy_fork
  - keel_strategy_search
  - keel_strategy_get
  - keel_strategy_checkout
  - keel_strategy_status
  - keel_strategy_push
  - keel_strategy_log
  - keel_strategy_restore
  - keel_strategy_compose
  - keel_strategy_diff
  - keel_backtest_run
---

# Workflow

## Step 1: Resolve the source

Determine what the user wants to fork:

- A strategy_id they own (e.g., `str_K9p2Lz`) → call `keel_strategy_get` directly.
- A share link or share_id → call `keel_strategy_fork(source=<id>)` first; this returns a new owned `strategy_id`.
- "Like that strategy I had last week" → call `keel_strategy_search(query=<hint>, limit=5)` or `keel_strategy_search(limit=5)`, then confirm which one.

## Step 2: Checkout to a local workspace

Call `keel_strategy_checkout(strategy_id=<id>)`. This writes `strategy.py` + `.keel-meta.json` into a workspace dir (project-local if cwd has `.keel/workspace.yaml`, else `~/.keel/workspace/<id>/`). Read it end-to-end before proposing a change — the user's intent often presupposes structure that may or may not be there.

(For just *reading* the source without editing, fetch `keel://strategy/<id>/source` directly. The checkout pattern is for the edit-and-push flow.)

## Step 3: Reason about the minimal diff

The principle is **minimal-change**: preserve the original author's intent and structure. For each requested change, ask:

- Is this a parameter tweak (lookback, threshold)? → edit that one param.
- Is this a component swap (`EqualWeightSizer` → `VolWeightSizer`)? → swap one component, check downstream type compatibility.
- Is this a new branch (add a filter)? → add a `Parallel` block with the existing computation as one branch and the new filter as the other (see `composition_mechanics` and mistake M-19 — don't serialize with Store/Load).
- Is this a structural rewrite? → push back to the user. "This is a substantial rework; we should treat it as a new strategy. Want me to draft from scratch?"

Do NOT restructure or add components the user didn't ask for. If you spot an existing bug (e.g., M-22 wrong sizer order), flag it as a separate question — don't silently fix it.

## Step 4: Edit the local file, then push

Modify the checked-out `strategy.py` in place (the path is in the checkout response's `file` field — e.g. `/proj/strategies/<id>/strategy.py`). Then:

1. `keel_strategy_status` — confirms you're `ahead` (local has edits) and shows the last 5 commits.
2. `keel_strategy_push(strategy_id=<id>, message="<one-line rationale>")` — validates, commits as a new version. The platform tracks the version chain — your edit becomes `v(n+1)`. The commit message you pass shows up in `keel_strategy_log` and the web app history.

If `keel_backtest_run` complains `local_ahead`, you forgot the push — re-run with `auto_push=True` or push first.

## Step 5: Show the diff

Call `keel_strategy_diff(strategy_id=<id>, ref_a=<n>, ref_b=<n+1>)` and surface the structured diff to the user:

- Added components (`+`)
- Removed components (`-`)
- Changed params (`~`)
- The `hero_url` for the updated strategy

## Step 6 (when the user changes their mind): undo

If the user says "actually revert that" or "go back", use `keel_strategy_log` to find the prior sequence number, then `keel_strategy_restore ref=<n>` — it creates a forward-revert commit (history preserved). Pull the workspace afterward to catch up locally.

# First-Session Ownership

- Call `keel_ownership_status(strategy_id=<id>)` after fetching or checking out a fork.
- If ownership evidence is missing or stale, explain the next proof step before suggesting broad optimization.
- Use `keel://ownership/strategy/{strategy_id}` when you need the projection without another tool call.

# Common mistakes

- **Restructuring "for clarity" when the user asked for a one-line change** (collaboration rule). Preserve their intent and structure.
- **Skipping the diff in your reply.** The user wants to see *what changed*, not just "done". Always print the structured diff.
- **Forgetting buffered rebalancing on production-targeted edits** (M-30 catalog #9). If the original strategy was live, preserve `Execution(rebalance="buffered", ...)`.
- **Silently fixing unrelated bugs.** Flag them as a separate question. Don't conflate the fix with the requested change.
- **Re-running the full creation flow.** This skill is for *focused diff*, not whole-cloth rebuilds.

# Expected output shape

1. One-sentence summary of the change ("Swapped equal-weight sizing for vol-targeted sizing at 20% annualized vol target").
2. The structured diff (`+` / `-` / `~` lines).
3. The new `version` and `hero_url`.
4. (Optional) Suggested next step: re-backtest to compare.

# When NOT to use this skill

- **Authoring a brand-new strategy** → use `strategy-creation`. Don't fork a nominal "template" if the user's request is structurally different.
- **Just reading a strategy** → fetch `keel://strategy/<id>/source` directly, no skill needed.
- **Running a backtest** → use `backtest-and-analyze`. This skill stops at the persist + diff.
- **Live re-deploy** → use `deploy-and-monitor` once the new version is backtested.

# Test prompts

1. "Fork str_K9p2Lz and change the lookback from 20 to 30."
2. "Based on the shared strategy gDXjURKqWPs8CZ4eXdqAI, can you add a BTC beta hedge?"
3. "Take my funding-carry strategy and switch it to vol-targeted sizing."
