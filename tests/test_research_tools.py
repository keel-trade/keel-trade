"""Research's tools and verbs (spec 07 §10): AT-1, AT-5, AT-7, AT-11, AT-14, AT-20, AT-26 and the handlers.

Every tool calls keel-api through a fake client that records each request and
answers from a table, so the arms read what the SDK sends and what it makes of
the answer, never a network.

Seeded reds (each reverted by reversing the edit; recorded in the commit):
- AT-5: give ``CHANNEL_MAP["study"]`` no ``reduce`` and the largest legal study
  read's structuredContent passes 8,000 bytes (``test_the_largest_study_read_*``
  carries the unreduced arm permanently, and asserts it is over the budget).
- AT-11: read ``research_served`` once (``True``) in ``listed_profile_tools``
  and the off arm lists the three research tools.
- AT-20: drop the paused branch from ``keel.errors.translate_http_error`` and
  every tool's off arm answers the generic not-found advice.
- AT-26: register in import order and ``keel_research_brief`` precedes
  ``keel_screener_run``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from keel.errors import KeelError, translate_http_error
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext

from tests._research_delta import (
    RESEARCH_ADDITIONS,
    RESEARCH_CLI_ONLY,
    RESEARCH_MCP_ADDITIONS,
)


_bootstrap()

PAUSED = json.dumps({"detail": "Research isn't available yet.", "code": "RESEARCH_PAUSED"})
SDK = Path(__file__).resolve().parents[1]


class FakeClient:
    """Answers each (method, path) from ``answers``; records every call."""

    def __init__(self, answers: dict | None = None, *, paused: bool = False) -> None:
        self.answers = answers or {}
        self.paused = paused
        self.calls: list[tuple[str, str, dict]] = []

    def _answer(self, method: str, path: str, payload: dict) -> Any:
        self.calls.append((method, path, payload))
        if self.paused:
            raise translate_http_error(404, PAUSED)
        for (m, prefix), value in self.answers.items():
            if m == method and path.startswith(prefix):
                return value(payload) if callable(value) else value
        raise AssertionError(f"unexpected {method} {path}")

    def get(self, path, *, headers=None, **params):
        return self._answer("get", path, params)

    def post(self, path, json=None, *, headers=None, **params):
        return self._answer("post", path, {"json": json, "headers": headers or {}})

    def put(self, path, json=None):
        return self._answer("put", path, {"json": json})

    def patch(self, path, json=None):
        return self._answer("patch", path, {"json": json})

    def delete(self, path, **params):
        return self._answer("delete", path, params)


def _ctx(client: FakeClient) -> ToolContext:
    return ToolContext(api_client=client)


def _study(state: str = "completed", **extra) -> dict:
    return {
        "study": {
            "study_id": "stu_a",
            "version": 1,
            "revision": 1,
            "title": "Momentum rotation v3",
            "kind_label": {"key": "check", "name": "Check a strategy", "short": "Check"},
            "state": state,
            "run_set_id": "rus_a",
        },
        "counts": {"allowance": None, "runs": {}, "versions_tried": {}},
        "links": {"app": "https://app.usekeel.io/research/stu_a"},
        **extra,
    }


# ── AT-1: the registry ───────────────────────────────────────────────────────


#: 07 §2.2: name → (action, rate tier) for the eight MCP tools.
MCP_ROWS = {
    "keel_research_plan": ("backtest.research.read", "read"),
    "keel_research_run": ("backtest.research.run", "backtest"),
    "keel_research_get": ("backtest.research.read", "read"),
    "keel_research_view_set": ("backtest.research.update", "mutation"),
    "keel_research_brief": ("backtest.research.read", "read"),
    "keel_research_note": ("backtest.research.update", "mutation"),
    "keel_research_cancel": ("backtest.research.cancel", "mutation"),
    "keel_research_make_signal": ("strategy.create", "mutation"),
}


def test_the_registry_carries_07s_table() -> None:
    registered = {n for n in OUTCOMES if n.startswith("keel_research_")}
    # Not vacuous: counts read from the delta, which is 07's table.
    assert len(RESEARCH_MCP_ADDITIONS) == 8 and len(RESEARCH_CLI_ONLY) == 16
    assert registered == RESEARCH_ADDITIONS
    for name, (action, tier) in MCP_ROWS.items():
        tool = OUTCOMES[name]
        assert not tool.cli_only and not tool.mcp_only, name
        assert (tool.required_action, tool.rate_tier) == (action, tier), name
        assert tool.cli_path[0] == "research", name
    for name in RESEARCH_CLI_ONLY:
        assert OUTCOMES[name].cli_only and not OUTCOMES[name].hostable, name


def test_the_cli_has_every_verb_and_mcp_none_of_the_cli_only() -> None:
    import click
    from keel.tools.outcomes._cli_adapter import register_all as cli_register_all
    from keel.tools.outcomes._mcp_adapter import loaded_tool_names

    root = click.Group("keel")
    cli_register_all(root, OUTCOMES)
    research = root.commands["research"]
    verbs = {OUTCOMES[n].cli_path[1] for n in RESEARCH_ADDITIONS}
    assert set(research.commands) == verbs and len(verbs) == 24
    assert not set(loaded_tool_names(OUTCOMES)) & RESEARCH_CLI_ONLY


# ── AT-5: the card and text budget at the largest legal shape ───────────────


SHAPES = {
    k: {f: v for f, v in body.items() if f != "_provenance"}
    for k, body in json.loads(
        (Path(__file__).parent / "fixtures" / "research_server_shapes.json").read_text()
    ).items()
}


def _compact(obj) -> int:
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode())


def _largest_read() -> dict:
    """AT-5 v2 (13 §3.5, WL-AG-13): keel-api's done-study read (server_shapes ``study_done``) at the largest legal
    shape: 100 bars (its own rows repeated, with served words), 50 subjects, 40 steps, 24 views, 5 notes, the stored
    source, a v2 ``card_summary`` from research.card_summary (SERVE's example) carrying the largest thumb the real
    builder admits (``lines_thumb_facts`` under ``THUMB_MAX_BYTES``, 2,989 B), and ``card_data``. No invented list."""
    env = json.loads(json.dumps(SHAPES["study_done"]))
    row = {
        **env["checks"]["rows"][0],
        "value_words": "0.99",
        "interval_words": "0.97 to 1.00",
        "sample_words": "150 books",
        "bar_words": "your bar ≥ 0.75",
    }
    env["checks"] = {
        "count": 100,
        "met": 90,
        "rows": [{**row, "bar_index": i, "label": f"{row['label']} {i}"} for i in range(100)],
    }
    step = env["steps"][0]
    env["steps"] = [{**step, "id": f"s{i}"} for i in range(40)]
    view = env["views"][0]
    env["views"] = [{**view, "index": i, "title": f"View {i} of the study"} for i in range(24)]
    subject = env["subjects"][0]
    env["subjects"] = [
        {**subject, "ref": f'Strategy("str_{i:028d}@3")', "label": f"Strategy {i} v3"}
        for i in range(50)
    ]
    env["notes"] = [
        {"kind": "note", "author_role": "agent", "date": "2026-10-29", "body": "x" * 200}
    ] * 5
    env["notes_total"] = 12
    env["source"] = "Study()\n" * 10_000
    env["card_summary"] = SHAPES["card_summary_v2_max_thumb"]
    env["card_data"] = SHAPES["card_data_v2"]
    env["view"] = {
        "kind": "study",
        "size": "view",
        "markdown": "x" * 7_000,
        "object": "stu_a",
        "at": "t",
        "seq": 1,
    }
    return env


def _largest_list() -> dict:
    """AT-5 v2's list arm (RI-610): 25 rows of keel-api's list read, each with a v2 card (the largest thumb)."""
    page = json.loads(json.dumps(SHAPES["list"]))
    row = page["data"][0]
    rows = [
        {
            **row,
            "study_id": f"stu_{i:024d}",
            "title": f"Momentum rotation v{i}",
            "card_summary": SHAPES["card_summary_v2_max_thumb"],
        }
        for i in range(25)
    ]
    page.pop("data")  # the SDK's list envelope names its rows `studies` (research_get._list)
    return {**page, "studies": rows, "studies_total": 120, "links": {"app": "https://app.usekeel.io/research"},
            "view": {"kind": "study", "size": "view", "markdown": "x" * 3_000, "object": None, "at": "t", "seq": 1}}  # fmt: skip


def test_the_largest_study_read_is_within_the_card_budget() -> None:
    """13 §3.5 (D-328 D2): structuredContent ≤ 8,000 B, `_meta["keel/card"]` ≤ 40,000 B, the whole result ≤ 60,000 B,
    with the card's own data moved (D-332 (6)). SEED (reversed): drop "card_summary.thumb" from the study channel's
    card set -> the thumb rides structuredContent and this reds (over 8,000 at the real thumb); the reduction arm below
    is the control for the bound itself."""
    from dataclasses import replace

    from keel.tools.outcomes import _channels

    env = _largest_read()
    assert (
        2_500 < _compact(env["card_summary"]["thumb"]) <= 3_000
    )  # not vacuous: the real largest thumb
    structured, card = _channels.partition(env, "study", move_card=True)
    size = _compact(structured)
    assert size <= 8_000, size
    assert "thumb" not in structured["card_summary"] and "card_data" not in structured
    assert (
        card["card_summary"]["thumb"] == env["card_summary"]["thumb"]
        and card["card_data"] == env["card_data"]
    )
    assert _compact(card) <= 40_000
    text = env["view"]["markdown"]
    assert len(text.encode()) + size + _compact(card) <= 60_000
    assert len(structured["checks"]["rows"]) == 5
    assert structured["steps"]["total"] == 40
    assert structured["views"] == {"total": 24} and structured["subjects"] == {"total": 50}
    assert "source" not in structured
    assert "headline" not in structured and "markdown" not in structured["view"]
    # The seeded arm, kept: the same read with the bound removed is over budget.
    raw = _unreduced(env, replace(_channels.CHANNEL_MAP["study"], reduce=None))
    assert _compact(raw) > 8_000


def test_d359_the_full_profile_keeps_card_data_out_of_model_context(monkeypatch) -> None:
    """D-359 (5) (a): the local (full) MCP profile asks keel-api for the card (``card=full``) like the hosted one, but
    the meta-move probe flag defaults off there, so card_data rode structuredContent (RECONCILE measured 12,743 B a
    study read). The study kind now moves its card rows whatever the flag says. Through the real MCP result path
    (``view_tool_result``) on the full profile with ``KEEL_CARD_META_MOVE`` unset: structuredContent ≤ 8,000 B and
    carries neither card_data nor the thumb, and ``_meta["keel/card"]`` carries both. SEED (reversed):
    ``always_move_card=True`` removed from the study channel -> this reds (card_data in structuredContent). Control:
    a backtest-kind partition with the flag off still keeps its card rows structured (the probe rule is unchanged)."""
    from keel.tools.outcomes import _channels
    from keel.tools.outcomes._mcp_adapter import view_tool_result

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    monkeypatch.delenv(_channels.CARD_META_ENV, raising=False)
    assert not _channels.card_meta_move()  # not vacuous: the flag is off, as on a local server
    env = _largest_read()
    result = view_tool_result(json.dumps(env), tool_name="keel_research_get")
    structured = result.structured_content
    size = _compact(structured)
    assert "card_data" not in structured and "thumb" not in structured.get("card_summary", {}), (
        sorted(structured)
    )
    assert size <= 8_000, size
    card = (result.meta or {}).get(_channels.CARD_META_KEY) or {}
    assert (
        card.get("card_data") == env["card_data"]
        and card["card_summary"]["thumb"] == env["card_summary"]["thumb"]
    )
    spec = _channels.CHANNEL_MAP["backtest"]
    assert (
        spec.card and not spec.always_move_card
    )  # control: the probe rule still governs other kinds


def test_d363_a_check_rows_lens_and_default_ride_the_card_not_model_context(monkeypatch) -> None:
    """D-363 (2b) (ENGINE 910e68d3fd): each check row's "stricter · looser" lens and ``bar_default_words`` are the app's
    and the card's. Over CARDUI's real chk_v3 envelope (structured + card merged back, as keel-api served it): the
    lens fields leave structuredContent for ``_meta["keel/card"]`` on the reduced rows, and the read is inside
    8,000 B (it was 8,029). SEED (reversed): ``_CHECK_ROW_CARD`` emptied -> this reds. Control: the row's bar and
    value words (what a reply quotes) stay in structuredContent."""
    from keel.tools.outcomes import _channels

    fx = json.loads(
        (Path(__file__).parent / "fixtures" / "cards" / "study" / "study-chk_v3.json").read_text()
    )
    env = json.loads(json.dumps(fx["structuredContent"]))
    _channels._merge(env, json.loads(json.dumps(fx["_meta"]["keel/card"])))
    rows = env["checks"]["rows"]
    assert rows and all(
        "stricter" in r and "bar_default_words" in r for r in rows
    )  # not vacuous: ENGINE's fields
    structured, card = _channels.partition(env, "study")
    assert _compact(structured) <= 8_000, _compact(structured)
    for row in structured["checks"]["rows"]:
        assert not {"stricter", "looser", "bar_default_words"} & set(row), sorted(row)
        assert row.get("bar_words") and row.get("value_words")  # control: what a reply quotes stays
    assert [r.get("stricter") for r in card["checks"]["rows"]] == [
        r.get("stricter") for r in rows[: len(card["checks"]["rows"])]
    ]


def test_406_a_headline_measures_spark_never_reaches_model_context() -> None:
    """ENGINE-2 406596345c (06 §5.1, WL-AE-23): each headline measure serves ``spark`` (a thumb, at most 1,200 B) and
    ``unit_words`` for keel-app's canvas Measures chips. The study read's ``headline`` is the card's and the app's (the
    card reads ``card_summary.headline``; the text quotes each measure's served value words), so it leaves
    structuredContent whole, and with it every spark: four at the ceiling would be 4,800 B of model context. Two owners
    exclude it (``structured_study``'s reduce and the study channel's drop set). SEED (reversed): both exclusions
    removed -> this reds (either one alone holds). Control: the text still writes the headline measures
    (``_headline_lines``) from the envelope."""
    from keel.tools.outcomes import _channels
    from keel.tools.outcomes import _research_view as rv

    env = _largest_read()
    measure = {
        **env["headline"]["measures"][0],
        "unit_words": "ratio",
        "spark": {"enc": "b64x2", "s": "A" * 1_200},
    }
    env["headline"] = {**env["headline"], "measures": [measure] * 4}
    structured, card = _channels.partition(env, "study", move_card=True)
    blob = json.dumps(structured)
    assert '"spark"' not in blob and "headline" not in structured, sorted(structured)
    assert _compact(structured) <= 8_000
    bare = {**env, "checks": None}
    assert f"- {measure['label']}: {measure['value_words']}" in rv.study_text(bare)  # control


def test_the_largest_list_read_keeps_its_row_cards_out_of_model_context() -> None:
    """RI-610 (measured, 13 §3.5): 25 list rows each carrying a v2 card are 25 x ~2.4 KB in structuredContent; the
    row cards ride `_meta["keel/card"]` (same length as `studies`, so the adapter's merge puts them back by index),
    and once the list serves `card_data.rows` they are dropped (the card reads those). Bare rows were 343 B each (8,575 B at
    25) before any card, so a row keeps only its id, kind, title and state words; the rest is the card's.
    SEED (reversed): ``_LIST_ROW_CARD`` empty -> structuredContent is over 8,000 B (this reds)."""
    from keel.tools.outcomes import _channels

    env = _largest_list()
    whole = _compact({k: v for k, v in env.items() if k != "view"})
    assert whole > 8_000, whole  # the measurement RI-610 asked for: over budget with the row cards
    structured, card = _channels.partition(env, "study", move_card=True)
    assert _compact(structured) <= 8_000, _compact(structured)
    assert all(
        set(r) == {"study_id", "kind_label", "title", "state_words"} for r in structured["studies"]
    )
    assert len(card["studies"]) == 25 and card["studies"][0]["card_summary"]["title"]
    with_rows = {
        **env,
        "card_data": {
            "rows": [{"link": "u", "lead_number_words": "Sharpe 1.95", "thumb": None}] * 25
        },
    }
    structured, card = _channels.partition(with_rows, "study", move_card=True)
    assert (
        "card_summary" not in card["studies"][0] and len(card["card_data"]["rows"]) == 25
    )  # card_data.rows instead


def _unreduced(env: dict, spec) -> dict:
    from keel.tools.outcomes import _channels

    saved = _channels.CHANNEL_MAP["study"]
    _channels.CHANNEL_MAP["study"] = spec
    try:
        return _channels.partition(env, "study", move_card=True)[0]
    finally:
        _channels.CHANNEL_MAP["study"] = saved


def test_the_text_holds_its_bound_and_keeps_the_link() -> None:
    from keel.tools.outcomes._research_view import TEXT_MAX_CHARS, study_text

    text = study_text(_largest_read())
    assert len(text) <= TEXT_MAX_CHARS
    assert (
        text.splitlines()[-1]
        == "View in Keel: https://app.usekeel.io/research/stu_chkv3000000000000000000000"
    )


# ── AT-7: no verdict words in research-owned copy ────────────────────────────


def _scanned_copy() -> dict[str, str]:
    from keel.tools.outcomes import _research_view

    copy = {
        "_research_view.py": Path(_research_view.__file__).read_text(),
        "card-study.js": (SDK / "keel/widgets/assets/card-study.js").read_text(),
    }
    for name in ("research.md", "research_steps.md"):
        bundled = SDK / "keel/data/reference" / name
        if bundled.exists():
            copy[name] = bundled.read_text()
    for name in OUTCOMES:
        if name.startswith("keel_research_"):
            copy[name] = OUTCOMES[name].description
    return copy


def test_research_copy_carries_no_verdict() -> None:
    from keel.tools.outcomes._research_view import VERDICT_WORDS

    copy = _scanned_copy()
    assert len(copy) >= 2 + 24  # not vacuous: the module, the card and every tool
    words = [w for w in VERDICT_WORDS if w != "mixed"]  # "mixed" is a served unit name
    pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)
    for name, text in copy.items():
        if name == "_research_view.py":
            # The module declares the list it scans with; its templates must not use them.
            text = (
                text.split("VERDICT_WORDS = (")[0]
                + text.split(")\n", 1)[-1].split("VERDICT_WORDS")[-1]
            )
        hits = pattern.findall(text)
        assert not hits, (name, hits)


# ── AT-11 / AT-20 (listing half): the listing follows research_served ───────


def test_the_listed_set_is_the_base_plus_three_only_while_served(monkeypatch) -> None:
    from keel.tools.outcomes._toolsets import (
        LISTED_RESEARCH_TOOLS,
        listed_profile_tools,
        research_served_context,
    )
    from keel.widgets import listed_card_excluded

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    unbound = listed_profile_tools()
    with research_served_context(False):
        off = listed_profile_tools()
        assert listed_card_excluded("study")
    with research_served_context(True):
        on = listed_profile_tools()
        assert not listed_card_excluded("study")
    assert unbound == off  # the listing fails closed while nothing is bound
    assert not off & LISTED_RESEARCH_TOOLS and off  # not vacuous
    assert on == off | LISTED_RESEARCH_TOOLS and len(LISTED_RESEARCH_TOOLS) == 3
    # Control: the full profile serves research unbound.
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    from keel.tools.outcomes._toolsets import research_served

    assert research_served() is True


def test_research_topics_are_withheld_while_not_served(monkeypatch) -> None:
    from keel.tools.outcomes._toolsets import research_served_context, research_topic_withheld

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    with research_served_context(False):
        assert research_topic_withheld("research") and research_topic_withheld("step:CostStress")
        assert not research_topic_withheld("screener_recipes")  # control
    with research_served_context(True):
        assert not research_topic_withheld("research")


# ── AT-14: generated next actions ────────────────────────────────────────────


def test_a_next_call_names_only_a_served_tool(monkeypatch) -> None:
    from keel.tools.outcomes._research_view import next_call
    from keel.tools.outcomes._toolsets import research_served_context

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    with research_served_context(True):
        assert next_call("keel_research_get", {"study_id": "stu_a"}).startswith(
            "keel_research_get("
        )
        assert next_call("keel_research_view_set", {"study_id": "stu_a"}).startswith(
            "keel_app_link("
        )
    with research_served_context(False):
        assert next_call("keel_research_get", {"study_id": "stu_a"}).startswith("keel_app_link(")
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    assert next_call("keel_research_view_set", {"study_id": "stu_a"}).startswith(
        "keel_research_view_set("
    )


# ── AT-20: every research tool answers RESEARCH_PAUSED while off ────────────


#: One valid call per research command (MCP and CLI-only alike).
CALLS: dict[str, dict] = {
    "keel_research_plan": {"template": "check", "subjects": ['Strategy("str_a@3")']},
    "keel_research_run": {"plan_id": "pln_a", "wait": False},
    "keel_research_get": {"study_id": "stu_a"},
    "keel_research_view_set": {"study_id": "stu_a", "source": "Study()", "expected_revision": 1},
    "keel_research_brief": {},
    "keel_research_note": {"anchor": "stu_a", "kind": "note", "body": "x"},
    "keel_research_cancel": {"run_set_id": "rus_a"},
    "keel_research_make_signal": {
        "study_id": "stu_a",
        "version": 1,
        "subject": "mix",
        "name": "Mix",
    },
    "keel_research_add": {"study_id": "stu_a", "add": ['Output("str_a@1", output="x")']},
    "keel_research_save": {"plan_id": "pln_a"},
    "keel_research_run_draft": {"study_id": "stu_a", "version": 2},
    "keel_research_watch": {"study_id": "stu_a"},
    "keel_research_data": {"study_id": "stu_a", "view": 0},
    "keel_research_list": {},
    "keel_research_resume": {"run_set_id": "rus_a"},
    "keel_research_retry": {"run_set_id": "rus_a"},
    "keel_research_steps": {},
    "keel_research_copy": {"share_id": "A" * 23},
    "keel_research_project": {"action": "create", "name": "p"},
    "keel_research_set_aside": {"study_id": "stu_a", "reason": "r"},
    "keel_research_restore": {"study_id": "stu_a"},
    "keel_research_archive": {"study_id": "stu_a"},
    "keel_research_delete": {"study_id": "stu_a"},
    "keel_research_shares": {"study_id": "stu_a"},
}


def test_the_off_calls_cover_every_research_command() -> None:
    assert set(CALLS) == RESEARCH_ADDITIONS


@pytest.mark.parametrize("name", sorted(CALLS))
def test_every_research_command_answers_paused_while_off(name: str) -> None:
    client = FakeClient(paused=True)
    with pytest.raises(KeelError) as caught:
        OUTCOMES[name].handler(dict(CALLS[name]), _ctx(client))
    env = caught.value.to_envelope()
    assert env["code"] == "RESEARCH_PAUSED", name
    assert env["message"] == "Research isn't available yet."
    assert env["suggested_next_action"] == {"tool": None, "args": {}}
    assert env["retryable"] is False
    assert len(client.calls) == 1  # one request, refused; nothing resent


# ── AT-26: research registers last ───────────────────────────────────────────


def test_research_registers_after_every_other_tool() -> None:
    from keel.tools.outcomes._mcp_adapter import registration_order

    order = [t.name for t in registration_order(OUTCOMES)]
    research = [i for i, n in enumerate(order) if n.startswith("keel_research_")]
    others = [i for i, n in enumerate(order) if not n.startswith("keel_research_")]
    assert research and others and min(research) > max(others)
    # The relative order of every other tool is the registry's own (the head).
    assert [n for n in order if not n.startswith("keel_research_")] == [
        n for n in OUTCOMES if not n.startswith("keel_research_")
    ]


# ── The handlers ─────────────────────────────────────────────────────────────


def test_plan_sends_the_form_and_reads_the_local_file(tmp_path) -> None:
    doc = tmp_path / "s.study"
    doc.write_text("Study(title='x')")
    plan = {
        "plan_id": "pln_a",
        "words": {"work": "3 runs · under a minute"},
        "run_allowed": True,
        "study": {"title": "t", "kind_label": {"name": "Check a strategy"}},
        "writes": "a new study",
        "links": {"app": "https://app.usekeel.io/research/new"},
    }
    client = FakeClient({("post", "/v1/research/plans"): plan})
    result = OUTCOMES["keel_research_plan"].handler({"source_file": str(doc)}, _ctx(client))
    assert client.calls == [
        ("post", "/v1/research/plans", {"json": {"source": "Study(title='x')"}, "headers": {}})
    ]
    text = result.extra["view"]["markdown"]
    assert 'keel_research_run(plan_id="pln_a")' in text and text.endswith("research/new")


def test_run_sends_one_key_and_resends_once_on_an_uncoded_error() -> None:
    answers = iter([KeelError("boom"), _study("running", progress={"words": "1 of 3 runs done"})])

    def submit(payload):
        got = next(answers)
        if isinstance(got, Exception):
            raise got
        return got

    client = FakeClient({("post", "/v1/research/runs"): submit})
    result = OUTCOMES["keel_research_run"].handler(
        {"plan_id": "pln_a", "wait": False}, _ctx(client)
    )
    keys = {c[2]["headers"]["Idempotency-Key"] for c in client.calls}
    assert (
        len(client.calls) == 2
        and len(keys) == 1
        and next(iter(keys)).startswith("sdk-research-run-")
    )
    # D-328 D5 (13 §2.1): a run answers as the fixed-height run card.
    assert result.extra["view"]["size"] == "progress" and result.extra["view"]["mode"] == "run"


def test_a_coded_refusal_is_never_resent() -> None:
    def refuse(payload):
        raise translate_http_error(409, json.dumps({"detail": "x", "code": "RESEARCH_PLAN_STALE"}))

    client = FakeClient({("post", "/v1/research/runs"): refuse})
    with pytest.raises(KeelError):
        OUTCOMES["keel_research_run"].handler({"plan_id": "pln_a", "wait": False}, _ctx(client))
    assert len(client.calls) == 1


def test_a_direct_run_states_max_runs() -> None:
    client = FakeClient()
    with pytest.raises(KeelError):
        OUTCOMES["keel_research_run"].handler(
            {"template": "check", "subjects": ["x"]}, _ctx(client)
        )
    assert not client.calls


def test_get_reads_one_chart_after_the_study() -> None:
    page = {
        "data": [{"cost_multiple": 0, "sharpe": 1.9}],
        "pagination": {"cursor": "c2", "has_more": True},
        "facts": {"rows_total": 6},
        "study": _study()["study"],
    }
    client = FakeClient(
        {
            ("get", "/v1/research/studies/stu_a/versions/1/data"): page,
            ("get", "/v1/research/studies/stu_a"): _study(
                views=[{"index": 3, "title": "Sharpe as costs rise"}]
            ),
        }
    )
    result = OUTCOMES["keel_research_get"].handler({"study_id": "stu_a", "view": 3}, _ctx(client))
    assert [c[1] for c in client.calls] == [
        "/v1/research/studies/stu_a",
        "/v1/research/studies/stu_a/versions/1/data",
    ]
    text = result.extra["view"]["markdown"]
    assert text.startswith("Sharpe as costs rise (version 1)")
    assert 'cursor="c2"' in text


def test_get_reads_a_charts_marker_marks_and_withheld_intervals() -> None:
    """RI-135: the text carries what keel-api's view read serves beyond the rows (page_facts.view_page): the
    Distribution's marker, the marks, and each withheld interval with its served reason (07 §3.2 rule 3).

    SEED (reversed): ``lines.extend(_view_fact_lines(facts))`` removed -> this arm fails; the plain page's control
    stays green."""
    reason = "40 days; this needs at least 60."
    page = {
        "data": [{"horizon": 30, "growth": 1.1}, {"horizon": 365, "growth": 1.6}],
        "pagination": {"cursor": None, "has_more": False},
        "facts": {
            "rows_total": 2,
            "bins": {"measure": "sharpe", "edges": [0, 1, 2], "counts": [40, 110], "population": 150,
                     "marker": {"label": "Momentum rotation v3", "value": 1.95}},
            "marks": [{"kind": "vline", "at": "2026-04-17", "label": "Split"},
                      {"kind": "hline", "at": 0, "label": ""}],
            "withheld": [30.0, 365],
            "withheld_reason": {"30": reason, "365": reason},
        },
        "study": _study()["study"],
    }  # fmt: skip
    client = FakeClient(
        {
            ("get", "/v1/research/studies/stu_a/versions/1/data"): page,
            ("get", "/v1/research/studies/stu_a"): _study(
                views=[{"index": 7, "title": "A year ahead"}]
            ),
        }
    )
    text = (
        OUTCOMES["keel_research_get"]
        .handler({"study_id": "stu_a", "view": 7}, _ctx(client))
        .extra["view"]["markdown"]
    )
    lines = text.splitlines()
    assert "Marker: Momentum rotation v3 at 1.95" in lines
    assert (
        "Marks: Split at Apr 17, 2026; a line across at 0" in lines
    )  # POLISH: a day in words, never ISO
    # AUDIT-WORDS M15: "No interval at …", the reason once
    assert f"No interval at 30, 365: {reason}" in lines
    assert text.count(reason) == 1  # one line per served reason, never per x


def test_control_a_page_with_no_view_facts_reads_as_before() -> None:
    page = {"data": [{"cost_multiple": 0, "sharpe": 1.9}], "pagination": {"cursor": None, "has_more": False},
            "facts": {"rows_total": 1}, "study": _study()["study"]}  # fmt: skip
    client = FakeClient(
        {
            ("get", "/v1/research/studies/stu_a/versions/1/data"): page,
            ("get", "/v1/research/studies/stu_a"): _study(views=[{"index": 3, "title": "Costs"}]),
        }
    )
    text = (
        OUTCOMES["keel_research_get"]
        .handler({"study_id": "stu_a", "view": 3}, _ctx(client))
        .extra["view"]["markdown"]
    )
    assert not any(
        line.startswith(("Marker", "Marks", "No interval")) for line in text.splitlines()
    )
    assert "| Cost multiple | Sharpe |" in text  # non-vacuity: the rows were written


def test_get_with_no_id_lists_the_users_studies() -> None:
    listing = {
        "data": [
            {
                "study_id": "stu_a",
                "kind_label": {"short": "Check"},
                "title": "t",
                "state_words": "Done",
            }
        ],
        "pagination": {"has_more": False},
        "studies_total": 1,
        "by_state": {"completed": 1},
    }
    client = FakeClient({("get", "/v1/research/studies"): listing})
    result = OUTCOMES["keel_research_get"].handler({}, _ctx(client))
    assert "Studies: 1 shown of 1" in result.extra["view"]["markdown"]
    assert result.extra["studies"][0]["study_id"] == "stu_a"


def test_a_note_to_set_aside_goes_through_the_set_aside_route() -> None:
    client = FakeClient({("post", "/v1/research/studies/stu_a/set-aside"): {"said": "Set aside."}})
    OUTCOMES["keel_research_note"].handler(
        {"anchor": "stu_a", "kind": "set_aside", "body": "dup"}, _ctx(client)
    )
    assert client.calls[0][1].endswith("/set-aside") and client.calls[0][2]["json"] == {
        "reason": "dup"
    }


def test_keel_signals_search_is_blocks_search_with_role_signal(monkeypatch) -> None:
    """07 A1 and §2.2 (A3 A1): ``keel signals search`` is ``keel blocks search --role signal``, a CLI alias, not a
    registry entry; ``keel signals`` was "No such command". The alias sends the same handler ``role="signal"`` unless
    the caller sends a role. SEED (reversed): delete the ``("signals", "search")`` row of ``_CLI_PRESET_ALIASES`` ->
    "No such command" (this reds). Control: ``keel blocks search`` itself presets nothing."""
    import click
    from click.testing import CliRunner
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import OutcomeResult
    from keel.tools.outcomes._cli_adapter import register_all

    _bootstrap()
    seen: list[dict] = []

    def handler(args, ctx):
        seen.append(dict(args))
        return OutcomeResult(extra={"data": []})

    import dataclasses

    outcomes = dict(OUTCOMES)
    outcomes["keel_blocks_search"] = dataclasses.replace(
        OUTCOMES["keel_blocks_search"], handler=handler
    )
    root = click.Group("keel")
    register_all(root, outcomes)
    assert "keel_signals_search" not in OUTCOMES  # not a registry entry
    runner = CliRunner()
    out = runner.invoke(root, ["signals", "search", "momentum", "--format", "json"])
    assert out.exit_code == 0, out.output
    out = runner.invoke(
        root, ["signals", "search", "momentum", "--role", "regime", "--format", "json"]
    )
    assert out.exit_code == 0, out.output
    out = runner.invoke(root, ["blocks", "search", "momentum", "--format", "json"])
    assert out.exit_code == 0, out.output
    assert [s.get("role") for s in seen] == ["signal", "regime", None], seen
    assert all(s["q"] == "momentum" for s in seen)


def test_every_research_read_names_its_card_layout(monkeypatch) -> None:
    """WL-AG-10 (13 §2.1, RI-603 SDK half): the SDK sets ``view.mode`` (and ``view.size``) for every tool and read, so
    the card picks its layout by a served field and only a read of one whole study draws the study card: the list,
    one chart's data, addresses and a project drew an empty "Untitled" study card. Real keel-api shapes
    (research_server_shapes.json). SEED (reversed): ``_list`` sends ``mode="study"`` -> this reds on the list arm;
    the study read (control) is ``study`` either way. An unknown mode or size raises (``study_result``)."""
    from keel.tools.outcomes._research_commands import study_result

    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    study, project = SHAPES["study_done"], SHAPES["project"]
    sid, pid = study["study"]["study_id"], project["project_id"]
    page = SHAPES["data_page"]
    answers = {
        ("get", f"/v1/research/studies/{sid}/versions/1/data"): page,
        ("get", f"/v1/research/studies/{sid}"): study,
        ("get", "/v1/research/studies"): SHAPES["list"],
        ("get", f"/v1/research/projects/{pid}"): project,
        ("get", "/v1/research/addresses"): {"subject": "s", "data": [], "pagination": {"has_more": False}, "rows_total": 0, "by_reason": {}},
        ("post", "/v1/research/plans"): SHAPES["plan"],
    }  # fmt: skip

    class Client:
        def get(self, path, *, headers=None, **params):
            best = max(
                (k for k in answers if k[0] == "get" and path.startswith(k[1])),
                key=lambda k: len(k[1]),
            )
            return json.loads(json.dumps(answers[best]))

        def post(self, path, json=None, *, headers=None, **params):
            return __import__("json").loads(__import__("json").dumps(answers[("post", path)]))

    _bootstrap()
    ctx = ToolContext(api_client=Client())

    def view(tool: str, args: dict) -> tuple[str, str]:
        got = OUTCOMES[tool].handler(args, ctx).extra["view"]
        return got["mode"], got["size"]

    assert view("keel_research_get", {"study_id": sid}) == ("study", "view")
    assert view("keel_research_get", {}) == ("list", "view")
    assert view("keel_research_get", {"study_id": sid, "view": 1}) == ("chart", "view")
    assert view("keel_research_get", {"subject": 'Strategy("str_a@3")'}) == ("addresses", "receipt")
    assert view("keel_research_get", {"project_id": pid}) == ("project", "receipt")
    assert view(
        "keel_research_plan", {"template": "check", "subjects": ['Strategy("str_a@3")']}
    ) == ("plan", "receipt")
    with pytest.raises(ValueError):
        study_result({}, "x", mode="tile")


def test_r24_a_hosted_agent_finds_keels_library_signals_and_benchmarks(monkeypatch) -> None:
    """R-24 (WL-AG-08, A3 #1): ``keel_research_get(library="signals")`` reads ``GET /v1/research/library?kind=signals``
    and writes each item with its exact subject call (the served ``writes``) and the plan that studies one; on the
    listed profile too (get is listed), so a hosted agent can study Keel's 7-day momentum from the connector alone.
    ``benchmarks`` reads the strategies kind and keeps the benchmarks. A one-row receipt card (mode ``library``).
    SEED (reversed): drop the ``library`` arm from ``_handler`` -> the call lists the user's studies instead (this
    reds); control: a call with no library still lists studies."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    calls: list = []

    class Client:
        def get(self, path, *, headers=None, **params):
            calls.append((path, params))
            if path == "/v1/research/library":
                key = {"signals": "library_signals", "strategies": "library_strategies"}[
                    params["kind"]
                ]
                return json.loads(json.dumps(SHAPES[key]))
            return json.loads(json.dumps({**SHAPES["list"]}))

    from keel.tools.outcomes._toolsets import research_served_context

    _bootstrap()
    ctx = ToolContext(api_client=Client())
    with research_served_context(True):
        got = OUTCOMES["keel_research_get"].handler({"library": "signals"}, ctx)
        text = got.extra["view"]["markdown"]
        assert calls[-1] == ("/v1/research/library", {"kind": "signals"})
        assert got.extra["view"]["mode"] == "library" and got.extra["view"]["size"] == "receipt"
        assert (
            '- 7-day momentum · the top 60 by volume · Output("keel/momentum_7d@1", output="momentum_7d")'
            in text
        )
        assert 'Study one: keel_research_plan(template="signal", subjects=[' in text
        bench = (
            OUTCOMES["keel_research_get"]
            .handler({"library": "benchmarks"}, ctx)
            .extra["view"]["markdown"]
        )
        assert calls[-1] == ("/v1/research/library", {"kind": "strategies"})
        assert '- BTC buy and hold · BTC alone · Strategy("keel/btc_hold@1")' in bench
        assert (
            "Add one to a study: keel_research_plan(study_id=<its id>, add=['Strategy(\"keel/btc_hold@1\")'])"
            in bench
        )
        listed = OUTCOMES["keel_research_get"].handler({}, ctx)  # control: no library lists studies
        assert calls[-1][0] == "/v1/research/studies" and listed.extra["view"]["mode"] == "list"
        with pytest.raises(KeelError):
            OUTCOMES["keel_research_get"].handler({"library": "signals", "study_id": "stu_a"}, ctx)


def test_d415_the_users_own_signals_are_listed_with_their_calls(monkeypatch) -> None:
    """D-415 D5 (SIGNALS-FIRST): ``keel_research_get(library="mine")`` lists the user's own signals in the library
    list's shape: keel-api's ``GET /v1/strategies?kind=signal`` (rows served as signals only; a screen in the page is
    never listed), each with its exact call ``Output("str_…@n", output="…")`` from ``GET /v1/blocks?scope=mine`` at that
    version, and the plan that studies one; a signal whose output was not read says so, never a guessed call. On the
    listed profile too. ``keel signals list`` is the same read (a CLI preset, not a registry entry).
    SEED (reversed by edit): ``_mine`` keying outputs by block id alone (no version) -> the old version's output is
    named and this reds; control: ``library="signals"`` still reads Keel's library."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    calls: list = []
    sid = "str_01m7xpdf9w5hkeywp3gyaz93q4"

    class Client:
        def get(self, path, *, headers=None, **params):
            calls.append((path, params))
            if path == "/v1/strategies":
                return {
                    "data": [
                        {
                            "id": sid,
                            "name": "Momentum carry skill mix",
                            "kind": "signal",
                            "current_sequence": 2,
                        },
                        {
                            "id": "str_unread",
                            "name": "Funding fade",
                            "kind": "signal",
                            "current_sequence": 1,
                        },
                        {
                            "id": "str_screen",
                            "name": "OI screen",
                            "kind": "screener",
                            "current_sequence": 4,
                        },
                    ],
                    "pagination": {"has_more": False},
                }
            if path == "/v1/blocks":
                return {
                    "items": [
                        {"ref": f"{sid}@1", "block": sid, "output": "old_mix", "role": "signal"},
                        {"ref": f"{sid}@2", "block": sid, "output": "eligible", "role": "universe"},
                        {"ref": f"{sid}@2", "block": sid, "output": "mix", "role": "signal"},
                    ]
                }
            if path == "/v1/research/library":
                return json.loads(json.dumps(SHAPES["library_signals"]))
            raise AssertionError(path)

    from keel.tools.outcomes._toolsets import research_served_context

    _bootstrap()
    ctx = ToolContext(api_client=Client())
    with research_served_context(True):
        got = OUTCOMES["keel_research_get"].handler({"library": "mine"}, ctx)
        text = got.extra["view"]["markdown"]
        assert calls[0] == (
            "/v1/strategies",
            {"kind": "signal", "limit": 100, "sort": "updated_desc"},
        )
        assert calls[1] == ("/v1/blocks", {"scope": "mine", "limit": 50})
        assert got.extra["view"]["mode"] == "library" and got.extra["view"]["size"] == "receipt"
        assert text.startswith("Your signals: 2 shown of 2"), text
        assert f'- Momentum carry skill mix · v2 · Output("{sid}@2", output="mix")' in text
        assert "- Funding fade · v1 · its output could not be read just now" in text
        assert "OI screen" not in text
        assert 'Study one: keel_research_plan(template="signal", subjects=[' in text
        # control: Keel's library is unchanged
        keel = OUTCOMES["keel_research_get"].handler({"library": "signals"}, ctx)
        assert calls[-1] == ("/v1/research/library", {"kind": "signals"})
        assert keel.extra["view"]["markdown"].startswith("Keel's library, signals")


def test_d415_keel_signals_list_is_research_get_library_mine() -> None:
    """D-415 D5: ``keel signals list`` is ``keel research get --library mine``, a CLI preset beside ``keel signals
    search``; no new registry entry. SEED (reversed): delete the ``("signals", "list")`` row of
    ``_CLI_PRESET_ALIASES`` -> "No such command" (this reds); control: ``keel signals search`` still answers."""
    import dataclasses

    import click
    from click.testing import CliRunner
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._base import OutcomeResult
    from keel.tools.outcomes._cli_adapter import register_all
    from keel.tools.outcomes._toolsets import research_served_context

    _bootstrap()
    seen: list[dict] = []

    def handler(args, ctx):
        seen.append(dict(args))
        return OutcomeResult(extra={"data": []})

    outcomes = dict(OUTCOMES)
    outcomes["keel_research_get"] = dataclasses.replace(
        OUTCOMES["keel_research_get"], handler=handler
    )
    outcomes["keel_blocks_search"] = dataclasses.replace(
        OUTCOMES["keel_blocks_search"], handler=handler
    )
    root = click.Group("keel")
    with research_served_context(True):
        register_all(root, outcomes)
    assert "keel_signals_list" not in OUTCOMES
    runner = CliRunner()
    out = runner.invoke(root, ["signals", "list", "--format", "json"])
    assert out.exit_code == 0, out.output
    out = runner.invoke(root, ["signals", "search", "carry", "--format", "json"])
    assert out.exit_code == 0, out.output
    assert seen[0].get("library") == "mine", seen
    assert seen[1].get("role") == "signal", seen


def test_the_widest_data_read_keeps_its_rows_in_the_text_not_the_model_context() -> None:
    """AT-5 v2's data arm (13 §3.5): a Map's view-0 page (25 heatmap rows, the validated world's) was 12,230 B of
    structuredContent with its rows in it (CARDUI's measure). The text writes every row (07 §3.2 rule 4), so the rows
    ride the card; the facts and the cursor stay. SEED (reversed): drop "data" from the study channel's card set ->
    the rows return to structuredContent and this reds; control: the text still names every row."""
    from keel.tools.outcomes import _channels
    from keel.tools.outcomes._research_view import data_text

    page = json.loads(json.dumps(SHAPES["data_page_map"]))
    text = data_text(
        page, title="Lookback × hold", version=1, call_args={"study_id": "stu_m", "view": 0}
    )
    env = {
        **page,
        "links": {"app": "u"},
        "view": {"kind": "study", "mode": "chart", "size": "view", "markdown": text},
    }
    assert _compact(page["data"]) > 3_000  # not vacuous: a wide page
    structured, card = _channels.partition(env, "study", move_card=True)
    assert _compact(structured) <= 8_000, _compact(structured)
    assert "data" not in structured and card["data"] == page["data"]
    assert structured["pagination"]["cursor"] == "c25" and structured["facts"]["rows_total"]
    assert text.count("\n| ") == len(page["data"]) + 1  # every row (and the header) is in the text


def test_the_research_cli_help_is_written_for_a_person() -> None:
    """WL-AG-15 (A3 #6, 07 §3.2 rule 11): every `keel research` verb's help is one line with no spec citation (16 of
    24 read "`keel research add` (07 §2.2)"), every flag says what it takes, and `plan --help` gives one example per
    form. SEED (reversed): drop the ("research", "view-set") row from ``RESEARCH_CLI_HELP`` (research_cli) ->
    view-set's help is its agent text ("Use when …") and this reds; control: a non-research command's help is unchanged."""
    import re as _re

    import click
    from click.testing import CliRunner
    from keel.tools.outcomes._cli_adapter import register_all
    from keel.tools.outcomes.research_cli import RESEARCH_CLI_HELP

    _bootstrap()
    root = click.Group("keel")
    register_all(root, OUTCOMES)
    group = root.commands["research"]
    assert len(group.commands) == 24  # not vacuous: every research verb
    runner = CliRunner()
    for name, cmd in group.commands.items():
        assert ("research", name) in RESEARCH_CLI_HELP, name
        out = runner.invoke(root, ["research", name, "--help"]).output
        assert not _re.search(r"\\d\\d §", out), (name, out)
        assert "Use when" not in cmd.help, name
        for param in cmd.params:
            if isinstance(param, click.Option) and param.name not in ("help",):
                assert param.help, (name, param.name)
    plan = runner.invoke(root, ["research", "plan", "--help"]).output
    assert plan.count("keel research plan ") >= 9
    assert "Run on yours" not in plan and "--share-id" in plan


def test_a_research_answer_on_a_terminal_is_its_text_with_keel_commands() -> None:
    """WL-AG-14 (A3 #5): in human output a study, plan, list or data answer prints its text, each next call as the
    `keel` command that makes it, and nothing after the View in Keel line; it printed the text, then every envelope
    field as indented JSON (plan source, steps, children), and its next calls were MCP syntax. Real keel-api shapes.
    SEED (reversed): drop the `study` branch in ``_cli_adapter._render`` -> the field dump returns (this reds);
    control: `--format json` still prints the whole envelope."""
    import dataclasses

    import click
    from click.testing import CliRunner
    from keel.tools.outcomes._cli_adapter import register_all
    from keel.tools.outcomes._research_commands import study_result
    from keel.tools.outcomes._research_view import plan_text, study_text

    _bootstrap()
    study, plan = SHAPES["study_done"], SHAPES["plan"]
    answers = {
        "keel_research_get": lambda args, ctx: study_result(study, study_text(study), mode="study"),
        "keel_research_plan": lambda args, ctx: study_result(
            plan, plan_text(plan), size="receipt", mode="plan"
        ),
    }
    outcomes = dict(OUTCOMES)
    for name, handler in answers.items():
        outcomes[name] = dataclasses.replace(OUTCOMES[name], handler=handler)
    root = click.Group("keel")
    register_all(root, outcomes)
    runner = CliRunner()
    got = runner.invoke(root, ["research", "get", "stu_x", "--format", "human"]).output
    lines = got.rstrip().splitlines()
    assert lines[0].startswith("Check a strategy · ") and lines[-1].startswith(
        "View in Keel: https://"
    )
    assert (
        "keel_research_" not in got
        and "keel research get stu_chkv3000000000000000000000 --view 0" in got
    )
    assert not any(line.lstrip().startswith(("{", '"', "source:", "steps:")) for line in lines), (
        got[-600:]
    )
    planned = runner.invoke(
        root, ["research", "plan", "--template", "check", "--format", "human"]
    ).output
    assert f"keel research run {plan['plan_id']}" in planned and "children" not in planned
    as_json = runner.invoke(
        root, ["research", "get", "stu_x", "--format", "json"]
    ).output  # control
    assert json.loads(as_json)["study"]["study_id"] == study["study"]["study_id"]


def test_view_set_renames_by_set_and_blocks_search_names_its_population(monkeypatch) -> None:
    """WL-AG-17 (A3 4.6, 4.10): ``keel_research_view_set`` takes D-191's ``set`` ({title} or {question}), sent as the
    presentation route's StudyTextSet, alone (with source or revert_to it is refused before any call); and
    ``keel_blocks_search``'s text says "Showing N of M" with each output's call. SEED (reversed): drop ``"set":
    text_set`` from view_set's body -> the route gets no set (this reds); control: a source edit still sends source."""
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")
    sent: list = []
    study = SHAPES["study_done"]
    sid = study["study"]["study_id"]

    class Client:
        def put(self, path, json=None):
            sent.append((path, json))
            return {**study, "saved": "Saved revision 2."}

        def get(self, path, **params):
            sent.append((path, params))
            return {
                "items": [
                    {
                        "ref": "str_oi@2",
                        "output": "oi_chg7",
                        "name": "OI build-up",
                        "role": "signal",
                    },
                    {"ref": "str_mom@1", "output": "mom", "name": "Momentum", "role": "signal"},
                ],
                "total": 12,
            }

    _bootstrap()
    ctx = ToolContext(api_client=Client())
    view_set = OUTCOMES["keel_research_view_set"].handler
    view_set(
        {"study_id": sid, "expected_revision": 1, "set": {"title": "Momentum, costs at 3x"}}, ctx
    )
    assert (
        sent[-1][1]["set"] == {"title": "Momentum, costs at 3x"}
        and sent[-1][1].get("source") is None
    )
    view_set(
        {"study_id": sid, "expected_revision": 1, "set": '{"question": null}'}, ctx
    )  # the CLI's JSON text
    assert sent[-1][1]["set"] == {"question": None}
    with pytest.raises(KeelError):
        view_set(
            {"study_id": sid, "expected_revision": 1, "set": {"title": "x"}, "revert_to": 1}, ctx
        )
    view_set({"study_id": sid, "expected_revision": 1, "source": "Bars()"}, ctx)  # control
    assert sent[-1][1]["source"] == "Bars()" and sent[-1][1].get("set") is None
    found = OUTCOMES["keel_blocks_search"].handler({"q": "oi", "role": "signal"}, ctx)
    text = found.extra["view"]["markdown"]
    assert text.splitlines()[0] == "Showing 2 of 12 outputs"
    assert '- OI build-up · oi_chg7 · signal · Output("str_oi@2", output="oi_chg7")' in text
    assert found.extra["total"] == 12  # the route's JSON stays the structured answer


def test_d344_plan_and_run_describe_their_own_cards_and_run_reads_outcome_neutral() -> None:
    """D-344 (8), CONFORM-MCP F10: the study card's widgetDescription (a chart card) is false for plan (one row) and
    run (the run card), so each has its own (TOOL_WIDGET_DESCRIPTIONS, as the paper writes); run's `invoked` is
    outcome-neutral ("Run returned": a run whose work is all reused completes at once, a refused one starts nothing).
    SEED (reversed): drop the keel_research_run row -> run falls back to the study card's words (this reds); control:
    get keeps the study card's description."""
    from keel.widgets import (
        MAX_WIDGET_DESCRIPTION_CHARS,
        TOOL_INVOCATION_STRINGS,
        TOOL_WIDGET_DESCRIPTIONS,
        WIDGET_DESCRIPTIONS,
    )

    for tool in ("keel_research_plan", "keel_research_run"):
        said = TOOL_WIDGET_DESCRIPTIONS[tool]
        assert said != WIDGET_DESCRIPTIONS["study"] and len(said) <= MAX_WIDGET_DESCRIPTION_CHARS
    assert "row" in TOOL_WIDGET_DESCRIPTIONS["keel_research_plan"]
    assert "run card" in TOOL_WIDGET_DESCRIPTIONS["keel_research_run"]
    assert "keel_research_get" not in TOOL_WIDGET_DESCRIPTIONS  # control: the study card's words
    assert TOOL_INVOCATION_STRINGS["keel_research_run"][1] == "Run returned"
