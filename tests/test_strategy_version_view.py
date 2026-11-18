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


def _get(
    version: str = "HEAD",
    runs=None,
    listing_fails: bool = False,
    *,
    head_validation: dict | None = None,
    v1_validation: dict | None = None,
    include_source: bool = False,
    version_read_raises: Exception | None = None,
) -> dict:
    from keel.tools.outcomes import _bootstrap, get
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = MagicMock()

    def fake_get(path, **_kw):
        if path == f"/v1/strategies/{SID}":
            row = dict(RECORDED["metadata"])
            if head_validation is not None:
                # keel-api's `include=graph` read carries HEAD's verdict (Q-7605).
                row["validation"] = head_validation
            return row
        if version_read_raises is not None and "/versions/" in path:
            raise version_read_raises
        if path.endswith("/versions/HEAD/source") or path.endswith(f"/versions/{HEAD}/source"):
            return {"source": HEAD_SOURCE, "sequence_number": HEAD}
        # keel-api resolves `v1` / `V1` as sequence 1, exactly as `1` (Q-7657).
        if path.endswith(("/versions/1/source", "/versions/v1/source", "/versions/V1/source")):
            payload = {"source": V1_SOURCE, "sequence_number": 1}
            # keel-api serves a version's own verdict only when asked (Q-7610).
            if v1_validation is not None and _kw.get("include") == "validation":
                payload["validation"] = v1_validation
            return payload
        if path == "/v1/backtests":
            if listing_fails:
                raise RuntimeError("listing down")
            return {"data": runs or [], "pagination": {}}
        return {}

    client.get.side_effect = fake_get
    ctx = ToolContext(api_client=client, app_url="https://app.usekeel.io", is_tty=False)
    args = {"strategy_id": SID, "version": version, "skip_readiness": True}
    if include_source:
        args["include_source"] = True
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


_INVALID_GLOBAL = {
    "valid": False,
    "errors": [
        {
            "severity": "error",
            "code": "INVALID_GLOBAL",
            "message": "Globals target_timeframe '15m' is not a valid timeframe.",
            "location": "line 1, globals",
        }
    ],
    "warnings": [],
}
_CLEAN = {"valid": True, "errors": [], "warnings": []}


@pytest.mark.parametrize("include_source", [False, True])
@pytest.mark.parametrize(
    ("v1_validation", "word"),
    [
        (_INVALID_GLOBAL, "1 error"),
        (_CLEAN, "valid"),
        # An older keel-api serves no version verdict: the documented fallback.
        (None, "valid"),
    ],
)
def test_a_historical_version_reads_its_own_verdict(v1_validation, word, include_source) -> None:
    """Q-7610: `version=1` of a strategy whose v1 carries a validation error
    read `valid` under a valid HEAD — the version read served no verdict and
    the view reads absence as "compiled, so valid". keel-api now serves the
    version's own verdict under `include=validation`; HEAD's (valid) must not
    leak in. Both version reads are covered: the view's own and the
    `include_source` one it reuses.

    SEEDS (run 2026-11-16, each reversed by reversing the edit): see the
    Q-7610 ledger entry's Log."""
    env = _get(
        "1",
        RUNS,
        head_validation=_CLEAN,
        v1_validation=v1_validation,
        include_source=include_source,
    )
    errors = len(v1_validation["errors"]) if v1_validation else 0
    assert env["view"]["validation"] == {"ok": errors == 0, "errors": errors, "warnings": 0}
    first = env["view"]["markdown"].splitlines()[0]
    assert f"v1 · HEAD is v{HEAD} · {word} · " in first, first


def test_head_reads_heads_verdict_not_an_older_versions() -> None:
    """CONTROL: at HEAD the verdict is the strategy GET's (Q-7605), never a
    version read's — an invalid v1 does not touch a valid HEAD's header."""
    env = _get("HEAD", RUNS, head_validation=_CLEAN, v1_validation=_INVALID_GLOBAL)
    assert env["view"]["validation"] == {"ok": True, "errors": 0, "warnings": 0}
    assert f"v{HEAD} · valid · " in env["view"]["markdown"].splitlines()[0]


# ── Q-7657: an unread version is never answered with HEAD's view ──────────────
#
# `version="v1"` 404'd at keel-api (its resolver had no `vN` arm), the SDK's
# version read swallowed the 404, and `_version_view_inputs` read the missing
# payload as HEAD: the result was HEAD's view, "valid", with no word that v1
# was never read.
#
# SEEDS (run 2026-11-16, each reversed by reversing the exact edit):
# * `_read_version` degrades a non-HEAD 404 like HEAD's (`if True:` for
#   `if _is_head(version):`): `test_an_unknown_version_is_not_found` reds
#   (no raise); the `v1` arms and the HEAD controls stay green;
# * the "not read" branch skipped (`if False:`), restoring the HEAD fallback:
#   `test_a_failed_version_read_says_so_and_draws_no_head` reds (HEAD's view
#   is drawn under v1); the `v1` arms and the HEAD controls stay green.


@pytest.mark.parametrize("ref", ["v1", "V1"])
@pytest.mark.parametrize("include_source", [False, True])
def test_a_displayed_version_reads_that_version(ref, include_source) -> None:
    env = _get(
        ref,
        RUNS,
        head_validation=_CLEAN,
        v1_validation=_INVALID_GLOBAL,
        include_source=include_source,
    )
    view = env["view"]
    assert view["version"] == 1 and view["head_version"] == HEAD
    assert view["header"]["universe"]["label"].startswith("Top 10 by volume")
    first = view["markdown"].splitlines()[0]
    assert f"v1 · HEAD is v{HEAD} · 1 error · " in first, first
    assert "source_error" not in env
    assert ("source" in env) is include_source


def test_an_unknown_version_is_not_found() -> None:
    from keel.errors import NotFoundError

    with pytest.raises(NotFoundError) as caught:
        _get("v9", RUNS, version_read_raises=NotFoundError("version not found: v9"))
    err = caught.value
    assert "Version 'v9'" in str(err) and "version not found: v9" in str(err)
    assert err.recovery_tool == "keel_strategy_get"
    assert err.recovery_tool_args == {"strategy_id": SID, "include_versions": True}
    assert "include_versions=true" in (err.suggestion or "")


@pytest.mark.parametrize("include_source", [False, True])
def test_a_failed_version_read_says_so_and_draws_no_head(include_source) -> None:
    from keel.errors import KeelError

    env = _get(
        "v1",
        RUNS,
        head_validation=_CLEAN,
        include_source=include_source,
        version_read_raises=KeelError("HTTP 503: upstream unavailable"),
    )
    assert "view" not in env
    assert "source" not in env
    note = env["source_error"]
    assert "Version 'v1' was not read" in note and "HTTP 503" in note
    assert "HEAD's is not shown in its place" in note
    assert env["head_summary"]["version"] == HEAD
    # What the model reads: the note, and no HEAD structure under v1's name.
    text = _text(env)
    assert "Version 'v1' was not read" in text
    assert "Top 30 by volume" not in text


def test_a_failed_head_read_keeps_heads_view() -> None:
    """CONTROL: HEAD's view stands on the strategy GET, so a failed source
    read costs only the Code tab, exactly as before."""
    from keel.errors import KeelError

    env = _get("HEAD", RUNS, version_read_raises=KeelError("HTTP 503"))
    assert env["view"]["version"] == HEAD
    assert "source_error" not in env and "head_summary" not in env
