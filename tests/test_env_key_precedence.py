"""Q-2579: ``KEEL_API_KEY`` is the whole credential; the stored login is its own.

The CLI and the local MCP server share ``~/.keel/config.yaml``. Before the
fix, ``KEEL_API_KEY`` replaced only the bearer: the file's refresh lineage
still refreshed (and the first request went out as the FILE identity), a 401
on the key fell back to the file's lineage, and the file's ``active_org_id``
rode along as ``X-Org-Id``. Each arm below drives a real ``KeelClient``
against a real local HTTP server (the network is faked, the resolution is
not), and the server records the bearer and org header of every request.

Arms:
- three seeded arms (stale lineage, key 401, stored org context) that are red
  on the pre-fix ``load_config`` merge;
- a persistence arm: a write made while a key is set never copies the key or
  ``KEEL_API_URL`` into the stored login;
- no-key controls: the stored login refreshes and sends its org as before;
- both configured: two real processes over ONE config file, one with a key,
  one without, each using its own source;
- the source is named by ``keel auth status`` / ``keel_account_status`` /
  ``keel doctor``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


ENV_KEY = "ENV_ORG_KEY"
FILE_STALE = "FILE_ORG_STALE_ACCESS"
FILE_FRESH = "FILE_ORG_FRESH_ACCESS"
RUN_PATH = "/v1/backtests/btr_run"


class FakeKeelApi:
    """A local keel-api that records `(method, path, bearer, x-org-id)`.

    `/v1/auth/oauth/refresh` mints FILE_FRESH; `ENV_KEY` and either file
    token read 200, unless `reject_env_key` is set (then ENV_KEY is 401)."""

    def __init__(self) -> None:
        self.seen: list[tuple] = []
        self.reject_env_key = False
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401 — silence the server log
                pass

            def _send(self, code: int, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                api.seen.append(("POST", self.path, body.get("refresh_token"), None))
                if self.path == "/v1/auth/oauth/refresh":
                    return self._send(
                        200,
                        {
                            "access_token": FILE_FRESH,
                            "refresh_token": "FILE_REFRESH_2",
                            "expires_in": 3600,
                        },
                    )
                return self._send(404, {})

            def do_GET(self):  # noqa: N802
                auth = self.headers.get("Authorization")
                api.seen.append(("GET", self.path, auth, self.headers.get("X-Org-Id")))
                if auth == f"Bearer {ENV_KEY}" and api.reject_env_key:
                    return self._send(401, {"code": "unauthorized"})
                org = {
                    f"Bearer {ENV_KEY}": "org_ENV",
                    f"Bearer {FILE_STALE}": "org_FILE",
                    f"Bearer {FILE_FRESH}": "org_FILE",
                }.get(auth)
                if org is None:
                    return self._send(401, {"code": "unauthorized"})
                if self.path == "/v1/me":
                    return self._send(
                        200, {"principal": {"id": "prn_x"}, "org": {"id": org, "name": org}}
                    )
                if self.path == "/v1/entitlements":
                    return self._send(200, {"plan": "free"})
                return self._send(200, {"id": "btr_run", "org": org})

        self._srv = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def refreshes(self) -> list[tuple]:
        return [s for s in self.seen if s[1] == "/v1/auth/oauth/refresh"]

    def gets(self) -> list[tuple]:
        return [s for s in self.seen if s[0] == "GET"]

    def close(self) -> None:
        self._srv.shutdown()


@pytest.fixture
def api():
    server = FakeKeelApi()
    yield server
    server.close()


def _stored_login(url: str, *, stale: bool, org_context: str | None = None) -> str:
    expires = "2026-01-01T00:00:00+00:00" if stale else "2099-01-01T00:00:00+00:00"
    text = (
        f"api_key: {FILE_STALE}\napi_url: {url}\nrefresh_token: FILE_REFRESH_1\n"
        f"token_expires_at: '{expires}'\nclient_name: Keel CLI/0.6.1\nanon_org_id: org_FILE\n"
    )
    if org_context:
        text += f"active_org_id: {org_context}\n"
    return text


def _two_reads():
    """Two fresh clients x one read each — the repro's shape."""
    from keel.client import KeelClient

    results = []
    for _ in range(2):
        client = KeelClient()
        try:
            results.append(client.get(RUN_PATH))
        finally:
            client.close()
    return results


# ── seeded arms: red on the pre-fix merge ─────────────────────────────


def test_stale_stored_lineage_never_refreshes_under_env_key(api, monkeypatch, _isolate_user_config):
    cfg = _isolate_user_config
    cfg.write_text(_stored_login(api.url, stale=True))
    before = cfg.read_bytes()
    monkeypatch.setenv("KEEL_API_KEY", ENV_KEY)
    monkeypatch.setenv("KEEL_API_URL", api.url)

    results = _two_reads()

    assert len(api.gets()) == 2  # non-vacuity: both reads reached the server
    assert api.refreshes() == [], f"stored lineage refreshed under KEEL_API_KEY: {api.seen}"
    assert [g[2] for g in api.gets()] == [f"Bearer {ENV_KEY}"] * 2
    assert [r["org"] for r in results] == ["org_ENV", "org_ENV"]
    assert cfg.read_bytes() == before, "a key-authenticated run rewrote the stored login"


def test_env_key_401_fails_as_a_rejected_key_never_as_the_stored_login(
    api, monkeypatch, _isolate_user_config
):
    from keel.client import KeelClient
    from keel.errors import AuthError

    cfg = _isolate_user_config
    cfg.write_text(_stored_login(api.url, stale=False))
    before = cfg.read_bytes()
    api.reject_env_key = True
    monkeypatch.setenv("KEEL_API_KEY", ENV_KEY)
    monkeypatch.setenv("KEEL_API_URL", api.url)

    client = KeelClient()
    with pytest.raises(AuthError) as exc:
        client.get(RUN_PATH)
    client.close()

    assert api.gets(), "non-vacuity: the read reached the server"
    assert api.refreshes() == [], f"a 401 on the key fell back to the stored lineage: {api.seen}"
    assert {g[2] for g in api.gets()} == {f"Bearer {ENV_KEY}"}
    message = str(exc.value)
    assert "API key was rejected" in message and "KEEL_API_KEY" in message
    assert exc.value.recovery_tool is None  # keel_auth_login would not fix it
    assert cfg.read_bytes() == before


def test_stored_org_context_is_not_sent_with_env_key(api, monkeypatch, _isolate_user_config):
    _isolate_user_config.write_text(
        _stored_login(api.url, stale=False, org_context="org_FILE_CLAIMED")
    )
    monkeypatch.setenv("KEEL_API_KEY", ENV_KEY)
    monkeypatch.setenv("KEEL_API_URL", api.url)

    _two_reads()

    assert len(api.gets()) == 2
    assert [g[3] for g in api.gets()] == [None, None], (
        f"stored X-Org-Id rode with the key: {api.gets()}"
    )


def test_a_write_under_env_key_never_copies_the_environment_into_the_stored_login(
    api, monkeypatch, _isolate_user_config
):
    """`_set_org_context` stands for every read-modify-write of the file
    (claim, pending claim, expiry stamp, token store)."""
    import yaml
    from keel.anon import _set_org_context

    cfg = _isolate_user_config
    cfg.write_text(_stored_login(api.url, stale=False))
    monkeypatch.setenv("KEEL_API_KEY", ENV_KEY)
    monkeypatch.setenv("KEEL_API_URL", "http://env-only.invalid")

    _set_org_context("org_CLAIMED")

    data = yaml.safe_load(cfg.read_text())
    assert data["active_org_id"] == "org_CLAIMED"  # non-vacuity: the write happened
    assert data["api_key"] == FILE_STALE
    assert data["api_url"] == api.url
    assert data["refresh_token"] == "FILE_REFRESH_1"


# ── controls: no key → the stored login behaves exactly as before ─────


def test_control_no_key_stored_login_refreshes_and_persists(api, monkeypatch, _isolate_user_config):
    import yaml

    cfg = _isolate_user_config
    cfg.write_text(_stored_login(api.url, stale=True))

    results = _two_reads()

    assert len(api.refreshes()) == 1
    assert [g[2] for g in api.gets()] == [f"Bearer {FILE_FRESH}"] * 2
    assert [r["org"] for r in results] == ["org_FILE", "org_FILE"]
    data = yaml.safe_load(cfg.read_text())
    assert data["api_key"] == FILE_FRESH and data["refresh_token"] == "FILE_REFRESH_2"
    assert data["anon_org_id"] == "org_FILE"  # the rest of the login survives


def test_control_no_key_stored_org_context_is_sent(api, _isolate_user_config):
    _isolate_user_config.write_text(
        _stored_login(api.url, stale=False, org_context="org_FILE_CLAIMED")
    )

    _two_reads()

    assert [g[3] for g in api.gets()] == ["org_FILE_CLAIMED"] * 2


# ── both configured on one machine: two real processes, one file ──────

_CHILD = textwrap.dedent(
    """
    import json, sys
    from keel.client import KeelClient
    from keel.tools.outcomes._base import ToolContext
    out = [KeelClient().get("/v1/backtests/btr_run")["org"]]
    # The local MCP server's path: a ToolContext per tool call, client built lazily.
    out.append(ToolContext(is_tty=False).get_client().get("/v1/backtests/btr_run")["org"])
    print(json.dumps(out))
    """
)


def _run_child(home, url: str, *, key: str | None) -> list[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KEEL_")}
    env.pop("PYTHONPATH", None)
    env.update({"HOME": str(home), "KEEL_ANON_AUTO": "0", "KEEL_API_URL": url})
    if key:
        env["KEEL_API_KEY"] = key
    done = subprocess.run(
        [sys.executable, "-c", _CHILD], env=env, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_both_configured_each_process_uses_its_own_source(api, tmp_path):
    import yaml

    home = tmp_path / "home"
    (home / ".keel").mkdir(parents=True)
    cfg = home / ".keel" / "config.yaml"
    cfg.write_text(_stored_login(api.url, stale=True, org_context=None))
    before = cfg.read_bytes()

    # 1. The keyed process (e.g. an MCP server configured with a key) first:
    #    the stored login is due for refresh and must not be touched.
    assert _run_child(home, api.url, key=ENV_KEY) == ["org_ENV", "org_ENV"]
    assert api.refreshes() == []
    assert cfg.read_bytes() == before

    # 2. The unkeyed process (e.g. the logged-in CLI) refreshes ITS login.
    assert _run_child(home, api.url, key=None) == ["org_FILE", "org_FILE"]
    assert len(api.refreshes()) == 1
    after_cli = cfg.read_bytes()
    assert yaml.safe_load(after_cli)["api_key"] == FILE_FRESH

    # 3. The keyed process again: still the key, and the CLI's refreshed
    #    login is left exactly as the CLI wrote it.
    assert _run_child(home, api.url, key=ENV_KEY) == ["org_ENV", "org_ENV"]
    assert cfg.read_bytes() == after_cli

    bearers = [g[2] for g in api.gets()]
    assert len(bearers) == 6  # non-vacuity: 3 processes x (CLI + MCP client)
    assert (
        bearers
        == [f"Bearer {ENV_KEY}"] * 2 + [f"Bearer {FILE_FRESH}"] * 2 + [f"Bearer {ENV_KEY}"] * 2
    )


# ── the active source is named ────────────────────────────────────────


def test_auth_status_names_the_env_key_and_its_org(api, monkeypatch, _isolate_user_config):
    from keel.auth import get_status

    _isolate_user_config.write_text(_stored_login(api.url, stale=False))
    monkeypatch.setenv("KEEL_API_KEY", ENV_KEY)
    monkeypatch.setenv("KEEL_API_URL", api.url)

    status = get_status()

    assert status["credential_source"] == "env KEEL_API_KEY"
    assert status["org"]["id"] == "org_ENV"


def test_auth_status_names_the_stored_login(api, _isolate_user_config):
    from keel.auth import get_status

    _isolate_user_config.write_text(_stored_login(api.url, stale=False))

    status = get_status()

    assert status["credential_source"] == "stored login"
    assert status["org"]["id"] == "org_FILE"


@pytest.mark.parametrize(
    ("key", "label", "org"),
    [(ENV_KEY, "env KEEL_API_KEY", "org_ENV"), (None, "stored login", "org_FILE")],
)
def test_account_status_and_doctor_name_the_source(
    api, monkeypatch, _isolate_user_config, key, label, org
):
    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes.doctor import _handler as doctor
    from keel.tools.outcomes.status import _handler as account_status

    _isolate_user_config.write_text(_stored_login(api.url, stale=False))
    monkeypatch.setenv("KEEL_API_URL", api.url)
    if key:
        monkeypatch.setenv("KEEL_API_KEY", key)

    body = account_status({}, ToolContext(is_tty=False)).extra
    assert body["credential_source"] == label
    assert body["identity"]["org_id"] == org

    checks = doctor({}, ToolContext(is_tty=False)).extra["checks"]
    auth = next(c for c in checks if c["name"] == "auth")
    assert auth["detail"]["credential_source"] == label
    assert auth["detail"]["org_id"] == org


@pytest.mark.parametrize(
    ("key", "expected"),
    [(ENV_KEY, "credential = env KEEL_API_KEY"), (None, "credential = stored login")],
)
def test_mcp_serve_logs_the_source_on_stderr_only(
    monkeypatch, capsys, _isolate_user_config, key, expected
):
    from keel.cli.commands.mcp_cmd import _log_credential_source

    _isolate_user_config.write_text(_stored_login("http://127.0.0.1:9", stale=False))
    if key:
        monkeypatch.setenv("KEEL_API_KEY", key)

    _log_credential_source()

    out = capsys.readouterr()
    assert out.out == ""  # stdout is the MCP stream
    assert expected in out.err
    assert ("stored login is not used" in out.err) == bool(key)
