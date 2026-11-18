"""Finite reporting negotiation; no financial values are calculated here."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from keel.errors import KeelError


CONTRACT = "account_performance.v1"
HEADER = "X-Keel-Performance-Contract"
ROUTES = re.compile(
    r"/v1/(?:(?:deployments|live)/(?:portfolio/(?:equity|summary)|[^/]+/(?:equity|stats|daily-pnl))"
    r"|managed/links/[^/]+/(?:live/(?:portfolio|equity)|measured-history/[^/]+))"
)
_SELECTED: set[str] = set()
_MONEY = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?")
_HASH = re.compile(r"[0-9a-f]{64}")


class ActivationWitness(Exception):
    """Internal exact HTTP 409 witness."""


def origin(api_url: str) -> str:
    url = urlsplit(api_url)
    if url.username is not None or url.password is not None:
        raise KeelError("API URLs must not contain credentials.", error_code="invalid_api_url")
    scheme = url.scheme.lower()
    host = (url.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = url.port
    except ValueError:
        raise KeelError("API URL has an invalid port.", error_code="invalid_api_url") from None
    suffix = "" if port is None or port == {"http": 80, "https": 443}.get(scheme) else f":{port}"
    return f"{scheme}://{host}{suffix}{url.path.rstrip('/')}"


def unavailable() -> KeelError:
    return KeelError(
        "Measured results require a compatible server response. Refresh to retry.",
        error_code="performance_contract_unavailable",
        retryable=False,
    )


def selected(api_url: str) -> bool:
    return origin(api_url) in _SELECTED


def select(api_url: str) -> None:
    _SELECTED.add(origin(api_url))


def activation(status: int, body: Any) -> bool:
    return (
        status == 409
        and isinstance(body, dict)
        and body.get("code") == "performance_contract_refresh_required"
        and body.get("supported_contract_version") == CONTRACT
    )


def is_performance(value: Any) -> bool:
    return isinstance(value, dict) and value.get("contract_version") == CONTRACT


def validate(value: Any) -> dict:
    try:
        return _validate(value)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise unavailable() from None


def _validate(value: Any) -> dict:
    """Validate consumer fields, preserving exact decimal strings and nulls."""

    def require(condition):
        if not condition:
            raise unavailable()

    def money(v):
        return v is None or isinstance(v, str) and _MONEY.fullmatch(v) is not None

    def digest(v):
        return isinstance(v, str) and _HASH.fullmatch(v) is not None

    def timestamp(v):
        require(isinstance(v, str))
        try:
            parsed = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise unavailable() from None
        require(parsed.tzinfo is not None)

    def strings(v):
        require(isinstance(v, list) and all(isinstance(item, str) for item in v))

    def integer(v, minimum=0):
        require(type(v) is int and v >= minimum)

    def components(rows):
        require(isinstance(rows, list))
        for row in rows:
            require(isinstance(row, dict))
            for key in ("account_id", "reporting_interval_id", "basis_id"):
                require(isinstance(row[key], str) and bool(row[key]))
            integer(row["binding_revision"], 1)

    def account(row):
        require(isinstance(row, dict) and row["kind"] == "account")
        for key in ("scope_id", "account_id", "deployment_id", "reporting_interval_id"):
            require(isinstance(row[key], str) and bool(row[key]))
        require(row["scope_id"] == row["deployment_id"] == row["reporting_interval_id"])
        timestamp(row["start"])
        if row["end"] is not None:
            timestamp(row["end"])

    def returns(row):
        require(isinstance(row, dict))
        quality = row.get("return_quality")
        require(quality in {"valuation_linked", "estimated", "unavailable"})
        require("exact_twr_pct" in row and "estimated_return_pct" in row)
        exact, estimate = row["exact_twr_pct"], row["estimated_return_pct"]
        require(money(exact) and money(estimate))
        require((exact is not None) == (quality == "valuation_linked"))
        require((estimate is not None) == (quality == "estimated"))

    def perps(row):
        require(isinstance(row, dict))
        require(row.get("performance_basis_id") == "perps_usdc_performance.v1")
        require(row.get("performance_scope_id") == "hyperliquid.main_perps.v1")
        require(digest(row.get("scope_hash")) and digest(row.get("input_revision_token")))
        timestamp(row["interval_start"])
        timestamp(row["interval_end"])
        strings(row["reason_codes"])
        require(row["evidence_manifest_ref"] is None or digest(row["evidence_manifest_ref"]))
        require(row["return_method_id"] is None or isinstance(row["return_method_id"], str))
        require(
            row["return_equivalence_proof_ref"] is None
            or digest(row["return_equivalence_proof_ref"])
        )
        require(row.get("quality") in {"complete", "pending", "held", "unavailable"})
        require("pnl" in row and money(row["pnl"]))
        require((row["pnl"] is not None) == (row["quality"] == "complete"))
        quality = row.get("return_quality")
        require(quality in {"exact", "estimated", "unavailable"})
        require(
            all(
                k in row and money(row[k])
                for k in ("return_pct", "exact_twr_pct", "estimated_return_pct")
            )
        )
        exact, estimate, result = (
            row["exact_twr_pct"],
            row["estimated_return_pct"],
            row["return_pct"],
        )
        if quality == "unavailable":
            require(exact is None and estimate is None and result is None)
        else:
            require(result is not None and result == (exact if quality == "exact" else estimate))
            require((estimate if quality == "exact" else exact) is None)
            require(isinstance(row.get("return_method_id"), str) and bool(row["return_method_id"]))
            require(digest(row.get("return_equivalence_proof_ref")))

    require(is_performance(value))
    require(value.get("numeraire") == "USDC")
    require(
        isinstance(value.get("scope"), dict)
        and value["scope"].get("kind") in {"account", "portfolio"}
    )
    require(digest(value.get("revision_token")) and digest(value.get("policy_sha256")))
    require(value.get("performance_quality") in {"complete", "pending", "partial", "unavailable"})
    require(value.get("value_quality") in {"current", "stale", "unavailable"})
    scope = value["scope"]
    if scope["kind"] == "account":
        account(scope)
    else:
        require(isinstance(scope["scope_id"], str) and digest(scope["scope_hash"]))
        require(scope["population"] == "current_active_accounts")
        require(isinstance(scope["accounts"], list) and value["quote"] is None)
        for row in scope["accounts"]:
            account(row)
        components(scope["basis_components"])
    components(value["basis_components"])
    require(value["basis_id"] is None or isinstance(value["basis_id"], str))
    if value["basis_binding_revision"] is not None:
        integer(value["basis_binding_revision"], 1)
    require(isinstance(value["method_id"], str) and bool(value["method_id"]))
    timestamp(value["generated_at"])
    strings(value["reason_codes"])
    integer(value["estimated_interval_count"])
    if value["first_estimated_at"] is not None:
        timestamp(value["first_estimated_at"])
    coverage = value["coverage"]
    require(isinstance(coverage, dict) and type(coverage["complete"]) is bool)
    timestamp(coverage["interval_start"])
    timestamp(coverage["interval_end"])
    if coverage["classified_through"] is not None:
        timestamp(coverage["classified_through"])
    integer(coverage["unresolved_count"])
    if value["baseline"] is not None:
        timestamp(value["baseline"]["observed_at"])
        require(value["baseline"]["value"] is not None and money(value["baseline"]["value"]))
    if value["quote"] is not None:
        quote = value["quote"]
        require(isinstance(quote, dict) and quote["value"] is not None and money(quote["value"]))
        require(quote["quality"] in {"current", "stale", "unavailable"})
        for key in ("basis_id", "representation_id"):
            require(isinstance(quote[key], str))
        for key in ("observed_at", "observed_interval_start", "observed_interval_end"):
            timestamp(quote[key])
        strings(quote["reason_codes"])
    if value["portfolio_context"] is not None:
        context = value["portfolio_context"]
        require(isinstance(context, dict) and digest(context["scope_hash"]))
        require(money(context["asof_value"]))
        require(context["asof_value_quality"] in {"current", "stale", "unavailable"})
        require(context["return_boundary_status"] in {"aligned", "unaligned", "unavailable"})
        for key in ("oldest_observed_at", "newest_observed_at"):
            if context[key] is not None:
                timestamp(context[key])
    returns(value)
    perps(value.get("perps_performance"))
    require(isinstance(value.get("points"), list))
    points = value["points"]
    if value["performance_tip"] is not None:
        tip = value["performance_tip"]
        require(isinstance(tip, dict) and tip["complete"] is True and digest(tip["revision_token"]))
        timestamp(tip["interval_start"])
        timestamp(tip["covered_through"])
        points = [*points, tip]
    for point in points:
        returns(point)
        timestamp(point.get("observed_at"))
        require(point.get("value") is not None and money(point["value"]))
        require(point.get("validity") in {"complete", "pending", "held", "unavailable"})
        require(
            point["factor_kind"] in {"no_flow", "flow_boundary", "modified_dietz", "unavailable"}
        )
        require(isinstance(point["segment_id"], str))
        integer(point["estimated_interval_count"])
        for key in ("pnl", "wealth", "flow"):
            require(money(point[key]))
        require("return_pct" in point and money(point["return_pct"]))
        require(
            point["return_pct"]
            == (
                point["exact_twr_pct"]
                if point["return_quality"] == "valuation_linked"
                else point["estimated_return_pct"]
            )
        )
        perps(point.get("perps_performance"))
    return value
