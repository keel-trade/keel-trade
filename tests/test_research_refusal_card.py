"""A research refusal's card and text (RI-237; research spec 07 §3.3 "Error: the error's own words, from
structuredContent", §3.2 rules 9 and 11, §3.4; 09 J13 F6/T5, J14 rows 1 and 7).

keel-api's research routes refuse with their own served sentence nested under ``detail``
(``HTTPException(403, detail={"code", "message", "unit"})``, research_backend.not_runnable). Until RI-237
``errors._translate_403`` read ``code`` at the body's top level and ``detail`` only as a string, so the refusal fell
to the scope-missing arm: code None, the raw body as the message (the card drew a JSON string), no app link, and on
the full profile "Re-login with live scope". Every arm here calls the real tool through the real server
(``keel.mcp.server.create_server``) with keel-api answered by a fake ``KeelClient``.

SEEDS (each reversed by reversing the edit, 2026-11-14): ``_translate_403`` without its ``_research_refusal`` call
-> the four card arms red (the message is the raw body) and the control stays green; ``apply_research_refusal``
without its ``links`` -> the card arms red on the missing app link; ``view_tool_result`` without its research
branch -> the four card arms red on the text (the envelope JSON follows the words again).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from keel.errors import translate_http_error
from keel.tools.outcomes._channels import NONVIEW_TEXT_ENV
from keel.tools.outcomes._research_commands import refusal_text


#: research_backend.not_runnable's bodies, with the words J13 F5/F6 and T4/T5 serve (words.py; D-14:
#: the served names of the plans that include more, no window words).
FREE_WALL = (
    "This study was not run and nothing was charged. You have used 3 of 3 studies; they reset Dec 14. "
    "Planning, reading, saving and runs that reuse every result do not count. Trader and Pro include more. "
    "Plans are changed in the Keel web app."
)
TRADER_MAP = (
    "This map has 384 settings; the most this account's plan can run is 25. Narrow an axis. Pro includes "
    "more. Plans are changed in the Keel web app."
)
REFUSED = {
    "free_fourth": {"code": "quota_exhausted", "message": FREE_WALL, "unit": "research_studies"},
    "trader_map": {
        "code": "quota_cap_reached",
        "message": TRADER_MAP,
        "unit": "research_backtests_max",
    },
}
APP = "https://app.usekeel.io"


class FakeClient:
    """keel-api: every POST is refused with ``body`` (403)."""

    def __init__(self, body: dict, status: int = 403) -> None:
        self.body, self.status = body, status
        self.paths: list[str] = []

    def post(self, path: str, json: Any = None, *, headers: Any = None, **params: Any) -> Any:
        self.paths.append(path)
        raise translate_http_error(self.status, _dumps({"detail": self.body}))


def _words(text: str) -> str:
    """A research refusal's text up to the appended envelope (D-344 (2): words, next call, link, then the JSON)."""
    head, sep, tail = text.partition("\n\n{")
    assert sep and tail.endswith("}"), text  # the envelope is there
    return head


def _dumps(body: dict) -> str:
    return json.dumps(body)


def _served_call(monkeypatch: pytest.MonkeyPatch, profile: str, body: dict, status: int = 403):
    import keel.client as keel_client

    monkeypatch.setenv("KEEL_SERVER_PROFILE", profile)
    monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    monkeypatch.delenv("KEEL_APP_URL", raising=False)
    monkeypatch.delenv(NONVIEW_TEXT_ENV, raising=False)
    client = FakeClient(body, status)
    monkeypatch.setattr(keel_client, "KeelClient", lambda *a, **k: client)

    def call(tool: str, args: dict[str, Any]) -> Any:
        from keel.mcp.server import create_server
        from keel.tools.outcomes._toolsets import research_served_context

        async def go():
            return await create_server().call_tool(tool, args)

        # The listed profile serves Research when the caller's /v1/me says so (bound per request when hosted).
        with research_served_context(True):
            return asyncio.run(go())

    return call, client


def _card(result: Any, line: str) -> str:
    """The journeys' error_card reading (tests/research_e2e/harness/calls.py): the words are one of the structured
    content's own string fields, word for word, and it carries an app link under /research."""
    assert getattr(result, "is_error", False), result
    structured = result.structured_content
    assert isinstance(structured, dict), structured
    assert line in [v for v in structured.values() if isinstance(v, str)], structured
    link = (structured.get("detail") or {}).get("links", {}).get("app")
    assert isinstance(link, str) and link.startswith(f"{APP}/research"), structured
    return link


@pytest.mark.parametrize("profile", ["full", "listed"])
@pytest.mark.parametrize("case", sorted(REFUSED))
def test_a_refused_run_draws_its_own_words_and_the_study_link(monkeypatch, profile, case) -> None:
    body = REFUSED[case]
    call, client = _served_call(monkeypatch, profile, body)
    result = call("keel_research_run", {"plan_id": "pln_" + "a" * 64})
    assert client.paths == [
        "/v1/research/runs"
    ]  # non-vacuity: the real handler reached keel-api once
    link = _card(result, body["message"])
    assert (
        link == f"{APP}/research"
    )  # a plan_id names no study: Research's home (keel-api's study_link rule)
    structured = result.structured_content
    assert structured["code"] == "insufficient_entitlements"
    assert (
        structured["detail"]["code"] == body["code"]
        and structured["detail"]["unit"] == body["unit"]
    )
    assert structured["suggested_next_action"] == {"tool": None, "args": {}}
    flat = json.dumps(structured)
    assert "keel_auth_login" not in flat and "live scope" not in flat and '\\"detail\\"' not in flat
    # The text: the words, then "View in Keel" last (07 §3.2 rules 9 and 11), no envelope after it.
    assert _words(result.content[0].text).splitlines() == [body["message"], f"View in Keel: {APP}/research"]


def test_a_refusal_naming_a_study_links_its_page_and_the_listed_next_call_is_an_app_link(
    monkeypatch,
) -> None:
    from keel.errors import KeelError
    from keel.tools.outcomes._research_commands import apply_research_refusal

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    err = KeelError("The change leaves what was measured as it is.")
    err.error_code = "RESEARCH_PRESENTATION_ONLY"
    err.detail = {"code": "RESEARCH_PRESENTATION_ONLY", "current_revision": 3}
    env = apply_research_refusal(
        err, {"study_id": "stu_a"}, app_url=f"{APP}/research"
    ).to_envelope()
    assert env["detail"]["links"] == {"app": f"{APP}/research/stu_a"}
    # keel_research_view_set is not listed: the next call is keel_app_link with the study's id (it has no `url`).
    assert env["suggested_next_action"]["tool"] == "keel_app_link"
    assert env["suggested_next_action"]["args"] == {"id": "stu_a"}
    assert refusal_text(env).splitlines() == [
        "The change leaves what was measured as it is.",
        "The change leaves what was measured as it is: save it as a presentation revision. "
        'keel_app_link(id="stu_a")',
        f"View in Keel: {APP}/research/stu_a",
    ]


def test_a_refused_plans_text_ends_with_the_app_link() -> None:
    """J14 rows 1 and 7: 07 §4.12's "Plan refused" lines, then "View in Keel: …" last, no envelope after it."""
    from keel.tools.outcomes._research_commands import apply_research_refusal

    issue = {
        "code": "STUDY_ADDRESS_SYNTAX",
        "path": "/subjects/v3/set",
        "message": '"ROC#1.period" is not an address.',
        "severity": "error",
    }
    err = translate_http_error(
        422, _dumps({"detail": {"code": "STUDY_ADDRESS_SYNTAX", "issues": [issue]}})
    )
    env = apply_research_refusal(
        err, {}, app_url=f"{APP}/research", tool="keel_research_plan"
    ).to_envelope()
    lines = refusal_text(env).splitlines()
    assert lines[0] == "Plan refused: 1 issue; nothing was planned or spent."
    assert lines[-1] == f"View in Keel: {APP}/research" and not any(
        x.startswith("{") for x in lines
    )
    # With no envelope in the text, the issue's served fix and near misses are lines of their own.
    issue = {**issue, "code": "STUDY_ADDRESS_NOT_FOUND", "message": "No address ROC.period.",
             "suggestion": "Use one of the addresses below.", "valid": ["step[2].period", "step[3].window"]}  # fmt: skip
    err = translate_http_error(422, _dumps({"detail": {"code": issue["code"], "issues": [issue]}}))
    env = apply_research_refusal(
        err, {}, app_url=f"{APP}/research", tool="keel_research_plan"
    ).to_envelope()
    assert refusal_text(env).splitlines()[1:3] == [
        "/subjects/v3/set · No address ROC.period. Use one of the addresses below.",
        "valid (2): step[2].period, step[3].window",
    ]


def test_control_a_non_research_tools_error_text_keeps_its_envelope() -> None:
    """Control: the adapter's research branch keys on the tool; any other tool's error text is message + envelope
    byte for byte, even if its envelope happened to carry a link."""
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    envelope = {"code": "x", "message": "Refused.", "what_was_expected": "y", "example": {},
                "suggested_next_action": {"tool": None, "args": {}}, "detail": {"links": {"app": f"{APP}/research"}},
                "exit_code": 1, "retryable": False}  # fmt: skip
    raw = json.dumps(envelope)
    assert view_tool_result(raw, "keel_backtest_run").content[0].text == f"Refused.\n\n{raw}"
    # D-344 (2): a research refusal's words and link lead, then the envelope as every tool appends it; a
    # what-was-expected that names no recovery is said in words too.
    assert (
        view_tool_result(raw, "keel_research_get").content[0].text
        == f"Refused.\ny\nView in Keel: {APP}/research\n\n{raw}"
    )


def test_control_an_error_without_a_research_link_has_no_research_text() -> None:
    """Control: a non-research error envelope (no ``detail.links``) is not a research refusal; its text stays the
    adapter's (message, lines, envelope)."""
    env = translate_http_error(
        403, _dumps({"detail": {"code": "LIVE_SHARE_CREATION_DISABLED", "message": "x"}})
    )
    assert refusal_text(env.to_envelope()) is None


@pytest.mark.parametrize("profile", ["full", "listed"])
def test_j14_an_address_refusal_names_the_address_list_and_ends_with_the_link(
    monkeypatch, profile
) -> None:
    """09 J14 rows 1 and 7 (the integrator's 11-14 ruling): the withdrawn alias shape is refused with one issue; the
    words name the call that lists every address of the pinned subject, and the last line is the app link. The same
    words on both profiles (row 7)."""
    ref = "str_momrot0000000000000000000@3"
    issue = {
        "code": "STUDY_ADDRESS_SYNTAX",
        "path": "/subjects/v3/set",
        "message": '"ROC#1.period" is not an address.',
        "severity": "error",
    }
    call, client = _served_call(
        monkeypatch, profile, {"code": "STUDY_ADDRESS_SYNTAX", "issues": [issue]}, status=422
    )
    subjects = [f'Setting("{ref}", set={{"ROC#1.period": 20}})']
    result = call("keel_research_plan", {"template": "check", "subjects": subjects})
    assert client.paths == ["/v1/research/plans"] and result.is_error
    assert _words(result.content[0].text).splitlines() == [
        "Plan refused: 1 issue; nothing was planned or spent.",
        '/subjects/v3/set · "ROC#1.period" is not an address.',
        f"The addresses of {ref}: " + 'keel_research_get(subject="Strategy(\\"' + ref + '\\")")',
        "Fix each, then plan again.",
        f"View in Keel: {APP}/research",
    ]


def test_control_a_non_address_refusal_names_no_address_list() -> None:
    from keel.tools.outcomes._research_commands import refused_text

    detail = {
        "issues": [
            {"code": "STUDY_PLAN_CHILD_TOO_LARGE", "path": "/steps/chance", "message": "Too large."}
        ]
    }
    text = refused_text(detail, {"subjects": ['Strategy("str_a@1")']})
    assert "keel_research_get" not in text and text.startswith("Plan refused: 1 issue")


@pytest.mark.parametrize("tool,args", [
    ("keel_research_note", {"anchor": "stu_a", "kind": "decision", "body": "Keep v3."}),
    ("keel_research_cancel", {"run_set_id": "rus_a"}),
])  # fmt: skip
def test_a_receipt_tools_refusal_ends_with_the_link_too(monkeypatch, tool, args) -> None:
    """07 §3.2 rule 9 is on every answer: note and cancel answer in the receipt mode (RI-220), whose error result is
    view_tool_result's; it is told the tool, so a refusal there ends with the link as well. The card meta is
    unchanged (neither tool has a card)."""
    call, client = _served_call(
        monkeypatch,
        "full",
        {"code": "RESEARCH_STUDY_NOT_FOUND", "message": "No such study."},
        status=404,
    )
    result = call(tool, args)
    assert client.paths and result.is_error  # non-vacuity: the handler reached keel-api
    lines = _words(result.content[0].text).splitlines()
    assert lines[0] == "No such study." and lines[-1].startswith(f"View in Keel: {APP}/research")
    assert not any(line.startswith("{") for line in lines)  # the words part (the envelope follows, D-344 (2))
    assert result.content[0].text.endswith("}") and '"RESEARCH_STUDY_NOT_FOUND"' in result.content[0].text
    assert not (result.meta or {}).get("ui"), result.meta
