"""A version compare names what each version changed (Q-1792).

claude.ai, staging dev-f960c13: "create mean-reversion top 20, then 10-day
and 30-day variants, backtest all, compare". The agent saved v1/v2/v3 as
VERSIONS of one strategy; the comparison's columns read "v1 BASELINE · v2 ·
v3" and nothing said v2 was the 10-day variant. The labeller could only see
Execution/Globals declaration keys — a pipeline block's param (the
lookback, `TimeSeriesMeanReversionForecast.k`) was invisible to it, and the
commit message every save now writes (Q-1752) was never read.

Every envelope here comes from the REAL `keel_backtest_compare` handler (HTTP
faked at `KeelClient.get`, sources and version records served per commit),
and the card arms render that envelope in real Chromium through
``tests/fixtures/cards/c_version_labels_check.mjs``. The card arms skip
(never silently pass) without node or Playwright.

Proof it can fail: ``# SEED:`` per test, run 2026-09-22, recorded in the
commit. Proof it is not vacuous: the sources really resolved (the spec diff
names the `k` change) and the card drew one column per run — quantities no
seed below can move.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from unittest.mock import patch

import pytest
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import backtest_compare as _bt_cmp_mod  # noqa: F401
from keel.tools.outcomes._base import ToolContext
from keel.widgets import build_card_html


HERE = pathlib.Path(__file__).resolve().parent
CHECK = HERE / "fixtures" / "cards" / "c_version_labels_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"

#: The founder's set: one strategy, three versions, only `k` differs.
_SRC = """\
Globals(target_timeframe="1d")
Universe(mode="dynamic", top_n=20)
Execution(rebalance="every_bar")
Pipeline([
    PriceDataLoader(timeframe="1d"),
    TimeSeriesMeanReversionForecast(k={k}),
    ForecastScaler(),
    ForecastWeightNormalizer(),
])
"""
_KS = (20, 10, 30)

#: What Q-1752's save writes: the change against the PARENT version.
_MESSAGES = (
    "Create Mean reversion top 20",
    "TimeSeriesMeanReversionForecast · k 20 → 10",
    "TimeSeriesMeanReversionForecast · k 10 → 30",
)


def _detail(i: int, strategy_id: str = "str_mr", name: str = "Mean reversion top 20") -> dict:
    return {
        "id": f"btr_{strategy_id}_{i}",
        "status": "COMPLETED",
        "strategy_id": strategy_id,
        "strategy_name": name,
        "sequence_number": i + 1,
        "commit_id": f"c_{strategy_id}_{i}",
        "engine": "native",
        "start_date": "2024-08-15",
        "end_date": "2026-09-22",
        "metrics": {
            "sharpe_ratio": 0.8 + 0.1 * i,
            "total_return": 20.0 + i,
            "max_drawdown": -12.0 - i,
            "win_rate": 51.0 + i,
        },
    }


def _curve(i: int) -> dict:
    points = [
        {"t": f"2025-{m:02d}-01T00:00:00", "equity": 10000.0 * (1 + 0.01 * m * (i + 1))}
        for m in range(1, 13)
    ]
    return {"points": points, "start": "2025-01-01", "end": "2025-12-01", "source_points": 12}


def _envelope(details: list[dict], sources: dict, messages: dict) -> dict:
    by_id = {d["id"]: (i, d) for i, d in enumerate(details)}

    def fake_get(path, **_kw):
        if path.startswith("/v1/backtests/") and path.endswith("/curve"):
            return _curve(by_id[path.split("/")[3]][0])
        if path.startswith("/v1/backtests/"):
            return by_id[path.rsplit("/", 1)[1]][1]
        for d in details:
            base = f"/v1/strategies/{d['strategy_id']}/versions/{d['commit_id']}"
            if path == f"{base}/source":
                return {"source": sources[d["commit_id"]]}
            if path == base:
                return {"message": messages.get(d["commit_id"])}
        raise AssertionError(f"unexpected GET {path}")

    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    with patch("keel.client.KeelClient.get", side_effect=fake_get):
        return (
            OUTCOMES["keel_backtest_compare"]
            .handler({"backtest_ids": [d["id"] for d in details]}, ctx)
            .to_envelope()
        )


def _versions_envelope(*, src=_SRC, messages=_MESSAGES) -> dict:
    details = [_detail(i) for i in range(3)]
    sources = {d["commit_id"]: src.format(k=k) for d, k in zip(details, _KS)}
    return _envelope(details, sources, {d["commit_id"]: m for d, m in zip(details, messages)})


def _message_only_envelope() -> dict:
    """Three blocks changed at once (k, and two added blocks): no short
    structural label exists, so the commit messages name the versions."""
    details = [_detail(i) for i in range(3)]
    extra = ["", "    ForecastCapper(),\n", "    ForecastCapper(),\n    ForecastCapper(),\n"]
    sources = {
        d["commit_id"]: _SRC.format(k=k).replace(
            "    ForecastScaler(),\n", "    ForecastScaler(),\n" + extra[i]
        )
        for i, (d, k) in enumerate(zip(details, _KS))
    }
    messages = (
        "Create Mean reversion top 20",
        "Shorter lookback, capped",
        "Longer lookback, capped twice over",
    )
    return _envelope(details, sources, {d["commit_id"]: m for d, m in zip(details, messages)})


def _cross_envelope() -> dict:
    """CONTROL: runs of different strategies keep their strategy names."""
    details = [
        _detail(0, "str_aaa", "Mean reversion 20D"),
        _detail(1, "str_bbb", "Mean reversion 10D"),
    ]
    sources = {d["commit_id"]: _SRC.format(k=k) for d, k in zip(details, _KS)}
    return _envelope(details, sources, {})


def _labels(env: dict) -> list[str]:
    return [run["label"] for run in env["view"]["runs"]]


WANT = ["v1 · k 20", "v2 · k 10", "v3 · k 30"]


def test_the_sources_really_resolved() -> None:
    """Non-vacuity: the structural diff SAW the `k` change on both
    variants, and the set is three runs of one strategy — quantities the
    label seeds below cannot move."""
    env = _versions_envelope()
    assert len(env["view"]["runs"]) == 3
    assert env["strategy_id"] == "str_mr"
    for diff, (old, new) in zip(env["spec_diffs"][1:], ((20, 10), (20, 30))):
        changed = diff["pipeline"]["changed"]
        assert [c["component"] for c in changed] == ["TimeSeriesMeanReversionForecast"]
        assert changed[0]["param_changes"] == [{"param": "k", "old": old, "new": new}]


def test_a_version_compare_names_each_versions_block_param() -> None:
    """The founder's set: the one block param that varies names EVERY run,
    the baseline included — in the envelope, the markdown table header
    (claude.ai's only model channel) and the summary line."""
    # SEED: in backtest_compare.py `_block_terms`, make the first line of
    # the body `return []` — the labels fall back to the commit messages
    # and this reds.
    env = _versions_envelope()
    assert _labels(env) == WANT
    header = env["view"]["markdown"].splitlines()[1]
    assert header == "| | " + " | ".join(WANT) + " |", header
    assert all(label in env["summary_text"] for label in WANT)


def test_the_commit_message_names_a_version_when_no_short_diff_does() -> None:
    """Three changes at once have no two-term label; the commit message
    each save writes (Q-1752) is the fallback, cut to whole words."""
    # SEED: in `_version_labels` change `if len({m for m in messages if m}) > 1:`
    # to `if False:` — the columns read bare "v1 · v2 · v3" again and this reds.
    env = _message_only_envelope()
    labels = _labels(env)
    # The create default reads "original" (Q-1802): "v1 · Create <name>"
    # repeated the card's header and said nothing about v1.
    # SEED: in `_version_message` return `message` unconditionally — this reds.
    assert labels == [
        "v1 · original",
        "v2 · Shorter lookback, capped",
        "v3 · Longer lookback, capped twice…",
    ], labels
    # A derived Q-1752 message too long for a label keeps its change.
    from keel.tools.outcomes.backtest_compare import _message_label

    assert _message_label(_MESSAGES[1]) == "k 20 → 10"
    assert _message_label("Execution · buffer 0.1 → 0.2") == "Execution · buffer 0.1 → 0.2"
    # Control arm: messages that do not tell the runs apart are not used.
    same = _versions_envelope(src=_SRC.replace("k={k}", "k=4"), messages=("save",) * 3)
    assert _labels(same) == ["v1", "v2", "v3"]


def test_an_unnameable_param_is_never_put_into_a_label() -> None:
    """A param name is a component author's identifier, not vetted copy: a
    name carrying a token the listed surface rejects is not a label word."""
    from keel.tools.outcomes.backtest_compare import _param_word

    assert _param_word("k") == "k"
    assert _param_word("vol_window") == "vol window"
    assert _param_word("min_trade_size") is None
    assert _param_word("funding_weight") is None


def test_a_cross_strategy_compare_keeps_its_strategy_names() -> None:
    """CONTROL: only a ONE-strategy compare is labelled by its changes."""
    assert _labels(_cross_envelope()) == ["Mean reversion 20D", "Mean reversion 10D"]


# ── The card shows the server's label ─────────────────────────────────


@pytest.fixture(scope="module")
def shown(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; what a column shows is a DOM rule")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    work = tmp_path_factory.mktemp("version-labels")
    (work / "compare.html").write_text(build_card_html("compare"), encoding="utf-8")
    envs = {
        "versions": _versions_envelope(),
        "messages": _message_only_envelope(),
        "cross": _cross_envelope(),
    }
    (work / "envelopes.json").write_text(json.dumps(envs), encoding="utf-8")
    proc = subprocess.run(
        [
            "node",
            str(CHECK),
            "--cards",
            str(work),
            "--envelopes",
            str(work / "envelopes.json"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the version-label check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def test_the_card_drew_every_run(shown: dict) -> None:
    """Non-vacuity: one column, one legend entry and one narrow block per
    run — a count the label seeds cannot move."""
    for arm, n in (("versions", 3), ("messages", 3), ("cross", 2)):
        assert len(shown[arm]["wide"]["columns"]) == n, (arm, shown[arm]["wide"])
        assert len(shown[arm]["wide"]["legend"]) == n, (arm, shown[arm]["wide"])
        assert len(shown[arm]["narrow"]["leads"]) == n, (arm, shown[arm]["narrow"])


def test_the_card_names_each_version_by_its_change(shown: dict) -> None:
    """The column headers, the legend and the narrow blocks read the
    server's label, whole: "v2 · k 10", not "v2"."""
    # SEED: in card-compare.js `table()` change `el("th", "run", labelOf(r))`
    # to `el("th", "run", labelOf(r).split(" · ")[0])` — the columns read
    # "v1 · v2 · v3" again and this reds while the legend stays right.
    wide = shown["versions"]["wide"]
    assert wide["columns"] == WANT, wide
    assert not any(wide["clipped"]), wide
    assert wide["legend"] == WANT, wide
    assert shown["versions"]["narrow"]["leads"] == WANT, shown["versions"]["narrow"]
    # The commit-message fallback: a long label may be cut by the column,
    # never lost — the whole label stays in the header's title.
    msg = shown["messages"]["wide"]
    assert msg["titles"] == _labels(_message_only_envelope()), msg
    assert [c.split(" · ")[0] for c in msg["columns"]] == ["v1", "v2", "v3"], msg
    # CONTROL: cross-strategy names are the Q-1780 shared-stem labels.
    assert shown["cross"]["wide"]["columns"] == ["20D", "10D"], shown["cross"]["wide"]
