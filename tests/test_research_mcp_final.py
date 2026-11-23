"""MCP-FINAL's SDK wording items (batches 2-4, 2026-11-20), each over the served shape a local server on research/r5
recorded against staging (scratchpad MCP-FINAL/results/). Each test names its seed (run, then reversed) and control.

- batch 2 #8: a bar with no number read "not measured, interval withheld: Not measured: … only 18 are active.;
  sample not stated; your bar ≥ 75th; not measured" (the reason twice, ".;", an empty sample worded as text).
- batch 2 #9: the brief's header counts were enum keys ("1 completed partial").
- batch 2 (cancel): a cancel of a COMPLETED run set offered "Resume later" for a plan that resumes nothing.
"""

from __future__ import annotations

from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes import _research_view as rv
from keel.tools.outcomes._base import ToolContext

from tests.test_research_tools import FakeClient


_bootstrap()

_WHY = (
    "Random books need at least twice as many active coins as the book holds: it holds as many as 20 at once, so it"
    " needs 40, and only 18 are active."
)
#: get-free-check1.json's first check row, as served.
_UNMEASURED = {
    "label": "Share of 150 random books below yours",
    "bar_index": 0,
    "ref": "luck.summary",
    "measure": "share_below",
    "unit": "rank",
    "sample": {},
    "op": ">=",
    "bar": 0.75,
    "withheld_reason": f"Not measured: {_WHY}",
    "locked": False,
    "value_words": "not measured",
    "bar_words": "your bar ≥ 75th",
}


def test_a_bar_with_no_number_says_why_once() -> None:
    """SEED (reversed): drop the no-number branch of ``bar_row`` -> the old line returns (this reds)."""
    line = rv.bar_row(_UNMEASURED, set())
    assert line == f"- {_UNMEASURED['label']}: not measured: {_WHY.rstrip('.')}; your bar ≥ 75th", (
        line
    )
    assert ".;" not in line and "sample not stated" not in line and line.count("Not measured") == 0


def test_control_a_measured_bar_reads_its_served_words() -> None:
    row = {
        **_UNMEASURED,
        "value": 0.81,
        "met": True,
        "value_words": "81st",
        "interval_words": "70th to 90th",
        "sample_words": "365 days",
        "withheld_reason": None,
    }
    line = rv.bar_row(row, set())
    assert line.startswith(f"- {row['label']}: 81st, 70th to 90th; 365 days; your bar ≥ 75th; "), (
        line
    )


def test_a_withheld_interval_reason_ends_in_no_doubled_stop() -> None:
    row = {
        **_UNMEASURED,
        "value": 0.5,
        "met": False,
        "value_words": "50th",
        "withheld_reason": "Too few periods.",
    }
    line = rv.bar_row(row, set())
    # AUDIT-WORDS M6: "no interval (why)"
    assert "no interval (Too few periods);" in line and ".;" not in line, line


def _brief(by_state: dict) -> str:
    client = FakeClient(
        {
            ("get", "/v1/research/brief"): {
                "studies_total": sum(by_state.values()),
                "by_state": by_state,
                "studies": [],
                "projects": [],
                "pagination": {"has_more": False, "cursor": None},
            }
        }
    )
    got = OUTCOMES["keel_research_brief"].handler({}, ToolContext(api_client=client))
    return got.extra["view"]["markdown"]


def test_the_brief_names_its_state_counts_in_words() -> None:
    """brief-trader.json's counts. SEED (reversed): the old ``str(k).replace('_', ' ')`` -> "1 completed partial"
    (this reds); control: the list header reads the same words (test_research_text_final)."""
    head = _brief({"completed": 1, "completed_partial": 1, "planned": 2}).splitlines()[0]
    assert "(2 planned, 1 done, 1 done with runs that didn't finish)" in head, head
    assert "completed" not in head and "_" not in head


def _cancel(env: dict) -> str:
    client = FakeClient({("post", "/v1/research/run-sets/"): env})
    got = OUTCOMES["keel_research_cancel"].handler(
        {"run_set_id": "rus_01m7x8hrcdek86f99h8spnj3th"}, ToolContext(api_client=client)
    )
    return got.extra["view"]["markdown"]


_DONE = {
    "run_set_id": "rus_01m7x8hrcdek86f99h8spnj3th",
    "study_id": "stu_01m7x8hr9d3xq2zsa2qk36mnka",
    "state": "completed",
    "progress": {"children": 12, "done": 12, "words": "Stopped at 9 of 9 settings"},
    "words": "Stopped. 9 settings done; nothing is charged for the rest.",
    "links": {"app": "https://app.usekeel.io/research/stu_01m7x8hr9d3xq2zsa2qk36mnka"},
}


def test_a_cancel_of_a_completed_run_set_offers_no_resume() -> None:
    """cancel-pro-done.json. SEED (reversed): ``if study_id:`` without ``_left_to_run`` -> "Resume later" (this reds)."""
    text = _cancel(_DONE)
    assert "Resume later" not in text and "resume" not in text, text
    assert text.splitlines()[-1].startswith("View in Keel: ")


def test_control_a_cancel_that_stopped_runs_offers_the_resume() -> None:
    stopped = {
        **_DONE,
        "state": "cancelled",
        "progress": {"children": 12, "done": 5, "words": "Stopped at 5 of 12"},
    }
    assert "resume=true)" in _cancel(stopped)


# ── batch 2 #2-#5: the refusals' next steps ─────────────────────────────────────────────────────────────────────────

import json  # noqa: E402

from keel.errors import translate_http_error  # noqa: E402
from keel.tools.outcomes._research_commands import (  # noqa: E402
    apply_research_refusal,
    refusal_text,
)


_APP = "https://app.usekeel.io/research"


def _refused(status: int, detail: dict, args: dict, tool: str) -> dict:
    err = translate_http_error(status, json.dumps({"detail": detail}))
    return apply_research_refusal(err, args, app_url=_APP, tool=tool).to_envelope()


def test_the_map_size_wall_says_narrow_not_wait() -> None:
    """run-trader-mapwall.json. SEED (reversed): drop the RESEARCH_SIZE_UNITS arm -> "before a slot frees" (this
    reds); control: a studies wall (research_studies, consumable) still says wait for the reset."""
    wall = {
        "code": "quota_cap_reached",
        "unit": "research_backtests_max",
        "message": "This map has 36 settings; the most this account's plan can run is 25. Narrow an axis.",
    }
    env = _refused(403, wall, {"plan_id": "pln_a"}, "keel_research_run")
    assert (
        "slot frees" not in env["what_was_expected"]
        and "narrow an axis" in env["what_was_expected"]
    ), env
    studies = {
        "code": "quota_exhausted",
        "unit": "research_studies",
        "message": "You have used 3 of 3 studies.",
    }
    assert "the reset" in _refused(403, studies, {}, "keel_research_run")["what_was_expected"]


def test_an_unpinned_subject_read_keeps_its_study_code() -> None:
    """get-pro-subject.json: a 404 nesting ``STUDY_SUBJECT_NOT_FOUND`` read top-level ``not_found``. SEED (reversed):
    only ``RESEARCH_`` codes typed -> "not_found" (this reds); control: a non-research 404 stays ``not_found``."""
    served = {
        "code": "STUDY_SUBJECT_NOT_FOUND",
        "message": "The address read takes a strategy version.",
    }
    env = _refused(404, served, {"subject": 'Strategy("keel/x")'}, "keel_research_get")
    assert env["code"] == "STUDY_SUBJECT_NOT_FOUND" and env["message"] == served["message"], env
    plain = translate_http_error(404, json.dumps({"detail": "Strategy not found"})).to_envelope()
    assert plain["code"] == "not_found"


def test_a_name_the_study_does_not_hold_points_to_its_valid_names() -> None:
    """ms-pro-bad.json (make_signal, subject "nope"). SEED (reversed): drop the ``detail.valid`` arm -> the next step
    is keel_help(topic="research") again (this reds); control: with no valid list the help topic stays."""
    served = {"code": "STUDY_SUBJECT_NOT_FOUND", "message": "nope is not a mix in this study.",
              "valid": ["mix_equal", "mix_skill"]}  # fmt: skip
    env = _refused(
        422, served, {"study_id": "stu_a", "subject": "nope"}, "keel_research_make_signal"
    )
    # POLISH (MCP-FINAL S3): the next call is the same call on the first valid name, never {"tool": null}
    assert env["suggested_next_action"]["tool"] == "keel_research_make_signal", env
    assert env["suggested_next_action"]["args"] == {"study_id": "stu_a", "subject": "mix_equal"}, (
        env
    )
    text = refusal_text(env)
    assert "valid (2): mix_equal, mix_skill" in text and "keel_help" not in text, text
    # POLISH: a study that holds no mix (ms-pro-notcompare.json, a Map) points to the action that makes one, a Compare
    # of signals (its SignalMix step), never the help topic
    bare = {k: v for k, v in served.items() if k != "valid"}
    none = _refused(422, bare, {"study_id": "stu_a", "subject": "s1"}, "keel_research_make_signal")
    assert none["suggested_next_action"]["tool"] == "keel_research_plan", none
    assert none["suggested_next_action"]["args"] == {"template": "compare"}
    assert "SignalMix" in none["suggested_next_action"]["reason"]
    # control: the same code from an address read keeps the help topic
    other = _refused(404, bare, {"subject": 'Strategy("keel/x")'}, "keel_research_get")
    assert other["suggested_next_action"]["tool"] == "keel_help"


def test_polish_a_bad_run_id_names_a_run_and_a_direct_run_without_max_runs_names_its_plan() -> None:
    """MCP-FINAL S3 (POLISH): cancel(bad rus_) said "A study id you can read" (cancel-pro-badrus.json); a direct run
    with no max_runs carried {"tool": null} (run-trader-direct-nomax.json). SEEDS (reversed): drop the run_set_id arm
    -> the run reason reds; leave the usage error bare -> the plan arm reds. Control: a bad study id keeps its words."""
    served = {"code": "RESEARCH_STUDY_NOT_FOUND", "message": "This run isn't one of yours."}
    env = _refused(404, served, {"run_set_id": "rus_x"}, "keel_research_cancel")
    reason = env["suggested_next_action"]["reason"]
    assert reason.startswith("A run of yours") and "study id" not in reason, env
    study = _refused(404, served, {"study_id": "stu_x"}, "keel_research_cancel")
    assert (
        study["suggested_next_action"]["reason"]
        == "A study id you can read; this lists your studies:"
    )
    from keel.errors import KeelError
    from keel.tools.outcomes import research_run

    form = {"template": "check", "subjects": ['Strategy("str_a@1")']}
    try:
        research_run._handler(dict(form), None)
    except KeelError as err:
        action = err.to_envelope()["suggested_next_action"]
    else:  # pragma: no cover - the handler must refuse
        raise AssertionError("a direct run without max_runs ran")
    assert action["tool"] == "keel_research_plan" and action["args"] == form, action


def _plan_refused(issue: dict, subjects: list[str]) -> dict:
    detail = {"code": issue["code"], "issues": [{**issue, "severity": "error"}]}
    return _refused(422, detail, {"template": "check", "subjects": subjects}, "keel_research_plan")


def test_a_refused_plan_names_the_read_that_fixes_it() -> None:
    """MCP-FINAL batch 2 #2: eight plan refusals (plan-pro-*.json) carried ``{"tool": null}`` and the generic "Change
    that input and retry". SEED (reversed): ``_study_issue_row`` returns None -> the three arms red; control: a plan
    wall (quota) keeps no tool (D-14: a plan limit has no automated next step)."""
    ref = 'Strategy("str_01m7xaj2r199y53hyfr8tyr260@1")'
    addr = _plan_refused({"code": "STUDY_ADDRESS_NOT_FOUND", "path": "/steps/map/axes/0", "message": "No such address."},
                         [ref])  # fmt: skip
    assert addr["suggested_next_action"] == {
        "tool": "keel_research_get",
        "args": {"subject": ref},
        "reason": "An address the subject has; this lists them:",
    }, addr
    lib = _plan_refused({"code": "STUDY_LIBRARY_REF_UNKNOWN", "path": "/subjects/s1/args/0", "message": "Not an item."},
                        ['Strategy("keel/momentum_hyperliqid@1")'])  # fmt: skip
    assert lib["suggested_next_action"]["tool"] == "keel_research_get"
    assert lib["suggested_next_action"]["args"] == {"library": "strategies"}
    syntax = _plan_refused(
        {"code": "STUDY_SYNTAX", "path": "/", "message": "This is not valid study text."}, []
    )
    assert syntax["suggested_next_action"]["tool"] == "keel_help", syntax
    assert "Change that input and retry" not in syntax["what_was_expected"]
    wall = {
        "code": "quota_exhausted",
        "unit": "research_studies",
        "message": "You have used 3 of 3 studies.",
    }
    assert _refused(403, wall, {}, "keel_research_plan")["suggested_next_action"] == {
        "tool": None,
        "args": {},
    }


# ── SERVER-2's plan words (served since 8ad213cd9c) ─────────────────────────────────────────────────────────────────

_SIZE = (
    "Every setting: the map it reads has 36 settings; the most this account's plan can run is 25."
)


def _plan(**words) -> dict:
    return {
        "plan_id": "pln_a",
        "study": {"title": "Momentum rotation v3", "kind_label": {"name": "Map settings"}},
        "writes": "version 2",
        "words": {"work": "15 runs, 9 reused · under a minute", **words},
        "steps": [
            {
                "id": "map",
                "label": "Every setting",
                "runnable": False,
                "locked": {"unit": "u", "words": _SIZE},
            },
            {"id": "summary", "label": "Summary", "runnable": True, "to_run": "15 runs"},
        ],
        "links": {"app": "https://app.usekeel.io/research"},
    }


def test_a_plan_prints_its_served_engine_words() -> None:
    """SEED (reversed): drop "engine" from plan_text's served keys -> the line is gone (this reds); control: a plan
    served with no engine words prints no engine line."""
    engine = "Keel's engine changed since version 1: 15 runs"
    assert engine in rv.plan_text(_plan(engine=engine)).splitlines()
    assert "engine" not in rv.plan_text(_plan()).lower()


def test_a_locked_step_reads_its_served_words() -> None:
    """SEED (reversed): the old ``f"{label}: {NOT_INCLUDED}"`` -> "Every setting: not included in this account's plan"
    (this reds); control: a locked step served with no words keeps the bare reason."""
    steps = next(line for line in rv.plan_text(_plan()).splitlines() if line.startswith("Steps ("))
    assert _SIZE.rstrip(".") in steps and "not included" not in steps, steps
    bare = _plan()
    bare["steps"][0] = {"id": "map", "label": "Every setting", "runnable": False}
    steps = next(line for line in rv.plan_text(bare).splitlines() if line.startswith("Steps ("))
    assert "Every setting: not included in this account's plan" in steps


# ── batch 3: the CLI's words ─────────────────────────────────────────────────────────────────────────────────────────

from keel.tools.outcomes._research_commands import cli_call, cli_text  # noqa: E402


def test_the_cli_lists_studies_with_research_list() -> None:
    """#2 (notfound.txt: "this lists your studies: keel research get"). SEED (reversed): drop the list arm of
    ``cli_call`` -> "keel research get" (this reds); control: a get naming a study stays ``keel research get``."""
    assert cli_call("keel_research_get", {}) == "keel research list"
    assert cli_call("keel_research_get", {"study_id": "stu_a"}).startswith(
        "keel research get stu_a"
    )


def test_a_share_read_on_the_cli_offers_only_the_copy_command() -> None:
    """#1 (share-read.txt). SEED (reversed): drop the copy rewrite in ``cli_text`` -> "keel app open … (the CLI: …)"
    (this reds); control: the MCP text keeps keel_app_link with the CLI's command beside it."""
    share = "AbCdEfGhIjKlMnOpQrStUvW"
    mcp = f'Make it yours, a copy with nothing run: keel_app_link(id="{share}") (the CLI: keel research copy {share})'
    assert cli_text(mcp) == f"Make it yours, a copy with nothing run: keel research copy {share}"
    assert "keel_app_link" in mcp


def test_the_cancel_confirm_says_what_a_cancel_does(monkeypatch) -> None:
    """New S3 (MCP-FINAL cli-r2): `keel research cancel` confirmed "a change that may not be undone", though a stopped
    study resumes. SEED (reversed): drop the CONFIRM_WORDS row -> the old words (this reds); control: another
    destructive verb keeps them."""
    import click
    from keel.tools.outcomes._cli_adapter import _confirm_destructive

    asked: list[str] = []
    monkeypatch.setattr(click, "confirm", lambda text, abort: asked.append(text))
    _confirm_destructive(OUTCOMES["keel_research_cancel"], {"run_set_id": "rus_a"})
    _confirm_destructive(OUTCOMES["keel_research_delete"], {"study_id": "stu_a"})
    assert "can be resumed" in asked[0] and "may not be undone" not in asked[0], asked[0]
    assert "may not be undone" in asked[1]


# ── r2: the address read's header ────────────────────────────────────────────────────────────────────────────────────


def test_the_address_header_counts_reasons_in_words(monkeypatch) -> None:
    """MCP-FINAL r2 ("1 inert, 28 settable, 3 wiring"). SEED (reversed): the old ``k.replace('_', ' ')`` -> the keys
    return (this reds); control/non-vacuity: every reason keel-api serves (libs/research/addresses.py WHY_NOT, plus
    "settable") has words."""
    import re
    from pathlib import Path

    from keel.tools.outcomes.research_get import ADDRESS_REASON_WORDS

    from tests.test_research_view import _run, _shape

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    page = _shape("addresses")
    _, text = _run(
        "keel_research_get", {"subject": page["subject"]}, {("get", "/v1/research/addresses"): page}
    )
    head = text.splitlines()[0]
    assert head.endswith(
        "(3 with no effect as the strategy stands, 15 you can set, 2 that wire the strategy together)"
    ), head
    src = (Path(__file__).resolve().parents[4] / "libs/research/addresses.py").read_text()
    served = re.search(r"WHY_NOT: tuple\[str, \.\.\.\] = \(([^)]*)\)", src)
    assert served is not None
    keys = set(re.findall(r'"([a-z_]+)"', served.group(1))) | {"settable"}
    assert len(keys) >= 8 and keys == set(ADDRESS_REASON_WORDS), keys ^ set(ADDRESS_REASON_WORDS)


def test_a_json_refusal_on_the_cli_names_its_keel_command(capsys) -> None:
    """Batch 3 #3 (notfound-json.txt: "tool": "keel_research_get"). SEED (reversed): ``_emit_research_refusal``
    returns False for a structured format (the old early return) -> no ``command`` (this reds); control: a refusal
    with no next tool prints the envelope unchanged through ``emit_error``."""
    from keel.tools.outcomes import OUTCOMES as _O
    from keel.tools.outcomes._cli_adapter import _emit_research_refusal

    env_err = translate_http_error(
        404, json.dumps({"detail": {"code": "RESEARCH_STUDY_NOT_FOUND", "message": "No."}})
    )
    apply_research_refusal(env_err, {"study_id": "stu_a"}, app_url=_APP, tool="keel_research_get")
    assert _emit_research_refusal(_O["keel_research_get"], env_err, "json") is True
    printed = json.loads(capsys.readouterr().err)
    action = printed["suggested_next_action"]
    assert action["command"] == "keel research list" and action["tool"] == "keel_research_get", (
        action
    )
    assert "keel_" not in action["reason"]
    wall = translate_http_error(403, json.dumps({"detail": {"code": "quota_exhausted", "unit": "research_studies",
                                                            "message": "Used 3 of 3."}}))  # fmt: skip
    apply_research_refusal(wall, {}, app_url=_APP, tool="keel_research_run")
    assert _emit_research_refusal(_O["keel_research_run"], wall, "json") is False


# ── batch 4 (D9): the text says each number as its tile draws it ──────────────────────────────────────────────────


def _tile_lines(env: dict) -> list[tuple[dict, str]]:
    """Each tile of ``card_summary.numbers`` with the text line that says the same number (its row's label)."""
    rows = {
        (r.get("ref"), r.get("measure")): r for r in (env.get("checks") or {}).get("rows") or []
    }
    text = rv.study_text(env).splitlines()
    out = []
    for tile in (env.get("card_summary") or {}).get("numbers") or []:
        row = rows.get((tile.get("ref"), tile.get("measure")))
        if row is None:
            continue
        out.append((tile, next(line for line in text if line.startswith(f"- {row['label']}: "))))
    return out


def test_every_number_s_text_string_is_its_card_string() -> None:
    """MCP-FINAL batch 4: on 7 staging studies 4 of 27 strings a Check disagreed between the tile and the text (the
    row's own words). Over the fixture study, with its rows' words made to disagree the way staging served them,
    every tile's value_words and sample_words are the text's. SEED (reversed): ``_bars_lines`` without ``_as_drawn``
    (the old composer: the row's own words) -> this reds; control: the unmodified fixture, whose rows and tiles
    agree, reads the same (non-vacuity: at least three tiles matched a row)."""
    import copy

    from tests.test_research_view import _shape

    agree = _shape("study_done")
    pairs = _tile_lines(agree)
    assert len(pairs) >= 3, len(pairs)
    for tile, line in pairs:
        assert tile["value_words"] in line and tile["sample_words"] in line, (tile, line)
    served = copy.deepcopy(agree)
    for row in served["checks"]["rows"]:
        row["value_words"] = f"{row.get('value_words')} (row)"
        row["sample_words"] = "Nov 17, 2025 to Nov 17, 2026"
    for tile, line in _tile_lines(served):
        assert tile["value_words"] in line and tile["sample_words"] in line, (tile, line)
        assert "(row)" not in line and "Nov 17, 2025" not in line, line


def test_fix13_a_cancel_given_a_study_id_stops_that_studys_run() -> None:
    """FINAL-JOURNEYS S3-11: `keel research cancel stu_… --yes` sent the study id as a run set; keel-api refused it by
    its pattern with a hint about strategy source. A study id in the run set's place names the study: its run set is
    read from the study and cancelled. SEED (reversed by reversing the edit): no stu_ rewrite -> the POST goes to
    /run-sets/stu_… (this reds). CONTROL: a rus_ id posts as before; anything else is refused in words, unsent."""
    import pytest
    from keel.errors import KeelError

    sid, rus = "stu_01m7x8hr9d3xq2zsa2qk36mnka", "rus_01m7x8hrcdek86f99h8spnj3th"
    client = FakeClient(
        {
            ("get", f"/v1/research/studies/{sid}"): {"study": {"run_set_id": rus}},
            ("post", "/v1/research/run-sets/"): {**_DONE, "state": "cancelled"},
        }
    )
    OUTCOMES["keel_research_cancel"].handler({"run_set_id": sid}, ToolContext(api_client=client))
    posted = [p for m, p, _ in client.calls if m == "post"]
    assert posted == [f"/v1/research/run-sets/{rus}/cancel"], client.calls
    # control: a run set id is posted as given, with no study read
    control = FakeClient({("post", "/v1/research/run-sets/"): _DONE})
    OUTCOMES["keel_research_cancel"].handler({"run_set_id": rus}, ToolContext(api_client=control))
    assert [m for m, _, _ in control.calls] == ["post"]
    # anything else is refused in words, before any call
    other = FakeClient({})
    with pytest.raises(KeelError) as got:
        OUTCOMES["keel_research_cancel"].handler(
            {"run_set_id": "x1"}, ToolContext(api_client=other)
        )
    assert "stu_" in str(got.value) and other.calls == []
