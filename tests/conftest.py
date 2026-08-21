"""Test-suite-wide fixtures.

Why this file exists: the SDK's config + token-store layer writes to
``~/.keel/config.yaml`` (real user state). Without isolation, ANY test
that constructs a `KeelClient` with a near-expiry refresh token will
trigger the proactive-refresh path → `store_oauth_tokens()` → blow
away the user's real credentials. That actually happened on 2026-05-22
during the v0.4.x staging smoke. The autouse fixture below redirects
config writes to tmp paths for every test, no opt-in needed.

It is ALSO the suite's network airlock. See `_block_outbound_network`:
nothing here may talk to a real host, because "a real host" in practice
means PRODUCTION ``api.usekeel.io``.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import httpx
import pytest


# ─────────────────────────────────────────────────────────────────────────
# Network airlock
# ─────────────────────────────────────────────────────────────────────────
#
# The SDK's default `api_url` is PRODUCTION (`https://api.usekeel.io`).
# Several handlers reach for the API opportunistically and swallow the
# failure (`keel/tools/outcomes/components_help.py::_detail_via_api` is
# the canonical offender: `except Exception: return None`), so a test
# that touches the network looks green while actually POSTing to prod.
#
# It did exactly that. `KeelClient._require_auth` mints an anonymous org
# via `POST /v1/auth/anonymous` when no credentials exist — and the
# autouse config isolation below guarantees no credentials exist. Between
# 2026-07-26 and 2026-08-01 this suite created 16 real `plan='anon'` orgs
# in the production `platform.orgs` table, in bursts that line up 1:1
# with `sdk-test` CI runs and local full-suite runs. It also caused a
# real flake: the anonymous notice printed to the CLI's stderr, and
# `CliRunner.Result.output` (Click ≥8.2) interleaves stderr into stdout,
# so `json.loads(result.output)` blew up with "Extra data" — but only
# when the grant happened to succeed.
#
# So: the suite may not reach a non-local host. Ever. Two layers, because
# one is not enough:
#
#   1. `httpx.HTTPTransport.handle_request` / the async twin — a
#      pass-through that RECORDS the in-flight method + URL. It does not
#      itself block, because respx (the suite's mocking library) patches
#      *below* it at the httpcore layer; blocking here would break every
#      respx-mocked test. Its only job is to make the error message name
#      the actual request.
#   2. `socket.socket.connect` / `connect_ex` / `socket.create_connection`
#      / `socket.getaddrinfo` — the layer that actually refuses. It sits
#      strictly below respx, below httpcore, below `requests`, below
#      `urllib`, and below any client the code under test constructs for
#      itself, so there is no bypass. DNS is included: resolving a prod
#      hostname is already egress.
#
# The refusal is a `BaseException`, deliberately. Every interesting call
# site in the SDK wraps its HTTP in `except Exception` or
# `except httpx.HTTPError` and degrades silently; an `Exception` here
# would be swallowed and the test would still pass. `pytest` reports a
# stray `BaseException` as a plain test failure (only `Exit` and
# `KeyboardInterrupt` abort the session), which is precisely the loud
# outcome we want.
#
# Opt out for a test that genuinely needs egress with
# `@pytest.mark.allow_network`. As of this writing NOTHING carries it.

_LOCAL_HOSTNAMES = frozenset(
    {
        "",
        "0.0.0.0",  # noqa: S104 — matching, not binding
        "::",
        "::1",
        "localhost",
        "localhost.localdomain",
        "testserver",  # httpx/starlette ASGI transport default
    }
)


class OutboundNetworkBlocked(BaseException):
    """A test tried to reach a non-local host.

    Intentionally a `BaseException`: the SDK swallows `Exception` around
    nearly every HTTP call, so an ordinary error here would be absorbed
    and the offending test would still pass.
    """


class _NetGuard:
    """Session-wide state for the airlock."""

    def __init__(self) -> None:
        self.enabled = True
        self.node_id = "<session>"
        #: (node_id, description) for every refused attempt — the
        #: "prove no egress" evidence trail.
        self.blocked: list[tuple[str, str]] = []
        #: In-flight httpx requests, innermost last.
        self.http_context: list[str] = []

    def refuse(self, target: str) -> "OutboundNetworkBlocked":
        context = self.http_context[-1] if self.http_context else None
        self.blocked.append((self.node_id, context or target))
        detail = f"{context} (connecting to {target})" if context else target
        return OutboundNetworkBlocked(
            "\n"
            "╭─ OUTBOUND NETWORK BLOCKED ────────────────────────────────\n"
            f"│ test:    {self.node_id}\n"
            f"│ request: {detail}\n"
            "│\n"
            "│ This suite must NEVER touch a real API. The SDK's default\n"
            "│ api_url is PRODUCTION, and an unauthenticated call mints a\n"
            "│ real anonymous org in prod's platform.orgs.\n"
            "│\n"
            "│ Fix the test, not the guard: mock the call with respx\n"
            "│   with respx.mock(base_url=...) as m:\n"
            "│       m.post('/v1/...').mock(return_value=Response(200, json={...}))\n"
            "│ or inject a fake client (`ToolContext(api_client=...)`).\n"
            "│\n"
            "│ If a test provably needs real egress, mark it\n"
            "│ `@pytest.mark.allow_network` — see tests/conftest.py.\n"
            "╰───────────────────────────────────────────────────────────"
        )


_GUARD = _NetGuard()


def _is_local_host(host: object) -> bool:
    """True for loopback / unspecified / ASGI-sentinel hosts.

    `None` counts as local: that's the wildcard form used when binding a
    listener (`getaddrinfo(None, 0)`), not an outbound destination.
    """
    if host is None:
        return True
    if not isinstance(host, (str, bytes)):
        return False
    text = host.decode() if isinstance(host, bytes) else host
    text = text.strip("[]").lower()
    if text in _LOCAL_HOSTNAMES:
        return True
    return text.startswith("127.")


def _check_host(host: object, target: str) -> None:
    if not _GUARD.enabled or _is_local_host(host):
        return
    raise _GUARD.refuse(target)


def _check_address(address: object) -> None:
    """Guard a socket address. Non-tuple addresses are AF_UNIX — local."""
    if not _GUARD.enabled or not isinstance(address, tuple) or not address:
        return
    _check_host(address[0], f"{address[0]}:{address[1] if len(address) > 1 else '?'}")


def _install_network_guard() -> list:
    """Patch the transport + socket layers. Returns undo callables."""
    undo: list = []

    # ── Layer 1: httpx transports (context only, never blocks) ──────────
    def _wrap_transport(cls: type, method_name: str, is_async: bool):
        original = getattr(cls, method_name)

        if is_async:

            async def wrapper(self, request, *args, **kwargs):
                _GUARD.http_context.append(f"{request.method} {request.url}")
                try:
                    return await original(self, request, *args, **kwargs)
                finally:
                    _GUARD.http_context.pop()
        else:

            def wrapper(self, request, *args, **kwargs):  # type: ignore[misc]
                _GUARD.http_context.append(f"{request.method} {request.url}")
                try:
                    return original(self, request, *args, **kwargs)
                finally:
                    _GUARD.http_context.pop()

        setattr(cls, method_name, wrapper)
        undo.append(lambda: setattr(cls, method_name, original))

    _wrap_transport(httpx.HTTPTransport, "handle_request", is_async=False)
    _wrap_transport(httpx.AsyncHTTPTransport, "handle_async_request", is_async=True)

    # ── Layer 2: sockets (the layer that actually refuses) ──────────────
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self, address):
        _check_address(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        _check_address(address)
        return real_connect_ex(self, address)

    def guarded_create_connection(address, *args, **kwargs):
        _check_address(address)
        return real_create_connection(address, *args, **kwargs)

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        # DNS for a prod hostname is already egress, and blocking here
        # keeps the error message on the hostname rather than an IP.
        _check_host(host, f"{host}:{port}")
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.create_connection = guarded_create_connection
    socket.getaddrinfo = guarded_getaddrinfo
    undo.extend(
        [
            lambda: setattr(socket.socket, "connect", real_connect),
            lambda: setattr(socket.socket, "connect_ex", real_connect_ex),
            lambda: setattr(socket, "create_connection", real_create_connection),
            lambda: setattr(socket, "getaddrinfo", real_getaddrinfo),
        ]
    )
    return undo


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_network: this test provably needs real outbound egress "
        "(nothing carries it today — see tests/conftest.py).",
    )


@pytest.fixture(scope="session", autouse=True)
def _block_outbound_network():
    """Install the airlock for the whole session."""
    undo = _install_network_guard()
    try:
        yield _GUARD
    finally:
        for fn in reversed(undo):
            fn()


@pytest.fixture(autouse=True)
def _network_guard_scope(request):
    """Name the current test in guard errors; honour `allow_network`."""
    _GUARD.node_id = request.node.nodeid
    _GUARD.enabled = request.node.get_closest_marker("allow_network") is None
    _GUARD.http_context.clear()
    try:
        yield
    finally:
        _GUARD.enabled = True
        _GUARD.node_id = "<session>"


def pytest_terminal_summary(terminalreporter):
    """One line of evidence: how much egress the suite attempted."""
    count = len(_GUARD.blocked)
    if count == 0:
        terminalreporter.write_line("netguard: 0 outbound non-local requests attempted.")
        return
    terminalreporter.write_line(f"netguard: BLOCKED {count} outbound non-local request(s):")
    for node_id, target in _GUARD.blocked:
        terminalreporter.write_line(f"  {node_id} -> {target}")


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """Redirect `~/.keel/config.yaml` to a per-test temp file.

    Test-suite-wide guard: any test that exercises auth, token refresh,
    or the CLI/MCP adapters might end up calling `save_config()`. We
    NEVER want that to land in the real user file.
    """
    fake_config = tmp_path / "config.yaml"
    fake_dir = tmp_path
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    monkeypatch.delenv("KEEL_API_URL", raising=False)
    # Default every test to local execution mode — hosted-mode behavior
    # (keel.hosting) is opted into explicitly by the hosted-mode tests.
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    # Take the product's own CI opt-out for anonymous instant start
    # (`keel/anon.py::anon_auto_enabled` — "CI environments that want a
    # hard auth failure"). Combined with the deleted credentials above,
    # a CLI-surface call that needs auth now refuses LOCALLY in
    # `_require_auth` instead of POSTing `/v1/auth/anonymous` and
    # minting a real prod org. This is defence in depth behind
    # `_block_outbound_network`, at the product's own seam.
    # `tests/test_anon.py` clears this var in its own autouse fixture, so
    # the instant-start contract itself stays fully covered.
    monkeypatch.setenv("KEEL_ANON_AUTO", "0")
    with (
        patch("keel.config.CONFIG_FILE", fake_config),
        patch("keel.config.CONFIG_DIR", fake_dir),
    ):
        yield fake_config


@pytest.fixture(autouse=True)
def _arm_direct_deploy(monkeypatch):
    """Arm the in-terminal direct-deploy escape hatch for the test suite.

    Going live is a web-app handoff on every surface (D7). The legacy
    in-terminal `keel_live_deploy(direct=true)` path is an
    advanced/headless escape hatch gated behind the
    ``KEEL_ALLOW_DIRECT_DEPLOY`` env opt-in (D28, sdk-v0.7.0) so a caller
    that merely guesses the param can never place real orders. Several
    test files exercise that direct path (preview/deploy plumbing, handoff
    walls, render cards), so the suite plays the role of the opted-in
    operator and arms it here.

    This never masks the DEFAULT behavior: the no-``direct`` web-handoff
    tests don't pass ``direct`` and so never reach the gate. The gate
    itself is locked by explicit tests in ``test_outcomes_live.py`` that
    unset the var (disabled → ``direct_deploy_disabled``) and set it
    (enabled → reaches the direct path).
    """
    monkeypatch.setenv("KEEL_ALLOW_DIRECT_DEPLOY", "1")


@pytest.fixture(autouse=True)
def _reset_calling_surface(monkeypatch):
    """Reset ``keel.surface._SURFACE`` before every test.

    ``set_surface()`` mutates a PROCESS-GLOBAL with no restore — by design,
    since the real entrypoints declare the surface once per process. In a test
    session that makes it order-dependent leakage: any ``CliRunner.invoke`` of a
    real subcommand runs the ``cli`` group callback, which calls
    ``set_surface("cli")``, and every later test in the session then sees
    ``current_surface() == "cli"``.

    That is not hypothetical. It reddened ``sdk-test`` on main (run
    30672350596): ``tests/test_cli_auth.py`` sorts before ``tests/test_client.py``,
    leaked ``cli``, and so ``TestAuth`` hit the CLI-only anonymous instant-start
    branch of ``KeelClient._require_auth`` — which POSTed to the PRODUCTION
    ``/v1/auth/anonymous`` and minted a real anonymous org on the runner instead
    of raising. Locally the same call was rate-limited (429), whose AuthError
    happened to match the assertion, so the suite looked green.

    Resetting here makes every test see the documented default (``sdk``) unless
    it declares otherwise; ``test_anon.py`` and ``test_outcomes_feedback.py``
    keep their own explicit per-test surface fixtures.
    """
    import keel.surface

    monkeypatch.setattr(keel.surface, "_SURFACE", None)
