"""A study's text in its served words (AUDIT batch 5: S3 #4, #5, #6, #11, #12; 07 §3.2, §3.5, §4.4; D-329).

- #4: a Draft's line composed its own words and never printed the served ``study.draft.words`` or, for a Draft that
  cannot run as it stands, the served reason (and still offered the run call).
- #5: the list header printed state keys ("1 completed partial", "2 cancel requested").
- #6: a share's "Run on yours" offered ``Strategy("str_…@3")`` for a Signal or Compare share too.
- #11: a data range dropped its served ``at`` and (in the golden) its served words.
- #12: the empty list's "Start one" named a placeholder subject with no way to find a real one.

Over keel-api's recorded shapes (``fixtures/research_server_shapes.json``, the mocked world's study, share and data
pages). SEEDS (each reversed by reversing the edit) are in the commit message.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from keel.tools.outcomes import _research_view as rv
from keel.tools.outcomes.research_get import STATE_COUNT_WORDS


SHAPES = json.loads(
    (Path(__file__).parent / "fixtures" / "research_server_shapes.json").read_text()
)
REPO = Path(__file__).resolve().parents[4]


def _shape(name: str) -> dict:
    return json.loads(json.dumps({k: v for k, v in SHAPES[name].items() if k != "_provenance"}))


def _with_draft(**draft) -> dict:
    study = _shape("study_planned")
    study["study"]["draft"] = {**study["study"]["draft"], **draft}
    return study


# ── #4: the Draft ─────────────────────────────────────────────────────────────


def test_a_draft_line_carries_its_served_run_words() -> None:
    study = _with_draft(words="24 runs; the rest reused", can_run=True)
    line = next(x for x in rv.study_text(study).splitlines() if "saved Draft" in x)
    assert line.startswith(
        f"Version {study['study']['version']} is a saved Draft that has not run yet: 24 runs; the rest reused."
    ), line
    assert "keel_research_plan(" in line  # it can run: its next call is there


def test_a_draft_that_cannot_run_says_why_and_offers_no_run() -> None:
    said = "Newer data is in since this was saved."
    study = _with_draft(words=None, can_run=False, reason=said)
    line = next(x for x in rv.study_text(study).splitlines() if "saved Draft" in x)
    assert (
        line == f"Version {study['study']['version']} is a saved Draft that has not run yet. {said}"
    ), line
    assert "keel_research_run" not in line and "keel_research_plan(" not in line


def test_control_a_draft_with_nothing_served_reads_as_before() -> None:
    study = _with_draft(words=None, can_run=True, reason=None)
    line = next(x for x in rv.study_text(study).splitlines() if "saved Draft" in x)
    assert "not run yet. keel_research_plan(" in line, line


# ── #5: the list header ───────────────────────────────────────────────────────


def test_the_state_words_cover_every_state_keel_api_serves() -> None:
    schema = (REPO / "services/keel-api/src/schemas/research.py").read_text()
    block = re.search(r"StudyState = Literal\[(.*?)\]", schema, re.S)
    assert block, "StudyState literal not found"
    served = set(re.findall(r'"([a-z_]+)"', block.group(1)))
    assert len(served) >= 7  # not vacuous: the literal was read
    assert set(STATE_COUNT_WORDS) == served


def test_the_list_header_names_states_in_words(monkeypatch) -> None:
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()

    class Client:
        def get(self, path, headers=None, **params):
            return {
                "data": [],
                "studies_total": 4,
                "by_state": {
                    "completed_partial": 1,
                    "cancel_requested": 2,
                    "completed": 1,
                    "failed": 0,
                },
                "pagination": {"has_more": False, "cursor": None},
            }

    got = OUTCOMES["keel_research_get"].handler({}, ToolContext(api_client=Client()))
    head = got.extra["view"]["markdown"].splitlines()[0]
    assert (
        head == "Studies: 0 shown of 4 (2 stopping, 1 done, 1 done with runs that didn't finish)"
    ), head
    assert "_" not in head and "completed" not in head


# ── #6: Run on yours ──────────────────────────────────────────────────────────


def _share(kind: str, subjects: list[tuple[str, str]]) -> dict:
    share = _shape("share")
    share["study"]["kind_label"] = {**share["study"]["kind_label"], "key": kind}
    share["subjects"] = [{"kind": k, "label": lbl, "ref": lbl, "key": None, "name": None, "edits": []}
                         for k, lbl in subjects]  # fmt: skip
    return share


def _run_line(share: dict) -> str:
    return next(x for x in rv.study_text(share).splitlines() if x.startswith("Run on yours"))


def test_run_on_yours_names_the_share_s_own_subject_kind() -> None:
    signal = _run_line(_share("signal", [("output", "28-day momentum")]))
    assert 'subjects=["Output(\\"str_…@1\\", output=\\"…\\")"]' in signal, signal
    assert signal.endswith("runs it on your own signal") and "Strategy(" not in signal
    compare = _run_line(_share("compare", [("output", "7-day momentum"), ("output", "Funding carry"),
                                           ("derived", "Mix of both")]))  # fmt: skip
    assert compare.count("Output(") == 2 and compare.endswith(
        "on your own signals (two to eight)"
    ), compare


def test_control_a_check_share_still_offers_a_strategy() -> None:
    check = _run_line(_shape("share"))
    assert 'subjects=["Strategy(\\"str_…@3\\")"]' in check and check.endswith(
        "on your own strategy"
    )


# ── #11: data ranges ──────────────────────────────────────────────────────────


def test_a_range_names_where_each_end_is() -> None:
    page = _shape("data_page")
    ranges = [x for x in rv.data_text(page, title="Growth of 1", version=1, call_args={}).splitlines()
              if x.startswith("Across")]  # fmt: skip
    assert ranges == [
        "Across all 585 rows: Growth of 1 0.96× (Day Mar 28, 2025) to 1.98× (Day Sep 19, 2026)"  # POLISH: days in words
    ]
    grid = _shape("data_page_map")
    sharpe = next(x for x in rv.data_text(grid, title="Map", version=1, call_args={}).splitlines()
                  if x.startswith("Across all") and ": Sharpe " in x)  # fmt: skip
    assert "−1.73 (hold 1, lookback 2) to 1.95 (hold 28, lookback 10)" in sharpe, sharpe
    assert not re.search(r"\d\.\d{3,}", sharpe)  # no raw float: the served words


def test_control_a_range_served_with_no_at_reads_its_ends_only() -> None:
    page = _shape("data_page")
    for end in page["facts"]["ranges"]["growth"].values():
        if isinstance(end, dict):
            end.pop("at", None)
    line = next(
        x
        for x in rv.data_text(page, title="G", version=1, call_args={}).splitlines()
        if x.startswith("Across")
    )
    assert line == "Across all 585 rows: Growth of 1 0.96× to 1.98×"


# ── #12: the empty list ───────────────────────────────────────────────────────


@pytest.mark.parametrize("total", [0, 3])
def test_the_empty_list_points_to_the_reader_s_strategies(total) -> None:
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()

    class Client:
        def get(self, path, headers=None, **params):
            rows = [{"study_id": f"stu_{i}", "title": "T", "kind_label": {"short": "Check"}, "state_words": "Done"}
                    for i in range(total)]  # fmt: skip
            return {
                "data": rows,
                "studies_total": total,
                "by_state": {},
                "pagination": {"has_more": False},
            }

    text = (
        OUTCOMES["keel_research_get"]
        .handler({}, ToolContext(api_client=Client()))
        .extra["view"]["markdown"]
    )
    start = [x for x in text.splitlines() if x.startswith("Start one")]
    if total:
        assert start == []  # control: a list with studies has no start line
        return
    assert start == [
        'Start one: keel_strategy_search() lists your strategies; keel_research_plan(template="check", '
        'subjects=["Strategy(\\"str_…@3\\")"]) checks one, with its id and version in place of str_…@3.'
    ], start
