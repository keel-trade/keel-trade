"""D-158: the agent text of a map in flight and of its rows as it fills in (07 §3.3, §3.5; 05 §6).

In flight, the study's first line ends with the served progress words and the text gives the call that reads it as it
fills in (07 §3.3's text). A data page with ``filling`` leads with its served words; the not-run (hatched) count is not
repeated while it fills in. A finished study and a finished page read as before. Seeded reds in the commit body.
"""

from __future__ import annotations

from keel.tools.outcomes import _research_view as rv


KIND = {"key": "map", "name": "Map a strategy's settings", "short": "Map"}


def _study(state: str, words: str | None) -> dict:
    env = {
        "study": {"study_id": "stu_map", "version": 1, "title": "Lookback × hold", "state": state,
                  "kind_label": KIND},
        "links": {"app": "https://app.usekeel.io/research/stu_map"},
    }  # fmt: skip
    if words is not None:
        env["progress"] = {"progress_seq": 212, "children": 386, "done": 214, "by_status": {},
                           "words": words}  # fmt: skip
    return env


def test_in_flight_the_first_line_carries_the_progress_and_the_read_call() -> None:
    text = rv.study_text(_study("running", "212 of 384 settings done"))
    lines = text.splitlines()
    assert (
        lines[0]
        == "Map a strategy's settings · Lookback × hold · version 1 · Running · 212 of 384 settings done"
    )
    assert 'keel_research_get(study_id="stu_map") reads it as it fills in.' in lines
    assert text.count("212 of 384 settings done") == 1


def test_a_finished_study_reads_as_before() -> None:
    text = rv.study_text(_study("completed", None))
    assert (
        text.splitlines()[0]
        == "Map a strategy's settings · Lookback × hold · version 1 · Done"  # AUDIT-WORDS M1: never the raw state
    )
    assert "fills in" not in text


def _page(filling: dict | None, cells: dict) -> dict:
    rows = [{"lookback": 2, "hold": 1, "status": "computed", "sharpe": 0.5},
            {"lookback": 2, "hold": 2, "status": "not_run", "sharpe": None}]  # fmt: skip
    return {
        "data": rows,
        "facts": {"rows_total": 384, "cells": cells},
        "filling": filling,
        "pagination": {"cursor": None, "has_more": False},
    }


def test_a_filling_page_leads_with_its_served_words() -> None:
    filling = {"state": "filling", "done": 212, "total": 384, "progress_seq": 212,
               "words": "212 of 384 settings done · the map fills in as runs finish"}  # fmt: skip
    text = rv.data_text(
        _page(filling, {"computed": 212, "not_run": 172}),
        title="Lookback × hold",
        version=1,
        call_args={"study_id": "stu_map", "view": 0},
    )
    lines = text.splitlines()
    assert lines[1] == "212 of 384 settings done · the map fills in as runs finish"
    assert lines[2] == "Rows 1–2 of 384 (212 measured)", (
        "the hatched rest is the words' remainder, not repeated"
    )


def test_a_stopped_page_keeps_its_not_run_count_and_a_written_page_reads_as_before() -> None:
    stopped = {"state": "stopped", "done": 212, "total": 384, "progress_seq": 212,
               "words": "212 of 384 settings · stopped"}  # fmt: skip
    text = rv.data_text(
        _page(stopped, {"computed": 212, "not_run": 172}), title="t", version=1, call_args={}
    )
    assert "212 of 384 settings · stopped" in text and "172 not run" in text
    written = rv.data_text(_page(None, {}), title="t", version=1, call_args={})
    assert written.splitlines()[1] == "Rows 1–2 of 384"
