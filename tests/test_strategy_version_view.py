"""The strategy view describes the version it is about, with representative
evidence (ChatGPT R4 #1 / #5 / #11, claude.ai R4 — Q-1879, Q-1884, Q-1885).

* `keel_strategy_get(version=1)` returned v1's SOURCE inside a view labelled
  with HEAD's version, drawing HEAD's structure and HEAD's evidence. Now every
  field describes v1 and HEAD rides apart (`head_summary`, `view.head_version`).
* The evidence is the newest FULL-window run of the version shown — not the
  newest run, which after a half-period split is a sub-window.
* Evidence from another version says so as a field
  (`evidence_matches_version: false`) and in the card's muted ink.
* The evidence's `net_of` comes from the backtest view's one owner:
  "carry", never "funding" beside the results' "carry".

Driven through the REAL handler and the REAL adapter.

SEEDS (run 2026-09-23, each reverted by reversing the exact edit):
* `_version_view_inputs` returns HEAD's graph and row for every version
  (`return row.get("graph"), row, None` after its first line) —
  `test_a_historical_version_is_described_as_itself` reds; the HEAD control
  and every evidence test stay green;
* `choose_evidence` picks the newest run regardless of window
  (`full = []`) — the full-window test reds; the all-sub-window arm and the
  listing-failure control stay green;
* `_evidence` hard-codes `"net_of": ["fees", "slippage", "funding"]` — the
  net-of test reds.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest


HERE = pathlib.Path(__file__).resolve().parent
RECORDED = json.loads((HERE / "fixtures" / "channels" / "strategy_get.envelope.json").read_text())
SID = RECORDED["metadata"]["id"]
HEAD = RECORDED["metadata"]["current_sequence"]  # 11
HEAD_SOURCE = RECORDED["view"]["source"]["text"]
#: v1: the same strategy on a Top 10 universe — a structure HEAD does not have.
V1_SOURCE = HEAD_SOURCE.replace("top_n=30", "top_n=10")
FULL = ("2024-07-27", "2026-09-23")
FIRST_HALF = ("2024-07-27", "2025-08-25")
SECOND_HALF = ("2025-08-25", "2026-09-23")


def _run(run_id: str, seq: int, window: tuple[str, str], completed: str, sharpe: float) -> dict:
    return {
        "id": run_id,
        "status": "completed",
        "sequence_number": seq,
        "start_date": window[0],
        "end_date": window[1],
        "completed_at": completed,
        "metrics": {
            "sharpe_ratio": sharpe,
            "total_return_pct": 10.0,
            "max_drawdown": 5.0,
            "total_trades": 42,
            "funding_included": True,
        },
    }


def _get(version: str = "HEAD", runs=None, listing_fails: bool = False) -> dict:
    from keel.tools.outcomes import _bootstrap, get
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = MagicMock()

    def fake_get(path, **_kw):
        if path == f"/v1/strategies/{SID}":
            return RECORDED["metadata"]
        if path.endswith("/versions/HEAD/source") or path.endswith(f"/versions/{HEAD}/source"):
            return {"source": HEAD_SOURCE, "sequence_number": HEAD}
        if path.endswith("/versions/1/source"):
            return {"source": V1_SOURCE, "sequence_number": 1}
        if path == "/v1/backtests":
            if listing_fails:
                raise RuntimeError("listing down")
            return {"data": runs or [], "pagination": {}}
        return {}

    client.get.side_effect = fake_get
    ctx = ToolContext(api_client=client, app_url="https://app.usekeel.io", is_tty=False)
    args = {"strategy_id": SID, "version": version, "skip_readiness": True}
    return get("keel_strategy_get").handler(args, ctx).to_envelope()


def _text(envelope: dict) -> str:
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    return view_tool_result(json.dumps(envelope, default=str), "keel_strategy_get").content[0].text


RUNS = [
    _run("btr_head_full", HEAD, FULL, "2026-09-23T10:00:00Z", 1.5),
    _run("btr_v1_full", 1, FULL, "2026-09-23T09:00:00Z", 0.7),
    _run("btr_head_half", HEAD, SECOND_HALF, "2026-09-23T11:00:00Z", 2.74),
]


def test_a_historical_version_is_described_as_itself() -> None:
    env = _get("1", RUNS)
    view = env["view"]
    assert view["version"] == 1
    assert view["head_version"] == HEAD
    assert view["header"]["universe"]["label"].startswith("Top 10 by volume")
    assert view["evidence"]["version"] == 1  # v1's own run, not HEAD's
    assert env["head_summary"]["version"] == HEAD
    first = view["markdown"].splitlines()[0]
    assert f"v1 · HEAD is v{HEAD}" in first, first
    # The model's text agrees with the structure it is shown.
    text = _text(env)
    assert "Top 10 by volume" in text and "Top 30" not in text


def test_head_is_described_as_head() -> None:
    """CONTROL: the default read is unchanged in shape — no HEAD aside."""
    env = _get("HEAD", RUNS)
    assert env["view"]["version"] == HEAD
    assert "head_version" not in env["view"] and "head_summary" not in env
    assert env["view"]["header"]["universe"]["label"].startswith("Top 30 by volume")


def test_the_evidence_is_the_full_window_run_not_the_latest_split() -> None:
    evidence = _get("HEAD", RUNS)["view"]["evidence"]
    # btr_head_half completed LATER, but it is a sub-window.
    assert evidence["window"]["start"] == FULL[0] and evidence["window"]["end"] == FULL[1]
    assert evidence["sharpe"] == 1.5
    assert "sub_window" not in evidence


def test_only_sub_window_runs_are_labelled() -> None:
    runs = [
        _run("btr_a", HEAD, FIRST_HALF, "2026-09-23T10:00:00Z", 1.33),
        _run("btr_b", HEAD, SECOND_HALF, "2026-09-23T11:00:00Z", 2.74),
        # a longer run of ANOTHER version sets the strategy's longest window
        _run("btr_c", 3, FULL, "2026-09-22T11:00:00Z", 0.9),
    ]
    env = _get("HEAD", runs)
    evidence = env["view"]["evidence"]
    assert evidence["sharpe"] == 2.74 and evidence["sub_window"] is True
    assert "(sub-window)" in env["view"]["markdown"]


def test_a_failed_listing_keeps_the_metadata_answer() -> None:
    """CONTROL: the listing is advisory — its failure is the old behaviour."""
    evidence = _get("HEAD", listing_fails=True)["view"]["evidence"]
    assert evidence["version"] == RECORDED["metadata"]["latest_backtest_sequence"]


def test_evidence_of_another_version_says_so_to_the_model() -> None:
    runs = [_run("btr_v2", 2, FULL, "2026-09-23T10:00:00Z", 1.1)]
    env = _get("HEAD", runs)
    assert env["view"]["evidence"]["matches_version"] is False
    text = _text(env)
    assert "\nevidence_matches_version: false — the backtest numbers are v2's run" in text
    # CONTROL: HEAD's own run carries no such key and no line.
    own = _get("HEAD", RUNS)
    assert "matches_version" not in own["view"]["evidence"]
    assert "evidence_matches_version" not in _text(own)


@pytest.mark.parametrize(("included", "expected"), [(True, "carry"), (False, None)])
def test_the_evidence_net_of_is_the_results_words(included: bool, expected) -> None:
    run = _run("btr_x", HEAD, FULL, "2026-09-23T10:00:00Z", 1.0)
    run["metrics"]["funding_included"] = included
    evidence = _get("HEAD", [run])["view"]["evidence"]
    assert "funding" not in evidence["net_of"]
    assert evidence["net_of"][:2] == ["fees", "slippage"]
    assert (evidence["net_of"][2:] or [None])[0] == expected
