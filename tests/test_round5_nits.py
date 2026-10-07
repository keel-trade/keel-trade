"""The round-5 audit's small findings (Q-2273 nits).

* the diff card names the strategy instead of "Untitled";
* `keel_feedback` with no or blank `text` is refused before anything is sent;
* `keel_strategy_compose` with a whitespace-only `source` is refused;
* an id that is not an id (`str_x/versions`) never reaches a URL path, and a
  `btr_` id passed as `strategy_id` is named for what it is (CONTROL: a
  well-formed id of the right kind, and an id with no known prefix, pass);
* `keel_components_search after=<unknown>` answers without the KeyError's
  quotes;
* `keel_backtest_compare` refuses a repeated id.

Seed (2026-10-01, reverted by reversing the edit): `_ID_SHAPE` widened to
`.+` → the slash-id arm red (the versions endpoint is read), the
wrong-prefix and control arms green.
"""

from __future__ import annotations

from unittest import mock

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._declared_choices import choice_violation


_bootstrap()


def _ctx(client=None):
    return ToolContext(api_client=client or mock.MagicMock(), is_tty=False)


def test_the_diff_card_names_the_strategy():
    client = mock.MagicMock()
    # A pipeline with no `name=`: the card had nothing to title it with.
    source = (
        "Globals(target_timeframe='1d')\n"
        "Universe(mode='top_volume', top_n=30, market='perp')\n"
        "Execution(rebalance='buffered', buffer_threshold=0.2, buffer_mode='relative', "
        "rebalance_method='to_edge')\n"
        "Pipeline([\n    PriceDataLoader(),\n    ROC(period=20),\n"
        "    ForecastScaler(avg_abs_target=10.0),\n    ForecastCapper(limit=20.0),\n"
        "    ForecastWeightNormalizer(target_leverage=1.0),\n])\n"
    )

    def get(path, **_):
        if path.endswith("/source"):
            return {"source": source}
        if path == "/v1/strategies/str_x":
            return {"strategy_id": "str_x", "name": "Momentum Core"}
        return {}

    client.get.side_effect = get
    client.post.return_value = {
        "changes": {
            "added_steps": [],
            "removed_steps": [],
            "modified_steps": [{"step_name": "ROC", "param_changes": {"period": [10, 20]}}],
            "reordered_steps": [],
            "component_version_changes": {},
        }
    }
    result = OUTCOMES["keel_strategy_diff"].handler(
        {"strategy_id": "str_x", "ref_a": "2", "ref_b": "3"}, _ctx(client)
    )
    view = result.extra["view"]
    assert view["name"] == "Momentum Core"
    assert "Untitled" not in view["markdown"]


@pytest.mark.parametrize("args", [{}, {"text": "   "}, {"text": "", "kind": "bug"}])
def test_feedback_without_text_is_refused_before_sending(args):
    client = mock.MagicMock()
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_feedback"].handler(args, _ctx(client))
    assert exc.value.error_code == "usage_error" and "`text`" in str(exc.value)
    client.post.assert_not_called()


def test_compose_refuses_a_whitespace_only_source():
    client = mock.MagicMock()
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_compose"].handler({"source": "   \n\t", "name": "n"}, _ctx(client))
    assert exc.value.error_code == "missing_input"
    client.post.assert_not_called()


def test_an_id_with_a_path_in_it_never_reaches_a_url():
    client = mock.MagicMock()
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_get"].handler({"strategy_id": "str_x/versions"}, _ctx(client))
    assert exc.value.error_code == "usage_error" and "is not an id" in str(exc.value)
    client.get.assert_not_called()


def test_a_backtest_id_passed_as_a_strategy_id_is_named():
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_strategy_get"].handler({"strategy_id": "btr_a"}, _ctx())
    message = str(exc.value)
    assert "a backtest run id" in message
    assert "keel_backtest_summarize" in exc.value.to_envelope()["what_was_expected"]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("keel_strategy_get", {"strategy_id": "str_01HZXK3Y6N3V7Q9S2T4W8R5M1E"}),
        ("keel_strategy_get", {"strategy_id": "  str_x  "}),
        ("keel_strategy_get", {"strategy_id": "legacy-id-7"}),
        ("keel_live_monitor", {"deployment_id": "all"}),
        ("keel_library_get", {"slug": "ma-crossover-crypto"}),
        ("keel_backtest_compare", {"backtest_ids": ["btr_a", "btr_b"]}),
    ],
)
def test_control_well_formed_ids_pass(tool, args):
    assert choice_violation(OUTCOMES[tool], args) is None


def test_search_after_an_unknown_component_reads_plainly():
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_components_search"].handler({"after": "NotAComponentQ2273"}, _ctx())
    assert exc.value.error_code == "not_found"
    assert not str(exc.value).startswith(('"', "'"))


def test_compare_refuses_a_repeated_id():
    client = mock.MagicMock()
    with pytest.raises(KeelError) as exc:
        OUTCOMES["keel_backtest_compare"].handler(
            {"backtest_ids": ["btr_a", "btr_b", "btr_a"]}, _ctx(client)
        )
    assert exc.value.error_code == "bad_compare_args" and "btr_a" in str(exc.value)
    client.get.assert_not_called()
