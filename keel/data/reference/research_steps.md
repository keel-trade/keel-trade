# Research steps

One line a step; `keel_help(topic="step:<Name>")` reads one in full.

## Steps

- `Backtest`: The backtest. Accepts Strategy, Setting.
- `Ranks`: Daily ranks. Accepts Output, Derived.
- `Read`: Stored results. Accepts Paper, Stored.
- `CostStress`: Costs from none to six times. Accepts Book. A variant step. Ranges: `multiples` any value from 0 to 10.
- `DelayStress`: Trading 1 to 3 days late. Accepts Book. A variant step. Ranges: `days` any value from 1 to 10.
- `TopCoins`: Only the most traded coins. Accepts Strategy. A variant step. Ranges: `n` any value from 5 to 200.
- `WithoutBestCoins`: Without its best coins. Accepts Strategy. A variant step. Ranges: `n` any value from 1 to 20.
- `RandomBooks`: Random books of the same size. Accepts Book. Ranges: `books` any value from 20 to 1000.
- `RepickSettings`: Re-picking the settings as you go. Accepts Grid. Ranges: `lookback_days` any value from 20 to 730; `every` any value from 1 to 180.
- `Neighbours`: Nearby settings. Accepts Grid.
- `DeflatedSharpe`: Sharpe discounted for versions tried. Accepts Returns.
- `ResampledPaths`: A year ahead, resampled. Accepts Returns. Ranges: `days` any value from 30 to 730; `paths` any value from 200 to 10000.
- `ReturnByCoin`: Where the return came from. Accepts Book.
- `MarketStates`: Up and down markets. Accepts Returns.
- `CorrelationWithRunning`: Alike what you already run. Accepts Returns.
- `RankIC`: How well it ranks, by days ahead. Accepts Scores. Ranges: `horizons` any value from 1 to 60.
- `Fifths`: Fifths, as bars and as paths. Accepts Scores. Ranges: `horizon` any value from 1 to 60.
- `SplitCompare`: Before and after the split. Accepts Scores, Mask, Events, Labels, Grid. Ranges: `horizons` any value from 1 to 60.
- `DateShuffle`: Against chance. Accepts Scores. Ranges: `horizon` any value from 1 to 60.
- `ICOverTime`: Ranking skill through time, and by month. Accepts Scores. Ranges: `window_days` any value from 20 to 180; `horizon` any value from 1 to 60.
- `TurnoverCoverage`: Turnover and coverage. Accepts Scores.
- `Overlap`: Alike other signals. Accepts Scores.
- `WhatsLeft`: What's left after the others. Accepts Scores. Ranges: `horizon` any value from 1 to 60.
- `TodaysRanking`: The ranking on the last day. Accepts Scores.
- `InsideOutside`: Inside the filter against outside it. Accepts Mask. Ranges: `horizons` any value from 1 to 60.
- `ByRegime`: Returns in each regime. Accepts Labels. Ranges: `horizons` any value from 1 to 60.
- `ICLate`: Ranking skill when acting late. Accepts Scores. Ranges: `late` any value from 1 to 3; `horizons` any value from 1 to 60.
- `SameFooting`: What they ran on. Accepts any.
- `PairedDifference`: Difference against a baseline. Accepts Returns.
- `Correlations`: How alike they are. Accepts Scores, Returns. Ranges: `horizon` any value from 1 to 60.
- `PairOverTime`: A pair over time. Accepts Scores, Returns. Ranges: `window_days` any value from 20 to 365.
- `SignalMix`: Mixing the signals. Accepts Scores. Ranges: `horizon` any value from 1 to 60.
- `Blend`: Running them together. Accepts Returns.
- `PaperVsBacktest`: Paper against the backtest. Accepts Paper.
- `FromCheckpoint`: From a checkpoint (champion against challenger). Accepts Returns.
- `SettingsGrid`: Every setting. Accepts Strategy. A variant step.
- `EventReturns`: Returns after the event, against random dates. Accepts Events, Trades. Ranges: `before` any value from 0 to 30; `after` any value from 1 to 60; `anchors` any value from 5 to 50; `draws` any value from 500 to 5000.
- `EntryIC`: Skill among entries. Accepts Trades. Ranges: `horizons` any value from 1 to 60; `anchors` any value from 5 to 50; `draws` any value from 500 to 5000.
- `TradeExcursions`: Best and worst move inside each trade, in R. Accepts Trades.
- `ExitsByReason`: Exits by reason, and what came after. Accepts Trades. Ranges: `after_days` any value from 1 to 30.
- `StopTargetMap`: Stop × target. Accepts Trades. A variant step. Ranges: `stops` any value above 0 and up to 0.5; `targets` any value above 0 and up to 2.
- `TradeStats`: Trade stats. Accepts Trades.
- `TradeShuffle`: Trades in a random order. Accepts Trades. Ranges: `shuffles` any value from 200 to 10000.

## Reference forms

- `Output(ref, output=name, role=?)`: a pinned pipeline's declared output, as a series. Example: `Output("keel/btc_trend@1", output="trend", role="regime")`
- `Final(step_output or Output(...), measure=name, at={axis: value}?)`: one value: a served measure, or an output's last value. Example: `Final("ic", measure="ic", at={"horizon": 1})`
- `step_id or step_id.output`: a step's result in this study. Example: `grid="map"`
- `StudyOut(study_id, version=n, output=name)`: another study's output, pinned at a run version, on the same footing. Example: `StudyOut("stu_mapv3000000000000000000000", version=1, output="grid")`

## Addresses

Forms: `step_path.param`, `var[name]`, `execution.<field>`, `globals.<field>`.

- step_path: `["factory[" name "]." | "var[" name "]."] seg ("." seg)*; seg := "step[" i "]" | "branch[" name "].step[" i "]"`
- Copy the address verbatim from the subject version's served address list; resolution is an exact lookup with no alias, ordinal, search or fallback.
- Examples: `step[2].period`, `step[7].branch[max_hold].step[1].threshold`, `var[lb]`, `execution.buffer_threshold`.

Builders: `Mix`.

## Templates

- `check`, Check a strategy: exactly one strategy. Defining: `Backtest`, `CostStress`, `DelayStress`. Default: `TopCoins`, `WithoutBestCoins`, `DeflatedSharpe`, `ResampledPaths`, `ReturnByCoin`, `MarketStates`. When: `RepickSettings` (the subject has settings to re-pick, on every plan: without `feature:research_repick` it is a locked row that never runs or counts), `Neighbours` (the Check has a grid, on every plan: over the Check's own grid, without `feature:research_repick` it is a locked row that never runs or counts), `CorrelationWithRunning` (the user runs something on paper or live), `SettingsGrid` (the Check re-picks its settings and no same-footing map exists, on every plan: without `feature:research_repick` it is a locked row that never runs or counts).
- `signal`, Study a signal: exactly one signal (an Output). Defining: `RankIC`, `Ranks`. Role signal: `RankIC`, `Ranks`. Role filter: `InsideOutside`, `Ranks`. Role regime: `ByRegime`, `Ranks`. Role event: `EventReturns`, `Ranks`. Default: `Fifths` (role signal), `SplitCompare` (every role, with a split), `DateShuffle` (role signal), `ICOverTime` (role signal), `TurnoverCoverage` (role signal), `Overlap` (role signal), `WhatsLeft` (role signal), `TodaysRanking` (role signal).
- `compare`, Compare: two or more subjects that all give returns, or all give scores. Defining: `Correlations`, `SameFooting`. Default: `Backtest` (the subjects are strategies), `Ranks` (the subjects are signals), `Read` (paper runs or stored results are subjects), `RankIC` (the subjects are signals), `ICOverTime` (the subjects are signals), `WhatsLeft` (the subjects are signals), `PairedDifference` (returns), `PairOverTime`, `SignalMix` (the subjects are signals), `Blend` (returns). When: `PaperVsBacktest` (a paper run is a subject), `FromCheckpoint` (a paper run is a subject).
- `map`, Map a strategy's settings: exactly one strategy. Defining: `SettingsGrid`. Default: `Neighbours`, `SplitCompare` (with a split).
- `entries`, Entries and exits: exactly one strategy. Defining: `Backtest`, `EventReturns`, `TradeExcursions`. Default: `ExitsByReason`, `StopTargetMap`, `TradeStats`, `TradeShuffle`. When: `EntryIC` (the strategy stores a strength value beside its entries).

## Rules

Each `STUDY_*` code the validator can return, and its fix.

- `STUDY_ADDRESS_CHANGED`: Confirm the setting at its new value, or pick another.
- `STUDY_ADDRESS_DEAD`: Pick a setting the strategy uses.
- `STUDY_ADDRESS_INERT`: Vary how often it trades too, or pick another setting.
- `STUDY_ADDRESS_MOVED`: for example, confirm the move to Period (ROC, second).
- `STUDY_ADDRESS_NOT_FOUND`: for example, did you mean Period (ROC)?
- `STUDY_ADDRESS_NOT_SETTABLE`: Pick another setting.
- `STUDY_ADDRESS_SYNTAX`: for example, ROC has period at step[2]; copy one from the address list.
- `STUDY_AXES_SAME_NAME`: Give each setting a name of its own, such as "fast <name>" and "slow <name>".
- `STUDY_AXES_SAME_TARGET`: Remove one of the two values for <address>.
- `STUDY_BAR_AT_INVALID`: Pick one cost level for this bar.
- `STUDY_BAR_ON_DETAIL`: Put the bar on an evidence output.
- `STUDY_BAR_OP`: Write ">=", "<=", or "between" with [low, high].
- `STUDY_BAR_UNKNOWN`: Use one of: sharpe, cagr, `max_drawdown`, `total_return`, `sharpe_vs_base`.
- `STUDY_BASELINE_UNKNOWN`: Use one of: <valid>.
- `STUDY_CLOCK_MISMATCH`: Study subjects on one clock in this step, or compare their daily returns.
- `STUDY_COINS_HOW_MISSING`: Add chosen=ByHand(), or the rule that picked them.
- `STUDY_COINS_UNKNOWN`: Remove <coin>, or pick a coin the store carries.
- `STUDY_COINS_UNORDERED`: Choose the coins by trading volume, or vary them another way.
- `STUDY_COINS_UNRESOLVED`: Choose the coins another way, for example the top 100 by 30-day volume.
- `STUDY_COSTS_INVALID`: Set <name> inside that range.
- `STUDY_COSTS_NOT_USED`: Set the costs to none.
- `STUDY_DATA_MANUAL`: Leave the data snapshot out and save; reading other data is a linked study.
- `STUDY_DATA_MISSING`: Save the study again so its data snapshot is written.
- `STUDY_DAYS_INVALID`: Write each date as YYYY-MM-DD, with the start before the end.
- `STUDY_DAYS_START_MOVED`: The days can start on or after <start>.
- `STUDY_DAYS_UNSEALED`: End the days on <sealed> or earlier.
- `STUDY_DECLARATION_DUPLICATE`: Merge the two <section>(...) calls into one.
- `STUDY_DECLARATION_MISSING`: Add <section>(...).
- `STUDY_DOMAIN_OUT_OF_RANGE`: for example, change the period to a value from 1 to 100000.
- `STUDY_EVIDENCE_CHANGED`: Save it as a new version.
- `STUDY_ID_PRIVATE_KIND`: Rename it so it does not begin like a Keel id.
- `STUDY_INPUT_AMBIGUOUS`: Remove one of them, or set one of them to read only the subjects it needs.
- `STUDY_INPUT_UNPRODUCED`: Add <fix step> before it.
- `STUDY_LIBRARY_REF_UNKNOWN`: Pick an item from Keel's library.
- `STUDY_LIMIT`: for example, keep 50 or fewer.
- `STUDY_NEWER_VERSION`: Accept v<version> to study the newer version.
- `STUDY_PAPER_COMMIT_MOVED`: Read it from <day>, at <version>.
- `STUDY_PIPELINE_TOO_LARGE`: Study a smaller strategy.
- `STUDY_PLAN_CELL_TOO_LARGE`: Use fewer coins or days, or a coarser clock.
- `STUDY_PLAN_CHILD_TOO_LARGE`: Use fewer coins or days, or a coarser clock.
- `STUDY_PLAN_CLOCK_UNSUPPORTED`: Study a version on a clock the platform serves.
- `STUDY_PLAN_HISTORY`: Start the days on <first> or later.
- `STUDY_PLAN_HISTORY_LIMIT`: Shorten the days to <limit> or fewer.
- `STUDY_PLAN_NO_COMMON_COINS`: Add one of them to the study's coins, or study another subject.
- `STUDY_PLAN_STEP_NOT_INCLUDED`: Plans are changed in the Keel web app.
- `STUDY_PLAN_STEP_UNAVAILABLE`: Remove <step> for now.
- `STUDY_PLAN_STORED_EXPIRED`: Nothing to do: the plan includes the rerun.
- `STUDY_PLAN_STORED_EXPIRING`: Nothing to do: the plan includes the rerun.
- `STUDY_PLAN_STUDIES_LIMIT`: Runs that reuse every result are free.
- `STUDY_PLAN_TEMPLATE_NOT_INCLUDED`: Plans are changed in the Keel web app.
- `STUDY_PLAN_TOO_COSTLY`: Cut the map or the subjects.
- `STUDY_PLAN_TOO_LARGE`: Cut the map or the subjects.
- `STUDY_PLAN_TOO_MANY_BACKTESTS`: Cut the map to <limit> settings or fewer.
- `STUDY_REF_CYCLE`: Replace one reference in the loop with a number or another output.
- `STUDY_REF_FINAL_UNDEFINED`: Read a measure the step serves, at a point its output has.
- `STUDY_REF_FOOTING`: Add a settings map to this study, or start a linked study on these days.
- `STUDY_REF_TYPE`: Bind the chart to a step output; draw the number with a mark, HLine(Final(...)).
- `STUDY_RUN_SETTING_INVALID`: Set <setting> to <allowed>.
- `STUDY_SPLIT_OUTSIDE_DAYS`: Pick a split day after <start> and before <end>.
- `STUDY_STEP_ARITY`: Add another subject, or remove <step>.
- `STUDY_STEP_DUPLICATE_WORK`: Remove one of them.
- `STUDY_STEP_ID_DUPLICATE`: Rename the second one.
- `STUDY_STEP_ID_RESERVED`: Name the step something else.
- `STUDY_STEP_NEEDS_SETTINGS`: Add a setting to sweep, chosen from the strategy's settings.
- `STUDY_STEP_NEEDS_SPLIT`: Set a split day inside the study's days.
- `STUDY_STEP_NEEDS_TRADES`: Run it on a strategy that trades.
- `STUDY_STEP_NOT_AVAILABLE`: Remove it.
- `STUDY_STEP_ON_UNKNOWN`: Use one of: <valid>.
- `STUDY_STEP_PARAM_INVALID`: for example, change the days ahead to a value from 1 to 60.
- `STUDY_STEP_PARAM_UNKNOWN`: Use one of: <valid>.
- `STUDY_STEP_REF_INVALID`: Name a settings grid step, or a map from another study on the same footing.
- `STUDY_STEP_SUBJECT_TYPE`: Add a subject this step reads, or remove the step.
- `STUDY_STEP_UNKNOWN`: Pick a step from the step catalog (keel research steps).
- `STUDY_STEP_UNRESOLVED`: Save the study again so <what> is written.
- `STUDY_STUDYOUT_UNRUN`: Name a version that has run.
- `STUDY_SUBJECT_AMBIGUOUS`: Save a version with one lb.
- `STUDY_SUBJECT_DUPLICATE`: Remove one of them, or pin another version.
- `STUDY_SUBJECT_ID_DUPLICATE`: Rename the second one.
- `STUDY_SUBJECT_KIND_MISMATCH`: Study one of its columns as a signal instead.
- `STUDY_SUBJECT_NOT_FOUND`: Pick a version from your own work or Keel's library.
- `STUDY_SUBJECT_OUTPUT_NOT_FOUND`: Use one of its outputs: <valid>.
- `STUDY_SUBJECT_REF_MALFORMED`: Copy the reference from the strategy's page or from `keel_strategy_get`.
- `STUDY_SUBJECT_ROLE_MISMATCH`: Remove the role given to it, or pick an output whose role is <role>.
- `STUDY_SUBJECT_UNPINNED`: Pin it: <pinned>.
- `STUDY_SYNTAX`: for example, check the brackets, commas and quotes near line 1.
- `STUDY_UNKNOWN_CALL`: for example, subjects are referenced, not written here: add the pipeline to Subjects as Strategy(`"str_…@n"`).
- `STUDY_VARIANT_INVALID`: Adjust `fast_period` or `slow_period` so `fast_period` < `slow_period`.
- `STUDY_VIEW_COLUMNS`: Remove Columns(...).
- `STUDY_VIEW_OF_MISSING`: Choose the step output it plots.
- `STUDY_VIEW_OF_UNKNOWN`: Use one of: <valid>.
- `STUDY_VIEW_ON_DETAIL`: Chart an evidence output instead.

## Views

A view draws one step output: `<Kind>("<title>", of="<step>", ...)`, `of=` a step id or `<step>.<output>`; each also takes `title`, `marks`, `where`. An axis is `Axis("<name>")`; a measure `Raw("<m>")`, `Rebased("<m>")`, `Rank("<m>")`; an interval `[Raw("<lo>"), Raw("<hi>")]`; a number `Final("<step>", measure="<m>")` (a chart computes none). Marks: `HLine(n)`, `VLine(v)`, `Ring(Yours())`, `Box(Selected())`. `Page(default="<title>", switch=[...])` picks the opening chart; it is not a chart.

- `Curve(x*=axis, y*=measure, band=interval, series=axis; dots=false)`: needs an ordered x axis (a setting, horizon, days from an event, time or fifth).
- `Bars(x*=axis, y*=measure, error=interval, dot=measure, group=axis; horizontal=false, inline_rows=false)`: needs one x axis of 1 to 200 values.
- `Heatmap(x*=axis, y*=axis, colour*=measure; order="values", select=null)`: needs two axes, each with at least 2 values.
- `Distribution(x*=measure, overlay=axis, marker=number; bins="auto", normalise=false)`: needs one measure with at least 10 points.
- `Fan(x*=axis, y*=measure; observed=true)`: needs a time or horizon x axis and a percentile axis.
- `Checks`: `of="bars"`, no channels.
- `Lines(plot*=measure, x=axis, series=axis, colour=measure or Category("<m>"); reference=[])`: needs a time axis (days), with an entity series.
- `River(plot*=measure, x=axis, series=axis)`: needs an entity series over time.
- `Scatter(x*=measure, y*=measure, colour=measure or Category("<m>"))`: needs points on an entity axis, or the days of a time axis.
- `Table(rows*=axis, columns=[measure, ...], sort=measure)`: needs one rows axis.
