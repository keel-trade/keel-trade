"""The SDK types keel-api's paused research refusal (Research 07 §3.4, RL-91 §4).

keel-api answers every research route ``404 {"detail": "Research isn't available yet.",
"code": "RESEARCH_PAUSED"}`` while ``research.enabled`` is off. The SDK serves
the sentence under the code, with no next action, no suggestion, no link and
not retryable; an uncoded 404 is untouched (control).

SEED (reverted by reversing the edit): comparing against ``"RESEARCH_PAUSE"``
in ``translate_http_error`` reds ``test_the_paused_404_is_typed``.
"""

from __future__ import annotations

import json

import pytest
from keel.errors import NotFoundError, translate_http_error


PAUSED = json.dumps({"detail": "Research isn't available yet.", "code": "RESEARCH_PAUSED"})


def test_the_paused_404_is_typed() -> None:
    err = translate_http_error(404, PAUSED)
    assert isinstance(err, NotFoundError)
    env = err.to_envelope()
    assert env["code"] == "RESEARCH_PAUSED"
    assert env["message"] == "Research isn't available yet."
    assert env["suggested_next_action"] == {"tool": None, "args": {}}
    assert env["detail"] == {"code": "RESEARCH_PAUSED"}
    assert env["retryable"] is False
    assert "keel_strategy_search" not in json.dumps(env)  # the generic 404 advice is not attached


def test_an_uncoded_404_is_unchanged() -> None:
    err = translate_http_error(404, json.dumps({"detail": "Strategy not found"}))
    assert err.error_code == "not_found"
    assert "keel_strategy_search" in (err.suggestion or "")


def test_ri611_a_research_limit_carries_its_card_words_and_the_study_link() -> None:
    """RI-611 SDK half (D-328 D3, 13 §5): a research limit's 403 keeps keel-api's ``card_words`` (the card's sentence,
    without the "Trader and Pro include more" clause the text keeps, D-321) in the error envelope's ``detail``, with
    the study's page as its link, so the card draws the limit with a link instead of "Result unavailable".
    SEED (reversed): keep only ``code``/``unit`` in ``_research_refusal``'s ``detail`` -> this reds; the message
    (control) is the served sentence either way."""
    import json as _json

    from keel.errors import translate_http_error
    from keel.tools.outcomes._research_commands import apply_research_refusal

    said = "You have used 3 of 3 studies this month. Trader and Pro include more. Plans are changed in the Keel web app."
    body = _json.dumps(
        {"detail": {"code": "quota_exhausted", "message": said, "unit": "research_studies",
                    "card_words": "You have used 3 of 3 studies this month."}}
    )  # fmt: skip
    err = translate_http_error(403, body)
    apply_research_refusal(
        err,
        {"study_id": "stu_x"},
        app_url="https://app.usekeel.io/research",
        tool="keel_research_run",
    )
    env = err.to_envelope()
    assert env["message"] == said
    assert env["detail"]["card_words"] == "You have used 3 of 3 studies this month."
    assert "Trader" not in env["detail"]["card_words"]
    assert env["detail"]["links"]["app"] == "https://app.usekeel.io/research/stu_x"


def _refusal_text(
    code: str, *, status: int = 409, valid=None, tool: str = "keel_research_get"
) -> tuple[str, dict]:
    """A keel-api-shaped refusal through the real SDK path: translate, the research hook, the MCP error result."""
    import json as _json

    from keel.errors import translate_http_error
    from keel.tools.outcomes._mcp_adapter import view_tool_result
    from keel.tools.outcomes._research_commands import apply_research_refusal

    detail = {"code": code, "message": "The words keel-api serves."}
    if valid is not None:
        detail["valid"] = valid
    err = translate_http_error(status, _json.dumps({"detail": detail}))
    apply_research_refusal(
        err, {"study_id": "stu_a"}, app_url="https://app.usekeel.io/research", tool=tool
    )
    envelope = err.to_envelope()
    result = view_tool_result(_json.dumps(envelope), tool_name=tool)
    return result.content[0].text, envelope


@pytest.mark.parametrize(
    "code", sorted(__import__("keel.tools.outcomes._research_commands", fromlist=["x"]).REFUSALS)
)
def test_d344_a_research_refusal_keeps_its_code_and_its_fix_on_the_text_channel(code) -> None:
    """D-344 (2), CONFORM-MCP F3: on claude.ai (which reads only the text block) a research refusal kept its words
    and link but lost its code, its fix and its near misses. Each code in REFUSALS now reads its words, then its
    next call or what was expected, then the envelope as every other tool appends it. SEED (reversed): drop the
    envelope append in ``_mcp_adapter`` -> the code leaves the text and every arm reds; control: a non-research
    error's text is unchanged (the mature path)."""
    from keel.tools.outcomes._research_commands import REFUSALS

    text, envelope = _refusal_text(code)
    tool, _build, expected = REFUSALS[code]
    assert text.startswith("The words keel-api serves.")
    assert f'"{code}"' in text  # the code, in the appended envelope (the words do not carry it)
    action = envelope.get("suggested_next_action") or {}
    if action.get("tool"):
        assert action["tool"] in text or "keel_app_link" in text
    else:
        assert isinstance(expected, str) and expected in text, text


def test_d344_near_misses_reach_the_text() -> None:
    """keel-api's RESEARCH_MEASURE_UNKNOWN serves `valid` (research_backend.py:7739); the text names them."""
    text, _ = _refusal_text("RESEARCH_MEASURE_UNKNOWN", status=422, valid=["sharpe", "cagr"])
    assert "valid (2): sharpe, cagr" in text and '"RESEARCH_MEASURE_UNKNOWN"' in text


@pytest.mark.parametrize(
    ("code", "unit", "expected"),
    [
        (
            "quota_exhausted",
            "research_studies",
            "Plan limit; don't retry before the reset. Tell the user the numbers.",
        ),
        (
            "quota_cap_reached",
            "research_shares_max",
            "Plan limit; don't retry before a slot frees.",
        ),
        ("plan_feature_unavailable", "feature:research", "Plan limit; retrying doesn't change it."),
    ],
)
def test_d350_a_research_wall_carries_the_mature_plan_limit_instruction(
    code, unit, expected
) -> None:
    """D-14 rule 5 (ENGINE relay, D-350): a research plan-limit 403 expects what every mature tool's wall does
    (``quota_suggestion``: "Plan limit; don't retry before the reset. Tell the user the numbers."), so an agent
    reading the envelope after the words knows not to retry. SEED (reversed): ``_research_refusal`` back on the old
    "A plan limit stopped this call; …" constant -> this reds. Control: a RESEARCH_* refusal keeps its own
    "The message says what this call needs."."""
    said = "You have used 3 of 3 studies; they reset Dec 3. Plans are changed in the Keel web app."
    err = translate_http_error(
        403, json.dumps({"detail": {"code": code, "message": said, "unit": unit}})
    )
    assert err.to_envelope()["what_was_expected"].startswith(expected)
    own = translate_http_error(
        403, json.dumps({"detail": {"code": "RESEARCH_PAUSED", "message": said}})
    )
    assert own.to_envelope()["what_was_expected"] == "The message says what this call needs."
