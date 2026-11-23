"""Value-and-pricing spec 02 §C3.2 / §C5.8 (MC D-12, Q-2080; research build D-422): the LISTED profile's Research
answers show no other plans' features, in the card's data and the text alike, while a limit the person reaches
still names the plans (MC D-14).

keel-api serves an agent's plan and study read with the reader's locked rows, each naming the plans that include it
("Nearby settings: included on Trader, Quant and Pro.", ``locked[].more``), and the plan's ``words.runs_without``.
On the full profile (the CLI, ``keel mcp serve``) those reach the agent and the card; on the listed profile (the
Claude and ChatGPT directories) ``_research_commands.call`` strips them before any tool reads the answer
(:func:`strip_other_plans`). Each arm drives the real server (``create_server().call_tool``: the handler, the
envelope, the channel reducer, the adapter) over a served body, and reads everything the result carries: the
text, ``structuredContent`` and ``_meta`` (the card's).

SEED (applied and reversed by exact edit): ``call`` returning ``out`` on the listed profile too (the strip skipped)
-> the listed arms red (a locked row and its plans reach the result); the full-profile control and the limit-text
arm stay green. Non-vacuity: the served bodies carry three locked plan steps, two locked check rows, their plans and
the runs-without line, and the full profile shows them.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


SHAPES = json.loads(
    (Path(__file__).parent / "fixtures" / "research_server_shapes.json").read_text(encoding="utf-8")
)
NAMES = "Trader, Quant and Pro"
RUNS_WITHOUT = (
    f"Nearby settings and re-pick are included on {NAMES}. The rest of the Check runs on Free. "
    "Plans are changed in the Keel web app."
)
LOCKS = (
    ("grid", "SettingsGrid", "Every setting"),
    ("repick", "RepickSettings", "Re-picking the settings as you go"),
    ("nearby", "Neighbours", "Nearby settings"),
)


def _locked_rows() -> list[dict]:
    return [
        {
            "step": sid,
            "unit": "feature:research_repick",
            "moment": "repick_gate",
            "words": f"{label}: included on {NAMES}.",
            "example": {
                "share_id": "AchkV3oct19eightOfTen01",
                "title": "Keel's Check",
                "view": 5,
                "step": name,
            },
            "name": name,
            "question": "Is your setting a lone peak?",
            "more": NAMES,
        }
        for sid, name, label in LOCKS
    ]


def _plan_body() -> dict:
    """keel-api's plan answer for a Free Check that runs without re-picking (D-198), on an agent surface."""
    body = copy.deepcopy(SHAPES["plan"])
    for sid, name, label in LOCKS:
        body["steps"].append(
            {
                "after": 0, "id": sid, "kind": "analysis", "label": label, "not_valid": 0, "ranges": None,
                "refused": None, "reused": 0, "runnable": False, "step": name, "to_run": 0, "words": None,
                "locked": {"unit": "feature:research_repick", "words": f"{label}: included on {NAMES}.",
                           "more": NAMES},
            }
        )  # fmt: skip
    body["locked"] = _locked_rows()
    body["issues"] = [
        *(body.get("issues") or []),
        {
            "code": "STUDY_PLAN_STEP_NOT_INCLUDED",
            "path": "/steps/repick",
            "severity": "warning",
            "message": "Re-picking the settings as you go: not included in this account's plan.",
        },
    ]
    body["words"] = {**body["words"], "runs_without": RUNS_WITHOUT}
    return body


def _study_body() -> dict:
    """keel-api's study read of that Check, run on Free: its re-picking and Nearby rows are locked for the reader."""
    body = copy.deepcopy(SHAPES["study_done"])
    for row in body["checks"]["rows"]:
        if str(row.get("ref", "")).split(".", 1)[0] in ("repick", "nearby"):
            row["locked"] = True
            row["met"] = None
    body["locked"] = [r for r in _locked_rows() if r["step"] in ("repick", "nearby")]
    return body


def _call(profile: str, tool: str, args: dict, body: dict):
    from keel.tools.outcomes._toolsets import research_served_context

    def post(self, path, *, json=None, headers=None, **params):
        return copy.deepcopy(body)

    def get(self, path, *, headers=None, **params):
        return copy.deepcopy(body)

    async def go():
        from keel.mcp.server import create_server

        return await create_server().call_tool(tool, args)

    saved = os.environ.get("KEEL_SERVER_PROFILE")
    os.environ["KEEL_SERVER_PROFILE"] = profile
    try:
        with (
            research_served_context(True),
            patch("keel.client.KeelClient.post", post),
            patch("keel.client.KeelClient.get", get),
        ):
            return asyncio.run(go())
    finally:
        if saved is None:
            os.environ.pop("KEEL_SERVER_PROFILE", None)
        else:
            os.environ["KEEL_SERVER_PROFILE"] = saved


def _everything(result) -> str:
    """All a result carries, as one string: its text blocks, structuredContent and _meta (the card's data)."""
    text = "\n".join(getattr(block, "text", "") or "" for block in result.content or [])
    meta = getattr(result, "meta", None) or getattr(result, "_meta", None) or {}
    return "\n".join(
        [text, json.dumps(result.structured_content or {}), json.dumps(meta, default=str)]
    )


def _plan(profile: str):
    return _call(profile, "keel_research_plan", {"template": "check"}, _plan_body())


def _study(profile: str):
    return _call(
        profile, "keel_research_get", {"study_id": "stu_chkv3000000000000000000000"}, _study_body()
    )


OTHER_PLAN_MARKS = (
    f"included on {NAMES}",
    "Re-picking the settings as you go",
    "Every setting",
    "runs on Free",
)


def test_listed_plan_shows_no_other_plans_steps() -> None:
    said = _everything(_plan("listed"))
    for mark in OTHER_PLAN_MARKS:
        assert mark not in said, mark
    assert '"locked": []' in said or '"locked":[]' in said.replace(
        " ", ""
    )  # the rows' list served empty


def test_listed_study_read_shows_no_locked_check_rows() -> None:
    result = _study("listed")
    said = _everything(result)
    assert f"included on {NAMES}" not in said
    assert (
        "Sharpe re-picking the settings every 30 days" not in said
    )  # the locked check row is gone
    assert "Sharpe a day late" in said  # not vacuous: the rows the reader's plan runs stay


def test_control_full_profile_keeps_them() -> None:
    """Control (green through the seed): the full profile is the D-422 surface, so the rows and their plans reach the
    agent and the card; the arms above test a strip, not an absence in the served bodies."""
    plan = _everything(_plan("full"))
    assert RUNS_WITHOUT in plan and "Every setting" in plan
    study = _everything(_study("full"))
    assert (
        f"included on {NAMES}" in study and "Sharpe re-picking the settings every 30 days" in study
    )


def test_listed_keeps_the_limit_words_that_name_the_plans() -> None:
    """MC D-14: a limit the person reaches names the plans in text on every client, the listed one included."""
    body = _plan_body()
    wall = (
        "Signal studies are included on Quant and Pro. Free runs the Check, which covers costs, delay and chance. "
        "Plans are changed in the Keel web app."
    )
    body["run_allowed"] = False
    body["words"] = {**body["words"], "limit": wall}
    body["limits"] = [
        {"unit": "feature:research_template_signal", "moment": "template_gate", "steps": [],
         "remedy": {"kind": "other_template", "included": ["check"],
                    "actions": [{"label": "Start a Check", "template": "check"}]}}
    ]  # fmt: skip
    said = _everything(_call("listed", "keel_research_plan", {"template": "signal"}, body))
    assert wall in said
    assert "Start a Check" in said  # the remedy rides beside it


@pytest.mark.parametrize("profile", ("full", "listed"))
def test_the_strip_never_changes_the_served_body(profile: str) -> None:
    from keel.tools.outcomes._research_commands import strip_other_plans

    body = _plan_body()
    before = json.dumps(body, sort_keys=True)
    strip_other_plans(body)
    assert json.dumps(body, sort_keys=True) == before
