"""The listed-surface HEAD fixtures and the driver that captures them (S06 AC-06-7).

Paper trading v2 changes nothing a hosted-connector client sees while
``KEEL_LISTED_PAPER`` is off (S06 §3.4.2, R-60; PT2-28). Two fixtures pin that
off state to the paper build's base commit, ``028a89104``, and were captured
there before any paper commit touched ``packages/keel-trade``
(``M8-listed-head-fixture``):

* ``fixtures/listed_surface_head.json`` — the served listed surface: the
  serialized ``tools/list`` (each tool's wire form: name, title, description,
  input and output schemas, annotations, ``_meta``), the listed instructions
  and every served listed card resource (``ui://``), each with its wire record
  and the sha256 and length of the bytes it serves (a card is ~200 KB of
  HTML; the digest is the byte-equality the fixture asserts).
* ``fixtures/listed_outputs_head.json`` — the listed read tools' outputs over
  HEAD's keel-api responses for one org with one ``real`` deployment: the
  structured content, text and ``_meta`` of ``keel_live_monitor`` with no
  arguments, with ``view=portfolio`` and with each of its eleven
  per-deployment views, of ``keel_account_status`` and of
  ``keel_plan_usage`` (fifteen calls), each with the requests it sent. The
  faked keel-api it was driven against is stored beside the outputs under
  ``world``: every body was built from keel-api's own response models at the
  base commit and serialized as FastAPI serves them (``world.provenance``).

The flag-off test (``M8-listed-flag-off``, ``tests/test_listed_outputs_head.py``)
drives the same calls through :func:`drive_listed_outputs` and compares. A
later NON-paper change to the listed surface or these outputs re-captures in
its own commit (``python tests/_listed_head_fixtures.py capture
tests/fixtures/listed_outputs_head.json``, re-using the world it holds, from the
SDK venv, ``env -u PYTHONPATH``, then ``scripts/ci/prettier-pinned.sh --write``
over both files, as the pre-commit hook would); no paper commit touches either
file. Compare parsed JSON, never raw bytes: prettier owns the layout.

Not a test module (no ``test_`` prefix) and not a conftest: the capture and
the comparison import the same functions, so the fixture and its check cannot
read the surface two different ways.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch


FIXTURES = Path(__file__).resolve().parent / "fixtures"
SURFACE_FIXTURE = FIXTURES / "listed_surface_head.json"
OUTPUTS_FIXTURE = FIXTURES / "listed_outputs_head.json"

#: The base commit both fixtures were captured at (PT2-28).
BASE_COMMIT = "028a89104"

#: The faked keel-api's host; nothing else is reachable while the driver runs.
API_HOST = "api.listed-head.test"
API = f"https://{API_HOST}"

#: Every environment variable that can move what the listed server serves or
#: renders. The driver sets the listed + hosted pair and clears the rest, so a
#: developer's shell (an exported ``KEEL_APP_URL``, a ``KEEL_TOOLSETS``) can
#: never leak into a capture or a comparison.
_PINNED_ENV = {
    "KEEL_SERVER_PROFILE": "listed",
    "KEEL_EXECUTION_MODE": "hosted",
    "KEEL_ANON_AUTO": "0",
}
_CLEARED_ENV = (
    "KEEL_TOOLSETS",
    "KEEL_LISTED_CLIENT",
    "KEEL_LISTED_PAPER",
    "KEEL_LISTED_PAPER_HOSTS",
    "KEEL_APP_URL",
    "KEEL_API_URL",
    "KEEL_API_KEY",
    "KEEL_SHARE_URL_ROOT",
    "KEEL_AGENT_MODE",
    "KEEL_ALLOW_DIRECT_DEPLOY",
)

#: The eleven per-deployment views, in the listed schema's spelling (what a
#: hosted client sends; the handler maps the renamed ones back).
PER_DEPLOYMENT_VIEWS = (
    "overview",
    "positions",
    "equity",
    "pnl",
    "stats",
    "weights",
    "weights-history",
    "executions",
    "order_history",
    "trade_history",
    "funding",
)


@contextmanager
def listed_hosted_env(env: dict[str, str] | None = None) -> Iterator[None]:
    """The directory registration's environment, isolated and restored.

    ``env`` is applied after the pinned and cleared keys (each must be one of
    them), so an arm can set the per-host listed paper switch (FDR-10).

    Also redirects ``~/.keel`` to a temp dir and resets the calling surface,
    the two process globals the suite's conftest isolates, so the driver
    behaves the same under pytest and as a standalone capture.
    """
    import keel.surface

    keys = (*_PINNED_ENV, *_CLEARED_ENV)
    saved = {k: os.environ.get(k) for k in keys}
    saved_surface = keel.surface._SURFACE
    for k in _CLEARED_ENV:
        os.environ.pop(k, None)
    os.environ.update(_PINNED_ENV)
    if env:
        assert set(env) <= set(keys), f"unisolated env keys: {sorted(set(env) - set(keys))}"
        os.environ.update(env)
    keel.surface._SURFACE = None
    with tempfile.TemporaryDirectory(prefix="keel-listed-head-") as tmp:
        try:
            with (
                patch("keel.config.CONFIG_FILE", Path(tmp) / "config.yaml"),
                patch("keel.config.CONFIG_DIR", Path(tmp)),
            ):
                yield
        finally:
            keel.surface._SURFACE = saved_surface
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _bytes(content: str | bytes) -> bytes:
    return content.encode("utf-8") if isinstance(content, str) else content


def _wire(model: Any) -> Any:
    """An MCP wire model as JSON, the way the server serializes it."""
    return model.model_dump(by_alias=True, mode="json", exclude_none=True)


# ─── The served listed surface ──────────────────────────────────────────


def listed_surface_snapshot(env: dict[str, str] | None = None) -> dict[str, Any]:
    """The listed surface a hosted client is served, built in-process exactly
    as the directory registration runs it (``create_server()`` under
    ``KEEL_SERVER_PROFILE=listed`` + ``KEEL_EXECUTION_MODE=hosted``)."""
    from keel.mcp.server import create_server
    from keel.tools.outcomes import _bootstrap

    with listed_hosted_env(env):
        _bootstrap()
        server = create_server()
        tools = sorted(
            (_wire(t.to_mcp_tool()) for t in asyncio.run(server.list_tools())),
            key=lambda t: t["name"],
        )
        cards: list[dict[str, Any]] = []
        for resource in asyncio.run(server.list_resources()):
            uri = str(resource.uri)
            if not uri.startswith("ui://"):
                continue
            read = asyncio.run(resource.read())
            cards.append(
                {
                    "uri": uri,
                    "resource": _wire(resource.to_mcp_resource()),
                    "contents": [
                        {
                            "sha256": hashlib.sha256(_bytes(c.content)).hexdigest(),
                            "length": len(_bytes(c.content)),
                            "mime_type": c.mime_type,
                            "meta": c.meta,
                        }
                        for c in read.contents
                    ],
                }
            )
        cards.sort(key=lambda c: c["uri"])
        return {
            "tool_count": len(tools),
            "tools": tools,
            "instructions": server.instructions or "",
            "card_resources": cards,
        }


# ─── The listed read tools' outputs over a faked keel-api ───────────────


#: The money aggregate that sums ONE named class (S01 §3.10, R-71; PT2-28).
PORTFOLIO_SUMMARY_PATH = "/v1/deployments/portfolio/summary"


def empty_portfolio_summary(capital_class: str) -> dict[str, Any]:
    """keel-api's ``PortfolioSummaryResponse`` for a class the org runs nothing
    in, echoing the class it summed (S01 §3.10)."""
    return {
        "active_count": 0,
        "capital_class": capital_class,
        "deployments": [],
        "total_account_value": 0.0,
        "total_count": 0,
        "total_fees": 0.0,
        "total_funding": 0.0,
        "total_realized_pnl": 0.0,
        "total_unrealized_pnl": None,
    }


class FakedKeelApi:
    """A respx side effect serving ``world["routes"]`` by exact (method, path),
    recording every request it receives (method, path, query params in the
    order sent). An unknown route answers 404 and is recorded like any other.

    The portfolio summary honours ``capital_class`` as the paper build's
    keel-api does (S01 §3.10, IC-01-9; PT2-28): an absent class reads as
    ``real``; a route may carry ``capital_class`` to serve that class only; a
    class no route serves answers the empty summary echoing it; ``all``
    answers 422. HEAD's world (one unkeyed ``real`` summary) is served
    unchanged to a ``real`` or classless read, so the base-commit outputs
    replay byte for byte.
    """

    def __init__(self, world: dict[str, Any]) -> None:
        self._routes = {
            (r["method"], r["path"]): r for r in world["routes"] if "capital_class" not in r
        }
        self._classed = {
            (r["method"], r["path"], r["capital_class"]): r
            for r in world["routes"]
            if "capital_class" in r
        }
        self.requests: list[dict[str, Any]] = []
        #: Per request, whether it carried the agent-telemetry marker (S06
        #: §3.5.1, R-61), kept apart so ``requests`` stays the base commit's
        #: shape.
        self.telemetry: list[bool] = []

    def _summary(self, method: str, capital_class: str) -> tuple[int, Any]:
        if capital_class not in ("real", "practice"):
            return 422, {"title": "Unprocessable Entity", "status": 422, "detail": "capital_class"}
        route = self._classed.get((method, PORTFOLIO_SUMMARY_PATH, capital_class))
        if route is None and capital_class == "real":
            route = self._routes.get((method, PORTFOLIO_SUMMARY_PATH))
        if route is None:
            return 200, empty_portfolio_summary(capital_class)
        return route["status"], route["body"]

    def __call__(self, request: Any) -> Any:
        import httpx

        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": [[k, v] for k, v in request.url.params.multi_items()],
            }
        )
        self.telemetry.append(request.headers.get("X-Keel-Agent-Telemetry") == "1")
        if request.url.path == PORTFOLIO_SUMMARY_PATH:
            status, body = self._summary(
                request.method, request.url.params.get("capital_class") or "real"
            )
            return httpx.Response(status, json=body)
        route = self._routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(
                404, json={"title": "Not Found", "status": 404, "detail": "no route"}
            )
        return httpx.Response(route["status"], json=route["body"])


#: The paper build's venue blocks (S01 IC-01-9).
REAL_VENUE = {"code": "HL", "capital_class": "real"}
PRACTICE_VENUE = {"code": "PAPER", "capital_class": "practice"}

#: The paper slot balance the paper build's entitlements carry (S01 §3.12,
#: IC-01-10): a capacity unit, never a backtest unit.
PAPER_SLOT_BALANCE = {
    "available": 19,
    "cap_current": 1,
    "enabled": None,
    "granted": 20,
    "period": None,
    "reserved": 0,
    "resets_at": None,
    "spent": 1,
    "type": "capacity",
    "unit": "paper_strategies_max",
    "unlimited": False,
}

#: The practice deployment :func:`paper_build_world` adds beside ``dep_real``.
PRACTICE_DEPLOYMENT = "dep_paper"


#: A paper fill's disclosure as the trades read serves it (S01 §3.10, PT2-49):
#: present only on a practice fill. keel-api leaves both keys OFF the wire
#: when null (``TradeResponse.disclosure_mid`` / ``disclosure_inception``,
#: ``exclude_if=_absent_when_null``, services/keel-api/src/schemas/live.py:961-962),
#: so a real trade row is byte for byte the base commit's.
PRACTICE_FILL_DISCLOSURE = {"disclosure_mid": 101.25, "disclosure_inception": False}

#: Where keel-api serves the venue block, per ``keel_live_monitor`` view, by
#: the response model each route declares (services/keel-api/src/routers/live.py
#: and schemas/live.py at the paper build). ONLY the detail row carries it;
#: every per-view payload is the base commit's shape, so a client learns a
#: run's class from the detail row and from nothing else (QA B1/V1).
VENUE_BY_VIEW: dict[str, str] = {
    # GET /v1/deployments/{id} → DeploymentResponse.venue (schemas/live.py:393)
    "overview": "DeploymentResponse.venue",
    # GET …/positions → ExpandedPositionsEnvelope (schemas/live.py:526): no venue
    "positions": "none",
    # GET …/equity → EquityCurveResponse (schemas/live.py:790): no venue
    "equity": "none",
    # GET …/daily-pnl → DailyPnlResponse (schemas/live.py:928): no venue
    "pnl": "none",
    # GET …/stats → LiveStatsResponse (schemas/live.py:840): no venue
    "stats": "none",
    # GET …/weights → WeightsEnvelope (schemas/live.py:575): no venue
    "weights": "none",
    # GET …/weights/history → WeightHistoryResponse (schemas/live.py:970): no venue
    "weights-history": "none",
    # GET …/executions → list[EnhancedExecutionResponse] (schemas/live.py:1199):
    # no top-level venue (a receipt's nested source_cutoff.venue is HEAD data)
    "executions": "none",
    # GET …/orders → list[OrderResponse] (schemas/live.py:541): no venue
    "order_history": "none",
    # GET …/trades → PaginatedResponse of TradeResponse (schemas/live.py:932):
    # no venue; a practice fill carries the two disclosures
    "trade_history": "none",
    # GET …/funding → FundingResponse (schemas/live.py:984): no venue
    "funding": "none",
}


def paper_build_world(head: dict[str, Any]) -> dict[str, Any]:
    """The base commit's world as the paper build's keel-api serves it, plus
    one ``practice`` deployment (S01 IC-01-9, §3.10; S06 AC-06-7's output arm).

    Each body has the shape keel-api's response model gives it
    (:data:`VENUE_BY_VIEW`): the ``dep_real`` DETAIL row carries the real
    venue block and the peer fields ``health``/``health_detail``/``parity``/
    ``graduation`` (null on a real row); no other per-deployment payload
    carries a venue block, and the real trades rows carry no disclosure key
    (null is left off the wire). ``/v1/entitlements`` and ``/v1/me`` carry a
    ``paper_strategies_max`` balance and ``/v1/me`` ``paper_enabled``; the
    real portfolio summary echoes its class (``PortfolioSummaryResponse``,
    schemas/live.py:1325-1338; its ``DeploymentSummary`` rows carry no venue,
    :1313-1322). Every ``dep_real`` read has a ``dep_paper`` twin: the detail
    row's venue block names ``practice``, its trades rows carry
    :data:`PRACTICE_FILL_DISCLOSURE`, and a practice summary lists it.
    Nothing in the base-commit world is removed or changed in value.

    Until 2026-10-12 this world put the venue block on EVERY per-deployment
    payload, which keel-api never serves; the listed monitor's paper-id
    refusal read the class off the payload and passed only because of that
    (QA B1/V1).
    """
    import copy

    world = copy.deepcopy(head)
    real_id = world["deployments"]["real"]
    real_prefix = f"/v1/deployments/{real_id}"
    routes: list[dict[str, Any]] = []
    practice_routes: list[dict[str, Any]] = []
    for route in world["routes"]:
        path, body = route["path"], route["body"]
        if path == "/v1/entitlements":
            balances = [*body.get("balances", []), dict(PAPER_SLOT_BALANCE)]
            routes.append({**route, "body": {**body, "balances": balances}})
            continue
        if path == "/v1/me":
            entitlements = [*body.get("entitlements", []), dict(PAPER_SLOT_BALANCE)]
            me = {**body, "entitlements": entitlements, "paper_enabled": True}
            routes.append({**route, "body": me})
            continue
        if path == PORTFOLIO_SUMMARY_PATH:
            rows = body.get("deployments", [])
            routes.append({**route, "body": {**body, "capital_class": "real", "deployments": rows}})
            practice_rows = [
                {**r, "deployment_id": PRACTICE_DEPLOYMENT} for r in body.get("deployments", [])
            ]
            practice_routes.append(
                {
                    **route,
                    "capital_class": "practice",
                    "body": {**body, "capital_class": "practice", "deployments": practice_rows},
                }
            )
            continue
        if not (path == real_prefix or path.startswith(real_prefix + "/")):
            routes.append(route)
            continue
        twin = path.replace(real_prefix, f"/v1/deployments/{PRACTICE_DEPLOYMENT}", 1)
        twin_body = body
        if path == real_prefix:
            # The detail row: DeploymentResponse.venue (schemas/live.py:393)
            # and the peer fields, null on a real row.
            peer = {"health": None, "health_detail": None, "parity": None, "graduation": None}
            routes.append({**route, "body": {**body, **peer, "venue": dict(REAL_VENUE)}})
            twin_body = {
                **body,
                **peer,
                "venue": dict(PRACTICE_VENUE),
                "deployment_id": PRACTICE_DEPLOYMENT,
                "health": "running",
            }
        else:
            routes.append(route)
            if path == f"{real_prefix}/trades" and isinstance(body, dict):
                twin_body = {
                    **body,
                    "data": [{**r, **PRACTICE_FILL_DISCLOSURE} for r in body.get("data", [])],
                }
        practice_routes.append({**route, "path": twin, "body": twin_body})
    world["routes"] = routes + practice_routes
    world["deployments"] = {**world["deployments"], "practice": PRACTICE_DEPLOYMENT}
    return world


def listed_calls(world: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """``(name, tool, args)`` for the fifteen ``real``-side listed reads."""
    dep = world["deployments"]["real"]
    calls: list[tuple[str, str, dict[str, Any]]] = [
        ("live_monitor:no_args", "keel_live_monitor", {}),
        ("live_monitor:portfolio", "keel_live_monitor", {"view": "portfolio"}),
    ]
    calls += [
        (f"live_monitor:{view}", "keel_live_monitor", {"deployment_id": dep, "view": view})
        for view in PER_DEPLOYMENT_VIEWS
    ]
    calls += [
        ("account_status", "keel_account_status", {}),
        ("plan_usage", "keel_plan_usage", {}),
    ]
    return calls


#: The instant every captured view is stamped with (see :func:`call_listed`).
FROZEN_NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return FROZEN_NOW if tz is None else FROZEN_NOW.astimezone(tz)


def call_listed(
    tool: str,
    args: dict[str, Any],
    world: dict[str, Any],
    *,
    telemetry: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """``tools/call`` on a real listed + hosted server with a bound caller,
    through the real MCP adapter; only HTTP to keel-api is faked.
    ``telemetry=True`` adds, per request, whether it carried the
    agent-telemetry marker (kept out of the base-commit comparison shape)."""
    import keel.client
    import keel.tools.outcomes._base as base
    import keel.tools.outcomes._handoff as handoff
    import respx
    from keel.hosting import bind_request_credentials, clear_request_credentials
    from keel.mcp.server import create_server

    api = FakedKeelApi(world)
    saved_sleep = keel.client.time.sleep
    saved_anon = handoff._is_anon_session
    saved_clock = (base.datetime, base._ELECTION_SEQ)
    keel.client.time.sleep = lambda *_a, **_k: None
    handoff._is_anon_session = lambda: False  # the hosted surface is never anonymous
    # A view's supersession key stamps the wall clock and a process counter
    # (`_base.election_key`); pin both so one call's bytes never depend on
    # when, or after how many other views, it ran.
    base.datetime, base._ELECTION_SEQ = _FrozenDatetime, itertools.count(1)
    try:
        with listed_hosted_env(env), respx.mock(assert_all_called=False) as router:
            router.route(host=API_HOST).mock(side_effect=api)
            server = create_server()
            reset = bind_request_credentials(token="listed-head-token", api_url=API)
            try:
                result = asyncio.run(server.call_tool(tool, args))
            finally:
                clear_request_credentials(reset)
    finally:
        keel.client.time.sleep = saved_sleep
        handoff._is_anon_session = saved_anon
        base.datetime, base._ELECTION_SEQ = saved_clock
    wire = _wire(result.to_mcp_result())
    content = wire.get("content") or []
    return {
        "structured": wire.get("structuredContent"),
        "text": "\n".join(c.get("text", "") for c in content if c.get("type") == "text"),
        "meta": wire.get("_meta"),
        "is_error": bool(wire.get("isError", False)),
        "requests": api.requests,
        **({"telemetry": api.telemetry} if telemetry else {}),
    }


def drive_listed_outputs(world: dict[str, Any]) -> list[dict[str, Any]]:
    """Every ``real``-side listed read of :func:`listed_calls`, in order."""
    from keel.tools.outcomes import _bootstrap

    _bootstrap()
    out = []
    for name, tool, args in listed_calls(world):
        out.append({"name": name, "tool": tool, "args": args, **call_listed(tool, args, world)})
    return out


def load_world(path: Path = OUTPUTS_FIXTURE) -> dict[str, Any]:
    """The faked keel-api a fixture was captured against."""
    return json.loads(path.read_text(encoding="utf-8"))["world"]


# ─── Capture (run once from the SDK venv; never from a paper commit) ────


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def capture(world_path: Path) -> None:
    """Write both fixtures. ``world_path`` is a world JSON (``provenance``,
    ``org_id``, ``deployments``, ``routes``), or an existing outputs fixture
    to re-capture against the world it already holds."""
    raw = json.loads(world_path.read_text(encoding="utf-8"))
    world = raw.get("world", raw)

    surface = listed_surface_snapshot()
    if not surface["tools"] or not surface["card_resources"]:
        raise SystemExit("listed surface snapshot is empty; refusing to write a vacuous fixture")
    _write(SURFACE_FIXTURE, {"base_commit": BASE_COMMIT, **surface})

    outputs = drive_listed_outputs(world)
    empty = [o["name"] for o in outputs if not (o["structured"] or o["text"])]
    if len(outputs) != 15 or empty:
        raise SystemExit(f"expected fifteen non-empty outputs; got {len(outputs)}, empty: {empty}")
    _write(OUTPUTS_FIXTURE, {"base_commit": BASE_COMMIT, "world": world, "outputs": outputs})
    print(
        f"{SURFACE_FIXTURE.name}: {surface['tool_count']} tools, "
        f"{len(surface['card_resources'])} card resources; "
        f"{OUTPUTS_FIXTURE.name}: {len(outputs)} outputs, "
        f"{sum(len(o['requests']) for o in outputs)} requests"
    )


if __name__ == "__main__":  # pragma: no cover - a one-off capture, not a test
    if len(sys.argv) != 3 or sys.argv[1] != "capture":
        raise SystemExit("usage: python tests/_listed_head_fixtures.py capture <world.json>")
    capture(Path(sys.argv[2]))
