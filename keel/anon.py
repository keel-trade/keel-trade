"""Anonymous claim-later tier — CLI instant start + claim (spec 05 R3/R4).

Instant start: the first CLI command that needs auth with NO stored
credentials auto-calls ``POST /v1/auth/anonymous``, prints the one-line
notice, stores the tokens (with the ``anon_org_id`` marker), and
proceeds. Never when credentials exist; never in hosted mode; CLI
surface only (agents on MCP surfaces have ``keel_auth_login``).

Claim: ``keel auth login`` detects the stored anon marker and, after the
OAuth flow succeeds, POSTs ``/v1/orgs/claim`` with a FRESH anon access
token (refreshed via the anon refresh token, which stays valid up to org
expiry) under the NEW account's credentials. The org re-owns to the new
account and the marker is cleared.

No client-side analytics anywhere in this module (DP2) — all telemetry
is server-side on the grant/claim endpoints.
"""

from __future__ import annotations

import os
import sys

import httpx

from keel.config import KeelConfig, load_config, save_config
from keel.errors import AuthError


_GRANT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)


def anon_auto_enabled() -> bool:
    """Instant start applies on the CLI surface unless explicitly disabled.

    ``KEEL_ANON_AUTO=0`` opts out (CI environments that want a hard auth
    failure); ``KEEL_ANON_AUTO=1`` forces it on for non-CLI local
    surfaces (e.g. direct SDK use in a notebook).
    """
    override = os.environ.get("KEEL_ANON_AUTO", "").strip()
    if override in ("0", "false"):
        return False
    if override in ("1", "true"):
        return True
    from keel.surface import current_surface

    return current_surface() == "cli"


def is_anon(config: KeelConfig | None = None) -> bool:
    """True when the stored credentials came from an anonymous grant."""
    config = config or load_config()
    return bool(config.api_key) and bool(config.anon_org_id)


def _default_client_name() -> str:
    from keel.auth import _default_client_name as real

    return real()


def anonymous_start(
    api_url: str | None = None,
    *,
    client_name: str | None = None,
    quiet: bool = False,
) -> KeelConfig:
    """Call the anonymous grant, persist tokens + marker, print the notice.

    Raises AuthError with the server's instructive message when the tier
    is disabled (503 kill switch), rate-limited (429), or unreachable —
    the caller surfaces it next to the normal login instructions.
    """
    config = load_config()
    if config.api_key:
        raise AuthError(
            "Credentials already exist — anonymous start is only for the "
            "no-credentials first run. Run `keel auth logout` first if you "
            "really want a fresh anonymous workspace."
        )
    resolved_url = (api_url or config.api_url).rstrip("/")
    resolved_name = client_name or _default_client_name()

    try:
        resp = httpx.post(
            f"{resolved_url}/v1/auth/anonymous",
            json={"client_name": resolved_name},
            timeout=_GRANT_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise AuthError(
            f"Could not reach {resolved_url} for anonymous start: {e}. "
            "Run `keel auth login` to authenticate normally.",
            retryable=True,
        ) from e

    if resp.status_code != 201:
        detail: object = None
        try:
            detail = resp.json().get("detail")
        except Exception:  # noqa: BLE001 — non-JSON error body
            detail = resp.text
        message = detail.get("message") if isinstance(detail, dict) else str(detail)
        raise AuthError(
            f"Anonymous access unavailable ({resp.status_code}): {message} ",
            suggestion="Run `keel auth login` to authenticate normally.",
        )

    data = resp.json()
    config.api_key = data["access_token"]
    config.refresh_token = data.get("refresh_token")
    config.client_name = resolved_name
    config.api_url = resolved_url
    config.anon_org_id = data.get("org_id")
    expires_in = data.get("expires_in")
    if expires_in:
        from datetime import datetime, timedelta, timezone

        config.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    # Spec 09 CL-3c: persist the org's GC instant so the <48h expiry
    # notice is a config read — no API call on any later command.
    org_expires_raw = data.get("org_expires_at")
    if org_expires_raw:
        from keel.config import _parse_expires_at

        config.anon_org_expires_at = _parse_expires_at(org_expires_raw)
    save_config(config)

    if not quiet:
        notice = data.get("notice") or (
            "Running anonymously — run `keel auth login` to keep your work."
        )
        print(notice, file=sys.stderr)
    return config


def _fresh_anon_access_token(api_url: str, refresh_token: str) -> str | None:
    """Rotate the anon refresh token for a fresh access token (claim proof).

    Returns None when the anon org has expired (refresh 401) or the
    request fails — the caller degrades to a warning, never a crash.
    """
    try:
        resp = httpx.post(
            f"{api_url.rstrip('/')}/v1/auth/oauth/refresh",
            json={"refresh_token": refresh_token},
            timeout=_GRANT_TIMEOUT,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    return resp.json().get("access_token")


def claim_anon_org(anon_access_token: str, *, set_default: bool = True) -> dict:
    """POST /v1/orgs/claim with the CURRENT (new, real) credentials.

    ``set_default=False`` is the existing-account attach (spec 09 CL-8):
    the claimant's current default org is preserved.
    """
    from keel.client import KeelClient

    client = KeelClient()
    try:
        body: dict = {"anon_token": anon_access_token}
        if not set_default:
            body["set_default"] = False
        return client.post("/v1/orgs/claim", json=body)
    finally:
        client.close()


def _set_org_context(org_id: str | None) -> None:
    """Spec 09 CL-9: post-claim continuation. OAuth tokens are org-bound
    at mint (BEFORE the claim), so the client pins the claimed org via
    X-Org-Id — otherwise the CLI would keep operating on the pre-claim
    org while the claimed work lives elsewhere."""
    if not org_id:
        return
    config = load_config()
    config.active_org_id = org_id
    save_config(config)


def _count_anon_work(api_url: str, anon_access_token: str) -> dict | None:
    """Pre-claim counts for the CL-8 prompt, read AS the anon principal.

    The anon caps (≤3 strategies / ≤15 backtests) make one unpaginated
    page exact. Returns None on any failure — the prompt degrades to
    countless wording, never blocks the flow."""
    base = api_url.rstrip("/")
    headers = {"Authorization": f"Bearer {anon_access_token}"}
    try:
        counts: dict[str, int] = {}
        for key, path in (("strategies", "/v1/strategies"), ("backtests", "/v1/backtests")):
            resp = httpx.get(f"{base}{path}", headers=headers, timeout=_GRANT_TIMEOUT)
            if resp.status_code != 200:
                return None
            data = resp.json().get("data")
            if not isinstance(data, list):
                return None
            counts[key] = len(data)
        return counts
    except Exception:  # noqa: BLE001 — counts are prompt sugar, never load-bearing
        return None


def _account_has_work() -> bool:
    """CL-8 signal: does the signed-in account's org already hold work?

    Content-based, no timing heuristics: ≥1 strategy → existing-with-work
    → confirm before attaching. A check FAILURE also reads as
    existing-with-work — the wrong silent default-flip (hiding an
    existing user's workspace) is the harm CL-8 exists to prevent, so
    uncertainty resolves toward the confirm, never past it."""
    from keel.client import KeelClient

    try:
        client = KeelClient()
        try:
            resp = client.get("/v1/strategies")
        finally:
            client.close()
        data = resp.get("data") if isinstance(resp, dict) else None
        if not isinstance(data, list):
            return True
        return len(data) > 0
    except Exception:  # noqa: BLE001 — uncertainty resolves toward the confirm
        return True


def _store_pending_claim(prior_config: KeelConfig, counts: dict | None, *, declined: bool) -> dict:
    """Persist the deferred-claim material (spec 09 CL-8) — the same file
    and permission class that held the anon credentials before login."""
    pending = {
        "anon_org_id": prior_config.anon_org_id,
        "refresh_token": prior_config.refresh_token,
        "api_url": prior_config.api_url,
        "counts": counts,
        "org_expires_at": (
            prior_config.anon_org_expires_at.isoformat()
            if prior_config.anon_org_expires_at
            else None
        ),
        "declined": declined,
    }
    config = load_config()
    config.pending_claim = pending
    save_config(config)
    return pending


def _clear_pending_claim() -> None:
    config = load_config()
    if config.pending_claim is not None:
        config.pending_claim = None
        save_config(config)


def pending_claim_public(pending: dict | None) -> dict | None:
    """The envelope-safe projection of a pending claim (spec 09 §1).

    The stored pending record carries the anon REFRESH TOKEN — a bearer
    capability that must never ride a tool result, status body, or any
    other surfaced payload (it would land in agent transcripts and logs).
    Everything a caller/agent needs to ask the user is here; the claim
    material itself stays in ~/.keel/config.yaml only."""
    if not pending:
        return None
    return {
        "anon_org_id": pending.get("anon_org_id"),
        "counts": pending.get("counts"),
        "org_expires_at": pending.get("org_expires_at"),
        "declined": pending.get("declined"),
    }


def _prompt_counts_line(counts: dict | None) -> str:
    if counts is None:
        return "the strategies and backtests in this anonymous workspace"
    n, m = counts.get("strategies", 0), counts.get("backtests", 0)
    strat = f"{n} strategy" if n == 1 else f"{n} strategies"
    bt = f"{m} backtest" if m == 1 else f"{m} backtests"
    return f"{strat} and {bt}"


def resolve_pending_claim(attach: bool, *, quiet: bool = False) -> dict | None:
    """Resolve a deferred CL-8 claim WITHOUT re-running OAuth.

    Called by `keel auth login --attach-anon/--no-attach-anon` and by
    `keel_auth_login` with `attach_anonymous_work` when a pending claim
    exists. Returns the claim result, a status dict, or None when there
    is nothing pending."""
    config = load_config()
    pending = config.pending_claim
    if not pending:
        return None

    if not attach:
        pending["declined"] = True
        config.pending_claim = pending
        save_config(config)
        expiry = pending.get("org_expires_at") or "its 7-day expiry"
        if not quiet:
            print(
                "Anonymous workspace kept aside — it stays claimable until "
                f"{expiry}. Attach it any time with `keel auth login --attach-anon`.",
                file=sys.stderr,
            )
        return {"pending_claim_declined": True, "claimable_until": pending.get("org_expires_at")}

    fresh = _fresh_anon_access_token(
        pending.get("api_url") or config.api_url, pending["refresh_token"]
    )
    if fresh is None:
        _clear_pending_claim()
        if not quiet:
            print(
                "Note: the anonymous workspace could not be claimed (it may "
                "have expired). Signed-in work is unaffected.",
                file=sys.stderr,
            )
        return {"pending_claim_expired": True}

    try:
        # Existing-account attach: preserve the claimant's default org.
        result = claim_anon_org(fresh, set_default=False)
    except Exception as e:  # noqa: BLE001 — best-effort; pending survives for retry
        if not quiet:
            print(
                f"Note: claiming the anonymous workspace failed: {e}. "
                "Retry with `keel auth login --attach-anon`.",
                file=sys.stderr,
            )
        return {"pending_claim_failed": True, "error": str(e)}

    _set_org_context(result.get("org_id"))
    _clear_pending_claim()
    if not quiet:
        print(result.get("message") or "Anonymous workspace claimed.", file=sys.stderr)
    return result


def maybe_claim_after_login(
    prior_config: KeelConfig,
    *,
    quiet: bool = False,
    attach_decision: bool | None = None,
    confirm=None,
) -> dict | None:
    """Auto-claim (spec 05 R3 + spec 09 CL-8): called by
    ``keel.auth.browser_login`` after the OAuth flow persisted the NEW
    account's tokens.

    ``prior_config`` is the config snapshot taken BEFORE login. When it
    carries an anon marker, refresh the anon token (proof must be a valid
    access token) and claim:

    * Fresh/empty account → claim SILENTLY (founder 2026-08-21), M7.2
      Option-C default flip preserved.
    * Account that already has work → ONE confirmation (CL-8):
      ``attach_decision`` (explicit True/False, e.g. from the MCP arg or
      the CLI flag) wins; else ``confirm(counts)`` (the CLI TTY prompt);
      else the claim DEFERS — pending material persists and the return
      value is ``{"pending_claim": {...}}`` for the caller to surface.

    Returns the claim response, the pending descriptor, or None.
    """
    if not (prior_config.anon_org_id and prior_config.refresh_token):
        return None

    def _clear_marker() -> None:
        # The marker refers to the pre-login tokens, which the login just
        # replaced — whatever the claim outcome, keeping it would make
        # is_anon()/keel_status lie about the signed-in account. The
        # expiry-notice fields follow the marker (spec 09 CL-3c); any
        # deferred-claim material lives in pending_claim instead.
        config = load_config()
        if config.anon_org_id or config.anon_org_expires_at:
            config.anon_org_id = None
            config.anon_org_expires_at = None
            config.anon_expiry_notice_at = None
            save_config(config)

    fresh = _fresh_anon_access_token(prior_config.api_url, prior_config.refresh_token)
    if fresh is None:
        _clear_marker()
        if not quiet:
            print(
                "Note: your anonymous workspace could not be claimed (it may "
                "have expired). Signed-in work is unaffected.",
                file=sys.stderr,
            )
        return None

    counts = _count_anon_work(prior_config.api_url, fresh)
    existing = _account_has_work()

    decision: bool | None
    if not existing:
        decision = True  # fresh/empty account: silent claim, Option-C.
    elif attach_decision is not None:
        decision = attach_decision
    elif confirm is not None:
        decision = bool(confirm(counts))
    else:
        decision = None  # defer (MCP / non-TTY): the caller asks the user.

    if decision is None:
        pending = _store_pending_claim(prior_config, counts, declined=False)
        _clear_marker()
        # Surfaced projection only — the refresh token stays in the config
        # file (spec 09 §1: the capability never rides an envelope).
        return {"pending_claim": pending_claim_public(pending)}

    if decision is False:
        _store_pending_claim(prior_config, counts, declined=True)
        _clear_marker()
        expiry = (
            prior_config.anon_org_expires_at.isoformat()
            if prior_config.anon_org_expires_at
            else "its 7-day expiry"
        )
        if not quiet:
            print(
                "Anonymous workspace kept aside — it stays claimable until "
                f"{expiry}. Attach it any time with `keel auth login --attach-anon`.",
                file=sys.stderr,
            )
        return {
            "pending_claim_declined": True,
            "claimable_until": (
                prior_config.anon_org_expires_at.isoformat()
                if prior_config.anon_org_expires_at
                else None
            ),
        }

    try:
        # Fresh account keeps Option-C (set_default). Existing-account
        # attach preserves the claimant's current default (CL-8).
        result = claim_anon_org(fresh, set_default=not existing)
    except Exception as e:  # noqa: BLE001 — claim is best-effort post-login enrichment
        if existing:
            # The user explicitly chose to attach — keep the material so
            # `keel auth login --attach-anon` can retry.
            _store_pending_claim(prior_config, counts, declined=False)
            _clear_marker()
            if not quiet:
                print(
                    f"Note: claiming your anonymous workspace failed: {e}. "
                    "Retry with `keel auth login --attach-anon`.",
                    file=sys.stderr,
                )
            return {"pending_claim_failed": True, "error": str(e)}
        _clear_marker()
        if not quiet:
            print(f"Note: claiming your anonymous workspace failed: {e}", file=sys.stderr)
        return None

    _set_org_context(result.get("org_id"))
    _clear_marker()
    _clear_pending_claim()
    if not quiet:
        message = result.get("message") or "Anonymous workspace claimed."
        print(message, file=sys.stderr)
    return result


_EXPIRY_NOTICE_WINDOW_HOURS = 48
_EXPIRY_NOTICE_INTERVAL_SECONDS = 3600


def maybe_print_expiry_notice(now=None) -> bool:
    """Spec 09 CL-3c: ONE stderr line when the anon org expires in <48h.

    At most once per hour (config-stamped). Reads only stored state — no
    API call. Returns True when a notice printed. Pre-spec-09 anon
    configs carry no stored expiry and stay silent (degrade, never guess).
    """
    from datetime import datetime, timezone

    config = load_config()
    if not (is_anon(config) and config.anon_org_expires_at):
        return False
    now = now or datetime.now(timezone.utc)
    remaining = config.anon_org_expires_at - now
    remaining_seconds = remaining.total_seconds()
    if remaining_seconds <= 0 or remaining_seconds > _EXPIRY_NOTICE_WINDOW_HOURS * 3600:
        return False
    last = config.anon_expiry_notice_at
    if last is not None and (now - last).total_seconds() < _EXPIRY_NOTICE_INTERVAL_SECONDS:
        return False
    hours = max(1, int(remaining_seconds // 3600))
    print(
        f"Your anonymous workspace expires in {hours}h — run `keel auth login` to keep your work.",
        file=sys.stderr,
    )
    config.anon_expiry_notice_at = now
    save_config(config)
    return True


__all__ = [
    "anon_auto_enabled",
    "anonymous_start",
    "claim_anon_org",
    "is_anon",
    "maybe_claim_after_login",
    "maybe_print_expiry_notice",
    "pending_claim_public",
    "resolve_pending_claim",
]
