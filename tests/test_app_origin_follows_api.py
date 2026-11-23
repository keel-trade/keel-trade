"""Every app and share link the SDK prints follows the API it is pointed at (DATA S3, STG-SURFACES #18).

A CLI pointed at staging (``KEEL_API_URL=https://staging-api…``) with no ``KEEL_APP_URL`` printed prod links: a 5xx
sent the person to ``status.usekeel.io``, an auth error to prod's API-keys page, and ``keel app open <share>`` to
``usekeel.io/share``. The origin is now one helper (``keel.config.configured_app_origin``) and the share root follows
the app (``open_in_app.share_root``). Control arm: the production API keeps every production link.
"""

from __future__ import annotations

import pytest

from keel.errors import translate_http_error
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._research_commands import research_url, usage_refusal
from keel.tools.outcomes.open_in_app import app_url_for

STAGING_API = "https://staging-api.tailf4d598.ts.net"
STAGING_APP = "https://staging-app.tailf4d598.ts.net"
SHARE_ID = "BI06bbDsXR3EkkWOcBcQrXc"


@pytest.fixture
def pointed_at(monkeypatch):
    def point(api_url: str) -> None:
        monkeypatch.delenv("KEEL_APP_URL", raising=False)
        monkeypatch.delenv("KEEL_SHARE_URL_ROOT", raising=False)
        monkeypatch.delenv("KEEL_EXECUTION_MODE", raising=False)
        # a key in the env makes the env the whole credential: no stored login is read (Q-2579)
        monkeypatch.setenv("KEEL_API_KEY", "keel_test_key")
        monkeypatch.setenv("KEEL_API_URL", api_url)

    return point


def _links() -> dict[str, str]:
    from keel.config import api_keys_url
    from keel.errors import _app_url

    ctx = ToolContext()
    refusal = usage_refusal({"code": "usage_error", "message": "x"}, "keel_research_get", {})
    return {
        "app": _app_url(),
        "api_keys": api_keys_url(),
        "server_error": translate_http_error(502, "").suggestion or "",
        "share": app_url_for("share", SHARE_ID, ctx),
        "study": app_url_for("study", "stu_01abc", ctx),
        "research": research_url(ctx),
        "refusal": refusal["detail"]["links"]["app"],
    }


def test_a_cli_pointed_at_staging_links_staging(pointed_at):
    pointed_at(STAGING_API)
    links = _links()
    assert links["app"] == STAGING_APP
    assert links["api_keys"] == f"{STAGING_APP}/settings?tab=api-keys"
    assert "status.usekeel.io" not in links["server_error"]
    assert links["share"] == f"{STAGING_APP}/share/{SHARE_ID}"
    for key in ("study", "research", "refusal"):
        assert links[key].startswith(STAGING_APP + "/"), (key, links[key])
    # non-vacuous: the seven links were all built, and none of them names prod's hosts
    assert len(links) == 7 and all(links.values())
    assert not any("app.usekeel.io" in v or "usekeel.io/share" in v for v in links.values())


def test_control_the_production_api_keeps_production_links(pointed_at):
    pointed_at("https://api.usekeel.io")
    links = _links()
    assert links["app"] == "https://app.usekeel.io"
    assert "https://status.usekeel.io" in links["server_error"]
    assert links["share"] == f"https://usekeel.io/share/{SHARE_ID}"
    assert links["research"].startswith("https://app.usekeel.io/")


def test_an_explicit_app_url_wins(pointed_at, monkeypatch):
    pointed_at(STAGING_API)
    monkeypatch.setenv("KEEL_APP_URL", "https://example-app.test/")
    links = _links()
    assert links["app"] == "https://example-app.test"
    assert links["share"] == f"https://example-app.test/share/{SHARE_ID}"
    assert links["refusal"].startswith("https://example-app.test/")
