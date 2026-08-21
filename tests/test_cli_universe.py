"""Tests for `keel universe resolve` — the v16 staging-audit §6.1 defects.

The CLI is the surface an operator actually types at, so the two
regressions are pinned here end-to-end as well as at the tool layer
(`test_tools_local.py`):

  (a) declared selectors — notably ``lookback`` — must reach the resolve
      request. Dropping ``lookback="90d"`` silently ranks on the server's
      7-day default and returns a DIFFERENT, wrong asset list.
  (b) writing the resolved list back must be a span edit. Flagship
      strategy files carry 100+-line changelog headers; the old canonical
      re-emission deleted every one of them.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from keel.cli.main import cli


HEADER = """\
# ══════════════════════════════════════════════════════════════════
# flagship v16 (C1, s=1.20)
#
# CHANGELOG
#   2026-07-04  seeded the venue leverage snapshot (191 names)
#   2026-08-01  top_n bumped to the full perp pool
#
# DO NOT canonically re-emit this file — the header IS the record.
# ══════════════════════════════════════════════════════════════════
"""

STRATEGY = (
    HEADER
    + """\
Globals(target_timeframe="1d")  # daily bars
Universe(
    mode="top_volume",
    market="perp",
    top_n=3,
    lookback="90d",  # 90-day volume ranking, NOT the 7d default
    volume_quartiles=["q1"],
)
Execution(rebalance="every_bar")
Pipeline([
    PriceDataLoader(timeframe="15min"),  # loader keeps its comment
    ROC(period=8),
])
"""
)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def stub_api(monkeypatch):
    """Stub KeelClient.post; records the body, returns a fixed resolution."""
    captured: dict = {}

    class _StubClient:
        def post(self, path: str, json: dict):
            captured["path"] = path
            captured["body"] = json
            return {
                "resolved": ["BTC", "ETH", "SOL"],
                "resolved_at": "2026-08-02T00:00:00+00:00",
                "count": 3,
            }

    monkeypatch.setattr("keel.client.KeelClient", _StubClient)
    return captured


def test_resolve_forwards_declared_lookback(runner, tmp_path, stub_api):
    path = tmp_path / "flagship.strategy"
    path.write_text(STRATEGY)

    result = runner.invoke(cli, ["universe", "resolve", str(path)])

    assert result.exit_code == 0, result.output
    assert stub_api["path"] == "/v1/universe/resolve"
    assert stub_api["body"]["lookback"] == "90d"
    assert stub_api["body"]["volume_quartiles"] == ["q1"]
    assert stub_api["body"]["top_n"] == 3


def test_resolve_write_back_preserves_the_comment_header(runner, tmp_path, stub_api):
    path = tmp_path / "flagship.strategy"
    path.write_text(STRATEGY)

    result = runner.invoke(cli, ["universe", "resolve", str(path)])
    assert result.exit_code == 0, result.output

    written = path.read_text()

    # Byte-for-byte outside the edited Universe(...) span.
    head_end = STRATEGY.index("Universe(")
    assert written[:head_end] == STRATEGY[:head_end]
    assert written.endswith(STRATEGY[STRATEGY.index("Execution(") :])
    assert HEADER in written

    # The resolution landed, and the write-back was NOT a reformat.
    assert "resolved_at" in written
    assert "BTC" in written
    payload = json.loads(result.output)
    assert payload["wrote_back"] is True
    assert payload["reformatted"] is False
    assert payload["resolved_count"] == 3


def test_resolve_stdin_form_preserves_comments(runner, stub_api):
    result = runner.invoke(cli, ["universe", "resolve", "-"], input=STRATEGY)

    assert result.exit_code == 0, result.output
    assert HEADER in result.output
    assert "# loader keeps its comment" in result.output


def test_deprecated_flag_form_forwards_lookback(runner, stub_api):
    """The deprecated `--mode` form had no way to express `lookback` at
    all — every call silently ranked on 7 days."""
    result = runner.invoke(
        cli,
        [
            "universe",
            "resolve",
            "--mode",
            "top_volume",
            "--top-n",
            "3",
            "--lookback",
            "90d",
            "--volume-quartiles",
            "q1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert stub_api["body"]["lookback"] == "90d"
    assert stub_api["body"]["volume_quartiles"] == ["q1"]
    assert stub_api["body"]["mode"] == "top_volume"
