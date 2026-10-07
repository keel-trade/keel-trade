# Changelog

All notable changes to `keel-trade` are documented here. Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html), and the format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

**A first-week backtest allowance is reported as one fact (connect-onboarding
spec 01 §1.8/§1.9).** When keel-api reports a `first_week` block on a
backtest balance (`{granted, remaining, ends_at}`), `keel_plan_usage` and
`keel_backtest_run` pass it through in each unit's `quota` block, projected
to those three keys. `keel_plan_usage` adds one talking point after the plan
sentence, for example "154 of 200 first-week backtests left; they end Tue 13
Oct 15:02 UTC." `keel_backtest_run` appends the same sentence to
`quota_notice` only when that line already renders without it, so the
allowance never makes a response carry a quota line, and it never appears in
`next`. The sentence is built from the `backtest_runs` block only, and only
when all three facts arrived. The neutral-wall guard (`assert_neutral_wall_text`,
and through it `validate_talking_points`) now also refuses expiry urgency:
"before they expire", "before it expires", "don't lose", "use them before",
"running out" and "expires soon". No tool description or server instruction
changed.

## [0.8.0] — 2026-10-06

**0.8.0 is the first wheel since 0.7.0 (2026-08-21).** It was versioned on
2026-08-23 and never published, so everything below reached only the hosted
connector until now. Pipx does not upgrade itself: run `pipx upgrade
keel-trade`.

**Ten MCP tools renamed; the old names are deprecated aliases (2026-10-01,
Q-2080).** OpenAI's plugin tool scan reads a tool name for what it does, and
ten of the hosted surface's names did not say: `keel_ownership_status` →
`keel_strategy_readiness`, `keel_doctor` → `keel_connection_check`,
`keel_components_compose_help` → `keel_components_get`,
`keel_components_detail_batch` → `keel_components_get_many`,
`keel_strategy_memory_read` / `_write` → `keel_strategy_notes_read` /
`keel_strategy_notes_add`, `keel_plan_status` → `keel_plan_usage`,
`keel_status` → `keel_account_status`, `keel_open_in_app` → `keel_app_link`,
`keel_strategy_log` → `keel_strategy_history`. `tools/list` advertises only
the new names; `tools/call` with an old name still runs the renamed tool, on
the hosted endpoint and on a local `keel mcp serve` alike, until at least
2027-01-01. The `no_ownership_hint` parameter of `keel_strategy_get`,
`keel_strategy_status`, `keel_backtest_run` and `keel_backtest_watch` is
`skip_readiness`: the old name is accepted over MCP on both servers and wins
when a call carries both, and the CLI keeps `--no-ownership-hint` as a hidden
alias of `--skip-readiness` (Q-2267). `keel_strategy_search`'s retired `tag`,
`owner` and `share_id` are accepted and dropped over MCP on every profile, so
a connector catalog frozen before their removal keeps working; the CLI
options are gone. CLI command paths are unchanged. In the same change:
`keel_share_create` and `keel_feedback` are `destructiveHint: true` (a public
disclosure and a sent note are irreversible), `keel_live_monitor` and
`keel_strategy_fork` are `openWorldHint: true` (a live exchange-account read;
any public share as a source); `keel_app_link` accepts real share ids (the
unprefixed 21-character tokens `keel_share_create` returns — it had only ever
recognised a `shr_` form no share carried); `keel_strategy_search` drops its
`tag`, `owner` and `share_id` inputs, which matched nothing; share links
derive the recipient's permission from `include_source`; and a save whose
source does not compile is reported as a stored draft with no new version.

**Round 2 (2026-10-01, Q-2268 / Q-2269).** `keel_strategy_get` returns
`recent_runs` — the strategy's newest backtest runs (up to 5, any status,
newest first: `run_id`, status, version, window, `completed_at`, headline
metrics), the ids `keel_backtest_summarize`, `keel_backtest_compare` and
`keel_share_create` take. On the LISTED (hosted directory) profile only:
`keel_account_status.identity` and `keel_connection_check`'s auth check no
longer carry `principal_id` / `org_id` (an email-free `display_name` joins
`org_name` and `plan`), `keel_strategy_history` entries carry `modified_via`
without the raw `client_name` / `auth_surface`, and `keel_strategy_readiness`'s
`projection` is an allow-list (stage, status, next step, missing evidence,
latest run) rather than keel-api's whole strategy-work projection; the CLI
and local server are unchanged. On every surface: the free-plan wall's plan
sentence is D-12's recorded fallback, "Plans are changed in the Keel web
app."; `keel_feedback` reports `delivered: false` (and a new `stored`) when
keel-api answers that the note could not be persisted; the hosted
no-credential error names no particular client; `keel_share_create`'s copy
says what a hidden-source link still publishes (name, description, metrics,
fork count, live status, market count, risk settings) and that
`include_source` decides whether viewers see and can copy the source.

**Round 3 (2026-10-01, Q-2268 / Q-2266).** On the LISTED profile only, every
tool that passes a keel-api row through returns an allow-listed projection of
it (one owner, `keel/tools/outcomes/_listed_projection.py`):
`keel_strategy_get`'s `metadata` (no org id, storage key, source / lock hash
or deployment id — `is_live` says whether the strategy is running), its
`include_versions` rows (the `keel_strategy_history` row) and its
`include_source` payload; `keel_strategy_search` results drop `owner`;
`keel_live_monitor`'s deployment rows drop the org, exchange-account and
config ids and the source hash, and its other views lose those keys at any
depth; notes and Library variants are allow-listed. Listed results also stop
naming the CLI or other clients: no `render.surface_hints`, no `exit_code` on
MCP error envelopes, a connection-check hint without the CLI sentence, and
compose's `missing_input` names only `source`. `keel_backtest_summarize` and
`keel_backtest_watch` no longer return a signed `results_url` there. The
listed `keel_strategy_compose` schema requires `source`. On every surface: the
internal-error remedy names `keel_connection_check` rather than `keel doctor`;
`keel_live_monitor`'s trade filters say they apply to the trade-history view
only; `keel_feedback`'s `severity` is described as free text;
`keel_strategy_fork` says a share must show its source and that forking adds
to its public fork count. The CLI and local server results are unchanged.

**The catalog and the live surface both grow, and the validator sheds two
whole passes.** The bundled component catalog goes from 190 to 223: the W3
data-loader families (per-bar flow, open interest, premium, predicted
funding) and the W5 authoring families (trading session, market structure /
SMC, level-derived risk). The outcome surface goes from 40 to 46 tools —
four of them (Q-0913) finally let an agent answer "what did my live
deployment actually do, what did it cost, and is the account halted?"
`5min` becomes a platform timeframe end to end. On the validator side the
phase-order pass and the entire Layer C runtime walk are retired, each
after a census showing it was never the sole catcher of a real defect.

**The ownership projection actually reaches agents.** `keel ownership
status`, the `keel://ownership/strategy/{id}` MCP resource, and the
ownership hint fields on `keel_strategy_get` / `keel_strategy_status` /
`keel_backtest_run` / `keel_backtest_watch` have promised since 0.5.0 to
tell an agent what a strategy still needs before it can go live. They
never could: the routes behind them existed only on an internal host no
SDK surface can reach, so 0.7.1 turned the feature off and made it say so
honestly. keel-api now serves the reads, and this release turns it back
on.

### Added

- **Short backtest windows, in the tools (new-data-loaders spec 07 §5–§6,
  Q-2025).** `keel_backtest_run` / `_watch` / `_summarize` carry
  `exposure` (bars that held a position out of the window's bars, with the
  first and last position) as a first-class field, an `exposure:` text line
  and a card line. `keel_backtest_summarize` takes `start` / `end` /
  `capital` and returns `slice`: that part of the run as it ran, scaled to
  `capital`, from keel-api's `/slice` read. An omitted `start_date` now
  runs the platform's default window for the strategy's timeframe (5min 60
  days, 15min 90 days, coarser 5,000 bars, capped at available data;
  Q-2012), and the tool text says how warm-up shows up.
- **Execution outcomes reach the CLI and MCP (Q-0913).** Four read-only
  tools, each a CLI verb and an MCP twin, all on the `live-read` toolset:
  `keel_deployments_list` / `keel deployments list` (every deployment with
  id, strategy, status, schedule, `account_id`, deployed version, realized
  P&L and open-position count — the id-lookup step for the other three);
  `keel_live_receipt` / `keel live receipt` (one EPISODE's sealed receipt,
  addressed by `(session_id, intent_rev)` — a multi-episode run has one
  receipt per episode and no run-level receipt);
  `keel_live_quality` / `keel live quality` (execution quality / TCA); and
  `keel_accounts_safety` / `keel accounts safety` (the server's execution
  HALT state, GET only — clearing a halt is an operator ceremony, not a CLI
  flag, and a refusal here is not an auth problem). None of the four is on
  the listed hosted profile.
- **`keel_live_monitor` `view='executions'`** gains `expand_orders` (the
  child orders behind each attempt's counts) and `execution_run_id` (narrow
  to one run). Both are executions-only and mirrored into the listed input
  schema.
- **`keel_backtest_compare` / `keel backtest compare`** — two runs' cost and
  turnover profiles side by side (Q-0581).
- **`keel_live_update` / `keel live update`** — the SDK/MCP producer of the
  UPDATE intent (deploy-wizard-v2 spec 04 §4). Mints a signed update link
  for a LIVE/PAUSED deployment (`POST /v1/deployments/update-intents`, body
  `{"deployment_id"}` and nothing else) and returns the shared handoff
  envelope: `action_url` opens the web update flow, `resume.token` +
  `intent_token` give a pure status poll (pending | completed | expired,
  with the applied version on completion). The agent can mint and share the
  URL; it cannot apply the update and cannot put configuration into the
  intent. Toolset `live-write`; never registered on the listed profile.
- **33 new components — the bundled catalog goes 190 → 223.**
  - Per-bar flow loaders (new-data-loaders spec 01), one serving core:
    `TakerFlowLoader` (aggressor buy/sell/net/total volume or trade counts),
    `WhalePrintLoader`, `VWAPLoader`, `DollarVolumeLoader`,
    `TwapVolumeLoader`.
  - Positioning and basis loaders: `OpenInterestLoader`, `PremiumLoader`
    (perp-vs-oracle), `PredictedFundingLoader` (the hourly PREDICTED rate),
    plus the market-wide regime filters `OpenInterestRegime` and
    `PremiumRegime`.
  - Trading-session family: `SessionMask`, `SessionVWAP`,
    `SessionRangeHigh`, `SessionRangeLow`, `SessionRelativeVolume`,
    `SessionCloseExit`.
  - Market-structure / SMC family: `SwingPivot`, `BreakOfStructure`,
    `ChangeOfCharacter`, `OrderBlock`, `BreakerBlock`, `FairValueGap`,
    `LiquiditySweep`, `Displacement`, `PremiumDiscount`.
  - Risk and sizing: `StopDistanceRiskSizer` (size so a stop hit loses a
    fixed fraction of equity), `TradeLevelRiskExit` (one lifecycle from a
    one-shot entry to its level-derived stop or R-multiple target — its
    `short_level_slot` lets that one lifecycle own both directions of a
    two-level trade, Q-1029), and `WeightCadence` (sample target weights at
    calendar boundaries and hold).
  - Signals and universe: `SMA`, `CumulativeSum` (the CVD carrier),
    `SignalProduct`, `SignalRatio`, and `RollingNotionalProxyMask` (keep
    only assets whose trailing candle-notional PROXY clears a dollar floor).
- **`5min` is a platform timeframe.** It joins `VALID_TIMEFRAMES` and the
  minutes table, so `frequency`, `interval` and `target_timeframe` all admit
  it. The wheel now also ships the stdlib-only `timeframes` package — the
  platform's clock alphabet, vendored verbatim rather than mirrored, which
  is what the bundled `validation_shared` re-exports.
- **`Universe(min_trailing_notional_proxy=…)`** — a dollar-liquidity floor
  criterion, parsed by the vendored engine and forwarded to the resolver.
- **Dollar volume is traded notional** (dollar-volume DV1–DV6): the sum of
  trade price × size (`pv_sum`), replacing the candle proxy `volume × close`.
  `DollarVolumeLoader` v2 serves it and, before 2025-03-23 where no trade
  notional exists, splices in the 15m candle `volume × close` — in that one
  place, named in its `dollar_volume_splice` provenance; it declares the new
  `DollarVolumeSeries` type. `RollingDollarVolumeMask` is the dollar-liquidity
  floor on it (a required `dollar_volume_slot`), and
  `Universe(min_trailing_dollar_volume=…)` is the floor field — the universe
  resolver ranks and floors on dollar volume, the same number at every
  clock. The universe tools write the new field name.
- **`keel universe resolve` reports `as_of` and `snapshot_note`** (Q-0983):
  the server names the ONE instant the list resolved at and labels it a
  snapshot rather than a membership rule; both pass through verbatim when
  the server sends them.
- **Two advisory catalog rules (Q-0580):** `UNREACHABLE_THRESHOLD_ARM`
  (warning — a mode-gated arm whose opposite-arm param is explicitly
  written, or an adjacent `Clip` that collapses an evaluated arm's
  `{-1,+1}` into the flat value) and `PRICE_MARKS_AUTO` (info — fires only
  when the funding-only auto-load will actually happen).
- **Validator surface:** relational param constraints (arm constraint schema
  v2, Q-0691); typed slots complete — `slot_domains`, slot operand refs and
  `cond_id` (Q-0692, Q-0548); and `cond_fixed`, which takes the transfer
  algebra to 7 ops.
- **Bundled agent knowledge:** `capability_boundaries.md` and
  `platform-operations.md` join the always-on corpus (Q-0840), and the
  pattern library gains `session_and_structure_patterns.md` and
  `screen_select_patterns.md`.
- **Every request names the client version** (Q-2500): `X-Keel-Client:
keel-trade/<version>`. keel-api treats the component pins a wheel without
  it sends (0.7.0 and earlier, whose bundled catalogue predates the server's)
  as advisory — latest on create, the stored lock kept on update — and keeps
  this release's explicit pins as the caller's.

### Changed

- **Six components rank or weight on dollar volume from a required
  `dollar_volume_slot`**, wired from `DollarVolumeLoader() -> Store(…)`:
  `RollingUniverseMask` v2, `RollingVolumeUniverseMask` v2,
  `VolumeUniverseReducer` v2, `VolumeUniverseReducerAny` v2,
  `VolumeWeightedMultiplier` v2 and `MarketVolumeRegimeFilter` v3. An
  unwired slot is a validation error naming it. The earlier versions are
  frozen, so a pinned strategy keeps running unchanged. The new versions drop
  `volume_column` (both reducers) and `ohlcv_slot`
  (`VolumeWeightedMultiplier`).
- **Deprecated, still valid with a warning naming the replacement:**
  `RollingNotionalProxyMask` (the candle proxy; use
  `RollingDollarVolumeMask`) and `Universe(min_trailing_notional_proxy=…)`
  (an alias of `min_trailing_dollar_volume` with the same meaning; declaring
  both is an error).

- **Every backtest count is named by the era that recorded it
  (trade-metrics spec 01, Q-2122).** A run's `trade_model` stamp decides
  what its stored counts are: runs with no stamp and `"reducing_order"`
  runs carry **Trades** (every order that reduced or closed a position,
  plus each position still open at the end) and a **Win rate** over closed
  trades; `"position_round_trip"` runs (2026-08-25 until the engine change)
  carry **Positions** and a **Position win rate**; current runs carry both,
  plus **Resizes**, **Turnover** and **Avg holding time**. The backtest
  view, `summary_metrics`, compare's rows and cost profile, the strategy
  evidence line and the cards read each run through one era read model:
  a count the run did not record reads "—", never 0; a basis line says
  which view a run lacks; a comparison of runs from different eras says so
  in one line. The sample-size note keys on positions when recorded.
  `keel_backtest_positions`' `position_count` is the run's own position
  count (null, with a note, when not recorded) and the envelope no longer
  relays the era stamp. Compare's cost profile reads `resizes` where it
  read `rebalance_legs`; `metrics_raw` stays verbatim.

- **`keel_plan_status` names no plan prices on the hosted connector.** On
  `mcp.usekeel.io` (`KEEL_SERVER_PROFILE=listed`) each `upgrade_options`
  entry now carries the plan name and its limit differences only, with no
  `price`. The hosted description says where plans are listed (the
  `manage_url` page) instead of promising prices. The CLI and the local
  stdio MCP server still return prices.
- **The Claude Desktop bundle is described as a strategy builder.** The
  `.mcpb` manifest's display name is now "Keel — Hyperliquid strategy
  builder", and its description, long description and keywords lead with
  building and backtesting strategies. They also state that the bundle
  cannot place orders or move funds by default: running a strategy with real
  capital happens in the Keel web app, and the local live-write tools stay off
  unless you opt in and arm them on your machine. The new copy notes that Keel
  is not investment advice. Only the wording changed. The bundle's tools and
  default toolsets (`always,read-only,backtest,share,live-read`) did not.
  The public README, the package readme's live-safety section and the PyPI
  summary now say the same, and the readme no longer describes the
  `direct=true` preview/confirm sequence as the way to go live.
- **One drawdown sign in backtest results** (Q-1805). `summary_metrics`
  (run, watch, summarize) and compare's `performance` / `performance_by_run`
  rows state the drawdown as `max_drawdown_pct`, negative or zero — the
  number and sign `view.metrics`, the card and the text already showed.
  The unsigned `max_drawdown` key is gone from those blocks; a script
  reading `.summary_metrics.max_drawdown` reads `-max_drawdown_pct`.
  `metrics_raw*` keep the worker's sealed metrics verbatim.

- **Deployment tools call `/v1/deployments`** (Q-0639). `live_monitor`,
  `live_deploy`, `live_control` and the handoff envelope migrate off the
  `/v1/live` alias to the canonical paths, on the CLI and both MCP
  surfaces.
- **Execution outcomes render per field, not as one-line JSON** (Q-0913).
  `keel/output.py` gains `FIELD_RENDERERS` / `format_field_line`, and BOTH
  human surfaces — `format_human` for the CLI-only verbs and the outcome
  tools' `_cli_adapter` — route through it, so a field cannot read two ways.
  Unrecognised nested values keep each surface's existing fallback.
- **`keel backtest run` wait budgets are split** (Q-0573): an interactive
  terminal waits 300s with a stderr progress line (healthy runs measured at
  ~141s); non-interactive surfaces, including MCP, keep 90s. The timeout
  message names the budget actually used.
- **`keel backtest summarize` treats fee drag as a first-class line**
  (Q-0581): `total_fees_paid`, `fees_pct_of_initial`,
  `fees_pct_of_gross_profit`, `fees_pct_of_net_profit` and `rebalance_legs`
  join the canonical summary keys (ordering and labelling only — values are
  verbatim from stored metrics).
- **The 8 bundled skills move to the portable Agent Skills layout**
  (Q-0267): `keel/skills/<name>/SKILL.md` instead of
  `keel/skills/<name>.md`.
- **The anonymous tier delivers the 10 backtests it advertises** (Q-0466).
  Every copy site, `keel/anon.py` included, now says 10.
- **`Execution(buffer_threshold=…)` floor drops 0.01 → 0.001** (Q-0636), so
  cost-referenced no-trade bands are expressible at intraday timeframes.
- **`NONDENSE_TERMINAL_WEIGHTS` is promoted** (Q-0686): a masked pipeline
  with no terminal `FillNaN(0.0)` now warns in both engines, and errors
  under production mode.
- **A deprecated latest is a lockable state** (Q-0684). `evolve_lock` pins
  it and full validation runs, with the catalog `DEPRECATED_COMPONENT`
  warning as the upgrade nudge; `COMPONENT_NOT_RUNNABLE` stays the hard
  gate. Previously such a strategy got one location-less
  `UNKNOWN_COMPONENT` with every other diagnostic suppressed.
- **`components_after` / `components_before` match the live engine** (Q-0732).
  The bundled answers are precomputed from `find_components_accepting` /
  `find_components_outputting` at regen instead of approximated by exact
  input-type string lookup plus transition expansion.
- **`keel_components_search` keeps a recall slot** (Q-1074): a lexical field
  with a weak tail no longer lets the strong set fill the whole quota and
  switch the semantic arm off.
- **Claims and clock metadata across the catalog** — the rank family,
  `ApplyUniverseMask`, the three FDM components, five forecast components
  and `SelectionToSignalConverter` now state what they actually claim
  instead of inheriting it from a name or declaring it falsely; a soft type
  name is a ROLE that survives (Q-0543); and the annualized-vol guidance
  agents compose against is corrected (Q-1151).
- **Ownership projection is live.** `PROJECTION_ROUTES_SERVED` is `True`
  and the fetch reads keel-api's `GET /v1/strategy-work?strategy_id=…` —
  ONE request per strategy read instead of the previous two, and it
  answers for a strategy that has never been opened in chat (the response
  carries a computed projection with a null `session_id`). Requires
  keel-api ≥ the 2026-08-23 release; against an older API the surfaces
  degrade to honest unavailability, never to an error.
- **`keel_ownership_status` and the MCP resource no longer fabricate.**
  The hardcoded `"not_started"` body — `missing_evidence:
[strategy_brief, baseline_evidence, failure_modes]`,
  `live_readiness_blockers: [no_baseline, …]` — is DELETED. Those lists
  are now read from the server or not reported at all. When there is no
  projection to read, the body carries `projection_available: false`, a
  machine-readable `unavailable_code` (`strategy_not_visible`,
  `projection_read_failed`, `projection_not_served`) and a plain-language
  `unavailable_reason`, and no evidence fields whatsoever.
- **A compose's create sends only the caller's own pins** (Q-2270): it used
  to send the wheel's whole bundled lock, which keel-api began honouring in
  release-v1.180 and which pinned new strategies at a 47-day-old catalogue
  (`PriceDataLoader` v1 under source written for v3). An update sends the
  stored lock with `expected_lock_hash`.
- **A component lookup never mints an anonymous workspace** (Q-2494).
  `keel components get` / `search` ask the server only when credentials
  already exist (the anonymous tier can read components since the matching
  keel-api release) and otherwise answer from the bundled catalogue — they
  had minted one per command wherever `~/.keel` did not persist. A 429 on
  those lookups is raised, never swallowed into the bundled answer.
- **Anonymous start refuses when its config cannot persist** (Q-2494): the
  tokens are read back after the save, and a config that does not return
  them is an error naming `KEEL_API_KEY` / `keel auth login --key`, not a
  workspace silently lost and re-minted on the next command.

### Fixed

- **The SDK and MCP stopped rejecting a `5min` strategy** (L37). The data
  bundle was regenerated before `5min` reached the loader/resampler/
  transform surface, so 7 param option lists still ended at `15min` and the
  vendored validator hard-rejected e.g. `PriceDataLoader(timeframe="5min")`
  with `PARAM_INVALID_OPTION` before it could reach the platform. All 12
  option lists that carry `15min` now carry `5min`.
- **A composer role key naming no branch is a pass-6 ERROR** (Q-1278). A
  one-character typo in `numerator_key` / `left_key` / `signal_key`
  validated with zero issues and then raised `ValueError` from inside the
  backtest; it now emits `COMPOSER_KEY_MISMATCH` with the offending key and
  the available branch names.
- **The local dry-run gives the platform's verdict on composers, param
  types and mask slots** (Q-1870). The bundled registry carried each
  composer's role contract, each param's structural type and each slot's
  domain demand, but the loader never read them back, so a local
  `keel strategy validate` / `keel_strategy_compose(dry_run=true)` never
  raised `COMPOSER_INPUT_TYPE_MISMATCH`, `COMPOSER_KEY_MISMATCH`, the
  composer `CLOCK_MISMATCH` / `TERMINAL_CLOCK_MISMATCH`,
  `PARAM_TYPE_MISMATCH` on `enum`/`list`/`dict`/union params, or the
  mask-slot `VALUE_DOMAIN_*` checks that the platform raised on save. Every
  conformance fixture now gets the same verdict locally as on the server.
- **`keel help` normalizes topics and never touches the network** (Q-0573).
  Lookups fold case, hyphens and spaces to the bundled slugs, and a miss is
  instant and offline with top-3 suggestions — the old fallback called a
  `/v1/reference` endpoint that never shipped, so every miss cost a
  guaranteed 404 round-trip.
- **Three components are deprecated in the bundled catalog:**
  `ExtractIndicatorOutput` (a false multi-output promise that silently
  passed through, Q-0823); `CumulativeTransform` (superseded by
  `CumulativeSum`); and `VolumeUniverseReducerAny` (Q-1087 — it chose a
  universe from a whole-window liquidity statistic and applied it from bar
  0, so extending the data forward changed which assets were kept at the
  start; a static reducer has no causal form).
- The `keel://ownership/strategy/{id}` resource honored `KEEL_APP_URL`
  nowhere, so a staging reader was handed prod URLs. It now reads the env
  like the CLI and MCP adapters do.
- `keel --help` rendered the `ownership` command group with no
  description.
- **Backtest results carry a realism line** (Q-1881): when a run's orders
  fall below the venue's minimum notional, the result says how many and that
  the reported returns include them, so a $100 sweep is no longer read as
  good without that caveat.
- **Works on fastmcp 4** (Q-2099): `ToolResult` is imported from
  `fastmcp.tools` and the five `except ImportError` fallbacks that turned
  the 4.x API change into degraded tool results are gone.

### Removed

- **`PHASE_ORDER_VIOLATION` is retired** (Q-0685) and its code tombstoned,
  reserved and never re-mintable. The census: 932 fires on 701 of 1380
  stored subjects, all false positives, and zero cases where the phase rule
  was the sole catcher of a real defect. The type system is the ordering
  authority.
- **Layer C — the runtime validation walk — is retired** (Q-0689, Q-0688).
  Write-time validation is the one authority; six runtime codes are
  tombstoned reserved. The restored walk rejects 8 of 18 Layer-B-valid
  library strategies, which is the defect it was.
- **`FUNDING_RATES` leaves the documented slot list.** The channel is now
  `FUNDING_SERIES` and its DSL half is retired, so the bundled slot
  reference no longer offers `Load(FUNDING_RATES)` as an example.

## [0.7.0] — 2026-08-21

**Hosted MCP, anonymous instant start, the Keel Library on the agent
surface, and a rebuilt validator.** The biggest release since the
0.3.0 outcome-tool rebuild: Keel is now reachable without installing
anything (hosted MCP at `mcp.usekeel.io`) and without an account (CLI
anonymous instant start with auto-claim on login). The outcome surface
grows from 34 to 40 tools, the bundled component catalog from 182 to
190 components, and the DSL validator is re-founded on a judgment-table
interpreter with a much richer issue envelope.

Three threads run through this release. First, **zero-friction entry**:
`https://mcp.usekeel.io` serves a curated hosted profile (26
read / research / backtest / share tools — live-write, destructive, and
filesystem-bound tools stay CLI/local-only), with per-request token
forwarding so the hosted server holds no credentials of its own; on the
CLI, the first command that needs auth silently mints an anonymous
workspace and a later `keel auth login` claims it — strategies and
backtests come with you automatically. Second, **agent-to-human
handoffs**: every wall an agent can hit (quota, plan caps, live scope,
unlinked accounts, go-live itself) now returns a structured
`HandoffRequired` envelope with an action URL and a pollable resume, and
go-live defaults to a web handoff instead of an in-terminal deploy.
Third, **validator depth**: local validation (in
`keel_strategy_compose` / `keel strategy compose` and the bundled
validator) now runs the same judgment-table interpreter as
the platform, emitting a 16-field `ValidationIssue` envelope, value-
domain checks, factory-call cycle detection, and multi-timeframe clock
inference — a strategy that validates clean now actually runs.

As always, this changelog covers what ships in `pipx install
keel-trade`, the `.mcpb` bundle, and the public `keel-trade/keel-trade`
GitHub repo. Platform-backend changes ride their own release cadence.

### Added

- **Hosted MCP server profiles.** The server now runs in one of two
  profiles: `full` (default — the local stdio surface: 38 tools active
  out of the box, 40 with the `live-write` toolset opted in via
  `KEEL_TOOLSETS`) and `listed` — the single hosted profile that
  `mcp.usekeel.io` serves to both the paste-URL connector and directory
  listings. The listed profile is a hard allow-list of 26 tools (read /
  research / compose / backtest / share / library / read-only live
  monitoring); `keel_live_deploy`, `keel_live_control`,
  `keel_strategy_delete`, `keel_strategy_restore`,
  `keel_accounts_list`, `keel_audit_list_last`, and all 8
  filesystem-bound workspace tools are excluded by construction, and a
  policy-scan test gates any addition. Hosted requests forward the
  caller's token per-request — the hosted server stores nothing.
- **CLI anonymous instant start + auto-claim** (`keel/anon.py`). The
  first CLI command that needs auth with no stored credentials
  auto-calls `POST /v1/auth/anonymous`, prints a one-line notice, and
  proceeds. `keel auth login` detects the anonymous marker and claims
  the workspace into the new account. `KEEL_ANON_AUTO=0` opts out (CI
  environments that want a hard auth failure); `KEEL_ANON_AUTO=1`
  forces it on for non-CLI local surfaces. Expiring anonymous grants
  print an hourly-throttled stderr warning inside the final 48 h.
- **Claim-before-handoff + existing-account claim confirm.** While
  anonymous, every human-required wall (deploy, account linking, live
  scope, quota, the good-result nudge) resolves to `keel_auth_login`
  with "your strategies and backtests come with you automatically" —
  never an app URL the future account won't own. Fresh/empty accounts
  claim silently; an account that already has strategies confirms
  first — CLI TTY prompt (default Y) with `--attach-anon` /
  `--no-attach-anon` twins, MCP via re-calling `keel_auth_login` with
  `attach_anonymous_work` (no re-OAuth). A successful claim pins the
  claimed org so subsequent calls land where the work lives;
  `keel_status` surfaces undecided pending claims.
- **Keel Library on the agent surface** — three new outcome tools
  (`keel_library_list`, `keel_library_get`, `keel_library_fork`) and
  the matching `keel library` CLI commands. Browse the published
  strategy library, read a verified entry, fork it into your own
  workspace — all three available on the hosted listed profile.
- **Shared handoff envelope + round-trip resumption.**
  `HandoffRequired` rides the standard error envelope with
  `blocked_action`, `reason`, `required_actor=human`, `action_url`,
  exact API-sourced `limit_details`/`cost`, `talking_points`, and a
  `resume` block (token or verify-call). Adopted by
  `keel_backtest_run` (quota), `keel_strategy_compose` (plan caps),
  `keel_live_deploy` (scope / unlinked account / go-live), and
  `keel_live_control` (scope). Handoffs are resumable: a status-poll
  `verify_call` confirms the human completed the action.
- **`keel_open_in_app`** — navigation bridge that returns an
  authenticated deep link into the web app for a strategy, backtest,
  or deployment (the only app bridge on the hosted listed profile).
- **`keel_feedback`** — never-fails feedback capture, available on
  every profile in the `always` toolset.
- **`keel_plan_status`** — read-only plan/quota facts with per-surface
  `manage_url` rules. Backtest and deploy responses also surface
  remaining quota when it drops below 20%.
- **4-card widget bundle + `keel open`.** `keel/widgets/` ships four
  self-contained result cards (strategy, backtest, live, deploy
  preflight) rendered from tool responses, with per-surface render
  hints and signed embed tokens minted in the render block. New
  `keel open <kind> <id>` CLI command opens the matching app view.
- **`.well-known` skills manifest + agent card.**
  `scripts/build_skills_manifest.py` generates
  `.well-known/skills/index.json` from the bundled skill registry
  (Stripe-style, install/usage pointers for all 8 shipped skills) and
  `.well-known/agent-card.json` states the product's honest envelope
  (for / not-for), endpoints, and provenance. Both published on the
  site and drift-gated against the bundle.
- **Bundled component catalog regenerated — 182 → 190 components.**
  Eight new: `RealizedVolatility` (frame-general per-target-bar RV
  from finer bars), `CrossSectionalDemedian`, `VolFloorScale`,
  `PortfolioMarginCap` (maintenance-margin cap on the proper
  venue-metadata path), `SeasonedAssetMask`, `RollingUniverseMask`,
  `SignalProjector`, and `TargetSignalProjector`. Existing components
  upgraded: `AdverseVolCap` gains an absolute threshold mode and
  bounded stale-carry gap semantics, `VolAttenuator` a relative
  anchor, `ReturnVolatility` a multi-span blend, `LeverageCap` a
  per-asset cap. The DSL adds the `Universe(max_leverages=...)`
  declaration and `buffer_mode='reference'`.
- **Multi-timeframe strategies in the DSL.** The clock system reaches
  its terminal stage (GATE-2): the validator infers and checks
  CARRY / TRANSFORM / MATCH clock transfer through resamplers,
  projectors, and converters, and two new bundled reference notes teach
  agents how to build multi-timeframe strategies.
- Universe tooling parity: `universe_set` accepts `lookback`, and
  `volume_quartiles` reaches CLI/MCP parity with the platform
  resolver.

### Changed

- **The DSL validator runs on the judgment-table interpreter** — the
  same table-driven engine the web editor executes, so parity is now
  structural rather than test-enforced. Every issue is a 16-field
  `ValidationIssue` envelope (code, severity, path, provenance,
  expected/actual TypeRefs, recoverable data, suggestion) instead of
  the old 5-field shape. New checks ship enabled: value-domain rules,
  factory-call cycle detection (a recursive factory expansion is now a
  validation error, not a hang), implicit slot-read existence checks
  (a pipeline can no longer validate clean and die at runtime), and
  recorded-resolution replay so component version bumps can't silently
  move pinned traces. Several advisory checks (soft bounds, domain
  refinement, slot-sibling narrowing, carrier fallback) surface as
  WARNINGs.
- **Component search ranks with one shared scorer** (the K15 chain) on
  both the SDK/MCP surface and the platform, with ubiquity-weighted
  name matching — "close price as signal" now ranks `ExtractSeries`
  first instead of 11th — live semantic recall for conceptual queries,
  and tokenized example search ("momentum strategy" went from 0 to 14
  results).
- **Go-live defaults to a web handoff.** `keel_live_deploy` returns a
  `HandoffRequired` into the web deploy flow (server-computed sizing,
  pollable resume) instead of enumerating accounts and POSTing a live
  deployment from the terminal. In-terminal direct deploy remains
  behind an explicit `direct=true` opt-in on CLI/local surfaces, and is
  refused outright on hosted surfaces.
- **MCP server instructions rebuilt from the knowledge corpus** — a
  lean always-on operating core (role, discipline rules, two-step
  discovery, routing) plus knowledge-grounded descriptions on every
  tool, and titles/annotations on all tools. The connector now ships
  the Keel mark as its icon.
- Component version-lock surface collapsed to two tools:
  `strategy_components_drift` / `strategy_components_upgrade`.
  Version pins are enforced with structured errors.
- Backtest configs are pre-validated client-side before submission.
- Docs overhaul that ships with the package: AGENTS.md rebuilt
  two-track (quick path + full runbook), `llms.txt`, a canonical
  surface-routing table (which surface for which job, CI-drift-gated),
  and regenerated tool references for the new tool set.

### Fixed

- **`serverInfo.version` now reports the keel-trade wheel version.**
  It was reporting the FastMCP framework version (3.4.0), which is what
  MCP connectors and directories display. The `.mcpb` bundle (which is
  not pip-installed) additionally falls back to the package's own
  version constant instead of `0.0.0`, pinned to `pyproject.toml` by a
  new test.
- **Backtest envelope carries every stored metric key.** The hand
  whitelist surfaced 4 of ~21 stored metrics and the docstring-promised
  `metrics_raw` was never written; the worker's metric dict now passes
  through verbatim (Q-0415).
- **`top_n` under-fill honesty.** Resolving a universe with `top_n`
  larger than the venue's qualifying pool used to write back a resolved
  set that immediately failed the `STALE_UNIVERSE` gate — with a
  re-resolve remediation that reproduced the same state forever. Both
  resolve twins now write `top_n` down to the achievable count and
  report `top_n_written_down`; manual-mode unknown/delisted symbols get
  advisory annotations (Q-0408).
- Universe resolve forwards `lookback` and applies span edits instead
  of destroying source headers; `lookback` is a typed enum
  (`7d`/`30d`/`90d` — a bad value is a clean 422 naming the allowed
  set), and the resolve response carries the per-symbol venue
  leverage map, which the SDK bakes into the source.
- The published SDK validator no longer breaks on a fresh install:
  `tombstoned_options.json` ships in the bundle, a self-containment
  guard keeps the bundle honest, and the bundled `pipeline_engine`
  DSL subset is synced to the platform's (drift-gated).
- Boolean signals are coerced to float and binary value-domains
  guarded, instead of failing downstream arithmetic.
- CLI reference pointed users at `app.usekeel.io/settings/accounts`, a
  route that never existed — corrected to `/accounts` (Q-0216).
- The SDK test suite no longer talks to production (it was minting
  real anonymous orgs); tests run airlocked.

### Compatibility

- **`keel_live_deploy` behavior change:** the default is now a web
  handoff, not a direct deployment. Existing automations that deploy
  from the terminal must pass `direct=true` (CLI/local surfaces only —
  hosted surfaces refuse direct deploy). `keel_live_control` on
  existing authorized deployments is unchanged.
- **Anonymous instant start** activates on the first CLI command that
  needs auth when no credentials are stored. Set `KEEL_ANON_AUTO=0`
  where a hard auth failure is preferred (CI).
- The richer validator surfaces issues that older versions missed
  (implicit slot reads, factory cycles, value domains). Compositions
  that previously validated clean but failed at runtime now fail
  validation — earlier and louder, same conditions.
- The hosted listed profile is intentionally narrower than the local
  surface. Live-write, destructive, account, audit, and workspace
  tools require the local CLI/stdio install.

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

Then open Claude and say _"Connect to Keel."_ — the agent calls
`keel_auth_login`, browser opens, sign-in completes, tokens land in
`~/.keel/config.yaml`. No terminal-side login dance.

### Added

- **`keel_auth_login` MCP-only outcome tool** — runs the same OAuth 2.1
  - PKCE loopback flow as the CLI's `keel auth login`. Optional args:
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

- all 26-27 scopes) — too verbose for both humans and parsing agents.
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
