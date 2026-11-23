"""AUDIT-3 S3: keel_research_plan's ``delay`` parameter says the planner's unit and range, days from 0 to 30, read
from the bundled step catalog's ``run_settings`` (research.steps.catalog), never a hand copy; it said "bars (0 to 3)"
while the planner reads ``delay_days`` and the validator allows 0 to 30.

Seeded red (reversed by reversing the edit): the hand-written "Act this many bars late (0 to 3)." reds both arms.
Control: another parameter's words are unchanged.
"""

from __future__ import annotations

import json
from importlib import resources

from keel.tools.outcomes import _output_schemas as s


def test_delay_reads_days_from_the_bundled_catalog() -> None:
    catalog = json.loads(
        resources.files("keel.data").joinpath("research_step_catalog.json").read_text("utf-8")
    )
    served = catalog["run_settings"]["delay"]
    assert (
        "days" in served and "30" in served
    )  # non-vacuity: the catalog says the unit and the bound
    assert s._PLAN_WORDS["delay"] == f"Act this many days late: {served}."
    assert "bars" not in s._PLAN_WORDS["delay"]


def test_control_other_words_unchanged() -> None:
    assert (
        s._PLAN_WORDS["split"] == "A date that divides the days into before and after (YYYY-MM-DD)."
    )
