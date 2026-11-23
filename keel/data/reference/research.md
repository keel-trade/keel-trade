# Research

<!-- topic: research -->

Research measures a strategy or signal the user already has: how it holds against costs, delay and chance, how a
signal ranks coins, how two things compare, what a map of its settings looks like, and what happens around its
entries and exits. A study is a small document (`study/1`) that names its subjects, the days and coins, the steps to
run, the user's bars and the charts. Keel plans it, says what it would run and spend, and runs only what is new.

Not for building or editing a strategy: that is `keel_strategy_compose`. Not for one backtest: that is
`keel_backtest_run`.

## The flow

1. `keel_research_plan` states the work: what would run, what is reused, the research time it spends and the
   versions tried. Nothing runs and nothing is reserved.
2. `keel_research_run(plan_id=…)` saves the study and runs what is new. It waits briefly, then returns the study
   or its progress.
3. `keel_research_get(study_id=…)` reads every measurement with its sample and interval, the user's bars, and the
   progress while it runs. With `view` (an index or a chart's title) it reads that chart's rows, 25 at a time, by
   cursor. With no id it lists the user's studies. With `subject` it reads a subject's address list.

A plan lasts 24 hours. A second run of one plan returns the run it already made. Planning, reading and saving do not
count against the account; a run that reuses everything, or runs nothing, does not either.

## Keel's library

`keel_research_get(library="signals")` lists Keel's library signals, each with the exact call a study names it by,
for example `Output("keel/momentum_7d@1", output="momentum_7d")`; study one with
`keel_research_plan(template="signal", subjects=[that call])`. `library="benchmarks"` lists the benchmarks
(`Strategy("keel/btc_hold@1")`, BTC held) and `library="market_states"` the market states, each added to a study
with `add=[…]`; `library="strategies"` lists Keel's library strategies. `library="mine"` lists the user's own
signals the same way, each with its call (`Output("str_…@2", output="mix")`); the CLI's `keel signals list` is that
read. Copy the call as written: the refs are pinned with `@version`.

## The five templates (plan's template form)

`keel_research_plan(template=…, subjects=[…])`, each subject a `study/1` subject call:

- `check` a strategy: costs, delay, fewer coins, without its best coins, re-picking the settings, nearby settings
  and the Sharpe discounted for the versions tried (random books is a step you add by hand). `subjects=['Strategy("str_…@3")']`.
- `signal`: how well a signal ranks the coins 1 to 30 days ahead, each fifth over time, against chance, and what is
  left after Keel's signals. `subjects=['Output("str_…@1", output="…")']`.
- `compare`: two to eight strategies or signals side by side on one footing.
- `map` a strategy's settings: `axes` is required, 1 to 3 `SettingAxis(address, [values], name=…)` calls. Copy
  each address verbatim from `keel_research_get(subject='Strategy("str_…@3")')`.
- `entries`: what happens to returns around a strategy's entries and exits.

Optional on every template: `days={"start": "2025-03-23", "end": "2026-10-29"}`, `split` (a date that divides
the days into before and after), and `coins` (a coin rule).

## The other plan forms

- `source`: a whole `study/1` document (131,072 bytes or fewer).
- `study_id` with `source`: an edit, always the whole document, never a diff. Send `base` too: the read the source
  came from (`keel_research_get` serves it beside the source). When the study's Draft holds changes that read does
  not, the edit is refused with the Draft named and nothing is planned: read the Draft (`version` = its number) and
  edit that. A change to the title, bars or views only is a presentation change, which reruns nothing: it is made
  in the Keel web app (`keel_app_link` with the study's id).
- `study_id` with `add`: add a subject or a reference, e.g. `add=['Output("str_…@1", output="…")']`; `to` names
  a step for an inline `against=` list. Every reference is pinned with `@version`.
- `share_id` (with your own `subjects`): run a shared study on yours.
- `study_id` with `cell={"grid": "map", "at": {"lookback": 12, "hold": 20}}`: one map cell's full report, the next
  version with that cell as a `Setting` subject and one `Backtest` of it. `grid` is the map's step id, `at` the
  cell's settings (each of the map's setting axes, by name), and `subject` names the map's subject when it has
  several. Its backtest is the map's own cell, reused.
- `from_plan`: plan again from an expired or stale plan. When the data moved (Keel refreshed its market data since
  a Draft was saved or a version ran), the Draft's run, its plan and `from_plan` all answer the same stale refusal:
  its `stale` fact (kind `newer_data`) carries `offer.plan`, and planning that (`study_id` with its `source`) runs
  the study again on today's data as a linked study.
- `study_id` with `resume: true` or `retry: true`: run what was stopped, or what could not run on Keel's side.
- `study_id` with `version` alone: plan that study's never-run Draft as it is saved; `keel_research_run` runs the
  plan it returns.

## Documents

A study document has six sections, in this order: `Study`, `Subjects`, `Run`, `Steps`, `Bars`, `Views`.
`keel_help(topic="research_steps")` lists every step, and `keel_help(topic="step:CostStress")` serves one step
in full with its parameters and an example.

A check of a strategy, with one bar and two charts. Both examples use Keel's library (`keel/<name>@1`), so they plan
as written; for the user's own strategy, put its pinned `str_…@n` ref in `Subjects`:

```study
Study(title="Equal weight top 30, costs and delay")
Subjects(ew=Strategy("keel/equal_weight_top30@1"))
Run(days=Days("2025-03-23", "2026-10-29"), split=Split("2026-04-17"),
    coins=Coins(chosen=SubjectUniverse("ew")), costs=Costs(fee=0.00045, slippage=0.00045))
Steps(
    base=Backtest(),
    costs=CostStress(multiples=[0, 1, 2, 4]),
    late=DelayStress(days=[1, 2, 3]),
)
Bars(Bar("costs", "sharpe", ">=", 0.3, at={"cost_multiple": 2}))
Views(
    Page(default="Growth of 1", switch=["Growth of 1", "Sharpe as costs rise"]),
    Lines("Growth of 1", of="base", plot=Raw("growth")),
    Curve("Sharpe as costs rise", of="costs", x=Axis("cost_multiple"), y=Raw("sharpe"), marks=[HLine(0)]),
)
```

A signal study:

```study
Study(title="7-day momentum")
Subjects(mom=Output("keel/momentum_7d@1", output="momentum_7d"))
Run(days=Days("2025-03-23", "2026-10-29"), coins=Coins(chosen=TopByVolume(100, days=30, on="2025-03-23", market="perp")), costs=NoCosts())
Steps(
    ranks=Ranks(),
    ic=RankIC(horizons=[1, 5, 10, 20]),
    fifths=Fifths(groups=5, horizon=1),
)
Views(Curve("How well it ranks", of="ic", x=Axis("horizon"), y=Raw("ic"), marks=[HLine(0)]))
```

## References

- Strategies: `Strategy("str_…@n")`, always pinned. Find the user's with `keel_strategy_search` and Keel's library
  strategies with `keel_library_list`.
- Signals and other pipeline outputs: `Output("str_…@n", output="name")`.
- A screen's column: `Output("str_…@n", output="column")`.
- A paper run or a stored backtest, read with its own provenance: `Paper("dep_…")`, `Stored("btr_…")`.
- Another study's output: `StudyOut("stu_…", version=n, output="…")`.

## Reading the answer

Every number is Keel's. Each measurement names its unit, its sample (days, coins, books) and its interval, or why
the interval is withheld. What was not measured is said. A run that could not finish on Keel's side is said plainly
and costs nothing. Keel does not grade a result: each of the user's bars is reported as met or not met.

## Where each verb lives

| Verb                                                          | Listed connector          | Full profile (`keel mcp serve`) | CLI                                                            | App |
| ------------------------------------------------------------- | ------------------------- | ------------------------------- | -------------------------------------------------------------- | --- |
| plan, run, get                                                | yes                       | yes                             | `keel research plan / run / get`                               | yes |
| view_set, brief, note, cancel, make_signal                    | `keel_app_link`           | yes                             | `keel research view-set / brief / note / cancel / make-signal` | yes |
| add, save, run-draft, watch, data, list, resume, retry, steps | through plan, run and get | through plan, run and get       | `keel research <verb>`                                         | yes |
| copy, projects, set aside, restore, archive, delete, shares   | `keel_app_link`           | `keel_app_link`                 | `keel research <verb>`                                         | yes |
