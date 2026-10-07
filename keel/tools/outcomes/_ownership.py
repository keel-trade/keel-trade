"""Ownership projection helpers for the CLI and MCP surfaces.

Spec 02 §4.1.1 promises agents the first-session ownership projection —
"what does this strategy still need before it can go live". Spec 20 put the
READ routes on keel-api (they had only ever existed on chat-api, which no SDK
surface can reach), so this module now fetches a real projection instead of
reporting the feature unserved.

Two rules govern everything here:

* **One request, and it answers the never-touched-strategy case.**
  ``GET /v1/strategy-work?strategy_id=…`` is strategy-scoped and returns a
  full computed projection with ``session_id: null`` for a strategy nobody
  has opened in chat. The old two-hop (list sessions → read the newest
  session's ownership) returned nothing at all for that strategy, which is
  the most common state an external agent meets.
* **Never fabricate.** When the projection cannot be read, the surfaces say
  so and say WHY. There is no hardcoded "not_started" evidence list — an
  evidence claim this process did not read is a lie to the calling agent
  (Q-0500).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from keel.errors import KeelError, NotFoundError

from ._base import ToolContext
from .open_in_app import app_url_for


logger = logging.getLogger(__name__)

# Q-0533 / spec 20 §7: keel-api serves the projection reads, so the fetch is
# armed. This flag is the ORDERING INTERLOCK between two independent release
# trains — keel-api's and the SDK's. It may only be True in an SDK release cut
# AFTER a keel-api release carrying
# services/keel-api/src/routers/ownership.py is verified in prod; flipping it
# first re-creates Q-0500 (a guaranteed 404 on every strategy read). It is
# also the kill switch if the routes ever have to be withdrawn.
PROJECTION_ROUTES_SERVED = True

# The strategy-scoped projection route (spec 20 §2.1). Session-scoped reads
# exist too (`/v1/strategy-work-sessions/{id}/ownership`) but require knowing
# a session id, and answer nothing when there is no session.
PROJECTION_PATH = "/v1/strategy-work"

UNAVAILABLE_STRATEGY_NOT_VISIBLE = "strategy_not_visible"
UNAVAILABLE_READ_FAILED = "projection_read_failed"
UNAVAILABLE_NOT_SERVED = "projection_not_served"


@dataclass(frozen=True)
class ProjectionFetch:
    """A projection, or the reason there isn't one.

    Surfaces that must ANSWER about ownership (``keel_strategy_readiness``, the
    ``keel://ownership`` resource) need the reason. Surfaces that merely
    decorate an envelope do not, and use :func:`fetch_ownership_projection`.
    """

    projection: dict[str, Any] | None = None
    unavailable_code: str | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        """Exactly one of "here is the projection" / "here is why not".

        Every failure arm of :func:`fetch_projection` sets BOTH a code and a
        reason; the success arm sets neither. This invariant is what stops a
        future arm from returning a bare ``ProjectionFetch()`` — which would
        render as ``projection_available: false`` with a null reason, the
        least useful answer a surface can give.
        """
        has_projection = self.projection is not None
        has_reason = self.unavailable_code is not None
        if has_projection == has_reason:
            raise ValueError(
                "ProjectionFetch carries either a projection or an "
                f"unavailable_code, never both and never neither (got "
                f"projection={has_projection}, code={self.unavailable_code!r})"
            )
        if has_reason and not self.unavailable_reason:
            raise ValueError(
                f"unavailable_code {self.unavailable_code!r} needs a "
                "human-readable unavailable_reason — a code alone tells the "
                "calling agent nothing it can act on"
            )


def fetch_projection(ctx: ToolContext, strategy_id: str) -> ProjectionFetch:
    """Read one strategy's ownership projection, or say why it could not be.

    Best-effort by spec (an ownership hint must never break a strategy read),
    but never silent: every failure is logged with its error.
    """
    if not PROJECTION_ROUTES_SERVED:
        return ProjectionFetch(
            unavailable_code=UNAVAILABLE_NOT_SERVED,
            unavailable_reason=(
                "The first-session ownership projection is not served on this "
                "API surface yet — view ownership status on the strategy page: "
                + app_url_for("strategy", strategy_id, ctx)
            ),
        )
    try:
        projection = ctx.get_client().get(PROJECTION_PATH, strategy_id=strategy_id)
    except NotFoundError as exc:
        # keel-api answers 404 only for a strategy this credential cannot
        # name — a strategy with no ownership work at all is a 200 carrying a
        # not-started projection (spec 20 §2.4 N1 vs N7).
        logger.debug("no ownership projection for %s: %s", strategy_id, exc)
        return ProjectionFetch(
            unavailable_code=UNAVAILABLE_STRATEGY_NOT_VISIBLE,
            unavailable_reason=(
                f"No strategy {strategy_id} is visible to this credential — "
                "check the id, or that the token is scoped to the right org. "
                f"Your strategies: {ctx.app_url}/strategies"
            ),
        )
    except KeelError as exc:
        logger.warning("ownership projection fetch failed for %s: %s", strategy_id, exc)
        return ProjectionFetch(
            unavailable_code=UNAVAILABLE_READ_FAILED,
            unavailable_reason=(
                f"The ownership projection could not be read for {strategy_id}: "
                f"{exc}. View it on the strategy page: " + app_url_for("strategy", strategy_id, ctx)
            ),
        )
    except Exception as exc:  # noqa: BLE001 — a hint must never break a read
        logger.warning("ownership projection fetch failed for %s", strategy_id, exc_info=True)
        return ProjectionFetch(
            unavailable_code=UNAVAILABLE_READ_FAILED,
            unavailable_reason=(
                f"The ownership projection could not be read for {strategy_id}: "
                f"{exc}. View it on the strategy page: " + app_url_for("strategy", strategy_id, ctx)
            ),
        )

    if not isinstance(projection, dict):
        logger.warning(
            "ownership projection for %s was %s, not an object",
            strategy_id,
            type(projection).__name__,
        )
        return ProjectionFetch(
            unavailable_code=UNAVAILABLE_READ_FAILED,
            unavailable_reason=(
                f"The ownership projection for {strategy_id} came back in an "
                "unexpected shape. View it on the strategy page: "
                + app_url_for("strategy", strategy_id, ctx)
            ),
        )
    projection.setdefault("resource_uri", f"keel://ownership/strategy/{strategy_id}")
    return ProjectionFetch(projection=projection)


def fetch_ownership_projection(ctx: ToolContext, strategy_id: str) -> dict[str, Any] | None:
    """The projection for envelope hints, or ``None``.

    A thin read of :func:`fetch_projection` — an envelope must not grow a
    "why not" key on the happy path of a strategy read, so hint surfaces
    simply add no fields when there is nothing to add.
    """
    return fetch_projection(ctx, strategy_id).projection


def ownership_envelope_fields(projection: dict[str, Any] | None) -> dict[str, Any]:
    """Return Spec 02 readiness hint fields for an outcome envelope.

    The LISTED profile carries no `live_readiness_blockers` (Q-2080,
    2026-10-01): that column is the live-trading half of the projection, and
    the listed surface is research and backtests only — a field named for
    going live on a connector that cannot read as a promise the tool makes.
    """
    if not projection:
        return {}
    from ._toolsets import is_listed_profile

    fields = {
        "ownership_resource_uri": projection.get("resource_uri"),
        "ownership_status": projection.get("overall_status"),
        "next_recommended_action": projection.get("next_recommended_action"),
        "missing_evidence": projection.get("missing_evidence") or [],
    }
    if not is_listed_profile():
        fields["live_readiness_blockers"] = projection.get("live_readiness_blockers") or []
    return fields


def projection_unavailable_fields(fetch: ProjectionFetch) -> dict[str, Any]:
    """Honest body for a surface that must answer without a projection.

    Says it is unavailable and why — and carries NO evidence fields. A
    ``missing_evidence`` list this process did not read is a fabrication
    (Q-0500), and the fact that one shape of absence ("not started") would
    often be right does not make asserting it honest.
    """
    return {
        "projection_available": False,
        "unavailable_code": fetch.unavailable_code,
        "unavailable_reason": fetch.unavailable_reason,
    }


__all__ = [
    "PROJECTION_PATH",
    "PROJECTION_ROUTES_SERVED",
    "UNAVAILABLE_NOT_SERVED",
    "UNAVAILABLE_READ_FAILED",
    "UNAVAILABLE_STRATEGY_NOT_VISIBLE",
    "ProjectionFetch",
    "fetch_ownership_projection",
    "fetch_projection",
    "ownership_envelope_fields",
    "projection_unavailable_fields",
]
