"""Gate S, the agent half (BUILD-PLAN §16; 07 §10): one golden transcript per research tool and verb.

Each transcript is a call, the requests the SDK sends keel-api (method, path,
body or query, the Idempotency-Key's shape) and the envelope it returns (the
text block and the structured fields), against a fixed keel-api answer. The
golden file is ``tests/fixtures/research_transcripts.json``; regenerate it with
``python tests/test_research_transcripts.py --write`` (from the SDK root, in
the SDK's own venv) and review the diff: every changed line is a change in
what an agent sends or reads. Every envelope of a view tool validates against
its declared output schema (07 §3.1, AT-3).

Seeded red (reverted by reversing the edit): change one field in the golden
file (e.g. ``keel_research_cancel``'s path), and that transcript reds.
Not vacuous: a transcript for every research command the registry carries
(24), and the count is appended to ``$GATE_COUNTS_FILE`` as ``transcripts``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest


SDK = Path(__file__).resolve().parents[1]
GOLDEN = SDK / "tests" / "fixtures" / "research_transcripts.json"

#: keel-api's REAL answers (RI-615, WL-AG-01): ``fixtures/research_server_shapes.json`` holds what keel-api's routes
#: answered on a disposable Postgres (the brief, list, project and every receipt) and, for the reads that need a run
#: (a done study, a share, a plan, a data page), the mocked world's envelopes each validated against keel-api's
#: closed route model; every entry says which in ``_provenance``. Until RI-615 this file's own hand-built study
#: footing used ``coins.n`` and ``costs.fee_bp`` and its brief ``study_id``, names the server never sends, so the
#: golden pinned "costs 0 bp a fill" and "- None · Check · …". Regenerate the shapes with
#: ``record_shapes.py`` (keel-artifacts lane-scratch/AGENT/), then this golden.
SHAPES = {
    k: {f: v for f, v in body.items() if f != "_provenance"}
    for k, body in json.loads(
        (SDK / "tests/fixtures/research_server_shapes.json").read_text()
    ).items()
}
_STUDY = SHAPES["study_done"]
SID = _STUDY["study"]["study_id"]
RUS = _STUDY["study"]["run_set_id"]
SHARE = SHAPES["share"]["study"]["share_id"]
_PLAN = SHAPES["plan"]
PLAN_ID = _PLAN["plan_id"]
_PAGE = SHAPES["data_page"]

#: keel-api's fixed answers: (method, path prefix) → body. Longest prefix wins.
ANSWERS: dict[tuple[str, str], Any] = {
    ("post", "/v1/research/plans"): _PLAN,
    ("post", "/v1/research/runs"): {**_STUDY, "progress": None},
    ("post", "/v1/research/saves"): _STUDY,
    ("post", f"/v1/research/studies/{SID}/versions/2/run"): _STUDY,
    ("post", f"/v1/research/studies/{SID}/add-plan"): SHAPES["add_plan"],
    ("get", f"/v1/research/studies/{SID}/versions/1/data"): _PAGE,
    ("get", f"/v1/research/studies/{SID}/shares"): SHAPES["shares_list"],
    ("get", f"/v1/research/studies/{SID}"): _STUDY,
    ("get", "/v1/research/studies"): SHAPES["list"],
    ("get", f"/v1/research/shares/{SHARE}"): SHAPES["share"],
    ("get", "/v1/research/addresses"): SHAPES["addresses"],
    ("put", f"/v1/research/studies/{SID}/presentation"): {**_STUDY, "saved": "Saved revision 2."},
    ("get", "/v1/research/brief"): SHAPES["brief"],
    ("post", "/v1/research/notes"): SHAPES["note"],
    (
        "post",
        f"/v1/research/run-sets/{RUS}/cancel",
    ): SHAPES["cancel"],
    ("post", f"/v1/research/run-sets/{RUS}/resume"): _STUDY,
    ("post", f"/v1/research/run-sets/{RUS}/retry"): _STUDY,
    ("post", f"/v1/research/studies/{SID}/make-signal"): SHAPES["make_signal"],
    ("get", "/v1/research/steps"): SHAPES["steps"],
    ("post", f"/v1/research/shares/{SHARE}/copies"): SHAPES["copy"],
    ("post", "/v1/research/projects"): SHAPES["project_create"],
    ("post", f"/v1/research/studies/{SID}/set-aside"): SHAPES["set_aside"],
    ("post", f"/v1/research/studies/{SID}/restore"): SHAPES["restore"],
    ("post", f"/v1/research/studies/{SID}/archive"): SHAPES["archive"],
    ("delete", f"/v1/research/studies/{SID}"): SHAPES["delete"],
}

#: One call per research command (the 07 §2.2 rows).
CALLS: dict[str, dict] = {
    "keel_research_plan": {"template": "check", "subjects": ['Strategy("str_a@3")']},
    "keel_research_run": {"plan_id": PLAN_ID, "wait": False},
    "keel_research_get": {"study_id": SID},
    "keel_research_view_set": {"study_id": SID, "source": "Bars()", "expected_revision": 1},
    "keel_research_brief": {},
    "keel_research_note": {"anchor": SID, "kind": "decision", "body": "keep v3"},
    "keel_research_cancel": {"run_set_id": RUS},
    "keel_research_make_signal": {
        "study_id": SID,
        "version": 1,
        "subject": "mix",
        "name": "Mix",
    },
    "keel_research_add": {"study_id": SID, "add": ['Output("str_a@1", output="x")']},
    "keel_research_save": {"plan_id": PLAN_ID},
    "keel_research_run_draft": {"study_id": SID, "version": 2},
    "keel_research_watch": {"study_id": SID},
    "keel_research_data": {"study_id": SID, "view": 1},
    "keel_research_list": {},
    "keel_research_resume": {"run_set_id": RUS},
    "keel_research_retry": {"run_set_id": RUS},
    "keel_research_steps": {"name": "CostStress"},
    "keel_research_copy": {"share_id": SHARE},
    "keel_research_project": {"action": "create", "name": "p"},
    "keel_research_set_aside": {"study_id": SID, "reason": "same as the library's"},
    "keel_research_restore": {"study_id": SID},
    "keel_research_archive": {"study_id": SID},
    "keel_research_delete": {"study_id": SID},
    "keel_research_shares": {"study_id": SID},
}


class RecordingClient:
    #: The API these fixed answers stand for (production): every link the SDK prints follows the API the call's
    #: client names (``open_in_app.app_base``). The transcripts are computed at import, before the suite's config
    #: isolation, so without it a developer's stored login (staging) moved every golden link (STG-SURFACES #18).
    _config = type("Config", (), {"api_url": "https://api.usekeel.io"})()

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def _answer(self, method: str, path: str, **payload: Any) -> Any:
        headers = payload.pop("headers", None) or {}
        record = {"method": method, "path": path, **{k: v for k, v in payload.items() if v}}
        if "Idempotency-Key" in headers:
            record["idempotency_key"] = re.sub(
                r"[0-9a-f-]{36}$", "<uuid>", headers["Idempotency-Key"]
            )
        self.requests.append(record)
        best = max(
            (k for k in ANSWERS if k[0] == method and path.startswith(k[1])),
            key=lambda k: len(k[1]),
            default=None,
        )
        assert best is not None, f"no fixed answer for {method} {path}"
        return json.loads(json.dumps(ANSWERS[best]))

    def get(self, path, *, headers=None, **params):
        return self._answer("get", path, query=params, headers=headers)

    def post(self, path, json=None, *, headers=None, **params):
        # a POST's query rides the record too (``?card=full`` on the run routes, WL-SV-21)
        return self._answer("post", path, body=json, query=params, headers=headers)

    def put(self, path, json=None):
        return self._answer("put", path, body=json)

    def patch(self, path, json=None):
        return self._answer("patch", path, body=json)

    def delete(self, path, **params):
        return self._answer("delete", path, query=params)


def _masked(envelope: dict) -> dict:
    view = envelope.get("view")
    if isinstance(view, dict):
        # The election key's instant and counter differ per call (`_base.election_key`).
        masks = {"at": "1970-01-01T00:00:00.000Z", "seq": 0}
        envelope["view"] = {k: masks.get(k, v) for k, v in view.items()}
    return envelope


def transcript(name: str) -> dict:
    """One call through the registered tool, on the full profile."""
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    client = RecordingClient()
    result = OUTCOMES[name].handler(dict(CALLS[name]), ToolContext(api_client=client))
    envelope = _masked(json.loads(json.dumps(result.to_envelope(), default=str)))
    return {"call": CALLS[name], "requests": client.requests, "envelope": envelope}


def all_transcripts() -> dict[str, dict]:
    os.environ["KEEL_SERVER_PROFILE"] = "full"
    return {name: transcript(name) for name in sorted(CALLS)}


@pytest.fixture(scope="module")
def produced() -> dict[str, dict]:
    return all_transcripts()


def test_every_research_command_has_a_transcript(produced) -> None:
    from keel.tools.outcomes import OUTCOMES

    research = {n for n in OUTCOMES if n.startswith("keel_research_")}
    assert set(CALLS) == research and len(research) == 24
    golden = json.loads(GOLDEN.read_text())
    assert set(golden) == research
    counts = os.environ.get("GATE_COUNTS_FILE")
    if counts:
        with open(counts, "a") as fh:
            fh.write(f"transcripts={len(golden)}\n")


@pytest.mark.parametrize("name", sorted(CALLS))
def test_the_transcript_is_the_golden_one(produced, name: str) -> None:
    golden = json.loads(GOLDEN.read_text())
    assert produced[name] == golden[name], (
        f"{name}: what the SDK sends or returns changed; review and rerun "
        "`python tests/test_research_transcripts.py --write`"
    )


@pytest.mark.parametrize("name", sorted(CALLS))
def test_a_view_tool_envelope_validates_against_its_schema(produced, name: str) -> None:
    import jsonschema
    from keel.tools.outcomes._output_schemas import OUTPUT_SCHEMAS

    from keel.tools.outcomes._channels import partition

    schema = OUTPUT_SCHEMAS.get(name)
    if schema is None:
        pytest.skip(f"{name} answers in text (07 §2.2)")
    # The schema is what MCP publishes for structuredContent, which is the envelope after the study channel's
    # reduction (07 §3.3: check rows keep 5, lists become counts). With the server-shaped done study (9 bars) the
    # raw envelope is not that payload; the old one-bar fixture let the two read as one.
    structured, _card = partition(produced[name]["envelope"], "study", move_card=True)
    jsonschema.validate(structured, schema)


if __name__ == "__main__":
    if "--write" not in sys.argv:
        sys.exit("usage: python tests/test_research_transcripts.py --write")
    sys.path.insert(0, str(SDK))
    GOLDEN.write_text(
        json.dumps(all_transcripts(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    print(f"wrote {GOLDEN.relative_to(SDK)}")
