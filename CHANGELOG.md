# Changelog

All notable changes to `keel-trade` are documented here. Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), and the format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.6.1] — 2026-06-16

**Hotfix for 0.6.0.** The validator parity work in 0.6.0 introduced a
top-level `import pandas as pd` in `pipeline_engine/validation_shared.py`
for a single duration-string parse. `pandas` is not declared as a wheel
runtime dependency, so any environment that didn't already have it
installed (`pipx install keel-trade` and the `.mcpb` bundle on first
launch) hit `ModuleNotFoundError: No module named 'pandas'` as soon as
the validator loaded. 0.6.1 replaces the parse with a stdlib regex; no
behavioral change to `bar_offset` validation.

### Fixed

- `parse_bar_offset_minutes` in `pipeline_engine/validation_shared.py`
  no longer requires `pandas`. Stdlib regex parses `'15min'`, `'30min'`,
  `'1h'`, `'12h'`, `'1d'`, `'90min'` etc. with the same rules and the
  same error messages. Same return values, same validation surface.

## [0.6.0] — 2026-06-16

**Validator parity with the browser editor + agent knowledge refresh.**

The SDK's strategy validator now agrees bit-for-bit with the Keel web
editor. A strategy that passes `keel strategy validate` (or the matching
MCP tool) will pass in the browser canvas, and vice versa — same error
codes, same messages, same verdict. Several agent skills and the bundled
knowledge surface picked up new content alongside.

This is an SDK release — what's in this changelog is everything that
ships in `pipx install keel-trade` (and the `.mcpb` bundle, and the
public `keel-trade/keel-trade` GitHub repo). The Keel platform backend
that the SDK talks to (backtest worker, live execution, eval worker,
signing service) has its own release cadence and is not changed by this
version.

### Added

- New agent knowledge doc `costs_and_fees.md` — Hyperliquid maker/taker,
  Keel builder fees by plan, backtest cost defaults. Agents answer "how
  much does this cost" from facts, not guesses.
- Skill updates in `strategy-creation`, `strategy-fork-and-iterate`, and
  `backtest-and-analyze` — clearer guidance on the compose → validate →
  backtest loop and when to call which tool.
- Glama directory metadata (`glama.json`) and Glama score badge on the
  public mirror README (for the awesome-mcp-servers listing).

### Changed

- **Full TS↔Python validator parity** (Option C type policy). The SDK's
  DSL validator and the browser editor's validator now emit the same
  error codes for the same compositions. Affects `keel strategy
  validate`, every MCP composition tool, and the in-browser canvas.

### Fixed

- `DICT_*` validation codes are now errors, not warnings. They
  represented composition shapes that crashed at runtime; promoting them
  to errors blocks the strategy before it reaches a backtest, with a
  clear message instead of a silent failure.
- `RegimeScale` component accepts a Series index and broadcasts cleanly
  across the universe.

### Compatibility

- `DICT_*` warning→error promotion: any strategy that compiled but
  emitted a `DICT_*` warning may now fail validation. The conditions are
  the same that previously crashed at runtime — the failure is earlier
  and louder.
- Validator parity tightens edge cases that were previously inconsistent
  between the SDK and browser. A small number of compositions that
  passed in one but failed in the other will now consistently pass or
  fail in both.

## [0.5.7] — 2026-06-03

Universe resolution lifecycle fixes. Closes the silent-failure mode that hit
the first external paying user: strategies pushed via CLI/MCP without
resolving the universe deployed cleanly but failed at every eval-worker tick
with no visibility to the agent. The web editor's auto-resolve-on-change
behavior now has a CLI/MCP analog.

### Added

- `universe_resolve(source)` MCP tool: reads criteria from the strategy
  source, calls `/v1/universe/resolve`, and returns the source with
  `resolved=[...]` and `resolved_at=...` baked in. No criteria args — the DSL
  is the source of truth. Pairs with `universe_set(source, ...)`: agents call
  `set` then `resolve` to produce a deploy-ready source.
- `keel universe resolve <file>` CLI command: same flow, reads from a file or
  stdin/workspace and writes the resolved source back in place.
- New validator codes `UNRESOLVED_UNIVERSE` (when `resolved` is missing/empty)
  and `STALE_UNIVERSE` (when `top_n` or `symbols` changed without
  re-resolving). Warnings in editor mode, errors when validating for
  production paths.

### Changed

- Knowledge bundle (`dsl_syntax.md`, `universe_selection.md`) updated to
  describe the `universe_set → universe_resolve` chain so agents pick the
  right tool sequence.
- Deprecated form `keel universe resolve --mode --top-n ...` still works and
  emits a one-line deprecation warning. Will be removed in 0.6.x.

### Compatibility

- Backend release v1.85 (shipped 2026-06-03) refuses `deploy` /
  `backtest_submit` when the strategy's compiled universe is unresolved or
  stale, with a clear 422 pointing at the unblock action. Strategies whose
  compiled blob predates v1.85 (no `universe` key in the spec) are
  grandfathered through as a back-compat — re-pushing the strategy after
  upgrading the SDK is what activates the new validation for them.
- All existing CLI commands, MCP tools, and DSL surfaces unchanged. Strategies
  with `resolved=[...]` already baked in (web-editor-created, HRP-shape) are
  unaffected.

## [0.5.6] — 2026-06-01

**Metadata-only refresh** for the Official MCP Registry listing. No
behavioral change to the SDK or MCP surface.

### Changed

- Tighter Registry description aligned to the landing page positioning:
  `Build, backtest, and automate Hyperliquid trading strategies — typed,
  deterministic, live parity.` Replaces the previous product-style
  framing on registry.modelcontextprotocol.io so the canonical entry
  cascades the right copy to downstream directories (PulseMCP auto-
  ingests from the Registry).

## [0.5.5] — 2026-06-01

**Adds Official MCP Registry verification marker.** No behavioral change
to the SDK or MCP surface. AGENTS.md (the PyPI package readme) now
contains an HTML-comment `mcp-name` marker that the Official MCP
Registry uses to verify ownership of the PyPI package
`keel-trade` and link it to the registry server name
`io.github.keel-trade/keel-trade`. Comment is invisible in rendered
markdown but visible to the registry's verification scraper.

### Added

- `<!-- mcp-name: io.github.keel-trade/keel-trade -->` at the top of
  AGENTS.md, enabling PyPI-package verification for the Official MCP
  Registry submission (registry.modelcontextprotocol.io).

## [0.5.4] — 2026-06-01

**Proactivity + friendlier defaults across the outcome surface.**
Agents were asking the user too many questions before doing anything —
this release closes the schema/handler gaps that forced those
questions, adds a logout tool so users can switch accounts without a
terminal, and fixes a `keel_status` bug where a transient identity
probe failure contradicted `authenticated: true` with a misleading
"session likely expired" hint.

### Added

- `keel_auth_logout` — MCP outcome tool wrapping
  `keel.auth.clear_credentials()`. Same shape as `keel_auth_login`,
  toolset `always`, returns `next: [keel_auth_login]` so the agent
  knows the round-trip for switching accounts.

### Changed

- `keel_backtest_run`: `start_date` is now optional and defaults to
  `2024-08-15` (earliest cached Hyperliquid data). Description now
  reads "when the user says 'backtest X' without dates, just run
  it — mention the dates used in your reply." Agents stop asking for
  a date range first.
- `keel_live_monitor`: `deployment_id` is now optional and defaults
  to the portfolio summary across every deployment. Handler already
  supported this; the required-flag in the schema was forcing agents
  to ask "which deployment?".
- `keel_help`: `topic` is now optional. Bare `keel_help` returns the
  list of bundled topics plus a one-line orientation, so an agent
  trying to find the right doc doesn't have to guess a slug.
- `keel_backtest_summarize`: description now explicitly says "BE
  PROACTIVE — after `keel_backtest_run` returns, call this
  automatically; don't ask 'do you want the full metrics?' first."
- `keel_strategy_pull` / `_push` / `_discard` / `_status`: raw
  `str(e)` from the workspace lib is now wrapped with a
  "Couldn't <verb> {strategy_id}: <e>" framing plus a concrete
  next-step suggestion.

### Fixed

- `keel_status`: only an actual `AuthError` (401 from `/v1/me`) flips
  `authenticated` to `false` and adds the `keel_auth_login` next-hint.
  Network blips, 5xx, and parse errors now surface `identity_error`
  for visibility but no longer contradict `authenticated: true` with
  a misleading "may need to re-auth" message. Reproduced and pinned
  with two new tests.

## [0.5.3] — 2026-05-31

**MCPB Python ABI fix — single cross-platform bundle.** v0.5.2's
platform-specific bundles shipped Python-3.11-compiled `.so` files
(pydantic_core, cryptography, cffi, …), which broke under Claude
Desktop's default launcher when it picked up Python 3.12 from
homebrew (`ModuleNotFoundError: pydantic_core._pydantic_core`).
0.5.3 ships a single ~430 KB cross-platform `.mcpb` containing only
pure-Python keel + pipeline_engine; runtime deps are pip-installed
on first launch into `~/.keel/mcpb-lib/py3.X/`. Works under any
Python 3.11+ on macOS, Windows, and Linux.

### Changed

- MCPB bundle is now a single cross-platform asset
  `keel-trade-0.5.3.mcpb`, replacing the per-platform
  `keel-trade-0.5.2-darwin.mcpb` / `-win32.mcpb` / `-linux.mcpb` set.
- First launch installs runtime deps for the current Python version
  (~10-30 sec); subsequent launches are instant (cache reused).
- `scripts/mcpb_bootstrap.py` — new entry point shipped inside the
  bundle. Handles dep install, sys.path setup, and
  `importlib.invalidate_caches()` after install (Python caches
  negative path lookups, so a fresh install isn't visible to
  subsequent imports without the explicit invalidation).
- `.github/workflows/build-mcpb.yml` — dropped the macOS / Windows /
  Linux matrix; the bundle is now built once on ubuntu-latest.

### Fixed

- `ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`
  when Claude Desktop launched the bundle with a different Python
  minor version than the one it was built against.

## [0.5.2] — 2026-05-31

**MCPB bundle distribution.** A platform-specific `.mcpb` bundle of
keel-trade ships alongside the PyPI wheel for one-click install in
Claude Desktop and distribution via the Anthropic Connectors Directory
and Smithery. Same MCP server, same outcome tools, same browser-OAuth
flow — no SDK code changes, no terminal required.

### Added

- `.mcpb` bundles for darwin, win32, and linux attached as release
  assets at
  https://github.com/keel-trade/keel-trade/releases/tag/v0.5.2. Drag
  onto Claude Desktop for a one-click install. User still needs system
  Python 3.11+ (same prerequisite as `pipx install keel-trade`).
- `scripts/build_mcpb.py` — reproducible build script in the SDK.
  Reads runtime deps from `pyproject.toml`, copies `keel/` +
  `pipeline_engine/` + vendored deps into a staging dir, and runs
  `@anthropic-ai/mcpb pack`. Produces
  `dist/keel-trade-<version>-<platform>.mcpb`.
- `scripts/manifest.template.json` — MCPB v0.3 manifest template with
  `{{VERSION}}` and `{{PLATFORMS_JSON}}` substitution.

## [0.5.1] — 2026-05-29

**Public mirror at github.com/keel-trade/keel-trade + housekeeping.**
The package now has a public GitHub mirror with Issues, Discussions,
and a synced release pipeline. The CHANGELOG, agent skills, and
package metadata all link to it.

### Added

- `[project.urls]` in `pyproject.toml` now includes `Repository`,
  `Issues`, `Discussions`, `Changelog`, and `Product page` entries
  pointing at the new mirror + the keel-mcp landing page. PyPI's
  sidebar now surfaces all the relevant external surfaces.
- `recover-from-error` skill routes reproducible technical bugs to
  `github.com/keel-trade/keel-trade/issues` (with instructions to
  include `keel doctor` output) and reserves `usekeel.io/contact` for
  credential / billing / private-account questions.

### Changed

- Code docstrings + comments across 17 files updated to refer to
  upstream Python modules by name (e.g. "the upstream
  `pipeline_engine.mcp.tools` module", "the API canonical
  `PaginatedResponse`") rather than by their internal source paths.
  Pure cosmetic — no behavior change. Makes the bundled SDK readable
  to public-mirror users without exposing irrelevant internal layout.
- Test files that mocked against staging URLs now use
  `staging-api.example.com` instead of the previous internal Tailscale
  hostname. All 601 tests pass identically.
- `CHANGELOG.md` historical entries scrubbed of internal monorepo
  paths in the same way.

### Fixed

- Empty test runs that previously left a stale `.benchmarks/` directory
  no longer pollute the SDK source tree (new `.gitignore` covers it
  along with all the usual Python build/cache artifacts).

## [0.5.0] — 2026-05-25

**Ships the 0.4.2 candidate (never tagged) plus three follow-on deltas.**
0.4.2 was prepared with the MCP-driven login work but was held back from
PyPI while live MCP smoke validated the recovery loop. Rather than ship
a backdated 0.4.2 now, this release rolls those changes into 0.5.0 along
with the post-0.4.2 work below.

### Added (post-0.4.2)

- **`auth_surface` parameter on `browser_login` + `keel_auth_login`**
  tags the OAuth authorize URL with `entry=mcp_auth`, `auth_surface=mcp`,
  and `utm_source/medium/campaign=keel_mcp/auth/mcp_auth_signup` when
  invoked via the MCP outcome. The keel-app `/oauth/connect` page +
  Clerk sign-up flow propagate the markers so `account_created`
  carries end-to-end MCP-origin attribution. CLI `keel auth login`
  unchanged (no `auth_surface` set).

### Changed (post-0.4.2)

- **`end_date` is now optional on `keel_backtest_run` and
  `keel backtest run`.** When omitted, the SDK fills today's UTC date,
  so agents can run open-ended backtests without computing the current
  date. The MCP input_schema drops `end_date` from `required`, the
  missing-date-range error message updates to mention only `start_date`,
  and the tool description calls out the new default. Existing callers
  that pass `end_date` are unaffected.
- Bundled SDK `registry.json` regenerated against the current
  `COMPONENT_REGISTRY`. Suggestion + option semantics preserved across
  the regen so existing pinned-version agents see no spec drift.

---

## [0.4.2] — 2026-05-20 (folded into 0.5.0 — never tagged on PyPI)

**MCP-driven login — agents can sign in without a terminal.** Stdio
MCP servers can't use Claude Code's built-in HTTP-MCP OAuth ceremony
(that's HTTP-transport-only). The v0.4.0/0.4.1 flow forced users to
exit Claude and run `keel auth login` in a separate terminal — the
opposite of the "talk to your agent" promise. This release closes the
loop with a `keel_auth_login` MCP tool the agent calls directly.

The new install + first-touch flow is:

```bash
pipx install keel-trade
claude mcp add keel -- keel mcp serve
```

Then open Claude and say *"Connect to Keel."* — the agent calls
`keel_auth_login`, browser opens, sign-in completes, tokens land in
`~/.keel/config.yaml`. No terminal-side login dance.

### Added

- **`keel_auth_login` MCP-only outcome tool** — runs the same OAuth 2.1
  + PKCE loopback flow as the CLI's `keel auth login`. Optional args:
  `scope="live"` to pre-check the live-trading consent box; `api_url`
  to target staging or a self-hosted Keel. Returns the same concise
  summary as the CLI command (authenticated/principal_id/org_id/plan/
  tier + next-hint). Always available in the `always` toolset — agents
  can call it before any authenticated tool.
- **`mcp_only` field on `OutcomeTool`** — declares that an outcome is
  MCP-only, so the CLI adapter doesn't try to register a duplicate
  command on top of a hand-rolled CLI (`keel auth login` stays
  hand-rolled to keep its bespoke help text + `--key` plumbing).
- **`recovery_tool` + `recovery_tool_args` on `KeelError`** —
  subclasses declare which MCP tool an agent should call to recover.
  `AuthError` → `keel_auth_login`. `EntitlementError` →
  `keel_auth_login(scope="live")`. The structured value surfaces in
  every tool's spec §13.5 envelope at
  `suggested_next_action.tool` / `.args`, so agents don't have to
  parse human-readable hints.
- **Per-subcommand `--format` flag** — `keel status --format json`
  works alongside the existing `keel --format json status`. The
  subcommand-level flag wins when both are present. Fixes the most
  common "click intuition" miss reported during the v0.4.x prod-
  readiness smoke.
- **`next` hint on unauthenticated `keel_status`** — when
  `authenticated: false`, the envelope now includes
  `next: ["keel_auth_login   # not authenticated — run this to sign in via browser", ...]`
  so agents know exactly how to recover.

### Changed

- 401 / 403 HTTP error mapping in `keel.errors.translate_http_error`
  now mentions both surfaces (MCP `keel_auth_login` AND CLI
  `keel auth login`) and routes the structured envelope via
  `recovery_tool` so the MCP `suggested_next_action.tool` is filled in
  automatically. CLI users still see the human-readable suggestion
  text via `emit_error()`.
- `KeelClient._require_auth()` "Not authenticated" message rewritten
  to lead with the MCP recovery path (`keel_auth_login` tool) and
  treat the terminal path as the fallback. Caught during the live
  MCP smoke — the old text only mentioned `keel auth login` (CLI),
  which left agents stuck after a 401.
- `FastMCP` server `instructions` block (printed at MCP initialize)
  now teaches agents the recovery loop: "when `keel_status` returns
  `authenticated: false`, OR when any tool's error envelope sets
  `suggested_next_action.tool` to `keel_auth_login`, call
  `keel_auth_login` directly". The old text just said "run
  `keel auth login`" — which is a CLI command, not a tool agents can
  call.
- `keel_status.identity` now reads the nested `/v1/me` shape
  (`{principal: {id}, org: {id, name, plan}, credential_scopes}`)
  instead of flat keys. Same fix v0.4.1 shipped for the login summary
  — the status handler was missed. Caught when post-auth status
  returned `{principal_id: null, org_id: null, plan: null}` against
  a real session. Now surfaces `principal_id`, `org_id`, `org_name`,
  `plan`, and `tier` (`base`/`live` derived from `credential_scopes`).
- `keel_strategy_compose` ValidationError envelopes (dry_run + persist
  paths) now include actionable suggestions: read `example.errors` for
  specifics, then re-call with a fixed source, optionally consult
  `keel_help(topic='dsl_syntax')`. The tool description also teaches
  the two most common gotchas upfront — NO `import` statements
  (component names are pre-resolved) and pipelines must end with a
  normalizer. Pre-fix the error envelope had `suggestion=None` and
  `suggested_next_action.reason="See message above."` — leaving agents
  no clear path to a fix.

### Fixed (critical — discovered during live MCP smoke)

- **Paginated response handlers were all silently broken.** Four
  outcome tools (`keel_audit_list_last`, `keel_accounts_list`,
  `keel_strategy_search`, `keel_strategy_memory_read`) looked for
  `payload["items"]` but the keel-api canonical paginated shape is
  `{data: [...], pagination: {cursor, has_more}}` (the canonical
  `PaginatedResponse` model). Every
  paginated MCP call therefore returned `[]` regardless of how many
  rows the API actually returned. Worst impact:
  `keel_strategy_search` is the discovery tool every authed agent
  calls — pre-fix it returned 0 strategies for a user with 10. New
  shared helper `keel/tools/outcomes/_pagination.py:extract_paginated()`
  is the single source of truth — accepts the canonical shape first,
  legacy shapes as fallbacks. All four handlers now funnel through
  it. The existing unit tests didn't catch this because they mocked
  responses with the (wrong) `items` shape; new
  `tests/test_outcomes_pagination.py` mocks each handler against the
  REAL `{data, pagination}` shape to prevent the regression class
  from recurring.
- `KeelClient._require_auth()` "Not authenticated" message rewritten
  to lead with the MCP recovery path (`keel_auth_login` tool) and
  treat the terminal path as the fallback. Caught during the live
  MCP smoke — the old text only mentioned `keel auth login` (CLI),
  which left agents stuck after a 401.
- `keel_strategy_fork` always sends `{}` instead of `None` as the
  POST body to `/v1/strategies/{id}/fork`. The API requires a
  `ForkStrategyRequest` body (all fields optional, but the body
  itself is required); passing `None` produced a 422 `Field required`
  response, so callers who didn't supply `name` or
  `target_workspace_id` couldn't fork anything. Regression test
  added.
- **SDK now bundles `pipeline_engine/types.py` — fixes broken NewType
  subtype walking that caused false TYPE_MISMATCH errors.** Pre-fix,
  `build_data.py` excluded `types.py` from the SDK pipeline_engine
  bundle (it imports pandas — too heavy). At runtime
  `_resolve_type_name()` couldn't find `pipeline_engine.types`, fell
  back to synthetic placeholder types with no `__supertype__`
  attribute, and `is_compatible(StreamSeries, SignalSeries)` returned
  False — even though `types.py` explicitly declares
  `StreamSeries = NewType("StreamSeries", SignalSeries)` (StreamSeries
  IS a subtype of SignalSeries by design). Real-world impact: any
  agent editing a strategy with `FundingDataLoader → TargetSignal*`
  components got a false TYPE_MISMATCH error from `keel_strategy_compose`,
  while the same strategy validated cleanly against the full `pipeline_engine`
  and backtested cleanly in production. Fix: `build_data.py` now ships
  a pandas-stripped copy of `types.py` (pandas import replaced with a
  `_PdStub` class whose `.DataFrame` and `.Series` resolve to `object`,
  runtime helpers `expect_instrument` / `expect_global` stubbed to
  no-ops). NewType chain stays intact — `StreamSeries.__supertype__ is
  SignalSeries` evaluates correctly in the SDK env, and `is_compatible`
  returns True. SDK wheel size unchanged (pandas still not a dep). Two
  regression tests lock the contract: one asserts the NewType chain is
  reachable from inside the SDK, one runs the full user-reported
  strategy shape through the validator and asserts `valid=True`.
- **`keel_strategy_compose` treats validation as feedback, not a gate.**
  Pre-fix the SDK wrapper raised `ValidationError` on any validation
  issue and refused to persist — making it the outlier behaviour across
  the system. The web app editor (JS validator inline, server-side
  Python validator log-only) + chat-api (validate is a separate
  read-only tool, save goes through keel-api which logs warnings and
  proceeds) + keel-api itself (`_validate_compile_graph` calls
  `dsl_validate_strategy`, logs warnings, continues to compile) all
  surface validation issues to the user without blocking the save.
  Compile is the actual gate. Now the MCP outcome matches: validation
  errors + warnings + type-flow always surface in the response under
  `validation.{errors, warnings}` (both dry_run and persist paths);
  only parse + compile failures block. Caught when an agent tried to
  edit a production strategy that backtests fine but trips Python pass
  6's strict per-component `input_type` literal check (the parent
  uses `FundingDataLoader → TargetSignalResampler` which the
  TYPE_TRANSITIONS table permits at the category level). Now the
  agent gets the full validation output and decides what to do —
  just like a human in the web editor would.
- **`keel.data.registry.load_registry` survives missing
  pipeline_engine deps.** The SDK ships a stripped pipeline_engine
  subset (no `context.py`, no `pipeline/compile.py`); when
  `pipeline_engine` resolves to the full upstream package
  (e.g. anyone with `PYTHONPATH` set inside a development checkout)
  and pandas/numpy aren't installed, the registry
  hydration used to explode with `ModuleNotFoundError: No module
  named 'pandas'` for every tool call. Fixed by catching the
  ImportError on the rich-registry import — bundled JSON data is
  still served for read-only queries (search/detail/after/before/
  dump). This matters for the pipx-install + monorepo-dev scenario;
  pure pipx users in their home dir were never affected. Regression
  test simulates the ImportError so future SDK changes can't
  re-introduce the bleed. v0.4.2 prod-readiness smoke caught this
  in the very last cycle before fresh-session test.
- `keel_components_search` falls back to bundled search when the
  keel-api `/v1/components` endpoint can't honor the requested
  filter. The API supports `category` server-side; everything else
  (`keyword`, `query`, `input_type`, `output_type`, `after`,
  `before`) needs the bundled `keel.data.registry.search_components`
  which implements all filters correctly. Pre-fix the handler trusted
  the API and returned ALL 182 components for any `query=...` call —
  agents asking "find momentum signals" got the entire catalog.
- `keel/tools/local.py` gained `_delegate_or_fallback` capability-
  detect helper + explicit DUPLICATION BOUNDARY docstring + parity
  test scaffold (`tests/test_implementations_parity.py`). Today
  the helper is a no-op (neither the pipx wheel nor our dev env has
  both the full `pipeline_engine.mcp.tools` AND `keel.tools.local`
  reachable in the same Python process) but the seam is in place
  for any future env where both coexist, and the parity tests
  activate as soon as that becomes true. The duplication is
  intentional — the SDK can't bundle the full `pipeline_engine.mcp.tools`
  without dragging in `pipeline.compile`'s pandas/numpy/ta-lib
  deps and breaking the lightweight pipx-install promise. See
  `projects/agent-v2/06-prod-readiness-followups.md` for the
  multi-day unification proposal.
- MCP adapter's missing-required-arg validation now yields a
  spec §13.5 envelope with `code=usage_error`,
  `suggested_next_action.tool=<the_tool_itself>`, and a list of the
  missing arg names. Pre-fix FastMCP's auto-pydantic layer raised
  upstream and surfaced the validation as raw `text` in the tool
  result — opaque to agents. The synthesizer now generates all
  params as optional (default None); required-arg enforcement
  happens inside our handler. (Limitation: FastMCP refuses to
  register functions with `**kwargs`, so unexpected/unknown arg
  names still fall to FastMCP's raw pydantic error. Agents must
  rely on `tools/list` to discover the correct arg names — that's
  the supported discovery path.)
- 403 (EntitlementError) suggestion text now points at the live-scope
  re-login path explicitly, since the dominant 403 cause is calling a
  live-trading tool with a `base`-tier credential.
- Getting Started docs at `docs.usekeel.io/getting-started` and
  `docs.usekeel.io/sdk/agent-setup` and the bundled
  `AGENTS.md` rewritten around the two-command install + MCP-driven
  login as the primary path. Terminal-side `keel auth login` is the
  documented fallback (CI, SSH, Codespaces, WSL).

### Notes

- `keel_auth_login` is **stdio-MCP-only by design** — there's no MCP
  protocol surface for "open a browser on the client's machine" except
  to expose it as a tool the agent invokes. The tool itself runs
  `webbrowser.open()` from the MCP subprocess (which runs on the
  user's machine), so the URL opens locally and the loopback listener
  works as in the CLI flow. Hosted (HTTP) MCP servers would get
  Claude Code's built-in OAuth dance for free — that path remains
  deferred per [`projects/agent-v2/04-install-and-auth-decision.md`](../../projects/agent-v2/04-install-and-auth-decision.md).
- 8 new tests cover the recovery-tool routing + `keel_auth_login`
  registration + `--format` propagation + unauth `next` hint. Total
  test count 450 (up from 442).

## [0.4.1] — 2026-05-20

**Patch — terse `keel auth login` confirmation.** The success output
was dumping the full `/v1/me` response (principal + org + entitlements
+ all 26-27 scopes) — too verbose for both humans and parsing agents.
Trimmed to a 7-field summary: `authenticated`, `principal_id`,
`org_id`, `org_name`, `plan`, `tier` (`base`/`live`), and a `next`
hint pointing at `keel status` and `keel strategy new`. Same shape
across human and JSON modes. The exhaustive view is one command away
(`keel auth status`).

### Changed

- `keel auth login` emits a concise summary instead of the full
  `/v1/me` dump. Human-mode goes from ~5 dense lines (each carrying
  inline JSON) to 7 readable key-value lines. JSON-mode shape is
  identical, agent-parseable.
- Test fixtures updated to match the production `/v1/me` shape
  (nested `principal`, `org`, `credential_scopes`) — earlier flat
  fixture drifted from reality.

### Added

- `_login_summary()` helper in `keel/cli/commands/auth.py` — pure
  function, easy to extend if we add fields like `expires_in`.
- `test_login_summary_marks_live_tier` — asserts `runner.*` scope
  flips `tier` from `base` to `live`.

## [0.4.0] — 2026-05-20

**New default: `keel auth login` opens a browser.** Loopback OAuth 2.1 +
PKCE replaces interactive API-key paste as the default authentication
path. Per [`projects/agent-v2/04-install-and-auth-decision.md`](../../projects/agent-v2/04-install-and-auth-decision.md) and the implementation
plan in [`05-install-auth-implementation-plan.md`](../../projects/agent-v2/05-install-auth-implementation-plan.md).

### Added

- **Loopback OAuth login** — `keel auth login` (no flags) opens a
  browser, runs PKCE S256 against `app.usekeel.io/oauth/connect`,
  captures the redirect on a `127.0.0.1:<random>` listener (RFC 8252),
  exchanges the code at `/v1/auth/oauth/token`, and persists access +
  refresh tokens. Five-minute timeout; if the browser fails to open
  the URL is printed to stderr.
- **`--scope live`** on `keel auth login` — pre-checks the
  "Include live trading" box on the consent page so the issued
  token carries the `runner.*` scope tier.
- **`--api-url <url>`** on `keel auth login` — point at staging or a
  self-hosted Keel. Endpoint discovery via RFC 8414 well-known metadata
  means the CLI works against any Keel environment without hardcoded
  URLs.
- **Transparent token refresh** in `KeelClient` — proactive when the
  access token is within ~60s of expiry, reactive on 401 (one retry per
  request). OAuth 2.1 §6.1 rotation with lineage burn respected;
  detected reuse clears local OAuth state and surfaces a friendly
  re-login prompt.
- New module `keel/browser_login.py` (loopback client) and
  `keel/token_store.py` (persistence + refresh).

### Changed

- `keel auth login` with **no flags** now opens a browser. To paste an
  API key without a browser — for CI, SSH sessions, GitHub Codespaces,
  WSL, or any environment where the CLI and your browser are not on the
  same machine — use `keel auth login --key <token>` with a key from
  [app.usekeel.io/settings?tab=api-keys](https://app.usekeel.io/settings?tab=api-keys).
- `keel auth logout` now clears the OAuth refresh token + expiry +
  client name in addition to the API key.
- `KeelConfig` gains three optional fields: `refresh_token`,
  `token_expires_at`, `client_name`. v0.3.x configs load unchanged
  (additive, backwards-compatible).
- The credential row persisted server-side is now labeled
  `oauth_refresh:Keel CLI/<version>` so operators can see which CLI
  version minted which credential.

### Notes

- The browser flow does not work over SSH, remote dev containers,
  Codespaces, or WSL without port forwarding. Use `--key` in those
  environments. Device flow (`gh auth login`-style — print a code,
  user authorizes on any device) is planned for **v1.1** gated on
  real user demand. See [`04-install-and-auth-decision.md`](../../projects/agent-v2/04-install-and-auth-decision.md) §4.
- The hosted MCP server at `https://mcp.usekeel.io` is **deferred** in
  v1 — the same OAuth backend remains live to serve this CLI flow.
  Register the local stdio MCP via:
  `claude mcp add keel -- keel mcp serve`.

## [0.3.0] — 2026-05-19

**Breaking — single hard break, no deprecation period.** The CLI and MCP
surface is rebuilt as a unified outcome-tool inventory shared between
both access channels. Per the workstream-3 spec (`projects/agent-v2/
03-ideal-experience-spec.md` §4 + §12), the same 22 outcomes are
reachable as `keel <verb>` (CLI) and `keel_<verb>` (MCP) — same args,
same returns, same destructive-action gating.

### Added

- **22 outcome tools** (14 primary + 8 auxiliary) covering the full
  product loop:
  - `keel_status`, `keel_doctor`, `keel_help` (always loaded)
  - `keel_strategy_*`: search, get, compose, fork, diff, delete,
    memory-read, memory-write
  - `keel_backtest_*`: run (with `--wait`, optional `--commit-id`),
    summarize
  - `keel_components_*`: search (collapses search/list/after/before/dump),
    compose-help (collapses detail/reference/examples)
  - `keel_accounts_list`
  - `keel_live_*`: deploy (with preview mode), monitor (12 read-only
    views via `--view`), control (pause/resume/stop/trigger)
  - `keel_share_create` (destructive — privacy disclosure)
  - `keel_audit_list_last`
- **`KEEL_TOOLSETS` env scoping** — default
  `read-only,backtest,share,live-read` exposes read-only live monitoring and
  hides live write tools from `tools/list`. Set
  `KEEL_TOOLSETS=read-only,backtest,share,live-read,live-write` to opt into
  deploy/control. `live` remains a deprecated compatibility alias for both
  live toolsets.
- **Standard return envelope** — every tool returns `{run_id?,
  hero_url?, share_url, summary_metrics?, resource_uri?, ...}`.
  `hero_url` defaults to an authenticated `app.usekeel.io/...` URL.
  `share_url` is always `None` except in `keel_share_create` output
  (the one explicit-publication tool).
- **5 MCP resources** (lazy-fetch from keel-api):
  - `keel://components/catalog`
  - `keel://components/{name}/schema`
  - `keel://strategy/{id}/source`
  - `keel://strategy/{id}/lockfile`
  - `keel://backtest/{id}/results`
  - `keel://dsl/reference/{topic}` (bundled in 0.3.0; API endpoint in
    Phase 2C)

### Removed

- ~50 legacy MCP tools (collapsed into the 22 outcomes). Examples:
  `strategy_validate` → `keel_strategy_compose --dry-run`;
  `strategy_components_after/before/dump` → `keel_components_search
  --after/--before`; `live_positions/equity/pnl/...` → `keel_live_monitor
  --view <slice>`.
- ~10 legacy CLI command files (`commands/strategy.py`, `live.py`,
  `backtest.py`, `components.py`, `accounts.py`, `sharing.py`,
  `audit.py`, `market_data.py`). Their content is reachable through
  the outcome surface.
- The standalone MCP registry + dispatch modules (`keel/mcp/registry.py`,
  `keel/mcp/dispatch.py`) — replaced by `keel.tools.outcomes._mcp_adapter`.
- The `keel skills` / `keel strategy checkout|push|pull|workspaces|
  discard` CLI commands. Workspace operations and skill management
  return in Phase 2D/2F with the redesigned interfaces.

### Changed

- **Live-trading scope-gating** is now env-driven (`KEEL_TOOLSETS`)
  rather than per-request `/v1/me` lookups. The MCP server starts
  faster and tool selection is deterministic.
- **`keel mcp serve` still ships** (stdio MCP). Hosted MCP at
  `mcp.usekeel.io` arrives in Phase 2C with Clerk-identity + Keel
  authorization (per spec §7).

### Internal

- New shared infrastructure in `keel/tools/outcomes/`:
  - `_base.py` — `OutcomeTool` dataclass, `OutcomeResult`,
    `ToolContext`, spec §13.5 5-field error envelope helper
  - `_toolsets.py` — `KEEL_TOOLSETS` env parsing
  - `_cli_adapter.py` — auto-render Click commands from JSON Schema
  - `_mcp_adapter.py` — auto-register FastMCP tools from JSON Schema
- 60+ unit tests covering the new outcome surface; the legacy CLI test
  files were deleted alongside their commands.

## [0.2.2] — 2026-05-19

### Added

- `keel strategy import-share <share-id>` — pulls a shared strategy's
  source as Keel DSL and prints to stdout (or to a file via `-o`).
  Hits the public `/s/{share_id}/graph` endpoint (no API key needed)
  and converts the returned graph to DSL client-side via the bundled
  `graph_to_spec` + `spec_to_dsl`. Returns a clean exit-code-3 error
  if the share owner didn't set `include_source=true`.

  Example: `keel strategy import-share gDXjURKqWPs8CZ4eXdqAI > my_carry.py`

- MCP tool `strategy_import_share` — same fetch + convert, returns
  DSL source as the tool result so agents can inspect or feed it back
  through `strategy_validate` / `strategy_explain`.

### Changed

- `KeelClient.get_public(path)` — new helper for public endpoints that
  don't require authentication (currently `/s/{share_id}/*`).

## [0.2.1] — 2026-05-19

Phase 1D + 1E of the v0.1.x catch-up. Mostly bundled-content fixes
that ship via the wheel; site-level docs land separately.

### Fixed

- `AGENTS.md` (bundled with the wheel): replaced the "4 meta-tools"
  fiction with an accurate description of the ~70-tool surface and
  the live-trading scope gating that hides write tools when the API
  key lacks `runner.*`.
- `carry` strategy template: replaced the placeholder ROC pipeline
  with a real funding-carry implementation (PriceDataLoader →
  FundingDataLoader → SignalResampler → NegateTransform →
  CrossSectionalZScore → VolatilityStandardizer → forecast scaling →
  weight normalisation). The previous template had a dead
  `Store("ohlcv_1d")` and no carry logic.

### Changed

- Bundled `keel/data/templates.json` regenerated to reflect the new
  carry template.

## [0.2.0] — 2026-05-19

Phase 1C of the v0.1.x catch-up — surface completion. The CLI now
reaches feature parity with the web app's live observability surface
and the platform-side strategy-version operations. Minor-version
bump (0.1.x → 0.2.0) signals the surface is now mature; no breaking
changes.

### Added

- `keel backtest run --commit-id <id>` — backtest a specific strategy
  version. Defaults to HEAD if omitted.
- `keel strategy version-diff <id> <ref-a> <ref-b>` — structural diff
  between two strategy versions on the platform. Named `version-diff`
  rather than `diff` to avoid colliding with the local two-file diff.
- `keel strategy generate --kind {screen,index} --params '<json>'` —
  template-or-index-builder dispatched generation (wraps the May 12
  `/v1/strategies/generate` endpoint).
- `keel live executions <id>` — execution-run history with optional
  `--expand-orders` and `--execution-run-id` filters.
- `keel live orders <id>` — recent orders for a deployment.
- `keel live trades <id>` — paginated trade (fill) history with
  symbol / side / time / sort filters.
- `keel live weights-history <id>` — historical weight snapshots.
- `keel live funding <id>` — funding events (critical for carry
  strategies).
- `keel sharing fork-by-id <strategy-id>` — direct fork without going
  through a share URL.
- MCP tools: `strategy_version_diff`, `strategy_generate`,
  `live_executions`, `live_orders`, `live_trades`,
  `live_weights_history`, `live_funding`, `sharing_fork_by_id` (8).

## [0.1.9] — 2026-05-19

### Added

- `keel accounts` command group — `list`, `show`, `create`, `authorize`,
  `reauthorize`, `check-deposit`, `refresh-mode`. Required for the live
  trading flow; surfaces the EIP-712 challenges that callers must sign
  with their Hyperliquid wallet.
- `keel live preview <strategy-id>` — dry-runs a deployment to return
  the derived schedule, timeframe, and bar offset before committing.
- MCP tools: `accounts_*` (7), `live_preview`.

### Fixed

- `keel live deploy` now requires `--account-id` (per the API's
  `DeployLiveRequest` contract) and accepts optional `--schedule`.
  Previously omitted the account_id field entirely, causing every
  deployment attempt to 422 server-side.

### Infrastructure

- New `sdk-catalog-freshness` workflow: AST-scans the upstream
  components + signal_framework packages on PRs and fails if the
  bundled `keel/data/registry.json` drifts. No pandas / numpy /
  ta-lib install required.
- `KeelClient` gains a `put()` method for endpoints like
  `accounts/{id}/refresh-mode`.

## [0.1.8] — 2026-05-18

### Fixed

- `tests/cli/test_main.py::test_version` now asserts against the real
  installed version (via `importlib.metadata`) instead of the obsolete
  hardcoded `"0.1.0"` string. This was the last gating piece of the
  0.1.7 release pipeline restoration.

## [0.1.7] — 2026-05-18

### Fixed

- `keel --version` now returns the actual installed version. It was
  hardcoded to `0.1.0` since the first release; `importlib.metadata` is
  used instead so the version stays in sync with `pyproject.toml`.

### Changed

- Bundled component catalog regenerated against the current platform
  registry (176 → 182 components, +6 net: `AdverseVolCap`,
  `BenchmarkDemean`, `RealizedVolCap`, `ResidualMomentum`,
  `VolAttenuator`, `VolumeWeightedMultiplier`).
- Bundled behavioral skills: replaced fabricated component names in the
  `component-discovery` and `strategy-creation` skills (e.g. `EWMAC`,
  `CarrySignal`, `VolatilityScaler`) with real components from the live
  catalog.

### Infrastructure

- Package source moved from `projects/archive/agent-sdk/keel-sdk/` to
  `packages/keel-trade/keel-sdk/`. The release pipeline had been broken
  since 2026-04-23, when the source was accidentally moved into
  `projects/archive/`; CI workflow paths now point at the new location.

## [0.1.6] — 2026-04-04

- `strategy_find_local` tool also scans `~/.keel/strategies/` in addition
  to the working directory.

## [0.1.5] — 2026-04-04

- New `strategy_find_local` tool for discovering local strategies.

## [0.1.4] — 2026-04-04

- Auth flow surfaces both interactive and non-interactive options when
  credentials are missing.

## [0.1.3] — 2026-04-04

- `test_translate_http_401` aligned with the new auth error message.

## [0.1.2] — 2026-04-04

- Auth error messages now include the API-key URL and mention local
  strategies; `keel_status` MCP tool tightened up auth guidance.

## [0.1.1] — 2026-04-04

- Removed dead imports and unused `search` optional deps.

## [0.1.0] — 2026-04-04

- Initial public release on PyPI. CLI (`keel ...`) + MCP server for
  AI-driven strategy development against the Keel platform.
