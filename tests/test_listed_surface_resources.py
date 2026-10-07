"""Listed/hosted resource gate — no resource reads the SERVER's filesystem.

`keel://context/user` reads ``~/.keel/context.md`` and
`keel://context/project` reads ``keel.md`` / ``CLAUDE.md`` from the process
cwd. On the user's machine (CLI, stdio MCP, .mcpb) that is the user's
context; on the hosted connector it is the pod's home and working
directory — always absent today, but a server-filesystem read on a shared
connector would serve any such file an image ever shipped to every caller,
and reads as a data-exposure path to a directory reviewer (S-5). So the two
are registered only where ``serves_local_filesystem_context()`` is true.

The sweep below does not trust the registration list alone: it builds the
REAL server in each hosted configuration, reads EVERY registered resource,
every template (instantiated with sample arguments) and every prompt with
``Path.home`` / ``Path.cwd`` / ``os.path.expanduser`` instrumented, and
fails on any call. The control arm runs the same probe in local mode and
must SEE the context resources touch the filesystem — proof the probe can
detect a read, not merely that it never looked.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest


CONTEXT_URIS = {"keel://context/user", "keel://context/project"}

# Hosted configurations the gate covers: the deployed one (listed +
# hosted — services/mcp-server always sets hosted), an unlisted hosted
# endpoint, and the listed profile run outside hosted mode.
HOSTED_CONFIGS = {
    "listed-hosted": {"KEEL_SERVER_PROFILE": "listed", "KEEL_EXECUTION_MODE": "hosted"},
    "full-hosted": {"KEEL_SERVER_PROFILE": "full", "KEEL_EXECUTION_MODE": "hosted"},
    "listed-local": {"KEEL_SERVER_PROFILE": "listed", "KEEL_EXECUTION_MODE": "local"},
}
LOCAL_CONFIG = {"KEEL_SERVER_PROFILE": "full", "KEEL_EXECUTION_MODE": "local"}

_TEMPLATE_ARGS = {
    "name": "ROC",
    "strategy_id": "str_probe",
    "backtest_id": "btr_probe",
    "topic": "phases",
    "section": "operating_core",
}


def _set_env(monkeypatch, env: dict[str, str]) -> None:
    monkeypatch.delenv("KEEL_TOOLSETS", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def _server(monkeypatch, env: dict[str, str]):
    from keel.mcp.server import create_server

    _set_env(monkeypatch, env)
    return create_server()


def _surface(server) -> tuple[set[str], set[str], set[str]]:
    resources = asyncio.run(server.list_resources())
    templates = asyncio.run(server.list_resource_templates())
    prompts = asyncio.run(server.list_prompts())
    return (
        {str(r.uri) for r in resources},
        {t.uri_template for t in templates},
        {p.name for p in prompts},
    )


def _instantiate(template: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: _TEMPLATE_ARGS[m.group(1)], template)


def _probe_reads(monkeypatch, server) -> tuple[dict[str, list[str]], int, dict[str, str]]:
    """Read every resource, template and prompt with filesystem-root
    lookups instrumented. Returns ({uri_or_prompt: [calls]}, reads_done,
    {target: exception_name} for reads that raised).

    The Keel API is stubbed (an empty payload) so API-backed resources run
    to completion offline; a hosted read binds a caller's credentials the
    way services/mcp-server does per request.
    """
    from keel.hosting import bind_request_credentials, clear_request_credentials

    uris, templates, prompts = _surface(server)
    calls: list[str] = []
    real_home, real_cwd, real_expand = Path.home, Path.cwd, os.path.expanduser

    def home(cls=Path):
        calls.append("Path.home")
        return real_home()

    def cwd(cls=Path):
        calls.append("Path.cwd")
        return real_cwd()

    def expanduser(p):
        if str(p).startswith("~"):
            calls.append(f"expanduser({p})")
        return real_expand(p)

    monkeypatch.setattr("keel.client.KeelClient.get", lambda self, *a, **k: {})
    monkeypatch.setattr("keel.client.KeelClient.get_public", lambda self, *a, **k: {})
    monkeypatch.setattr("keel.client.KeelClient.close", lambda self: None)

    findings: dict[str, list[str]] = {}
    # Reads that raised (e.g. a stubbed-empty API payload a resource
    # cannot shape) — kept for the failure message; their FS calls are
    # still recorded above.
    failed: dict[str, str] = {}
    reads = 0
    token = bind_request_credentials(token="probe-token", api_url="https://api.example")
    try:
        with monkeypatch.context() as m:
            m.setattr(Path, "home", classmethod(home))
            m.setattr(Path, "cwd", classmethod(cwd))
            m.setattr(os.path, "expanduser", expanduser)
            targets = [("resource", u) for u in sorted(uris)]
            targets += [("resource", _instantiate(t)) for t in sorted(templates)]
            targets += [("prompt", p) for p in sorted(prompts)]
            for kind, target in targets:
                calls.clear()
                try:
                    if kind == "resource":
                        asyncio.run(server.read_resource(target))
                    else:
                        asyncio.run(server.render_prompt(target))
                    reads += 1
                except Exception as exc:  # noqa: BLE001 — a failed read still reports its FS calls
                    failed[target] = type(exc).__name__
                if calls:
                    findings[target] = list(calls)
    finally:
        clear_request_credentials(token)
    return findings, reads, failed


@pytest.mark.parametrize("config", sorted(HOSTED_CONFIGS))
def test_hosted_server_does_not_register_local_filesystem_context(monkeypatch, config):
    uris, templates, _ = _surface(_server(monkeypatch, HOSTED_CONFIGS[config]))
    leaked = CONTEXT_URIS & (uris | templates)
    assert not leaked, f"{config}: local-filesystem context resources registered: {leaked}"
    # Non-vacuity: the list is not simply empty — the catalog is served,
    # and per-strategy context (read through the API) stays.
    assert uris, f"{config}: no resources registered at all"
    assert "keel://components/catalog" in uris
    assert "keel://context/strategy/{strategy_id}" in templates


def test_control_local_server_registers_local_filesystem_context(monkeypatch):
    uris, templates, _ = _surface(_server(monkeypatch, LOCAL_CONFIG))
    assert CONTEXT_URIS <= uris
    assert "keel://context/strategy/{strategy_id}" in templates


@pytest.mark.parametrize("config", sorted(HOSTED_CONFIGS))
def test_no_hosted_resource_or_prompt_reads_the_server_filesystem(monkeypatch, config):
    server = _server(monkeypatch, HOSTED_CONFIGS[config])
    findings, reads, failed = _probe_reads(monkeypatch, server)
    assert not findings, f"{config}: reads touched the server filesystem: {findings}"
    # Non-vacuity: the sweep actually read things (resources + templates +
    # prompts), rather than failing every read before any code ran.
    assert reads >= 10, f"{config}: only {reads} successful reads — vacuous sweep; failed: {failed}"


def test_control_probe_detects_the_local_context_reads(monkeypatch):
    """The probe must SEE a filesystem read when one happens — otherwise
    the hosted sweep's silence proves nothing."""
    server = _server(monkeypatch, LOCAL_CONFIG)
    findings, reads, _ = _probe_reads(monkeypatch, server)
    assert CONTEXT_URIS <= set(findings), findings
    assert reads >= 10
