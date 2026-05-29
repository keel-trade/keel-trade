"""`keel_doctor` — diagnose auth/api/cache/registry/mcp issues.

Per spec §13.3: read-only, idempotent. Returns a structured snapshot
the agent can use to decide its next step when something's wrong.

Exit code: 0 when every check passes, 1 when any check fails — so
scripts can `keel doctor && deploy` reliably.
"""

from __future__ import annotations

from typing import Any

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    from keel.config import load_config

    config = load_config()

    checks: list[dict[str, Any]] = []

    # 1. Auth
    if config.api_key:
        try:
            from keel.auth import get_identity

            me = get_identity()
            # /v1/me returns `{principal: {id, type, ...}, org: {id, name,
            # plan, ...}, ...}`. The doctor used to read `me.get("org_id")`
            # (flat) which has been None since the principal/org split —
            # surface the real values now.
            org = me.get("org") or {}
            principal = me.get("principal") or {}
            checks.append({
                "name": "auth",
                "ok": True,
                "detail": {
                    "principal_id": principal.get("id"),
                    "org_id": org.get("id"),
                    "org_name": org.get("name"),
                    "plan": org.get("plan"),
                },
            })
        except Exception as e:  # noqa: BLE001
            checks.append(
                {
                    "name": "auth",
                    "ok": False,
                    "detail": f"Failed identity probe: {e}",
                    "suggestion": "Re-run `keel auth login` or set KEEL_API_KEY.",
                }
            )
    else:
        checks.append(
            {
                "name": "auth",
                "ok": False,
                "detail": "No API key configured.",
                "suggestion": "Run `keel auth login` or set KEEL_API_KEY in the environment.",
            }
        )

    # 2. API reachability
    try:
        client = ctx.get_client() if config.api_key else None
        if client is None:
            checks.append(
                {
                    "name": "api",
                    "ok": False,
                    "detail": "Skipped — no auth available.",
                }
            )
        else:
            # Cheap GET to verify connectivity + token freshness
            client.get("/v1/me")
            checks.append({"name": "api", "ok": True, "detail": config.api_url})
    except Exception as e:  # noqa: BLE001
        checks.append(
            {
                "name": "api",
                "ok": False,
                "detail": f"Could not reach {config.api_url}: {e}",
                "suggestion": "Check network and API key validity.",
            }
        )

    # 3. Toolset surface
    from . import all_tools
    from ._toolsets import load_toolsets

    active = load_toolsets()
    checks.append(
        {
            "name": "toolsets",
            "ok": True,
            "detail": {
                "active": sorted(active),
                "tool_count": sum(
                    1
                    for t in all_tools()
                    if t.toolset == "always" or t.toolset in active
                ),
            },
        }
    )

    all_ok = all(c["ok"] for c in checks)
    if not all_ok:
        # Surface a non-zero exit so CI / `keel doctor && deploy`
        # scripts gate on the result. The KeelError carries the same
        # structured `checks` payload as the success path would.
        failed = [c["name"] for c in checks if not c["ok"]]
        raise KeelError(
            f"Diagnostics failed: {', '.join(failed)}",
            error_code="diagnostics_failed",
            exit_code=1,
            suggestion="See the `checks` field in the output below for per-check details.",
            input={"checks": checks},
        )

    return OutcomeResult(
        run_id=None,
        hero_url=None,
        share_url=None,
        extra={"checks": checks, "all_ok": True},
    )


DOCTOR = register(
    OutcomeTool(
        name="keel_doctor",
        required_action="audit.read",
        cli_path=("doctor",),
        toolset="always",
        description=(
            "Diagnose the Keel CLI/MCP installation: auth, API reachability, "
            "and active toolsets. Call this when a tool returns an unexpected "
            "error or when wiring up for the first time. "
            "Do NOT use to enumerate strategies or accounts — call `keel_strategy_search` "
            "or `keel_accounts_list` instead."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
