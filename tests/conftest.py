"""Test-suite-wide fixtures.

Why this file exists: the SDK's config + token-store layer writes to
``~/.keel/config.yaml`` (real user state). Without isolation, ANY test
that constructs a `KeelClient` with a near-expiry refresh token will
trigger the proactive-refresh path → `store_oauth_tokens()` → blow
away the user's real credentials. That actually happened on 2026-05-22
during the v0.4.x staging smoke. The autouse fixture below redirects
config writes to tmp paths for every test, no opt-in needed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """Redirect `~/.keel/config.yaml` to a per-test temp file.

    Test-suite-wide guard: any test that exercises auth, token refresh,
    or the CLI/MCP adapters might end up calling `save_config()`. We
    NEVER want that to land in the real user file.
    """
    fake_config = tmp_path / "config.yaml"
    fake_dir = tmp_path
    monkeypatch.delenv("KEEL_API_KEY", raising=False)
    monkeypatch.delenv("KEEL_API_URL", raising=False)
    with (
        patch("keel.config.CONFIG_FILE", fake_config),
        patch("keel.config.CONFIG_DIR", fake_dir),
    ):
        yield fake_config
