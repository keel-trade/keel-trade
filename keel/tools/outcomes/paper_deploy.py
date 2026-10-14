"""`keel_paper_deploy` — start a simulated paper run of a saved strategy.

paper-trading-v2 S06 §3.2 (IC-06-1). The only deploy verb on the platform
that completes in band (§3.1): one call to keel-api's ``POST /v1/paper``
(S01 IC-01-9) under the caller's normal auth, with no account enumeration,
no preview, no confirmation phase and no local state. Every fill is
simulated; no real order is placed and nothing is armed. Going live stays a
web handoff (``keel_live_deploy``).

The module registers only while the paper SDK is released
(``_toolsets.PAPER_SDK_RELEASED``, S06 §3.11 item 1, PT2-28): it is listed in
``outcomes._PAPER_MODULES`` and imported by ``_register_paper_modules``.

Every runtime line it renders comes from ``_paper_strings`` (§3.4.6), so the
listed connector, once its flag flips, renders the listed forms.

The response (IC-06-1): ``deployment_id``, ``venue`` (the route's block,
verbatim), ``status`` (keel-app's word for it, never the raw ``LIVE``),
``strategy_id``, ``schedule``, ``simulated_capital``,
``url`` (``{app}/paper/{id}``), ``remaining`` (the route's object, relayed only
when the route sends it: below 20% of the plan's paper slots) and
``disclosures`` (IC-01-10's two constants), plus ``run_notes``: the two
disclosure lines and the ladder, as the text a reader sees.
"""

from __future__ import annotations

from typing import Any

from keel.errors import EntitlementError, KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext
from ._paper_status import paper_status_word
from ._paper_strings import (
    CONTROL_RUNS_POINTER,
    DEPLOY_AUTO_PAUSE_DISCLOSURE,
    DEPLOY_FILL_DISCLOSURE,
    DEPLOY_SERVER_ERROR_SUGGESTION,
    LADDER_LINE,
)
from .open_in_app import app_url_for


#: IC-02-2 item 5's simulated-capital presets. The route is the one validator
#: (§3.2.4: it answers 422 to any other value); the declared enum lets every
#: surface refuse one before a request is sent (the declared-choices wrapper,
#: the CLI's Choice), and the handler adds no check of its own.
SIMULATED_CAPITAL_PRESETS: tuple[int, ...] = (1000, 10000, 100000)
DEFAULT_SIMULATED_CAPITAL = 10000

#: IC-01-10's auto-pause constants, disclosed at deploy time (S01 §3.13).
IDLE_AUTO_PAUSE_DAYS = 28
OVER_CAP_GRACE_DAYS = 7

PAPER_CREATE_PATH = "/v1/paper"


def _simulated_capital(args: dict) -> int:
    value = args.get("simulated_capital")
    return DEFAULT_SIMULATED_CAPITAL if value is None else value


def _run_notes(disclosures: dict[str, int]) -> str:
    """The text a reader sees: both disclosure lines, then the ladder."""
    lines = [
        DEPLOY_FILL_DISCLOSURE.for_profile(),
        (DEPLOY_AUTO_PAUSE_DISCLOSURE.for_profile() or "").format(
            idle_days=disclosures["idle_auto_pause_days"],
            grace_days=disclosures["over_cap_grace_days"],
        ),
        LADDER_LINE.for_profile(),
    ]
    return "\n".join(line for line in lines if line)


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    strategy_id = str(args.get("strategy_id") or "").strip()
    if not strategy_id:
        raise KeelError(
            "Missing required `strategy_id` argument.",
            error_code="missing_strategy_id",
            exit_code=2,
            suggestion="Pass strategy_id as the positional argument (from keel_strategy_search).",
        )
    simulated_capital = _simulated_capital(args)
    body: dict[str, Any] = {"strategy_id": strategy_id, "simulated_capital": simulated_capital}
    for optional in ("schedule", "config_id"):
        value = args.get(optional)
        if value not in (None, ""):
            body[optional] = value

    try:
        response = ctx.get_client().post(PAPER_CREATE_PATH, json=body)
    except EntitlementError as e:
        # §3.2.4: the claim arm (a local anonymous session), the permission
        # arm (a credential that may not run backtests) and HEAD's neutral
        # plan-limit wall for a slot denial with limit ≥ 1 (R-59: no
        # action_url, no other plan, no slot-freeing point; stopping a run
        # lives in the description). Anything else re-raises unchanged.
        from ._paper_walls import paper_wall

        handoff = paper_wall(
            e,
            blocked_action="paper_deploy",
            retry_call={"tool": "keel_paper_deploy", "args": dict(args)},
        )
        if handoff is not None:
            raise handoff from e
        raise
    except KeelError as e:
        # A 5xx may have started the run (no idempotency key): the read-back
        # is the run list on this profile, never a blind resend (S10 review F3).
        from ._paper_guard import paper_server_error

        server = paper_server_error(
            e, suggestion=DEPLOY_SERVER_ERROR_SUGGESTION.for_profile() or "", keep_message=True
        )
        if server is not None:
            raise server from e
        raise
    if not isinstance(response, dict) or not response.get("deployment_id"):
        raise KeelError(
            "keel-api answered POST /v1/paper without a deployment_id.",
            error_code="unexpected_response",
            exit_code=1,
            suggestion=f"The run may have started: {CONTROL_RUNS_POINTER.for_profile()}",
        )

    deployment_id = str(response["deployment_id"])
    url = app_url_for("paper", deployment_id, ctx)
    disclosures = {
        "idle_auto_pause_days": IDLE_AUTO_PAUSE_DAYS,
        "over_cap_grace_days": OVER_CAP_GRACE_DAYS,
    }
    extra: dict[str, Any] = {
        "deployment_id": deployment_id,
        "venue": response.get("venue"),
        # keel-app's word for the run's status (Deploying / Paper / Paused /
        # Stopped), never the raw `LIVE` a reader takes for real capital (QA-W2).
        "status": paper_status_word(
            response.get("status"),
            health=response.get("health"),
            pause_reason=response.get("pause_reason"),
        ),
        "strategy_id": response.get("strategy_id") or strategy_id,
        "schedule": response.get("schedule"),
        "simulated_capital": response.get("simulated_capital", simulated_capital),
        "url": url,
    }
    remaining = response.get("remaining")
    if remaining:
        extra["remaining"] = remaining
    extra["disclosures"] = disclosures
    extra["run_notes"] = _run_notes(disclosures)
    return OutcomeResult(run_id=deployment_id, hero_url=url, share_url=None, extra=extra)


PAPER_DEPLOY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["strategy_id"],
    "properties": {
        "strategy_id": {
            "type": "string",
            "x-cli-positional": True,
            "description": "Strategy to paper-trade. From `keel_strategy_search`.",
        },
        "schedule": {
            "type": "string",
            "description": (
                "Cron schedule for the run's evaluations, derived the same way as a live "
                "run's. Omit for the platform default."
            ),
        },
        "simulated_capital": {
            "type": "integer",
            "enum": list(SIMULATED_CAPITAL_PRESETS),
            "default": DEFAULT_SIMULATED_CAPITAL,
            "x-cli-flag": "--capital",
            "description": (
                "Simulated starting capital preset: 1000, 10000, or 100000 (US dollars)."
            ),
        },
        "config_id": {
            "type": "string",
            "description": "Execution configuration to run with; omit for the strategy's default.",
        },
    },
}

#: Full profile (§3.2.5): what it does, autonomy, the web-handoff half and the
#: lifecycle pointer. The slot sentence is the one place an agent learns how a
#: full slot clears; the quota wall states facts only (§3.2.4b, R-59).
PAPER_DEPLOY_DESCRIPTION = (
    "Start a paper-trading run of a saved strategy: fills are simulated at live "
    "Hyperliquid prices under Keel's published fill model, no real orders are placed "
    "and no funds are at risk. It completes here, in band, without a confirmation step, "
    "and needs no exchange account, wallet or live opt-in. Each plan runs a set number "
    "of paper runs at once (free plans one); `keel_plan_usage` shows this plan's paper "
    "slots. Going live is a separate human step in the Keel web app; `keel_live_deploy` "
    "returns that handoff. The response carries the auto-pause disclosures; manage the "
    "run with `keel_paper_control` (stopping a paper run frees its slot) and watch it "
    "with `keel_live_monitor`. On a timeout, read `keel_deployments_list` with "
    "`capital_class=practice` before calling again: a second call starts a second run "
    "and takes (or is refused) another slot."
)

#: Listed profile (§3.2.5, §3.4.4; the control sentence and retry sentence,
#: S10 §4-5, PT2-90): served only while the listed flag is on for the caller's
#: host (§3.4.2, FDR-10). Paper compounds plus "simulated", the live half in the listed
#: register, only listed tool names, declarative.
PAPER_DEPLOY_LISTED_DESCRIPTION = (
    "Starts a paper-trading run of a saved strategy: fills are simulated at live "
    "Hyperliquid prices under Keel's published fill model, no real orders are placed "
    "and no real capital is at risk. The run starts here without a confirmation step "
    "and needs no exchange account. Each plan has a number of concurrent paper runs; "
    "`keel_plan_usage` reports this plan's. Running it with real capital is a step you "
    "take in the Keel web app. The response carries the auto-pause rules; "
    "`keel_live_monitor` reads the run, and `keel_paper_control` pauses, updates or stops "
    "it; stopping frees its slot. Each call starts another run; after a timeout "
    "`keel_live_monitor` shows whether one started."
)


PAPER_DEPLOY = register(
    OutcomeTool(
        name="keel_paper_deploy",
        required_action="backtest.create",
        cli_path=("paper", "deploy"),
        toolset="paper",
        # grounded-in: system/operating_core.md:116-125 (the `live` section:
        # going live hands off to the web app, the description's live half)
        # + paper-trading-v2 specs/06 §3.2.5 (description as policy) and
        # specs/01 §3.13 (the auto-pause rules disclosed at deploy).
        description=PAPER_DEPLOY_DESCRIPTION,
        listed_description=PAPER_DEPLOY_LISTED_DESCRIPTION,
        listed_title="Start Paper Run (Simulated)",
        input_schema=PAPER_DEPLOY_INPUT_SCHEMA,
        annotations={
            "title": "Paper-Trade a Strategy (Simulated)",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            # Bounded to the caller's own Keel workspace: a simulated run on
            # Keel's market data; it contacts no exchange account and publishes
            # nothing (the annotation policy, _annotation_justifications.py;
            # S10 / PT2-90 alignment with keel_backtest_run).
            "openWorldHint": False,
        },
        handler=_handler,
        confirm_in_cli=False,
    )
)
