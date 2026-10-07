"""`keel_account_status` as a view tool (agent-surface-cleanup spec 02 §2.5).

One pure builder: the status body `status._handler` already assembles →
`capabilities` (derived from the tools the ACTIVE profile loads, never from
`KEEL_TOOLSETS`, which the listed profile ignores — the defect where the
listed status said "live trading allowed" while serving no live-write tool)
and the `view` whose markdown is the model's text block:

    **Keel** · signed in as {org_name} · {plan} plan
    [Identity check failed: {identity_error}]   (only when the probe failed)
    {quota_sentences} | [Quota could not be read: {entitlements_error}] Quota is per org and shared across the web app, the CLI and other chats.
    {line_3}
    View in Keel: {app_url}/strategies

Every sentence is a fact read from the body; a unit the server did not send
is omitted, never invented (Q-1590).
"""

from __future__ import annotations

from typing import Any

from ._base import election_key


__all__ = [
    "CAPABILITY_PHRASE",
    "LIVE_WRITE_TOOLS",
    "build_status_view",
    "capabilities_for",
    "capability_line",
    "status_next",
]

#: Tools whose presence means live WRITES happen on this server.
LIVE_WRITE_TOOLS = frozenset({"keel_live_deploy", "keel_live_control", "keel_live_update"})

KIND_STATUS = "status"

_UNLIMITED = 2147483647

#: Line 3, keyed by `(profile, monitor, live_actions)` — every reachable text
#: (spec 02 §2.5's table). A combination outside the table has no line.
#:
#: Each text states what THIS server's tools do, never a permission level
#: (Q-1960): "live monitoring read-only; live actions in the Keel web app"
#: reached a ChatGPT user as "the connected Keel session is read-only for
#: live trading". The listed/read-only row's phrase is `CAPABILITY_PHRASE`,
#: which the listed `keel_account_status` description and the `surface-listed`
#: instructions body quote byte for byte (agent-surface-cleanup fix-wave
#: resolution #10; pinned by test_policy_scan's permission guard).
#:
#: Aligned with the listed instructions' no-trading boundary (04 §5.3, D-12):
#: "no order or start/stop tools — those are in the Keel web app" implied
#: ordering was one step away. It now states the same boundary the
#: instructions do — the connector cannot place orders, move funds or
#: connect wallets, and running strategies are managed in the web app.
CAPABILITY_PHRASE = (
    "research and backtests; it cannot place orders, move funds or connect wallets; "
    "strategies that are running are managed in the Keel web app"
)
_NOT_LOADED_PHRASE = (
    "research and backtests; live status is not loaded; it cannot place orders, move "
    "funds or connect wallets; strategies that are running are managed in the Keel web app"
)
_LINE_3: dict[tuple[str, str, str], str] = {
    ("listed", "read-only", "web app"): f"This connector: {CAPABILITY_PHRASE}.",
    ("listed", "none", "web app"): f"This connector: {_NOT_LOADED_PHRASE}.",
    ("full", "none", "web app"): f"This server: {_NOT_LOADED_PHRASE}.",
    ("full", "read-only", "web app"): f"This server: {CAPABILITY_PHRASE}.",
    ("full", "read-write", "here"): (
        "This server: research, backtests, live status, and live tools that start, "
        "stop and update strategies with explicit consent."
    ),
}


def capabilities_for(loaded: Any) -> dict[str, Any]:
    """`capabilities` from the loaded tool names — ONE source (spec 02 §2.5)."""
    names = set(loaded or [])
    live_write = bool(names & LIVE_WRITE_TOOLS)
    if "keel_live_monitor" not in names:
        monitor = "none"
    else:
        monitor = "read-write" if live_write else "read-only"
    return {
        "research": "keel_components_search" in names,
        "backtest": "keel_backtest_run" in names,
        "monitor": monitor,
        "live_actions": "here" if "keel_live_deploy" in names else "web app",
    }


def capability_line(profile: str, capabilities: dict[str, Any]) -> str | None:
    key = (profile, str(capabilities.get("monitor")), str(capabilities.get("live_actions")))
    return _LINE_3.get(key)


def _unit_block(entry: dict) -> dict[str, Any] | None:
    """`entitlements.summary[]` → a quota block `render_quota_sentence` reads."""
    if not isinstance(entry, dict):
        return None
    limit, remaining = entry.get("granted"), entry.get("available")
    if entry.get("unlimited") or limit == _UNLIMITED:
        return {"unit": entry.get("unit"), "unlimited": True}
    if not isinstance(limit, int) or not isinstance(remaining, int):
        return None
    block: dict[str, Any] = {
        "unit": entry.get("unit"),
        "limit": limit,
        "used": entry.get("spent"),
        "remaining": remaining,
    }
    for key in ("period", "resets_at", "seconds_to_reset", "reserved"):
        if entry.get(key) is not None:
            block[key] = entry[key]
    return block


def _fraction(block: dict) -> str:
    from keel.errors import _label_for_unit

    label = block.get("label") or _label_for_unit(str(block.get("unit") or ""))
    text = f"{block['remaining']:,} of {block['limit']:,} {label}"
    reserved = block.get("reserved")
    if isinstance(reserved, int) and not isinstance(reserved, bool) and reserved > 0:
        text += f" ({reserved:,} reserved by runs in flight)"
    return text


def quota_sentences(summary: Any) -> str | None:
    """Line 2's sentences: one per finite unit the server sent; units that
    share a period and a reset instant are joined into one sentence."""
    from keel.errors import (
        _label_for_unit,
        format_reset_instant,
        quota_period_phrase,
        quota_reset_instant,
    )

    blocks = [b for b in (_unit_block(e) for e in summary or []) if b is not None]
    if not blocks:
        return None
    sentences: list[str] = []
    groups: dict[tuple[Any, Any], list[dict]] = {}
    order: list[tuple[Any, Any]] = []
    for block in blocks:
        if block.get("unlimited"):
            label = _label_for_unit(str(block.get("unit") or ""))
            sentences.append(f"Unlimited {label}.")
            continue
        key = (block.get("period"), quota_reset_instant(block))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(block)
    for key in order:
        group = groups[key]
        head = " and ".join(_fraction(b) for b in group)
        when = key[1]
        reset = f"; they reset {format_reset_instant(when)}" if when else ""
        sentences.append(f"{head} left{quota_period_phrase(group[0])}{reset}.")
    return " ".join(sentences) if sentences else None


def status_next(body: dict, *, hosted: bool, profile: str) -> str | None:
    """The ONE `next` string (spec 02 §2.5) — the first condition that holds."""
    if body.get("authenticated") is False:
        if hosted or profile == "listed":
            return (
                "Not signed in — the connector's own sign-in (reconnect it in the "
                "client) starts a session."
            )
        return (
            "Not signed in — keel_auth_login opens the browser sign-in "
            "(keel auth login from a terminal)."
        )
    claim = body.get("pending_claim")
    if isinstance(claim, dict) and not claim.get("declined"):
        return (
            "A deferred claim is waiting: keel_auth_login(attach_anonymous_work=true|false) "
            "resolves it."
        )
    anonymous = body.get("anonymous")
    if isinstance(anonymous, dict) and anonymous.get("active"):
        expires = anonymous.get("org_expires_at") or "soon"
        return (
            f"This is an anonymous workspace, expiring {expires}; keel_auth_login keeps "
            "its strategies and backtests."
        )
    return None


def _header(body: dict) -> str:
    anonymous = body.get("anonymous")
    if isinstance(anonymous, dict) and anonymous.get("active"):
        expires = anonymous.get("org_expires_at")
        return "**Keel** · anonymous workspace" + (f" · expires {expires}" if expires else "")
    if body.get("authenticated") is False:
        return "**Keel** · not signed in"
    identity = body.get("identity") if isinstance(body.get("identity"), dict) else {}
    bits = ["**Keel**"]
    if identity.get("org_name"):
        bits.append(f"signed in as {identity['org_name']}")
    elif body.get("authenticated"):
        bits.append("signed in")
    if identity.get("plan"):
        bits.append(f"{identity['plan']} plan")
    return " · ".join(bits)


def _anon_quota(anonymous: dict) -> str | None:
    """Line 2 for an anonymous org — the same sentence renderer over the
    server's own `remaining` / `limits` (never numbers invented locally)."""
    remaining, limits = anonymous.get("remaining"), anonymous.get("limits")
    if not isinstance(remaining, dict) or not isinstance(limits, dict):
        return None
    summary = [
        {"unit": unit, "granted": limits.get(unit), "available": left}
        for unit, left in remaining.items()
        if isinstance(limits.get(unit), int) and isinstance(left, int)
    ]
    return quota_sentences(summary)


def build_status_view(body: dict, *, profile: str, url: str | None) -> dict[str, Any]:
    """The status `view`: `{kind, size, markdown, object, at, seq}`."""
    lines = [_header(body)]
    # claude.ai reads ONLY the text block, so a failed probe is a line, not
    # just a `structuredContent` field: without it a network blip read as a
    # signed-in session with no org and no quota (review 2, lane A).
    identity_error = body.get("identity_error")
    if isinstance(identity_error, str) and identity_error.strip():
        lines.append(f"Identity check failed: {identity_error.strip()}")
    anonymous = body.get("anonymous")
    if isinstance(anonymous, dict) and anonymous.get("active"):
        quota = _anon_quota(anonymous)
    else:
        entitlements = body.get("entitlements")
        summary = entitlements.get("summary") if isinstance(entitlements, dict) else None
        quota = quota_sentences(summary)
    if quota:
        lines.append(
            f"{quota} Quota is per org and shared across the web app, the CLI and other chats."
        )
    else:
        entitlements_error = body.get("entitlements_error")
        if isinstance(entitlements_error, str) and entitlements_error.strip():
            lines.append(f"Quota could not be read: {entitlements_error.strip()}")
    line_3 = capability_line(profile, body.get("capabilities") or {})
    if line_3:
        lines.append(line_3)
    if url:
        lines.append(f"View in Keel: {url}")
    view: dict[str, Any] = {"kind": KIND_STATUS, "size": "receipt"}
    identity = body.get("identity") if isinstance(body.get("identity"), dict) else {}
    # The card is about the caller's account. The listed result carries no
    # org id (Q-2268), so its election object is the constant — every status
    # card in one session is about the same account and supersedes the last.
    view.update(election_key(identity.get("org_id") or "account"))
    view["markdown"] = "\n".join(lines) + "\n"
    return view
