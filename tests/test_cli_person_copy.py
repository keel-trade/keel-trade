"""What a person at the terminal reads (STG-SURFACES #1, #16, #17, #19, #26, #27).

The staging walk's CLI transcripts printed agent-facing copy to a person: "1 coins", a share's envelope field by
field (`views: [ 0, 1, 2 ]`), a confirm prompt naming `keel_share_create` and `key=value` pairs, `keel app open`'s
help about "this strategy" with no study id, a library signal named by its strategy id, and flag help quoting
`keel_research_get(subject=…)` and "in the chat". Each arm here drives the real formatter or the real CLI command
tree; the controls keep what must not change (the share's structured fields, a non-library output's ref, the MCP
schema's own words).
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


_bootstrap()


def _root() -> click.Group:
    from keel.tools.outcomes._cli_adapter import register_all

    root = click.Group("keel")
    register_all(root, OUTCOMES)
    return root


def _command(root: click.Group, *path: str) -> click.Command:
    cmd: click.Command = root
    for part in path:
        assert isinstance(cmd, click.Group), path
        cmd = cmd.commands[part]
    return cmd


# ── #1: one coin is "1 coin" ────────────────────────────────────────────────────


def test_the_footing_counts_one_coin_as_a_coin() -> None:
    from keel.tools.outcomes._research_view import footing_words

    footing = {
        "days": {"start": "2025-11-17", "end": "2026-11-17"},
        "coins": {"count": 1, "chosen": "The coins BTC buy and hold trades"},
        "costs": "none",
    }
    assert " · 1 coin: the coins BTC buy and hold trades" in footing_words(footing)
    # control: many coins keep the plural
    many = footing_words({**footing, "coins": {"count": 30, "chosen": "Top 30 by volume"}})
    assert " · 30 coins: top 30 by volume" in many


# ── #16: a study share prints a receipt, not its envelope ──────────────────────


class _Sharing:
    def post(self, path, json=None, **_):
        return {
            "share_id": "S" * 23,
            "study_id": "stu_a",
            "version": 1,
            "url": "https://staging-app.example/share/" + "S" * 23,
            "views": [0, 1, 2],
            "show_author": False,
        }


def test_a_study_share_prints_its_receipt(monkeypatch) -> None:
    for k in (
        "KEEL_SERVER_PROFILE",
        "KEEL_EXECUTION_MODE",
        "KEEL_TOOLSETS",
        "KEEL_AGENT_MODE",
        "CLAUDECODE",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ToolContext, "get_client", lambda self: _Sharing())
    out = CliRunner().invoke(_root(), ["share", "create", "stu_a", "--yes", "--format", "human"])
    assert out.exit_code == 0, out.output
    lines = [line for line in out.output.splitlines() if line.strip()]
    assert lines == [
        # AUDIT-WORDS M35: the study by its title (the share answer serves none: "the study"), never its id
        "Shared a snapshot of the study, version 1, showing 3 charts, without your name.",
        "Share link: https://staging-app.example/share/" + "S" * 23,
    ], lines
    # control: the structured answer keeps every field
    env = OUTCOMES["keel_share_create"].handler(
        {"target_id": "stu_a"}, ToolContext(api_client=_Sharing())
    )
    fields = env.to_envelope()
    assert (
        fields["views"] == [0, 1, 2] and fields["share_type"] == "study" and fields["version"] == 1
    )


# ── #17: the confirm prompt reads the command, not the tool ────────────────────


def test_the_confirm_prompt_names_the_command(monkeypatch) -> None:
    from keel.tools.outcomes import _cli_adapter

    asked: list[str] = []
    monkeypatch.setattr(_cli_adapter.click, "confirm", lambda text, **_: asked.append(text))
    tool = OUTCOMES["keel_research_delete"]
    _cli_adapter._confirm_destructive(
        tool, {"study_id": "stu_a", "revoke_shares": False, "yes": False}
    )
    assert asked == [
        "This runs `keel research delete stu_a`, a change that may not be undone.\nContinue?"
    ]
    assert "keel_" not in asked[0] and "=" not in asked[0]


# ── #19: `keel app open` help names every id it takes ──────────────────────────


def test_app_open_help_is_written_for_the_person() -> None:
    help_text = _command(_root(), "app", "open").help or ""
    assert "this strategy" not in help_text and "stu_" in help_text and "share" in help_text
    # control: the listed tool's own description is frozen and unchanged (the MCP surface)
    assert OUTCOMES["keel_app_link"].description.startswith(
        "Returns a link to view and manage this strategy in the Keel web app."
    )


# ── #26: a library output is named by its keel ref ─────────────────────────────


def test_signals_search_names_a_library_output_by_its_keel_ref(monkeypatch) -> None:
    from keel.tools.outcomes import blocks_search

    items = [
        {"name": "7-day momentum", "output": "momentum_7d", "role": "signal", "ref": "str_01x@1",
         "keel_ref": "keel/momentum_7d@1"},
        {"name": "Mine", "output": "carry", "role": "signal", "ref": "str_02y@3"},
    ]  # fmt: skip

    class _R:
        extra = {"items": items, "total": 2}

    monkeypatch.setattr(blocks_search, "_SEARCH", lambda args, ctx: _R())
    text = blocks_search._handler({}, ToolContext()).to_envelope()["view"]["markdown"]
    assert 'Output("keel/momentum_7d@1", output="momentum_7d")' in text
    assert "str_01x" not in text
    # control: an output with no keel ref keeps its own pinned ref
    assert 'Output("str_02y@3", output="carry")' in text


# ── #27: research flag help speaks the CLI ─────────────────────────────────────


def _research_option_help() -> dict[str, str]:
    root = _root()
    out: dict[str, str] = {}
    research = _command(root, "research")
    assert isinstance(research, click.Group)
    for name, cmd in research.commands.items():
        for param in cmd.params:
            if isinstance(param, click.Option) and not param.hidden:
                out[f"{name} {param.opts[0]}"] = param.help or ""
    return out


def test_cli_research_flag_help() -> None:
    from keel.tools.outcomes._cli_adapter import CLI_FLAG_WORDS
    from keel.tools.outcomes._output_schemas import RESEARCH_INPUT_SCHEMAS

    helps = _research_option_help()
    assert len(helps) > 40  # non-vacuous: every research verb's flags were read
    leaking = {k: v for k, v in helps.items() if "keel_research_" in v or "in the chat" in v}
    assert not leaking, leaking
    assert (
        "plan --present" not in helps
    )  # a terminal draws no card: the flag is accepted, not listed
    # every rewrite still has a schema line to rewrite (no dead entry), and the MCP schema keeps its own words
    schema_lines = [
        p.get("description", "")
        for s in RESEARCH_INPUT_SCHEMAS.values()
        for p in s.get("properties", {}).values()
    ]
    for mcp, _cli in CLI_FLAG_WORDS:
        assert any(mcp in line for line in schema_lines), mcp


def test_the_present_flag_is_still_accepted() -> None:
    cmd = _command(_root(), "research", "get")
    present = [p for p in cmd.params if p.name == "present"]
    assert present and present[0].hidden


@pytest.mark.parametrize("path", [("research", "plan"), ("research", "run")])
def test_axes_help_names_the_cli_read(path) -> None:
    cmd = _command(_root(), *path)
    axes = next(p for p in cmd.params if p.name == "axes")
    assert "`keel research get --subject '…'`" in (axes.help or "")
