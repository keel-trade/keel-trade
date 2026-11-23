"""Research's links, help, polling and review words (AUDIT batch 5: S3 #7, #8, #9, #13, #14).

- #7: ``keel_app_link`` refused a ``prj_`` id as an unknown prefix, though a project has a page.
- #8: ``keel research steps`` printed the catalog's internal id ("catalog keel.research.steps/1#43", 07 §3.2 rule 11).
- #9: the run's wait polled ``?card=full`` every 2 s, so keel-api rebuilt ``card_data`` ~45 times a call.
- #13: ``keel_research_run``'s destructiveHint justification led with "Append-only:" while saying a Draft is saved in
  its place.
- #14: ``keel signals --help`` listed its command as only "`keel blocks search --role signal`.".

SEEDS (each reversed by reversing the edit) are in the commit message.
"""

from __future__ import annotations

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


_bootstrap()


# ── #7 ────────────────────────────────────────────────────────────────────────


def test_app_link_links_a_project_to_its_page(monkeypatch) -> None:
    monkeypatch.setenv("KEEL_APP_URL", "https://app.usekeel.io")
    got = OUTCOMES["keel_app_link"].handler({"id": "prj_01abc"}, ToolContext())
    assert got.extra["url"] == "https://app.usekeel.io/research/projects/prj_01abc"
    assert got.extra["target_kind"] == "project_page"
    # control: a study still links its study page, and an unknown prefix is still refused
    study = OUTCOMES["keel_app_link"].handler({"id": "stu_01abc"}, ToolContext())
    assert study.extra["url"] == "https://app.usekeel.io/research/stu_01abc"
    from keel.errors import KeelError

    with pytest.raises(KeelError, match="unknown prefix"):
        OUTCOMES["keel_app_link"].handler({"id": "zzz_01abc"}, ToolContext())


# ── #8 ────────────────────────────────────────────────────────────────────────


def test_the_steps_listing_names_no_catalog_id() -> None:
    served = {
        "catalog_version": "keel.research.steps/1#43",
        "steps": [{"name": "Backtest", "version": 1, "about": "One run of the subject."}],
        "steps_total": 43,
    }

    class Client:
        def get(self, path, **params):
            return served

    ctx = ToolContext()
    ctx.get_client = lambda: Client()
    text = OUTCOMES["keel_research_steps"].handler({}, ctx).extra["view"]["markdown"]
    assert text.splitlines()[0] == "Steps (43)", text
    assert "keel.research.steps" not in text and "catalog" not in text
    # control: the rows are as served; AUDIT-WORDS M29: by the step's label, never "Backtest v1"
    assert "- Backtest: One run of the subject." in text and " v1" not in text


# ── #9 ────────────────────────────────────────────────────────────────────────


def test_the_run_wait_asks_for_the_card_on_its_last_read_only(monkeypatch) -> None:
    from keel.tools.outcomes import research_run

    monkeypatch.setattr(research_run.time, "sleep", lambda s: None)
    states = iter(["running"] * 5 + ["completed"])
    asked: list[dict] = []

    class Client:
        def post(self, path, json=None, headers=None, **params):
            return {
                "study": {"study_id": "stu_a", "version": 1, "state": "running"},
                "quota_notice": "q",
            }

        def get(self, path, headers=None, **params):
            asked.append(dict(params))
            state = next(states, "completed")
            return {"study": {"study_id": "stu_a", "version": 1, "state": state}, "links": {}}

    ctx = ToolContext()
    ctx.get_client = lambda: Client()
    env = research_run.wait_for(ctx, research_run.submit(ctx, {"plan_id": "pln_a"}), wait=True)
    assert len(asked) == 7, (
        asked
    )  # not vacuous: six polls to the terminal state, then the one card read
    assert [a.get("card") for a in asked] == [None] * 6 + ["full"], asked
    assert all(a.get("version") == 1 for a in asked)
    assert env["study"]["state"] == "completed" and env["quota_notice"] == "q"


def test_control_a_run_already_terminal_reads_nothing_more(monkeypatch) -> None:
    from keel.tools.outcomes import research_run

    class Client:
        def get(self, path, headers=None, **params):  # pragma: no cover - must not be called
            raise AssertionError("a terminal answer is not polled")

    ctx = ToolContext()
    ctx.get_client = lambda: Client()
    # SDK-FIX (J6): a terminal answer that carries its result is not read again (one without reads once, below)
    done = {
        "study": {"study_id": "stu_a", "version": 1, "state": "completed"},
        "headline": {"sentence": "x"},
    }
    assert research_run.wait_for(ctx, done, wait=True) is done


def test_a_run_done_at_its_answer_with_no_result_reads_it_once() -> None:
    """J6 w10 (staging fc5ff7629a): a Map whose 28 runs were all reused answered "28 of 28 runs done" and nothing
    else, as keel-api's run answer serves no headline. SEED (run, then reversed): the old early return for any
    terminal state -> no read (this reds); control: the test above (a result-carrying answer reads nothing)."""
    from keel.tools.outcomes import research_run

    calls = []

    class Client:
        def get(self, path, headers=None, **params):
            calls.append((path, params))
            return {"study": {"study_id": "stu_a", "version": 1, "state": "completed"},
                    "headline": {"sentence": "Highest of 25 settings: hold 3 · lookback 28 at 1.37"}}  # fmt: skip

    ctx = ToolContext()
    ctx.get_client = lambda: Client()
    done = {"study": {"study_id": "stu_a", "version": 1, "state": "completed"}, "quota_notice": "q"}
    got = research_run.wait_for(ctx, done, wait=True)
    assert len(calls) == 1 and calls[0][0] == "/v1/research/studies/stu_a", calls
    assert got["headline"]["sentence"].startswith("Highest of 25") and got["quota_notice"] == "q"


# ── #13 ───────────────────────────────────────────────────────────────────────


def test_the_run_justification_never_claims_append_only() -> None:
    from keel.tools.outcomes._annotation_justifications import (
        LISTED_RESEARCH_ANNOTATION_JUSTIFICATIONS,
    )

    row = LISTED_RESEARCH_ANNOTATION_JUSTIFICATIONS["keel_research_run"]
    why = row.destructive
    assert not why.lower().startswith("append-only"), why
    assert "saves a new study or a new study version" in why and "never-run Draft" in why
    # control: the mature append-only tools keep their label
    from keel.tools.outcomes._annotation_justifications import LISTED_ANNOTATION_JUSTIFICATIONS

    assert LISTED_ANNOTATION_JUSTIFICATIONS["keel_backtest_run"].destructive.startswith(
        "Append-only: "
    )


# ── #14 ───────────────────────────────────────────────────────────────────────


def test_keel_signals_help_says_what_the_command_does() -> None:
    from click.testing import CliRunner
    from keel.cli.main import cli

    group = CliRunner().invoke(cli, ["signals", "--help"], prog_name="keel")
    assert group.exit_code == 0, group.output
    listed = " ".join(group.output.split("Commands:")[1].split())
    # D-415 D5: `keel signals list` (your own signals) sits beside search, each saying what it does
    assert "search Find signals, Keel's library signals and your own" in listed, listed
    assert listed.startswith(
        "list List your signals, each with the exact call a study names it by."
    ), listed
    one = CliRunner().invoke(cli, ["signals", "search", "--help"], prog_name="keel")
    assert "The same as `keel blocks search --role signal`." in " ".join(one.output.split())


# ── #3's class, read on a TTY: a bare tool name on the CLI ────────────────────


def test_the_cli_text_names_a_bare_tool_by_its_command() -> None:
    import json
    from pathlib import Path

    from keel.tools.outcomes._research_commands import cli_text
    from keel.tools.outcomes._research_view import study_text

    shapes = json.loads(
        (Path(__file__).parent / "fixtures" / "research_server_shapes.json").read_text()
    )
    study = {k: v for k, v in shapes["study_planned"].items() if k != "_provenance"}
    mcp = study_text(study)
    assert (
        "keel_research_run runs that plan." in mcp
    )  # not vacuous: the MCP text names the tool bare
    cli = cli_text(mcp)
    # AUDIT-WORDS M34: on the CLI the Draft's own verb
    assert (
        "keel research run-draft " in cli and " runs it." in cli and "keel_research_run" not in cli
    ), cli
    # control: a name no CLI command answers to stays as written
    assert (
        cli_text("keel_nope and `keel_strategy_search`") == "keel_nope and `keel strategy search`"
    )
