"""The held paper tools' agent outputs say only what is true (lane RQ2; QA-W1..W4).

Fresh-eyes QA of the MCP/CLI paper surface (``qa-mcp-share.md``) found four
classes of false or wrong-surface text in what the paper tools hand an agent.
Each arm below pins the fix, with a control that stays green through the seed.

* **W1 — the paper quota wall said the limit "lifts at the reset".** A paper
  slot is a concurrent cap (``cap_exceeded``, no period, no reset instant), so
  no reset ever comes. The wall now says the limit does not reset on a clock,
  and stays neutral: no action, no slot-freeing point (D-12, R-59). Control: a
  metered (weekly) wall still names its reset.
* **W2 — every paper output carried the raw ``"status": "LIVE"``.** Paper
  statuses now render in keel-app's words (``paperHealthBadge`` in
  ``services/keel-app/src/lib/status-badge.ts``): the parity arm reads that
  function and requires ``_paper_status.PAPER_STATUS_WORDS`` to equal it; the
  render arm requires no paper output to carry the token ``LIVE``; control: a
  real row in the same list keeps its ``LIVE``.
* **W3 — ``keel_paper_control`` outcomes never said "simulated".** Every
  action's outcome carries the paper label.
* **W4 — paper 409s and unknown-id 404s carried live/strategy advice.** A
  refused transition is answered in paper words (``paper_run_state_conflict``)
  and an unknown id points at the paper run list. Controls: a coded 409 is
  relayed unchanged; a live id still gets ``invalid_target``.
* **Polish** — the ladder claims no backtest the run may not have had, and
  ``keel_paper_control``'s description no longer leaks its annotation plumbing
  (the annotation itself stays).

Seeds (run 2026-10-12, each applied by an exact edit and reversed by the
inverse edit; recorded in the lane log ``lanes/RQ2.md``).
"""

from __future__ import annotations

import json
import re

import pytest

from tests import _surface_fixtures as sf
from tests.test_outcomes_paper import (
    _PAPER_CREATE_RESPONSE,
    _PAPER_DEP,
    _REAL_DEP,
    PAPER_CAP_403_BODY,
    _classed_ctx,
    _ClassedServer,
    _FakeTransport,
    _paper_ctx,
)


STATUS_BADGE_TS = sf.REPO_ROOT / "services" / "keel-app" / "src" / "lib" / "status-badge.ts"

#: The raw deployment status a reader takes for real capital.
_RAW_LIVE = re.compile(r"\bLIVE\b")
#: The full-profile wall names no paper tool (R-59). Since D-14 (doc 07 §5)
#: it does say how a slot frees ("stop one to free it"), the cap's own fact.
_WALL_ACTION_RE = re.compile(r"keel_paper_control", re.IGNORECASE)
#: A claimed reset in any text (the card's `reset` KEY is its line's name).
_RESET_CLAIM_RE = re.compile(r'\breset\b(?! on a clock)(?!": )', re.IGNORECASE)


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# ── W1: the concurrent cap's wall names no reset ───────────────────────────


def _paper_wall() -> tuple[dict, str]:
    from keel.errors import translate_http_error
    from keel.tools.outcomes import get
    from keel.tools.outcomes._handoff import HandoffRequired

    transport = _FakeTransport(
        {("POST", "/v1/paper/dep_p/resume"): translate_http_error(403, PAPER_CAP_403_BODY)}
    )
    with pytest.raises(HandoffRequired) as exc:
        get("keel_paper_control").handler(
            {"deployment_id": "dep_p", "action": "resume"}, _paper_ctx(transport)
        )
    return exc.value.to_envelope(), exc.value.suggestion


def test_w1_the_paper_cap_wall_says_it_does_not_reset(paper_sdk_released):
    env, suggestion = _paper_wall()
    assert env["limit_details"]["unit"] == "paper_strategies_max"  # non-vacuity
    blob = _dump({**env, "suggestion": suggestion})
    assert "before a slot frees" in suggestion
    assert "once a slot frees" in env["resume"]["verify_call"]["reason"]
    # The free way forward (spec 02 §D, J11): stopping one frees it, and a newer version is an update.
    assert "Stopping one frees it" in env["talking_points"][1]
    # No claim of a reset anywhere on the envelope.
    assert not _RESET_CLAIM_RE.findall(blob), _RESET_CLAIM_RE.findall(blob)
    # No paper tool named on the full profile (R-59), no destination.
    for text in (suggestion, env["resume"]["verify_call"]["reason"], *env["talking_points"]):
        assert not _WALL_ACTION_RE.search(text), text
    assert env.get("action_url") is None


def test_w1_the_403_suggestion_says_a_cap_does_not_reset():
    from keel.errors import translate_http_error

    err = translate_http_error(403, PAPER_CAP_403_BODY)
    assert "before a slot frees" in err.suggestion
    assert "before the reset" not in err.suggestion


def test_w1_control_a_metered_wall_still_names_its_reset():
    """CONTROL: a weekly backtest wall is not a cap; its reset stands."""
    from keel.errors import translate_http_error
    from keel.tools.outcomes._handoff import maybe_quota_handoff

    from tests.test_handoff_envelope import SPENT_WITH_PATHS_403

    err = translate_http_error(403, SPENT_WITH_PATHS_403)
    handoff = maybe_quota_handoff(
        err, blocked_action="backtest_run", retry_call={"tool": "keel_backtest_run", "args": {}}
    )
    env = handoff.to_envelope()
    assert env["resume"]["verify_call"]["reason"] == (
        "The same call, unchanged; it succeeds after the reset."
    )
    assert "before the reset" in handoff.suggestion
    assert "before the reset" in err.suggestion
    assert "slot frees" not in _dump(env) + handoff.suggestion + err.suggestion


# ── W2: paper statuses in keel-app's words ─────────────────────────────────

_CASE_RE = re.compile(r'^\s*case "([A-Z_]+)":')
_REFINE_RE = re.compile(r'^\s*if \((health|pause_reason) === "([a-z_]+)"\)')
_LABEL_RE = re.compile(r'label: "([^"]+)"')


def _ts_paper_badge_labels(text: str) -> dict[str, str]:
    """keel-app's ``paperHealthBadge`` → {branch: label}, read off the source.

    A label under ``if (health|pause_reason === "x")`` is ``CASE:x``; the
    case's unconditional label is ``CASE``; ``default:`` is ``default``."""
    start = text.index("export function paperHealthBadge(")
    body = text[start : text.index("\n}\n", start)]
    out: dict[str, str] = {}
    case: str | None = None
    refinement: str | None = None
    for line in body.splitlines():
        if m := _CASE_RE.match(line):
            case, refinement = m.group(1), None
        elif line.strip() == "default:":
            case, refinement = "default", None
        elif m := _REFINE_RE.match(line):
            refinement = m.group(2)
        elif (m := _LABEL_RE.search(line)) and case is not None:
            key = f"{case}:{refinement}" if refinement else case
            assert key not in out, f"two labels for {key} in paperHealthBadge"
            out[key] = m.group(1)
            refinement = None
    return out


@pytest.mark.skipif(not STATUS_BADGE_TS.is_file(), reason="reads keel-app (monorepo only)")
def test_w2_status_words_equal_keel_apps_paper_badge():
    from keel.tools.outcomes._paper_status import PAPER_STATUS_WORDS

    app = _ts_paper_badge_labels(STATUS_BADGE_TS.read_text(encoding="utf-8"))
    # Non-vacuity: every status the app draws, its refinements and the default.
    assert {"DEPLOYING", "LIVE", "PAUSED", "STOPPED", "default"} <= set(app), app
    assert len(app) >= 9, app
    assert PAPER_STATUS_WORDS == app


def test_w2_listed_words_pass_the_listed_text_rules(monkeypatch):
    """Every word the listed connector can emit passes the listed scan; the
    one override is needed (the app's "Deploying" fails it) and used."""
    from keel.tools.outcomes import _paper_status as st

    from tests.test_policy_scan import _text_violations

    listed = {**st.PAPER_STATUS_WORDS, **st.PAPER_STATUS_LISTED}
    violations = [v for k, w in listed.items() for v in _text_violations("status", k, w)]
    assert violations == []
    # CONTROL: the scan reds on the app's word the override replaces.
    assert _text_violations("status", "DEPLOYING", st.PAPER_STATUS_WORDS["DEPLOYING"])
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    assert st.paper_status_word("DEPLOYING") == st.PAPER_STATUS_LISTED["DEPLOYING"]
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    assert st.paper_status_word("DEPLOYING") == "Deploying"


@pytest.mark.parametrize(
    ("status", "health", "pause_reason", "word"),
    [
        ("LIVE", None, None, "Paper"),
        ("live", "degraded", None, "Paper · degraded"),
        ("LIVE", "stalled", "idle", "Paper · stalled"),
        ("PAUSED", None, "over_cap", "Paused · plan limit"),
        ("PAUSED", None, "idle", "Paused · idle"),
        ("PAUSED", "stalled", "user", "Paused"),
        ("STOPPED", None, None, "Stopped"),
        ("ARCHIVED", None, None, "Unknown"),  # never echoed
        (None, None, None, "Unknown"),
    ],
)
def test_w2_paper_status_word_follows_the_badge_rules(status, health, pause_reason, word):
    from keel.tools.outcomes._paper_status import paper_status_word

    assert paper_status_word(status, health=health, pause_reason=pause_reason) == word


def _paper_outputs(monkeypatch) -> list[tuple[str, str]]:
    """(output, dumped envelope) for every paper output this lane renders."""
    from keel.tools.outcomes import get

    outputs: list[tuple[str, str]] = []

    class _Create:
        def post(self, path, json=None, **_kw):
            return dict(_PAPER_CREATE_RESPONSE)

    deploy = get("keel_paper_deploy")
    outputs.append(
        (
            "keel_paper_deploy (full)",
            _dump(
                deploy.handler({"strategy_id": "strat_y"}, _classed_ctx(_Create())).to_envelope()
            ),
        )
    )
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_LISTED_PAPER", "1")
    outputs.append(
        (
            "keel_paper_deploy (listed)",
            _dump(
                deploy.handler({"strategy_id": "strat_y"}, _classed_ctx(_Create())).to_envelope()
            ),
        )
    )
    monkeypatch.delenv("KEEL_SERVER_PROFILE", raising=False)
    monkeypatch.delenv("KEEL_LISTED_PAPER", raising=False)

    row = {"deployment_id": "dep_p", "venue": {"code": "PAPER", "capital_class": "practice"}}
    transport = _FakeTransport(
        {
            ("POST", "/v1/paper/dep_p/pause"): {**row, "status": "PAUSED", "pause_reason": "user"},
            ("POST", "/v1/paper/dep_p/resume"): {**row, "status": "LIVE"},
            ("DELETE", "/v1/paper/dep_p"): None,
            ("POST", "/v1/paper/dep_p/restart"): {
                **row,
                "deployment_id": "dep_q",
                "status": "LIVE",
                "restarted_from": "dep_p",
            },
        }
    )
    control = get("keel_paper_control")
    for action in ("pause", "resume", "stop", "restart"):
        env = control.handler(
            {"deployment_id": "dep_p", "action": action}, _paper_ctx(transport)
        ).to_envelope()
        outputs.append((f"keel_paper_control {action}", _dump(env)))

    server = _ClassedServer({"/v1/deployments": [_REAL_DEP, _PAPER_DEP]})
    for capital_class in ("all", "practice"):
        env = (
            get("keel_deployments_list")
            .handler({"capital_class": capital_class}, _classed_ctx(server))
            .to_envelope()
        )
        practice = [r for r in env["deployments"] if r["deployment_id"] == "dep_paper"]
        outputs.append((f"keel_deployments_list {capital_class} practice row", _dump(practice)))
    return outputs


def test_w2_no_paper_output_carries_the_raw_live_status(paper_sdk_released, monkeypatch):
    outputs = _paper_outputs(monkeypatch)
    assert len(outputs) == 8 and all(text for _, text in outputs)  # non-vacuity
    offenders = [name for name, text in outputs if _RAW_LIVE.search(text)]
    assert offenders == [], f"a paper output carries the raw status LIVE: {offenders}"
    # Each output carries a status word (the projection ran, not a dropped key).
    words = ("Paper", "Paused", "Stopped", "Deploying", "Starting")
    for name, text in outputs:
        assert any(f'"{w}' in text for w in words), name


def test_w2_control_a_real_row_keeps_its_status(paper_sdk_released):
    """CONTROL: the detector fires on a real row; only practice rows map."""
    from keel.tools.outcomes import get

    server = _ClassedServer({"/v1/deployments": [_REAL_DEP, _PAPER_DEP]})
    env = get("keel_deployments_list").handler({}, _classed_ctx(server)).to_envelope()
    real = [r for r in env["deployments"] if r["deployment_id"] == "dep_live"]
    assert real and real[0]["status"] == "LIVE"
    assert _RAW_LIVE.search(_dump(real))


# ── W3: every paper_control outcome says simulated ─────────────────────────


def test_w3_every_paper_control_outcome_carries_the_paper_label(paper_sdk_released, monkeypatch):
    from keel.tools.outcomes._paper_strings import MONITOR_PAPER_LABEL

    controls = [
        (n, t) for n, t in _paper_outputs(monkeypatch) if n.startswith("keel_paper_control")
    ]
    assert len(controls) == 4
    for name, text in controls:
        env = json.loads(text)
        assert env["label"] == MONITOR_PAPER_LABEL.full, name
        assert "simulated" in env["label"]


def test_w3_new_state_is_keel_apps_word(paper_sdk_released, monkeypatch):
    states = {
        n.rsplit(" ", 1)[1]: json.loads(t)["new_state"]
        for n, t in _paper_outputs(monkeypatch)
        if n.startswith("keel_paper_control")
    }
    assert states == {"pause": "Paused", "resume": "Paper", "stop": "Stopped", "restart": "Paper"}


# ── W4: paper 409s and 404s carry paper advice ─────────────────────────────

_LIVE_ADVICE_RE = re.compile(
    r"keel strategy pull|keel_strategy_search|keel_strategy_history|commits|backtests"
    r"|keel_audit_list_last|server copy changed",
    re.IGNORECASE,
)


def _control_error(action: str, method: str, path: str, status: int, body, extra=None):
    from keel.errors import KeelError, translate_http_error
    from keel.tools.outcomes import get

    raw = body if isinstance(body, str) else json.dumps(body)
    routes = {(method, path): translate_http_error(status, raw), **(extra or {})}
    with pytest.raises(KeelError) as exc:
        get("keel_paper_control").handler(
            {"deployment_id": "dep_p", "action": action}, _paper_ctx(_FakeTransport(routes))
        )
    return exc.value


def _transition(current: str, target: str) -> dict:
    return {
        "detail": f"Invalid deployment transition: {current} -> {target}. "
        f"Allowed from {current}: none (terminal state)"
    }


@pytest.mark.parametrize(
    ("action", "method", "path", "body", "message_has", "suggestion_has"),
    [
        (
            "stop",
            "DELETE",
            "/v1/paper/dep_p",
            _transition("STOPPED", "STOPPED"),
            "already stopped",
            "keel_paper_deploy",
        ),
        (
            "resume",
            "POST",
            "/v1/paper/dep_p/resume",
            _transition("STOPPED", "LIVE"),
            "is stopped and cannot be resumed",
            "keel_paper_deploy",
        ),
        (
            "restart",
            "POST",
            "/v1/paper/dep_p/restart",
            {"detail": "Cannot restart a paper run in status 'STOPPED'."},
            "is stopped and cannot be restarted",
            "keel_paper_deploy",
        ),
        (
            "pause",
            "POST",
            "/v1/paper/dep_p/pause",
            _transition("PAUSED", "PAUSED"),
            "already paused",
            "action='resume'",
        ),
        (
            "resume",
            "POST",
            "/v1/paper/dep_p/resume",
            _transition("LIVE", "LIVE"),
            "already running (Paper)",
            "keel_live_monitor",
        ),
        (
            "restart",
            "POST",
            "/v1/paper/dep_p/restart",
            {"detail": "This paper run has a pending update. Retry that update before restarting."},
            "pending update",
            "keel_live_monitor",
        ),
    ],
)
def test_w4_a_refused_transition_is_answered_in_paper_words(
    paper_sdk_released, action, method, path, body, message_has, suggestion_has
):
    err = _control_error(action, method, path, 409, body)
    assert err.error_code == "paper_run_state_conflict"
    assert message_has in str(err)
    assert suggestion_has in (err.suggestion or "")
    text = f"{err} {err.suggestion}"
    assert not _LIVE_ADVICE_RE.search(text), text
    assert not _RAW_LIVE.search(text) and "->" not in str(err), text
    assert "simulated" in str(err) or "paper run" in str(err).lower()


def test_w4_control_a_coded_conflict_is_relayed_unchanged(paper_sdk_released):
    """CONTROL: a 409 that names its own code speaks for itself."""
    body = {"detail": {"code": "SOME_CODED_REFUSAL", "message": "coded"}}
    err = _control_error("pause", "POST", "/v1/paper/dep_p/pause", 409, body)
    assert err.error_code == "SOME_CODED_REFUSAL"


def test_w4_an_unknown_id_points_at_the_paper_run_list(paper_sdk_released):
    from keel.errors import NotFoundError, translate_http_error

    from tests.test_outcomes_paper import NOT_FOUND_BODY

    err = _control_error(
        "pause",
        "POST",
        "/v1/paper/dep_p/pause",
        404,
        NOT_FOUND_BODY,
        extra={("GET", "/v1/deployments/dep_p"): translate_http_error(404, NOT_FOUND_BODY)},
    )
    assert isinstance(err, NotFoundError) and err.error_code == "not_found"
    assert "keel_deployments_list" in err.suggestion
    assert "capital_class=practice" in err.suggestion
    assert not _LIVE_ADVICE_RE.search(err.suggestion), err.suggestion


def test_w4_control_a_live_id_still_names_keel_live_control(paper_sdk_released):

    from tests.test_outcomes_paper import NOT_FOUND_BODY

    err = _control_error(
        "pause",
        "POST",
        "/v1/paper/dep_p/pause",
        404,
        NOT_FOUND_BODY,
        extra={
            ("GET", "/v1/deployments/dep_p"): {
                "deployment_id": "dep_p",
                "venue": {"code": "HYPERLIQUID", "capital_class": "real"},
            }
        },
    )
    assert err.error_code == "invalid_target"
    assert err.suggestion == "use keel_live_control"


# ── Polish ─────────────────────────────────────────────────────────────────


def test_polish_the_ladder_claims_no_backtest(paper_sdk_released, monkeypatch):
    from keel.tools.outcomes._paper_strings import LADDER_LINE

    deploys = [t for n, t in _paper_outputs(monkeypatch) if n.startswith("keel_paper_deploy")]
    assert len(deploys) == 2
    for text in deploys:
        notes = json.loads(text)["run_notes"]
        assert "Backtest → Paper → Live" in notes  # the ladder is still drawn
        assert "Backtested" not in notes and "✓" not in notes
    assert "Backtested" not in f"{LADDER_LINE.full} {LADDER_LINE.listed}"


def test_polish_paper_control_description_states_no_annotation_plumbing(paper_sdk_released):
    from keel.tools.outcomes import get

    tool = get("keel_paper_control")
    assert "destructiveHint" not in tool.description
    assert tool.annotations["destructiveHint"] is True  # the annotation stays
