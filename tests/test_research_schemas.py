"""The research tools' schema rows (spec 07 §2.2, §3, §4): the day-0 contract.

The input and output schemas are checked in before the tools register
(BUILD-PLAN §4.4). These tests hold them to 07: the eight MCP tools each have
an input schema, the five view tools an output schema, every schema is valid
JSON Schema, the paused refusal's five-field envelope validates, and the card's
bounded `checks` refuses a sixth row (RL-58).

SEED (reverted by reversing the edit): `"maxItems": 5` → `"maxItems": 6` on
`_STUDY_PROPS["checks"]["rows"]` reds `test_structured_checks_are_bounded`;
deleting `keel_research_cancel` from RESEARCH_INPUT_SCHEMAS reds
`test_every_research_tool_has_an_input_schema`.
"""

from __future__ import annotations

import jsonschema
import pytest
from keel.errors import KeelError
from keel.tools.outcomes._base import normalize_input_schema
from keel.tools.outcomes._output_schemas import (
    OUTPUT_SCHEMAS,
    RESEARCH_INPUT_SCHEMAS,
    RESEARCH_OUTPUT_SCHEMAS,
    STUDY_SCHEMA,
)


#: 07 A1: eight MCP tools.
RESEARCH_TOOLS = {
    "keel_research_plan",
    "keel_research_run",
    "keel_research_get",
    "keel_research_view_set",
    "keel_research_brief",
    "keel_research_note",
    "keel_research_cancel",
    "keel_research_make_signal",
}
#: 07 §2.2's "Mode and card": these carry a view.
VIEW_RESEARCH_TOOLS = {
    "keel_research_plan",
    "keel_research_run",
    "keel_research_get",
    "keel_research_view_set",
    "keel_research_brief",
}


def test_every_research_tool_has_an_input_schema() -> None:
    assert set(RESEARCH_INPUT_SCHEMAS) == RESEARCH_TOOLS


def test_the_view_tools_have_output_schemas() -> None:
    assert set(RESEARCH_OUTPUT_SCHEMAS) == VIEW_RESEARCH_TOOLS
    # Registered (07 §3.3): exactly the five view tools join OUTPUT_SCHEMAS.
    assert set(OUTPUT_SCHEMAS) & RESEARCH_TOOLS == VIEW_RESEARCH_TOOLS


@pytest.mark.parametrize("tool", sorted(RESEARCH_TOOLS))
def test_each_input_schema_is_valid_and_closes(tool: str) -> None:
    schema = normalize_input_schema(RESEARCH_INPUT_SCHEMAS[tool])
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) <= set(schema["properties"])
    assert schema["properties"], tool  # not vacuous
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"role": "agent"}, schema)


def test_plan_and_run_share_the_eight_forms() -> None:
    plan = set(RESEARCH_INPUT_SCHEMAS["keel_research_plan"]["properties"])
    run = set(RESEARCH_INPUT_SCHEMAS["keel_research_run"]["properties"])
    forms = plan - {"cursor", "limit"}
    assert len(forms) == 21  # RI-483: the edit form's base
    assert forms <= run
    assert run - forms == {"plan_id", "max_runs", "wait", "present"}


def test_the_template_forms_costs_and_run_settings_reach_the_route() -> None:
    """RI-164 (RI-138's template form): costs ({fee, slippage} or "none"), delay, leverage and vol_cap are plan
    properties and plan_body forwards each to keel-api's PlanRequest."""
    from keel.tools.outcomes.research_plan import plan_body

    schema = RESEARCH_INPUT_SCHEMAS["keel_research_plan"]
    for costs in ({"fee": 0.0004, "slippage": 0.0002}, "none"):
        jsonschema.validate({"template": "check", "costs": costs}, schema)
    for bad in ("some", {"fee": 0.0004}, {"fee": 0.0004, "slippage": 0.0002, "extra": 1}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"template": "check", "costs": bad}, schema)
    args = {
        "template": "check",
        "subjects": ["str_x@1"],
        "costs": "none",
        "delay": 1,
        "exposure_multiple": 2.0,
        "vol_cap": 0.3,
    }
    body = plan_body(args)
    # D-344 (1): the listed name is exposure_multiple, the route's field leverage.
    assert "leverage" not in schema["properties"] and "exposure_multiple" not in body
    assert "leverage" not in RESEARCH_INPUT_SCHEMAS["keel_research_run"]["properties"]
    assert {k: body[k] for k in ("costs", "delay", "leverage", "vol_cap")} == {
        "costs": "none",
        "delay": 1,
        "leverage": 2.0,
        "vol_cap": 0.3,
    }
    # The CLI's --costs arrives as text: a JSON object becomes the object the route takes.
    cli = plan_body({**args, "costs": '{"fee": 0.0004, "slippage": 0.0002}'})
    assert cli["costs"] == {"fee": 0.0004, "slippage": 0.0002}


def test_the_cell_form_reaches_the_route() -> None:
    """RI-463 (07 §4.1's Cell form, D-290): `cell` {grid, at, subject?} is a plan property, the schema closes it as
    keel-api's CellForm does, and plan_body forwards it, the CLI's JSON text as the object."""
    from keel.tools.outcomes.research_plan import plan_body

    schema = normalize_input_schema(RESEARCH_INPUT_SCHEMAS["keel_research_plan"])
    cell = {"grid": "map", "at": {"lookback": "12", "hold": 20}}
    jsonschema.validate({"study_id": "stu_x", "cell": cell}, schema)
    jsonschema.validate({"study_id": "stu_x", "cell": {**cell, "subject": "v3"}}, schema)
    for bad in (
        {"at": {"lookback": 12}},  # no grid
        {"grid": "map"},  # no at
        {**cell, "row": 1},  # closed
        {"grid": "map", "at": {"lookback": [12]}},  # a value is one scalar
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"study_id": "stu_x", "cell": bad}, schema)
    assert plan_body({"study_id": "stu_x", "cell": cell}) == {"study_id": "stu_x", "cell": cell}
    cli = plan_body(
        {"study_id": "stu_x", "cell": '{"grid": "map", "at": {"lookback": "12", "hold": 20}}'}
    )
    assert cli["cell"] == cell
    run = RESEARCH_INPUT_SCHEMAS["keel_research_run"]["properties"]
    # run takes every plan form (07 §4.3): the same shape; its description says it is the direct run's (R-25)
    strip = lambda prop: {k: v for k, v in prop.items() if k != "description"}  # noqa: E731
    assert strip(run["cell"]) == strip(schema["properties"]["cell"])


def test_get_pages_25_rows_on_mcp() -> None:
    props = RESEARCH_INPUT_SCHEMAS["keel_research_get"]["properties"]
    assert props["limit"]["maximum"] == 25
    assert props["measures"]["maxItems"] == 12
    assert props["where"]["maxProperties"] == 4


def test_output_schema_is_valid() -> None:
    jsonschema.Draft202012Validator.check_schema(STUDY_SCHEMA)
    assert "headline" not in STUDY_SCHEMA["properties"]  # card_summary is its one home


_ENVELOPE = {
    "study": {
        "study_id": "stu_abc",
        "version": 1,
        "title": "Momentum rotation v3",
        "kind_label": {"key": "check", "name": "Check a strategy", "short": "Check"},
        "state": "completed",
    },
    "footing": {"days": {"start": "2025-03-23", "end": "2026-10-29"}},
    "counts": {"allowance": None, "runs": {}, "versions_tried": {}},
    "stale": [],
    "links": {"app": "https://app.usekeel.io/research/stu_abc", "share": None},
    "checks": {"count": 9, "met": 7, "rows": [{}] * 5},
    "steps": {"total": 13, "by_status": {"completed": 13}},
    "views": {"total": 10},
    "notes": {"notes_total": 0},
    "view": {"kind": "study", "size": "view", "markdown": "Check a strategy · …"},
}


def test_a_study_envelope_validates() -> None:
    jsonschema.validate(_ENVELOPE, STUDY_SCHEMA)


def test_structured_checks_are_bounded() -> None:
    six = {**_ENVELOPE, "checks": {**_ENVELOPE["checks"], "rows": [{}] * 6}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(six, STUDY_SCHEMA)


def test_a_plan_envelope_validates() -> None:
    plan = {
        **_ENVELOPE,
        "plan_id": "pln_abc",
        "class": "new_study",
        "steps": [{"id": "costs", "step": "CostStress", "to_run": 5, "runnable": True}],
        "runs": {"children": 26, "to_run": 23},
        "run_allowed": True,
        "words": {"work": "24 runs, 2 reused · under a minute"},
    }
    jsonschema.validate(plan, STUDY_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**plan, "class": "upgrade"}, STUDY_SCHEMA)


def test_the_paused_refusal_envelope_validates() -> None:
    """07 §3.4: one code, the one sentence, no next action and no link."""
    err = KeelError("Research isn't available yet.")
    err.error_code = "RESEARCH_PAUSED"
    err.recovery_tool = None
    err.detail = {"code": "RESEARCH_PAUSED"}
    envelope = err.to_envelope()
    assert envelope["code"] == "RESEARCH_PAUSED"
    assert envelope["message"] == "Research isn't available yet."
    assert envelope["suggested_next_action"] == {"tool": None, "args": {}}
    jsonschema.validate(envelope, STUDY_SCHEMA)


def test_every_fixture_card_validates_against_keel_apis_exported_card_contract() -> None:
    """13 §7.3 (WL-AG-13): every ``card_summary`` and ``card_data`` the SDK's research fixtures carry validates against
    the JSON Schema keel-api exports from its closed models (``libs/research/fixtures/card_v2/schema.json``, generated
    by keel-api's test_research_card_contract.py), so the SDK's tests cannot run on a card keel-api would refuse to
    serve. A share's card is ``ShareCardSummary`` (its maker a display name) and is checked against the same schema with
    that one field widened. The seeded arm is kept in the test: the v2 card with a field the contract does not hold is
    refused; control: the recorded cards as served validate. Non-vacuity: 2 or more cards and 1 card_data checked."""
    import json
    from pathlib import Path

    contract = json.loads(
        (
            Path(__file__).resolve().parents[4] / "libs/research/fixtures/card_v2/schema.json"
        ).read_text()
    )
    shapes = json.loads(
        (Path(__file__).parent / "fixtures" / "research_server_shapes.json").read_text()
    )
    cards = [
        shapes["study_done"]["card_summary"],
        shapes["card_summary_v2_max_thumb"],
        *(r["card_summary"] for r in shapes["list"]["data"] if r.get("card_summary")),
    ]
    data = [shapes["card_data_v2"]]
    checked = 0
    for card in cards:
        jsonschema.validate(
            {k: v for k, v in card.items() if k != "_provenance"}, contract["card_summary"]
        )
        checked += 1
    share_schema = json.loads(json.dumps(contract["card_summary"]))
    share_schema["properties"]["maker"] = {"type": ["string", "null"]}
    jsonschema.validate(shapes["share"]["card_summary"], share_schema)
    for body in data:
        jsonschema.validate(
            {k: v for k, v in body.items() if k != "_provenance"}, contract["card_data"]
        )
    assert checked >= 2 and len(data) == 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {**cards[1], "subtitle": "not in the contract"}, contract["card_summary"]
        )
