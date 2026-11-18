"""Research's receipt tools over MCP (07 §2.2 "text (receipt line)", §4.8, §4.11; RI-220).

note, cancel and make_signal answer in text: the text block is the receipt the handler wrote, and the served
object rides as ``structuredContent``, so a caller reads ``ref`` and ``next`` as fields. Before RI-220 the adapter
registered them as legacy ``-> str`` tools, FastMCP wrapped the envelope as ``{"result": "<JSON string>"}``, and
the journey's J08.2 found ``structured["next"]`` absent.

Every arm calls the real tool through the real server (``keel.mcp.server.create_server``, the full profile), with
keel-api answered by a fake ``KeelClient``: the handler, ``_make_handler``'s envelope and the adapter's mode all
run as served.

SEED (reverted by reversing the edit): drop the ``RESEARCH_RECEIPT_TOOLS`` branch from
``_mcp_adapter.registration_mode`` and the three object arms, the refusal arm and the probe arm red (the
structuredContent is ``{"result": …}`` again), while the legacy control stays green.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from keel.errors import translate_http_error
from keel.tools.outcomes._channels import NONVIEW_TEXT_ENV
from keel.tools.outcomes._output_schemas import OUTPUT_SCHEMAS, RESEARCH_RECEIPT_TOOLS


#: keel-api's own shapes (research_backend.make_signal): the ref is the new signal's full Output, and `next` is the
#: call that adds it to the comparison.
REF = 'Output("str_new0000000000000000000@1", output="mix")'
NEXT = f"keel_research_plan(study_id=\"stu_a\", add=['{REF}'])"

#: keel-api's answers, shaped as its response models (schemas/research.py MakeSignalResponse, NoteResponse,
#: CancelResponse).
ANSWERS: dict[str, Any] = {
    "/make-signal": {
        "strategy_id": "str_new0000000000000000000",
        "version": 1,
        "ref": REF,
        "provenance": {"study_id": "stu_a", "version": 1, "mix": "mix_equal"},
        "words": "Saved your mix of str_a, str_b (equal weights) as a new signal, v1. Nothing ran.",
        "next": NEXT,
    },
    "/v1/research/notes": {
        "note_id": "nte_a",
        "anchor": "stu_a",
        "kind": "decision",
        "author_role": "agent",
        "source": "mcp_full",
        "created_at": "2026-11-14T18:00:00Z",
        "words": "Noted on Momentum rotation v3.",
    },
    "/cancel": {
        "run_set_id": "rus_a",
        "state": "running",
        "progress": {
            "total": 9,
            "by_status": {"done": 4, "running": 1, "queued": 4},
            "words": "4 of 9 done",
        },
        "words": "Stopping. Runs already started will finish.",
        "links": {"app": "https://app.usekeel.io/research/stu_a"},
    },
}

#: Each receipt tool's call, and the served field its object must carry (non-vacuity: a field only keel-api's
#: answer holds, never one the SDK adds).
CALLS: dict[str, tuple[dict[str, Any], str]] = {
    "keel_research_make_signal": (
        {"study_id": "stu_a", "version": 1, "subject": "mix_equal", "name": "Equal mix"},
        "ref",
    ),
    "keel_research_note": ({"anchor": "stu_a", "kind": "decision", "body": "Keep v3."}, "note_id"),
    "keel_research_cancel": ({"run_set_id": "rus_a"}, "progress"),
}


class FakeClient:
    """keel-api: answers each POST from ``ANSWERS`` by path suffix, or raises ``refuse``."""

    def __init__(self, refuse: tuple[int, dict] | None = None) -> None:
        self.refuse = refuse
        self.paths: list[str] = []

    def post(self, path: str, json: Any = None, *, headers: Any = None, **params: Any) -> Any:
        self.paths.append(path)
        if self.refuse is not None:
            status, body = self.refuse
            raise translate_http_error(status, _dumps(body))
        for suffix, answer in ANSWERS.items():
            if path.endswith(suffix):
                return dict(answer)
        raise AssertionError(f"unexpected POST {path}")


def _dumps(body: dict) -> str:
    return json.dumps(body)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch):
    """The full-profile server, its keel-api a ``FakeClient``; returns ``(call, client)``."""
    import keel.client as keel_client

    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    monkeypatch.delenv(NONVIEW_TEXT_ENV, raising=False)
    client = FakeClient()
    monkeypatch.setattr(keel_client, "KeelClient", lambda *a, **k: client)

    def call(tool: str, args: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        from keel.mcp.server import create_server

        async def go():
            server = create_server()
            listed = {t.name: t for t in await server.list_tools()}
            return await server.call_tool(tool, args), listed

        result, listed = asyncio.run(go())
        return result, listed

    return call, client


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_a_receipt_tool_serves_the_object_and_the_receipt(served, tool: str) -> None:
    call, client = served
    args, field = CALLS[tool]
    result, listed = call(tool, args)
    assert client.paths, "the handler never reached keel-api"  # non-vacuous: the real handler ran
    structured = result.structured_content
    assert isinstance(structured, dict) and "result" not in structured, structured
    assert (
        structured[field]
        == next(v for k, v in ANSWERS.items() if client.paths[-1].endswith(k))[field]
    )
    assert not getattr(result, "is_error", False)
    # The text block is the receipt (07 §4.8, §4.11), never the envelope as JSON; the markdown is not duplicated.
    text = result.content[0].text
    assert not text.lstrip().startswith("{"), text
    assert "markdown" not in structured.get("view", {})
    # No declared schema (07 §3.3 declares the five view tools'), and no inferred `{"result": string}` wrapper.
    assert listed[tool].to_mcp_tool().outputSchema is None
    assert tool not in OUTPUT_SCHEMAS


def test_make_signal_reads_as_0711(served) -> None:
    """J08.2 (09 J8 row 2): the words, then the one call that adds the full Output; ref and next are fields."""
    call, _ = served
    result, _ = call("keel_research_make_signal", CALLS["keel_research_make_signal"][0])
    structured = result.structured_content
    # The journey's own reading of the object (j08_make_signal_test.make_signal).
    assert structured["ref"] == REF
    assert "Output(" in structured["next"] and structured["ref"] in structured["next"]
    # 07 §4.11's text: the next call said once (keel-api's `next` is the same add call the receipt names).
    assert result.content[0].text.splitlines() == [
        "Saved your mix of str_a, str_b (equal weights) as a new signal, v1. Nothing ran.",
        f'Add it to the comparison: keel_research_plan(study_id="stu_a", add={json.dumps([REF])})',
        "View in Keel: https://app.usekeel.io/research/stu_a",  # 07 §3.2 rule 9 (WL-AG-18)
    ]


def test_a_notes_source_is_the_surface_not_a_dsl_line(served) -> None:
    """07 §4.8: the note's `source` is the surface that wrote it; the catalogue's DSL `source:` line never draws it."""
    call, _ = served
    result, _ = call("keel_research_note", CALLS["keel_research_note"][0])
    assert result.structured_content["source"] == "mcp_full"
    # A3 #12 (07 §3.2 rule 9): the receipt's served words, then "View in Keel" at the study.
    assert result.content[0].text.splitlines() == [
        "Noted on Momentum rotation v3.",
        "View in Keel: https://app.usekeel.io/research/stu_a",
    ]


def test_a_refusal_is_the_whole_envelope_and_an_error(served, monkeypatch) -> None:
    import keel.client as keel_client

    call, _ = served
    refusing = FakeClient(
        refuse=(
            422,
            {
                "code": "RESEARCH_BLEND_NOT_A_STRATEGY",
                "detail": "A blend of strategies stays a what-if; it is not saved as a strategy.",
            },
        )
    )
    monkeypatch.setattr(keel_client, "KeelClient", lambda *a, **k: refusing)
    result, _ = call("keel_research_make_signal", CALLS["keel_research_make_signal"][0])
    assert refusing.paths  # non-vacuous: the refusal came from keel-api, not the pre-flight
    assert result.is_error is True
    structured = result.structured_content
    assert isinstance(structured, dict) and "result" not in structured, structured
    assert structured["code"] == "RESEARCH_BLEND_NOT_A_STRATEGY"


def test_one_shape_under_the_non_view_probe_arm(served, monkeypatch) -> None:
    """Deployed servers run the non-view arm (KEEL_NONVIEW_TEXT_ONLY=1): a receipt tool keeps its object there."""
    monkeypatch.setenv(NONVIEW_TEXT_ENV, "1")
    call, _ = served
    result, listed = call("keel_research_make_signal", CALLS["keel_research_make_signal"][0])
    assert result.structured_content["ref"] == REF
    assert listed["keel_research_make_signal"].to_mcp_tool().outputSchema is None


def test_control_a_legacy_tool_keeps_its_wrapper(served) -> None:
    """CONTROL: the receipt mode is the three research tools only; a legacy tool keeps `{"result": string}`."""
    call, _ = served
    assert len(RESEARCH_RECEIPT_TOOLS) == 3 and RESEARCH_RECEIPT_TOOLS == set(CALLS)
    result, listed = call("keel_help", {"topic": "dsl_syntax"})
    assert result.structured_content == {"result": result.content[0].text}
    assert listed["keel_help"].to_mcp_tool().outputSchema.get("x-fastmcp-wrap-result")
    # Non-vacuous: every receipt tool is served on the full profile.
    assert RESEARCH_RECEIPT_TOOLS <= set(listed)


# ── RI-241: a research view tool's text never draws the study form as a DSL `source:` block ─────────────────────

#: The owner's stored form, served as the read's top-level `source` (07 §3.1; up to 131,072 bytes).
STUDY_FORM = (
    'Study(title="Momentum rotation v3")\n'
    'Subjects(v3=Strategy("str_a0000000000000000000@3"))\n'
    "Steps(base=Backtest())\n"
)


class FakeReads:
    """keel-api's study read (GET /v1/research/studies/{id}): ``_study`` with the owner's stored form."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def get(self, path: str, *, headers: Any = None, **params: Any) -> Any:
        from tests.test_research_tools import _study

        self.paths.append(path)
        return _study(source=STUDY_FORM)


@pytest.mark.parametrize("nonview", [False, True])
def test_a_research_read_never_appends_its_study_form_as_a_dsl_line(monkeypatch, nonview) -> None:
    """RI-241 (07 §3.2 rules 9-10): keel_research_get's text was view.markdown, then the catalogue's ``source:``
    line drawing the read's stored study form as "the DSL at HEAD" in a python fence, after "View in Keel", so the
    link was no longer the last line and the form (up to 131 KB) rode the text. The catalogue's one owner
    (``_render_field``) now draws ``source`` for no ``study`` envelope; the form stays out of structuredContent too
    (``structured_study``). Control: a strategy envelope's ``source`` still draws its DSL line.
    Seeded red (reversed): the ``study`` branch removed -> both arms red on the ``source:`` line; the control stays
    green. Non-vacuity: the real handler read keel-api (one GET) and the text carries the study's first line."""
    import keel.client as keel_client
    from keel.tools.outcomes._mcp_adapter import operational_lines

    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    if nonview:
        monkeypatch.setenv(NONVIEW_TEXT_ENV, "1")
    else:
        monkeypatch.delenv(NONVIEW_TEXT_ENV, raising=False)
    reads = FakeReads()
    monkeypatch.setattr(keel_client, "KeelClient", lambda *a, **k: reads)

    async def go():
        from keel.mcp.server import create_server

        return await create_server().call_tool("keel_research_get", {"study_id": "stu_a"})

    result = asyncio.run(go())
    assert reads.paths == [
        "/v1/research/studies/stu_a"
    ]  # non-vacuous: the real handler read keel-api
    text = result.content[0].text
    assert "Momentum rotation v3" in text
    assert "source:" not in text and "Subjects(" not in text, text
    assert text.rstrip().splitlines()[-1].startswith("View in Keel: ")  # 07 §3.2 rule 9
    assert "source" not in (result.structured_content or {})
    # Control: the catalogue still draws a strategy read's requested DSL.
    dsl = 'Pipeline(\n  Universe(["BTC"]),\n)'
    assert operational_lines({"view": {"kind": "strategy"}, "source": dsl})[0].startswith(
        "source: the DSL at HEAD"
    )
