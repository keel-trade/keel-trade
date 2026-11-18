"""RI-483 (D-301, 07 §4.1, §3.4): an agent's edit of a study states the read it was made on.

keel_research_get's default read is the newest version that ran (07 §4.4); a study whose head is a Draft names it in
``study.draft``. An agent that edits that read's source and plans it as the edit form (``study_id`` + ``source``)
with no base had its plan made on the Draft head, so the save replaced the Draft (RL-29) and dropped its edits. Now:

- keel_research_get serves ``base`` (``{version, revision, evidence_hash}`` of the read, copied from ``study``) beside
  the source, kept in MCP's structuredContent (the source is not: RL-58); a share read serves none;
- the edit form takes ``base`` and sends it to POST /v1/research/plans (the CLI's ``--base`` as JSON text);
- keel-api refuses a stale base, or no base while a Draft is ahead, 409 RESEARCH_REVISION_CONFLICT naming the Draft;
  that refusal's next call reads THE DRAFT (``keel_research_get(study_id, version=<draft>)``), never the default read,
  which would serve the version that ran again;
- a read of an older version than the Draft says where the edit is made: the Draft's own read.

SEEDS (each reversed by reversing the edit, 2026-11-15; results in the commit body): ``read_base`` returning None reds
``test_get_serves_the_base_beside_the_source`` and the round trip; ``_conflict_read`` without the Draft's version
reds ``test_a_draft_ahead_conflict_reads_the_draft``; ``base`` dropped from PLAN_FIELDS reds the route test and
the round trip.
Non-vacuity: the read carries a source and a Draft ahead (asserted), and the controls (a view_set conflict, the
Draft's own read, a share read) keep their own answers.
"""

from __future__ import annotations

import json

import jsonschema
import pytest
from keel.errors import KeelError, translate_http_error
from keel.tools.outcomes import OUTCOMES
from keel.tools.outcomes._base import normalize_input_schema
from keel.tools.outcomes._output_schemas import RESEARCH_INPUT_SCHEMAS

from tests.test_research_tools import FakeClient, _ctx, _study


BASE = {"version": 1, "revision": 2, "evidence_hash": "sha256:ev1"}
SOURCE = 'Study(title="Momentum rotation v3")\n'


def _read_of_v1() -> dict:
    """The unversioned read of a study whose Draft (version 2) is ahead of version 1, which ran."""
    env = _study(source=SOURCE)
    env["study"] = {
        **env["study"],
        "revision": 2,
        "evidence_hash": "sha256:ev1",
        "draft": {"version": 2},
    }
    return env


def test_the_edit_forms_base_is_closed_and_reaches_the_route() -> None:
    from keel.tools.outcomes.research_plan import plan_body

    schema = normalize_input_schema(RESEARCH_INPUT_SCHEMAS["keel_research_plan"])
    jsonschema.validate({"study_id": "stu_a", "source": SOURCE, "base": BASE}, schema)
    for bad in ({"version": 1, "revision": 2}, {**BASE, "extra": 1}, {**BASE, "revision": 0}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"study_id": "stu_a", "source": SOURCE, "base": bad}, schema)
    body = {"study_id": "stu_a", "source": SOURCE, "base": BASE}
    assert plan_body(body) == body
    assert plan_body({**body, "base": json.dumps(BASE)}) == body  # the CLI's --base as JSON text
    run = RESEARCH_INPUT_SCHEMAS["keel_research_run"]["properties"]
    # run takes every plan form (07 §4.3): the same shape; its description says it is the direct run's (R-25)
    strip = lambda prop: {k: v for k, v in prop.items() if k != "description"}  # noqa: E731
    assert strip(run["base"]) == strip(schema["properties"]["base"])


def test_get_serves_the_base_beside_the_source() -> None:
    from keel.tools.outcomes import _channels

    client = FakeClient({("get", "/v1/research/studies/stu_a"): _read_of_v1()})
    result = OUTCOMES["keel_research_get"].handler({"study_id": "stu_a"}, _ctx(client))
    assert result.extra["source"] == SOURCE  # non-vacuity: the owner's read serves its source
    assert result.extra["base"] == BASE
    structured, _card = _channels.partition(dict(result.extra), "study")
    assert (
        structured["base"] == BASE and "source" not in structured
    )  # the base is the model's on MCP


def test_control_a_share_read_serves_no_base() -> None:
    share = _study()
    share["study"] = {
        k: v for k, v in share["study"].items() if k not in ("study_id", "run_set_id")
    } | {"share_id": "s" * 23}
    client = FakeClient({("get", "/v1/research/shares/"): share})
    result = OUTCOMES["keel_research_get"].handler({"share_id": "s" * 23}, _ctx(client))
    assert "base" not in result.extra


def test_an_edit_of_a_read_sends_that_reads_base() -> None:
    """The round trip an agent makes: read, edit the source, plan the edit with the read's base."""
    plan = {"plan_id": "pln_a", "words": {"work": "3 runs"}, "run_allowed": True, "writes": "version 2",
            "study": {"title": "t", "kind_label": {"name": "Check a strategy"}},
            "links": {"app": "https://app.usekeel.io/research/stu_a"}}  # fmt: skip
    client = FakeClient(
        {("get", "/v1/research/studies/stu_a"): _read_of_v1(), ("post", "/v1/research/plans"): plan}
    )
    read = OUTCOMES["keel_research_get"].handler({"study_id": "stu_a"}, _ctx(client)).extra
    edited = read["source"].replace("v3", "v3, late")
    OUTCOMES["keel_research_plan"].handler(
        {"study_id": "stu_a", "source": edited, "base": read["base"]}, _ctx(client)
    )
    assert client.calls[-1][1] == "/v1/research/plans"
    assert client.calls[-1][2]["json"] == {"study_id": "stu_a", "source": edited, "base": BASE}


def _refused_plan(monkeypatch, detail: dict) -> KeelError:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")

    def refuse(_payload):
        raise translate_http_error(409, json.dumps({"detail": detail}))

    client = FakeClient({("post", "/v1/research/plans"): refuse})
    with pytest.raises(KeelError) as raised:
        OUTCOMES["keel_research_plan"].handler(
            {"study_id": "stu_a", "source": SOURCE, "base": BASE}, _ctx(client)
        )
    return raised.value


DRAFT_AHEAD = "This study has a Draft, version 2, with changes you aren't editing here. Open the Draft and make your edit there."


def test_a_draft_ahead_conflict_reads_the_draft(monkeypatch) -> None:
    err = _refused_plan(
        monkeypatch,
        {"code": "RESEARCH_REVISION_CONFLICT", "message": DRAFT_AHEAD, "current_revision": 1,
         "head": {"version": 2, "revision": 1}, "draft": {"version": 2}},
    )  # fmt: skip
    assert err.recovery_tool == "keel_research_get"
    assert err.recovery_tool_args == {"study_id": "stu_a", "version": 2}
    assert "read the Draft" in err.suggestion
    assert err.to_envelope()["message"] == DRAFT_AHEAD  # the served words lead


def test_control_a_conflict_with_no_draft_reads_the_study_again(monkeypatch) -> None:
    err = _refused_plan(
        monkeypatch,
        {"code": "RESEARCH_REVISION_CONFLICT", "message": "This study changed since you opened it.",
         "current_revision": 3, "head": {"version": 2, "revision": 3}, "draft": None},
    )  # fmt: skip
    assert err.recovery_tool_args == {"study_id": "stu_a"}
    assert err.suggestion.startswith("The study was edited elsewhere first")


def test_a_read_of_an_older_version_names_the_drafts_own_read() -> None:
    from keel.tools.outcomes._research_view import study_text

    text = study_text(_read_of_v1())
    assert 'Edit it on its own read: keel_research_get(study_id="stu_a", version=2)' in text
    own = _read_of_v1()
    own["study"] = {**own["study"], "version": 2}  # control: the Draft's own read
    assert "Edit it on its own read" not in study_text(own)
    assert "Version 2 is a saved Draft that has not run yet." in study_text(own)
