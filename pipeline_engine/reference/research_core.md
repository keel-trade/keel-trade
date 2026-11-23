<!-- The research corpus source: the one operating-core section that points an
agent at Research. Sectioned exactly like `screens_core.md` (same front matter,
same reader, `system/assemble.py`) and kept OUT of `system/` for the same
reason: every file under `system/` is a strategy prompt carrier for Keel's
chat. Its section is `surfaces: [mcp]` and `when: research_served`, so it
reaches the MCP instructions only for a caller served Research (its
`/v1/me`): a local `keel mcp serve`'s one caller, or the hosted server's
caller at initialize (services/mcp-server swaps it in per caller), and on
the listed profile only beside the SCREENS line. Never a chat prompt and
never the served operating-core document. Off, every instructions string is
byte for byte what it was before this file (gate E). How to build a study is
`keel_help(topic="research")`, not this line. -->

---

id: research
layer: body
surfaces: [mcp]
when: research_served
register: base

---

RESEARCH — a question about a strategy or signal the user already has (whether it holds up against costs, delay and chance, how it compares with another, or how its settings behave) is a study: keel_research_plan with a template (check, compare, map, signal or entries) says what would run and what it spends, then keel_research_run runs that plan. One backtest is still keel_backtest_run.
