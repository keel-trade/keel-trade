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
from keel.errors import AuthError, KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import OutcomeResult, ToolContext
from keel.tools.outcomes._render import SURFACE_HINTS, card_render_block


_bootstrap()

_MINT_RESPONSE = {
    "embed_id": "emb_01TEST",
    "embed_token": "tok.jwt.value",
    "embed_url": "https://server-built.example/embed/backtest/btr_1?token=tok.jwt.value",
    "expires_at": "2026-07-17T01:00:00+00:00",
    "resource_type": "backtest",
    "resource_id": "btr_1",
}


@pytest.fixture
def ctx():
    return ToolContext(is_tty=False, app_url="https://app.usekeel.io")


@pytest.fixture
def auth_ctx():
    """Authenticated context: the handler already holds a client."""
    client = MagicMock()
    client.post.return_value = dict(_MINT_RESPONSE)
    return ToolContext(api_client=client, is_tty=False, app_url="https://app.usekeel.io")


# ─── The hint table itself ──────────────────────────────────────────────


def test_surface_hints_cover_the_capability_matrix():
    """One hint per surface family from research/05; each carries the
    plain-link-line instruction (the non-negotiable fallback)."""
    assert set(SURFACE_HINTS) == {"claude", "claude_code", "cursor", "chatgpt"}
    for surface, hint in SURFACE_HINTS.items():
        assert "link line" in hint, f"{surface}: hint must mandate the plain link line"


def test_surface_hints_encode_the_per_surface_rules():
    # claude.ai/Desktop: widgets + URL fallback, never bare image blocks.
    assert "never emit bare image blocks" in SURFACE_HINTS["claude"]
    # Claude Code: metrics table + URL + `keel open` hint.
    assert "metrics table" in SURFACE_HINTS["claude_code"]
    assert "keel open" in SURFACE_HINTS["claude_code"]
    # Cursor: image block acceptable + URL.
    assert "image is acceptable" in SURFACE_HINTS["cursor"]
    # ChatGPT: inline card; fullscreen on request; no PiP on mobile.
    assert "fullscreen only on user request" in SURFACE_HINTS["chatgpt"]
    assert "no PiP on mobile" in SURFACE_HINTS["chatgpt"]


def test_card_render_block_shape(ctx):
    block = card_render_block("backtest", fallback_url="https://x/y", ctx=ctx, embed_id="btr_1")
    assert block["card"] == "backtest"
    assert block["card_resource_uri"] == "ui://keel/cards/backtest.html"
    assert block["fallback_url"] == "https://x/y"
    assert block["surface_hints"] is SURFACE_HINTS
    # M5.1 embed contract: {KEEL_APP_URL}/embed/{kind}/{id}; tokenless
    # here because there is no authenticated client to mint with —
    # and the block build never constructs one just to sign a nicety.
    assert block["embed_url"] == "https://app.usekeel.io/embed/backtest/btr_1"
    assert ctx.api_client is None


def test_preflight_has_no_embed_route(ctx):
    block = card_render_block("preflight", fallback_url="https://x/y", ctx=ctx, embed_id="str_1")
    assert "embed_url" not in block


# ─── Embed-token minting (M5 T2 integration: POST /v1/embeds) ───────────


def test_mint_attaches_signed_token_exactly_once(auth_ctx):
    block = card_render_block(
        "backtest", fallback_url="https://x/y", ctx=auth_ctx, embed_id="btr_1"
    )
    assert block["embed_url"] == "https://app.usekeel.io/embed/backtest/btr_1?token=tok.jwt.value"
    # Exactly ONE mint per emitted render block — no double-mint spam.
    auth_ctx.api_client.post.assert_called_once_with(
        "/v1/embeds", json={"resource_type": "backtest", "resource_id": "btr_1"}
    )


def test_mint_url_is_client_built_not_server_echoed(auth_ctx):
    """ctx.app_url stays the single URL knob: the minted URL must agree
    with hero/fallback URLs even if the server's app_base_url differs
    (only the token is taken from the mint response)."""
    block = card_render_block(
        "backtest", fallback_url="https://x/y", ctx=auth_ctx, embed_id="btr_1"
    )
    assert block["embed_url"].startswith("https://app.usekeel.io/embed/")
    assert "server-built.example" not in block["embed_url"]


@pytest.mark.parametrize(
    "failure",
    [
        KeelError("boom"),
        AuthError("not authenticated"),
    ],
)
def test_mint_failure_falls_back_to_tokenless(auth_ctx, failure):
    """Delivery failure NEVER fails the tool — the tokenless URL ships
    exactly as before the mint existed (card JS hides the frame on 401)."""
    auth_ctx.api_client.post.side_effect = failure
    block = card_render_block(
        "backtest", fallback_url="https://x/y", ctx=auth_ctx, embed_id="btr_1"
    )
    assert block["embed_url"] == "https://app.usekeel.io/embed/backtest/btr_1"


@pytest.mark.parametrize("resp", [{}, {"embed_token": ""}, ["not-a-dict"], None])
def test_malformed_mint_response_falls_back_to_tokenless(auth_ctx, resp):
    auth_ctx.api_client.post.return_value = resp
    block = card_render_block(
        "backtest", fallback_url="https://x/y", ctx=auth_ctx, embed_id="btr_1"
    )
    assert block["embed_url"] == "https://app.usekeel.io/embed/backtest/btr_1"


def test_mint_programming_error_is_not_swallowed(auth_ctx):
    """never-fails ≠ swallow bugs (lessons.md): only KeelError delivery
    failures degrade; a TypeError is a defect and must propagate."""
    auth_ctx.api_client.post.side_effect = TypeError("bug")
    with pytest.raises(TypeError):
        card_render_block("backtest", fallback_url="https://x/y", ctx=auth_ctx, embed_id="btr_1")


def test_preflight_never_mints(auth_ctx):
    block = card_render_block(
        "preflight", fallback_url="https://x/y", ctx=auth_ctx, embed_id="str_1"
    )
    assert "embed_url" not in block
    auth_ctx.api_client.post.assert_not_called()


def test_no_embed_id_never_mints(auth_ctx):
    block = card_render_block("live", fallback_url="https://x/y", ctx=auth_ctx, embed_id=None)
    assert "embed_url" not in block
    auth_ctx.api_client.post.assert_not_called()


def test_dry_run_never_mints(auth_ctx):
    """Minting creates a live capability (a mutation) — dry-run contexts
    get the tokenless URL and no POST."""
    auth_ctx.dry_run = True
    block = card_render_block(
        "backtest", fallback_url="https://x/y", ctx=auth_ctx, embed_id="btr_1"
    )
    assert block["embed_url"] == "https://app.usekeel.io/embed/backtest/btr_1"
    auth_ctx.api_client.post.assert_not_called()


# ─── Render blocks on the four card tools ───────────────────────────────


def test_backtest_summarize_attaches_render_block(ctx):
    detail = {"id": "btr_1", "status": "completed", "strategy_id": "str_1", "metrics": {}}
    with (
        patch("keel.client.KeelClient.get", side_effect=[detail, {}]),
        patch("keel.client.KeelClient.post", return_value=dict(_MINT_RESPONSE)) as mock_post,
    ):
        env = OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "btr_1"}, ctx)
    envelope = env.to_envelope()
    render = envelope["render"]
    assert render["card"] == "backtest"
    assert render["fallback_url"] == envelope["hero_url"]
    # The handler's authenticated client mints the signed embed URL.
    assert render["embed_url"] == (
        "https://app.usekeel.io/embed/backtest/btr_1?token=tok.jwt.value"
    )
    # Exactly one POST for the whole response — the mint, nothing else.
    assert mock_post.call_count == 1
    assert envelope["url_line"] == f"View in Keel: {envelope['hero_url']}"


def test_backtest_summarize_mint_failure_keeps_tokenless_embed(ctx):
    detail = {"id": "btr_1", "status": "completed", "strategy_id": "str_1", "metrics": {}}
    with (
        patch("keel.client.KeelClient.get", side_effect=[detail, {}]),
        patch("keel.client.KeelClient.post", side_effect=KeelError("mint down")),
    ):
        env = OUTCOMES["keel_backtest_summarize"].handler({"backtest_id": "btr_1"}, ctx)
    envelope = env.to_envelope()
    assert envelope["render"]["embed_url"] == "https://app.usekeel.io/embed/backtest/btr_1"
    assert envelope["url_line"] == f"View in Keel: {envelope['hero_url']}"


def test_strategy_get_attaches_render_block(ctx):
    meta = {"id": "str_1", "name": "Momentum", "status": "active"}
    mint = {**_MINT_RESPONSE, "resource_type": "strategy", "resource_id": "str_1"}
    with (
        patch("keel.client.KeelClient.get", return_value=meta),
        patch("keel.client.KeelClient.post", return_value=mint) as mock_post,
    ):
        env = OUTCOMES["keel_strategy_get"].handler(
            {"strategy_id": "str_1", "include_versions": False, "include_source": False}, ctx
        )
    envelope = env.to_envelope()
    assert envelope["render"]["card"] == "strategy"
    assert envelope["render"]["embed_url"] == (
        "https://app.usekeel.io/embed/strategy/str_1?token=tok.jwt.value"
    )
    mock_post.assert_called_once_with(
        "/v1/embeds", json={"resource_type": "strategy", "resource_id": "str_1"}
    )
    assert envelope["url_line"] == f"View in Keel: {envelope['hero_url']}"


def test_live_monitor_attaches_render_block(ctx):
    mint = {**_MINT_RESPONSE, "resource_type": "live", "resource_id": "dep_1"}
    with (
        patch("keel.client.KeelClient.get", return_value={"positions": []}),
        patch("keel.client.KeelClient.post", return_value=mint) as mock_post,
    ):
        env = OUTCOMES["keel_live_monitor"].handler({"deployment_id": "dep_1"}, ctx)
    envelope = env.to_envelope()
    assert envelope["render"]["card"] == "live"
    assert envelope["render"]["embed_url"] == (
        "https://app.usekeel.io/embed/live/dep_1?token=tok.jwt.value"
    )
    assert mock_post.call_count == 1
    assert envelope["url_line"].startswith("View in Keel: https://")


def test_live_monitor_portfolio_has_no_embed(ctx):
    """No single deployment id → no embed route, no mint, but the card
    + URL line still ride along."""
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
    # preview + deploy-intent mint only — the preflight card never
    # triggers an embed mint (it has no embed route).
    assert client.post.call_count == 2


def test_live_deploy_preview_falls_back_to_overview_url(tmp_path, monkeypatch):
    """No deploy-intent minted → the strategy overview is the fallback
    (same routing as keel_open_in_app — one URL source of truth)."""
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
    assert envelope["render"]["fallback_url"] == "https://app.usekeel.io/strategies/str_1"
    assert envelope["url_line"] == "View in Keel: https://app.usekeel.io/strategies/str_1"


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
    env = OUTCOMES["keel_open_in_app"].handler({"id": "str_01X"}, ctx).to_envelope()
    assert env["url_line"] == "View in Keel: https://app.usekeel.io/strategies/str_01X"
