"""Tests for remote tool implementations using respx mocks."""

from __future__ import annotations

import pytest

try:
    import respx
    from httpx import Response

    HAS_RESPX = True
except ImportError:
    HAS_RESPX = False

pytestmark = pytest.mark.skipif(not HAS_RESPX, reason="respx not installed")


@pytest.fixture
def mock_api():
    """Set up respx mock for the Keel API."""
    import os

    os.environ["KEEL_API_KEY"] = "test-key"
    os.environ["KEEL_API_URL"] = "https://api.test.usekeel.io"

    with respx.mock(base_url="https://api.test.usekeel.io") as mock:
        yield mock

    os.environ.pop("KEEL_API_KEY", None)
    os.environ.pop("KEEL_API_URL", None)


class TestRemoteCompile:
    def test_strategy_compile(self, mock_api):
        mock_api.post("/v1/strategies/compile").mock(
            return_value=Response(200, json={"compiled": True, "fingerprint": "abc123"})
        )

        from keel.tools.remote import strategy_compile

        result = strategy_compile(source="Pipeline([])")
        assert result["compiled"] is True


class TestRemoteLock:
    def test_lock_generate(self, mock_api):
        mock_api.post("/v1/strategies/lock/generate").mock(
            return_value=Response(200, json={"component_lock": {"Foo": 1}})
        )

        from keel.tools.remote import strategy_lock_generate_remote

        result = strategy_lock_generate_remote(source="Pipeline([])")
        assert "component_lock" in result

    def test_lock_check(self, mock_api):
        mock_api.post("/v1/strategies/lock/check").mock(
            return_value=Response(200, json={"status": "current", "drift": []})
        )

        from keel.tools.remote import strategy_lock_status_remote

        result = strategy_lock_status_remote(source="Pipeline([])")
        assert result["status"] == "current"

    def test_lock_upgrade(self, mock_api):
        mock_api.post("/v1/strategies/lock/upgrade").mock(
            return_value=Response(200, json={"component_lock": {"Foo": 2}, "upgraded": ["Foo"]})
        )

        from keel.tools.remote import strategy_lock_upgrade_remote

        result = strategy_lock_upgrade_remote(source="Pipeline([])", component_lock={"Foo": 1})
        assert "Foo" in result["upgraded"]
