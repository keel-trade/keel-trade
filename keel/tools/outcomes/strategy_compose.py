"""`keel_strategy_compose` — create, update, validate, or compile a strategy.

Replaces: `strategy_new`, `strategy_validate`, `strategy_compile`,
`strategy_explain`, `strategy_create`, `strategy_update`, `update_strategy`,
`strategy_push`, `pipeline_stage`, and the write portion of the lock tools.

Modes:
    `dry_run=true`  → validate + compile only (local + remote compile,
                      no persistence).
    `dry_run=false` → if `strategy_id` provided: PATCH update; else: POST
                      create.

Do NOT use to fork an existing strategy — call `keel_strategy_fork`.
Do NOT use to run a backtest — call `keel_backtest_run`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from keel.errors import KeelError, ValidationError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


def _read_source(args: dict) -> str:
    source = args.get("source")
    source_file = args.get("source_file")
    if source and source_file:
        raise KeelError(
            "Pass exactly one of `source` or `source_file`, not both.",
            error_code="conflicting_inputs",
            exit_code=2,
            suggestion=(
                "Drop whichever you don't need. `source` is inline DSL text; "
                "`source_file` is a path to a .py file containing the DSL."
            ),
        )
    if source:
        return str(source)
    if source_file:
        p = Path(str(source_file))
        if not p.exists():
            raise KeelError(
                f"source_file not found: {source_file}",
                error_code="not_found",
                exit_code=3,
                suggestion=(
                    "Verify the path. Use an absolute path or one relative to "
                    "your cwd. For checked-out strategies, the file is at "
                    "`<workspace>/strategy.py` (find via `keel_strategy_workspaces`)."
                ),
            )
        return p.read_text(encoding="utf-8")
    raise KeelError(
        "Missing required input: pass either `source` (DSL text) or `source_file` (path).",
        error_code="missing_input",
        exit_code=2,
        suggestion=(
            "Pass inline DSL via `source='Strategy(...)'`, OR a file path via "
            "`source_file='./strategy.py'`. Use `keel_components_search` to "
            "discover components for the DSL body first."
        ),
    )


def _try_local_validate(source: str) -> dict[str, Any]:
    """Use libs/keel local validator. Returns
    {ok: bool, warnings: [], errors: []}."""
    try:
        from keel.tools.local import strategy_lock_generate, strategy_validate
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "warnings": [], "errors": [f"validator-unavailable: {e}"]}
    try:
        lock = None
        try:
            lock = strategy_lock_generate(source=source).get("component_lock")
        except Exception:
            pass
        result = strategy_validate(source=source, component_lock=lock) if lock else strategy_validate(source=source)
        ok = bool(result.get("valid", False))
        return {
            "ok": ok,
            "warnings": result.get("warnings", []) or [],
            "errors": result.get("errors", []) or [],
            "lock": lock,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "warnings": [], "errors": [str(e)]}


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    source = _read_source(args)
    strategy_id: str | None = args.get("strategy_id")
    name: str | None = args.get("name")
    dry_run: bool = bool(args.get("dry_run", False)) or ctx.dry_run
    parent_version: str | None = args.get("parent_version")

    # 1. Local validation always runs first.
    validation = _try_local_validate(source)

    # Per the system policy keel-api ships: validation is FEEDBACK, not a
    # gate. The web app editor (JS validator inline) + keel-api strategy
    # POST/PATCH (logs warnings, doesn't block) + chat-api (validate is a
    # separate read-only tool) all surface validation issues to the user
    # without blocking the save. Only parse / compile errors block.
    # Pre-v0.4.x our MCP wrapper was the outlier — it raised on validation
    # errors. Now we match the rest of the system. See
    # `projects/agent-v2/06-prod-readiness-followups.md` for the deeper
    # rationale (Python vs JS validator divergence on per-component
    # input_type literals — fix is to treat the strict literal check as
    # advisory, matching what runtime + JS already accept).

    if dry_run:
        # Optionally hit the server compile endpoint for richer errors.
        # We still try compile even if local validation flagged issues —
        # compile is the source-of-truth gate.
        compiled: dict[str, Any] | None = None
        compile_error: str | None = None
        try:
            from keel.tools.remote import strategy_compile

            compiled = strategy_compile(source=source, component_lock=validation.get("lock"))
        except KeelError as e:
            compile_error = str(e)
        except Exception as e:  # noqa: BLE001
            compile_error = str(e)

        body: dict[str, Any] = {
            "validation": {
                "ok": validation["ok"] and not validation["errors"],
                "errors": validation["errors"],
                "warnings": validation["warnings"],
            },
            "compiled": bool(compiled),
            "compile_error": compile_error,
            "dry_run": True,
        }
        return OutcomeResult(
            run_id=strategy_id,
            hero_url=f"{ctx.app_url}/strategies/{strategy_id}" if strategy_id else f"{ctx.app_url}/strategies",
            share_url=None,
            extra=body,
        )

    # 2. Real persist path. Validation issues surface in the response
    # under `validation.errors` / `validation.warnings` but don't block —
    # matches keel-api `_validate_compile_graph` policy (log + proceed).
    # The actual gate is the API call (which runs its own validate +
    # compile + raises on compile failure).

    if not strategy_id and not name:
        # POST /v1/strategies requires `name` (min_length=1). Surface a
        # clean error instead of letting keel-api 422.
        raise ValidationError(
            "Creating a new strategy requires `--name`.",
            suggestion="Re-run with --name <slug>, or pass --strategy-id <id> to update an existing strategy.",
            input={"missing": "name"},
        )

    client = ctx.get_client()
    payload: dict[str, Any] = {"source": source}
    if name:
        payload["name"] = name
    if validation.get("lock"):
        payload["component_lock"] = validation["lock"]
    if parent_version:
        payload["parent_version"] = parent_version

    if strategy_id:
        # Update existing
        try:
            result = client.patch(f"/v1/strategies/{strategy_id}", json=payload)
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to update strategy {strategy_id}: {e}",
                suggestion=(
                    "If the strategy is checked out locally, prefer the "
                    "lightweight-git flow: edit the file, then "
                    "`keel_strategy_push -m '<msg>'`. Run `keel_doctor` if "
                    "the API itself seems unhealthy."
                ),
            )
        sid = result.get("strategy_id") or strategy_id
    else:
        try:
            result = client.post("/v1/strategies", json=payload)
        except KeelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeelError(
                f"Failed to create strategy: {e}",
                suggestion=(
                    "Re-validate the source locally first via "
                    "`keel_strategy_compose dry_run=True`. If validation "
                    "passes but create fails, run `keel_doctor`."
                ),
            )
        sid = result.get("strategy_id") or result.get("id")

    # Surface validation feedback in the response even on successful
    # persist — matches the web app editor pattern where warnings stay
    # visible after save so the agent (and user) can act on them.
    body = {
        "strategy_id": sid,
        "version": result.get("current_sequence") or result.get("version"),
        "validation": {
            "ok": validation["ok"] and not validation["errors"],
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        },
    }
    return OutcomeResult(
        run_id=sid,
        hero_url=f"{ctx.app_url}/strategies/{sid}" if sid else f"{ctx.app_url}/strategies",
        share_url=None,
        extra=body,
    )


STRATEGY_COMPOSE = register(
    OutcomeTool(
        name="keel_strategy_compose",
        required_action="strategy.create",
        cli_path=("strategy", "compose"),
        toolset="backtest",
        description=(
            "Create or update a strategy from DSL source. With `dry_run=true`, "
            "validates + tries to compile without persisting — use this first "
            "to iterate cheaply. With `strategy_id` set, updates an existing "
            "strategy; otherwise creates a new one. Pass exactly one of "
            "`source` (DSL text) or `source_file` (path). "
            "\n\n"
            "FIRST-TIME COMPOSING in this session? Invoke the `strategy-creation` "
            "MCP prompt FIRST (see `prompts/list`). It auto-loads the full "
            "decompose → discover → reason → draft workflow plus ~7 knowledge "
            "files (reasoning_principles, composition_mechanics, dsl_syntax, "
            "mistakes, tool_usage, universe_selection, pipeline_system) — the "
            "same knowledge chat-api keeps always-on. Without it you're "
            "composing blind and will likely hit common mistakes the skill "
            "catalogs. For modifying an existing strategy, invoke "
            "`strategy-fork-and-iterate` instead. "
            "\n\n"
            "Validation feedback (errors + warnings + type-flow) always "
            "surfaces in the response under `validation.errors` and "
            "`validation.warnings`. Validation does NOT block the save — "
            "matches the web app editor + chat-api policy where the user "
            "sees issues inline but compile is the actual gate. Only parse "
            "and compile errors block. "
            "DSL constraints: NO Python `import` statements (component names "
            "like `ROC`, `PriceDataLoader`, `ForecastScaler` are pre-resolved "
            "— use them directly). The pipeline must end with a normalizer "
            "(`ForecastWeightNormalizer` or equivalent). Call `keel_help"
            "(topic='dsl_syntax')` for the full DSL reference, or "
            "`keel_components_search` to discover available components. "
            "Do NOT use to fork an existing strategy — call `keel_strategy_fork`. "
            "Do NOT use to run a backtest — call `keel_backtest_run`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "DSL source (raw text). Exactly one of `source` or `source_file` required.",
                },
                "source_file": {
                    "type": "string",
                    "description": "Path to a .py DSL file. Exactly one of `source` or `source_file` required.",
                },
                "strategy_id": {
                    "type": "string",
                    "description": "If set, updates the named strategy; otherwise creates a new one.",
                },
                "name": {
                    "type": "string",
                    "description": "Name for the new strategy (create mode).",
                },
                "dry_run": {
                    "type": "boolean",
                    "default": False,
                    "description": "Only validate + compile, do not persist.",
                },
                "parent_version": {
                    "type": "string",
                    "description": "Optional commit/version ref this update is based on.",
                },
            },
            "required": [],
        },
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        handler=_handler,
    )
)
