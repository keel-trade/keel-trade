"""Actual HTTP boundary controls for finite SDK contract negotiation."""

from copy import deepcopy

import httpx
import pytest
import respx
from keel import account_performance as p
from keel.client import KeelClient
from keel.config import KeelConfig
from keel.errors import KeelError
from keel.tools.outcomes.live_monitor import _bounded_equity, _compact_curve, _overview_enrichment


BASE = "https://reporting.test"
PATH = "/v1/deployments/deployment/stats"
WITNESS = {
    "code": "performance_contract_refresh_required",
    "supported_contract_version": p.CONTRACT,
}


def report():
    return {
        "contract_version": p.CONTRACT,
        "scope": {
            "kind": "account",
            "scope_id": "deployment",
            "account_id": "account",
            "deployment_id": "deployment",
            "reporting_interval_id": "deployment",
            "start": "2026-10-01T00:00:00Z",
            "end": None,
        },
        "basis_components": [],
        "basis_id": "basis",
        "basis_binding_revision": 1,
        "method_id": "account_twr.v1",
        "generated_at": "2026-10-01T01:00:00Z",
        "estimated_interval_count": 0,
        "first_estimated_at": None,
        "reason_codes": [],
        "baseline": None,
        "quote": None,
        "portfolio_context": None,
        "performance_tip": None,
        "coverage": {
            "interval_start": "2026-10-01T00:00:00Z",
            "interval_end": "2026-10-01T01:00:00Z",
            "classified_through": None,
            "unresolved_count": 0,
            "complete": True,
        },
        "numeraire": "USDC",
        "revision_token": "a" * 64,
        "policy_sha256": "b" * 64,
        "performance_quality": "complete",
        "value_quality": "current",
        "return_quality": "valuation_linked",
        "exact_twr_pct": "10",
        "estimated_return_pct": None,
        "points": [],
        "perps_performance": {
            "interval_start": "2026-10-01T00:00:00Z",
            "interval_end": "2026-10-01T01:00:00Z",
            "reason_codes": [],
            "evidence_manifest_ref": None,
            "return_method_id": None,
            "return_equivalence_proof_ref": None,
            "performance_basis_id": "perps_usdc_performance.v1",
            "performance_scope_id": "hyperliquid.main_perps.v1",
            "scope_hash": "c" * 64,
            "input_revision_token": "d" * 64,
            "quality": "complete",
            "pnl": "0",
            "return_quality": "unavailable",
            "return_pct": None,
            "exact_twr_pct": None,
            "estimated_return_pct": None,
        },
    }


@pytest.mark.parametrize(
    "key",
    [
        "scope",
        "coverage",
        "revision_token",
        "perps_performance",
        "quote",
        "performance_tip",
        "basis_components",
    ],
)
def test_canonical_shape_missing_authoritative_field_refuses(key):
    value = report()
    del value[key]
    with pytest.raises(KeelError):
        p.validate(value)


def test_canonical_points_keep_server_returns_and_refuse_inconsistent_quality():
    value = report()
    point = {
        "observed_at": "2026-10-01T00:10:00Z",
        "value": "1100",
        "pnl": "100",
        "wealth": "1.1",
        "return_pct": "10",
        "return_quality": "valuation_linked",
        "factor_kind": "no_flow",
        "exact_twr_pct": "10",
        "estimated_return_pct": None,
        "estimated_interval_count": 0,
        "flow": "0",
        "validity": "complete",
        "segment_id": "segment",
        "perps_performance": deepcopy(value["perps_performance"]),
    }
    value["points"] = [point]
    assert p.validate(value)["points"][0] == point
    point["return_pct"] = "11"
    with pytest.raises(KeelError):
        p.validate(value)


@pytest.fixture(autouse=True)
def isolated_selection(monkeypatch):
    monkeypatch.setattr(p, "_SELECTED", set())


def client(base=BASE):
    return KeelClient(KeelConfig(api_key="test", api_url=base))


def test_origin_normalizes_host_default_ports_and_preserves_environment_path():
    p.select("https://API.EXAMPLE:443/env/")
    assert p.selected("https://api.example/env")
    assert not p.selected("https://api.example/other")
    assert not p.selected("https://api.example:444/env")
    assert p.origin("http://[::1]:80/base/") == "http://[::1]/base"
    assert p.origin("http://[::1]:8080/base") == "http://[::1]:8080/base"


@pytest.mark.parametrize("url", ["https://user:secret@api.example", "https://user@api.example"])
def test_api_url_credentials_are_refused_not_used_as_selection_identity(url):
    with pytest.raises(KeelError, match="must not contain credentials"):
        p.selected(url)


@respx.mock
def test_exact_witness_selects_once_and_replacement_client_never_downgrades():
    route = respx.get(BASE + PATH).mock(
        side_effect=[
            httpx.Response(409, json=WITNESS),
            httpx.Response(200, json=report()),
            httpx.Response(200, json={"net_pnl": 999}),
        ]
    )
    with client() as c:
        result = c.get(PATH)
    assert result["perps_performance"]["pnl"] == "0"
    assert result["perps_performance"]["return_pct"] is None
    with client() as replacement, pytest.raises(KeelError) as error:
        replacement.get(PATH)
    assert not error.value.retryable
    assert [call.request.headers.get(p.HEADER) for call in route.calls] == [
        None,
        p.CONTRACT,
        p.CONTRACT,
    ]


@pytest.mark.parametrize(
    "status,body",
    [
        (409, {"code": "other"}),
        (403, WITNESS),
        (409, {**WITNESS, "supported_contract_version": "future"}),
    ],
)
@respx.mock
def test_other_errors_never_select(status, body):
    route = respx.get(BASE + PATH).respond(status, json=body)
    with client() as c, pytest.raises(KeelError):
        c.get(PATH)
    assert len(route.calls) == 1 and not p.selected(BASE)


@respx.mock
def test_versioned_failure_is_finite_and_origin_isolated():
    route = respx.get(BASE + PATH).mock(
        side_effect=[httpx.Response(409, json=WITNESS), httpx.Response(503, json={})]
    )
    with client() as c, pytest.raises(KeelError) as error:
        c.get(PATH)
    assert not error.value.retryable and len(route.calls) == 2
    other = respx.get("https://staging.test" + PATH).respond(200, json={"net_pnl": 7})
    with client("https://staging.test") as c:
        assert c.get(PATH) == {"net_pnl": 7}
    assert p.HEADER not in other.calls[0].request.headers


@respx.mock
def test_caller_header_and_unsolicited_versioned_success_are_not_activation():
    route = respx.get(BASE + PATH).respond(200, json=report())
    with client() as c, pytest.raises(KeelError):
        c.get(PATH, headers={p.HEADER: p.CONTRACT})
    assert len(route.calls) == 1
    assert p.HEADER not in route.calls[0].request.headers
    assert not p.selected(BASE)


@respx.mock
@pytest.mark.parametrize("final_status", [200, 503])
def test_versioned_oauth_refresh_replays_once_without_reopening_transport_retries(final_status):
    from datetime import datetime, timedelta, timezone

    config = KeelConfig(
        api_key="jwt.expired",
        api_url=BASE,
        refresh_token="krt_old",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    route = respx.get(BASE + PATH).mock(
        side_effect=[
            httpx.Response(409, json=WITNESS),
            httpx.Response(401),
            httpx.Response(final_status, json=report() if final_status == 200 else {}),
        ]
    )
    refresh = respx.post(BASE + "/v1/auth/oauth/refresh").respond(
        200,
        json={
            "access_token": "jwt.fresh",
            "refresh_token": "krt_new",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "",
        },
    )
    with KeelClient(config) as c:
        if final_status == 200:
            assert c.get(PATH) == report()
        else:
            with pytest.raises(KeelError) as error:
                c.get(PATH)
            assert not error.value.retryable
    assert refresh.call_count == 1 and route.call_count == 3
    assert [call.request.headers.get(p.HEADER) for call in route.calls] == [
        None,
        p.CONTRACT,
        p.CONTRACT,
    ]
    assert route.calls[-1].request.headers["authorization"] == "Bearer jwt.fresh"


@pytest.mark.parametrize("key", ["scope_id", "deployment_id", "reporting_interval_id"])
def test_account_scope_must_name_one_exact_interval(key):
    value = report()
    value["scope"][key] = "different"
    with pytest.raises(KeelError):
        p.validate(value)


@respx.mock
def test_legacy_inflight_response_cannot_win_after_other_request_activation():
    def respond(request):
        if p.HEADER not in request.headers:
            p.select(BASE)  # Another request's witnessed activation while this read is outstanding.
            return httpx.Response(200, json={"net_pnl": 999})
        return httpx.Response(200, json=report())

    route = respx.get(BASE + PATH).mock(side_effect=respond)
    with client() as c:
        assert c.get(PATH) == report()
    assert len(route.calls) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"pnl": None},
        {"return_pct": "10"},
        {"pnl": 0},
        {"return_quality": "exact", "return_pct": "10", "exact_twr_pct": "10"},
    ],
)
def test_perps_never_borrows_nav_return_or_invents_zero(change):
    value = report()
    value["perps_performance"].update(change)
    with pytest.raises(KeelError):
        p.validate(value)


def test_canonical_history_is_not_legacy_compacted_or_downsampled():
    value = report()
    assert _compact_curve(value) is value
    assert _bounded_equity(value) is value


@respx.mock
def test_overview_uses_one_corrected_snapshot_and_refuses_legacy_fallback():
    changed = deepcopy(report())
    changed["revision_token"] = "e" * 64
    changed["exact_twr_pct"] = None
    changed["return_quality"] = "unavailable"
    respx.get(BASE + PATH).mock(
        side_effect=[httpx.Response(409, json=WITNESS), httpx.Response(200, json=report())]
    )
    curve = respx.get(BASE + "/v1/deployments/deployment/equity").respond(200, json=changed)
    with client() as c:
        out = _overview_enrichment(c, "deployment")
        assert out["stats"] == out["curve"] == changed
        respx.get(BASE + PATH).respond(200, json=report())
        curve.respond(200, json={"points": []})
        with pytest.raises(KeelError):
            _overview_enrichment(c, "deployment")


def test_actual_card_renders_independent_metrics_gaps_and_replaces_correction():
    import json
    import subprocess
    from pathlib import Path

    card = Path(__file__).parents[1] / "keel/widgets/assets/card-live.js"
    value = report()
    value["points"] = [
        {"return_pct": "0", "validity": "complete", "observed_at": "2026-10-01T00:00:00Z"},
        {"return_pct": None, "validity": "held", "observed_at": "2026-10-01T00:10:00Z"},
        {"return_pct": "10", "validity": "complete", "observed_at": "2026-10-01T01:00:00Z"},
    ]
    corrected = deepcopy(value)
    corrected.update(
        points=[], exact_twr_pct=None, return_quality="unavailable", performance_quality="pending"
    )
    segmented = deepcopy(value)
    segmented["points"] = [
        {**value["points"][0], "segment_id": "first"},
        {**value["points"][2], "segment_id": "second"},
    ]
    script = r"""
const fs = require('fs'), vm = require('vm');
class Node {
  constructor(tag) { this.tag=tag; this.children=[]; this.attrs={}; this.content=''; }
  set textContent(x) { this.content=x; this.children=[]; }
  get textContent() { return this.content + this.children.map(x=>x.textContent).join(' '); }
  appendChild(x) { this.children.push(x); return x; }
  setAttribute(k,v) { this.attrs[k]=v; }
}
const body=new Node('body'); let callback;
const fmt={text:x=>({text:String(x)}),money:x=>({text:String(x)+' USDC'}),pct:x=>({text:String(x)+'%'}),ago:()=>null};
const H={fmt,displayMode:()=>'',isNarrow:()=>false,header:()=>{},receipt:()=>null,onResize:()=>{},ready:f=>callback=f,
  el:(tag,cls,text)=>{let n=new Node(tag);n.textContent=text;return n;},
  tiles:rows=>{let n=new Node('tiles');n.textContent=rows.map(r=>r[0]+': '+r[1].text).join(' | ');return n;}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{window:{KeelHost:H},document:{getElementById:()=>body,createElementNS:(_,t)=>new Node(t)}});
const reports=JSON.parse(fs.readFileSync(0,'utf8')), results=[];
for(const data of reports){callback({view:'stats',data});results.push({text:body.textContent,paths:body.children.filter(n=>n.tag==='svg').flatMap(n=>n.children.map(x=>x.attrs.d))});}
process.stdout.write(JSON.stringify(results));
"""
    result = subprocess.run(
        ["node", "-e", script, str(card)],
        input=json.dumps([value, segmented, corrected]),
        text=True,
        capture_output=True,
        check=True,
    )
    first, segments, second = json.loads(result.stdout)
    assert "Main perps profit: 0 USDC · complete" in first["text"]
    assert "Main perps return: Unavailable · unavailable" in first["text"]
    assert "Account NAV return: 10% · valuation_linked" in first["text"]
    assert first["paths"][0].count("M") == 2 and "L" not in first["paths"][0]
    assert segments["paths"][0].count("M") == 2 and "L" not in segments["paths"][0]
    assert "10%" not in second["text"] and second["paths"] == []
    assert "No measured history" in second["text"]
