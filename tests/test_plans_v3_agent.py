"""Plans v3 on the agent surfaces (value-and-pricing spec 02 §C4, §F G2 and G9; V3-API's STABLE shapes).

* G9: a plan limit is never a failed call: every plan-limit envelope the SDK builds (each kind, the coin cap, the
  split floor, the saved-screen cap) leaves MCP ``isError`` off and carries no "error" / "failed" wording in any text
  the person or the model reads;
* G2 (SDK half): no plan id, plan name or plan price is typed into the SDK's plan-limit copy modules; every name
  reaches copy from served data;
* the coin cap's remedy becomes the next call's arguments (``narrow_to_plan: true`` on the SAME tool), the way
  ``BACKTEST_TOO_LARGE``'s window does, and nothing narrows without it;
* compose's dry run reads ``plan_findings`` and says a failed read instead of reading it as "no finding";
* a paid plan of ANY name gets its plan grant read (``status.py``, spec 02 §C4.10: a Quant org got none).
"""

from __future__ import annotations

import ast
import json
import re
from unittest.mock import MagicMock

import pytest
from keel.errors import EntitlementError, KeelError, ValidationError, translate_http_error
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._channels import is_plan_limit_envelope

from . import _plan_catalog as cat


_bootstrap()

NAMES = [{"plan": p, "version": v, "name": cat.display_name(p, v)} for p, v in cat.paid_rows()]
WEB = "Plans are changed in the Keel web app."


def _coins_422(resolved: int = 180) -> str:
    return json.dumps(
        {
            "detail": {
                "code": "SCREEN_COINS_OVER_PLAN",
                "unit": "screen_assets_max",
                "kind": "setting",
                "message": f"Free screens cover up to 30 coins; this one covers {resolved}. "
                f"Trader, Quant and Pro include more. {WEB}",
                "limit": 30,
                "resolved": resolved,
                "plan": "free",
                "plan_name": "Free",
                "higher_plans": NAMES,
                "remedy": {
                    "kind": "narrow_universe",
                    "max_coins": 30,
                    "resolved_coins": resolved,
                    "apply": {"narrow_to_plan": True},
                    "words": f"Keeps the 30 most traded of the {resolved}.",
                },
                "suggestion": "Plan limit. Tell the user the coin limit and which plans include more; use the top "
                "30 or a 30-coin pick. Don't split the screen into several runs to get past it.",
            }
        }
    )


def _floor_422(applies: str = "paper") -> str:
    return json.dumps(
        {
            "detail": {
                "code": "SERVING_CLOCK_BELOW_PLAN_FLOOR",
                "floor": "1d",
                "requested": "4h",
                "unit": "paper_live_clock_floor",
                "kind": "floor",
                "applies": applies,
                "plan": "free",
                "plan_name": "Free",
                "higher_plans": NAMES,
                "remedy": {"kind": "slower_clock", "applies": applies, "admitted": ["1d"]},
            }
        }
    )


def _quota_403(
    unit: str, label: str, *, limit: int, used: int, cap: bool, remedy: dict | None = None
) -> str:
    quota = {
        "kind": "cap_exceeded" if cap else "insufficient",
        "unit": unit,
        "label": label,
        "code": "quota_cap_reached" if cap else "quota_exhausted",
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "plan": "free",
        "plan_name": "Free",
        "higher_plans": NAMES,
    }
    if not cap:
        quota.update(period="30d", resets_at="2026-12-05T13:00:00Z")
    body = {"title": "Forbidden", "status": 403, "code": quota["code"], "quota": quota}
    if remedy:
        body["remedy"] = remedy
    return json.dumps(body)


def _plan_limit_envelopes() -> dict[str, dict]:
    """Every plan-limit kind an agent meets, as the SDK builds its envelope (V3-API's bodies)."""
    out = {
        "consumable": translate_http_error(
            403, _quota_403("backtest_runs", "backtests", limit=20, used=20, cap=False)
        ),
        "cap": translate_http_error(
            403,
            _quota_403(
                "paper_strategies_max",
                "paper strategies",
                limit=1,
                used=1,
                cap=True,
                remedy={"kind": "stop_one", "running": ["dep_1"], "update_if_same_strategy": False},
            ),
        ),
        "saved_screens": translate_http_error(
            403,
            _quota_403(
                "screeners_max",
                "saved screens",
                limit=2,
                used=2,
                cap=True,
                remedy={"kind": "free_one", "actions": ["delete", "archive", "run_unsaved"]},
            ),
        ),
        "setting_coins": translate_http_error(422, _coins_422()),
        "floor_paper": translate_http_error(422, _floor_422("paper")),
        "floor_live": translate_http_error(422, _floor_422("live")),
        "feature": translate_http_error(
            403,
            json.dumps(
                {
                    "detail": {
                        "code": "plan_feature_unavailable",
                        "unit": "feature:research_template_signal",
                        "message": f"Study a signal isn't on Free. Quant and Pro include it. {WEB}",
                    }
                }
            ),
        ),
    }
    return {name: err.to_envelope() for name, err in out.items()}


#: G9's words: a plan limit is not an error (spec 02 §C0 test 4).
_FAILURE_WORDS = re.compile(r"\berror\b|\bfailed\b|\bfailure\b", re.IGNORECASE)


def _texts(envelope: dict) -> list[str]:
    out = [envelope.get("message"), envelope.get("what_was_expected")]
    out.append((envelope.get("suggested_next_action") or {}).get("reason"))
    out += list(envelope.get("talking_points") or ())
    out += [v for v in (envelope.get("limit_view") or {}).values() if isinstance(v, str)]
    return [t for t in out if isinstance(t, str)]


def test_g9_a_plan_limit_is_never_a_failed_call():
    """Non-vacuity: every kind is built, and each is a plan-limit envelope (so isError stays off on the wire)."""
    envelopes = _plan_limit_envelopes()
    assert len(envelopes) == 7
    for name, envelope in envelopes.items():
        assert is_plan_limit_envelope(envelope), (name, envelope["code"])
        assert envelope["retryable"] is False, name
        for text in _texts(envelope):
            assert not _FAILURE_WORDS.search(text), (name, text)


def test_g9_the_guard_reds_on_failure_wording(monkeypatch):
    """SEED: a free way forward that says "failed" reds; CONTROL: the real one passes (above). A code that is not
    a plan limit is not admitted as one (the isError arm reacts to the code, not to every 4xx)."""
    from dataclasses import replace

    import keel.limit_copy as lc

    row = lc.LIMIT_ROWS["backtest_runs"]
    monkeypatch.setitem(lc.LIMIT_ROWS, "backtest_runs", replace(row, free_way="The run failed."))
    env = translate_http_error(
        403, _quota_403("backtest_runs", "backtests", limit=20, used=20, cap=False)
    ).to_envelope()
    assert any(_FAILURE_WORDS.search(t) for t in _texts(env))
    other = translate_http_error(
        422, json.dumps({"detail": {"code": "WINDOW_EMPTY", "message": "x"}})
    ).to_envelope()
    assert not is_plan_limit_envelope(other)


def test_the_floor_wall_names_its_side_and_carries_the_admitted_clock():
    envelopes = _plan_limit_envelopes()
    paper, live = envelopes["floor_paper"], envelopes["floor_live"]
    assert paper["message"].startswith("Paper on Free runs daily; this strategy is 4h.")
    assert live["message"].startswith("Live on Free runs daily; this strategy is 4h.")
    assert paper["detail"]["applies"] == "paper" and paper["detail"]["remedy"]["admitted"] == ["1d"]
    assert "higher_plans" not in json.dumps(paper), "names only"
    assert "never suggest live trading" in paper["what_was_expected"]


def test_the_coin_cap_is_the_servers_wall_with_its_remedy():
    env = _plan_limit_envelopes()["setting_coins"]
    assert env["code"] == "SCREEN_COINS_OVER_PLAN"
    assert env["message"].startswith("Free screens cover up to 30 coins; this one covers 180.")
    assert env["detail"]["remedy"]["apply"] == {"narrow_to_plan": True}
    assert env["detail"]["more_plans"] == cat.names(NAMES)
    assert "Don't split the screen" in env["what_was_expected"]


def _client_raising(err: KeelError, then: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.post.side_effect = [err] if then is None else [err, then]
    return client


def test_screener_run_turns_the_coin_cap_into_the_same_call_with_apply():
    """§C4.3a / E1.3: the next action is keel_screener_run with every argument kept plus narrow_to_plan, and the
    refused call sent none (nothing narrows without it)."""
    tool = OUTCOMES["keel_screener_run"]
    client = _client_raising(translate_http_error(422, _coins_422()))
    with pytest.raises(ValidationError) as exc:
        tool.handler({"source": "Globals()", "wait": False}, ToolContext(api_client=client))
    env = exc.value.to_envelope()
    assert env["suggested_next_action"]["tool"] == "keel_screener_run"
    assert env["suggested_next_action"]["args"] == {
        "source": "Globals()",
        "wait": False,
        "narrow_to_plan": True,
    }
    sent = client.post.call_args.kwargs["json"]
    assert "narrow_to_plan" not in sent
    # The same call with the argument sends it.
    client = MagicMock()
    client.post.return_value = {"run_id": "rr_1", "status": "QUEUED"}
    tool.handler(
        {"source": "Globals()", "wait": False, "narrow_to_plan": True},
        ToolContext(api_client=client),
    )
    assert client.post.call_args.kwargs["json"]["narrow_to_plan"] is True


def test_a_narrowed_run_says_what_was_kept_and_the_coins_left_out():
    from keel.tools.outcomes._plan_fit import narrowed_fields

    fields = narrowed_fields(
        {
            "plan_narrowed": {
                "unit": "screen_assets_max",
                "max_coins": 30,
                "resolved_coins": 32,
                "kept_coins": 30,
                "mode": "manual",
                "words": "Keeps the 30 most traded of these 32, as a fixed list.",
                "message": "Keeps the 30 most traded of these 32, as a fixed list. Free screens cover up to 30 coins.",
                "left_out": ["AAA", "BBB"],
            }
        }
    )
    assert fields["plan_narrowed_notice"] == (
        "Keeps the 30 most traded of these 32, as a fixed list. Free screens cover up to 30 coins. "
        "Left out: AAA, BBB."
    )
    assert narrowed_fields({"plan_narrowed": None}) == {}


def test_compose_dry_run_reads_the_plan_findings_and_says_a_failed_read():
    from keel.tools.outcomes import strategy_compose as sc

    finding = json.loads(_coins_422(42))["detail"] | {"severity": "warning"}
    client = MagicMock()
    client.post.return_value = {"valid": True, "plan_findings": [finding]}
    got = sc._plan_findings("src", ToolContext(api_client=client), None, None)
    assert got["plan_findings"][0]["code"] == "SCREEN_COINS_OVER_PLAN"
    assert got["plan_findings"][0]["more_plans"] == cat.names(NAMES)
    assert "higher_plans" not in json.dumps(got)
    assert "`narrow_to_plan: true`" in got["plan_findings_notice"]
    # A served validate the Use context already read is not called twice.
    client = MagicMock()
    assert (
        sc._plan_findings("src", ToolContext(api_client=client), None, {"plan_findings": []}) == {}
    )
    client.post.assert_not_called()
    # A failed read is said, never read as "no finding".
    client = MagicMock()
    client.post.side_effect = KeelError("rate limited", error_code="rate_limited")
    said = sc._plan_findings("src", ToolContext(api_client=client), None, None)
    assert set(said) == {"plan_check_error"} and "could not run" in said["plan_check_error"]
    # Auth is the call's own error, not a missing check.
    client.post.side_effect = EntitlementError("no")
    with pytest.raises(EntitlementError):
        sc._plan_findings("src", ToolContext(api_client=client), None, None)


def test_every_paid_plan_reads_its_plan_grant():
    """§C4.10: derived from the ladder, never a list of paid ids. Every paid plan in plans.yaml (every version)
    gets the call; the unpaid rungs do not. SEED: restore the pre-v3 ("starter", "trader", "pro") list and the
    Quant arm reds."""
    from keel.tools.outcomes.status import is_paid_plan

    paid = {plan for plan, _ in cat.paid_rows()} | {"starter"}
    assert "quant" in paid, "non-vacuity: the v3 tier is in the catalog"
    assert all(is_paid_plan(p) for p in paid)
    assert not is_paid_plan("free") and not is_paid_plan("anon") and not is_paid_plan(None)


#: The SDK modules that render or decide plan-limit copy (G2, SDK half).
_COPY_MODULES = (
    "keel/limit_copy.py",
    "keel/errors.py",
    "keel/tools/outcomes/plan_status.py",
    "keel/tools/outcomes/_plan_fit.py",
    "keel/tools/outcomes/_handoff.py",
    "keel/tools/outcomes/screener_run.py",
    "keel/tools/outcomes/status.py",
)
#: Allowed by ruling: the ladder's unpaid rungs (``status.UNPAID_PLANS``), and the anonymous workspace's org kind
#: (sign-in instead of a plan, spec 05).
_ALLOWED = {
    ("keel/tools/outcomes/status.py", "anon"),
    ("keel/tools/outcomes/status.py", "free"),
    ("keel/errors.py", "anon"),
    ("keel/tools/outcomes/_handoff.py", "anon"),
}


def _plan_literals(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    docs = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    ids = {p for p, _ in cat.paid_rows()} | {"free", "anon", "starter"}
    names = cat.all_plan_names()
    name_re = re.compile(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b")
    price_re = re.compile(r"\$\s?\d")
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            if node.value in ids or name_re.search(node.value) or price_re.search(node.value):
                hits.append((node.lineno, node.value[:80]))
    return hits


def test_g2_the_sdk_copy_modules_type_no_plan_name_or_price():
    root = cat.REPO_ROOT / "packages" / "keel-trade" / "keel-sdk"
    found = []
    for rel in _COPY_MODULES:
        for line, value in _plan_literals((root / rel).read_text()):
            if (rel, value) not in _ALLOWED:
                found.append(f"{rel}:{line}: {value!r}")
    assert found == [], (
        "plan copy names a plan or a price; read it from served data:\n" + "\n".join(found)
    )
    # Non-vacuity: the allowances are real literals in their files (the guard sees them).
    seen = {
        (rel, value)
        for rel in _COPY_MODULES
        for _, value in _plan_literals((root / rel).read_text())
    }
    assert ("keel/tools/outcomes/status.py", "free") in seen


@pytest.mark.parametrize(
    "seed",
    [
        'X = "Trader and Pro include more."\n',
        'PAID = ("starter", "trader", "pro")\n',
        'P = "Quant is $129."\n',
    ],
)
def test_g2_the_sdk_guard_finds_a_seeded_literal(seed):
    assert _plan_literals(seed), seed


def test_a_narrowed_share_copy_carries_its_share_facts_and_names_only():
    """V3-API shape 2 (e4649f831f): a share copy says how much of the share's top 10 it left out, links the share
    (not a billing link, D-422) and names the plans that include more, never their numbers."""
    from keel.tools.outcomes._plan_fit import narrowed_fields

    served = {
        "unit": "screen_assets_max",
        "max_coins": 30,
        "resolved_coins": 180,
        "kept_coins": 30,
        "mode": "top_volume",
        "words": "Keeps the 30 most traded of the 180.",
        "message": "Keeps the 30 most traded of the 180. Free screens cover up to 30 coins. Keel's screen ranks all "
        "180, and 4 of its top 10 are outside your 30. Trader, Quant and Pro include more.",
        "higher_plans": [{**n, "limit": 500} for n in NAMES],
        "outside_top10": 4,
        "share_top": 10,
        "share_ranked_coins": 180,
        "share_result_at": "2026-11-22T12:00:00Z",
        "share_url": "https://usekeel.io/s/shr_1",
    }
    fields = narrowed_fields({"plan_narrowed": served})
    projected = fields["plan_narrowed"]
    assert projected["more_plans"] == cat.names(NAMES)
    assert projected["outside_top10"] == 4 and projected["share_url"].endswith("/s/shr_1")
    assert "higher_plans" not in projected and "500" not in json.dumps(projected)
    assert fields["plan_narrowed_notice"] == served["message"]


def test_pitch_words_refuse_savings_and_keep_the_saved_screens_wall():
    """D-14 §7's pitch words refuse "savings" and "save", and keep spec 02 §D's own saved-screens wall ("run it
    without saving"). SEED: the earlier pattern (``savings?``) matches "saving", so it refused the wall the server
    renders; the current one does not (the control arm)."""
    from keel.errors import PITCH_WORDS_RE, assert_plan_copy

    wall = "Saved screens: 2 of 2 on Free. Delete or archive one, or run it without saving."
    assert assert_plan_copy(wall) == wall
    for pitch in ("Big savings on every plan.", "Save more with a plan."):
        with pytest.raises(ValueError, match="pitch"):
            assert_plan_copy(pitch)
    seeded = re.compile(
        PITCH_WORDS_RE.pattern.replace(r"\bsavings\b", r"\bsavings?\b"), re.IGNORECASE
    )
    assert seeded.pattern != PITCH_WORDS_RE.pattern
    assert seeded.search(wall) and not PITCH_WORDS_RE.search(wall)
