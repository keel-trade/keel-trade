from keel.tools.outcomes.screener_run import DESCRIPTION as RUN
from keel.tools.outcomes.screener_view import DESCRIPTION as VIEW


def test_frozen_description_budget_and_neighbours():
    assert 200 <= len(RUN) and 200 <= len(VIEW) and len(RUN) + len(VIEW) <= 1000
    assert "keel_backtest_run" in RUN.split(". ")[0]
    assert "keel_backtest_summarize" in VIEW.split(". ")[0]
