"""The listed read tools while ``KEEL_LISTED_PAPER`` is off (paper-trading-v2
S06 §3.5.1, §3.4.2, §3.4.6; PT2-28, PT2-29).

The hosted connector shows what it showed at the base commit until the founder
flips the listed flag: its read tools ask for ``real`` and relay no key the
paper build adds, and a paper id is refused. Driven over the real listed +
hosted MCP server (``tests/_listed_head_fixtures.call_listed``) against
:func:`paper_build_world`: the base-commit world as the paper build's keel-api
serves it (the ``dep_real`` detail row carries the venue block and the peer
fields, no per-view payload carries a venue block — the response models'
shapes, ``VENUE_BY_VIEW`` — and the real summary echoes its class) plus a
``practice`` deployment with a twin of every read.

``M8-listed-reads-real`` (this module's first arms):

* every ``keel_live_monitor`` call a HEAD client can make (no arguments,
  ``view=portfolio`` and the eleven per-deployment views of ``dep_real``)
  returns the base commit's structured content, text and ``_meta``, byte for
  byte (``fixtures/listed_outputs_head.json``), although a read it made
  carries what the paper build adds; its requests are the base commit's plus
  the summary's named class and, on the ten non-overview views, the one
  guard read of the detail row that learns the run's class (QA B1);
* an execution receipt's ``source_cutoff.venue`` (HEAD data, nested) is still
  relayed;
* each of the eleven per-deployment views of the practice id raises
  ``paper_run_not_listed`` (exit code 2, §3.4.6's refusal line) after exactly
  one keel-api read — the detail row, the one payload that carries the venue
  block — with no row data, and a refused overview read carried no telemetry
  marker (it counted as the owner's visit); a non-overview view's read was the
  guard read, with the marker, and never reached the view's payload;
* ``LISTED_DEPLOYMENT_FIELDS`` names none of the new fields.

Seeds (each by an exact reversible edit, then reversed):

* add ``"venue"`` to ``LISTED_DEPLOYMENT_FIELDS`` unconditionally ⇒ the
  overview arm reds, its diff naming ``venue``, and so does the constant's
  own arm. The portfolio arm stays green under this seed: a portfolio row is
  guarded twice, by the added-keys strip (the summary's rows are rows it
  lists) and then by the allow-list, so the seed alone does not reach it;
* strip ``LISTED_PAPER_ADDED_KEYS`` at every depth (``scrub``) ⇒ the
  executions arm and its control red on the receipt's ``venue``;
* relay a practice overview (drop the refusal) ⇒ the practice arms red;
* read the class off the view's own payload instead of the guard read (the
  pre-QA-B1 code) ⇒ the ten non-overview practice arms red (the data is
  relayed under ``/live/dep_paper``); the overview arm stays green.

Count: thirteen monitor calls compared to non-empty base-commit outputs,
each reading at least one body that differs from the base commit's; eleven
practice views refused.

``M8-listed-flag-off`` (AC-06-7, IC-06-3) completes the module:

* the served listed surface with ``KEEL_LISTED_PAPER`` unset (the serialized
  ``tools/list``, ``listed_instructions()`` and every listed card resource)
  equals ``fixtures/listed_surface_head.json`` field by field, and the
  comparison names each field it compared and each that differs;
* ``KEEL_LISTED_PAPER=maybe`` makes ``create_server()`` raise ``ValueError``;
* ``LISTED_BOUNDARY_SENTENCE`` and ``CAPABILITY_PHRASE`` are today's literals,
  ``paper trading`` still violates the listed text rule (no allowance off),
  and ``compute_numbers()["tool_count"]`` is 29;
* the output arm covers all fifteen ``real``-side reads, ``keel_account_status``
  and ``keel_plan_usage`` included, over a world whose entitlements carry a
  ``paper_strategies_max`` balance and whose ``/v1/me`` carries
  ``paper_enabled``, asserted to hold one deployment of each class first.

Seeds (each by an exact reversible edit, then reversed): make
``listed_paper_enabled()`` return ``True`` when unset ⇒ the surface diff names
``keel_paper_deploy`` (the instructions' paper row); route the monitor's
listed sentence through its shared ``listed_description`` ⇒ the diff names
``keel_live_monitor.description``; drop ``capital_class=real`` from the listed
portfolio read ⇒ the portfolio request arms red while their outputs match.
Count: 29 tools and at least one card resource in the fixture, and at least
29 × 6 fields compared.
"""

from __future__ import annotations

import json

import pytest

from tests._listed_head_fixtures import (
    OUTPUTS_FIXTURE,
    PER_DEPLOYMENT_VIEWS,
    PRACTICE_DEPLOYMENT,
    call_listed,
    load_world,
    paper_build_world,
)


_HEAD = json.loads(OUTPUTS_FIXTURE.read_text())
_HEAD_OUTPUTS = {o["name"]: o for o in _HEAD["outputs"]}
_MONITOR_CALLS = [n for n in _HEAD_OUTPUTS if n.startswith("live_monitor:")]
_OUTPUT = ("structured", "text", "meta", "is_error")
_TOOL_RE = __import__("re").compile(r"\bkeel_[a-z0-9_]+\b")
_SUMMARY = "/v1/deployments/portfolio/summary"
_REFUSAL = (
    "This connector does not show simulated (paper) runs yet; open this one in the "
    "Keel web app with keel_app_link."
)


@pytest.fixture(scope="module")
def paper_world():
    world = paper_build_world(load_world())
    # The world holds one deployment of each class before any call.
    assert set(world["deployments"]) == {"real", "practice"}
    return world


def test_the_monitor_calls_are_counted():
    assert len(_MONITOR_CALLS) == 13, _MONITOR_CALLS
    assert len(_HEAD_OUTPUTS) == 15, sorted(_HEAD_OUTPUTS)


@pytest.mark.parametrize("name", ["account_status", "plan_usage"])
def test_the_account_reads_return_heads_bytes(name, paper_world):
    """keel_account_status keeps only its own balances and named /v1/me
    fields, keel_plan_usage its two backtest units (S06 §3.5.1): neither
    relays the paper slot balance or `paper_enabled`."""
    head = _HEAD_OUTPUTS[name]
    assert head["text"] and head["structured"], "empty base output; this arm proves nothing"
    me = next(r["body"] for r in paper_world["routes"] if r["path"] == "/v1/me")
    assert me["paper_enabled"] is True  # non-vacuity: the paper keys are served
    got = call_listed(head["tool"], head["args"], paper_world)
    assert {k: got[k] for k in _OUTPUT} == {k: head[k] for k in _OUTPUT}
    assert got["requests"] == head["requests"]


def _surface_diff(head: dict, now: dict) -> tuple[list[str], int]:
    """(differing fields, fields compared) between two surface snapshots,
    named `tool.field`, `instructions`, `card:<uri>`."""
    diffs: list[str] = []
    compared = 0
    head_tools = {t["name"]: t for t in head["tools"]}
    now_tools = {t["name"]: t for t in now["tools"]}
    for name in sorted(set(head_tools) ^ set(now_tools)):
        diffs.append(f"{name} (only in {'base' if name in head_tools else 'served'})")
    for name in sorted(set(head_tools) & set(now_tools)):
        for field in sorted(set(head_tools[name]) | set(now_tools[name])):
            compared += 1
            if head_tools[name].get(field) != now_tools[name].get(field):
                diffs.append(f"{name}.{field}")
    compared += 1
    if head["instructions"] != now["instructions"]:
        tools = sorted(
            set(_TOOL_RE.findall(now["instructions"])) - set(_TOOL_RE.findall(head["instructions"]))
        )
        diffs.append(f"instructions (newly named: {', '.join(tools) or 'none'})")
    head_cards = {c["uri"]: c for c in head["card_resources"]}
    now_cards = {c["uri"]: c for c in now["card_resources"]}
    for uri in sorted(set(head_cards) | set(now_cards)):
        compared += 1
        if head_cards.get(uri) != now_cards.get(uri):
            diffs.append(f"card:{uri}")
    return diffs, compared


def test_the_flag_off_listed_surface_is_heads():
    from tests._listed_head_fixtures import SURFACE_FIXTURE, listed_surface_snapshot

    head = json.loads(SURFACE_FIXTURE.read_text())
    assert head["tool_count"] == 29 and len(head["tools"]) == 29
    assert head["card_resources"], "no card resource in the fixture"
    diffs, compared = _surface_diff(head, listed_surface_snapshot())
    assert compared >= 29 * 6, f"compared only {compared} fields"
    assert diffs == [], f"the flag-off listed surface moved: {diffs}"


def test_an_invalid_flag_refuses_server_creation():
    import os

    from keel.mcp.server import create_server

    from tests._listed_head_fixtures import listed_hosted_env

    with listed_hosted_env():
        os.environ["KEEL_LISTED_PAPER"] = "maybe"
        try:
            with pytest.raises(ValueError, match="KEEL_LISTED_PAPER"):
                create_server()
        finally:
            os.environ.pop("KEEL_LISTED_PAPER", None)


def test_the_boundary_owners_and_counts_are_heads():
    from keel.tools.outcomes._status_view import CAPABILITY_PHRASE

    from tests.test_policy_scan import LISTED_BOUNDARY_SENTENCE, _text_violations

    assert LISTED_BOUNDARY_SENTENCE == (
        "It cannot place orders, move funds or connect wallets; strategies that are "
        "running are managed in the Keel web app (keel_app_link)."
    )
    assert CAPABILITY_PHRASE == (
        "research and backtests; it cannot place orders, move funds or connect wallets; "
        "strategies that are running are managed in the Keel web app"
    )
    assert _text_violations("probe", "text", "paper trading"), "an allowance applies off"
    assert _load_checker().compute_numbers()["tool_count"] == 29


def _load_checker():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "check_surface_routing.py"
    spec = importlib.util.spec_from_file_location("check_surface_routing_for_flag", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", _MONITOR_CALLS)
def test_a_heads_monitor_call_returns_heads_bytes(name, paper_world):
    head = _HEAD_OUTPUTS[name]
    assert head["text"] and head["structured"], "empty base output; this arm proves nothing"
    head_routes = {(r["method"], r["path"]): r["body"] for r in load_world()["routes"]}
    paper_routes = {
        (r["method"], r["path"]): r["body"]
        for r in paper_world["routes"]
        if "capital_class" not in r
    }
    got = call_listed(head["tool"], head["args"], paper_world)
    # Non-vacuity: a body this call read differs from the base commit's world
    # (the detail row's venue block and peer fields, or the summary's class),
    # so the equality below is the projection's.
    read = [(r["method"], r["path"]) for r in got["requests"]]
    assert [k for k in read if paper_routes[k] != head_routes[k]], read
    assert {k: got[k] for k in _OUTPUT} == {k: head[k] for k in _OUTPUT}
    # The requests are the base commit's, except that the summary read now
    # names its class (M2-sdk-portfolio-real; S01 §3.10, PT2-16): every list
    # or summary request a listed tool sends asks for `real`; and a
    # non-overview view first reads the detail row once, the one payload that
    # carries the run's class (QA B1).
    expected = [
        {**r, "params": [["capital_class", "real"]]} if r["path"] == _SUMMARY else r
        for r in head["requests"]
    ]
    if head["args"].get("view") not in (None, "overview", "portfolio"):
        dep = head["args"]["deployment_id"]
        guard = {"method": "GET", "path": f"/v1/deployments/{dep}", "params": []}
        expected = [guard, *expected]
    assert got["requests"] == expected


def test_the_receipt_venue_is_head_data_and_still_relayed(paper_world):
    """CONTROL: the nested source-cutoff venue survives the projection."""
    got = call_listed(
        "keel_live_monitor",
        {"deployment_id": "dep_real", "view": "executions"},
        paper_world,
    )
    rows = json.loads(got["text"])["data"] if got["text"].startswith("{") else None
    text = got["text"] + json.dumps(got["structured"])
    assert '"source_cutoff"' in text and '"venue": "hyperliquid"' in text, rows


@pytest.mark.parametrize("view", PER_DEPLOYMENT_VIEWS)
def test_a_practice_id_is_refused_after_one_read(view, paper_world):
    got = call_listed(
        "keel_live_monitor",
        {"deployment_id": PRACTICE_DEPLOYMENT, "view": view},
        paper_world,
        telemetry=True,
    )
    assert got["is_error"], got["text"][:400]
    assert len(got["requests"]) == 1, got["requests"]
    # The one read is the detail row (QA B1): keel-api's only payload that
    # carries the venue block; a refused view's own payload is never read.
    assert got["requests"][0]["path"] == f"/v1/deployments/{PRACTICE_DEPLOYMENT}"
    rendered = got["text"] + json.dumps(got["structured"])
    assert "paper_run_not_listed" in rendered
    assert _REFUSAL in got["text"]
    # No row data: nothing the practice payload carried is relayed.
    assert PRACTICE_DEPLOYMENT not in got["text"].replace(_REFUSAL, "")
    assert '"data"' not in rendered and "Momentum Basket" not in rendered
    if view == "overview":
        # The overview's detail read is the owner visit (R-61): no marker.
        assert got["telemetry"] == [False]
    else:
        assert got["telemetry"] == [True]


def test_listed_deployment_fields_stay_heads():
    from keel.tools.outcomes._listed_projection import (
        LISTED_DEPLOYMENT_FIELDS,
        LISTED_PAPER_ADDED_KEYS,
        listed_deployment_fields,
    )

    new = {"venue", "health", "health_detail", "parity", "graduation", "pause_reason"}
    assert not new & set(LISTED_DEPLOYMENT_FIELDS)
    assert listed_deployment_fields(paper=False) == LISTED_DEPLOYMENT_FIELDS
    on = listed_deployment_fields(paper=True)
    assert on[: len(LISTED_DEPLOYMENT_FIELDS)] == LISTED_DEPLOYMENT_FIELDS
    assert {"venue", "health", "health_detail", "graduation"} <= set(on)
    assert {"venue", "disclosure_mid", "disclosure_inception"} <= LISTED_PAPER_ADDED_KEYS
