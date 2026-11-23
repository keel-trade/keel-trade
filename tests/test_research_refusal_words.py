"""Research refusals and lists in the words their reader needs (STG-SURFACES #20, #21, #23, #25, #28, #31, #32).

- #20: a removed share's 410 read "Keel refused the request (HTTP 410): …" then "Check the inputs against the tool's
  schema"; it now reads keel-api's served sentence, with its code.
- #21: a refusal with no expectation printed the envelope's default "Valid input matching the tool's schema.".
- #23: the delete-with-shares refusal named `keel research delete --revoke-shares` with no study id.
- #25 / #28: an empty list offered no next step; the CLI listed 25 where R-22 says 100.
- #31 / #32: the MCP refusal text carried no `error: <code>` line, and a plan wall's instruction only in the
  envelope. The CLI, read by a person, prints neither.
"""

from __future__ import annotations

import json

import click
from click.testing import CliRunner
from keel.errors import translate_http_error
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._research_commands import (
    apply_research_refusal,
    cli_refusal_text,
    refusal_text,
)


_bootstrap()
APP = "https://app.usekeel.io"
REMOVED = "This snapshot was removed by its author on Nov 18, 2026."


def _refused(status: int, body: dict, args: dict, tool: str = "keel_research_get") -> dict:
    err = translate_http_error(status, json.dumps(body))
    return apply_research_refusal(err, args, app_url=f"{APP}/research", tool=tool).to_envelope()


def test_a_removed_share_reads_its_served_words() -> None:
    body = {"code": "RESEARCH_SHARE_REMOVED", "message": REMOVED, "removed_on": "2026-11-18"}
    env = _refused(410, body, {"share_id": "S" * 23})
    assert env["code"] == "RESEARCH_SHARE_REMOVED" and env["message"] == REMOVED
    assert cli_refusal_text(env).splitlines() == [REMOVED, f"View in Keel: {APP}/research"]
    # control: any other uncoded status keeps the plain "Keel refused the request" sentence
    other = translate_http_error(405, "")
    assert str(other) == "Keel refused the request (HTTP 405)."


def test_the_schema_default_is_never_printed() -> None:
    from keel.errors import UsageError

    err = UsageError("Send --ref-kind (study, strategy, output, screen or paper_run) for this ref.")
    env = apply_research_refusal(err, {}, app_url=f"{APP}/research").to_envelope()
    assert (
        env["what_was_expected"] == "Valid input matching the tool's schema."
    )  # what the envelope defaults to
    lines = cli_refusal_text(env).splitlines()
    assert lines == [str(err), f"View in Keel: {APP}/research"], lines
    # control: a refusal whose expectation says what to do still prints it
    no_coins = _refused(422, {"detail": {"code": "RESEARCH_SHARE_NO_COINS", "message": "x"}}, {})
    assert any(
        line.startswith("The share cannot be copied")
        for line in cli_refusal_text(no_coins).splitlines()
    )


def test_the_delete_refusal_names_the_command_for_this_study() -> None:
    body = {
        "detail": {"code": "RESEARCH_DELETE_SHARED", "message": "This study has 1 shared link."}
    }
    env = _refused(409, body, {"study_id": "stu_a"}, tool="keel_research_delete")
    lines = cli_refusal_text(env).splitlines()
    assert (
        "Revoke its shares with the delete: keel research delete stu_a --revoke-shares" in lines
    ), lines


def test_mcp_text_carries_the_code_and_the_plan_wall_instruction() -> None:
    wall = {
        "detail": {
            "code": "quota_exhausted",
            "unit": "research_studies",
            "message": "You have used 3 of 3 studies; they reset Dec 18.",
        }
    }
    env = _refused(403, wall, {})
    mcp = refusal_text(env).splitlines()
    # D-403: the code stays in the envelope, never a text line
    assert mcp[-1] == f"View in Keel: {APP}/research" and not any(
        line.startswith("error:") for line in mcp
    ), mcp
    assert any(line.startswith("Plan limit;") for line in mcp), mcp
    # control: the person's CLI text names neither the code nor the agent's instruction
    cli = cli_refusal_text(env)
    assert "error:" not in cli and "Plan limit;" not in cli


# ── #25 / #28: the list ────────────────────────────────────────────────────────


class _Pages:
    """GET /v1/research/studies: ``total`` studies, at most 25 a page (the route's ``le=25``)."""

    def __init__(self, total: int) -> None:
        self.total, self.asked = total, []

    def get(self, path, headers=None, **params):
        assert path == "/v1/research/studies"
        limit = int(params["limit"])
        assert limit <= 25, limit
        start = int(params.get("cursor") or 0)
        self.asked.append((start, limit))
        end = min(start + limit, self.total)
        rows = [{"study_id": f"stu_{i:03d}", "title": f"Study {i}", "kind_label": {"short": "Check"},
                 "state_words": "Done"} for i in range(start, end)]  # fmt: skip
        more = end < self.total
        return {
            "data": rows,
            "studies_total": self.total,
            "pagination": {"has_more": more, "cursor": str(end) if more else None},
        }


def _cli_list(monkeypatch, client, *argv: str) -> str:
    from keel.tools.outcomes._cli_adapter import register_all

    for k in ("KEEL_SERVER_PROFILE", "KEEL_AGENT_MODE", "CLAUDECODE", "KEEL_TOOLSETS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ToolContext, "get_client", lambda self: client)
    root = click.Group("keel")
    register_all(root, OUTCOMES)
    out = CliRunner().invoke(root, ["research", "list", *argv, "--format", "human"])
    assert out.exit_code == 0, out.output
    return out.output


def test_the_cli_lists_100_studies_by_default(monkeypatch) -> None:
    client = _Pages(130)
    text = _cli_list(monkeypatch, client)
    assert client.asked == [(0, 25), (25, 25), (50, 25), (75, 25)]
    assert text.splitlines()[0].startswith("Studies: 100 shown of 130")
    assert "More: keel research get --cursor 100" in text
    # control: -n reads only what it asks for
    client = _Pages(130)
    _cli_list(monkeypatch, client, "-n", "30")
    assert client.asked == [(0, 25), (25, 5)]


def test_an_empty_list_says_how_to_start(monkeypatch) -> None:
    text = _cli_list(monkeypatch, _Pages(0))
    # AUDIT S3 #12: the reader's own strategies are listed first, so the placeholder is one it can fill
    assert (
        "Start one: keel strategy search lists your strategies; keel research plan --template check --subjects"
        " 'Strategy(\"str_…@3\")' checks one, with its id and version in place of str_…@3." in text
    ), text
    # control: a list with studies offers no start line; MCP's list keeps 25 a page
    assert "Start one" not in _cli_list(monkeypatch, _Pages(3))
    client = _Pages(40)
    monkeypatch.setattr(ToolContext, "get_client", lambda self: client)
    OUTCOMES["keel_research_get"].handler({}, ToolContext())
    assert client.asked == [(0, 25)]
