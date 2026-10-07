"""`keel_connection_check` — diagnose auth/api/registry/mcp issues.

Renamed from `keel_doctor` on 2026-10-01 (Q-2080): "doctor" is a metaphor
OpenAI's name scan could not read. The old name is a callable alias
(`_toolsets.TOOL_ALIASES`). On a HOSTED server the checks name no internal
URL, no raw exception text and no local remedy (`keel auth login`,
`KEEL_API_KEY`): the caller holds a connector, not a shell.

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
    from keel.hosting import is_hosted

    config = load_config()
    hosted = is_hosted()

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
            detail: dict[str, Any] = {
                "org_name": org.get("name"),
                "plan": org.get("plan"),
            }
            from ._toolsets import is_listed_profile

            if not is_listed_profile():
                # Internal identifiers stay off the listed surface (Q-2268:
                # OpenAI's review asks for no internal account ids in a
                # result); the CLI and local server keep them for support.
                detail = {
                    "principal_id": principal.get("id"),
                    "org_id": org.get("id"),
                    **detail,
                }
            checks.append({"name": "auth", "ok": True, "detail": detail})
        except Exception as e:  # noqa: BLE001
            checks.append(
                {
                    "name": "auth",
                    "ok": False,
                    # Bounded on the hosted server: no raw exception text.
                    "detail": (
                        "The signed-in identity could not be read."
                        if hosted
                        else f"Failed identity probe: {e}"
                    ),
                    "suggestion": (
                        "Reconnect this connector in the client to sign in again."
                        if hosted
                        else "Re-run `keel auth login` or set KEEL_API_KEY."
                    ),
                }
            )
    else:
        checks.append(
            {
                "name": "auth",
                "ok": False,
                "detail": "Not signed in." if hosted else "No API key configured.",
                "suggestion": (
                    "Reconnect this connector in the client to sign in."
                    if hosted
                    else "Run `keel auth login` or set KEEL_API_KEY in the environment."
                ),
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
            # The hosted server's `api_url` is the pod's in-cluster address
            # (Q-1744) — plumbing, never served; the user's address is the app.
            checks.append(
                {
                    "name": "api",
                    "ok": True,
                    "detail": "Keel API reachable" if hosted else config.api_url,
                }
            )
    except Exception as e:  # noqa: BLE001
        checks.append(
            {
                "name": "api",
                "ok": False,
                "detail": (
                    "The Keel API could not be reached from this server."
                    if hosted
                    else f"Could not reach {config.api_url}: {e}"
                ),
                "suggestion": (
                    "Retry shortly; a report for a human is keel_feedback."
                    if hosted
                    else "Check network and API key validity."
                ),
            }
        )

    # 3. Toolset surface
    from . import all_tools
    from ._toolsets import is_tool_loaded, load_toolsets

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
                    if is_tool_loaded(t.toolset, active, local_only=t.local_only, name=t.name)
                ),
            },
        }
    )

    # Cross-surface routing hints (spec 07 R7): the doctor is where
    # agents land when a surface-mismatched ask fails (file ops on the
    # hosted server, chart asks in a terminal) — point across in one
    # line. Included on both the success and failure payloads.
    from ._surface_hints import surface_hints

    hints = surface_hints()

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
            input={"checks": checks, "surface_hints": hints},
        )

    return OutcomeResult(
        run_id=None,
        hero_url=None,
        share_url=None,
        extra={"checks": checks, "all_ok": True, "surface_hints": hints},
    )


DOCTOR = register(
    OutcomeTool(
        name="keel_connection_check",
        required_action="audit.read",
        cli_path=("doctor",),
        toolset="always",
        # grounded-in: system/chat/tool_usage.md:27-29 ("Retrying After a Tool Error" —
        # same error twice with the same root cause → stop and reason, don't
        # slide parameters); doctor.py docstring (spec §13.3 non-zero exit on
        # any failed check so `keel doctor && …` gates cleanly).
        description=(
            "Check the connection to Keel from the Keel CLI/MCP installation when a call "
            "fails for an environmental reason — identity and quota are "
            "`keel_account_status`. One pass over sign-in, API reachability and the tool "
            "surface this server loads, returned as a `checks` list (name, ok, detail) in "
            "JSON text in `result`; a tool's own structured error names its cause "
            "instead. A call failing twice on one root cause is a setup question rather "
            "than a retry, and a report for a human is `keel_feedback`. Exits non-zero "
            "when any check fails, so `keel doctor && …` gates cleanly in scripts."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        annotations={
            "title": "Check Connection",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
        # Listed-profile copy (spec 01 R3): must not route to tools
        # absent from the listed surface (keel_accounts_list).
        listed_description=(
            "Check this connection to Keel when a call fails for an environmental reason — "
            "identity and quota are `keel_account_status`. One pass over sign-in, API "
            "reachability and the tool surface this server loads, returned as a `checks` "
            "list (name, ok, detail). A call failing twice on one "
            "root cause is a setup question, and a report for a human is `keel_feedback`."
        ),
    )
)
