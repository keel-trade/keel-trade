"""Render-hint layer (spec 06 R3) + URL lines (R2/R4).

Hints are per-SURFACE presentation rules riding in tool responses —
derived from fields the tool already returns (D2.2 render-only rule),
never surface detection or per-surface business logic. Every canonical
URL also ships as one plain `url_line` so any host renders a clickable
link without envelope parsing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import OutcomeResult, ToolContext
from keel.tools.outcomes._render import SURFACE_HINTS, card_render_block


_bootstrap()


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


@pytest.fixture
def auth_ctx():
    """Authenticated context: the handler already holds a client."""
    client = MagicMock()
    return ToolContext(api_client=client, is_tty=False, app_url="https://app.usekeel.io")


# ─── The hint table itself ──────────────────────────────────────────────


def test_surface_hints_cover_the_capability_matrix():
    """One hint per surface family from research/05; each carries the
    plain link line (the non-negotiable fallback) — stated as a fact about
    the surface, never an imperative to the model (Q-1804)."""
    assert set(SURFACE_HINTS) == {"claude", "claude_code", "cursor", "chatgpt"}
    for surface, hint in SURFACE_HINTS.items():
        assert "link line" in hint, f"{surface}: hint must name the plain link line"
        for word in ("ALWAYS", "NEVER", "never", "always", "Relay", "include the"):
            assert word not in hint, f"{surface}: imperative {word!r} in the hint"


def test_surface_hints_encode_the_per_surface_rules():
    # claude.ai/Desktop: the card (in-card chart) + URL fallback, never
    # bare image blocks.
    assert "bare image blocks collapse behind an expander" in SURFACE_HINTS["claude"]
    assert "in-card chart" in SURFACE_HINTS["claude"]
    # Claude Code: the VIEW as-is + URL + `keel open` hint. It used to
    # say "show a compact metrics table" — the one thing the render
    # cadence contract forbids (BUILD §3.4): the view already IS the
    # table, and a second one is the same numbers twice.
    assert "`view.markdown` is the complete rendering" in SURFACE_HINTS["claude_code"]
    assert "a receipt is one line" in SURFACE_HINTS["claude_code"]
    assert "metrics table" not in SURFACE_HINTS["claude_code"]
    assert "keel open" in SURFACE_HINTS["claude_code"]
    # Cursor: image block acceptable + URL.
    assert "image is acceptable" in SURFACE_HINTS["cursor"]
    # ChatGPT: inline card; fullscreen on request; no PiP on mobile.
    assert "fullscreen is the user's choice" in SURFACE_HINTS["chatgpt"]
    assert "no PiP on mobile" in SURFACE_HINTS["chatgpt"]


def test_card_render_block_shape(ctx):
    block = card_render_block("backtest", fallback_url="https://x/y", ctx=ctx, embed_id="btr_1")
    assert block == {
        "card": "backtest",
        "card_resource_uri": "ui://keel/cards/backtest.html",
        "fallback_url": "https://x/y",
        "surface_hints": SURFACE_HINTS,
    }
    assert ctx.api_client is None


@pytest.mark.parametrize("kind", ["backtest", "strategy", "live", "preflight"])
def test_no_card_carries_an_embed_route(auth_ctx, kind):
    """Q-1505: the nested embed iframe is gone from every card — Claude
    restricts third-party frames, so the block carries no `embed_url`
    and building it never mints (no POST, no network) even with an
    authenticated client in hand."""
    block = card_render_block(kind, fallback_url="https://x/y", ctx=auth_ctx, embed_id="id_1")
    assert "embed_url" not in block
    auth_ctx.api_client.post.assert_not_called()
    auth_ctx.api_client.get.assert_not_called()


# ─── Render blocks on the four card tools ───────────────────────────────


def test_backtest_summarize_attaches_render_block(ctx):
    detail = {"id": "btr_1", "status": "completed", "strategy_id": "str_1", "metrics": {}}
    with (
        patch("keel.client.KeelClient.get", side_effect=[detail, {}, {}]),
        patch("keel.client.KeelClient.post") as mock_post,
    ):
        env = OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "btr_1"}, ctx)
    envelope = env.to_envelope()
    render = envelope["render"]
    assert render["card"] == "backtest"
    assert render["fallback_url"] == envelope["hero_url"]
    assert "embed_url" not in render
    # No POST at all for a read: nothing is minted (Q-1505).
    mock_post.assert_not_called()
    assert envelope["url_line"] == f"View in Keel: {envelope['hero_url']}"


def test_strategy_get_attaches_render_block(ctx):
    meta = {"id": "str_1", "name": "Momentum", "status": "active"}
    with (
        patch("keel.client.KeelClient.get", return_value=meta),
        patch("keel.client.KeelClient.post") as mock_post,
    ):
        env = OUTCOMES["keel_strategy_get"].handler(
            {"strategy_id": "str_1", "include_versions": False, "include_source": False}, ctx
        )
    envelope = env.to_envelope()
    assert envelope["render"]["card"] == "strategy"
    assert "embed_url" not in envelope["render"]
    mock_post.assert_not_called()
    assert envelope["url_line"] == f"View in Keel: {envelope['hero_url']}"


def test_live_monitor_attaches_render_block(ctx):
    with (
        patch("keel.client.KeelClient.get", return_value={"positions": []}),
        patch("keel.client.KeelClient.post") as mock_post,
    ):
        env = OUTCOMES["keel_live_monitor"].handler({"deployment_id": "dep_1"}, ctx)
    envelope = env.to_envelope()
    assert envelope["render"]["card"] == "live"
    assert "embed_url" not in envelope["render"]
    mock_post.assert_not_called()
    assert envelope["url_line"].startswith("View in Keel: https://")


def test_live_monitor_portfolio_carries_card_and_url_line(ctx):
    with (
        patch("keel.client.KeelClient.get", return_value={}),
        patch("keel.client.KeelClient.post") as mock_post,
    ):
        env = OUTCOMES["keel_live_monitor"].handler(
            {"deployment_id": "all", "view": "portfolio"}, ctx
        )
    envelope = env.to_envelope()
    assert envelope["render"]["card"] == "live"
    assert "embed_url" not in envelope["render"]
    mock_post.assert_not_called()
    assert "url_line" in envelope


def test_live_deploy_preview_attaches_preflight_card(tmp_path, monkeypatch):
    """The preview envelope carries the preflight card + an explicit
    URL line (it has no hero_url — the fallback is the handoff URL or
    the strategy overview)."""
    from pathlib import Path
    from unittest.mock import MagicMock

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.post.side_effect = [
        {"derived_schedule": "1h", "est_slippage": 0.001, "est_fees": 0.0004},
        {
            "handoff_url": "https://app.usekeel.io/deploy/di_1",
            "intent_token": "di_1",
            "suggested_config": {"sizing_usd": 1000, "sizing_basis": "drawdown"},
        },
    ]
    live_ctx = ToolContext(api_client=client, app_url="https://app.usekeel.io")
    env = OUTCOMES["keel_live_deploy"].handler(
        {"strategy_id": "str_1", "account_id": "acc_1", "preview": True, "direct": True}, live_ctx
    )
    envelope = env.to_envelope()
    assert envelope["render"]["card"] == "preflight"
    assert envelope["render"]["fallback_url"] == "https://app.usekeel.io/deploy/di_1"
    assert envelope["url_line"] == "View in Keel: https://app.usekeel.io/deploy/di_1"
    assert "embed_url" not in envelope["render"]
    # preview + deploy-intent mint only — the render block never posts.
    assert client.post.call_count == 2


def test_live_deploy_preview_falls_back_to_overview_url(tmp_path, monkeypatch):
    """No deploy-intent minted → the strategy overview is the fallback
    (same routing as keel_app_link — one URL source of truth)."""
    from pathlib import Path
    from unittest.mock import MagicMock

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    client.post.side_effect = [
        {"derived_schedule": "1h"},
        {},  # mint response with no handoff_url → intent omitted
    ]
    live_ctx = ToolContext(api_client=client, app_url="https://app.usekeel.io")
    env = OUTCOMES["keel_live_deploy"].handler(
        {"strategy_id": "str_1", "account_id": "acc_1", "preview": True, "direct": True}, live_ctx
    )
    envelope = env.to_envelope()
    assert envelope["render"]["fallback_url"] == "https://app.usekeel.io/strategies/str_1/edit"
    assert envelope["url_line"] == "View in Keel: https://app.usekeel.io/strategies/str_1/edit"


# ─── URL lines everywhere (spec 06 R4) ──────────────────────────────────


def test_url_line_derives_from_hero_url():
    env = OutcomeResult(hero_url="https://app.usekeel.io/strategies/str_1").to_envelope()
    assert env["url_line"] == "View in Keel: https://app.usekeel.io/strategies/str_1"


def test_url_line_falls_back_to_share_url():
    env = OutcomeResult(share_url="https://usekeel.io/share/shr_1").to_envelope()
    assert env["url_line"] == "View in Keel: https://usekeel.io/share/shr_1"


def test_url_line_absent_without_canonical_url():
    env = OutcomeResult().to_envelope()
    assert "url_line" not in env


def test_extra_url_line_passes_through_only_when_unset():
    # Handler-supplied line survives when there's no canonical URL...
    env = OutcomeResult(extra={"url_line": "View in Keel: https://x/y"}).to_envelope()
    assert env["url_line"] == "View in Keel: https://x/y"
    # ...but can never clobber the derived one.
    env2 = OutcomeResult(hero_url="https://a/b", extra={"url_line": "spoofed"}).to_envelope()
    assert env2["url_line"] == "View in Keel: https://a/b"


def test_open_in_app_carries_url_line(ctx):
    env = OUTCOMES["keel_app_link"].handler({"id": "str_01X"}, ctx).to_envelope()
    # V-6 (ratified 2026-09-19): a strategy link lands in the editor.
    assert env["url_line"] == "View in Keel: https://app.usekeel.io/strategies/str_01X/edit"
