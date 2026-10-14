"""The paper tools' 403 arms (paper-trading-v2 S06 §3.2.4, R-58, R-59; IC-06-1).

Which arm applies is decided by the session first and the response second,
and the claim discriminator runs before the quota builder:

1. **Claim** — a local anonymous session (``_handoff._is_anon_session()``,
   never true on hosted) refused with ``entitlement:no_cap_grants`` on
   ``paper_strategies_max``, or ``cap_exceeded`` with ``limit=0``: HEAD's
   ``anon_signin_handoff`` as it exists, with ``reason="account_required"``,
   the §3.4.6 claim line as its context point and the paper call as
   ``then_retry``.
2. **Permission** — a local credential refused ``backtest.create``
   (``credential:scope_denied:backtest.create`` or
   ``role:viewer_cannot:backtest.create``, a narrowed key or a viewer):
   ``EntitlementError`` with no recovery tool and the §3.4.6 permission
   line. The SDK's default scope-missing arm would ask for a live re-login,
   which is wrong for a simulated action (T1). On hosted, ``_translate_403``'s
   hosted arm already names neither a re-login nor live consent, so it stands.
3. **Quota** — ``cap_exceeded`` with ``limit ≥ 1``: HEAD's neutral
   ``maybe_quota_handoff`` wall (D-12). On the listed profile with the listed
   flag on for the caller's host it gains one fact line,
   ``_paper_strings.PAPER_WALL_SLOT_LINE`` ("Stopping a paper run frees its
   slot (`keel_paper_control` `stop`)."): it names Keel's own tool, never a
   plan, and passes the same D-12 scan (S10 §5; PT2-90 supersedes R-59's "no
   slot-freeing point" for the listed form only). Everywhere else the wall is
   HEAD's, byte for byte.

Off arm 1, a ``no_cap_grants`` or ``limit=0`` denial is relayed as the route's
403 with no wall added. Every other 403 re-raises unchanged. A ``401`` never
reaches these arms: the SDK's own ``AuthError`` for the credential stands
(the caller already has an account).
"""

from __future__ import annotations

from typing import Any

from keel.errors import EntitlementError, KeelError

from ._paper_strings import CLAIM_CONTEXT_POINT, PAPER_WALL_SLOT_LINE, PERMISSION_REFUSAL_LINE


#: The server reasons that mean "this credential may not make paper writes"
#: (``libs/platform_auth/authorize.py``): paper writes authorize
#: ``backtest.create`` (PT2-21).
_PERMISSION_REASONS = (
    "credential:scope_denied:backtest.create",
    "role:viewer_cannot:backtest.create",
)


def _paper_slot_denial(e: EntitlementError) -> bool:
    """A ``no_cap_grants`` or ``limit=0`` denial on ``paper_strategies_max``."""
    info = e.input if isinstance(e.input, dict) else None
    if e.recovery_tool is not None or info is None:
        return False
    if info.get("unit") != "paper_strategies_max":
        return False
    return info.get("kind") == "no_cap_grants" or (
        info.get("kind") == "cap_exceeded" and info.get("limit") == 0
    )


def paper_permission_refusal(e: EntitlementError) -> EntitlementError | None:
    """Arm 2: the local refusal of a credential that may not run backtests."""
    from ._toolsets import local_tools_registered

    if not local_tools_registered():
        return None
    if not set(e.server_reasons) & set(_PERMISSION_REASONS):
        return None
    line = PERMISSION_REFUSAL_LINE.form(listed=False)
    err = EntitlementError(
        f"{line[0].upper()}{line[1:]}.",
        suggestion=(
            "A Keel credential or role that may run backtests may also start and "
            "change paper runs; this one may not."
        ),
    )
    err.recovery_tool = None
    err.recovery_tool_args = None
    return err


def paper_wall(
    e: EntitlementError, *, blocked_action: str, retry_call: dict[str, Any]
) -> KeelError | None:
    """The arm for a 403 a paper tool received, or ``None`` to re-raise ``e``.

    ``blocked_action`` is ``paper_deploy`` or ``paper_control``; the claim arm
    is ``keel_paper_deploy``'s only (``paper_control`` acts on runs an
    anonymous session cannot own).
    """
    from ._handoff import _is_anon_session, anon_signin_handoff, maybe_quota_handoff

    if _paper_slot_denial(e):
        if blocked_action == "paper_deploy" and _is_anon_session():
            return anon_signin_handoff(
                blocked_action=blocked_action,
                reason="account_required",
                context_point=CLAIM_CONTEXT_POINT.form(listed=False),
                retry_call=retry_call,
            )
        return None  # the route's 403, relayed with no wall added
    refusal = paper_permission_refusal(e)
    if refusal is not None:
        return refusal
    return maybe_quota_handoff(
        e, blocked_action=blocked_action, retry_call=retry_call, extra_points=_slot_points()
    )


def _slot_points() -> list[str]:
    """The listed paper wall's slot-freeing line, only where `keel_paper_control`
    is served beside the paper tool that hit the wall (S10 §5)."""
    from ._toolsets import is_listed_profile, listed_paper_enabled

    if not (is_listed_profile() and listed_paper_enabled()):
        return []
    line = PAPER_WALL_SLOT_LINE.form(listed=True)
    return [line] if line else []
