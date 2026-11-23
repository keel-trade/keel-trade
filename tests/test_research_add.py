"""07 §4.2's add, end to end through the real command (J7.4; AT-2).

``keel research add stu_… --add …`` plans through ``POST /v1/research/studies/{id}/add-plan`` (04 §1.7), whose
served ``words.work`` carries what adding costs ("1 run on the same days and coins · Counts as 1 version tried",
``words.picker_cost_line``). The plans route's add form serves the study-wide work line only, so the CLI that
called it never showed the add words. The MCP add form (``keel_research_plan`` with ``study_id`` and ``add``)
takes the same route and prints the same text. The network is respx; the client, the CLI and the tool are real.

SEED (reverted by reversing the edit): point ``research_cli._add`` back at ``/v1/research/plans`` ->
``test_the_cli_add_prints_the_served_add_words`` fails (the add-plan route is never called and the words are
absent); the template-form control stays green.
Not vacuous: the add-plan route is called exactly once per surface, with the one ref, and the printed steps
name the seven reused signals.
"""

from __future__ import annotations

import json

import httpx
import keel.surface as surface_mod
import pytest
import respx
from click.testing import CliRunner
from keel.cli.main import cli
from keel.client import KeelClient
from keel.config import KeelConfig
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


API = "https://api.research-add.test"
REF = 'Output("str_keel_nearhigh@1", output="near_high_30d")'
ADD_WORDS = "1 run on the same days and coins · Counts as 1 version tried"
LEAD = "Adds Near the 30-day high (Keel's library, v1) to Signals, side by side"
SIGNALS = ("ROC", "RSI", "Breakout", "Carry", "Reversal", "Volume", "Skew")


def _plan(work: str) -> dict:
    steps = [
        {"id": f"s{i}", "label": n, "to_run": 0, "runnable": True} for i, n in enumerate(SIGNALS)
    ]
    steps.append({"id": "s10", "label": "Near the 30-day high", "to_run": 1, "runnable": True})
    return {
        "plan_id": "pln_" + "b" * 64,
        "expires_at": "2026-11-13T14:00:00Z",
        "class": "new_version",
        "writes": "version 2",
        "study": {
            "study_id": "stu_a",
            "title": "Signals, side by side",
            "kind_label": {"name": "Compare"},
        },
        "steps": steps,
        "runs": {"to_run": 1, "reused": 7},
        "run_allowed": True,
        "words": {"work": work},
        "links": {"app": "https://app.usekeel.io/research/stu_a"},
    }


ADDED = {
    **_plan(f"{LEAD}: {ADD_WORDS}"),
    "added": [
        {
            "ref": "s10",
            "to_run": 1,
            "reused": 0,
            "versions_tried_added": 1,
            "cost_line": "1 run on the same days and coins",
            "count_line": "Counts as 1 version tried",
        }
    ],
}
#: What the plans route's add form serves: the study-wide work line, no add words.
PLANNED = _plan("1 run · under a minute · versions tried +1")


@pytest.fixture(autouse=True)
def _api(monkeypatch):
    _bootstrap()
    monkeypatch.setenv("KEEL_API_KEY", "tok")
    monkeypatch.setenv("KEEL_API_URL", API)
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    with respx.mock(base_url=API, assert_all_called=False) as mock:
        mock.add_plan = mock.post("/v1/research/studies/stu_a/add-plan").mock(
            return_value=httpx.Response(200, json=ADDED)
        )
        mock.plans = mock.post("/v1/research/plans").mock(
            return_value=httpx.Response(200, json=PLANNED)
        )
        yield mock


def _text(stdout: str) -> str:
    """The command's text, whether it printed the text block or the JSON envelope."""
    try:
        env = json.loads(stdout)
    except ValueError:
        return stdout
    return json.dumps(env, ensure_ascii=False)


def test_the_cli_add_prints_the_served_add_words(_api, monkeypatch) -> None:
    monkeypatch.setattr(surface_mod, "_SURFACE", "cli")
    out = CliRunner().invoke(cli, ["research", "add", "stu_a", "--add", REF])
    assert out.exit_code == 0, out.output
    text = _text(out.stdout)
    assert f"{LEAD}: {ADD_WORDS}" in text, text[:1500]  # RI-049: printed as served
    assert _api.add_plan.call_count == 1 and not _api.plans.called
    assert json.loads(_api.add_plan.calls.last.request.content) == {"add": [REF]}
    reused = [n for n in SIGNALS if f"{n}: reused" in text]
    assert len(reused) == 7, text[:1500]  # non-vacuity: every reused signal printed


def test_the_mcp_add_form_takes_the_same_route_and_text() -> None:
    """AT-2: the MCP add form and the CLI verb send one request and print one plan text."""
    ctx = lambda: ToolContext(api_client=KeelClient(KeelConfig(api_key="tok", api_url=API)))  # noqa: E731
    via_mcp = OUTCOMES["keel_research_plan"].handler({"study_id": "stu_a", "add": [REF]}, ctx())
    via_cli = OUTCOMES["keel_research_add"].handler({"study_id": "stu_a", "add": [REF]}, ctx())
    mcp_text = via_mcp.extra["view"]["markdown"]
    assert ADD_WORDS in mcp_text
    assert mcp_text == via_cli.extra["view"]["markdown"]


def test_control_other_plan_forms_keep_the_plans_route(_api) -> None:
    """A template form, and an add form that pages, are not the picker's add: they stay on the plans route."""
    ctx = ToolContext(api_client=KeelClient(KeelConfig(api_key="tok", api_url=API)))
    OUTCOMES["keel_research_plan"].handler(
        {"template": "check", "subjects": ['Strategy("str_a@3")']}, ctx
    )
    OUTCOMES["keel_research_plan"].handler({"study_id": "stu_a", "add": [REF], "limit": 25}, ctx)
    assert _api.plans.call_count == 2 and not _api.add_plan.called
