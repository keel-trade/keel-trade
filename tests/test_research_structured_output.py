"""RI-588: every research view tool's structuredContent validates against the outputSchema it publishes.

A strict MCP client (the official Python ``mcp`` 2.2 client) validates a tool's structuredContent against the
tool's declared outputSchema and raises on a mismatch. On staging every ``keel_research_get`` and
``keel_research_run`` of a study failed that way: the card reducer (``_research_view``) sends ``subjects`` as a
count ``{total}`` (07 §3.3, RL-58) while the schema declared an array. The schema tests only ever validated
hand-written envelopes that carried no ``subjects``, so nothing drove a real served body through the real reducer.

This drives each research view tool through the real MCP server (``create_server().call_tool``: the handler, the
envelope, the channel reducer and the adapter's structuredContent, on both profiles) over keel-api's own served
bodies: the Research mock world (``services/keel-app/e2e/mocked/fixtures/research/world.gen.json`` and
``rows.gen``), which gen.py builds from keel-api's research routes and gen_test validates against keel-api's
response schemas. HTTP is the only seam faked. Each structuredContent is validated with jsonschema against the
outputSchema the server publishes for that tool.

It found two more of the class the walk had not reached: a submit's ``quota_notice`` is null (the error set's plain
string overrode the study kind's ``string | null`` in ``_envelope``), and the brief's ``facts`` is null (keel-api's
``BriefResponse.facts: Json | None``; the schema said object).

SEEDS (each reversed by reversing the edit):
- ``_STUDY_PROPS["subjects"]`` back to ``_ARR`` reds the study, share and view_set arms on both profiles ("{'total':
  1} is not of type 'array'"). The plan arm (its ``subjects`` is a served list) and the run arms (a submit carries no
  subjects) stay green.
- ``_envelope`` letting the error set win again reds both run arms ("None is not of type 'string'" at
  ``quota_notice``).
- RI-592: ``mix`` out of ``CHANNEL_MAP["study"].drop`` reds every study read that serves a mix ("channel map: study
  field 'mix' has no declared home"). A served field with no home now fails here; it used to only warn. Turning the
  warning into a failure found 13 such fields at once (``mix``, ``coin_choices`` dropped; ``children``, ``added``,
  ``refused``, ``filling``, ``by_keel`` and a project read's six fields homed as structured, spec 07 §3.3).
Not vacuous: the served-body counts are asserted per tool, and the world must exist (a missing world fails, never
skips).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
from typing import Any
from unittest.mock import patch

import jsonschema
import pytest


REPO = pathlib.Path(__file__).resolve().parents[4]
WORLD_DIR = REPO / "services" / "keel-app" / "e2e" / "mocked" / "fixtures" / "research"


def _world() -> dict:
    path = WORLD_DIR / "world.gen.json"
    assert path.exists(), f"the Research mock world is missing: {path}"
    return json.loads(path.read_text())


WORLD = _world()


def _latest(versions: dict) -> dict:
    return versions[max(versions, key=int)]


def _rows(study_id: str) -> list[dict] | None:
    path = WORLD_DIR / "rows.gen" / f"{study_id}.json"
    if not path.exists():
        return None
    by_version = json.loads(path.read_text())
    return by_version[max(by_version, key=int)]


def _list_body() -> dict:
    rows = WORLD["list"]
    by: dict[str, int] = {}
    for r in rows:
        by[str(r["state"])] = by.get(str(r["state"]), 0) + 1
    return {
        "data": rows[:25],
        "pagination": {"cursor": "25" if len(rows) > 25 else None, "has_more": len(rows) > 25},
        "studies_total": len(rows),
        "by_state": by,
        "view": None,
    }


def _brief_body() -> dict:
    """keel-api's brief, as the mock serves it (routes.ts ``GET /v1/research/brief``)."""
    rows = WORLD["list"]
    body = _list_body()
    aside = [r for r in rows if r.get("set_aside")]
    planned = [r for r in rows if r.get("state") == "planned"]
    return {
        "projects": WORLD["brief_projects"],
        "projects_total": len(WORLD["brief_projects"]),
        "studies": body["data"],
        "studies_total": body["studies_total"],
        "by_state": body["by_state"],
        "facts": None,
        "pagination": body["pagination"],
        "versions_tried": WORLD["brief_versions_tried"],
        "families_total": len(WORLD["brief_versions_tried"]),
        "set_aside": aside,
        "set_aside_total": len(aside),
        "planned": planned,
        "planned_total": len(planned),
        "notes": WORLD["brief_notes"],
        "notes_total": len(WORLD["brief_notes"]),
        "paper_and_live": [],
        "paper_and_live_total": 0,
        "view": None,
        "next": None,
    }


def _data_page(study_id: str, view: dict) -> dict:
    """keel-api's data page for one view (the mock's ``dataPage``): the study block, the first 25 rows, the facts."""
    study = _latest(WORLD["studies"][study_id])["study"]
    rows = view["rows"]
    return {
        "study": study,
        "data": rows[:25],
        "pagination": {"cursor": "25" if len(rows) > 25 else None, "has_more": len(rows) > 25},
        "facts": view.get("facts"),
        "filling": view.get("filling"),
        "view": {
            "kind": "study",
            "size": "view",
            "markdown": f"{view['title']} · rows {min(len(rows), 25)} of {len(rows)}",
            "object": study_id,
            "at": "2026-11-12T12:00:00Z",
            "seq": 0,
        },
    }


class _KeelApi:
    """keel-api as the mock world answers it: one served body per route the research view tools call."""

    def __init__(self) -> None:
        self.answer: dict[tuple[str, str], Any] = {}

    def route(self, method: str, path: str) -> Any:
        if (method, path) in self.answer:
            return self.answer[(method, path)]
        parts = path.strip("/").split("/")
        if method == "get" and path == "/v1/research/studies":
            return _list_body()
        if method == "get" and path == "/v1/research/brief":
            return _brief_body()
        if method == "get" and parts[:3] == ["v1", "research", "studies"] and len(parts) == 4:
            return _latest(WORLD["studies"][parts[3]])
        if method == "get" and parts[:3] == ["v1", "research", "shares"] and len(parts) == 4:
            return WORLD["shares"][parts[3]]["body"]
        if method == "get" and parts[:3] == ["v1", "research", "projects"] and len(parts) == 4:
            return WORLD["projects"][parts[3]]
        raise AssertionError(f"no served body for {method.upper()} {path}")


def _call(api: _KeelApi, profile: str, tool: str, args: dict, *, data: dict | None = None):
    """``tool(args)`` through the real server; returns (structuredContent, published outputSchema)."""
    from keel.tools.outcomes._toolsets import research_served_context

    def get(self, path, *, headers=None, **params):
        if data is not None and path.endswith("/data"):
            return data
        return api.route("get", path)

    def post(self, path, json=None, *, headers=None, **params):
        return api.route("post", path)

    def put(self, path, json=None, **params):
        return api.route("put", path)

    async def go():
        from keel.mcp.server import create_server

        server = create_server()
        tools = {t.name: t for t in await server.list_tools()}
        result = await server.call_tool(tool, args)
        return result.structured_content, tools[tool].to_mcp_tool().outputSchema

    # RI-592: a served field the channel map gives no home only WARNED ("channel map: study field 'mix' has no
    # declared home") and rode into structuredContent unreviewed. Here it fails the call instead.
    unmapped: list[str] = []

    class _Unmapped(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "has no declared home" in record.getMessage():
                unmapped.append(record.getMessage())

    channels_log = logging.getLogger("keel.tools.outcomes._channels")
    handler = _Unmapped(level=logging.WARNING)
    channels_log.addHandler(handler)

    saved = os.environ.get("KEEL_SERVER_PROFILE")
    os.environ["KEEL_SERVER_PROFILE"] = profile
    try:
        with (
            research_served_context(True),
            patch("keel.client.KeelClient.get", get),
            patch("keel.client.KeelClient.post", post),
            patch("keel.client.KeelClient.put", put),
        ):
            out = asyncio.run(go())
        assert not unmapped, (tool, unmapped)
        return out
    finally:
        channels_log.removeHandler(handler)
        if saved is None:
            os.environ.pop("KEEL_SERVER_PROFILE", None)
        else:
            os.environ["KEEL_SERVER_PROFILE"] = saved


def _valid(tool: str, structured: dict, schema: dict, what: str) -> None:
    assert structured, (tool, what)
    assert "code" not in structured, (tool, what, structured.get("code"), structured.get("message"))
    try:
        jsonschema.validate(structured, schema)
    except jsonschema.ValidationError as exc:
        raise AssertionError(f"{tool} ({what}): {exc.message} at {list(exc.absolute_path)}") from None


PROFILES = ("full", "listed")


@pytest.mark.parametrize("profile", PROFILES)
def test_get_a_study_validates_for_every_served_study(profile: str) -> None:
    api = _KeelApi()
    studies = sorted(WORLD["studies"])
    with_subjects = 0
    for study_id in studies:
        sc, schema = _call(api, profile, "keel_research_get", {"study_id": study_id})
        _valid("keel_research_get", sc, schema, study_id)
        with_subjects += isinstance(sc.get("subjects"), dict)
    # Not vacuous: every served study read, and most carry the subjects the strict client refused.
    assert len(studies) >= 40 and with_subjects >= 30, (len(studies), with_subjects)


@pytest.mark.parametrize("profile", PROFILES)
def test_get_with_a_view_validates(profile: str) -> None:
    api = _KeelApi()
    read = 0
    for study_id in sorted(WORLD["studies"]):
        views = _rows(study_id)
        if not views:
            continue
        view = views[0]
        args = {"study_id": study_id, "view": view["index"]}
        sc, schema = _call(api, profile, "keel_research_get", args, data=_data_page(study_id, view))
        _valid("keel_research_get", sc, schema, f"{study_id} view {view['index']}")
        read += 1
    assert read >= 20, read


@pytest.mark.parametrize("profile", PROFILES)
def test_get_a_share_the_list_and_a_project_validate(profile: str) -> None:
    api = _KeelApi()
    shares = [k for k, s in sorted(WORLD["shares"].items()) if s["status"] == 200]
    for share_id in shares:
        sc, schema = _call(api, profile, "keel_research_get", {"share_id": share_id})
        _valid("keel_research_get", sc, schema, share_id)
    sc, schema = _call(api, profile, "keel_research_get", {})
    _valid("keel_research_get", sc, schema, "the study list")
    for project_id in sorted(WORLD["projects"]):
        sc, schema = _call(api, profile, "keel_research_get", {"project_id": project_id})
        _valid("keel_research_get", sc, schema, project_id)
    assert len(shares) >= 5 and WORLD["projects"], (len(shares), list(WORLD["projects"]))


@pytest.mark.parametrize("profile", PROFILES)
def test_plan_validates_for_every_served_plan(profile: str) -> None:
    api = _KeelApi()
    plans = sorted(WORLD["plans"])
    for plan_id in plans:
        api.answer[("post", "/v1/research/plans")] = WORLD["plans"][plan_id]
        sc, schema = _call(
            api,
            profile,
            "keel_research_plan",
            {"template": "check", "subjects": ['Strategy("str_x@1")']},
        )
        _valid("keel_research_plan", sc, schema, plan_id)
    assert len(plans) >= 20, len(plans)


@pytest.mark.parametrize("profile", PROFILES)
def test_run_validates_for_every_served_submit(profile: str) -> None:
    api = _KeelApi()
    submits = 0
    for run_set, preview in sorted(WORLD["preview_runs"].items()):
        for answer in ("submitted", "running", "completed"):
            api.answer[("post", "/v1/research/runs")] = preview["answers"][answer]
            sc, schema = _call(
                api, profile, "keel_research_run", {"plan_id": preview["plan_id"], "wait": False}
            )
            _valid("keel_research_run", sc, schema, f"{run_set} {answer}")
            submits += 1
    assert submits >= 6, submits


@pytest.mark.parametrize("profile", ("full",))
def test_view_set_and_brief_validate(profile: str) -> None:
    """view_set and brief are on the full profile only (07 §2.2: never listed)."""
    api = _KeelApi()
    study_id = "stu_siglowvol00000000000000000"
    read = _latest(WORLD["studies"][study_id])
    api.answer[("put", f"/v1/research/studies/{study_id}/presentation")] = read
    sc, schema = _call(
        api,
        profile,
        "keel_research_view_set",
        {"study_id": study_id, "expected_revision": read["study"].get("revision") or 1, "source": "x"},
    )
    _valid("keel_research_view_set", sc, schema, study_id)
    assert isinstance(sc.get("subjects"), dict)  # not vacuous: the reduced subjects reached the check
    sc, schema = _call(api, profile, "keel_research_brief", {})
    _valid("keel_research_brief", sc, schema, "the brief")


#: 07 §3.3's structuredContent budget (AT-5): compact JSON, in bytes.
STRUCTURED_BUDGET = 8_000


@pytest.mark.parametrize("profile", PROFILES)
def test_a_100_item_project_read_stays_inside_the_budget(profile: str) -> None:
    """RI-593: a project read serves up to 100 items (keel-api's limit), each about 150 bytes, which alone is about
    15 KB. structuredContent carries them as a count, ``{total}``, as it does subjects (RL-58). The text names
    every item, and the API keeps the list. SEED (reversed by reversing the edit): drop the ``items`` count from
    ``structured_study``, and it reads 19,695 bytes and reds."""
    api = _KeelApi()
    project = WORLD["projects"]["prj_fixture1"]
    rows = project["items"]
    items = [
        {**rows[i % len(rows)], "name": f"{rows[i % len(rows)]['name']} · item {i:03d} with a long name"}
        for i in range(100)
    ]
    api.answer[("get", "/v1/research/projects/prj_hundred")] = {
        **project,
        "project_id": "prj_hundred",
        "items": items,
        "items_total": 100,
    }
    sc, schema = _call(api, profile, "keel_research_get", {"project_id": "prj_hundred"})
    _valid("keel_research_get", sc, schema, "a 100-item project")
    size = len(json.dumps(sc, separators=(",", ":")).encode())
    # Not vacuous: the served list alone is far over the budget.
    assert len(json.dumps(items, separators=(",", ":")).encode()) > 2 * STRUCTURED_BUDGET
    assert size <= STRUCTURED_BUDGET, size
    assert sc["items"] == {"total": 100} and sc["items_total"] == 100
