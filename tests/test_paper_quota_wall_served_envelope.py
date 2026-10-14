"""The paper quota wall built from the 403 keel-api actually serves (QA N1).

The paper-wall arms in ``test_outcomes_paper.py`` build their denials from
reason strings in the pre-contract vocabulary (``limit=1:current=1``), which
the SDK reads only through its labelled legacy fallback
(``keel/errors.py`` ``_quota_context``, producer 2). keel-api has not sent that
shape since Q-1590: the ONE producer, ``platform_auth.quota.format_denial_reason``
(``libs/platform_auth/quota.py:356-388``), writes ``limit=``/``used=``/
``remaining=``, and keel-api's 403 handler (``services/keel-api/src/errors.py``
``domain_exception_handler``, the ``ForbiddenError`` arm) serves ``code`` and a
parsed ``quota`` block beside the reasons. No arm drove that served block for
a paper cap. These do, for both paper walls:

* the create wall (``POST /v1/paper``; ``authorize_action`` with
  ``cost_estimate={"paper_strategies_max": 1}``, ``routers/paper.py:858-868``,
  denied by ``check_cap_entitlement``, ``libs/platform_auth/entitlements.py:568-570``);
* the resume wall (``POST /v1/paper/{id}/resume``; ``_require_running_slot``,
  ``routers/paper.py:1215-1243``).

Both are the same envelope: ``SERVED_PAPER_CAP_403`` below, copied from the
producers at the paper build. ``test_the_fixture_is_the_producers_output``
re-derives its machine half from ``platform_auth`` whenever that package is
importable (the monorepo env), and skips in the SDK's own venv, which carries
no ``libs/``.

What the arms prove: the wall is the signed-in quota handoff (never the claim
wall), its numbers come from the SERVED block (``plan`` exists only there; the
reasons carry none), ``used`` and its legacy alias ``current`` agree, and each
wall names its own blocked tool.

Seed (a scratch pytest plugin wrapping ``keel.errors._quota_context`` so it
ignores the served block and reads the reasons only; no repo edit) ⇒ both
walls red on ``plan`` (KeyError) while their counts stay green — the served
block, not the reasons, is what the arms read.
"""

from __future__ import annotations

import json

import pytest


#: keel-api's 403 for a paper slot at its cap, as served on create and on
#: resume. ``reasons`` is ``format_denial_reason("cap_exceeded",
#: "paper_strategies_max", quota_view_from_cap(..., effective_cap=1,
#: current_count=1))`` (libs/platform_auth/quota.py:291-313, :356-388);
#: ``quota`` is ``parse_denial_reason`` of it (:391-408) plus ``plan`` and
#: ``higher_plans`` (services/keel-api/src/errors.py ``_quota_with_paths``,
#: plans.yaml: free 1, starter 5, trader 20, pro 50); ``code`` is
#: ``DENIAL_CODES["cap_exceeded"]`` (quota.py:116-122); ``detail`` is
#: ``_entitlement_detail``'s cap sentence and ``type`` is
#: ``_ERROR_TYPES["forbidden"]`` (errors.py). The label is what keel-api
#: serves today: ``UNIT_LABELS`` names no paper unit, so ``label_for`` falls
#: back to the unit's own words.
SERVED_PAPER_CAP_403 = {
    "type": "https://api.usekeel.io/errors/authorization_denied",
    "title": "Forbidden",
    "status": 403,
    "detail": "You're using 1 of 1 paper strategies, the maximum on this plan.",
    "code": "quota_cap_reached",
    "reasons": ["entitlement:cap_exceeded:paper_strategies_max:limit=1:used=1:remaining=0"],
    "quota": {
        "kind": "cap_exceeded",
        "unit": "paper_strategies_max",
        "label": "paper strategies",
        "code": "quota_cap_reached",
        "limit": 1,
        "used": 1,
        "remaining": 0,
        "plan": "free",
        "higher_plans": [
            {"plan": "starter", "limit": 5},
            {"plan": "trader", "limit": 20},
            {"plan": "pro", "limit": 50},
        ],
    },
}

AUTH_API = "https://api.paper-wall.test"


def test_the_fixture_is_the_producers_output():
    quota = pytest.importorskip("platform_auth.quota")
    pricing = pytest.importorskip("platform_auth.pricing")

    reason = quota.format_denial_reason(
        "cap_exceeded",
        "paper_strategies_max",
        quota.quota_view_from_cap("paper_strategies_max", effective_cap=1, current_count=1),
    )
    assert SERVED_PAPER_CAP_403["reasons"] == [reason]
    parsed = quota.parse_denial_reason(reason)
    served = {
        **parsed,
        "plan": "free",
        "higher_plans": pricing.higher_plan_limits("free", "paper_strategies_max"),
    }
    assert SERVED_PAPER_CAP_403["quota"] == served
    assert SERVED_PAPER_CAP_403["code"] == quota.DENIAL_CODES["cap_exceeded"]


@pytest.fixture
def signed_in(monkeypatch, tmp_path):
    """A stored (non-anonymous) login against the faked keel-api."""
    from keel.config import KeelConfig, save_config

    for key in ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_API_KEY", "KEEL_API_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KEEL_ANON_AUTO", "0")
    monkeypatch.setattr("keel.config.CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr("keel.config.CONFIG_DIR", tmp_path)
    save_config(KeelConfig(api_key="real_access", api_url=AUTH_API))


def _assert_served_quota(limit_details: dict) -> None:
    # Non-vacuity: the served block names the caller's plan; the reason
    # string carries none, so `plan` below can only come from the block.
    assert "plan" not in SERVED_PAPER_CAP_403["reasons"][0]
    assert limit_details["unit"] == "paper_strategies_max"
    assert (limit_details["limit"], limit_details["used"]) == (1, 1)
    assert limit_details["current"] == limit_details["used"]
    assert limit_details["remaining"] == 0
    assert limit_details["kind"] == "cap_exceeded"
    # The caller's own plan: a fact only the served block carries.
    assert limit_details["plan"] == "free"
    # Other plans' limits are numbers keel-api serves and no wall renders.
    assert "higher_plans" not in json.dumps(limit_details)


def test_the_create_wall_reads_the_served_block(paper_sdk_released, signed_in):
    import httpx
    import respx
    from keel.tools.outcomes import get
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes._handoff import HandoffRequired

    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{AUTH_API}/v1/paper").mock(
            return_value=httpx.Response(403, json=SERVED_PAPER_CAP_403)
        )
        with pytest.raises(HandoffRequired) as exc:
            get("keel_paper_deploy").handler(
                {"strategy_id": "str_x"}, ToolContext(app_url="https://app.usekeel.io")
            )
    assert route.called
    envelope = exc.value.to_envelope()
    assert envelope["reason"] != "account_required"
    assert envelope["blocked_action"] == "paper_deploy"
    assert envelope["resume"]["verify_call"]["tool"] == "keel_paper_deploy"
    _assert_served_quota(envelope["limit_details"])


def test_the_resume_wall_reads_the_served_block(paper_sdk_released, signed_in):
    from keel.errors import translate_http_error
    from keel.tools.outcomes import get
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes._handoff import HandoffRequired

    calls: list[tuple[str, str]] = []

    class _Transport:
        def post(self, path, json=None, *, headers=None, **params):
            calls.append(("POST", path))
            raise translate_http_error(403, _json(SERVED_PAPER_CAP_403))

        def get(self, path, *, headers=None, **params):  # pragma: no cover - not reached
            calls.append(("GET", path))
            raise AssertionError("the resume wall reads nothing")

    with pytest.raises(HandoffRequired) as exc:
        get("keel_paper_control").handler(
            {"deployment_id": "dep_p", "action": "resume"},
            ToolContext(api_client=_Transport(), app_url="https://app.usekeel.io"),
        )
    assert calls == [("POST", "/v1/paper/dep_p/resume")]
    envelope = exc.value.to_envelope()
    assert envelope["reason"] != "account_required"
    assert envelope["blocked_action"] == "paper_control"
    assert "action_url" not in envelope
    _assert_served_quota(envelope["limit_details"])


def _json(body: dict) -> str:
    return json.dumps(body)
