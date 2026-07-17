"""Hosted execution mode — per-request credential binding (spec 01 R1).

The contract under test:

* ``local`` mode (default): nothing changes — config file / env creds.
* ``hosted`` mode: the ONLY credential source is the per-request binding
  (`keel.hosting.bind_request_credentials`). Ambient sources
  (``KEEL_API_KEY``, ``~/.keel/config.yaml``) are NEVER consulted, and a
  hosted request with no binding fails with an instructive auth error —
  never a silent fallback (`.claude/rules/lessons.md`).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from keel.config import load_config
from keel.hosting import (
    HostedAuthError,
    bind_request_credentials,
    clear_request_credentials,
    current_request_credentials,
    execution_mode,
    is_hosted,
)


@pytest.fixture(autouse=True)
def _clean_binding():
    """Never leak a request binding across tests."""
    clear_request_credentials()
    yield
    clear_request_credentials()


# ---------------------------------------------------------------------------
# execution_mode()
# ---------------------------------------------------------------------------


def test_default_mode_is_local(monkeypatch):
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    assert execution_mode() == "local"
    assert not is_hosted()


def test_hosted_mode_env(monkeypatch):
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    assert execution_mode() == "hosted"
    assert is_hosted()


def test_invalid_mode_raises_never_downgrades(monkeypatch):
    """A typo'd mode must never silently mean 'local' — that would
    re-enable ambient credentials on a hosted deployment."""
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "production")
    with pytest.raises(ValueError, match="KEEL_EXECUTION_MODE"):
        execution_mode()


# ---------------------------------------------------------------------------
# load_config() credential resolution
# ---------------------------------------------------------------------------


def test_local_mode_ignores_binding_absence(monkeypatch):
    monkeypatch.setenv("KEEL_API_KEY", "local-user-token")
    config = load_config()
    assert config.api_key == "local-user-token"


def test_binding_wins_in_any_mode(monkeypatch):
    """A bound request credential takes precedence even over env vars."""
    monkeypatch.setenv("KEEL_API_KEY", "ambient-env-token")
    bind_request_credentials(token="caller-token", api_url="https://staging-api.test")
    config = load_config()
    assert config.api_key == "caller-token"
    assert config.api_url == "https://staging-api.test"
    # The bound config never carries refresh state — refresh belongs to
    # the caller's client, not the hosting pod.
    assert config.refresh_token is None


def test_hosted_without_binding_raises_instructive_error(monkeypatch):
    """THE no-ambient-fallback guarantee: hosted mode + pod-ambient
    KEEL_API_KEY + no request binding → instructive auth error. The pod
    credential must never be used (the M0 baseline defect: a valid
    caller Bearer was dropped and the pod-ambient client answered)."""
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("KEEL_API_KEY", "pod-ambient-SECRET")
    with pytest.raises(HostedAuthError) as exc_info:
        load_config()
    envelope = exc_info.value.to_envelope()
    assert envelope["code"] == "auth_failed"
    assert "Re-authenticate" in envelope["what_was_expected"]
    # keel_auth_login is local-only and NOT registered hosted-side —
    # the recovery must point at the client's own OAuth flow instead.
    assert envelope["suggested_next_action"]["tool"] is None


def test_hosted_with_binding_returns_caller_config(monkeypatch):
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    reset = bind_request_credentials(token="caller-abc", api_url="https://staging-api.test")
    config = load_config()
    assert config.api_key == "caller-abc"
    assert config.api_url == "https://staging-api.test"
    clear_request_credentials(reset)
    assert current_request_credentials() is None
    with pytest.raises(HostedAuthError):
        load_config()


def test_binding_is_context_isolated():
    """Two concurrent contexts binding different tokens each see only
    their own — the property the hosted server's per-request identity
    rests on."""

    async def caller(token: str, gate: asyncio.Event) -> str:
        bind_request_credentials(token=token, api_url="https://staging-api.test")
        await gate.wait()  # force both bindings to coexist
        return load_config().api_key

    async def scenario():
        gate = asyncio.Event()
        t_alice = asyncio.create_task(caller("tok-alice", gate))
        t_bob = asyncio.create_task(caller("tok-bob", gate))
        await asyncio.sleep(0.01)
        gate.set()
        return await asyncio.gather(t_alice, t_bob)

    alice_key, bob_key = asyncio.run(scenario())
    assert alice_key == "tok-alice"
    assert bob_key == "tok-bob"


def test_repr_never_leaks_token():
    bind_request_credentials(token="super-secret-token", api_url="https://x.test")
    assert "super-secret-token" not in repr(current_request_credentials())


# ---------------------------------------------------------------------------
# MCP adapter — per-request KeelClient injection
# ---------------------------------------------------------------------------


def _dummy_tool(handler):
    from keel.tools.outcomes._base import OutcomeTool

    return OutcomeTool(
        name="keel_dummy_probe",
        cli_path=("dummy", "probe"),
        toolset="read-only",
        description="probe",
        input_schema={"type": "object", "properties": {}, "required": []},
        annotations={"title": "Probe", "readOnlyHint": True, "destructiveHint": False},
        handler=handler,
        required_action="audit.read",
    )


def test_adapter_injects_per_request_client(monkeypatch):
    """The handler's ToolContext carries a KeelClient with the CALLER's
    token + api_url, and the adapter closes it after the call."""
    from keel.tools.outcomes._base import OutcomeResult
    from keel.tools.outcomes._mcp_adapter import _make_handler

    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("KEEL_API_KEY", "pod-ambient-SECRET")
    seen: dict = {}

    def handler(args, ctx):
        client = ctx.get_client()
        seen["api_key"] = client._config.api_key
        seen["api_url"] = client._config.api_url
        seen["client"] = client
        return OutcomeResult(extra={"ok": True})

    fn = _make_handler(_dummy_tool(handler), frozenset({"read-only"}))
    bind_request_credentials(token="caller-tok-123", api_url="https://staging-api.test")
    payload = json.loads(fn())

    assert payload["ok"] is True
    assert seen["api_key"] == "caller-tok-123", "client must carry the caller token"
    assert seen["api_url"] == "https://staging-api.test"
    assert seen["client"]._client.is_closed, "per-request client must be closed after the call"


def test_adapter_hosted_unbound_returns_auth_envelope_without_running_handler(monkeypatch):
    """Hosted + no binding: the handler must NOT run (no ambient client
    could be correct) and the caller gets the instructive envelope."""
    from keel.tools.outcomes._mcp_adapter import _make_handler

    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    monkeypatch.setenv("KEEL_API_KEY", "pod-ambient-SECRET")
    ran = []

    def handler(args, ctx):  # pragma: no cover — must not execute
        ran.append(True)
        raise AssertionError("handler must not run without caller credentials")

    fn = _make_handler(_dummy_tool(handler), frozenset({"read-only"}))
    payload = json.loads(fn())

    assert not ran
    assert payload["code"] == "auth_failed"
    assert "Re-authenticate" in payload["what_was_expected"]
    assert payload["suggested_next_action"]["tool"] is None
    assert "pod-ambient-SECRET" not in json.dumps(payload)


def test_adapter_local_mode_unchanged(monkeypatch):
    """Local mode with no binding: ToolContext gets no injected client
    and the lazy ambient path stays exactly as before."""
    from keel.tools.outcomes._base import OutcomeResult
    from keel.tools.outcomes._mcp_adapter import _make_handler

    monkeypatch.setenv("KEEL_API_KEY", "local-user-token")
    seen: dict = {}

    def handler(args, ctx):
        seen["injected"] = ctx.api_client
        seen["lazy_key"] = ctx.get_client()._config.api_key
        return OutcomeResult(extra={"ok": True})

    fn = _make_handler(_dummy_tool(handler), frozenset({"read-only"}))
    payload = json.loads(fn())
    assert payload["ok"] is True
    assert seen["injected"] is None
    assert seen["lazy_key"] == "local-user-token"
