"""D-359 (5) (b), spec 12 §13 (R-02, D-329): the SDK half of the surface parity test. It reads SERVE's parity tree
(``libs/research/testing/surface_parity/``: one envelope per action and state, built by the real owners, and
``expected_strings.json``, what every surface must print byte for byte) and drives the SDK's own text builders, the
text every host and the CLI print (``view.markdown`` is the CLI's human output and the MCP text block alike):

- ``plan`` and ``refine``: ``plan_text``; ``run``, ``progress`` and ``result``: ``study_text``; ``stop``: the cancel
  tool's answer through its handler; ``limits``: the refusal through ``translate_http_error``, the MCP error result
  (``view_tool_result``) and the CLI's ``cli_refusal_text``.

Every expected string is accounted for exactly once:

- ``TEXT``: printed in the SDK text, asserted byte for byte;
- ``CARD``: a string the MCP study card draws from what the SDK forwards (``card_summary`` / ``card_data``), never
  composed in text (the card's own tests draw it); asserted to reach the card payload untouched.

Non-vacuity: the fixture count is pinned, every §13 action has a fixture, and ``expected_strings.json`` names exactly
the fixtures. SEED (reversed): ``plan_text`` re-cases the served allowance ("Running it used 1 of 3 …") -> the
plan_ready arm reds. Control: every other arm stays green under that seed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4] / "libs" / "research" / "testing" / "surface_parity"
pytestmark = pytest.mark.skipif(not ROOT.is_dir(), reason="the parity tree is the monorepo's (SERVE's WL-SV-23)")

ACTIONS = {"plan", "run", "progress", "result", "refine", "stop", "list", "limits"}

#: The card's strings: what a host draws from the forwarded card_summary / card_data, by fixture.
CARD = {
    "plan_ready": {"Plan · Check · Momentum rotation v3"},  # words.card_line: the plan row's own line
    "result_done": {
        "Meets 1 of 2 of your bars",
        "585 days · 100 coins · 9 bp a fill",  # the card's footing line; the text writes the footing in full
        "Sharpe (daily) over the window",
        "1.95",
        "585 days · 100 coins",
        "1.42 to 2.31",
        "Share of random books below it",
        "99.3%",
        "585 days · 150 books",
    },
    "list_page": {
        "Meets 1 of 2 of your bars",  # a row's card lead
        "Your studies · 3 studies · 1 running",  # the list card's head
        "+2 more studies",
    },
}

STUDY = {
    "study_id": "stu_parity00000000000000000",
    "title": "Momentum rotation v3",
    "version": 3,
    "kind_label": {"name": "Check a strategy", "short": "Check"},
}
LINK = {"app": "https://app.usekeel.io/research/stu_parity00000000000000000"}


def _fixtures() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted((ROOT / "fixtures").glob("*.json"))}


def _expected() -> dict[str, list[str]]:
    return json.loads((ROOT / "expected_strings.json").read_text())


def _progress(fx: dict) -> dict:
    """The parity envelope's progress as the study read serves it (D-349: ``progress.receipt``)."""
    progress = dict(fx.get("progress") or {})
    if fx.get("receipt"):
        progress["receipt"] = fx["receipt"]
    return progress


def _cancel_text(fx: dict, monkeypatch) -> str:
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    class Client:
        def post(self, path, json=None, *, headers=None, **params):
            return {"words": fx["words"], "links": LINK, "study_id": STUDY["study_id"]}

    _bootstrap()
    got = OUTCOMES["keel_research_cancel"].handler({"run_set_id": "rus_parity"}, ToolContext(api_client=Client()))
    return got.extra["view"]["markdown"]


def _list_text(fx: dict) -> str:
    """The list read through keel_research_get's handler: each served row's title and state words."""
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    rows = [
        {**r, "study_id": f"stu_parity{i:017d}", "kind_label": STUDY["kind_label"], "card_summary": {"lead": r.get("lead")}}
        for i, r in enumerate(fx.get("rows") or [])
    ]

    class Client:
        def get(self, path, *, headers=None, **params):
            return {"data": rows, "studies_total": 3, "pagination": {"cursor": None, "has_more": False}}

    _bootstrap()
    return OUTCOMES["keel_research_get"].handler({}, ToolContext(api_client=Client())).extra["view"]["markdown"]


def _limit_texts(fx: dict) -> list[str]:
    from keel.errors import translate_http_error
    from keel.tools.outcomes._mcp_adapter import view_tool_result
    from keel.tools.outcomes._research_commands import apply_research_refusal, cli_refusal_text

    words = fx["words"]
    body = {"detail": {"code": "quota_exhausted", "unit": "research_studies", "message": words["limit"],
                       "card_words": words["card_words"]}}  # fmt: skip
    err = translate_http_error(403, json.dumps(body))
    apply_research_refusal(err, {"study_id": STUDY["study_id"]}, app_url=LINK["app"], tool="keel_research_run")
    envelope = err.to_envelope()
    mcp = view_tool_result(json.dumps(envelope), tool_name="keel_research_run").content[0].text
    return [mcp, cli_refusal_text(envelope) or ""]


def _texts(name: str, fx: dict, monkeypatch) -> list[str]:
    from keel.tools.outcomes import _research_view as rv

    action = fx["action"]
    if action in ("plan", "refine"):
        env = {"words": fx.get("words") or {}, "study": STUDY, "plan_id": "pln_parity", "run_allowed": True,
               "edit": fx.get("edit"), "links": LINK}  # fmt: skip
        return [rv.plan_text(env)]
    if action in ("run", "progress", "result"):
        state = fx.get("state") or ("running" if action == "run" else "completed")
        env = {"study": {**STUDY, "state": state}, "progress": _progress(fx) or None, "headline": fx.get("headline"),
               "card_summary": fx.get("card_summary"), "links": LINK}  # fmt: skip
        if fx.get("words") and action == "progress":
            env["progress"] = {**(env["progress"] or {}), "receipt": fx.get("receipt") or fx["words"]}
        return [rv.study_text(env)]
    if action == "stop":
        return [_cancel_text(fx, monkeypatch)]
    if action == "limits":
        return _limit_texts(fx)
    if action == "list":
        return [_list_text(fx)]
    return []


def test_the_parity_tree_is_whole() -> None:
    fixtures, expected = _fixtures(), _expected()
    assert len(fixtures) == 14, sorted(fixtures)  # not vacuous: SERVE's tree as built
    assert set(expected) == set(fixtures)
    assert {fx["action"] for fx in fixtures.values()} == ACTIONS
    assert set(CARD) <= set(fixtures)
    for name, strings in CARD.items():
        assert strings <= set(expected[name]), (name, strings - set(expected[name]))


@pytest.mark.parametrize("name", sorted(_expected()) if ROOT.is_dir() else [])
def test_every_served_string_reaches_the_agent_byte_for_byte(name, monkeypatch) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    fx = _fixtures()[name]
    card = CARD.get(name, set())
    texts = _texts(name, fx, monkeypatch)
    forwarded = json.dumps({k: fx.get(k) for k in ("card_summary", "card_data", "words", "rows")}, ensure_ascii=False)
    for s in _expected()[name]:
        if s in card:
            assert s in forwarded, (name, s)  # the card draws it from what the SDK forwards
            continue
        assert texts, (name, "no SDK text for an action with TEXT strings")
        for text in texts:
            assert s in text, f"{name}: {s!r} not in the SDK text:\n{text}"
