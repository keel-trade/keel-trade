"""Remote tool implementations — calls to keel-api endpoints.

These tools require a KEEL_API_KEY and make HTTP requests to the
live API for operations that need component classes or platform state.
"""

from __future__ import annotations

from typing import Any


def _client():
    from keel.client import KeelClient

    return KeelClient()


def strategy_compile(source: str, component_lock: dict[str, int] | None = None) -> dict[str, Any]:
    """Compile a strategy via the remote API."""
    body: dict[str, Any] = {"source": source}
    if component_lock:
        body["component_lock"] = component_lock
    client = _client()
    try:
        return client.post("/v1/strategies/compile", json=body)
    finally:
        client.close()


def update_strategy(source: str, name: str | None = None) -> dict[str, Any]:
    """Validate + lock + graph via the remote API."""
    body: dict[str, Any] = {"source": source}
    if name:
        body["name"] = name
    client = _client()
    try:
        return client.post("/v1/strategies/update-source", json=body)
    finally:
        client.close()


def strategy_lock_generate_remote(source: str) -> dict[str, Any]:
    """Generate a component lock via the remote API (live versions)."""
    client = _client()
    try:
        return client.post("/v1/strategies/lock/generate", json={"source": source})
    finally:
        client.close()


def strategy_lock_status_remote(
    source: str, component_lock: dict[str, int] | None = None
) -> dict[str, Any]:
    """Check lock status via the remote API."""
    body: dict[str, Any] = {"source": source}
    if component_lock:
        body["component_lock"] = component_lock
    client = _client()
    try:
        return client.post("/v1/strategies/lock/check", json=body)
    finally:
        client.close()


def strategy_lock_upgrade_remote(
    source: str,
    component_lock: dict[str, int] | None = None,
    components: list[str] | None = None,
) -> dict[str, Any]:
    """Upgrade lock via the remote API."""
    body: dict[str, Any] = {"source": source}
    if component_lock:
        body["component_lock"] = component_lock
    if components:
        body["components"] = components
    client = _client()
    try:
        return client.post("/v1/strategies/lock/upgrade", json=body)
    finally:
        client.close()


# ─────────────────────────────────────────────────────────────────────────────
# New "components" surface (2026-06-29). The HTTP endpoints stay the same
# (server-side backward compat); these are SDK-side aliases so user code
# uses the cleaner name.
# ─────────────────────────────────────────────────────────────────────────────
strategy_components_drift_remote = strategy_lock_status_remote
strategy_components_upgrade_remote = strategy_lock_upgrade_remote
