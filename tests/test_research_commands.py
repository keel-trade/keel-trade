"""Research's refusal table (spec 07 §3.4; AT-14's error half).

Every row's next step is a real tool on the full profile, and on the listed
profile a tool the listing does not serve becomes keel_app_link. Refusals with
no next action carry an explicit None, entitlement refusals included (never
keel_auth_login).

SEED (reverted by reversing the edit): removing the listed substitution in
``apply_research_refusal`` reds ``test_listed_next_steps_are_served_tools``.
"""

from __future__ import annotations

import json

import pytest
from keel.errors import EntitlementError, KeelError, translate_http_error
from keel.tools.outcomes._research_commands import NO_NEXT, REFUSALS, apply_research_refusal


def _err(code: str, **detail) -> KeelError:
    err = KeelError("refused")
    err.error_code = code
    err.detail = {"code": code, **detail}
    return err


def test_the_table_is_not_vacuous() -> None:
    assert len(REFUSALS) >= 10 and len(NO_NEXT) >= 8


@pytest.mark.parametrize("code", sorted(REFUSALS))
def test_full_profile_next_steps(monkeypatch, code) -> None:
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    err = apply_research_refusal(
        _err(code, plan_id="pln_a", current_revision=3),
        {"study_id": "stu_a", "plan_id": "pln_a", "cursor": "c"},
    )
    tool = REFUSALS[code][0]
    assert err.recovery_tool == tool
    assert err.suggestion
    if code == "RESEARCH_PLAN_EXPIRED":
        assert err.recovery_tool_args == {"from_plan": "pln_a"}
    if code == "RESEARCH_CURSOR_INVALID":
        assert "cursor" not in err.recovery_tool_args
    if code == "RESEARCH_PRESENTATION_ONLY":
        assert err.recovery_tool_args == {"study_id": "stu_a", "expected_revision": 3}
    if (
        code == "STUDY_EVIDENCE_CHANGED"
    ):  # AT-9: view_set's evidence refusal points at a plan of the study
        assert err.recovery_tool_args == {"study_id": "stu_a"}
    if code == "RESEARCH_CELL_REPORT_HELD":  # RI-461: no held step in this detail: the study alone
        assert err.recovery_tool_args == {"study_id": "stu_a"}


def test_a_held_cell_report_reads_its_summary(monkeypatch) -> None:
    """RI-461 (07 §3.4): RESEARCH_CELL_REPORT_HELD's next call reads the held report's summary, named by the
    refusal's ``held.step``, on the study asked of."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    held = {
        "step": "report_d9864bcb",
        "grid": "map",
        "at": {"lookback": 12, "hold": 20},
        "words": "lookback 12 · hold 20",
    }
    err = apply_research_refusal(
        _err("RESEARCH_CELL_REPORT_HELD", held=held), {"study_id": "stu_a"}
    )
    assert err.recovery_tool == "keel_research_get"
    assert err.recovery_tool_args == {"study_id": "stu_a", "output": "report_d9864bcb.summary"}


def test_listed_next_steps_are_served_tools(monkeypatch) -> None:
    from keel.tools.outcomes._toolsets import listed_profile_tools

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    served = listed_profile_tools() | {"keel_app_link"}
    substituted = 0
    for code in sorted(REFUSALS):
        err = apply_research_refusal(
            _err(code), {"study_id": "stu_a"}, app_url="https://app.usekeel.io/research/stu_a"
        )
        if err.recovery_tool is None:
            continue
        assert err.recovery_tool in served, (code, err.recovery_tool)
        substituted += err.recovery_tool == "keel_app_link"
    assert substituted >= 5  # not vacuous: the research tools are not listed yet


@pytest.mark.parametrize("code", sorted(NO_NEXT))
def test_no_next_is_explicit(code) -> None:
    err = _err(code)
    err.recovery_tool = "keel_auth_login"
    out = apply_research_refusal(err, {})
    assert out.recovery_tool is None and out.recovery_tool_args is None


def test_an_entitlement_refusal_never_names_a_login() -> None:
    body = json.dumps(
        {"detail": "This study was not run and nothing was charged.", "code": "quota_exhausted"}
    )
    err = translate_http_error(403, body)
    assert isinstance(err, EntitlementError)
    out = apply_research_refusal(err, {})
    assert out.to_envelope()["suggested_next_action"] == {"tool": None, "args": {}}


def test_the_paused_refusal_stays_bare() -> None:
    err = translate_http_error(
        404, json.dumps({"detail": "Research is paused.", "code": "RESEARCH_PAUSED"})
    )
    env = apply_research_refusal(err, {"study_id": "stu_a"}).to_envelope()
    assert env["message"] == "Research is paused."
    assert env["suggested_next_action"] == {"tool": None, "args": {}}


def test_an_unknown_code_passes_through() -> None:
    err = _err("SOMETHING_ELSE")
    err.recovery_tool = "keel_help"
    assert apply_research_refusal(err, {}).recovery_tool == "keel_help"


# ── J14 rows 1 and 6: a refused plan's text (07 §4.12) and the CLI's plan form ──


def _issue(
    path: str, message: str, code: str = "STUDY_ADDRESS_SYNTAX", severity: str = "error"
) -> dict:
    return {"code": code, "path": path, "message": message, "severity": severity}


def test_a_refused_plan_leads_with_the_count_and_lists_each_issue() -> None:
    """SEED (reversed): drop ``err.args = (text,)`` in apply_research_refusal -> this test fails."""
    one = _err(
        "STUDY_ADDRESS_SYNTAX",
        issues=[_issue("/subjects/s1/set", '"ROC#1.period" is not an address.')],
    )
    apply_research_refusal(one, {}, tool="keel_research_plan")
    assert str(one).splitlines() == [
        "Plan refused: 1 issue; nothing was planned or spent.",
        '/subjects/s1/set · "ROC#1.period" is not an address.',
        "Fix each, then plan again.",
    ]
    three = _err(
        "STUDY_ADDRESS_NOT_FOUND",
        issues=[_issue(f"/steps/map/axes/{i}", f"axis {i}") for i in range(3)]
        + [_issue("/run", "a warning", severity="warning")],
    )
    apply_research_refusal(three, {}, tool="keel_research_plan")
    assert str(three).startswith("Plan refused: 3 issues; nothing was planned or spent.\n")
    assert "a warning" not in str(three)


def test_only_the_plan_tool_and_only_issues_rewrite_the_text() -> None:
    other = _err("STUDY_ADDRESS_SYNTAX", issues=[_issue("/x", "y")])
    apply_research_refusal(other, {}, tool="keel_research_view_set")
    assert str(other) == "refused"
    bare = _err("RESEARCH_PLAN_STALE")
    apply_research_refusal(bare, {}, tool="keel_research_plan")
    assert str(bare) == "refused"


def test_ri381_a_data_move_points_at_the_refusals_newer_data_offer(monkeypatch) -> None:
    """RI-381 (04 §1.6, 07 §3.4): on moved data a Draft's Run, its plan and ``from_plan`` all answer
    RESEARCH_PLAN_STALE carrying the ``newer_data`` fact, so the next call is that fact's ``offer.plan`` (the linked
    study on today's data), said in words. Controls: a stale refusal with no offer (the study or the engine moved)
    still points at ``from_plan``; a ``newer_subject`` fact's offer is not the data's.

    SEED (reversed by reversing the edit): the row's builder back to ``_from_plan`` -> the offer arm fails."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    plan = {"study_id": "stu_a", "source": 'Study(title="t")\n'}
    body = {
        "code": "RESEARCH_PLAN_STALE",
        "message": "Newer data is in since this was saved.",
        "stale": [
            {
                "kind": "newer_data",
                "words": "Newer data is in since this was saved.",
                "offer": {"snapshot": "snap_new", "plan": plan},
            }
        ],
    }
    err = apply_research_refusal(
        translate_http_error(409, json.dumps({"detail": body})), {"study_id": "stu_a"}
    )
    assert (err.recovery_tool, err.recovery_tool_args) == ("keel_research_plan", plan)
    assert "newer-data offer" in err.suggestion and "linked study" in err.suggestion
    bare = apply_research_refusal(_err("RESEARCH_PLAN_STALE"), {"plan_id": "pln_a"})
    assert bare.recovery_tool_args == {"from_plan": "pln_a"}  # control: no offer
    subject = apply_research_refusal(
        _err(
            "RESEARCH_PLAN_STALE",
            stale=[{"kind": "newer_subject", "offer": {"plan": {"study_id": "stu_b"}}}],
        ),
        {"plan_id": "pln_a"},
    )
    assert subject.recovery_tool_args == {"from_plan": "pln_a"}  # control: not the data's offer


def test_ri381_the_research_topic_says_a_data_moves_way_forward() -> None:
    """RI-381: the agent reference (keel_help topic "research", bundled byte for byte) names the offer."""
    from importlib.resources import files

    text = files("keel.data").joinpath("reference/research.md").read_text(encoding="utf-8")
    assert "`offer.plan`" in text and "linked study" in text


def test_the_cli_plan_form_sends_no_unset_again_flags() -> None:
    from keel.tools.outcomes.research_plan import plan_body

    body = plan_body({"template": "check", "subjects": ["S"], "resume": False, "retry": False})
    assert body == {"template": "check", "subjects": ["S"]}
    again = plan_body({"study_id": "stu_x", "resume": True, "retry": False})
    assert again == {"study_id": "stu_x", "resume": True}


def test_a_coinless_share_names_what_to_do_not_change_your_input() -> None:
    """RI-040: keel-api answers a coinless snapshot ``RESEARCH_SHARE_NO_COINS`` 422 on the copy and on the
    from-share plan. The caller's input cannot fix it, so the table's sentence replaces the 422's generic
    "change that input and retry", with no next tool. Control: an unlisted 422 keeps the generic sentence.

    SEED (reverted by reversing the edit): drop the RESEARCH_SHARE_NO_COINS row from ``REFUSALS`` -> this test
    fails on the generic sentence; the control arm stays green."""
    body = {
        "code": "RESEARCH_SHARE_NO_COINS",
        "message": "This shared study doesn't list the coins.",
    }
    err = apply_research_refusal(translate_http_error(422, json.dumps({"detail": body})), {})
    assert err.recovery_tool is None and err.recovery_tool_args is None
    assert "retry" not in (err.suggestion or ""), err.suggestion
    assert "ask its author to share it again" in err.suggestion
    other = {"code": "RESEARCH_SOMETHING_ELSE", "message": "x"}
    control = apply_research_refusal(translate_http_error(422, json.dumps({"detail": other})), {})
    assert "retry" in (control.suggestion or "")


def test_ri286_an_argument_refusal_of_a_research_tool_links_into_the_app(monkeypatch) -> None:
    """RI-286 (07 §3.2 rule 9, §3.4): ``usage_refusal`` gives a research tool's argument refusal the app link
    every research answer carries: the study's page when the call names a study, else Research's home, on
    ``KEEL_APP_URL`` when set; its text is then the research refusal text, ending "View in Keel: {url}". Control:
    any other tool's envelope comes back unchanged (the SDK's non-research errors keep their shape).
    SEED (reversed): ``usage_refusal`` returning ``dict(envelope)`` first thing -> the two research arms red."""
    from keel.tools.outcomes._research_commands import refusal_text, usage_refusal

    env = {
        "code": "usage_error",
        "message": "Invalid arguments to keel_research_get: x.",
        "detail": {"a": 1},
    }
    monkeypatch.delenv("KEEL_APP_URL", raising=False)
    home = usage_refusal(env, "keel_research_plan", {})
    assert home["detail"] == {"a": 1, "links": {"app": "https://app.usekeel.io/research"}}
    monkeypatch.setenv("KEEL_APP_URL", "https://staging-app.example")
    study = usage_refusal(env, "keel_research_get", {"study_id": "stu_x"})
    assert study["detail"]["links"]["app"] == "https://staging-app.example/research/stu_x"
    assert (
        refusal_text(study).splitlines()[-1]
        == "View in Keel: https://staging-app.example/research/stu_x"
    )
    assert usage_refusal(env, "keel_strategy_get", {"study_id": "stu_x"}) == env  # control
    assert env == {
        "code": "usage_error",
        "message": "Invalid arguments to keel_research_get: x.",
        "detail": {"a": 1},
    }


# ── RI-251 (COST-SDK residual 5): the CLI prints a research refusal as the MCP text does ─────────────────────────


def _cli_refused(monkeypatch, argv: list[str], body: dict, status: int = 400):
    import click
    from click.testing import CliRunner
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes._cli_adapter import register_all

    class Refusing:
        def get(self, path, **params):
            raise translate_http_error(status, json.dumps({"detail": body}))

        post = get

    _bootstrap()
    monkeypatch.setattr(ToolContext, "get_client", lambda self: Refusing())
    monkeypatch.setenv("KEEL_APP_URL", "https://app.usekeel.io")
    root = click.Group("keel")
    register_all(root, OUTCOMES)
    return CliRunner().invoke(root, argv)


def test_the_cli_prints_a_research_refusal_as_words_next_command_and_link(monkeypatch) -> None:
    """Human output: the served words, the next call as the `keel` command that makes it, `View in Keel` last; no
    `Error:` line and no envelope. Seeded red (reverted by reversing the edit): `_emit_research_refusal` returning
    False reds this test (the old `Error: …` line comes back)."""
    body = {
        "code": "RESEARCH_CURSOR_INVALID",
        "message": "That page of results is no longer there.",
    }
    out = _cli_refused(monkeypatch, ["research", "get", "stu_a", "--cursor", "c1"], body)
    assert out.exit_code != 0
    lines = [line for line in out.output.splitlines() if line.strip()]
    assert lines[0] == "That page of results is no longer there."
    assert lines[-1] == "View in Keel: https://app.usekeel.io/research/stu_a"
    assert any(line.endswith("keel research get stu_a") for line in lines[1:-1]), lines
    assert not any(line.startswith("Error:") or line.lstrip().startswith("{") for line in lines)


def test_the_cli_keeps_the_envelope_in_json_output(monkeypatch) -> None:
    """Control: --format json prints the envelope, unchanged by the research text."""
    body = {
        "code": "RESEARCH_CURSOR_INVALID",
        "message": "That page of results is no longer there.",
    }
    argv = ["research", "get", "stu_a", "--cursor", "c1", "--format", "json"]
    out = _cli_refused(monkeypatch, argv, body)
    env = json.loads(out.output[out.output.index("{") :])
    assert env["code"] == "RESEARCH_CURSOR_INVALID"
    assert env["detail"]["links"]["app"] == "https://app.usekeel.io/research/stu_a"


def test_cli_call_renders_the_registered_command() -> None:
    from keel.tools.outcomes import _bootstrap
    from keel.tools.outcomes._research_commands import cli_call

    _bootstrap()

    assert (
        cli_call("keel_research_plan", {"from_plan": "pln_a"})
        == "keel research plan --from-plan pln_a"
    )
    assert (
        cli_call("keel_research_get", {"study_id": "stu_a", "view": 2})
        == "keel research get stu_a --view 2"
    )
    assert cli_call("keel_app_link", {"id": "stu_a"}) == "keel app open stu_a"
    assert cli_call("keel_no_such_tool", {}) is None


def test_the_steps_listing_names_its_grammar() -> None:
    """K15 (RI-092): `keel research steps` (no name) says the answer carries the grammar; one step by name does not."""
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()
    grammar = {
        "references": [{}] * 4,
        "addresses": {},
        "builders": ["Mix"],
        "rules": {"STUDY_A": {}, "STUDY_B": {}},
    }
    served = {
        "catalog_version": "v#2",
        "steps": [{"name": "Backtest", "version": 1, "about": "a"}],
        "steps_total": 1,
    }

    class Client:
        def __init__(self, env):
            self.env = env

        def get(self, path, **params):
            return self.env

    def text(env):
        ctx = ToolContext()
        ctx.get_client = lambda: Client(env)
        return OUTCOMES["keel_research_steps"].handler({}, ctx).extra["view"]["markdown"]

    assert "Grammar: 4 reference forms, the address grammar and 2 rules" in text(
        {**served, "grammar": grammar}
    )
    assert "Grammar:" not in text(served)


@pytest.mark.parametrize(
    ("handler", "args", "path"),
    [
        ("resume", {"run_set_id": "rus_a"}, "/v1/research/run-sets/rus_a/resume"),
        ("retry", {"run_set_id": "rus_a"}, "/v1/research/run-sets/rus_a/retry"),
        (
            "run_draft",
            {"study_id": "stu_a", "version": 2},
            "/v1/research/studies/stu_a/versions/2/run",
        ),
    ],
)
def test_ri504_every_costed_research_verb_sends_an_idempotency_key(
    monkeypatch, handler, args, path
) -> None:
    """RI-504: keel-api refuses a costed research call with no Idempotency-Key (422 idempotency_key_required; it was a
    500). The CLI's run-draft, resume and retry each send one, a fresh key per call (``keel research run`` already
    did). SEED: drop ``headers=`` from ``_resubmit`` -> the resume and retry arms red; run_draft stays green."""
    from keel.tools.outcomes import research_cli

    sent: list[tuple[str, dict | None]] = []

    class Client:
        def post(self, p, json=None, headers=None, **params):  # a POST's query (``?card=full``, WL-SV-21)
            sent.append((p, headers))
            return {"study": {"study_id": "stu_a", "state": "running"}, "links": {}}

    class Ctx:
        def get_client(self):
            return Client()

    monkeypatch.setattr(research_cli, "_study_answer", lambda env: env)
    run = research_cli._run_draft if handler == "run_draft" else research_cli._resubmit(handler)
    run(args, Ctx())
    run(args, Ctx())
    assert [p for p, _ in sent] == [path, path]  # non-vacuity: the handler posted, twice
    keys = [(h or {}).get("Idempotency-Key") for _, h in sent]
    assert all(keys) and keys[0] != keys[1], keys


@pytest.mark.parametrize(
    "body",
    [
        # research's own routes (research_backend.authorize_run, RI-504)
        {
            "detail": {
                "code": "idempotency_key_required",
                "message": "A run takes an Idempotency-Key header.",
            }
        },
        # keel-api's global handler on any other route (errors.py, RI-516)
        {
            "type": "https://api.usekeel.io/errors/validation_error",
            "title": "Unprocessable Entity",
            "status": 422,
            "detail": "This request uses your allowance, so it takes an Idempotency-Key header.",
            "code": "idempotency_key_required",
        },
    ],
    ids=["research-route", "global-handler"],
)
def test_ri516_a_missing_idempotency_key_reads_what_to_send_with_no_next_tool(body) -> None:
    """RI-504 / RI-516: keel-api answers a costed call with no Idempotency-Key ``422 idempotency_key_required`` in
    both of its shapes. The agent gets the table's words (send the header; nothing ran) and no next tool, never the
    generic 422 "change that input and retry". SEED: drop the row from ``REFUSALS`` -> both arms red (the
    row is gone); the coinless-share control above stays green."""
    err = apply_research_refusal(translate_http_error(422, json.dumps(body)), {})
    assert err.error_code == "idempotency_key_required"  # non-vacuity: both shapes carry the code
    assert err.recovery_tool is None and err.recovery_tool_args is None
    assert err.suggestion == REFUSALS["idempotency_key_required"][2]
    assert "Idempotency-Key" in err.suggestion and "Nothing ran" in err.suggestion
