"""MCP-FINAL batch 3 #6 (FINAL-LIST §D, one runtime switch): an existing tool's Research-only parameters publish only
while the caller is served Research. keel_share_create's ``views`` ("For a study (stu_*) …") and ``show_author`` were
on its schema with Research off, so the MCP schema and ``keel share create --help`` talked about studies on a surface
that has none. Off, the schema is the pre-Research one (gate E's mcp leg, core_flow_arm.py --flag off, checks it byte
for byte against e720b11557); served, the parameters join it.

SEED (run 2026-11-20, reversed): ``without_research_properties`` returns ``schema`` unchanged -> the three off arms red
(MCP, CLI, the hosted per-caller swap); the served arms (control) stay green."""

from __future__ import annotations

import json

import click
from click.testing import CliRunner
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._cli_adapter import _cli_input_schema
from keel.tools.outcomes._cli_adapter import register_all as cli_register_all
from keel.tools.outcomes._mcp_adapter import (
    RESEARCH_ONLY_PROPERTIES,
    effective_input_schema,
    input_schema_for,
)
from keel.tools.outcomes._toolsets import research_served_context


_bootstrap()

_TOOL = OUTCOMES["keel_share_create"]
_GONE = RESEARCH_ONLY_PROPERTIES["keel_share_create"]


def _mentions_studies(schema: dict) -> bool:
    text = json.dumps(schema).lower()
    return "stu_" in text or "study" in text or "studies" in text


def test_research_only_parameters_exist_served() -> None:
    """Control and non-vacuity: served, the schema carries every Research-only parameter, and they talk about studies."""
    with research_served_context(True):
        schema = effective_input_schema(_TOOL)
    assert set(_GONE) <= set(schema["properties"])
    assert _mentions_studies(schema)
    assert set(_GONE) <= set(input_schema_for(_TOOL, research_served=True)["properties"])


def test_off_the_mcp_schema_has_no_research_parameter() -> None:
    with research_served_context(False):
        schema = effective_input_schema(_TOOL)
    assert not set(_GONE) & set(schema["properties"]), schema["properties"].keys()
    assert not set(_GONE) & set(schema.get("required") or [])
    assert not _mentions_studies(schema)
    assert "target_id" in schema["properties"]  # the rest of the schema is kept


def test_off_the_hosted_per_caller_schema_has_no_research_parameter() -> None:
    """The hosted server's one swap function, called outside any binding (as its tools/list does)."""
    schema = input_schema_for(_TOOL, research_served=False)
    assert not set(_GONE) & set(schema["properties"])
    assert not _mentions_studies(schema)


def test_off_the_cli_has_no_research_flag() -> None:
    with research_served_context(False):
        assert not set(_GONE) & set(_cli_input_schema(_TOOL)["properties"])
        root = click.Group("keel")
        cli_register_all(root, OUTCOMES)
        out = CliRunner().invoke(root, ["share", "create", "--help"])
    assert out.exit_code == 0, out.output
    assert "--views" not in out.output and "--show-author" not in out.output
    assert (
        "stu_" not in out.output and "TARGET_ID" in out.output and "--permission" in out.output
    )  # the rest is kept
