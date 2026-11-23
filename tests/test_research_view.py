"""The research text and envelope builder (spec 07 §3.2, §3.3, §3.5; AT-4, AT-5, AT-7, AT-14).

Fixtures use shipped unit keys (``score``, ``rank``, ``count``) until ENG's G14
adds the five research units to the one formatter.

SEEDS (each reverted by reversing the edit):
- ``LIST_ROWS_MAX = 25`` → ``30`` reds ``test_lists_name_their_population``;
- deleting ``out["checks"] = {…}`` in ``structured_study`` reds
  ``test_structured_content_is_bounded_at_the_largest_shape`` (AT-5).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import jsonschema
import pytest
from keel.tools.outcomes import _research_view as rv
from keel.tools.outcomes._output_schemas import STUDY_SCHEMA


KIND = {"key": "check", "name": "Check a strategy", "short": "Check"}


def _bar(i: int, met: bool | None = True) -> dict:
    return {
        "label": f"Sharpe at {i + 2} times the costs (daily returns)",
        "ref": "costs.summary",
        "measure": "sharpe",
        "at": None,
        "value": 1.9 - i / 100,
        "unit": "score",
        "interval": {"lo": 1.1, "hi": 2.4, "level": 0.9, "method": "hac"},
        "sample": {"days": 584, "coins": 100},
        "sample_words": "584 days · 100 coins",  # served (research.words.sample_words), never rebuilt
        "op": ">=",
        "bar": 0.3,
        "met": met,
    }


def _study(*, bars: int = 9, steps: int = 13, views: int = 10, thumb_bytes: int = 0) -> dict:
    return {
        "study": {
            "study_id": "stu_abc",
            "version": 1,
            "revision": 1,
            "title": "Momentum rotation v3",
            "kind_label": KIND,
            "state": "completed",
            "run_set_id": "rus_abc",
        },
        "footing": {
            "days": {"start": "2025-03-23", "end": "2026-10-29", "start_moved_from": None},
            "split": "2026-04-17",
            "coins": {
                "count": 100,
                "chosen": "Top 100 by 30-day volume, picked Mar 23, 2025, kept fixed",
                "names_sha256": "n",
            },
            "costs": {"fee": 0.00045, "slippage": 0.00045},
            "delay": 0,
            "leverage": 1.0,
            "vol_cap": None,
            "data": "snap_3f9a1c07b2e4d815",
            "engine": "e",
        },
        "counts": {
            "allowance": None,
            "runs": {
                "planned": 24,
                "executed": 22,
                "reused": 2,
                "failed_platform": 0,
                "failed_content": 0,
                "words": "22 runs, 2 reused, none failed",
            },
            "versions_tried": {
                "family": "fam:str_momrot00000000000000000000",
                "family_name": "momentum rotation",
                "count": 388,
                "added": 0,
                "why": "checks",
            },
        },
        "stale": [],
        "links": {"app": "https://app.usekeel.io/research/stu_abc?r=1.1", "share": None},
        "view": {
            "kind": "study",
            "size": "view",
            "markdown": "",
            "object": "stu_abc",
            "at": "t",
            "seq": 1,
        },
        "headline": {
            "sentence": "Sharpe 1.95 over 584 days (daily returns); 2.62 before the split, 0.61 after.",
            "measures": [],
        },
        "checks": {"count": bars, "met": bars - 1, "rows": [_bar(i, i != 3) for i in range(bars)]},
        "steps": [
            {
                "id": f"s{i}",
                "step": f"Step{i % 9}",
                "version": 1,
                "label": f"Step {i}",
                "category": "c",
                "status": "completed",
            }
            for i in range(steps)
        ],
        "views": [
            {"index": i, "title": f"Chart {i}", "family": "Lines", "of": "base", "points": 6}
            for i in range(views)
        ],
        "not_measured": ["coins as they were each day (the coins are fixed for the whole window)"],
        "notes": [],
        "notes_total": 0,
        "card_summary": {
            "schema": "keel.research.card/1",
            "kind_label": KIND,
            "maker": "you",
            "state": "completed",
            "state_words": "Done · 29 Oct",
            "title": "Momentum rotation v3",
            "headline": {"text": "Sharpe 1.95 over 584 days (daily returns)", "chips": []},
            "thumb": {"family": "Lines", "view_index": 1, "facts": {"q": "x" * thumb_bytes}},
            "top5": {"kind": "checks", "rows": [], "total": bars},
            "numbers": [],
            "footing_line": "584 days · 100 coins · 9 bp a fill",
            "footer": {"subject": "Momentum rotation v3", "runs_words": "24 runs"},
        },
    }


def test_the_footing_costs_are_fee_plus_slippage_a_fill() -> None:
    """RI-507: the text's costs are the card's (research.card_summary.cost_bp_a_fill): fee PLUS slippage a fill.
    Seeded red (reverted): reading the fee alone gives "costs 2 bp a fill" here, and 4.5 on the default study."""
    footing = {**_study()["footing"], "costs": {"fee": 0.0002, "slippage": 0.0001}}
    assert " · costs 3 bp a fill · " in rv.footing_words(footing)
    assert " · no costs · " in rv.footing_words({**footing, "costs": "none"})  # control


def test_the_text_follows_07s_rules() -> None:
    text = rv.study_text(_study())
    lines = text.splitlines()
    assert lines[0] == "Check a strategy · Momentum rotation v3 · version 1 · Done · 29 Oct"
    assert lines[1].startswith("Sharpe 1.95 over 584 days")
    assert lines[2] == (
        "Days Mar 23, 2025 to Oct 29, 2026, split Apr 17, 2026 · 100 coins: top 100 by 30-day volume,"
        " picked Mar 23, 2025, kept fixed · costs 9 bp a fill · data as of Oct 29, 2026"
    )
    # JOURNEYS S3: the step versions are a details line after the numbers, never the line under the footing
    # D-401: the steps by their labels, never "Step0 v1"
    method = next(i for i, line in enumerate(lines) if line.startswith("Measured with step 0, step 1"))
    assert method > lines.index("Your checks (9; 8 met):") and not lines[3].startswith(
        "Measured with"
    )
    assert "Your checks (9; 8 met):" in lines
    bar_lines = [line for line in lines if line.startswith("- ")]
    assert len(bar_lines) == 9
    assert bar_lines[0] == (
        "- Sharpe at 2 times the costs (daily returns): 1.9, interval 1.1 to 2.4; 584 days · 100 coins;"
        " your bar >= 0.3; met"
    )
    assert bar_lines[3].endswith("; not met")
    assert "Not measured: coins as they were each day" in text
    assert (
        "Runs: 22 runs, 2 reused, none failed · versions tried for momentum rotation: 388" in text
    )
    assert lines[-1] == "View in Keel: https://app.usekeel.io/research/stu_abc?r=1.1"


def test_lists_name_their_population() -> None:
    text = rv.study_text(_study(bars=40))
    assert len([line for line in text.splitlines() if line.startswith("- ")]) == 25
    assert "Your checks (40; 39 met):" in text
    # AUDIT-WORDS M23/M63: no em dash; "checks" for the list
    assert 'and 15 more checks, 15 met: keel_research_get(study_id="stu_abc", output="bars")' in text


def _ran_without_repicking(state: str) -> dict:
    """A Check read whose version ran without re-picking (D-198, D-201): seven bars apply, and the two bars over the
    re-picking steps keep their rows with no value (met null). ``state`` is those steps' served run state for the
    reader: ``locked`` (a Free or Trader reader) or ``needs_run`` (a Pro reader of the same version, RI-223)."""
    env = _study(bars=7)
    words = "Re-picking the settings as you go: not included in this account's plan."
    for sid, label in (
        ("repick", "Sharpe re-picking the settings every 30 days (daily returns)"),
        ("nearby", "Nearby settings as a share of yours (ratio)"),
    ):
        env["checks"]["rows"].append(
            {**_bar(0), "label": label, "ref": f"{sid}.summary", "value": None, "met": None}
        )
        env["steps"].append(
            {
                "id": sid,
                "step": "RepickSettings",
                "version": 1,
                "label": "Re-picking the settings as you go",
                "category": "c",
                "status": "completed",
                "run_state": {"state": state, "runnable": state != "locked", "words": words},
            }
        )
    return env


def test_a_bar_over_a_locked_step_reads_not_included_with_no_number() -> None:
    """RI-243 (07 §4.4, D-198, D-201): on a reader whose plan lacks re-picking, a bar over the step the version ran
    without reads "{bar}: not included in this account's plan", with no number, never "not measured". SEED
    (reversed): dropping the locked branch in ``_bars_lines`` reds this with "…: not measured, no interval; … not
    measured"."""
    text = rv.study_text(_ran_without_repicking("locked"))
    assert "Your checks (7; 6 met):" in text  # the served count: the two locked rows are out of it
    locked = [
        line for line in text.splitlines() if line.endswith(": not included in this account's plan")
    ]
    assert locked == [
        "- Sharpe re-picking the settings every 30 days (daily returns): not included in this account's plan",
        "- Nearby settings as a share of yours (ratio): not included in this account's plan",
    ]  # non-vacuity: both locked rows, and only them
    assert "not measured" not in text
    assert len([line for line in text.splitlines() if line.startswith("- ")]) == 9
    # Control: the same version read by a reader whose plan runs re-picking (needs_run) is not locked for them.
    pro = rv.study_text(_ran_without_repicking("needs_run"))
    assert "not included in this account's plan" not in pro
    assert pro.count(": not measured") == 2


def _shared(env: dict, *, locked: bool) -> dict:
    """``env`` as a share read serves it (RI-249): no steps (so no run state), each bar row carrying ``locked``,
    the snapshot's frozen flag (true on a bar over a step the shared version ran without)."""
    out = {**env, "steps": [], "checks": {**env["checks"], "rows": []}}
    for row in env["checks"]["rows"]:
        over = str(row["ref"]).split(".", 1)[0] in ("repick", "nearby")
        out["checks"]["rows"].append({**row, "locked": locked and over})
    return out


def test_ri267_a_shared_reads_locked_bar_reads_not_included_from_its_row() -> None:
    """RI-267 (07 §4.4, D-201): a share read serves no steps, so no run state; its rows carry ``locked`` (RI-249).
    A shared Free Check's re-picking bars read "not included in this account's plan" from the row, as the study read
    does, never "not measured". SEED (reversed): ``_locked_steps`` reading run states only (the row loop dropped)
    reds this with the two rows "…: not measured, no interval; …"."""
    text = rv.study_text(_shared(_ran_without_repicking("locked"), locked=True))
    locked = [
        line for line in text.splitlines() if line.endswith(": not included in this account's plan")
    ]
    assert locked == [
        "- Sharpe re-picking the settings every 30 days (daily returns): not included in this account's plan",
        "- Nearby settings as a share of yours (ratio): not included in this account's plan",
    ]  # non-vacuity: both locked rows, and only them
    assert "not measured" not in text
    # Control: the same share whose rows are not locked (a version that ran re-picking, measured nothing) reads them
    # as not measured, so the verdict follows the row's flag.
    unlocked = rv.study_text(_shared(_ran_without_repicking("locked"), locked=False))
    assert "not included in this account's plan" not in unlocked
    assert unlocked.count(": not measured") == 2


def test_ri267_a_served_run_state_wins_over_the_rows_locked() -> None:
    """RI-223 against RI-267: a Pro reader of a version that ran without re-picking is served ``needs_run``; the
    row's ``locked`` (the version's fact) must not lock it for them. SEED (reversed): the row loop without its
    ``step not in stated`` guard reds this ("not included" on a Pro read)."""
    env = _ran_without_repicking("needs_run")
    for row in env["checks"]["rows"]:
        row["locked"] = str(row["ref"]).split(".", 1)[0] in ("repick", "nearby")
    assert (
        sum(1 for r in env["checks"]["rows"] if r["locked"]) == 2
    )  # non-vacuity: two rows say locked
    text = rv.study_text(env)
    assert "not included in this account's plan" not in text
    assert text.count(": not measured") == 2


def test_a_platform_failure_is_never_a_failed_check() -> None:
    env = _study()
    env["counts"]["runs"]["failed_platform"] = 2
    env["counts"]["runs"]["words"] = "22 runs, 2 reused, 2 didn't finish on our side"
    text = rv.study_text(env)
    assert "Runs: 22 runs, 2 reused, 2 didn't finish on our side" in text
    assert "none failed" not in text


def test_the_runs_line_prints_the_served_count_words() -> None:
    """D-146 e (FIX-7's J5: "Runs: 9 run, 0 reused"): the counts' words are
    research.words' (served as ``counts.runs.words``), never the SDK's; a
    payload without them writes no runs line rather than its own wording. SEED
    (reversed): the pre-fix ``f"{executed:,} run"`` builder reds the first
    assertion with "Runs: 9 run, 0 reused"."""
    env = _study()
    env["counts"]["runs"].update(executed=9, reused=0, words="9 runs, 0 reused, none failed")
    assert "Runs: 9 runs, 0 reused, none failed" in rv.study_text(env)
    env["counts"]["runs"].pop("words")
    assert "Runs:" not in rv.study_text(env)


def test_the_text_holds_8000_characters_at_the_largest_shape() -> None:
    text = rv.study_text(_study(bars=100, steps=40, views=24))
    assert 1_000 < len(text) <= rv.TEXT_MAX_CHARS
    assert text.splitlines()[-1].startswith("View in Keel: ")


def test_structured_content_is_bounded_at_the_largest_shape() -> None:
    """AT-5's shape: 100 bars, 40 steps, 24 views, a 2,300-byte thumb."""
    out = rv.structured_study(_study(bars=100, steps=40, views=24, thumb_bytes=2_200))
    assert len(out["checks"]["rows"]) == 5 and out["checks"]["count"] == 100
    assert out["steps"] == {"total": 40, "by_status": {"completed": 40}}
    assert out["views"] == {"total": 24}
    assert "headline" not in out and "markdown" not in out["view"]
    jsonschema.validate(out, STUDY_SCHEMA)
    size = len(json.dumps(out, separators=(",", ":")).encode())
    assert 3_000 < size <= 8_000, size


def test_a_card_names_the_stale_offer_without_its_document() -> None:
    """RL-58 (FIX-7's J1.6): a v3 Check with v4 out serves a ``newer_subject``
    fact whose offer's plan holds the whole re-pinned document (3,459 bytes on
    the stack); the card carries the offer (the newer ref, the study) and not the
    document, so the largest shape stays within 8,000 bytes (5,332 here).
    SEED (reversed): deleting ``out["stale"] = [...]`` in ``structured_study``
    reds the offer assertion; the card it served was 8,807 bytes."""
    env = _study(bars=100, steps=40, views=24, thumb_bytes=2_200)
    source = 'Study(title="Momentum rotation v3")\n' + "#" * 3_422 + "\n"
    env["stale"] = [
        {
            "kind": "newer_subject",
            "words": "Momentum rotation v3 has a newer version, v4.",
            "offer": {
                "ref": "str_momrot@4",
                "plan": {"study_id": "stu_abc", "source": source},
            },
        }
    ]
    assert len(source.encode()) == 3_459  # non-vacuity: the stack's document size
    out = rv.structured_study(env)
    (fact,) = out["stale"]
    assert fact["kind"] == "newer_subject" and fact["words"].endswith("v4.")
    assert fact["offer"] == {"ref": "str_momrot@4", "plan": {"study_id": "stu_abc"}}
    size = len(json.dumps(out, separators=(",", ":")).encode())
    assert size <= 8_000, size
    # The read keeps the document: the envelope the CLI and API serve is not reduced.
    assert env["stale"][0]["offer"]["plan"]["source"] == source
    jsonschema.validate(out, STUDY_SCHEMA)


def test_an_unknown_unit_raises() -> None:
    env = _study()
    env["checks"]["rows"][0]["unit"] = "furlongs"
    with pytest.raises(ValueError):
        rv.study_text(env)


def test_next_calls_follow_the_served_registry(monkeypatch) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    assert (
        rv.next_call("keel_research_view_set", {"study_id": "stu_a"})
        == 'keel_research_view_set(study_id="stu_a")'
    )
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    out = rv.next_call(
        "keel_research_view_set",
        {"study_id": "stu_a"},
        app_url="https://app.usekeel.io/research/stu_a",
    )
    assert out == 'keel_app_link(id="stu_a")'  # RI-253: the tool takes an id, never a url


_STUDY = "stu_01HZXK3Y6N3V7Q9S2T4W8R5M1E"


def _dispatch(call: str) -> dict:
    """A rendered call, parsed as Python and run through the tool's real MCP dispatch (``_make_handler``)."""
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._mcp_adapter import _make_handler

    _bootstrap()
    node = ast.parse(call, mode="eval").body
    assert isinstance(node, ast.Call) and not node.args, call
    kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords}
    return json.loads(_make_handler(OUTCOMES[node.func.id], frozenset())(**kwargs))


def test_ri253_a_listed_next_call_runs_through_keel_app_link(monkeypatch) -> None:
    """RI-253 (07 §3.4, AT-14): on the listed profile an unserved research call becomes ``keel_app_link`` to the
    study's page. It rendered ``keel_app_link(url=…)``, an argument the tool does not take, so the call it told the
    model to make was refused. The rendered call, run through the tool's real dispatch, links the study's page; a call
    that names no study points to the answer's "View in Keel" line instead of a call that cannot succeed.
    Seeded red (reversed): the old ``url=`` rendering -> the dispatch refuses it (1 failed); control: the full
    profile renders the research call itself. Non-vacuity: the dispatch ran the handler (its hero_url is the page)."""
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("KEEL_APP_URL", raising=False)
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    app = f"https://app.usekeel.io/research/{_STUDY}"
    call = rv.next_call("keel_research_view_set", {"study_id": _STUDY}, app_url=app)
    from keel.tools.outcomes._toolsets import research_served_context

    # the caller is served Research (a listed tool it does not list routes to the page); unserved, app_link refuses a
    # study (test_outcomes_open_in_app's flag-off arms)
    with research_served_context(True):
        env = _dispatch(call)
    assert "error" not in env and env.get("code") is None, env
    assert env["hero_url"] == app
    assert rv.next_call("keel_research_brief", {"cursor": "c1"}, app_url=app) == rv.VIEW_IN_KEEL
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    assert rv.next_call("keel_research_view_set", {"study_id": _STUDY}).startswith(
        "keel_research_view_set("
    )


def test_a_plan_text() -> None:
    plan = {
        "study": {"title": "Momentum rotation v3", "kind_label": KIND, "state": "planned"},
        "writes": "a new study, version 1",
        "words": {
            "work": "24 runs, 2 reused · under a minute · versions tried +0",
            "allowance": "Counts as 1 study.",
        },
        "footing": _study()["footing"],
        "steps": [
            {"label": "Costs from none to six times", "to_run": 5, "runnable": True},
            {"label": "Backtest", "to_run": 0, "runnable": True},
            {
                "label": "Re-picking the settings every 30 days",
                "to_run": 1,
                "runnable": False,
                "locked": {
                    "unit": "feature:research_repick",
                    "words": "Re-picking settings: not included in this account's plan.",
                },
            },
        ],
        "plan_id": "pln_" + "a" * 64,
        "run_allowed": False,
        "links": {"app": "https://app.usekeel.io/research/new?template=check&subject=str_momrot@3"},
    }
    text = rv.plan_text(plan)
    assert (
        text.splitlines()[0]
        == "Plan · Check a strategy · Momentum rotation v3 · a new study, version 1"
    )
    assert (
        # SERVER-2: a locked step reads its served words (steps[].locked.words)
        "Steps (3): Costs from none to six times: 5 runs · Backtest: reused · Re-picking settings: not included in this account's plan"
        in text
    )
    assert "Re-picking settings: not included in this account's plan." in text
    assert "Run it" not in text
    assert 'keel_research_run(plan_id="pln_' in rv.plan_text({**plan, "run_allowed": True})


def test_a_served_wall_replaces_the_run_line_once() -> None:
    """07 §3.4: the served ``words.limit`` (the neutral wall, which already carries each locked step's words) is
    written once; the steps' own locked words are not repeated beside it."""
    lock = "Re-picking settings: not included in this account's plan."
    wall = lock + " Pro includes it. Plans are changed in the Keel web app."
    plan = {
        "study": {"title": "Momentum rotation v3", "kind_label": KIND, "state": "planned"},
        "writes": "a new study, version 1",
        "words": {
            "work": "22 runs",
            "allowance": "2 of 3 studies left this month; resets Dec 3.",
            "limit": wall,
        },
        "steps": [
            {
                "label": "Re-picking",
                "to_run": 1,
                "runnable": False,
                "locked": {"unit": "u", "words": lock},
            }
        ],
        "plan_id": "pln_" + "a" * 64,
        "run_allowed": False,
        "links": {"app": "https://app.usekeel.io/research/new"},
    }
    text = rv.plan_text(plan)
    assert text.count(lock) == 1 and wall in text  # non-vacuity: the lock's words are there, once
    assert "2 of 3 studies left this month; resets Dec 3." in text
    assert "Run it" not in text


def test_a_data_page() -> None:
    page = {
        "data": [
            {"lookback": 10, "hold": 1, "sharpe": 1.2},
            {"lookback": 20, "hold": 1, "sharpe": 0.4},
        ],
        "pagination": {"cursor": "c2", "has_more": True},
        "facts": {
            "rows_total": 212,
            "cells": {"computed": 212, "not_run": 172},
            "ranges": {
                "sharpe": {"low": {"value": -0.41}, "high": {"value": 2.31}, "below_zero": 18}
            },
        },
    }
    text = rv.data_text(
        page,
        title="Lookback × hold · Sharpe",
        version=1,
        call_args={"study_id": "stu_a", "view": 0},
    )
    assert "Rows 1–2 of 212 (212 measured, 172 not run)" in text
    assert "| 10 | 1 | 1.2 |" in text
    assert "Across all 212 rows: Sharpe −0.41 to 2.31; 18 below zero" in text
    assert text.endswith('More: keel_research_get(study_id="stu_a", view=0, cursor="c2")')


def test_no_verdict_words_in_the_builders_copy() -> None:
    """AT-7's scan of research-owned copy: this module's string literals."""
    tree = ast.parse(Path(rv.__file__).read_text())
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(getattr(n.body[0], "value", None), ast.Constant)
    }
    literals = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]
    literals = [s for s in literals if s not in rv.VERDICT_WORDS]
    assert len(literals) > 30  # not vacuous
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in rv.VERDICT_WORDS) + r")\b", re.I)
    hits = [s for s in literals if pattern.search(s)]
    assert not hits, hits
    text = rv.study_text(_study(bars=40))
    assert not pattern.search(text)


def test_the_served_count_leads_and_a_what_if_step_says_so() -> None:
    """A run answer's served ``words.allowance`` leads (after rule 1's first line), as on a plan; J3.2: a step served
    ``what_if`` (a strategy blend) writes "{label}: {what_if}". Both are the payload's words, never composed."""
    env = _study()
    allowance = "398 of 400 studies left this month; resets Dec 3. Planning, reading and saving do not count."
    what_if = "A what-if on the stored results: nothing is traded."
    env["words"] = {"allowance": allowance}
    env["steps"].append(
        {"id": "blend", "step": "Blend", "version": 1, "label": "Side by side", "category": "Measure",
         "status": "completed", "what_if": what_if}
    )  # fmt: skip
    lines = rv.study_text(env).splitlines()
    assert lines[1] == allowance
    assert f"Side by side: {what_if}" in lines
    plain = rv.study_text(_study()).splitlines()
    assert plain[1].startswith(
        "Sharpe 1.95"
    )  # control: no served count, the headline follows the first line
    assert not any("what-if" in line for line in plain)


def test_a_content_outcome_gives_its_served_reason() -> None:
    """07 §3.2 rule 6 / D-130: a step served ``outcome {kind: content, reason}`` writes "{label}: {reason}" word for
    word (J1.2's RandomBooks refusal). Control: a step with no outcome writes no such line. Non-vacuity: one of
    the fixture's steps carries the outcome."""
    reason = "RandomBooks needs at least 2 coins to shuffle; this study has 1."
    env = _study()
    assert env["steps"], "the fixture has steps"
    env["steps"][0] = {
        **env["steps"][0],
        "status": "failed",
        "outcome": {"kind": "content", "reason": reason},
    }
    label = env["steps"][0]["label"]
    assert f"{label}: {reason}" in rv.study_text(env).splitlines()
    assert not any(reason in line for line in rv.study_text(_study()).splitlines())


def test_a_study_with_no_bars_writes_its_headline_measures() -> None:
    """J2.3 (07 §3.2 rule 3): a signal study serves no bars, so its served headline measures are the measurement
    lines: label, value, interval, sample. Control: a study with bars writes none (its bar lines carry them).
    Non-vacuity: 2 measures served, 2 lines written."""
    env = _study()
    env["checks"] = None
    env["study"]["kind_label"] = {"key": "signal", "name": "Study a signal", "short": "Signal"}
    env["headline"] = {
        "sentence": "How well it ranks, next day: 0.085 (t 8.3)",
        "measures": [
            {
                "ref": "ic", "measure": "ic", "label": "How well it ranks, next day", "value": 0.085,
                "unit": "score", "interval": {"lo": 0.065, "hi": 0.105, "level": 0.95, "method": "hac_overlap"},
                "sample": {"days": 584, "effective_days": 584, "coins": 100}, "method_version": "m/1",
                "sample_words": "584 days · 584 effective days · 100 coins",
            },
            {
                "ref": "fifths.spread", "measure": "spread", "label": "Top fifth minus bottom fifth",
                "value": 0.004, "unit": "fraction", "interval": None,
                "withheld_reason": "below 20 separate periods", "sample": {"days": 584}, "method_version": "m/1",
                "sample_words": "584 days",
            },
        ],
    }  # fmt: skip
    lines = rv.study_text(env).splitlines()
    assert lines[1] == "How well it ranks, next day: 0.085 (t 8.3)"
    i = lines.index("In short:")
    assert lines[i + 1] == (
        "- How well it ranks, next day: 0.085, interval 0.065 to 0.105; 584 days · 584 effective days · 100 coins"
    )
    assert lines[i + 2] == (
        "- Top fifth minus bottom fifth: 0.4%, no interval (below 20 separate periods); 584 days"
    )
    # SDK-FIX (JOURNEYS S2): a study with bars says only the measures no bar row states (its bar lines carry the
    # rest); every measure barred -> no "In short" (the old control, now scoped to barred measures)
    with_bars = _study()
    with_bars["headline"]["measures"] = env["headline"]["measures"]
    i = rv.study_text(with_bars).splitlines().index("In short:")
    assert (
        rv.study_text(with_bars)
        .splitlines()[i + 1]
        .startswith("- How well it ranks, next day: 0.085")
    )
    with_bars["checks"]["rows"][0].update(ref="ic", measure="ic")
    with_bars["checks"]["rows"][1].update(ref="fifths.spread", measure="spread")
    assert "In short:" not in rv.study_text(with_bars).splitlines()


def test_a_locked_step_is_said_once_on_the_steps_line() -> None:
    """RI-263 (07 §4.1 "Truthful plans"): a Free Check's re-picking reads "...: not included in this account's plan"
    on the Steps line; its STUDY_PLAN_STEP_NOT_INCLUDED issue (the same sentence, 07 A16) is not printed again.
    Every other issue line still is. SEED (reversed by edit): the skip removed reds the count (2 != 1)."""
    lock = "Re-picking the settings as you go: not included in this account's plan."
    expired = "Costs from none to six times reads the book of AT-9 v3's backtest, which has been cleared; this plan runs that backtest again."
    plan = {
        "study": {"title": "Momentum rotation v3", "kind_label": KIND, "state": "planned"},
        "writes": "a new study, version 1",
        "words": {"work": "22 runs"},
        "steps": [
            {"id": "costs", "label": "Costs from none to six times", "to_run": 5, "runnable": True},
            {
                "id": "repick",
                "label": "Re-picking the settings as you go",
                "to_run": 0,
                "runnable": False,
                "locked": {"unit": "feature:research_repick", "words": lock},
            },
        ],
        "issues": [
            {"code": "STUDY_PLAN_STEP_NOT_INCLUDED", "path": "/steps/repick", "message": lock},
            {"code": "STUDY_PLAN_STORED_EXPIRED", "path": "/steps/costs", "message": expired},
        ],
        "plan_id": "pln_" + "a" * 64,
        "run_allowed": True,  # D-198: the Check runs without its re-picking
        "links": {"app": "https://app.usekeel.io/research/new"},
    }
    text = rv.plan_text(plan)
    assert text.count("not included in this account's plan") == 1, text
    assert (
        "Re-picking the settings as you go: not included in this account's plan" in text
    )  # its served words
    assert (
        expired in text and 'keel_research_run(plan_id="pln_' in text
    )  # non-vacuity: other lines stay
    # Control: the issue of a step the Steps line does not name as locked is still printed.
    other = {**plan, "issues": [{**plan["issues"][0], "path": "/steps/costs"}]}
    assert lock in rv.plan_text(other)


def test_a_plan_that_cannot_run_without_a_wall_says_a_locked_step_once() -> None:
    """RI-278 (07 §4.1; RI-263's rule): run_allowed false with no served words.limit (the fallback) prints no locked
    step's words the Steps line already says as "not included"; a locked step past the line's 40 (not named there)
    keeps its words (control). SEED (reversed by edit): the fallback printing every locked step's words reds the
    count (2 != 1)."""
    lock = "Re-picking the settings as you go: not included in this account's plan."
    steps = [
        {
            "id": "repick",
            "label": "Re-picking the settings as you go",
            "to_run": 0,
            "runnable": False,
            "locked": {"unit": "feature:research_repick", "words": lock},
        },
        *({"id": f"s{i}", "label": f"Step {i}", "to_run": 1, "runnable": True} for i in range(40)),
    ]
    plan = {
        "study": {"title": "Momentum rotation v3", "kind_label": KIND, "state": "planned"},
        "writes": "a new study, version 1",
        "words": {"work": "41 runs"},
        "steps": steps,
        "issues": [],
        "plan_id": "pln_" + "a" * 64,
        "run_allowed": False,
        "links": {"app": "https://app.usekeel.io/research/new"},
    }
    text = rv.plan_text(plan)
    assert text.count("not included in this account's plan") == 1, text
    assert (
        "Steps (41): Re-picking the settings as you go: not included" in text
    )  # non-vacuity: the line names it
    late = "Step 99: not included in this account's plan."
    past = {
        **plan,
        "steps": [
            *steps,
            {"id": "s99", "label": "Step 99", "runnable": False, "locked": {"words": late}},
        ],
    }
    assert late in rv.plan_text(
        past
    )  # control: a locked step the line does not name keeps its words


def _side_by_side(cells: dict | None) -> dict:
    """keel-api's data read of cmp_v3v4's "Side by side" with the stored backtest added (research.page_facts
    .view_read): the rows as stored, field keys and raw values; its sentences and, when served, its cells' words."""
    v4, stored = "Momentum rotation v4", "Momentum rotation v4 backtest of Oct 18"
    facts: dict = {
        "rows_total": 4,
        "footing_words": [
            f"Fee: {v4} 0.045%; {stored} 0.09%.",
            f"Coins: {v4} 3 coins; {stored} not recorded.",
        ],
    }
    if cells is not None:
        facts["footing_cells"] = cells
    return {
        "data": [
            {"result": v4, "metric": "fee", "value": "0.00045"},
            {"result": stored, "metric": "fee", "value": "0.0009"},
            {"result": v4, "metric": "coins", "value": "BTC,ETH,SOL"},
            {"result": stored, "metric": "coins", "value": "not recorded"},
        ],
        "pagination": {"cursor": None, "has_more": False},
        "facts": facts,
    }


#: D-252: the served words for _side_by_side's cells (research.page_facts.footing_cells).
FOOTING_CELLS = {
    "axis": "metric",
    "measure": "value",
    "labels": {"fee": "Fee", "coins": "Coins"},
    "values": {
        "fee": {"0.00045": "0.045%", "0.0009": "0.09%"},
        "coins": {"BTC,ETH,SOL": "3 coins", "not recorded": "not recorded"},
    },
}


def test_ri354_the_side_by_side_text_reads_in_the_served_words() -> None:
    """RI-354 (D-252; 07 §3.2 rule 2): an agent reading SameFooting's table as text gets the words the app draws, the
    field's label and each value's served words, and the served difference sentences after the rows; no stored key
    and no raw value. Non-vacuity: four rows, both sentences, every cell worded."""
    text = rv.data_text(
        _side_by_side(FOOTING_CELLS),
        title="Side by side",
        version=2,
        call_args={"study_id": "stu_a", "view": 4},
    )
    rows = [line for line in text.splitlines() if line.startswith("| Momentum")]
    assert rows == [
        "| Momentum rotation v4 | Fee | 0.045% |",
        "| Momentum rotation v4 backtest of Oct 18 | Fee | 0.09% |",
        "| Momentum rotation v4 | Coins | 3 coins |",
        "| Momentum rotation v4 backtest of Oct 18 | Coins | not recorded |",
    ], text
    assert (
        "Fee: Momentum rotation v4 0.045%; Momentum rotation v4 backtest of Oct 18 0.09%." in text
    )
    assert (
        "Coins: Momentum rotation v4 3 coins; Momentum rotation v4 backtest of Oct 18 not recorded."
        in text
    )
    assert not [
        w for w in ("0.00045", "0.0009", "BTC,ETH,SOL", "| fee |", "| coins |") if w in text
    ], text


def test_ri354_an_unworded_value_reads_a_dash_and_other_tables_are_unchanged() -> None:
    """RI-354 control: a value the read serves no words for reads "—", never its key; a page with no footing cells
    (any other table) writes its rows as served, and its sentences still follow when served."""
    cells = {**FOOTING_CELLS, "labels": {"fee": "Fee"}}
    text = rv.data_text(
        _side_by_side(cells), title="Side by side", version=2, call_args={"study_id": "stu_a"}
    )
    assert "| Momentum rotation v4 | — | 3 coins |" in text and "| coins |" not in text
    bare = rv.data_text(
        _side_by_side(None), title="Side by side", version=2, call_args={"study_id": "stu_a"}
    )
    assert (
        "| Momentum rotation v4 | fee | 0.00045 |" in bare
    )  # no served cells: as served, as before
    assert (
        "Fee: Momentum rotation v4 0.045%; Momentum rotation v4 backtest of Oct 18 0.09%." in bare
    )


def test_ri361_an_axis_column_reads_its_served_label_beside_its_key() -> None:
    """RI-361 (07 §3.2 rule 2): an agent reading a page as text gets each axis column's served label, the words the
    app draws ("Days late"), with the key a ``where`` takes beside it; a setting whose label is its name already
    ("lookback") reads once, and a measure column reads as served. Control: a page frozen before RI-361 (no labels)
    writes its keys as before. Non-vacuity: three columns, two of them axes. SEED (reverse the edit): ``_header``
    returning ``column`` -> the labelled arm reds, the control stays green."""
    axes = [
        {
            "name": "days_late",
            "kind": "param",
            "role": "ordered",
            "values": 2,
            "label": "Days late",
        },
        {
            "name": "lookback",
            "kind": "param",
            "role": "ordered",
            "values": 1,
            "address": "step[2].period",
            "label": "lookback",
        },
    ]
    page = {
        "data": [
            {"days_late": 1, "lookback": 10, "sharpe": 0.9},
            {"days_late": 2, "lookback": 10, "sharpe": 0.2},
        ],
        "pagination": {"cursor": None, "has_more": False},
        "facts": {"rows_total": 2, "axes": axes},
    }
    text = rv.data_text(page, title="Sharpe when trading late", version=1, call_args={})
    # MCP-FINAL S3 (POLISH): the label alone in the header; the key a `where` takes said once under the table
    # AUDIT-WORDS M13: a column with no served label reads in words too ("Sharpe"), never its key
    assert "| Days late | lookback | Sharpe |" in text, text
    assert "Keys for where, sort and measures: Days late = days_late, Sharpe = sharpe." in text, text
    assert "| 1 | 10 | 0.9 |" in text
    old = {**page, "facts": {"rows_total": 2, "axes": [{**a, "label": None} for a in axes]}}
    bare = rv.data_text(old, title="Sharpe when trading late", version=1, call_args={})
    # control (an older server, no labels): the keys as phrases, never snake case
    assert "| Days late | Lookback | Sharpe |" in bare and "days_late |" not in bare


def test_d315_a_money_measure_reads_its_served_basis_beside_its_key() -> None:
    """D-315 (RI-568): a trades page's ``gross_return`` reads "before fees and funding" beside its key, the words the
    data route serves (``facts.measures[].basis``) and the app's CSV header writes; ``ret`` (withheld, null) and a
    measure with no basis read their keys as served. SEED (reverse the edit): ``_header`` ignoring ``bases`` -> red."""
    measures = [
        {"name": "ret", "label": "Return", "unit": "fraction"},
        {"name": "gross_return", "label": "Return before fees and funding", "unit": "fraction",
         "basis": "before fees and funding"},
    ]  # fmt: skip
    page = {
        "data": [{"coin": "BNB", "ret": None, "gross_return": -0.0605}],
        "pagination": {"cursor": None, "has_more": False},
        "facts": {"rows_total": 1, "axes": [], "measures": measures},
    }
    text = rv.data_text(page, title="Trades", version=3, call_args={})
    # WL-AG-12 / POLISH: the measure's label (its basis in it) in the header, the keys once under the table
    assert "| Coin | Return | Return before fees and funding |" in text, text
    assert (
        "Keys for where, sort and measures: Coin = coin, Return = ret, Return before fees and funding = gross_return."
        in text
    )
    nolabel = rv.data_text(
        {**page, "facts": {**page["facts"], "measures": [{**m, "label": None} for m in measures]}},
        title="Trades",
        version=3,
        call_args={},
    )
    assert "| Coin | Ret | Gross return (before fees and funding) |" in nolabel, (
        nolabel
    )  # the basis beside the key as a phrase
    assert (
        "| BNB | \u2014 | \u22120.0605 |" in text and "None" not in text
    )  # a withheld (null) value, never Python's None


# ── Over keel-api's real shapes (RI-615, WL-AG-01..07) ───────────────────────
#
# ``fixtures/research_server_shapes.json``: what keel-api's routes answered on a disposable Postgres (the brief, the
# project, the list, the receipts), and the mocked world's study, share, plan and data page, each validated against
# keel-api's closed route model (``_provenance`` says which). The texts below are read over THOSE shapes: the
# fixtures above were the agent's own, and their field names were the ones the server never sends (RI-615).

SHAPES = json.loads(
    (Path(__file__).parent / "fixtures" / "research_server_shapes.json").read_text()
)


def _shape(name: str) -> dict:
    return json.loads(json.dumps({k: v for k, v in SHAPES[name].items() if k != "_provenance"}))


class _Client:
    """keel-api's fixed answers by (method, path prefix), longest first; records every call."""

    def __init__(self, answers: dict) -> None:
        self.answers, self.calls = answers, []

    def _answer(self, method: str, path: str, params=None) -> dict:
        self.calls.append(
            (method, path, {k: v for k, v in (params or {}).items() if v is not None})
        )
        best = max(
            (k for k in self.answers if k[0] == method and path.startswith(k[1])),
            key=lambda k: len(k[1]),
        )
        return json.loads(json.dumps(self.answers[best]))

    def get(self, path, *, headers=None, **params):
        return self._answer("get", path, params)

    def post(self, path, json=None, *, headers=None, **params):
        return self._answer("post", path, params)


def _run(tool: str, args: dict, answers: dict):
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = _Client(answers)
    got = OUTCOMES[tool].handler(args, ToolContext(api_client=client))
    return client, got.extra["view"]["markdown"]


def test_ri615_the_brief_reads_the_servers_names_and_lists_its_rows(monkeypatch) -> None:
    """RI-615 (A3 #2): the brief over keel-api's real answer names every study, project and set-aside study by its id,
    attributes each note ("you", "your agent"), lists the family's versions tried, and its next calls name real ids.
    It printed ``- None · Check · Momentum rotation v3`` and ``note (decision, None)``: it read ``study_id`` and
    ``author_role``, the server sends ``id`` and ``by``.
    SEED (reversed): ``study.get('id')`` -> ``study.get('study_id')`` in ``brief_text`` -> this reds on "None"; the
    project and set-aside rows (control) stay green. Not vacuous: three studies, a project, a set-aside study, three
    notes and one family, all from the recorded route."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    brief = _shape("brief")
    assert (
        len(brief["studies"]) == 3
        and brief["projects"]
        and brief["set_aside"]
        and len(brief["notes"]) == 3
    )
    _, text = _run("keel_research_brief", {}, {("get", "/v1/research/brief"): brief})
    assert "None" not in text, text
    for study in brief["studies"]:
        assert (
            f"- {study['id']} · Check · {study['title']} · {study['state_words']}" in text
        )  # served words
    project = brief["projects"][0]
    assert f"- {project['project_id']} · Momentum: Does momentum survive costs?" in text
    aside = brief["set_aside"][0]
    assert (
        f'- {aside["study"]} · Second look: "same as the first; costs too high" (you, Nov 17, 2026)'
        in text
    )
    assert "(your agent, Nov 17, 2026)" in text and "- Decision on project prj_" in text
    assert "- Momentum rotation: 5 since Nov 17, 2026" in text
    assert f'Read a study: keel_research_get(study_id="{brief["studies"][0]["id"]}")' in text
    assert f'Read a project: keel_research_get(project_id="{project["project_id"]}")' in text


def test_ri615_a_long_note_is_cut_and_says_so_and_the_brief_holds_8000_characters(
    monkeypatch,
) -> None:
    """WL-AG-01: a note body is at most 200 characters in the brief, cut at a word with its length said; at the
    largest legal brief (25 studies, 50 projects, 50 set aside, 10 notes of 8,192 characters) the text is at most
    8,000 characters and keeps its next calls. SEED (reversed): ``NOTE_BODY_MAX = 200`` -> ``9_000`` -> the cut
    assertion reds and the cap still holds (``_cap``, the control) but drops the project rows."""
    from keel.tools.outcomes import research_brief as rb
    from keel.tools.outcomes._base import ToolContext

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    brief = _shape("brief")
    long = next(n for n in brief["notes"] if len(n["body"]) > 1000)
    text = rb.brief_text(brief, ToolContext())
    assert (
        f"of {len(' '.join(long['body'].split())):,} characters)" in text
        and long["body"] not in text
    )
    big = _shape("brief")
    study, project, aside, note = big["studies"][0], big["projects"][0], big["set_aside"][0], long
    big["studies"] = [{**study, "id": f"stu_{i:024d}", "title": "t" * 120} for i in range(25)]
    big["projects"] = [
        {**project, "project_id": f"prj_{i:023d}", "question": "q" * 300} for i in range(50)
    ]
    big["set_aside"] = [{**aside, "study": f"stu_{i:024d}", "why": "w " * 4000} for i in range(50)]
    big["notes"] = [{**note, "body": "n " * 4096} for _ in range(10)]
    big["studies_total"], big["pagination"] = 400, {"cursor": "c25", "has_more": True}
    text = rb.brief_text(big, ToolContext())
    assert len(text) <= rv.TEXT_MAX_CHARS, len(text)
    assert 'More studies: keel_research_brief(cursor="c25")' in text
    assert 'Read a study: keel_research_get(study_id="stu_' in text and text.splitlines()[
        -1
    ].startswith("View in Keel")


def _forty_step_plan() -> dict:
    plan = _shape("plan")
    step = plan["steps"][0]
    plan["steps"] = [{**step, "id": f"s{i}", "label": f"Step number {i}"} for i in range(40)]
    plan["issues"] = [
        {
            "code": "STUDY_PLAN_STORED_EXPIRING",
            "severity": "warning",
            "path": f"/steps/s{i}",
            "message": "This step's stored detail expires after 30 days; its evidence is kept.",
            "suggestion": None,
        }
        for i in range(40)
    ] + [
        {
            "code": "STUDY_PLAN_LONG",
            "severity": "warning",
            "path": None,
            "message": "This plan is long. " + "It runs many settings. " * 300,
            "suggestion": None,
        }
    ]
    return plan


def test_wl_ag_04_the_cap_keeps_the_run_line_and_a_repeated_warning_prints_once(
    monkeypatch,
) -> None:
    """WL-AG-04 (A3 #8): a 40-step plan with 40 served warnings and a long one keeps its "Run it" line under the cap;
    the warning repeated on every step prints once with its count. The cap popped lines from the end, and a plan's
    last body line is "Run it": the 40-step plan lost the one line that acts.
    SEED (reversed): ``isinstance(body[i], Keep)`` -> ``False`` in ``_cap`` -> the Run line is dropped (this reds);
    the control (a short plan) keeps every line and no "(more in the app)"."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    text = rv.plan_text(_forty_step_plan())
    assert len(text) <= rv.TEXT_MAX_CHARS
    assert f'keel_research_run(plan_id="{_shape("plan")["plan_id"]}")' in text, text[-600:]
    assert text.count("stored detail expires") == 1
    assert (
        "This step's stored detail expires after 30 days; its evidence is kept. (on 40 steps)"
        in text
    )
    assert text.splitlines()[0].startswith("Plan · ") and text.splitlines()[-1].startswith(
        "View in Keel: "
    )
    assert rv.MORE_IN_THE_APP in text
    short = rv.plan_text(_shape("plan"))  # control
    assert rv.MORE_IN_THE_APP not in short and "keel_research_run(" in short


def test_wl_ag_03_a_share_read_names_its_maker_and_offers_both_actions(monkeypatch) -> None:
    """WL-AG-03 (A3 #4, 07 §4.4, AT-29): a share read's first line ends "· shared by {name or Keel}", it offers Run on
    yours (the plan's From share form) and Make it yours (``keel_app_link`` to the snapshot; the CLI's copy beside it),
    and its charts and "more bars" calls carry the share id. SEED (reversed): ``_target`` returning ``{}`` -> the
    charts call names no id (this reds); the study read's charts call (control) is unchanged."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    share = _shape("share")
    sid = share["study"]["share_id"]
    text = rv.study_text(share)
    first = text.splitlines()[0]
    assert first.startswith("Shared study · Check a strategy · ") and first.endswith(
        "· shared by Test User"
    ), first
    assert (
        f'Run on yours: keel_research_plan(share_id="{sid}", subjects=["Strategy(\\"str_…@3\\")"])'
        in text
    )
    assert f'Make it yours, a copy with nothing run: keel_app_link(id="{sid}")' in text
    assert f'keel_research_get(share_id="{sid}", view=0)' in text
    assert "Runs: 0 runs" not in text  # a snapshot's counts are not this reader's runs
    keel = rv.study_text({**share, "by_keel": True})
    assert keel.splitlines()[0].endswith("· shared by Keel")
    many = {
        **share,
        "checks": {**share["checks"], "count": 30, "rows": share["checks"]["rows"] * 4},
    }
    assert f'keel_research_get(share_id="{sid}", output="bars")' in rv.study_text(many)
    study = _shape("study_done")  # control
    assert 'keel_research_get(study_id="stu_chkv3000000000000000000000", view=0)' in rv.study_text(
        study
    )
    assert "Run on yours" not in rv.study_text(study)


def test_wl_ag_03_a_shares_chart_rows_and_bars_are_read_by_the_share_id(monkeypatch) -> None:
    """A3 #4: ``keel_research_get`` with ``share_id`` and ``view`` reads the public share-view data route (a chart by
    its title or index), and ``output="bars"`` the snapshot's bars; it refused any view without a ``study_id``.
    SEED (reversed): route ``_share_data`` back to the study-only refusal -> both arms red; the study's data read
    (control) still calls the study's route."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    share = _shape("share")
    sid = share["study"]["share_id"]
    page = {k: v for k, v in _shape("data_page").items() if k not in ("study", "view", "filling")}
    answers = {
        ("get", f"/v1/research/shares/{sid}"): share,
        ("get", f"/public/study-shares/{sid}/views/1/data"): {**page, "share_id": sid, "view": "1"},
    }
    client, text = _run("keel_research_get", {"share_id": sid, "view": "Growth of 1"}, answers)
    assert client.calls[-1] == ("get", f"/public/study-shares/{sid}/views/1/data", {"limit": 25})
    assert (
        text.splitlines()[0] == "Growth of 1, as shared (run Oct 19, 2026)"
        and "Rows 1–25 of 585" in text
    )
    assert f'keel_research_get(share_id="{sid}", view=1, cursor="c25")' in text
    _, bars = _run("keel_research_get", {"share_id": sid, "output": "bars"}, answers)
    assert "bars 1–9 of 9" in bars and bars.count("; your bar ") == 9 and "View in Keel: " in bars
    study = _shape("study_done")  # control
    sid2 = study["study"]["study_id"]
    client, _ = _run(
        "keel_research_get",
        {"study_id": sid2, "view": 1},
        {
            ("get", f"/v1/research/studies/{sid2}/versions/1/data"): _shape("data_page"),
            ("get", f"/v1/research/studies/{sid2}"): study,
        },
    )
    assert client.calls[-1][1] == f"/v1/research/studies/{sid2}/versions/1/data"


def test_wl_ag_02_a_project_read_names_each_item_by_id(monkeypatch) -> None:
    """WL-AG-02 (A3 #3, RI-593 follow-up): over keel-api's real project read, each item is written with its id, the
    first study's read is the next call, and the text ends "View in Keel". SEED (reversed): ``· {item.get('ref')}``
    dropped from the item row -> no id reaches the agent (this reds); the item names (control) stay."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    project = _shape("project")
    _, text = _run(
        "keel_research_get",
        {"project_id": project["project_id"]},
        {("get", f"/v1/research/projects/{project['project_id']}"): project},
    )
    for item in project["items"]:
        assert f"- {item['ref_kind']} · {item['name']} · {item['ref']}" in text
    assert f'Open a study: keel_research_get(study_id="{project["items"][0]["ref"]}")' in text
    assert text.splitlines()[-1].startswith("View in Keel: https://")


def test_wl_ag_05_cancel_by_run_set_names_the_study_in_its_resume_call(monkeypatch) -> None:
    """WL-AG-05 (A3 #12): a cancel by ``run_set_id`` writes "Resume later: keel_research_plan(study_id=…,
    resume=true)", the study read from the served link; it wrote ``keel_research_plan(resume=true)``, a usage error.
    SEED (reversed): ``args.get("study_id") or _study_of(env)`` -> ``args.get("study_id")`` -> no resume line names a
    study (this reds); the served words and progress (control) stay."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    cancel = _shape("cancel")
    _, text = _run(
        "keel_research_cancel",
        {"run_set_id": cancel["run_set_id"]},
        {("post", f"/v1/research/run-sets/{cancel['run_set_id']}/cancel"): cancel},
    )
    # MCP-FINAL S3 (POLISH): the served sentence says the stop once; the tile's words only with no sentence served
    assert text.splitlines()[0] == cancel["words"] and cancel["progress"]["words"] not in text
    assert (
        'Resume later: keel_research_plan(study_id="stu_chkv3000000000000000000000", resume=true)'
        in text
    )


def test_wl_ag_05_a_stale_fact_names_its_offers_call_and_a_study_writes_its_notes(
    monkeypatch,
) -> None:
    """WL-AG-05 (07 §3.1, §4.4): a stale fact's words are followed by its offer's call (the plan keel-api built; a
    too-long one points to the app), and a study read writes its latest notes, attributed, and its linked studies.
    SEED (reversed): ``_stale_line`` returning ``words`` alone -> no "Plan it" (this reds); a fact with no offer
    (control) writes its words alone."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    study = _shape("study_planned")
    assert study["stale"] and study["notes"]  # not vacuous: the recorded read serves both
    text = rv.study_text(study)
    assert (
        'has a newer version, v4. Plan it in the app: keel_app_link(id="stu_' in text
    )  # its source is long
    short = {
        "kind": "newer_subject",
        "words": "Newer.",
        "offer": {"plan": {"study_id": "stu_a", "source": "S"}},
    }
    assert 'Newer. Plan it: keel_research_plan(study_id="stu_a", source="S")' in rv.study_text(
        {**study, "stale": [short]}
    )
    bare = {"kind": "newer_subject", "words": "Its subject was deleted.", "offer": None}
    assert "Its subject was deleted.\n" in rv.study_text({**study, "stale": [bare]})  # control
    assert (
        "Notes (1 shown of 1, newest first):" in text
        and "- Hypothesis (your agent, Nov 17, 2026): " in text
    )
    linked = {
        **study["study"],
        "linked_to": [
            {
                "study_id": "stu_b",
                "version": 1,
                "reason": "days",
                "title": "Later",
                "words": "A newer study on later days: Later",
            }
        ],
    }
    assert "A newer study on later days: Later (stu_b v1)" in rv.study_text(
        {**study, "study": linked}
    )
    # AUDIT-WORDS M61: a count not known is left out, never "unknown"
    assert "versions tried:" not in text and "unknown" not in text


def test_wl_ag_05_an_add_plan_leads_with_what_it_adds(monkeypatch) -> None:
    """07 §4.2 (A3 #12): an add plan's first line names what is added, by the plan's own subject names, and at which
    version; the served work line follows once. Control: a plan with no ``added`` keeps its "Plan · …" line."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    plan = _shape("add_plan")
    assert plan["added"]  # not vacuous
    text = rv.plan_text(plan)
    assert (
        text.splitlines()[0]
        == "Adds 28-day momentum, OI screen to Signals, side by side · version 2"
    ), text
    assert text.count("1 run, not counted against you") == 1
    assert rv.plan_text(_shape("plan")).startswith("Plan · Check a strategy · ")


def test_r22_the_cli_reads_100_rows_a_page_and_mcp_25(monkeypatch) -> None:
    """R-22 (07 §3.5): the CLI's ``keel research data`` pages 100 rows by default (the API's), MCP's get 25.
    SEED (reversed): ``CLI_DATA_ROWS = 25`` -> the CLI arm reds; the MCP arm (control) stays at 25."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    study = _shape("study_done")
    sid = study["study"]["study_id"]
    answers = {
        ("get", f"/v1/research/studies/{sid}/versions/1/data"): _shape("data_page"),
        ("get", f"/v1/research/studies/{sid}"): study,
    }
    cli, _ = _run("keel_research_data", {"study_id": sid, "view": 1}, answers)
    assert cli.calls[-1][2]["limit"] == 100
    mcp, _ = _run("keel_research_get", {"study_id": sid, "view": 1}, answers)
    assert mcp.calls[-1][2]["limit"] == 25


def test_wl_ag_06_a_shared_studys_id_links_its_snapshot(monkeypatch) -> None:
    """WL-AG-06, D-341 (1): ``keel_app_link`` takes a shared study's 23-character id (the share read's Make it yours
    call) and links the public share page ``/share/{id}`` (the page keel-api's share read links, proven signed out),
    never the signed-in ``/research/{id}``; it answered "unknown prefix". SEED (reversed): drop the 23-character arm
    of ``kind_for_id`` -> "unknown prefix" again (this reds); control: a strategy share keeps its page."""
    from keel.tools.outcomes import open_in_app as oia
    from keel.tools.outcomes._base import ToolContext

    monkeypatch.delenv("KEEL_APP_URL", raising=False)
    sid = _shape("share")["study"]["share_id"]
    assert len(sid) == 23 and oia.kind_for_id(sid) == "share"
    got = oia.OPEN_IN_APP.handler({"id": sid}, ToolContext())
    assert (
        got.hero_url == f"https://usekeel.io/share/{sid}"
        and got.extra["target_kind"] == "share_page"
    )
    assert oia.kind_for_id("A" * 21) == "share"  # control: a strategy share keeps its page
    # D-358: the Research wording rides a bound yes only; unbound, the full profile is the pre-Research text.
    from keel.tools.outcomes import OUTCOMES
    from keel.tools.outcomes._mcp_adapter import effective_description
    from keel.tools.outcomes._toolsets import research_served_context

    assert (
        "`stu_...`" in oia.RESEARCH_DESCRIPTION and oia.DESCRIPTION == oia.LISTED_HEAD_DESCRIPTION
    )
    assert "stu_" not in effective_description(
        OUTCOMES["keel_app_link"]
    )  # unbound: the pre-Research copy
    with research_served_context(True):
        assert effective_description(OUTCOMES["keel_app_link"]) == oia.RESEARCH_DESCRIPTION
    with research_served_context(False):
        assert "stu_" not in effective_description(OUTCOMES["keel_app_link"])


def test_d9_a_bar_line_quotes_the_served_words(monkeypatch) -> None:
    """D-328 D9 (WL-AG-12, 13 §3.2): over keel-api's done study (rows serving value/interval/sample/bar words), each
    bar line quotes those strings, the ones the card and the app show ("99th", "your bar ≥ 75th"), never a second
    rendering (it read "0.993" and "your bar >= 0.75"); a measure column reads its served label beside its key.
    SEED (reversed): ``bar_row`` ignoring ``value_words`` (the formatter back in the bar line) -> this reds; control:
    a row served without words still reads through the formatter."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    study = _shape("study_done")
    rows = study["checks"]["rows"]
    assert all(r.get("value_words") for r in rows)  # not vacuous: the served words are there
    text = rv.study_text(study)
    luck = rows[0]
    assert (
        f"- {luck['label']}: {luck['value_words']}, no interval; {luck['sample_words']}; {luck['bar_words']}; met"
        in text
    )
    assert "0.993" not in text and ">= 0.75" not in text
    bare = {k: v for k, v in luck.items() if not k.endswith("_words")}
    assert rv.bar_row(bare, set()).startswith(
        f"- {luck['label']}: 0.993"
    )  # control: no words, the formatter
    page = _shape("data_page")
    lines = rv.data_text(page, title="Growth of 1", version=1, call_args={}).splitlines()
    # POLISH: labels in the header, keys under the table; M13: a page of one result leaves the Result column out
    assert lines[2] == "| Growth of 1 | Day |"
    assert "Keys for where, sort and measures: Growth of 1 = growth, Day = time." in lines


def test_d341_a_never_run_draft_names_its_next_call(monkeypatch) -> None:
    """D-341 (3): a never-run Draft's read offers its run with no new tool: plan the Draft, then run that plan. Over
    keel-api's real planned-study read: with its live plan served (``draft.plan_id``, SERVE's half) the call is
    ``keel_research_plan(from_plan=…)``; without it, the plan tool's draft form ``(study_id=…, version=n)``, short
    whatever the Draft's size (the recorded source is 4.8 KB and MCP never carries it). SEED (reversed): drop the
    ``elif`` arm in ``study_text`` -> the Draft line names no call (this reds); control: an older version's read still
    points to the Draft's own read."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    study = _shape("study_planned")
    sid, version = study["study"]["study_id"], study["study"]["version"]
    assert study["study"]["draft"]["version"] == version  # not vacuous: the read is the Draft
    assert len(study["source"]) > 4_000  # not vacuous: too long to write inline
    text = rv.study_text(study)
    assert (
        f'not run yet. keel_research_plan(study_id="{sid}", version={version}) plans its run; '
        "keel_research_run runs that plan." in text
    ), text
    assert "keel_app_link" not in text.split("not run yet.")[1].splitlines()[0]
    live = {
        **study,
        "study": {**study["study"], "draft": {**study["study"]["draft"], "plan_id": "pln_draft"}},
    }
    assert (
        'keel_research_plan(from_plan="pln_draft") plans its run; keel_research_run runs that plan.'
        in rv.study_text(live)
    )
    older = {
        **study,
        "study": {
            **study["study"],
            "version": 1,
            "draft": {**study["study"]["draft"], "version": 2},
        },
    }
    assert "Edit it on its own read: keel_research_get(" in rv.study_text(older)  # control


class _BodyClient(_Client):
    """``_Client`` that also records each POST's body."""

    def post(self, path, json=None, *, headers=None, **params):
        self.calls.append(("body", path, json))
        return self._answer("post", path, params)


def test_d341_the_draft_form_plans_the_drafts_own_source_and_base(monkeypatch) -> None:
    """D-341 (3): ``keel_research_plan(study_id, version)`` alone reads that version and plans the never-run Draft
    through keel-api's edit form with the read's own source and base (keel-api plans a saved never-run study by its
    own source: test_research_agent_contract_postgres ``_two_plans``). SEED (reversed): ``set(body) ==
    DRAFT_FIELDS`` -> ``False`` -> the route gets ``{study_id, version}`` and no source (this reds). Control: a
    version that ran is refused in words, nothing is planned."""
    from keel.errors import KeelError
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    _bootstrap()
    study, plan = _shape("study_planned"), _shape("plan")
    sid, version = study["study"]["study_id"], study["study"]["version"]
    client = _BodyClient(
        {("get", "/v1/research/studies/"): study, ("post", "/v1/research/plans"): plan}
    )
    got = OUTCOMES["keel_research_plan"].handler(
        {"study_id": sid, "version": version}, ToolContext(api_client=client)
    )
    assert ("get", f"/v1/research/studies/{sid}") == client.calls[0][:2] and client.calls[0][2][
        "version"
    ] == version
    posted = [c for c in client.calls if c[0] == "body"]
    assert len(posted) == 1 and posted[0][1] == "/v1/research/plans"
    body = posted[0][2]
    assert (
        body["study_id"] == sid
        and body["source"] == study["source"]
        and body["base"]["version"] == version
    )
    assert set(body) == {"study_id", "source", "base"}
    assert "keel_research_run(plan_id=" in got.extra["view"]["markdown"]
    ran = {**study, "study": {**study["study"], "draft": None}}
    refused = _BodyClient(
        {("get", "/v1/research/studies/"): ran, ("post", "/v1/research/plans"): plan}
    )
    with pytest.raises(KeelError, match="not a saved Draft that has not run"):
        OUTCOMES["keel_research_plan"].handler(
            {"study_id": sid, "version": version}, ToolContext(api_client=refused)
        )
    assert not [c for c in refused.calls if c[0] == "body"]  # control: nothing planned


def test_ag18_an_address_read_says_why_in_the_served_words(monkeypatch) -> None:
    """WL-AG-18 (07 §4.12), over keel-api's address read (the validated world's Momentum rotation v3, 20 rows): a row
    that cannot be set reads its served ``why_not_words``, never the bare code ("— inert"), and ``bound_to`` (a list)
    reads as the addresses; the answer ends "View in Keel". SEED (reversed): read ``why_not`` before
    ``why_not_words`` -> "— inert" returns (this reds); control: a settable row reads its value only."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    page = _shape("addresses")
    subject = page["subject"]
    _, text = _run(
        "keel_research_get", {"subject": subject}, {("get", "/v1/research/addresses"): page}
    )
    inert = next(r for r in page["data"] if r.get("why_not") == "inert")
    assert f"— {inert['why_not_words']}" in text and "— inert" not in text
    assert "(bound to [])" not in text and text.splitlines()[-1].startswith("View in Keel: ")
    settable = next(r for r in page["data"] if r.get("settable") and r.get("component"))
    step = f"- {settable['address']} = {json.dumps(settable['value'])} · {settable['component']} v{settable['version']}"
    assert step in text  # control: a step's address names its component and version
    # MCP-FINAL #9: a declaration's address (execution.*, globals.*) belongs to no component and names none. SEED
    # (reversed): the old unconditional " · {component} v{version}" -> "· None vNone" on 6 rows (this reds).
    declared = next(r for r in page["data"] if r.get("settable") and not r.get("component"))
    assert "None vNone" not in text
    assert f"- {declared['address']} = {json.dumps(declared['value'])}\n" in text


def test_d344_a_plan_states_its_run_call_as_a_fact_with_its_count(monkeypatch) -> None:
    """D-344 (5), R-L4 (CONFORM-MCP F7): a result never instructs a quota-spending call. Over keel-api's plan, the run
    call is stated with the served count ("Running it counts as 1 of your 400 studies …: keel_research_run(…)"), never
    "Run it: …", and the count is not said twice. SEED (reversed): the old "Run it: " line -> this reds; control: a
    plan served with no allowance words states "… runs this plan."."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    plan = _shape("plan")
    allowance = plan["words"]["allowance"]
    text = rv.plan_text(plan)
    assert "Run it:" not in text
    # D-359 (5) (b): the served count line byte for byte, then the call as a fact (never a re-cased "Running it …").
    assert allowance in text.splitlines()
    assert f'keel_research_run(plan_id="{plan["plan_id"]}") runs this plan.' in text
    assert text.count(allowance[1:]) == 1
    bare = {**plan, "words": {**plan["words"], "allowance": None}}
    assert f'keel_research_run(plan_id="{plan["plan_id"]}") runs this plan.' in rv.plan_text(bare)


def test_d353_include_source_writes_the_document_in_the_text_only(monkeypatch) -> None:
    """D-353: ``keel_research_get(study_id, include="source")`` writes the study's study/1 document and the read's
    ``base`` after the study text, so an agent on any host (claude.ai reads only the text block) can read it, change
    it and plan the change; structuredContent never carries it. Over keel-api's real planned-study read (4.8 KB of
    source), on the listed and the full profile. SEED (reversed): ``return _source(args, ctx)`` dropped from the
    handler -> the plain read answers, no document (this reds). Control: a share id is refused in words."""
    from keel.errors import KeelError
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    study = _shape("study_planned")
    sid, source = study["study"]["study_id"], study["source"]
    assert len(source) > 4_000  # not vacuous: the whole document, past any inline offer
    from keel.tools.outcomes._toolsets import research_served_context

    for profile in ("listed", "full"):
        monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
        with research_served_context(
            True
        ):  # the listed profile serves plan, run and get while Research is on
            client, text = _run(
                "keel_research_get",
                {"study_id": sid, "include": "source"},
                {("get", "/v1/research/studies/"): study},
            )
        assert source.rstrip() in text, profile
        assert (
            f"Source of version {study['study']['version']} ({len(source):,} characters):" in text
        )
        assert (
            f'Plan a change: keel_research_plan(study_id="{sid}", source="<the changed document>", base='
            in text
        )
        assert study["study"]["evidence_hash"] in text  # the base an edit sends
    _bootstrap()
    got = OUTCOMES["keel_research_get"].handler(
        {"study_id": sid, "include": "source"},
        ToolContext(api_client=_Client({("get", "/v1/research/studies/"): study})),
    )
    from keel.tools.outcomes._channels import partition

    structured, _card = partition(got.extra, "study", move_card=True)
    assert source[:200] not in json.dumps(structured)  # never in structuredContent
    with pytest.raises(KeelError, match="shared study serves no source"):
        OUTCOMES["keel_research_get"].handler(
            {"share_id": "a" * 23, "include": "source"}, ToolContext(api_client=_Client({}))
        )  # control


def test_a_cut_note_on_the_study_read_says_its_length(monkeypatch) -> None:
    """SERVE 3fbd0571b9 (over keel-api's real planned-study read): a note body the read cut at 200 characters is served
    ``truncated`` with its ``length``; the text says "(the first N of 1,360 characters)", as the brief does.
    SEED (reversed): the ``truncated`` arm removed -> this reds. Control: an uncut note reads its body only."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    study = _shape("study_planned")
    note = study["notes"][0]
    assert (
        note["truncated"] is True and note["length"] == 1360
    )  # not vacuous: the recorded note was cut
    assert f"of {note['length']:,} characters)" in rv.study_text(study)
    whole = {**study, "notes": [{**note, "body": "keep v3", "truncated": False, "length": 7}]}
    assert (
        '"keep v3"' in rv.study_text(whole)
        and "characters)" not in rv.study_text(whole).split("Notes")[1]
    )


def test_d353_the_sdk_reads_the_words_serve_already_serves(monkeypatch) -> None:
    """Integrator D-353 (SERVE 63cc77014e): the SDK reads what keel-api serves instead of re-requesting it. A data
    read asks ``words=true`` and writes each cell's served words; a headline measure reads its value/interval/sample
    words; versions tried names ``family_name`` (never the ledger's ``fam:`` id); a brief row reads its state words
    and its bars line. Fields added to real shapes as 63cc77014e's schemas declare them. SEED (reversed): the cell's
    ``words`` read removed from ``_served_cell`` -> the "−0.9%" arm reds. Control: a row without words reads its raw
    value as before."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    study = _shape("study_done")
    sid = study["study"]["study_id"]
    page = _shape("data_page")
    assert all(
        "words" in r for r in page["data"]
    )  # not vacuous: the recorded page is worded (?words=true)
    page["data"][0]["words"] = {"growth": "−0.9%"}
    page["data"][1].pop("words")  # control: a row served without words
    client, text = _run("keel_research_get", {"study_id": sid, "view": 1}, {
        ("get", "/v1/research/studies/"): study, ("get", f"/v1/research/studies/{sid}/versions/"): page})  # fmt: skip
    asked = [c for c in client.calls if c[1].endswith("/data")]
    assert asked and asked[0][2].get("words") == "true"
    assert "| −0.9% |" in text and "| words |" not in text
    # control: a row with no served words reads its value rounded, never a raw float
    assert f"| {format(page['data'][1]['growth'], '.3g')} |" in text
    named = {
        **study,
        "counts": {
            **study["counts"],
            "versions_tried": {
                **study["counts"]["versions_tried"],
                "family_name": "Momentum rotation",
            },
        },
    }
    assert "versions tried for Momentum rotation: 388" in rv.study_text(named)
    assert "fam:str_" not in rv.study_text(study)
    bare = {**study, "checks": None, "headline": {"sentence": "s", "measures": [{
        "label": "Sharpe", "value": 1.95, "unit": "ratio", "value_words": "1.95", "interval_words": "1.42 to 2.31",
        "sample_words": "585 days · 100 coins", "sample": {}}]}}  # fmt: skip
    assert "- Sharpe: 1.95, 1.42 to 2.31; 585 days · 100 coins" in rv.study_text(bare)
    from keel.tools.outcomes import research_brief as rb

    brief = _shape("brief")
    brief["studies"][0]["bars_met"] = "Meets 7 of 9 of your bars"
    out = rb.brief_text(brief, None)
    row = brief["studies"][0]
    assert (
        f"- {row['id']} · Check · {row['title']} · {row['state_words']} · Meets 7 of 9 of your bars"
        in out
    )


def test_stg_surfaces_2_a_bar_reads_its_served_sample_words_never_a_rebuild() -> None:
    """STG-SURFACES #2 (a Check of keel/btc_hold@1 on staging): every bar line read "sample not stated" although the
    row served its sample line. The bar line quotes the served ``sample_words`` (keel-api serves the line there, D9)
    and never rebuilds a sample from ``sample``. SEED (reversed): ``_sample`` rebuilding from ``sample`` again reds the
    second arm ("584 days" from a row that serves no words). Control: the first arm stays green under the seed.
    Non-vacuity: both rows carry a ``sample``."""
    served = {**_bar(0), "value_words": "1.9", "interval_words": "1.1 to 2.4", "bar_words": "your bar ≥ 0.30",
              "sample": {"first_day": "2025-11-17", "last_day": "2026-11-17"},
              "sample_words": "Nov 17, 2025 to Nov 17, 2026"}  # fmt: skip
    assert rv.bar_row(served, set()).endswith(
        "; Nov 17, 2025 to Nov 17, 2026; your bar ≥ 0.30; met"
    )
    unworded = {**served, "sample": {"days": 584}, "sample_words": None}
    # AUDIT-WORDS M7: with no served sample words the line says none, never "sample not stated"
    assert unworded["sample"] and "sample not stated" not in rv.bar_row(unworded, set())
    assert rv.bar_row(unworded, set()).endswith("1.1 to 2.4; your bar ≥ 0.30; met")


#: A Checks view's data row as keel-api serves it (``page_facts.checks_read`` over the study read's check row).
_CHECK_ROW = {
    "check": 0, "label": "Sharpe a day late", "bar_index": 0, "result": None, "at": None, "ref": "late",
    "measure": "sharpe", "value": 2.0912345678, "unit": "ratio",
    "interval": {"lo": 1.1, "hi": 2.4, "level": 0.9, "method": "hac"},
    "sample": {"first_day": "2025-11-17", "last_day": "2026-11-17"}, "op": ">=", "bar": 0.3, "met": True,
    "locked": False, "withheld_reason": None, "value_words": "2.09", "interval_words": "1.10 to 2.40",
    "sample_words": "Nov 17, 2025 to Nov 17, 2026", "sample_line": "Nov 17, 2025 to Nov 17, 2026",
    "bar_words": "your bar ≥ 0.30", "bar_default_words": "default ≥ 0.30",
    "stricter": {"bar": 0.36, "bar_words": "your bar ≥ 0.36", "met": True},
    "looser": {"bar": 0.24, "bar_words": "your bar ≥ 0.24", "met": True},
    "value_lo": 1.1, "value_hi": 2.4, "how": "Trades a day late", "runs": 2,
}  # fmt: skip


def test_stg_surfaces_3_a_checks_page_reads_as_the_bar_lines_never_its_internal_columns() -> None:
    """STG-SURFACES #3 (WL-AG-12): ``keel research data <stu> --view N`` on a Checks view printed Python reprs
    ({'lo': …}), raw floats and the rows' internal columns (bar_index, withheld_reason, stricter, looser). A Checks
    page reads as the study read's bar lines, every number in its served words. SEED (reversed): ``_is_checks_page``
    returning False -> this reds (the table prints ``bar_index`` and a repr). Control: the generic-page arm below.
    Non-vacuity: the row carries every internal column the seed would print."""
    page = {
        "data": [_CHECK_ROW],
        "facts": {"rows_total": 1, "axes": [{"name": "check", "label": "Check"}]},
    }
    text = rv.data_text(page, title="Your bars", version=1, call_args={})
    assert (
        "- Sharpe a day late: 2.09, 1.10 to 2.40; Nov 17, 2025 to Nov 17, 2026; your bar ≥ 0.30; met"
        in text
    )
    for internal in (
        "bar_index",
        "withheld_reason",
        "stricter",
        "looser",
        "{'",
        "2.0912345678",
        "None",
    ):
        assert internal not in text, internal


def test_stg_surfaces_3_ranges_marks_and_cells_read_their_served_words() -> None:
    """STG-SURFACES #3: "Across all …" and "Marks: … at -0.706…" printed the stored floats; each now reads its served
    words (page_facts: ``ranges.*.words``, ``marks[].at_words``), a nested column is never drawn (the app's table),
    and a cell no words are served for reads as text, never a repr. SEED (reversed): the range line reading
    ``low.get('value')`` again -> this reds. Control: a share frozen before the words reads its plain value."""
    page = {
        "data": [{"day": "2026-01-02", "growth": 1.2345, "detail": {"k": 1}, "words": {"growth": "1.23×"}}],
        "facts": {
            "rows_total": 1, "axes": [{"name": "day", "label": "Day"}],
            "measures": [{"name": "growth", "label": "Growth of 1", "unit": "multiple"}],
            "ranges": {"growth": {"low": {"value": 0.81234, "words": "0.81×"}, "high": {"value": 1.2345, "words": "1.23×"},
                                  "below_zero": None}},
            "marks": [{"kind": "hline", "at": -0.70612345, "label": "Final", "at_words": "−0.71"}],
        },
    }  # fmt: skip
    text = rv.data_text(page, title="Growth", version=1, call_args={})
    assert (
        "Across all 1 rows: Growth of 1 0.81× to 1.23×" in text
    )  # MCP-FINAL batch 3 #4: the label only
    assert "Marks: Final at −0.71" in text
    assert (
        "| Jan 2, 2026 | 1.23× |" in text
        and "2026-01-02" not in text
        and "detail" not in text
        and "{'" not in text
    )
    old = {**page, "facts": {**page["facts"], "marks": [{"kind": "hline", "at": 0.5, "label": ""}]}}
    assert "Marks: a line across at 0.5" in rv.data_text(
        old, title="Growth", version=1, call_args={}
    )


def test_stg_surfaces_3_a_view_given_as_text_names_its_chart() -> None:
    """STG-SURFACES #3: ``keel research data stu_… --view 0`` titled the rows "0 (version 1)": the CLI passes the
    index as text and the study read's view index is a number. SEED (reversed): matching ``index == view`` again ->
    the text arm reds. Control: the number arm and a title match."""
    from keel.tools.outcomes.research_get import _is_view

    row = {"index": 0, "title": "Your bars"}
    assert _is_view(row, "0") and _is_view(row, 0) and _is_view(row, "Your bars")
    assert not _is_view(row, "1") and not _is_view({"index": None, "title": "x"}, "None")


def test_stg_surfaces_6_the_cli_writes_every_next_step_as_a_keel_command(monkeypatch) -> None:
    """STG-SURFACES #6: ``keel research cancel`` printed "Resume later: keel_research_plan(…, resume=true)", an MCP
    call where every other CLI next step is a ``keel research …`` command (``literal_eval`` refused JSON's ``true``,
    so ``cli_text`` left it as written); the share read's "Run on yours: keel research plan --share-id X, with
    subjects=[…]" mixed the two forms. SEED (reversed): ``_call_literal`` back to ``ast.literal_eval`` -> the resume
    arm reds. Control: the share arm (no boolean) is a command under the seed."""
    from keel.tools.outcomes import _bootstrap
    from keel.tools.outcomes._research_commands import cli_text

    _bootstrap()
    resume = cli_text(
        "Resume later: " + rv.next_call("keel_research_plan", {"study_id": "stu_x", "resume": True})
    )
    assert resume == "Resume later: keel research plan stu_x --resume", resume
    share = cli_text(rv._share_next_lines({"study": {"share_id": "abc"}})[0])
    assert (
        share
        == "Run on yours: keel research plan --share-id abc --subjects 'Strategy(\"str_…@3\")' runs it on your own strategy"
    ), share
    assert "keel_" not in share and "subjects=" not in share


def test_d382_the_runs_line_prints_keels_share_and_later_tries_as_served() -> None:
    """EN-13 / D-382: the count is the version's frozen one; Keel's share ("including 3 tried by Keel") and the family's
    later tries ("2 more tried since") follow it in keel-api's words, never summed into it. SEED (reversed by
    reversing the edit): drop the ``extra`` words in ``_runs_line`` -> the served arm reds. Control: a count with no
    such words prints the count alone."""
    study = _study()
    vt = {**study["counts"]["versions_tried"], "keel_words": "including 3 tried by Keel"}
    vt = {**vt, "more_since": 2, "more_since_words": "2 more tried since"}
    text = rv.study_text({**study, "counts": {**study["counts"], "versions_tried": vt}})
    count = study["counts"]["versions_tried"]["count"]
    line = f"versions tried for momentum rotation: {count:,}"
    assert f"{line} (including 3 tried by Keel, 2 more tried since)" in text, text
    assert line in rv.study_text(study)  # control: the count alone
    assert "more tried since" not in rv.study_text(study)


def test_stg8_the_text_says_the_served_method_sentence() -> None:
    """STG-SURFACES #8, D-401: the text says the study read's served ``words.method`` (``research.words.method_line``,
    plain words), never its own list beside it. Seeded red: ignoring the served sentence prints "Measured with step 0,
    step 1, ...". Control: a read without it (an older server) names every step by its label, never a class name or
    a version ("Step0 v1")."""
    said = "Measured with the backtest and costs from none to six times."
    env = {**_study(), "words": {**(_study().get("words") or {}), "method": said}}
    lines = rv.study_text(env).splitlines()
    assert said in lines
    assert not any(line.startswith("Measured with step 0, step 1") for line in lines)
    bare = rv.study_text(_study())
    assert any(line.startswith("Measured with step 0, step 1") for line in bare.splitlines())
    assert " v1" not in bare.split("Measured with", 1)[1].splitlines()[0] and "Step0" not in bare


def test_polish_a_column_nothing_measured_is_left_out_and_named_once() -> None:
    """MCP-FINAL S3 (POLISH): a done map's Turnover read "not measured" in every cell. A column no row on the page
    measured is left out of the table and named once below it; a column measured on some rows stays. Seeded red:
    keeping every column draws "not measured" in each row. Control: Sharpe, measured, stays. Non-vacuity: 3 rows."""
    measures = [
        {"name": "sharpe", "label": "Sharpe", "unit": "ratio"},
        {"name": "turnover", "label": "Turnover", "unit": "ratio"},
        {"name": "cagr", "label": "CAGR", "unit": "fraction"},
    ]
    rows = [
        {"lookback": n, "sharpe": 0.8, "turnover": None, "cagr": c,
         "words": {"sharpe": "0.80", "turnover": "not measured", "cagr": w}}
        for n, c, w in ((7, 0.15, "15.0%"), (10, None, "not measured"), (14, 0.2, "20.0%"))
    ]  # fmt: skip
    page = {
        "data": rows,
        "pagination": {"cursor": None, "has_more": False},
        "facts": {"rows_total": 3, "axes": [], "measures": measures},
    }
    lines = rv.data_text(page, title="Map", version=1, call_args={}).splitlines()
    assert "| Lookback | Sharpe | CAGR |" in lines, lines
    assert "| 10 | 0.80 | not measured |" in lines  # measured on some rows: the column stays
    assert "Not measured on these rows: Turnover." in lines
    assert sum("not measured" in line for line in lines if line.startswith("| ")) == 1


def test_polish_a_stopped_read_says_the_stop_once_and_prints_its_served_reason() -> None:
    """MCP-FINAL S3 + SERVER-5 (POLISH): a stopped Map's read said "Stopped at 15 of 48 settings" in its first line
    and again below it with the receipt; and ``words.stopped`` (the engine-change reason) was never printed. The
    first line carries the tally; below it the served reason once. Seeded red: printing the progress line as before
    repeats the tally. Control: a read whose first line does not carry the tally keeps its progress line."""
    tally = "Stopped at 15 of 48 settings"
    reason = (
        "Keel's engine was updated during this run, so the rest of it didn't run. Resume sets it up again on the "
        "updated engine."
    )
    base = _study()
    env = {
        **base,
        "study": {**base["study"], "state": "completed_partial"},
        "card_summary": {**(base.get("card_summary") or {}), "state_words": tally},
        "progress": {
            "words": tally,
            "receipt": "Stopped. 15 settings done; nothing is charged for the rest.",
        },
        "words": {**(base.get("words") or {}), "stopped": reason},
    }
    lines = rv.study_text(env).splitlines()
    assert lines[0].endswith(tally)
    assert sum(tally in line for line in lines) == 1, lines
    assert sum(line == reason for line in lines) == 1, lines
    plain = {**env, "words": {**env["words"], "stopped": None}}
    assert sum(tally in line for line in rv.study_text(plain).splitlines()) == 1
    bare = {**plain, "card_summary": {**env["card_summary"], "state_words": "Stopped"}}
    assert any("nothing is charged" in line for line in rv.study_text(bare).splitlines())  # control
