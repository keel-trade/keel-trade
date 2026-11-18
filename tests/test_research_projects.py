"""RI-178: the project tool's item calls carry 08's ``ref_kind`` in a body, a project is read through D-177's
route, and a study linked from a deleted source reads its served words (D-176), never "from None v1".

D-189 (07 §5, a read is read-scoped): the project read is ``keel_research_get`` with ``project_id``
(``backtest.research.read``), never an action of ``keel_research_project`` (``backtest.research.update``), so a
read-only token reads a project. SEED (reversed): ``show`` put back in ``keel_research_project``'s action enum ->
the scope arm fails (1 failed, 7 passed); the handler's ``project_id`` branch removed -> the read arm (it lists the
user's studies instead) and the mixture arm fail (2 failed, 6 passed).

SEED (reversed): ``_item_body`` without ``ref_kind`` -> the add, remove and stated-kind arms fail (3 failed, 3
passed); the DELETE sent as params again -> the remove arm fails (1 failed, 5 passed); ``_first_line`` printing
``from {study_id} v{version}`` whatever the source -> the deleted-source arm fails, it reads "from None v1" (1
failed, 5 passed).
"""

from __future__ import annotations

from typing import Any

import pytest
from keel.errors import UsageError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._research_view import study_text


_bootstrap()

_PROJECT = {
    "project_id": "prj_a",
    "name": "Momentum work",
    "question": "Does momentum survive costs?",
    "archived": False,
    "items": [
        {
            "ref_kind": "study",
            "ref": "stu_a",
            "name": "Momentum rotation v3",
            "available": True,
            "words": None,
        },
        {
            "ref_kind": "strategy",
            "ref": "str_b@2",
            "name": None,
            "available": False,
            "words": "No longer available",
        },
    ],  # fmt: skip
    "items_total": 2,
}


class _Client:
    """Records each request: (method, path, params, json)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, Any]] = []

    def get(self, path, *, headers=None, **params):
        self.calls.append(("get", path, params, None))
        return _PROJECT

    def post(self, path, json=None, *, headers=None):
        self.calls.append(("post", path, {}, json))
        return _PROJECT

    def delete(self, path, **params):
        self.calls.append(("delete", path, params, None))
        return _PROJECT

    def _request(self, method, path, **kwargs):
        self.calls.append((method.lower(), path, kwargs.get("params") or {}, kwargs.get("json")))
        return _PROJECT


def _project(args: dict) -> tuple[_Client, Any]:
    client = _Client()
    got = OUTCOMES["keel_research_project"].handler(args, ToolContext(api_client=client))
    return client, got


def test_add_sends_the_kind_its_ref_names_in_the_body() -> None:
    client, _ = _project({"action": "add", "project_id": "prj_a", "ref": "stu_a"})
    assert client.calls == [
        ("post", "/v1/research/projects/prj_a/items", {}, {"ref_kind": "study", "ref": "stu_a"})
    ]


def test_remove_sends_a_body_not_params() -> None:
    client, _ = _project({"action": "remove", "project_id": "prj_a", "ref": "str_b@2"})
    assert client.calls == [
        (
            "delete",
            "/v1/research/projects/prj_a/items",
            {},
            {"ref_kind": "strategy", "ref": "str_b@2"},
        )
    ]


def test_a_ref_whose_kind_its_prefix_does_not_name_needs_ref_kind() -> None:
    with pytest.raises(UsageError, match="--ref-kind"):
        _project({"action": "add", "project_id": "prj_a", "ref": "base.signal"})
    client, _ = _project(
        {"action": "add", "project_id": "prj_a", "ref": "base.signal", "ref_kind": "output"}
    )
    assert client.calls[0][3] == {
        "ref_kind": "output",
        "ref": "base.signal",
    }  # control: stated, it is sent


def _get(args: dict) -> tuple[_Client, Any]:
    client = _Client()
    got = OUTCOMES["keel_research_get"].handler(args, ToolContext(api_client=client))
    return client, got


def test_a_project_is_read_by_the_read_scoped_tool() -> None:
    """D-189: the read tool is read-scoped and takes ``project_id``; the project tool keeps only writes."""
    get, project = OUTCOMES["keel_research_get"], OUTCOMES["keel_research_project"]
    assert get.required_action == "backtest.research.read"
    assert get.input_schema["properties"]["project_id"]["pattern"] == "^prj_"
    actions = project.input_schema["properties"]["action"]["enum"]
    assert "show" not in actions and len(actions) == 5, actions  # non-vacuity: the 5 writes remain
    assert project.required_action == "backtest.research.update"


def test_a_project_id_with_a_study_is_refused() -> None:
    with pytest.raises(UsageError, match="project_id reads a project"):
        _get({"project_id": "prj_a", "study_id": "stu_a"})


def test_get_reads_the_project_and_names_each_item() -> None:
    client, got = _get({"project_id": "prj_a"})
    assert client.calls == [("get", "/v1/research/projects/prj_a", {}, None)]
    text = got.extra["view"]["markdown"]
    # WL-AG-02 (A3 #3): each item by kind, name AND id (structuredContent carries only the count, RI-593), the
    # call that opens a study, and the View in Keel line.
    assert text.splitlines()[:3] == [
        "Project · Momentum work · prj_a",
        "Does momentum survive costs?",
        "Items: 2 shown of 2",
    ]
    assert "- study · Momentum rotation v3 · stu_" in text and "- strategy · No longer available" in text
    assert 'Open a study: keel_research_get(study_id="stu_' in text
    assert text.splitlines()[-1].startswith("View in Keel: ")


def _linked(linked_from: dict) -> str:
    env = {
        "study": {
            "study_id": "stu_b", "version": 1, "title": "Momentum on later days", "state": "completed",
            "kind_label": {"key": "check", "name": "Check a strategy", "short": "Check"},
            "linked_from": linked_from,
        },
    }  # fmt: skip
    return study_text(env).splitlines()[0]


def test_a_study_linked_from_a_deleted_source_reads_the_served_words() -> None:
    line = _linked(
        {"study_id": None, "version": 1, "reason": "days", "title": None,
         "words": "Linked from a study that was deleted"}
    )  # fmt: skip
    assert "None" not in line and line.endswith("· Linked from a study that was deleted"), line


def test_control_a_live_source_keeps_its_id_for_the_next_call() -> None:
    line = _linked(
        {"study_id": "stu_a", "version": 3, "reason": "days", "title": "Momentum rotation v3",
         "words": "Linked from Momentum rotation v3"}
    )  # fmt: skip
    assert "from stu_a v3 (Linked from Momentum rotation v3)" in line, line
