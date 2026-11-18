"""Research's bundled help (07 §4.10, §8.6): the topics, the step catalog and their freshness.

``scripts/build_data.py`` copies ``libs/research/reference/{research,
research_steps}.md`` and STEP-B's ``step_catalog.json`` into ``keel/data``;
the copies are byte-identical to their sources (when the monorepo is beside
the SDK), ``keel_help`` serves ``research``, ``research_steps`` and
``step:<Name>``, and withholds all three where Research is not served.

SEED (reverted by reversing the edit): append a line to the bundled
``research.md``; the freshness arm reds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._toolsets import research_served_context


_bootstrap()
SDK = Path(__file__).resolve().parents[1]
DATA = SDK / "keel" / "data"
LIBS = SDK.parents[2] / "libs" / "research"
PAIRS = {
    DATA / "reference" / "research.md": LIBS / "reference" / "research.md",
    DATA / "reference" / "research_steps.md": LIBS / "reference" / "research_steps.md",
    DATA / "research_step_catalog.json": LIBS / "steps" / "step_catalog.json",
    # 07 §5.1, RI-251: the research link routes (open_in_app.research_link)
    DATA / "research_link_routes.json": LIBS / "link_routes.json",
}


def _help(topic: str):
    return OUTCOMES["keel_help"].handler({"topic": topic}, ToolContext())


@pytest.mark.skipif(not LIBS.is_dir(), reason="the monorepo's libs/research is not beside the SDK")
@pytest.mark.parametrize("bundled", sorted(PAIRS), ids=lambda p: p.name)
def test_the_bundled_copy_is_its_source(bundled: Path) -> None:
    assert bundled.read_bytes() == PAIRS[bundled].read_bytes(), (
        f"{bundled.name} is stale: run PYTHONPATH=libs python packages/keel-trade/keel-sdk/scripts/build_data.py"
    )


def test_the_topics_are_served_on_the_full_profile(monkeypatch) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    body = _help("research").extra["body"]
    assert "keel_research_plan" in body and "```study" in body
    steps = json.loads((DATA / "research_step_catalog.json").read_text())["steps"]
    assert len(steps) >= 40  # not vacuous: the catalog's steps
    for step in steps:
        got = _help(f"step:{step['name']}").extra
        assert got["topic"] == f"step:{step['name']}" and step["name"] in got["body"]
    assert "Backtest" in _help("research_steps").extra["body"]


def test_the_steps_topic_carries_the_grammar(monkeypatch) -> None:
    """K15 (01 §4.9, RI-092): the generated topic ends with the reference forms, the address grammar and every
    STUDY_* rule with its fix, so an agent composes and checks a study from the help alone."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    body = _help("research_steps").extra["body"]
    # D-344 (7): ENGINE's d782477b60 folds the builders into the reference forms (the topic is an index).
    for heading in ("## Reference forms", "## Addresses", "## Templates", "## Rules"):
        assert heading in body, heading
    assert "`StudyOut(study_id, version=n, output=name)`" in body and "`var[name]`" in body
    rules = [line for line in body.partition("## Rules")[2].splitlines() if line.startswith("- `STUDY_")]
    assert len(rules) > 80  # not vacuous: every rule code


def test_an_unknown_step_names_the_near_misses(monkeypatch) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    with pytest.raises(KeelError) as caught:
        _help("step:CostStres")
    assert "CostStress" in (caught.value.suggestion or "")


def test_a_step_topic_names_what_its_money_measures_read(monkeypatch) -> None:
    """RI-477 (D-299): step:<Name> shows a money measure's label, which says it reads the return before fees and
    funding; a measure with no basis shows its name only (the control)."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    stats = _help("step:TradeStats").extra["body"]
    assert "win_rate (Win rate before fees and funding)" in stats
    assert "expectancy (Expectancy before fees and funding)" in stats
    assert "trades_per_month," in stats  # control: no basis, the name alone
    assert "(Win rate" not in _help("step:Backtest").extra["body"]


def test_a_step_topic_names_its_variant(monkeypatch) -> None:
    """D-296: step:<Name> prints the variant the catalog serves (its varied parameters, the edit, a condition); a
    step that makes no variant prints none."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    cost = _help("step:CostStress").extra["body"]
    assert "- Variant: varies `multiples`; edits run_setting `costs` through Run.costs." in cost
    stop = _help("step:StopTargetMap").extra["body"]
    assert "- Variant (when form runs): varies `stops`, `targets`; edits param `stop_at and target_at`" in stop
    assert "- Variant" not in _help("step:Backtest").extra["body"]  # control: null variant, no line


@pytest.mark.parametrize("topic", ["research", "research_steps", "step:CostStress"])
def test_the_topics_are_withheld_where_research_is_not_served(monkeypatch, topic) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    with research_served_context(False), pytest.raises(KeelError):
        _help(topic)
    with research_served_context(True):
        assert _help(topic).extra["topic"]  # control: served when Research is
