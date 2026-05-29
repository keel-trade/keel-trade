---
name: recover-from-error
description: |
  Triage and recover from repeated tool failures. Diagnoses auth gaps,
  config drift, stale schemas, and known landmines from the mistakes
  catalog. Auto-triggers after three consecutive `keel_*` tool errors
  in the same session.
trigger: |
  Use when three or more `keel_*` tool calls have failed in a row, OR
  when the user says "this keeps failing", "what's wrong", "I can't get
  past this error", "debug this", or pastes a tool error message. Do NOT
  use as a substitute for normal error messages — single failures are
  the originating skill's problem, not this one's.
knowledge:
  - mistakes
  - tool_usage
tools:
  - keel_status
  - keel_doctor
  - keel_auth_login
---

# Workflow

## Step 1: Capture the last 3 errors

Look at the recent tool-use accordion. Note:

- Tool names
- Error codes (HTTP status, validation code, traceback class)
- The arguments that caused them

If you can't see prior errors (fresh session), ask the user to paste the most recent error verbatim.

## Step 2: Run `keel_status`

This is the first triage call. It reports:

- Auth state (token present, expiry, scopes)
- Loaded toolsets (read-only / backtest / live / share)
- API reachability
- Recent error rate

Most "keeps failing" sessions trace to one of: expired token, missing toolset, wrong account context.

## Step 3: If status is clean, run `keel_doctor`

`keel_doctor` is a deeper probe — it walks through schema cache, network paths, and known config drift. Surface what it returns in plain prose, not raw JSON.

## Step 4: Match the error pattern against the mistakes catalog

The 30+ catalog entries cover the recurring landmines. Match the symptom to a known cause:

- `missing_required_component` → mistake catalog #1 (no Universe), #5 (no account_id)
- `type_mismatch` on compile → catalog #2 (sizer-signal shape mismatch)
- `unsupported_timeframe` → catalog #3 (target_timeframe vs data mismatch)
- 401/403 → auth or scope missing (call `keel_auth_login`; CLI equivalent: `keel auth login`)
- Compile error after edit → catalog #4 (validate-before-create skipped)
- `local_ahead` on `keel_backtest_run` → strategy is checked out + local has unpushed edits; `keel_strategy_push` first, OR re-run with `auto_push=True`, OR pass `commit_id=...` to test a historical version
- `conflict` / 409 on `keel_strategy_push` → server moved since checkout; `keel_strategy_status` to see who's ahead, then `keel_strategy_pull` (re-do edits) or `keel_strategy_push force=True` (overwrite their changes — destructive, confirm first)
- `missing_strategy_id` / "not in workspace" on push/pull/status → no checkout in cwd; `keel_strategy_workspaces` to list, or `keel_strategy_checkout <id>` to start one

Each entry has a documented fix. Apply it.

## Step 5: Propose ONE specific next step

Don't dump a list of "things to try". Pick the highest-likelihood fix and say:

> "The error trace points at <X>. Try <one specific command>. If that still fails, paste the new error and I'll dig deeper."

If the issue is auth, the next tool is usually `keel_auth_login` (or `keel auth login` in the CLI). If it's a config gap, the fix is usually editing the workspace's `workspace.yaml` or `.env`. If it's the platform's fault, escalate (link to a GitHub issue template).

## Step 6: Don't loop

If the same recover-from-error skill triggers twice in one session, the diagnostic loop isn't converging. Hand off to a human: surface the full error trace, the diagnostic output, and ask the user to report it. For reproducible bugs, the GitHub issue tracker at `https://github.com/keel-trade/keel-trade/issues` is the right place — instruct them to include the output of `keel doctor` and the exact prompt or command that triggered the failure. For credential, billing, or other private-account questions, `https://usekeel.io/contact` is the better channel. Don't trap them in agent retry hell.

# Common mistakes

- **Cargo-culting "run keel doctor" without reading the output.** The output names the problem; quote it back to the user.
- **Suggesting five "things to try" simultaneously.** Pick one, the most likely. If it fails, iterate.
- **Restarting the failing tool without changing inputs.** That's the definition of insanity. If the previous call failed with the same args, those args are the problem.
- **Hiding the original error.** Quote it verbatim so the user knows you read it.
- **Looping past 2 retries.** If the recovery itself fails twice, escalate to a human-filed issue.

# Expected output shape

1. One-sentence diagnosis ("Your token expired 2 hours ago — re-auth needed").
2. The diagnostic evidence (which call's output or status field led to the conclusion).
3. The single recommended next command.
4. (Optional) Catalog entry reference (e.g., "see mistakes catalog #5").

# When NOT to use this skill

- **First-time error, user not yet stuck** → let the originating skill explain. Recover-from-error is for the *third* failure, not the first.
- **The user is asking a "how do I" question, not a recovery question** → the relevant skill handles it.
- **The error is structural / requires code review** → escalate. This skill diagnoses agent-tool friction, not strategy logic bugs.

# Test prompts

1. "This keeps failing — same error every time."
2. "I can't get past this 422 error on strategy compose, here's the trace ..."
3. "Why does every backtest call return 401?"
