"""Best-effort ownership projection helpers for CLI and MCP surfaces."""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from ._base import ToolContext


def fetch_ownership_projection(
    ctx: ToolContext,
    strategy_id: str,
) -> dict[str, Any] | None:
    """Fetch the newest ownership projection for a strategy, if available."""
    client = ctx.get_client()
    try:
        sessions = client.get("/v1/strategy-work-sessions", strategy_id=strategy_id)
        items = sessions.get("items") if isinstance(sessions, dict) else None
        if not items:
            return None
        session_id = items[0].get("session_id")
        if not session_id:
            return None
        projection = client.get(f"/v1/strategy-work-sessions/{session_id}/ownership")
        if isinstance(projection, dict):
            projection.setdefault("resource_uri", f"keel://ownership/strategy/{strategy_id}")
            return projection
    except KeelError:
        return None
    except Exception:
        return None
    return None


def ownership_envelope_fields(projection: dict[str, Any] | None) -> dict[str, Any]:
    """Return Spec 02 ownership hint fields for an outcome envelope."""
    if not projection:
        return {}
    return {
        "ownership_resource_uri": projection.get("resource_uri"),
        "ownership_status": projection.get("overall_status"),
        "next_recommended_action": projection.get("next_recommended_action"),
        "missing_evidence": projection.get("missing_evidence") or [],
        "live_readiness_blockers": projection.get("live_readiness_blockers") or [],
    }


__all__ = ["fetch_ownership_projection", "ownership_envelope_fields"]
