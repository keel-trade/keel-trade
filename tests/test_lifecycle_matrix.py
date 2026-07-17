"""SDK-side lifecycle-matrix cells (spec 08 R8 / M1b.4).

Companion to services/keel-api/tests/test_lifecycle_matrix.py and
projects/fable/agent-first-build/orchestration/lifecycle-matrix.md.
Covers the local-working-copy cells the server can't see: deleted
strategy touched locally, and the checked-out → ahead → stale →
conflicted state walk driven purely by hashes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from keel.errors import KeelError, NotFoundError
from keel.tools.outcomes import OUTCOMES, ToolContext, _bootstrap
from keel.workspace import (
    STRATEGY_FILE,
    WorkspaceMeta,
    _compute_hash,
)
from keel.workspace import (
    status as ws_status,
)


@pytest.fixture(autouse=True)
def _bootstrap_outcomes():
    _bootstrap()
    from keel.tools.outcomes import (  # noqa: F401
        strategy_pull,
        strategy_push,
        strategy_status,
    )


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("keel.workspace.WORKSPACE_ROOT", tmp_path / "ws"):
        yield tmp_path / "ws"


def _checkout(root, strategy_id: str, source: str) -> None:
    ws_dir = root / strategy_id
    ws_dir.mkdir(parents=True)
    (ws_dir / STRATEGY_FILE).write_text(source)
    WorkspaceMeta(
        strategy_id=strategy_id,
        name="L",
        source_hash=_compute_hash(source),
        checked_out_at="2026-07-16T00:00:00Z",
        current_sequence=1,
    ).save(ws_dir)


_CTX = ToolContext(is_tty=False, app_url="https://app.usekeel.io")


class TestDeletedStrategyLocalTouch:
    """Transition: delete (server-side) × surviving local checkout.

    Invariant: the next local write touch fails INSTRUCTIVELY (structured
    not_found with a suggestion) — no silent success, no orphan write."""

    def test_push_after_delete_is_instructive_404(self, workspace_root):
        _checkout(workspace_root, "str_gone", "BASE")
        (workspace_root / "str_gone" / STRATEGY_FILE).write_text("EDITED")
        inst = MagicMock()
        inst.patch.side_effect = NotFoundError(
            "Strategy not found",
            suggestion="Verify the id is correct; list yours via `keel_strategy_search`.",
        )

        with patch("keel.client.KeelClient", return_value=inst):
            with pytest.raises(KeelError) as exc:
                OUTCOMES["keel_strategy_push"].handler({"strategy_id": "str_gone"}, _CTX)

        assert exc.value.error_code == "not_found"
        assert exc.value.suggestion  # instructive, never bare

    def test_pull_after_delete_is_instructive_404(self, workspace_root):
        _checkout(workspace_root, "str_gone", "BASE")
        inst = MagicMock()
        inst.get.side_effect = NotFoundError(
            "Strategy not found",
            suggestion="Verify the id is correct; list yours via `keel_strategy_search`.",
        )

        with patch("keel.client.KeelClient", return_value=inst):
            with pytest.raises(KeelError) as exc:
                OUTCOMES["keel_strategy_pull"].handler({"strategy_id": "str_gone"}, _CTX)

        assert exc.value.error_code == "not_found"
        assert exc.value.suggestion


class TestLocalStateWalk:
    """States checked-out → ahead → stale → conflicted derive purely from
    (local_hash, base_hash, server_hash) — the hash contract every sync
    decision rests on."""

    @pytest.mark.parametrize(
        "local_src,server_src,expected",
        [
            ("BASE", "BASE", "current"),  # checked-out, clean
            ("EDITED", "BASE", "ahead"),  # file edit only
            ("BASE", "MOVED", "behind"),  # server moved (stale)
            ("EDITED", "MOVED", "conflict"),  # both — true conflict
        ],
        ids=["clean", "ahead", "stale", "conflicted"],
    )
    def test_state_from_hashes(self, workspace_root, local_src, server_src, expected):
        _checkout(workspace_root, "str_walk", "BASE")
        (workspace_root / "str_walk" / STRATEGY_FILE).write_text(local_src)
        inst = MagicMock()
        inst.get.return_value = {
            "strategy_id": "str_walk",
            "name": "L",
            "source": server_src,
            "source_hash": _compute_hash(server_src),
            "current_sequence": 2,
        }

        with patch("keel.client.KeelClient", return_value=inst):
            result = ws_status("str_walk")

        assert result["state"] == expected
