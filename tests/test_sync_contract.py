"""Spec 08 state-model tests — pull-through write-back (R3) + the
three-way conflict envelope (R4).

Server HEAD is the single source of truth. These tests pin:

  * compose on a machine with a checkout updates the file (hash match
    after compose) — and NEVER clobbers uncommitted local edits;
  * hosted mode never touches the workspace (no local context exists);
  * a stale checkout's `status` says exactly ONE thing to do
    (`keel_strategy_pull`);
  * a true conflict (local edited AND server moved) stops push/pull with
    the three-way envelope, and both documented recovery paths work
    (pull_force, explicit commit_id pinning).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap
from keel.workspace import (
    STRATEGY_FILE,
    WorkspaceMeta,
    _compute_hash,
)


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()
    from keel.tools.outcomes import (  # noqa: F401
        backtest_run,
        strategy_compose,
        strategy_pull,
        strategy_push,
        strategy_status,
    )


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    """Isolated home workspace root + cwd outside any project."""
    monkeypatch.chdir(tmp_path)
    with patch("keel.workspace.WORKSPACE_ROOT", tmp_path / "ws"):
        yield tmp_path / "ws"


def _checkout(root, strategy_id: str, source: str, name: str = "WS") -> None:
    ws_dir = root / strategy_id
    ws_dir.mkdir(parents=True)
    (ws_dir / STRATEGY_FILE).write_text(source)
    WorkspaceMeta(
        strategy_id=strategy_id,
        name=name,
        source_hash=_compute_hash(source),
        checked_out_at="2026-07-16T00:00:00Z",
        current_sequence=1,
    ).save(ws_dir)


def _compose_ctx(fake_client) -> ToolContext:
    return ToolContext(api_client=fake_client, is_tty=False, app_url="https://app.usekeel.io")


class _FakeClient:
    """Minimal KeelClient stand-in recording calls."""

    def __init__(self, patch_payload=None):
        self.patch_payload = patch_payload or {}
        self.calls = []

    def patch(self, path, json=None, **kw):
        self.calls.append(("PATCH", path, json))
        return self.patch_payload

    def get(self, path, **params):
        self.calls.append(("GET", path, params or None))
        return {}

    def post(self, path, json=None, **kw):
        self.calls.append(("POST", path, json))
        return {}


def _valid(monkeypatch):
    import keel.tools.outcomes.strategy_compose as mod

    monkeypatch.setattr(
        mod,
        "_try_local_validate",
        lambda src: {"ok": True, "warnings": [], "errors": [], "lock": None},
    )


# ─── R3: compose write-back ───────────────────────────────────────────────


def test_compose_update_writes_back_to_clean_checkout(workspace_root, monkeypatch):
    """Compose on a machine with a clean checkout updates file + meta.

    Spec 08 AC: hash match after compose.
    """
    _valid(monkeypatch)
    _checkout(workspace_root, "str_ws", "OLD SOURCE")
    new_source = "NEW SOURCE"
    new_hash = _compute_hash(new_source)
    fake = _FakeClient(
        patch_payload={
            "strategy_id": "str_ws",
            "current_sequence": 2,
            "source_hash": new_hash,
            "name": "WS",
        }
    )

    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": new_source, "strategy_id": "str_ws"}, _compose_ctx(fake))
        .to_envelope()
    )

    # File written back — hash-match after compose.
    file_text = (workspace_root / "str_ws" / STRATEGY_FILE).read_text()
    assert file_text == new_source
    assert _compute_hash(file_text) == new_hash

    # Meta synced to the new server hash + sequence.
    meta = WorkspaceMeta.load(workspace_root / "str_ws")
    assert meta.source_hash == new_hash
    assert meta.current_sequence == 2

    # Surfaced in the envelope.
    assert env["workspace_sync"]["status"] == "written_back"
    assert env["workspace_sync"]["server_hash"] == new_hash


def test_compose_update_meta_syncs_when_file_already_matches(workspace_root, monkeypatch):
    """Compose from the checkout's own content → file untouched, meta synced."""
    _valid(monkeypatch)
    _checkout(workspace_root, "str_ws", "OLD SOURCE")
    # Simulate an edit made in the file, composed via inline source.
    edited = "EDITED SOURCE"
    (workspace_root / "str_ws" / STRATEGY_FILE).write_text(edited)
    fake = _FakeClient(
        patch_payload={
            "strategy_id": "str_ws",
            "current_sequence": 2,
            "source_hash": _compute_hash(edited),
        }
    )

    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": edited, "strategy_id": "str_ws"}, _compose_ctx(fake))
        .to_envelope()
    )

    assert env["workspace_sync"]["status"] == "meta_synced"
    meta = WorkspaceMeta.load(workspace_root / "str_ws")
    assert meta.source_hash == _compute_hash(edited)
    # Local is clean w.r.t. the new base: no stale/ahead state left behind.
    assert (
        _compute_hash((workspace_root / "str_ws" / STRATEGY_FILE).read_text()) == meta.source_hash
    )


def test_compose_update_never_clobbers_dirty_checkout(workspace_root, monkeypatch):
    """Local uncommitted edits differ from the composed source → file kept.

    Conflicts are never resolved silently (spec 08 contract): the
    response says what happened and what to do, the file is untouched,
    and meta keeps the old base so `status` reports the conflict.
    """
    _valid(monkeypatch)
    _checkout(workspace_root, "str_ws", "BASE SOURCE")
    local_edits = "LOCAL WIP EDITS"
    (workspace_root / "str_ws" / STRATEGY_FILE).write_text(local_edits)
    composed = "COMPOSED FROM CHAT"
    fake = _FakeClient(
        patch_payload={
            "strategy_id": "str_ws",
            "current_sequence": 2,
            "source_hash": _compute_hash(composed),
        }
    )

    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": composed, "strategy_id": "str_ws"}, _compose_ctx(fake))
        .to_envelope()
    )

    # File NOT overwritten.
    assert (workspace_root / "str_ws" / STRATEGY_FILE).read_text() == local_edits
    # Meta base unchanged → status will show local_modified + remote_modified.
    meta = WorkspaceMeta.load(workspace_root / "str_ws")
    assert meta.source_hash == _compute_hash("BASE SOURCE")
    sync = env["workspace_sync"]
    assert sync["status"] == "local_dirty"
    assert "keel_strategy_status" in sync["instruction"]


def test_compose_hosted_never_touches_workspace(monkeypatch):
    """Hosted regression (M1.2): no local context — write-back must not run."""
    _valid(monkeypatch)
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    fake = _FakeClient(
        patch_payload={
            "strategy_id": "str_h",
            "current_sequence": 2,
            "source_hash": "h",
        }
    )

    with patch("keel.workspace.get_workspace") as gw:
        env = (
            OUTCOMES["keel_strategy_compose"]
            .handler({"source": "S", "strategy_id": "str_h"}, _compose_ctx(fake))
            .to_envelope()
        )

    gw.assert_not_called()
    assert "workspace_sync" not in env


def test_compose_create_mode_does_no_write_back(workspace_root, monkeypatch):
    """Create mode (no strategy_id) has no checkout to sync — no write-back."""
    _valid(monkeypatch)

    class _CreateClient(_FakeClient):
        def post(self, path, json=None, **kw):
            self.calls.append(("POST", path, json))
            return {"strategy_id": "str_new", "current_sequence": 1}

    env = (
        OUTCOMES["keel_strategy_compose"]
        .handler({"source": "S", "name": "New"}, _compose_ctx(_CreateClient()))
        .to_envelope()
    )
    assert env["strategy_id"] == "str_new"
    assert "workspace_sync" not in env


# ─── R3: stale checkout → status says exactly one thing to do ────────────


def test_stale_checkout_status_says_exactly_pull(monkeypatch):
    """`behind` state → ONE instruction: keel_strategy_pull."""
    fake_status = {
        "strategy_id": "str_stale",
        "state": "behind",
        "name": "S",
        "local_hash": "aaa",
        "remote_hash": "bbb",
        "sequence": 7,
        "workspace": "/tmp/ws",
        "file": "/tmp/ws/strategy.py",
    }
    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    with patch("keel.workspace.status", return_value=fake_status):
        env = (
            OUTCOMES["keel_strategy_status"]
            .handler({"strategy_id": "str_stale", "no_ownership_hint": True}, ctx)
            .to_envelope()
        )
    assert env["status"] == "behind"
    assert len(env["next"]) == 1
    assert "keel_strategy_pull" in env["next"][0]


def test_stale_checkout_detected_end_to_end(workspace_root):
    """Server moved, local clean → real status() computes `behind` from hashes."""
    _checkout(workspace_root, "str_stale", "BASE")
    server_strategy = {
        "strategy_id": "str_stale",
        "name": "S",
        "source": "SERVER MOVED",
        "source_hash": _compute_hash("SERVER MOVED"),
        "current_sequence": 2,
    }
    inst = MagicMock()
    inst.get.return_value = server_strategy
    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")
    with patch("keel.client.KeelClient", return_value=inst):
        env = (
            OUTCOMES["keel_strategy_status"]
            .handler(
                {
                    "strategy_id": "str_stale",
                    "include_recent": False,
                    "no_ownership_hint": True,
                },
                ctx,
            )
            .to_envelope()
        )
    assert env["status"] == "behind"
    assert len(env["next"]) == 1
    assert "keel_strategy_pull" in env["next"][0]


# ─── R4: the three-way conflict envelope ─────────────────────────────────


def _conflicted_workspace(root) -> None:
    """Checkout at BASE, local edited to LOCAL (server will differ)."""
    _checkout(root, "str_cfl", "BASE")
    (root / "str_cfl" / STRATEGY_FILE).write_text("LOCAL")


_SERVER_HEAD_COMMIT = {
    "commit_id": "cmt_srv_head",
    "sequence_number": 9,
    "source_hash": _compute_hash("SERVER"),
    "message": "edited in chat",
    "created_at": "2026-07-16T22:00:00Z",
    "client_name": "claude.ai",
    "auth_surface": "hosted-mcp",
}


def test_push_conflict_raises_three_way_envelope(workspace_root):
    """Push into a moved server HEAD → sync_conflict with full R4 context."""
    from keel.errors import ConflictError

    _conflicted_workspace(workspace_root)
    inst = MagicMock()
    inst.patch.side_effect = ConflictError("Source hash mismatch")
    inst.get.return_value = {"data": [_SERVER_HEAD_COMMIT]}
    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")

    with patch("keel.client.KeelClient", return_value=inst):
        with pytest.raises(KeelError) as exc:
            OUTCOMES["keel_strategy_push"].handler({"strategy_id": "str_cfl"}, ctx)

    err = exc.value
    assert err.error_code == "sync_conflict"
    payload = err.input or {}
    # Three-way context
    assert payload["base_hash"] == _compute_hash("BASE")
    assert payload["local_hash"] == _compute_hash("LOCAL")
    assert payload["server_hash"] == _compute_hash("SERVER")
    assert payload["server_last_modified"] == "2026-07-16T22:00:00Z"
    assert payload["server_modified_via"].startswith("modified via claude.ai (hosted-mcp)")
    # Diff hint + the three recovery options (never force-push)
    assert payload["diff_hint"]["tool"] == "keel_strategy_diff"
    option_names = [o["option"] for o in payload["options"]]
    assert option_names == ["pull_force", "manual_merge", "pin_commit"]
    assert all(
        "force-push" not in str(o) and "push force" not in str(o) for o in payload["options"]
    )
    # Envelope wording is agent-actionable in one step
    envelope = err.to_envelope()
    assert "keel_strategy_pull" in envelope["what_was_expected"]


def test_pull_conflict_raises_envelope_and_pull_force_recovers(workspace_root):
    """Pull on a true conflict raises the envelope; pull_force recovers."""
    _conflicted_workspace(workspace_root)
    server_strategy = {
        "strategy_id": "str_cfl",
        "name": "CFL",
        "source": "SERVER",
        "source_hash": _compute_hash("SERVER"),
        "current_sequence": 9,
    }
    inst = MagicMock()
    inst.get.side_effect = lambda path, **kw: (
        {"data": [_SERVER_HEAD_COMMIT]} if path.endswith("/versions") else server_strategy
    )
    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")

    with patch("keel.client.KeelClient", return_value=inst):
        with pytest.raises(KeelError) as exc:
            OUTCOMES["keel_strategy_pull"].handler({"strategy_id": "str_cfl"}, ctx)

        payload = exc.value.input or {}
        assert exc.value.error_code == "sync_conflict"
        assert payload["base_hash"] == _compute_hash("BASE")
        assert payload["local_hash"] == _compute_hash("LOCAL")
        assert payload["server_hash"] == _compute_hash("SERVER")
        assert payload["server_modified_via"].startswith("modified via claude.ai")

        # Recovery path 1 (per the envelope's pull_force option): take server.
        env = (
            OUTCOMES["keel_strategy_pull"]
            .handler({"strategy_id": "str_cfl", "force": True}, ctx)
            .to_envelope()
        )
    assert env["status"] == "force_pulled"
    assert (workspace_root / "str_cfl" / STRATEGY_FILE).read_text() == "SERVER"
    meta = WorkspaceMeta.load(workspace_root / "str_cfl")
    assert meta.source_hash == _compute_hash("SERVER")


def test_conflict_recovery_by_commit_id_pinning(workspace_root):
    """Recovery path 2: pin an explicit commit_id on the blocked action.

    A conflicted workspace must not block `keel_backtest_run` when the
    agent pins a historical commit — the guard is skipped by design.
    """
    _conflicted_workspace(workspace_root)
    submitted = {"id": "bt_pinned", "status": "queued", "strategy_id": "str_cfl"}
    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")

    with (
        patch("keel.client.KeelClient.post", return_value=submitted) as post_mock,
        patch("keel.workspace.push") as push_mock,
    ):
        env = (
            OUTCOMES["keel_backtest_run"]
            .handler(
                {
                    "strategy_id": "str_cfl",
                    "commit_id": "cmt_srv_head",
                    "wait": False,
                    "no_ownership_hint": True,
                },
                ctx,
            )
            .to_envelope()
        )

    push_mock.assert_not_called()
    assert post_mock.call_args.kwargs["json"]["commit_id"] == "cmt_srv_head"
    assert env["run_id"] == "bt_pinned"


def test_conflict_envelope_builds_even_when_context_fetch_fails(workspace_root):
    """The envelope must not depend on the best-effort server-context GET."""
    from keel.errors import ConflictError

    _conflicted_workspace(workspace_root)
    inst = MagicMock()
    inst.patch.side_effect = ConflictError("Source hash mismatch")
    inst.get.side_effect = RuntimeError("network down")
    ctx = ToolContext(is_tty=False, app_url="https://app.usekeel.io")

    with patch("keel.client.KeelClient", return_value=inst):
        with pytest.raises(KeelError) as exc:
            OUTCOMES["keel_strategy_push"].handler({"strategy_id": "str_cfl"}, ctx)

    payload = exc.value.input or {}
    assert exc.value.error_code == "sync_conflict"
    assert payload["base_hash"] == _compute_hash("BASE")
    assert payload["server_hash"] is None
    assert payload["server_modified_via"] is None
    assert [o["option"] for o in payload["options"]] == [
        "pull_force",
        "manual_merge",
        "pin_commit",
    ]


# ─── R5: surface attribution surfaced in strategy_log ────────────────────


def test_strategy_log_surfaces_modified_via():
    """Commits render 'modified via <client> (<surface>), <age>' (spec 08 R5)."""
    from keel.tools.outcomes import strategy_log  # noqa: F401

    versions = [
        {
            "commit_id": "cmt_b",
            "sequence_number": 2,
            "source_hash": "b" * 64,
            "message": "tuned ROC",
            "created_at": "2026-07-16T22:00:00Z",
            "client_name": "claude.ai",
            "auth_surface": "hosted-mcp",
        },
        {
            # Legacy commit predating migration V36.3 — no attribution.
            "commit_id": "cmt_a",
            "sequence_number": 1,
            "source_hash": "a" * 64,
            "message": "initial",
            "created_at": "2026-06-01T00:00:00Z",
        },
    ]
    inst = MagicMock()
    inst.get.return_value = {"data": versions}
    ctx = ToolContext(api_client=inst, is_tty=False, app_url="https://app.usekeel.io")

    env = OUTCOMES["keel_strategy_log"].handler({"strategy_id": "str_x"}, ctx).to_envelope()

    head, legacy = env["commits"]
    assert head["client_name"] == "claude.ai"
    assert head["auth_surface"] == "hosted-mcp"
    assert head["modified_via"].startswith("modified via claude.ai (hosted-mcp)")
    assert legacy["modified_via"] is None  # nullable — never fabricated


def test_format_modified_via_shapes():
    from keel.workspace import format_modified_via

    assert format_modified_via(None, None, "2026-07-16T00:00:00Z") is None
    assert format_modified_via("web", "web", None) == "modified via web"
    s = format_modified_via("claude.ai", "hosted-mcp", "2026-07-16T22:00:00Z")
    assert s.startswith("modified via claude.ai (hosted-mcp), ")


# ─── R1: the contract is stated where agents read ────────────────────────


def test_state_model_contract_stated_in_agents_md():
    from pathlib import Path

    agents_md = (Path(__file__).parent.parent / "AGENTS.md").read_text(encoding="utf-8")
    assert "single source of truth" in agents_md
    assert "Write-through by default" in agents_md
    assert "Pull-through" in agents_md
    assert "sync_conflict" in agents_md
    assert "pull_force" in agents_md
    assert "modified_via" in agents_md


def test_workspace_family_descriptions_state_the_model():
    """An agent reading only tool descriptions must infer: server HEAD is
    the source of truth; the checkout is a working copy; write-through
    defaults; conflicts stop."""
    for tool in (
        "keel_strategy_checkout",
        "keel_strategy_push",
        "keel_strategy_compose",
        "keel_backtest_run",
        "keel_live_deploy",
        "keel_strategy_log",
    ):
        assert "source of truth" in OUTCOMES[tool].description, tool

    assert "WORKING COPY" in OUTCOMES["keel_strategy_checkout"].description
    for tool in ("keel_backtest_run", "keel_live_deploy"):
        desc = OUTCOMES[tool].description
        assert "Write-through" in desc, tool
        assert "auto_push=False" in desc, tool
        assert "conflict" in desc.lower(), tool


# ─── R5: surface self-identification header ──────────────────────────────


def test_surface_defaults_to_sdk(monkeypatch):
    import keel.surface as surface_mod

    monkeypatch.setattr(surface_mod, "_SURFACE", None)
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    assert surface_mod.current_surface() == "sdk"


def test_surface_hosted_mode_wins(monkeypatch):
    import keel.surface as surface_mod

    monkeypatch.setattr(surface_mod, "_SURFACE", "cli")
    monkeypatch.setenv("KEEL_EXECUTION_MODE", "hosted")
    assert surface_mod.current_surface() == "hosted-mcp"


def test_surface_rejects_unknown_values():
    from keel.surface import set_surface

    with pytest.raises(ValueError):
        set_surface("carrier-pigeon")


def test_client_sends_surface_header(monkeypatch):
    import keel.surface as surface_mod
    from keel.client import KeelClient
    from keel.config import KeelConfig

    monkeypatch.setattr(surface_mod, "_SURFACE", "cli")
    monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
    client = KeelClient(config=KeelConfig(api_key="k", api_url="https://api.test"))
    try:
        assert client._client.headers["x-keel-surface"] == "cli"
    finally:
        client.close()


def test_server_instructions_state_the_model():
    """Spec 08 R1: MCP server instructions state the contract on both
    profiles (full: write-through + conflict envelope; listed: policy-
    safe server-authoritative phrasing only)."""
    from keel.mcp.server import LISTED_INSTRUCTIONS, _full_instructions

    full = _full_instructions(live_write_loaded=True)
    assert "single source of truth" in full
    assert "WRITE THROUGH" in full or "write through" in full.lower()
    assert "sync_conflict" in full
    assert "pull_force" in full
    assert "never auto-merged" in full

    assert "STATE MODEL" in LISTED_INSTRUCTIONS
    assert "canonical version" in LISTED_INSTRUCTIONS
    # Policy boundary: the listed copy must stay free of deploy/fund verbs.
    for banned in ("deploy", "fund", "trade verb", "wallet", "leverage"):
        assert banned not in LISTED_INSTRUCTIONS.split("STATE MODEL")[1].lower()
