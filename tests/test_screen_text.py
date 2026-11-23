"""What a screen tool's TEXT says (Q-3634).

On claude.ai the model reads only a tool's text block, so the screen text must
carry the rows the rendered view shows, the view revision, and nothing that
misleads. Founder test, staging 2026-11-05: a TopN(15) view listed 10 rows and
"169 more assets" (counted from the universe), cut its column labels, doubled
the unit ("Funding now, a year (% a year)"), and kept a dangling
"universe 179 assets ·" line and "179 of 179 assets have a value".

Each guard names its ``# SEED:`` — the edit that restores the old behaviour
and reds it — and every fixture is built from the checked-in fallback
envelope, so the arms read real envelope shapes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._screener_view import TEXT_ROWS, build_result


FIXTURE = Path(__file__).parent / "fixtures/cards/screener_fallback.json"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
COINS = [a + b for a in LETTERS[:3] for b in LETTERS][:60]


def screen(
    *,
    passing=15,
    rows=None,
    universe=179,
    label="Funding now, a year",
    unit="rate_per_year",
    view_revision=2,
    tie=None,
    entered=(),
    left=(),
    columns=("funding_now",),
    with_value=None,
):
    """A founder-shaped envelope: a TopN(``passing``) funding view (None = no
    filter), its rows already filtered by keel-api the way the route serves them."""
    env = copy.deepcopy(json.loads(FIXTURE.read_text()))
    env["screener"] = {"strategy_id": "str_" + "f" * 26, "name": "Top funding", "version": 1}
    if view_revision is not None:
        env["screener"]["view_revision"] = view_revision
    c = env["completed"]
    c["manifest"]["universe"] = {"count": universe}
    c["manifest"]["outputs"] = [{"name": "funding_now"}, {"name": "funding_7d"}]
    c["labels"] = [
        {"label": label, "unit": unit},
        {"label": "Funding, 7-day mean", "unit": "rate_per_year"},
    ]
    c["ranked_by"], c["order"] = "funding_now", "desc"
    count = rows if rows is not None else (passing if passing is not None else 50)
    values = [[COINS[i], 1.0 - i / 100, 0.5] for i in range(min(count, 50))]
    c["latest"] = {"columns": ["asset", "funding_now", "funding_7d"], "rows": values}
    c["graphs"] = {"default_channels": {"kind": "Table", "columns": list(columns)}}
    c["copy"].update(
        coverage=f"{with_value or universe} of {universe} assets have a value",
        gaps=None,
        ranking="",
        clock="Hourly bars",
    )
    c["coverage"] = {"with_value": with_value or universe, "of": universe}
    if passing is None:
        c["passing"] = None
        c["hero"]["passing"] = None
    else:
        names = [r[0] for r in values]
        c["passing"] = {
            "now": {"bar_ms": 1793883600000, "assets": names[:5]},
            "previous": {"bar_ms": 1793880000000, "assets": []},
            "entered": list(entered),
            "left": list(left),
            "counts": {"passing": passing, "entered": len(entered), "left": len(left)},
            **({"tie": tie} if tie else {}),
        }
        c["hero"]["passing"] = names
    return env


def text(env, size="view"):
    return build_result(env, ToolContext(), size=size).extra["view"]["markdown"]


def table_rows(markdown):
    return [line for line in markdown.splitlines() if line.startswith("| ") and "---" not in line][
        1:
    ]


# ── 1. The rows follow the rendered view ─────────────────────────────────────


def test_a_top_15_view_lists_15_rows_and_no_universe_count():
    """# SEED: cut the table back to `shown_rows[:10]` in build_result — 10 rows,
    this reds; restoring `total - min(10, len(rows))` reds the "more" arm."""
    markdown = text(screen(passing=15))
    rows = table_rows(markdown)
    assert len(rows) == 15, markdown
    assert rows[0].startswith("| 1 | AA |") and rows[-1].startswith("| 15 | AO |")
    assert "15 of 179 coins pass now" in markdown
    # The rest is counted from the passing set, never from the universe.
    assert "more" not in markdown and "164" not in markdown and "169" not in markdown


def test_a_long_pass_list_stops_at_the_text_cap_and_counts_the_rest_from_passing():
    """# SEED: count the rest as `universe - shown` — "154 more" appears, red."""
    markdown = text(screen(passing=40))
    assert len(table_rows(markdown)) == TEXT_ROWS == 25
    assert f"{40 - TEXT_ROWS} more pass — see Keel." in markdown
    assert "154 more" not in markdown


def test_a_view_with_no_filter_is_a_ranking_with_no_pass_words():
    """keel-api serves `passing` null for a view with no filter (contract 2);
    an older envelope's "179 of 179 pass" reads the same way.

    # SEED (run): delete the all-pass return in `passing_facts` — the older
    envelope's arm reds ("179 of 179 coins pass now")."""
    for env in (screen(passing=None), screen(passing=179, rows=50)):
        markdown = text(env)
        assert "179 coins ranked by Funding now, a year (highest first)." in markdown
        assert "pass" not in markdown, markdown
        assert len(table_rows(markdown)) == TEXT_ROWS
        assert f"{179 - TEXT_ROWS} more ranked — see Keel." in markdown


def test_a_tie_at_the_cutoff_is_said_in_words():
    """# SEED: drop `tie_words` from the summary line — red; the control (no
    tie) never says "tie"."""
    markdown = text(screen(passing=15, tie={"value": 0.109, "count": 8}))
    assert "15 of 179 coins pass now, ranked by Funding now, a year (highest first). " in markdown
    assert "8 coins tie at +10.9% at the cutoff." in markdown
    assert "tie" not in text(screen(passing=15))


def test_entered_and_left_appear_only_when_something_moved():
    """# SEED: always append the "Since …" line — the quiet arm reds."""
    quiet = text(screen(passing=15))
    assert "Since" not in quiet and "entered" not in quiet and "left" not in quiet
    moved = text(screen(passing=15, entered=["AA"]))
    assert "Since Nov 5 12:00 UTC: entered AA." in moved
    assert "left" not in moved  # the side that did not move says nothing


# ── 2. Labels in full, the unit once, no noise lines ─────────────────────────


@pytest.mark.parametrize(
    "label,unit,header",
    [
        # The label already says it: no second unit (the founder's doubled one).
        ("Funding now, a year", "rate_per_year", "Funding now, a year"),
        ("Funding now (% a year)", "rate_per_year", "Funding now (% a year)"),
        ("Funding per year", "rate_per_year", "Funding per year"),
        ("Funding rate, per hour", "rate_per_hour", "Funding rate, per hour"),
        # Control arms: the label does not say it, so the header does, once.
        ("Funding now", "rate_per_year", "Funding now (% a year)"),
        ("Funding now", "rate_per_hour", "Funding now (% an hour)"),
        ("Price change", "percent", "Price change"),
    ],
)
def test_the_unit_is_said_once(label, unit, header):
    """# SEED: append the unit words unconditionally in `column_label` — the
    first four arms red, the controls stay green."""
    markdown = text(screen(label=label, unit=unit))
    head = next(line for line in markdown.splitlines() if line.startswith("| # |"))
    assert head == f"| # | Asset | {header} |", head


def test_labels_are_never_cut():
    """# SEED: cap labels at 32 characters again (`text(..., 32)`) — red in the
    header and in the structured labels."""
    long = "Relative volume · rank across coins over the last 30 days"
    env = screen(label=long, unit="rank")
    markdown = text(env)
    assert f"| # | Asset | {long} |" in markdown
    assert "…" not in markdown
    result = build_result(env, ToolContext())
    assert result.extra["labels"][0]["label"] == long
    assert len(result.extra["labels"]) == 2  # every output's label, not the first three


def test_no_universe_line_and_coverage_only_when_fewer_have_a_value():
    """# SEED: append `copy.coverage` unconditionally — the full arm reds."""
    full = text(screen(passing=15))
    assert "universe" not in full and " ·\n" not in full and "· ·" not in full
    assert "have a value" not in full
    partial = text(screen(passing=15, with_value=170))
    assert "170 of 179 assets have a value" in partial


def test_the_gap_line_is_the_servers_to_send():
    env = screen(passing=15)
    assert "gaps" not in text(env)
    env["completed"]["copy"]["gaps"] = "Some assets have gaps in upstream data."
    assert "Some assets have gaps in upstream data." in text(env)


def test_a_table_view_draws_its_own_columns():
    """The view's declared Table columns are the text's columns; a view with
    none (Lines) shows the ranking first, then the next outputs."""
    assert table_rows(text(screen(columns=("funding_now",))))[0].count("|") == 4
    lines = text(screen(columns=()))
    head = next(line for line in lines.splitlines() if line.startswith("| # |"))
    assert head == "| # | Asset | Funding now, a year | Funding, 7-day mean (% a year) |"


# ── 3. The view revision in the header ──────────────────────────────────────


def test_the_header_names_the_view_revision():
    """# SEED: drop the `view rev` part of `title` — red; the control (no saved
    view) never says it."""
    assert text(screen(view_revision=2)).startswith("**Top funding** · screen v1 · view rev 2 ·")
    assert "view rev" not in text(screen(view_revision=None))
    assert "view rev" not in text(screen(view_revision=0))
    receipt = text(screen(view_revision=3), size="receipt")
    assert receipt.startswith("Screen · Top funding v1 · view rev 3 ·")


def test_a_receipt_names_the_first_coins_with_their_value():
    receipt = text(screen(passing=15), size="receipt")
    first, link = receipt.splitlines()
    assert "15 of 179 coins pass now" in first
    assert "AA +100.0%, AB +99.0%, AC +98.0%, AD +97.0%, AE +96.0%" in first
    assert "AF" not in first and "· ·" not in first
    assert link.startswith("View in Keel: ")


# ── 3b. The range below the rows (Q-3638 produces it, Q-3639 says it) ───────

#: keel-api's `completed.range` (CONTRACT field 1) for the founder's morning:
#: the top 15 are all positive, while MINA paid −140.6% a year.
RANGE = {
    "output": "funding_now",
    "low": {"asset": "MINA", "value": -1.406},
    "high": {"asset": "AA", "value": 1.0},
    "below_zero": 44,
    "count": 179,
}
RANGE_LINE = "Across 179 coins: −140.6% (MINA) to +100.0% (AA) a year; 44 below zero."


def with_range(env, **change):
    env["completed"]["range"] = {**copy.deepcopy(RANGE), **change}
    return env


def test_a_top_n_of_positives_says_the_range_below_its_rows():
    """Claude told the user "no coin is paying negative funding at this hour"
    from a top-15 screen whose text listed only positive rows. The text now
    says the ranked output's range over every coin with a value.

    # SEED: drop `lines.append(span_words + ".")` in build_result — the view
    # arm reds; dropping `span_words` from the receipt's parts reds that arm.
    """
    env = with_range(screen(passing=15))
    markdown = text(env)
    rows = table_rows(markdown)
    # Not vacuous, and out of the seed's reach: the shown set really is the
    # top 15 and every listed value is positive, so "no negatives" was the
    # only reading the old text allowed.
    assert len(rows) == 15 and all("| +" in row for row in rows), rows
    lines = markdown.splitlines()
    assert RANGE_LINE in lines, markdown
    # Read with the summary, before the table.
    assert lines.index(RANGE_LINE) == 2 and lines[1].startswith("15 of 179 coins pass now")
    receipt = text(env, size="receipt").splitlines()[0]
    assert RANGE_LINE.rstrip(".") in receipt
    # The card and a model reading structuredContent get it as served.
    assert build_result(env, ToolContext()).extra["range"] == RANGE


def test_the_range_is_said_only_when_the_rows_are_a_subset():
    """# SEED: pass `range_words` a rendered count of 0 — the all-shown arm
    (15 rows of 15 coins with a value) reds; the absent arm stays quiet."""
    whole = with_range(screen(passing=None, universe=15, rows=15), count=15)
    assert len(table_rows(text(whole))) == 15  # every coin with a value is listed
    assert "Across" not in text(whole)
    # keel-api without CONTRACT field 1: nothing is said, never a range made
    # up from the rows in hand.
    assert "Across" not in text(screen(passing=15))
    assert "range" not in build_result(screen(passing=15), ToolContext()).extra


@pytest.mark.parametrize(
    "change,unit,words",
    [
        # An output that cannot be negative: no below-zero clause at all.
        ({"below_zero": None}, "rate_per_year", "(AA) a year."),
        # None below zero is said, so "none" is a fact, not an omission.
        ({"below_zero": 0}, "rate_per_year", "(AA) a year; none below zero."),
        ({"below_zero": 1}, "rate_per_hour", "(AA) an hour; 1 below zero."),
        # Percent cells carry their own %; no unit tail.
        ({}, "percent", "−1.4% (MINA) to +1.0% (AA); 44 below zero."),
    ],
)
def test_the_range_line_reads_its_unit_and_its_below_zero_count(change, unit, words):
    markdown = text(with_range(screen(passing=15, unit=unit), **change))
    line = next(x for x in markdown.splitlines() if x.startswith("Across "))
    assert line.endswith(words), line


# ── Through the MCP boundary: the text block a host hands its model ─────────

SID = "str_" + "f" * 26
VIEW_SOURCE = (
    'Columns(funding_now=Column("Funding now, a year"))\n'
    'Page(default="Top 15")\n'
    'Table("Top 15", columns=[Raw("funding_now")], filter=TopN(15))'
)
REVISIONS = [
    {"revision": 3, "updated_at": "2026-11-05T13:10:00Z", "label": "Top 15", "origin": "agent"},
    {"revision": 2, "updated_at": "2026-11-05T12:40:00Z", "label": "Above 20%", "origin": "agent"},
    {"revision": 1, "updated_at": "2026-11-05T12:00:00Z", "label": None, "origin": "editor"},
]


class Screens:
    """A keel-api stand-in for the screen routes: an envelope read that
    refuses with ``refusal`` (status, body) when set, and the views routes,
    which never need a run."""

    def __init__(self, *, refusal=None, envelope=None, revision=3, persisted=True, submit=None):
        self.refusal, self.envelope = refusal, envelope or screen(passing=15)
        self.revision, self.persisted, self.submit = revision, persisted, submit
        self.calls = []

    def get(self, path, **params):
        from keel.errors import translate_http_error

        self.calls.append(("get", path, params))
        if path.endswith("/views"):
            return {"revision": self.revision, "persisted": self.persisted, "source": VIEW_SOURCE}
        if path.endswith("/views/revisions"):
            return REVISIONS if self.persisted else []
        if self.refusal is not None:
            raise translate_http_error(*self.refusal)
        if path.startswith("/v1/result-runs/") and not path.endswith("/envelope"):
            return {"run_id": "res_" + "a" * 26, "status": "COMPLETED"}
        return self.envelope

    get_public = get

    def post(self, path, json=None, headers=None, **params):
        self.calls.append(("post", path, json))
        return self.submit or {"run_id": "res_" + "a" * 26, "status": "COMPLETED"}


def served_text(tool_name, args, client, monkeypatch):
    """The text block a host hands its model for one call: the handler, the
    envelope (or the refusal's), then the adapter's mode for this tool."""
    from keel.errors import KeelError
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._mcp_adapter import registration_mode, result_for_mode

    _bootstrap()
    monkeypatch.setattr(ToolContext, "get_client", lambda self: client)
    try:
        envelope = OUTCOMES[tool_name].handler(args, ToolContext(api_client=client)).to_envelope()
    except KeelError as err:
        envelope = err.to_envelope()
    raw = json.dumps(envelope, default=str)
    result = result_for_mode(registration_mode(tool_name), raw, tool_name)
    # A legacy `-> str` tool's success is the bare string FastMCP wraps.
    return (result if isinstance(result, str) else result.content[0].text), envelope


NOT_RUN = json.dumps(
    {
        "detail": {
            "code": "SCREENER_NOT_RUN_YET",
            "detail": "This screen hasn't run yet. Run it first.",
        }
    }
)


# ── 4. keel_screener_view: present, the view source, a never-run screen ─────


def test_view_takes_present_like_run_and_backtest():
    """`present` was a usage_error on keel_screener_view while run, compose and
    backtest took it (founder test). The in-flight table's pin
    (test_widgets_inflight) holds the card's prediction to the schema.

    # SEED: drop "present" from screener_view.PROPERTIES — red here and in
    # test_the_inflight_table_matches_each_tools_real_schema."""
    from keel.tools.outcomes import OUTCOMES, _bootstrap

    _bootstrap()
    tool = OUTCOMES["keel_screener_view"]
    assert tool.input_schema["properties"]["present"]["enum"] == ["receipt", "view"]
    client = Screens()
    receipt = tool.handler(
        {"strategy_id": SID, "present": "receipt"}, ToolContext(api_client=client)
    )
    assert receipt.extra["view"]["size"] == "receipt"
    full = tool.handler({"strategy_id": SID}, ToolContext(api_client=client))
    assert full.extra["view"]["size"] == "view"


def test_include_view_source_puts_the_revision_and_source_in_the_text(monkeypatch):
    """# SEED: drop the ("view_source", …) row from `_OPERATIONAL_FIELDS` —
    the text loses the revision and the source, red."""
    text, envelope = served_text(
        "keel_screener_view",
        {"strategy_id": SID, "include_view_source": True},
        Screens(),
        monkeypatch,
    )
    assert envelope["view_source"]["revision"] == 3
    assert (
        "view source: view revision 3, 3 lines; earlier revisions "
        "2 (Nov 5 12:40 UTC, Above 20%); 1 (Nov 5 12:00 UTC)" in text
    )
    assert f"```python\n{VIEW_SOURCE}\n```" in text
    # Control: without the flag, no source rides the text.
    plain, _ = served_text("keel_screener_view", {"strategy_id": SID}, Screens(), monkeypatch)
    assert "view source" not in plain and "Columns(" not in plain


@pytest.mark.parametrize(
    "status,body,words",
    [
        # Contract 4: keel-api's code and its own sentence.
        (404, NOT_RUN, "This screen hasn't run yet. Run it first."),
        # The CodedHTTPException shape (top-level code beside a string detail).
        (
            404,
            json.dumps({"detail": "This screen hasn't run yet.", "code": "SCREENER_NOT_RUN_YET"}),
            "This screen hasn't run yet.",
        ),
        # The founder's staging body: a bare code an older keel-api answered with.
        (
            404,
            json.dumps({"detail": {"code": "RESULT_RUN_NOT_FOUND"}}),
            "This screen hasn't run yet. Run it first.",
        ),
    ],
    ids=["contract", "top-level-code", "older-keel-api"],
)
def test_a_never_run_screen_refuses_with_the_run_as_next_step(status, body, words, monkeypatch):
    """Founder test: `{"code":"not_found","message":"API error (fields: code)", …
    "For backtests, find via the strategy page"}`, next action null, on every
    new screen.

    # SEED: register keel_screener_view without `_with_refusals` — the next
    # action is null and the backtest hint is back, red. A second seed, the
    # `elif coded` arm in errors.translate_http_error removed, reds the
    # top-level-code arm only."""
    text, envelope = served_text(
        "keel_screener_view",
        {"strategy_id": SID, "include_view_source": True},
        Screens(refusal=(status, body), revision=0, persisted=False),
        monkeypatch,
    )
    assert text.startswith(words + "\n"), text[:120]
    assert envelope["code"] == "SCREENER_NOT_RUN_YET"
    assert envelope["suggested_next_action"]["tool"] == "keel_screener_run"
    assert envelope["suggested_next_action"]["args"] == {"strategy_id": SID}
    assert "backtest" not in text.lower() and "API error" not in text
    # The view still comes back to edit, revision 0 before anything is saved.
    assert "view source: view revision 0 (Keel's default view; nothing saved yet), 3 lines" in text
    assert text.count("Columns(funding_now") == 1  # fenced once, not again as JSON


def test_a_screen_that_is_not_there_names_the_list_never_backtests(monkeypatch):
    body = json.dumps({"detail": {"code": "SCREENER_NOT_FOUND", "detail": "Not a screener"}})
    text, envelope = served_text(
        "keel_screener_view", {"strategy_id": SID}, Screens(refusal=(404, body)), monkeypatch
    )
    assert envelope["suggested_next_action"]["tool"] == "keel_screener_list"
    assert "backtest" not in text.lower()
    # Control: a non-screen tool keeps keel-api's generic advice.
    from keel.errors import translate_http_error

    assert "For backtests" in translate_http_error(404, body).suggestion


# ── 6. keel_screener_list: compact lines, never thumbnails ──────────────────


CURATED = {
    "items": [
        {
            "share_id": "shr_movers",
            "slug": "top-movers",
            "url": "https://usekeel.io/screens/top-movers",
            "title": "Top movers",
            "sentence": "The coins that moved most over the last day.",
            "headline": {"passing": 10, "of": 179},
            "clock_words": "Hourly bars",
            "card_url": "https://usekeel.io/og/top-movers.png",
            "thumb": {"assets": COINS[:12], "values": [[0.1] * 60 for _ in range(12)]},
            "fork_count": 3,
        }
    ],
    "total_hint": 1,
    "description": "Keel's screens rank Hyperliquid perps.",
}


@pytest.mark.parametrize("text_only", ["1", "0"])
def test_the_curated_list_text_is_its_lines_and_carries_no_thumbnail(text_only, monkeypatch):
    """Founder test: `keel_screener_list(scope="keel")` text carried every
    curated screen's thumbnail series (12 coins x 60 values x 5 screens).

    # SEED: keep the rows whole (`_without_thumbs` returns `page`) — both
    # arms red; under the text arm, returning the JSON (no `_screen_markdown`)
    # reds too."""
    monkeypatch.setenv("KEEL_NONVIEW_TEXT_ONLY", text_only)

    class Curated(Screens):
        def get(self, path, **params):
            return (
                copy.deepcopy(CURATED)
                if path == "/public/screeners"
                else super().get(path, **params)
            )

        get_public = get

    text, envelope = served_text("keel_screener_list", {"scope": "keel"}, Curated(), monkeypatch)
    assert "thumb" not in json.dumps(envelope) and "0.1, 0.1" not in text
    assert envelope["keel"]["items"][0]["share_id"] == "shr_movers"  # the row stays
    if text_only == "1":
        assert text == (
            "Keel's screens:\nTop movers · The coins that moved most over the last day. · "
            "Hourly bars · 10 of 179 pass · https://usekeel.io/screens/top-movers · shr_movers"
        )


# ── 7. keel_screener_run: the quota a run spends ────────────────────────────


def test_a_screen_run_says_the_backtest_runs_left(monkeypatch):
    """A screen run spends a backtest run; its submit now carries the same
    `remaining` / `quota` (contract 5), rendered as keel_backtest_run's line.

    # SEED: drop `result.extra.update(quota)` in screener_run — red; the
    # control (a submit with no quota) never prints a quota line."""
    submit = {
        "run_id": "res_" + "a" * 26,
        "status": "COMPLETED",
        "remaining": {"backtest_runs": 3},
        "quota": [
            {
                "unit": "backtest_runs",
                "limit": 50,
                "used": 47,
                "remaining": 3,
                "resets_at": "2026-11-09T00:00:00Z",
                "tier": "critical",
                "period": "week",
                "plan": "starter",
            }
        ],
    }
    text, envelope = served_text(
        "keel_screener_run", {"strategy_id": SID}, Screens(submit=submit), monkeypatch
    )
    assert "quota: 3 of 50 backtests left; resets Nov 9." in text
    assert envelope["remaining"] == {"backtest_runs": 3}
    assert "plan" not in envelope["quota"][0]  # the projection, never the plan ladder
    plain, plain_env = served_text(
        "keel_screener_run", {"strategy_id": SID}, Screens(), monkeypatch
    )
    assert "quota" not in plain and "quota" not in plain_env


# ── 5. keel_screener_view_set: the revision saved, then the screen ──────────


class Views(Screens):
    """The views PUT: a save answers the next revision, a dry run the one sent."""

    def put(self, path, json=None, **params):
        self.calls.append(("put", path, json))
        dry = (json or {}).get("dry_run")
        return {
            "revision": json.get("expected_revision") if dry else 4,
            "check": {"valid": True, "issues": [], "resolved": {}},
            "units": [
                {
                    "output": "funding_now",
                    "unit": "rate_per_year",
                    "sentence": "funding_now is a rate per year",
                }
            ],
        }


SAVE = {"strategy_id": SID, "view": VIEW_SOURCE, "expected_revision": 3, "label": "Top 15"}


def served(tool_name, args, client, monkeypatch):
    """The whole MCP result a host receives (text, structuredContent, _meta)
    for one call, beside the handler's envelope."""
    from keel.tools.outcomes._mcp_adapter import registration_mode, result_for_mode

    _text, envelope = served_text(tool_name, args, client, monkeypatch)
    raw = json.dumps(envelope, default=str)
    return result_for_mode(registration_mode(tool_name), raw, tool_name), envelope


def card_shown(markdown):
    from keel.tools.outcomes._mcp_adapter import CARD_SHOWN_LINE

    return CARD_SHOWN_LINE + "\n\n" + markdown


@pytest.mark.parametrize("text_only", ["1", "0"])
def test_a_saved_view_names_its_revision_and_shows_the_screen(text_only, monkeypatch):
    """Founder: "shouldn't view also show screen in chat?" — and Claude: "no
    view revision after edit". The text says the revision saved (the next
    expected_revision), then the screen re-read with the new view.

    # SEED: return the PUT response alone from screener_view_set._handler
    # (the pre-Q-3634 body) — no "Saved view revision" and no rows, red."""
    monkeypatch.setenv("KEEL_NONVIEW_TEXT_ONLY", text_only)
    client = Views(envelope=screen(passing=15, view_revision=4))
    text, envelope = served_text("keel_screener_view_set", SAVE, client, monkeypatch)
    markdown = envelope["view"]["markdown"]
    assert markdown.startswith(
        "Saved view revision 4: Top 15.\n\n**Top funding** · screen v1 · view rev 4"
    )
    assert len(table_rows(markdown)) == 15
    assert envelope["revision"] == 4 and envelope["units"]  # the route's fields stay
    assert [c[:2] for c in client.calls] == [
        ("put", f"/v1/screeners/{SID}/views"),
        ("get", f"/v1/screeners/{SID}/envelope"),
    ]
    # One text block on every arm: the card preface, then the lines.
    assert text == card_shown(markdown)


@pytest.mark.parametrize("meta_move", ["1", "0"])
def test_a_saved_view_is_the_screen_card_keel_screener_view_returns(meta_move, monkeypatch):
    """Q-3639: after an edit the user saw text only. A save now answers with
    the screen `keel_screener_view` returns — the same card, the same data —
    with the saved revision leading the text and the route's three fields
    beside it.

    # SEED: drop "keel_screener_view_set" from `CARD_TOOLS` — no `_meta.ui`
    # on the result, red; returning `_receipt(...)` on a save (a one-row
    # card) reds the equality with keel_screener_view's envelope."""
    monkeypatch.setenv("KEEL_NONVIEW_TEXT_ONLY", "1")
    monkeypatch.setenv("KEEL_CARD_META_MOVE", meta_move)
    env = with_range(screen(passing=15, view_revision=4))
    saved, saved_env = served("keel_screener_view_set", SAVE, Views(envelope=env), monkeypatch)
    read, read_env = served(
        "keel_screener_view", {"strategy_id": SID}, Views(envelope=env), monkeypatch
    )
    # Not vacuous: the read draws a full screen card, rows and series.
    assert read_env["view"]["size"] == "view" and read_env["hero"] and read_env["manifest"]
    resource = "ui://keel/cards/screener.html"
    assert saved.meta["ui"]["resourceUri"] == read.meta["ui"]["resourceUri"] == resource
    assert saved.meta.get("keel/card") == read.meta.get("keel/card")
    lead = "Saved view revision 4: Top 15.\n\n"
    assert saved_env["view"]["markdown"] == lead + read_env["view"]["markdown"]
    # The card's words for the edit and the revision saved; the rest is
    # keel_screener_view's envelope, field for field.
    assert saved_env["run"] == {**read_env["run"], "status_words": "View saved."}
    assert saved_env["screener"] == {**read_env["screener"], "view_revision": 4}
    mine = ("view", "run", "screener", "revision", "check", "units")
    same = {k: v for k, v in saved_env.items() if k not in mine}
    assert same == {k: v for k, v in read_env.items() if k not in mine}
    assert {k: v for k, v in saved_env["view"].items() if k != "markdown"} == {
        k: v for k, v in read_env["view"].items() if k != "markdown"
    }
    # What the model reads: the saved line, the screen, and the range below
    # its rows.
    text = saved.content[0].text
    assert text == card_shown(saved_env["view"]["markdown"])
    assert RANGE_LINE in text


def test_a_dry_run_checks_and_previews_without_a_revision(monkeypatch):
    """A dry run stays a text receipt: the check, the units, the preview on
    one line; its card is one row that says it checked and saved nothing."""
    monkeypatch.setenv("KEEL_NONVIEW_TEXT_ONLY", "1")
    client = Views(envelope=screen(passing=15, view_revision=3))
    result, envelope = served(
        "keel_screener_view_set", {**SAVE, "dry_run": True}, client, monkeypatch
    )
    lines = result.content[0].text.split("\n\n", 1)[1].splitlines()
    assert lines[:2] == [
        "Checked, not saved: the view is valid.",
        "units: funding_now is a rate per year",
    ]
    preview = next(line for line in lines if line.startswith("Screen · "))
    assert "15 of 179 coins pass now" in preview and "view rev" not in preview
    # The preview read sends the unsaved source; nothing was written.
    read = [c for c in client.calls if c[0] == "get"][-1]
    assert read[2]["view"] == VIEW_SOURCE and client.calls[0][2]["dry_run"] is True
    # The card's one row: the screen's name, and the check as its words.
    assert envelope["view"]["size"] == "receipt" and envelope["view"]["name"] == "Top funding"
    assert envelope["run"]["status_words"] == "Checked, not saved: the view is valid."
    assert "view_revision" not in envelope["screener"] and "manifest" not in envelope
    assert envelope["revision"] == 3 and envelope["check"]["valid"] is True
    assert result.meta["ui"]["resourceUri"] == "ui://keel/cards/screener.html"


def test_a_saved_view_on_a_never_run_screen_names_the_run(monkeypatch):
    monkeypatch.setenv("KEEL_NONVIEW_TEXT_ONLY", "1")

    class NeverRun(Views):
        def get(self, path, **params):
            if path == f"/v1/strategies/{SID}":
                self.calls.append(("get", path, params))
                return {"name": "Top funding", "current_sequence": 1}
            return super().get(path, **params)

    text, envelope = served_text(
        "keel_screener_view_set", SAVE, NeverRun(refusal=(404, NOT_RUN)), monkeypatch
    )
    assert text == card_shown(
        "Saved view revision 4: Top 15.\n\n"
        "The screen hasn't run yet, so there are no rows to show.\n"
        f'next: keel_screener_run(strategy_id="{SID}")'
    )
    assert "code" not in envelope  # the save succeeded; only the read had nothing
    # The card: one row under the screen's own name, saying the same thing.
    assert envelope["view"]["size"] == "receipt"
    assert (envelope["view"]["name"], envelope["view"]["version"]) == ("Top funding", 1)
    assert envelope["run"]["status_words"] == (
        "Saved view revision 4: Top 15. The screen hasn't run yet, so there are no rows to show."
    )
    assert envelope["screener"]["view_revision"] == 4
    # A name read that fails costs the name, never the answer.
    _text, bare = served_text(
        "keel_screener_view_set", SAVE, Views(refusal=(404, NOT_RUN)), monkeypatch
    )
    assert "name" not in bare["view"] and bare["run"]["status_words"].startswith("Saved")


def test_a_result_with_no_run_passes_the_card_its_words_and_revision():
    """Lane 2's card draws a no-result receipt from `run.status_words`,
    `copy.card` and `screener.view_revision`; the outputs and labels are
    never capped at three (a fourth column's label reached the card as its
    raw slot name)."""
    pending = {
        "screener": {"strategy_id": SID, "name": "Top funding", "version": 1, "view_revision": 4},
        "run": {"run_id": "res_" + "a" * 26, "status": "QUEUED", "status_words": "Queued"},
        "copy": {"card": {"pass_now": "{passing} of {of} pass now"}},
        "completed": None,
    }
    extra = build_result(pending, ToolContext(), size="receipt").extra
    assert extra["copy"]["card"]["pass_now"] and extra["screener"]["view_revision"] == 4
    assert extra["view"]["markdown"].startswith("Screen · Top funding v1 · view rev 4 · Queued")
    env = screen()
    names = ["funding_now", "funding_7d", "oi_change", "volume_rank"]
    env["completed"]["manifest"]["outputs"] = [{"name": n} for n in names]
    env["completed"]["labels"] = [{"label": f"Label {n}", "unit": "score"} for n in names]
    extra = build_result(env, ToolContext()).extra
    assert [o["name"] for o in extra["manifest"]["outputs"]] == names
    assert [item["label"] for item in extra["labels"]] == [f"Label {n}" for n in names]
