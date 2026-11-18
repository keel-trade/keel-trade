"""Paper runtime strings, each in a full form and a listed-safe form.

paper-trading-v2 S06 §3.4.6 (R-31): runtime strings reach listed (hosted
connector) callers too, so every envelope, talking-point and label string the
paper surfaces emit ships from this one module, as a :class:`PaperString` row
carrying both forms. A render path never retypes one of these texts; it imports
the row and calls :meth:`PaperString.for_profile` (or :meth:`PaperString.form`
when the caller already knows the profile), which selects by
:func:`~keel.tools.outcomes._toolsets.is_listed_profile` at call time.

A form that is never emitted on a profile is an explicit ``None`` (the claim
and permission rows never reach the hosted connector; the paper-id refusal is
never emitted on the full profile). Every row declares BOTH forms: a form left
undeclared stays :data:`MISSING`, which ``tests/test_policy_scan.py``'s
enumeration arm refuses and which :meth:`PaperString.form` raises on rather
than guessing a text (AC-06-21).

Every non-``None`` listed form passes the policy scan's ``FORBIDDEN_TEXT_RE``
WITHOUT the paper-compound allowance (§3.4.4), so it is correct with the
listed flag in either state, and names no tool the listed profile does not
serve. The scan also asserts no module under ``keel/`` but this one carries
any of these texts as a literal.

Templates: the carry-over row's full form takes ``{commit}`` and the
auto-pause disclosure takes ``{idle_days}`` and ``{grace_days}``; the caller
formats them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ._toolsets import is_listed_profile


__all__ = [
    "ALL_PAPER_STRINGS",
    "CLAIM_CONTEXT_POINT",
    "CONTROL_REAL_ID_REFUSAL",
    "CONTROL_REAL_ID_SUGGESTION",
    "CONTROL_RUNS_POINTER",
    "DEPLOY_AUTO_PAUSE_DISCLOSURE",
    "DEPLOY_SERVER_ERROR_SUGGESTION",
    "DEPLOY_FILL_DISCLOSURE",
    "FULL_DEPLOY_LINE_SENTENCE",
    "GRADUATION_CARRY_OVER_LINE",
    "GRADUATION_CRITERIA_LINE",
    "GRADUATION_KEEP_PAPER_LINE",
    "GRADUATION_LIVE_CALL_LINE",
    "HEALTH_LINE_ROWS",
    "LADDER_LINE",
    "MISSING",
    "MONITOR_PAPER_ID_REFUSAL",
    "MONITOR_PAPER_LABEL",
    "PAPER_WALL_SLOT_LINE",
    "PERMISSION_REFUSAL_LINE",
    "PORTFOLIO_PAPER_SECTION_LABEL",
    "PaperString",
]


class _Missing:
    """The marker for a form a row never declared (distinct from ``None``)."""

    _instance: _Missing | None = None

    def __new__(cls) -> _Missing:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "MISSING"


#: An undeclared form. Never a valid value: the enumeration arm refuses it and
#: :meth:`PaperString.form` raises on it.
MISSING: Final = _Missing()


@dataclass(frozen=True)
class PaperString:
    """One §3.4.6 row: a key, the spec context, and its two forms."""

    key: str
    context: str
    full: str | None | _Missing = MISSING
    listed: str | None | _Missing = MISSING
    #: True for a row whose listed form is emitted ONLY while the listed flag
    #: is on for the caller (S10, PT2-90): it may name the listed paper tools
    #: (``_toolsets.LISTED_PAPER_TOOLS``), which that surface then serves.
    listed_flag_on_only: bool = False

    def form(self, *, listed: bool) -> str | None:
        """The listed or full form; ``None`` when that profile never emits it.

        Raises ``LookupError`` for an undeclared form: a row without its
        listed variant (or its explicit ``None``) is a build defect, never a
        reason to fall back to the other form.
        """
        value = self.listed if listed else self.full
        if isinstance(value, _Missing):
            which = "listed" if listed else "full"
            raise LookupError(
                f"paper string {self.key!r} declares no {which} form; declare the text "
                "or an explicit None in keel/tools/outcomes/_paper_strings.py (S06 §3.4.6)"
            )
        return value

    def for_profile(self) -> str | None:
        """The form for the active server profile, read at call time."""
        return self.form(listed=is_listed_profile())


# ── The §3.4.6 rows ────────────────────────────────────────────────────────

CLAIM_CONTEXT_POINT = PaperString(
    key="claim_context_point",
    context="Claim wall: the context point (§3.2.4a arm 1)",
    full=(
        "creating a paper run needs a free Keel account; everything in it is simulated, "
        "and no real capital is ever at risk"
    ),
    # Never emitted on hosted: _is_anon_session() is False there.
    listed=None,
)

PERMISSION_REFUSAL_LINE = PaperString(
    key="permission_refusal_line",
    context="Permission refusal line (§3.2.4a)",
    full=(
        "this Keel credential cannot start or change paper runs; they need the same "
        "permission as running a backtest, and signing in again does not change it"
    ),
    # Never emitted on hosted: the hosted arm of _translate_403 speaks there.
    listed=None,
)

GRADUATION_LIVE_CALL_LINE = PaperString(
    key="graduation_live_call_line",
    context="Graduation: the live-is-your-call line (§3.5.4)",
    full="going live is the user's call in the Keel web app",
    listed="running it with real capital is a step you take in the Keel web app",
)

GRADUATION_KEEP_PAPER_LINE = PaperString(
    key="graduation_keep_paper_line",
    context="Graduation: the keep-paper line (§3.5.4)",
    full="keeping the paper run going is a fine choice",
    listed="keeping the paper run going is a fine choice",
)

GRADUATION_CARRY_OVER_LINE = PaperString(
    key="graduation_carry_over_line",
    context="Graduation: the carry-over line (§3.5.4); formats {commit}",
    full="your paper run proved commit {commit}; the strategy has moved ahead",
    # Never emitted on listed: the handoff mint returns None there.
    listed=None,
)

LADDER_LINE = PaperString(
    key="ladder_line",
    # The rungs, with no check mark: no paper route requires a backtest, so a
    # "Backtested ✓" rung claimed a step the run may never have had (QA polish).
    context="The ladder line (§3.2.3, §3.5.4)",
    full="Backtest → Paper → Live — going live is a web-app step",
    listed="Backtest → Paper → Live — running with real capital is a web-app step",
)

FULL_DEPLOY_LINE_SENTENCE = PaperString(
    key="full_deploy_line_sentence",
    context="The full-profile deploy line's paper sentence (§3.9 row 7)",
    full=(
        "— or start a paper run now with `keel_paper_deploy` "
        "(simulated, no exchange account needed)"
    ),
    # n/a on listed: the listed nudge arm returns None.
    listed=None,
)

MONITOR_PAPER_LABEL = PaperString(
    key="monitor_paper_label",
    context="The monitor's paper label (§3.5.2)",
    full="Paper run (simulated)",
    # Emitted on listed only with the listed flag on.
    listed="Paper run (simulated)",
)

MONITOR_PAPER_ID_REFUSAL = PaperString(
    key="monitor_paper_id_refusal",
    context="The monitor's paper-id refusal (§3.5.1, listed with the flag off; PT2-28)",
    # Never emitted on full: the full profile renders the row (§3.5.2).
    full=None,
    listed=(
        "This connector does not show simulated (paper) runs yet; open this one in the "
        "Keel web app with keel_app_link."
    ),
)

# ── Rows the module carries beyond §3.4.6's table ─────────────────────────

DEPLOY_FILL_DISCLOSURE = PaperString(
    key="deploy_fill_disclosure",
    context=(
        "keel_paper_deploy: the fill-model disclosure line (§3.2.3, the deploy-surface "
        "duty of S01 §3.13); points at the published model, never its constants"
    ),
    full=(
        "Fills are simulated at live Hyperliquid prices under Keel's published fill "
        "model (see the Paper Trading docs); no real orders are placed and no funds "
        "are at risk."
    ),
    # No section name: "Paper Trading" is a banned listed token without the
    # compound allowance (§3.4.4).
    listed=(
        "Fills are simulated at live Hyperliquid prices under Keel's published fill "
        "model for paper runs (see the Keel docs); no real orders are placed and no "
        "real capital is at risk."
    ),
)

DEPLOY_AUTO_PAUSE_DISCLOSURE = PaperString(
    key="deploy_auto_pause_disclosure",
    context=(
        "keel_paper_deploy: the auto-pause disclosure line (§3.2.3, S01 §3.13); "
        "formats {idle_days} and {grace_days} from the response's disclosures"
    ),
    full=(
        "The run auto-pauses after {idle_days} days without an owner visit, and after "
        "a plan downgrade that leaves it over the plan's paper limit it auto-pauses "
        "after at least {grace_days} days' grace; the record is preserved and "
        "resuming it is one call."
    ),
    listed=(
        "The run auto-pauses after {idle_days} days without an owner visit, and after "
        "a plan change that leaves it over the plan's paper limit it auto-pauses "
        "after at least {grace_days} days' grace; the record is preserved and the run "
        "can be resumed from its page in the Keel web app."
    ),
)

GRADUATION_CRITERIA_LINE = PaperString(
    key="graduation_criteria_line",
    context=(
        "Graduation: the criteria met, from the envelope's own values (§3.5.4); formats "
        "{days}, {evals}, {residual_bps}, {band_bps}, {paper_max_dd_pct}, "
        "{backtest_max_dd_pct}. Never a return without its drawdown: it names none."
    ),
    # PT2-93 (founder, 2026-10-15): the comparison with the backtest is shown,
    # never judged; readiness no longer reads the parity band, and this line
    # names no band. The difference itself is in the run's `parity` block.
    full=(
        "its graduation criteria are met: {days} days running, {evals} evaluations, and "
        "a max drawdown of {paper_max_dd_pct}% against the backtest's {backtest_max_dd_pct}%"
    ),
    listed=(
        "its graduation criteria are met: {days} days running, {evals} evaluations, and "
        "a max drawdown of {paper_max_dd_pct}% against the backtest's {backtest_max_dd_pct}%"
    ),
)

PORTFOLIO_PAPER_SECTION_LABEL = PaperString(
    key="portfolio_paper_section_label",
    context=(
        "The portfolio view's paper section label (§3.5.5): the practice summary is its "
        "own section, never summed into the live totals"
    ),
    full="Paper (simulated)",
    # Emitted on listed only with the listed flag on (the section is absent off).
    listed="Paper (simulated)",
)

# ── The health-line phrase map (S05 IC-05-6; S06 §3.5.3; finding F29) ──────
#
# One phrase per IC-04-2 ``health_detail.reason``, total over the reason enum
# (R-43's ``schedule_unreadable`` included), plus the running phrase for a null
# reason. The full forms are keel-app's ``HEALTH_LINE_PHRASES``
# (``services/keel-app/src/lib/status-badge.ts``, the single edit point) as
# templates, and ``tests/test_paper_health_line_parity.py`` reads that map so
# the two cannot drift. keel-app's ``n === 1`` plural is two rows here. A
# listed form differs only where the full text is not listed-safe.

HEALTH_LINE_RUNNING = PaperString(
    key="health_line_running",
    context="Health line, reason null (the run is evaluating); formats {cadence}",
    full="evaluating every {cadence}",
    listed="evaluating every {cadence}",
)

HEALTH_LINE_CONSECUTIVE_MISSING_ONE = PaperString(
    key="health_line_consecutive_missing_one",
    context="Health line, consecutive_missing with one miss; formats {n} and {since}",
    full="{n} missed evaluation since {since}",
    listed="{n} missed evaluation since {since}",
)

HEALTH_LINE_CONSECUTIVE_MISSING_MANY = PaperString(
    key="health_line_consecutive_missing_many",
    context="Health line, consecutive_missing with several misses; formats {n} and {since}",
    full="{n} missed evaluations since {since}",
    listed="{n} missed evaluations since {since}",
)

HEALTH_LINE_LATEST_EVAL_FAILED = PaperString(
    key="health_line_latest_eval_failed",
    context="Health line, latest_eval_failed",
    full="last evaluation failed",
    listed="last evaluation failed",
)

HEALTH_LINE_LATEST_EXECUTION_FAILED = PaperString(
    key="health_line_latest_execution_failed",
    context="Health line, latest_execution_failed",
    full="last execution failed",
    listed="last execution failed",
)

HEALTH_LINE_EXECUTION_MISSING = PaperString(
    key="health_line_execution_missing",
    context="Health line, execution_missing",
    full="evaluation completed — no execution recorded",
    listed="evaluation completed — no execution recorded",
)

HEALTH_LINE_FUNDING_STALLED = PaperString(
    key="health_line_funding_stalled",
    context="Health line, funding_stalled; formats {since}",
    full="funding data delayed — accrual paused since {since}",
    # "funding" is a banned listed token (FORBIDDEN_TEXT_RE): the listed form
    # names the same fact without it.
    listed="rate data delayed — accrual paused since {since}",
)

HEALTH_LINE_FAILURE_RATE = PaperString(
    key="health_line_failure_rate",
    context="Health line, failure_rate",
    full="recent evaluations failing",
    listed="recent evaluations failing",
)

HEALTH_LINE_SILENCE = PaperString(
    key="health_line_silence",
    context="Health line, silence",
    full="no evaluations received — if this continues, stop the run and start it again",
    listed="no evaluations received — if this continues, stop the run and start it again",
)

HEALTH_LINE_DISPATCH_EXHAUSTED = PaperString(
    key="health_line_dispatch_exhausted",
    context="Health line, dispatch_exhausted",
    full="last evaluation was not handed to execution — retries ran out",
    listed="last evaluation was not handed to execution — retries ran out",
)

HEALTH_LINE_RAIL_BLOCKED = PaperString(
    key="health_line_rail_blocked",
    context="Health line, rail_blocked",
    full="last bar's orders were held by the order-rate limit",
    listed="last bar's orders were held by the order-rate limit",
)

HEALTH_LINE_EQUITY_STALE = PaperString(
    key="health_line_equity_stale",
    context="Health line, equity_stale; formats {since}",
    full="simulated value not updated since {since}",
    listed="simulated value not updated since {since}",
)

HEALTH_LINE_HALTED = PaperString(
    key="health_line_halted",
    context="Health line, halted",
    full="execution halted by a safety check — no fills until cleared",
    listed="execution halted by a safety check — no fills until cleared",
)

HEALTH_LINE_SCHEDULE_UNREADABLE = PaperString(
    key="health_line_schedule_unreadable",
    context="Health line, schedule_unreadable (R-43)",
    full="schedule unreadable — evaluations cannot run",
    listed="schedule unreadable — evaluations cannot run",
)

# ── keel_paper_control on the listed profile (S10 §4-5, PT2-90) ────────────
#
# The listed connector serves keel_paper_control with the flag on, so its
# pointers name only listed tools: keel_deployments_list and keel_live_control
# are not on that surface (S06 §3.4.4).

CONTROL_RUNS_POINTER = PaperString(
    key="control_runs_pointer",
    listed_flag_on_only=True,
    context="keel_paper_control: where an agent finds this org's paper run ids",
    full=(
        "`keel_deployments_list` with `capital_class=practice` lists this org's paper "
        "runs and their ids."
    ),
    listed=(
        "`keel_live_monitor` with no deployment_id lists this org's paper runs and their "
        "ids in its paper section."
    ),
)

CONTROL_REAL_ID_REFUSAL = PaperString(
    key="control_real_id_refusal",
    listed_flag_on_only=True,
    context=(
        "keel_paper_control's guard message for a real-capital id (S06 §3.6) on the "
        "listed profile; formats {deployment_id}. The full profile keeps the guard's "
        "own message, which names keel_live_control."
    ),
    full=None,
    listed=(
        "{deployment_id} is a real-capital strategy, not a paper run; `keel_paper_control` "
        "changes simulated paper runs only."
    ),
)

CONTROL_REAL_ID_SUGGESTION = PaperString(
    key="control_real_id_suggestion",
    listed_flag_on_only=True,
    context="The listed suggestion beside CONTROL_REAL_ID_REFUSAL",
    full=None,
    listed="Real-capital strategies are managed in the Keel web app; `keel_app_link` opens it.",
)

DEPLOY_SERVER_ERROR_SUGGESTION = PaperString(
    key="deploy_server_error_suggestion",
    listed_flag_on_only=True,
    context=(
        "keel_paper_deploy on a 5xx (S10 review F3): the create may have started a run, "
        "so the read-back is the run list, never a blind resend"
    ),
    full=(
        "`keel_deployments_list` with `capital_class=practice` shows whether a run "
        "started; call again only if none did."
    ),
    listed=(
        "`keel_live_monitor` with no deployment_id shows whether a run started; call "
        "again only if none did."
    ),
)

PAPER_WALL_SLOT_LINE = PaperString(
    key="paper_wall_slot_line",
    listed_flag_on_only=True,
    context=(
        "The paper quota wall's slot line (S10 §5, PT2-90): names Keel's own tool, "
        "never a plan. Both forms say to ask first: agents swapped a running paper run "
        "for a new one without asking far more often than they hit this wall (prod, "
        "2026-10-17..11-16). The full form names no tool (R-59)."
    ),
    full="Ask the user which paper run to stop before stopping one; a stop ends its record.",
    listed=(
        "Stopping a paper run frees its slot (`keel_paper_control` `stop`); ask the user "
        "which one first."
    ),
)

#: keel-app's phrase keys → rows. ``"null"`` is the running phrase; keel-app's
#: one ``consecutive_missing`` template is its two plural rows (``:one`` when
#: ``n == 1``, ``:many`` otherwise).
HEALTH_LINE_ROWS: Final[dict[str, PaperString]] = {
    "null": HEALTH_LINE_RUNNING,
    "consecutive_missing:one": HEALTH_LINE_CONSECUTIVE_MISSING_ONE,
    "consecutive_missing:many": HEALTH_LINE_CONSECUTIVE_MISSING_MANY,
    "latest_eval_failed": HEALTH_LINE_LATEST_EVAL_FAILED,
    "latest_execution_failed": HEALTH_LINE_LATEST_EXECUTION_FAILED,
    "execution_missing": HEALTH_LINE_EXECUTION_MISSING,
    "funding_stalled": HEALTH_LINE_FUNDING_STALLED,
    "failure_rate": HEALTH_LINE_FAILURE_RATE,
    "silence": HEALTH_LINE_SILENCE,
    "dispatch_exhausted": HEALTH_LINE_DISPATCH_EXHAUSTED,
    "rail_blocked": HEALTH_LINE_RAIL_BLOCKED,
    "equity_stale": HEALTH_LINE_EQUITY_STALE,
    "halted": HEALTH_LINE_HALTED,
    "schedule_unreadable": HEALTH_LINE_SCHEDULE_UNREADABLE,
}

#: Every row, §3.4.6's order first. The policy scan enumerates the module and
#: refuses a row missing from this tuple.
ALL_PAPER_STRINGS: Final[tuple[PaperString, ...]] = (
    CLAIM_CONTEXT_POINT,
    PERMISSION_REFUSAL_LINE,
    GRADUATION_LIVE_CALL_LINE,
    GRADUATION_KEEP_PAPER_LINE,
    GRADUATION_CARRY_OVER_LINE,
    LADDER_LINE,
    FULL_DEPLOY_LINE_SENTENCE,
    MONITOR_PAPER_LABEL,
    MONITOR_PAPER_ID_REFUSAL,
    DEPLOY_FILL_DISCLOSURE,
    DEPLOY_AUTO_PAUSE_DISCLOSURE,
    GRADUATION_CRITERIA_LINE,
    PORTFOLIO_PAPER_SECTION_LABEL,
    *HEALTH_LINE_ROWS.values(),
    CONTROL_RUNS_POINTER,
    CONTROL_REAL_ID_REFUSAL,
    DEPLOY_SERVER_ERROR_SUGGESTION,
    CONTROL_REAL_ID_SUGGESTION,
    PAPER_WALL_SLOT_LINE,
)
