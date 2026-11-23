<!-- The screens corpus source: the operating-core sections that teach screens.
Sectioned exactly like `system/operating_core.md` (same front matter, same
reader, `system/assemble.py`), and kept OUT of `system/` on purpose: every file
under `system/` is a strategy prompt carrier for Keel's chat, and screen
guidance lives only in screen carriers (screeners L13). Its sections are
`surfaces: [mcp]`, so they reach the MCP server instructions and the served
operating-core document, never a chat prompt. -->

---

id: screens
layer: body
surfaces: [mcp]
profiles: [listed, full]
when: screens
register: base

---

SCREENS — a question about which coins rank highest or lowest now is a screen: a pipeline file with `Outputs(...)` in place of `Execution(...)`; build it with the same sequence, with keel_screener_run in place of keel_backtest_run. A filter, sort, title or top-N over an output it stores is keel_screener_view_set, not a new version. A ranking is data, not a recommendation.

Plans bound the coins one screen covers (`keel_plan_usage` reports `screens.coins_per_screen`). A screen over it is refused before anything is spent (`SCREEN_COINS_OVER_PLAN`), compose's dry run reports it first in `plan_findings`, and the same save, run or copy with `narrow_to_plan: true` keeps the highest-volume coins the plan covers. The limit is per screen: several runs that together cover more coins are a way around it, not a screen the plan includes.
