"""Latency budgets for the two tools a strategy loop spends its time in (Q-1886).

The founder bar (2026-09-23): the agent-surface-cleanup batch made
`keel_backtest_run` take 15-36 s for runs the worker finished in 2.4-5.7 s,
and `keel_backtest_compare` 12.9-32.8 s where it had taken 0.4 s. The bounds
here are the ABSOLUTE ones that bar names, not "faster than before":

* `keel_backtest_run(wait=true)` returns within the worker's time + 1 s;
* `keel_backtest_compare` over 2-4 runs returns within 0.5 s;
* neither makes a price read: no `/curve?references=true`, no hold line in the
  result (founder, 2026-09-23 — a hold line is asked for via compare's `holds`,
  never volunteered); the opt-in compare has its own, looser bound.

keel-api is a fake whose every endpoint SLEEPS its measured in-cluster latency
(staging access logs, 2026-09-23: a run row ~20 ms, a source ~80 ms, the run
listing ~30 ms; `/curve` 250 ms — its S3 `results.json` read — and +400 ms when
the hold lines are asked for), and whose run row turns COMPLETED `WORKER_S`
after the submit. Its `/curve` ALWAYS carries `references`, as an older
keel-api does, so an SDK that projected lines unasked would show one. A budget
failure here is the SDK's own wall clock: a polling cadence that oversleeps a
finished run, or reads that wait on each other when they need not.

    SEED (run 2026-09-23): `_POLL_FAST_INTERVAL_S = 3.0` in backtest_run.py
    (the pre-fix flat cadence) → the run budget arm red (found at 3.0 s for a
    2.0 s worker, +1.3 s over); the compare arms stay green.
    SEED (run): `parallel_map` → `return [fn(item) for item in items]` in
    _backtest_view.py → the compare budget arm red (4 x 250 ms of `/curve`
    in sequence); the run arm stays green (its three reads fit its 1 s slack
    even in sequence — its bound is the cadence's).
    SEED (run): `fetch_curve_and_reference` passes `holds=HOLD_SYMBOLS` →
    the run arm red (a `references=true` read, a hold line in the result);
    the compare arms stay green (compare reads through `fetch_curve_and_holds`).
    SEED (run): compare passes `("BTC",)` for run 1 whatever `holds` says →
    both default compare arms red; the run arm stays green.
Non-vacuity: every arm asserts the fake was actually exercised — the run
polled until COMPLETED and read its curve; the compare read every run's curve.

The compare arms judge the MEDIAN of `COMPARE_SAMPLES` warmed calls, after a
`gc.collect()` (Q-2210). Their bound leaves 90 ms over 410 ms of serial fake
sleeps, and the suite's ~2,800 earlier tests leave a ~240k-object heap whose
full collection measured 40-120 ms: one pause, or one slow runner tick, in a
single sample reddened SDK Test five times with no change in the SDK's own
work (L2's per-call cost measured +0.3 ms CPU). A structural regression
moves every sample, so the median keeps every SEED above red.
    SEED (run 2026-09-30, Q-2210): a one-shot 150 ms stall in the fake's
    first timed `/curve` read → both single-sample arms red (0.58 s,
    0.59 s); every median arm green. Against the median, the sequential
    `parallel_map` seed reds all three compare arms (0.82 / 1.60 / 1.62 s),
    the `("BTC",)` seed both default arms, and a 30 ms sleep per
    `parallel_map` call (~+120 ms of constant work) both default arms at
    0.56 s; the run arm stays green through all four.
"""

from __future__ import annotations

import gc
import statistics
import threading
import time
from unittest.mock import patch

import pytest
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes import backtest_compare as _bt_cmp_mod  # noqa: F401
from keel.tools.outcomes import backtest_run as _bt_run_mod  # noqa: F401
from keel.tools.outcomes._base import ToolContext

from .test_outcomes_backtest_compare import METRICS_A, METRICS_B, SRC_A, SRC_B


#: The worker's time from submit to COMPLETED (staging: 2.4-5.7 s).
WORKER_S = 2.0
#: `keel_backtest_run(wait=true)` may exceed the worker by at most this.
RUN_SLACK_S = 1.0
#: `keel_backtest_compare` over 2-4 runs (R3 pre-batch measured 0.4 s).
COMPARE_BUDGET_S = 0.5

LATENCY_S = {
    "run_row": 0.02,
    "curve": 0.25,
    "source": 0.08,
    "listing": 0.03,
    "other": 0.03,
    "hold_lines": 0.4,
}
#: The opt-in compare (`holds`) — one extra server-side price read.
COMPARE_WITH_HOLDS_BUDGET_S = 1.0
#: Warmed compare calls per arm; the arm judges their median (Q-2210).
COMPARE_SAMPLES = 3

_CURVE = {
    "points": [
        {"t": f"2025-0{m}-01T00:00:00+00:00", "equity": 10000.0 + 100 * m, "drawdown_pct": -1.0}
        for m in range(1, 10)
    ],
    "start": "2025-01-01",
    "end": "2025-09-01",
    "source_points": 5000,
    "references": [
        {
            "label": "BTC hold",
            "basis": "price only",
            "symbol": "BTC",
            "ret_pct": 12.5,
            "dd_pct": -20.0,
            "start_close": 90000.0,
            "end_close": 101250.0,
            "rebased_at": "2025-01-01",
            "end_close_at": "2025-08-31",
            "series": [
                {"t": "2025-01-01", "equity": 10000.0},
                {"t": "2025-08-31", "equity": 11250.0},
            ],
        },
        None,
        None,
    ],
}


class FakeKeelApi:
    """keel-api at its measured latencies, with a worker that takes WORKER_S."""

    def __init__(self, details: dict[str, dict]):
        self.details = details
        self.submitted_at: float | None = None
        self.calls: list[str] = []
        self.curve_params: list[dict] = []
        self._lock = threading.Lock()

    def _record(self, path: str) -> None:
        with self._lock:
            self.calls.append(path)

    def post(self, path, json=None, **_kw):
        assert path == "/v1/backtests"
        self.submitted_at = time.monotonic()
        return {"id": "btr_run", "status": "queued", "strategy_id": "str_1"}

    def get(self, path, **kw):
        self._record(path)
        if path.endswith("/curve"):
            with self._lock:
                self.curve_params.append(dict(kw))
            time.sleep(LATENCY_S["curve"])
            if kw.get("references") == "true":
                time.sleep(LATENCY_S["hold_lines"])
            return _CURVE
        if "/versions/" in path and path.endswith("/source"):
            time.sleep(LATENCY_S["source"])
            commit = path.split("/versions/")[1].split("/")[0]
            return {"source": SRC_A if commit.endswith("a") else SRC_B}
        if path == "/v1/backtests":
            time.sleep(LATENCY_S["listing"])
            return {"items": []}
        if path.startswith("/v1/backtests/"):
            time.sleep(LATENCY_S["run_row"])
            run_id = path.rsplit("/", 1)[1]
            if run_id == "btr_run":
                done = time.monotonic() - (self.submitted_at or 0) >= WORKER_S
                return self.details[run_id] if done else {"id": run_id, "status": "RUNNING"}
            return self.details[run_id]
        time.sleep(LATENCY_S["other"])
        return {}


def _detail(run_id: str, commit: str, metrics: dict, seq: int) -> dict:
    return {
        "id": run_id,
        "status": "COMPLETED",
        "strategy_id": "str_1",
        "strategy_name": "Momentum",
        "commit_id": commit,
        "sequence_number": seq,
        "engine": "native",
        "start_date": "2025-01-01",
        "end_date": "2025-09-01",
        "completed_at": "2026-09-23T22:00:00+00:00",
        "execution_time": 1.9,
        "metrics": metrics,
    }


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


def _patched(api: FakeKeelApi):
    return (
        patch("keel.client.KeelClient.get", side_effect=api.get),
        patch("keel.client.KeelClient.post", side_effect=api.post),
    )


def _timed_compares(api: FakeKeelApi, args: dict, ctx) -> tuple[float, list[dict], list[list[str]]]:
    """The median wall clock of `COMPARE_SAMPLES` warmed compare calls.

    One call first: the component registry's load (~0.3 s, the spec diff's
    first use) is once per PROCESS — a hosted server pays it once — and the
    budget is per call. Then one `gc.collect()`: the collector's debt is the
    earlier tests' heap, not this call's work (Q-2210). Returns the median,
    every envelope, and every sample's calls (for the non-vacuity asserts).
    """
    get_patch, post_patch = _patched(api)
    elapsed, envs, calls = [], [], []
    with get_patch, post_patch:
        OUTCOMES["keel_backtest_compare"].handler(args, ctx)
        gc.collect()
        for _ in range(COMPARE_SAMPLES):
            api.calls.clear()
            api.curve_params.clear()
            t0 = time.monotonic()
            envs.append(OUTCOMES["keel_backtest_compare"].handler(args, ctx).to_envelope())
            elapsed.append(time.monotonic() - t0)
            calls.append(list(api.calls))
    return statistics.median(elapsed), envs, calls


def test_backtest_run_wait_returns_within_worker_time_plus_one_second(ctx):
    api = FakeKeelApi({"btr_run": _detail("btr_run", "cmt_a", METRICS_A, 2)})
    get_patch, post_patch = _patched(api)
    with get_patch, post_patch:
        t0 = time.monotonic()
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler({"strategy_id": "str_1", "end_date": "2025-09-01"}, ctx)
            .to_envelope()
        )
        elapsed = time.monotonic() - t0

    # Non-vacuity: the run was polled to COMPLETED and its curve was read.
    assert env["status"] == "completed"
    assert "/v1/backtests/btr_run/curve" in api.calls
    assert api.calls.count("/v1/backtests/btr_run") >= 2
    assert elapsed >= WORKER_S
    # The default path reads no prices and shows no hold line.
    assert api.curve_params and all("references" not in k for k in api.curve_params)
    assert "reference" not in env
    assert elapsed <= WORKER_S + RUN_SLACK_S, (
        f"keel_backtest_run took {elapsed:.2f}s for a {WORKER_S:.1f}s worker "
        f"(budget {WORKER_S + RUN_SLACK_S:.1f}s)"
    )


@pytest.mark.parametrize("n_runs", [2, 4])
def test_backtest_compare_returns_within_half_a_second(ctx, n_runs):
    ids = [f"btr_{i:026d}" for i in range(n_runs)]
    details = {
        run_id: _detail(
            run_id,
            "cmt_a" if i % 2 == 0 else "cmt_b",
            METRICS_A if i % 2 == 0 else METRICS_B,
            i + 1,
        )
        for i, run_id in enumerate(ids)
    }
    api = FakeKeelApi(details)
    elapsed, envs, calls = _timed_compares(api, {"backtest_ids": ids}, ctx)

    # Non-vacuity: every sample read every run's curve and drew a comparison.
    assert len(envs) == len(calls) == COMPARE_SAMPLES
    for env, sample in zip(envs, calls, strict=True):
        curve_reads = [c for c in sample if c.endswith("/curve")]
        assert sorted(curve_reads) == sorted(f"/v1/backtests/{i}/curve" for i in ids)
        assert env["view"]["kind"] == "comparison"
        assert "reference" not in env and "references" not in env
    assert all("references" not in k for k in api.curve_params)
    assert elapsed <= COMPARE_BUDGET_S, (
        f"keel_backtest_compare over {n_runs} runs took {elapsed:.2f}s "
        f"(median of {COMPARE_SAMPLES}; budget {COMPARE_BUDGET_S:.1f}s)"
    )


def test_compare_with_holds_reads_them_once_within_its_own_bound(ctx):
    ids = [f"btr_{i:026d}" for i in range(3)]
    details = {
        run_id: _detail(run_id, "cmt_a" if i % 2 == 0 else "cmt_b", METRICS_A, i + 1)
        for i, run_id in enumerate(ids)
    }
    api = FakeKeelApi(details)
    elapsed, envs, _calls = _timed_compares(api, {"backtest_ids": ids, "holds": ["BTC"]}, ctx)

    # `curve_params` holds the last sample's reads.
    asked = [k for k in api.curve_params if k.get("references") == "true"]
    assert len(asked) == 1  # run 1's read only
    assert len(envs) == COMPARE_SAMPLES
    assert all(env["reference"]["label"] == "BTC hold" for env in envs)
    assert elapsed <= COMPARE_WITH_HOLDS_BUDGET_S, (
        f"keel_backtest_compare with holds took {elapsed:.2f}s "
        f"(median of {COMPARE_SAMPLES}; budget {COMPARE_WITH_HOLDS_BUDGET_S:.1f}s)"
    )
