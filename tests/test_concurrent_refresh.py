"""Q-2602: clients sharing one stored login refresh it once, and a refused
refresh never deletes a newer login.

A key-less CLI and a key-less local MCP server both act as the login in
``~/.keel/config.yaml``, and each ``KeelClient`` decides from the snapshot it
loaded. keel-api rotates a refresh token once; a second presentation is
refused with 401 (within 60 s the lineage is kept, Q-2532/Q-2548). Before the
fix both processes sent the same token, and the loser's 401 ran
``clear_oauth_tokens()`` over the login the winner had just saved.

The fake keel-api below enforces that reuse detection: the first
presentation of the current refresh token rotates it, any other is refused
and its 401 is held back until after the winner's 200, which is how the
loser of a real race (blocked on the winner's row lock) is answered.

Arms:
- two real processes, one stored login due for refresh (the fix);
- control: one process refreshes exactly as before;
- in one process: a client built before another client's refresh adopts it;
- a refused refresh adopts a newer stored login written meanwhile, and still
  clears the stored login when it is the very one refused (control).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml


WINNER_DELAY_S = 0.3
LOSER_DELAY_S = 1.0


class ReuseDetectingApi:
    """A local keel-api: ``/v1/auth/oauth/refresh`` rotates ``R<n>`` →
    ``R<n+1>`` / ``A<n+1>`` exactly once per token; ``/v1/ping`` answers 200
    only to the current access token."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.refresh = "R1"
        self.access = "A1"
        self.generation = 1
        self.refresh_posts: list[tuple[str, int]] = []  # (token sent, status)
        self.pings: list[tuple[str, int]] = []
        self.on_refresh = None  # optional hook(api) run before answering
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401
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
                if self.path != "/v1/auth/oauth/refresh":
                    return self._send(404, {})
                sent = body.get("refresh_token")
                with api.lock:
                    won = sent == api.refresh
                    if won:
                        api.generation += 1
                        api.refresh = f"R{api.generation}"
                        api.access = f"A{api.generation}"
                        minted = (api.access, api.refresh)
                    api.refresh_posts.append((sent, 200 if won else 401))
                if api.on_refresh is not None:
                    api.on_refresh(api)
                if won:
                    time.sleep(WINNER_DELAY_S)
                    return self._send(
                        200,
                        {"access_token": minted[0], "refresh_token": minted[1], "expires_in": 3600},
                    )
                time.sleep(LOSER_DELAY_S)
                return self._send(401, {"detail": "Invalid refresh token"})

            def do_GET(self):  # noqa: N802
                bearer = (self.headers.get("authorization") or "").removeprefix("Bearer ")
                with api.lock:
                    ok = bearer == api.access
                api.pings.append((bearer, 200 if ok else 401))
                return self._send(200 if ok else 401, {"ok": ok})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def api():
    server = ReuseDetectingApi()
    yield server
    server.close()


def _due_login(url: str) -> str:
    """A stored login whose access token expired a minute ago."""
    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    return yaml.safe_dump(
        {
            "api_key": "A1",
            "refresh_token": "R1",
            "token_expires_at": expired.isoformat(),
            "api_url": url,
            "client_name": "keel-cli",
        }
    )


# ── two real processes, one stored login ──────────────────────────────

_CHILD = textwrap.dedent(
    """
    import json, pathlib, sys, time
    from keel.client import KeelClient
    from keel.token_store import needs_refresh
    tag, rendezvous = sys.argv[1], pathlib.Path(sys.argv[2])
    client = KeelClient()  # the snapshot: both children load it before either refreshes
    start = {"refresh": client._config.refresh_token, "due": needs_refresh(client._config)}
    (rendezvous / f"ready-{tag}").touch()
    while not (rendezvous / "go").exists():
        time.sleep(0.005)
    try:
        client.get("/v1/ping")
        out = {"start": start, "ok": True, "err": None}
    except Exception as e:
        out = {"start": start, "ok": False, "err": f"{type(e).__name__}: {e}"}
    print(json.dumps(out))
    """
)


def _spawn(home, rendezvous, tag: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KEEL_")}
    env.pop("PYTHONPATH", None)
    env.update({"HOME": str(home), "KEEL_ANON_AUTO": "0"})
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD, tag, str(rendezvous)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_children(tmp_path, url: str, tags: list[str]) -> tuple[list[dict], object]:
    home = tmp_path / "home"
    (home / ".keel").mkdir(parents=True)
    cfg = home / ".keel" / "config.yaml"
    cfg.write_text(_due_login(url))
    rendezvous = tmp_path / "rendezvous"
    rendezvous.mkdir()
    procs = [_spawn(home, rendezvous, t) for t in tags]
    deadline = time.monotonic() + 60
    while not all((rendezvous / f"ready-{t}").exists() for t in tags):
        assert time.monotonic() < deadline, "children never loaded their snapshots"
        assert all(p.poll() is None for p in procs), [p.communicate() for p in procs]
        time.sleep(0.01)
    (rendezvous / "go").touch()
    results = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        results.append(json.loads(out.strip().splitlines()[-1]))
    return results, cfg


def test_two_processes_refresh_one_stored_login_once(api, tmp_path):
    results, cfg = _run_children(tmp_path, api.url, ["cli", "mcp"])

    # Non-vacuity: both processes started from the same due token, so both
    # would have refreshed it on their own.
    assert [r["start"] for r in results] == [{"refresh": "R1", "due": True}] * 2

    stored = yaml.safe_load(cfg.read_text()) or {}
    # The stored login survives as the one rotation minted (pre-fix: the
    # loser's 401 cleared it and this read `{}`).
    assert (stored.get("api_key"), stored.get("refresh_token")) == ("A2", "R2"), stored
    # The fake server saw exactly one refresh: the second process adopted it.
    assert api.refresh_posts == [("R1", 200)]
    assert [r["err"] for r in results] == [None, None]
    assert api.pings == [("A2", 200), ("A2", 200)]


def test_control_one_process_refreshes_as_before(api, tmp_path):
    results, cfg = _run_children(tmp_path, api.url, ["cli"])

    assert results == [{"start": {"refresh": "R1", "due": True}, "ok": True, "err": None}]
    assert api.refresh_posts == [("R1", 200)]
    stored = yaml.safe_load(cfg.read_text())
    assert (stored["api_key"], stored["refresh_token"]) == ("A2", "R2")
    assert api.pings == [("A2", 200)]


# ── in one process: two clients over one snapshot ─────────────────────


def test_client_built_before_anothers_refresh_adopts_it(api, _isolate_user_config):
    from keel.client import KeelClient

    _isolate_user_config.write_text(_due_login(api.url))
    first, second = KeelClient(), KeelClient()  # both snapshot A1/R1
    assert first._config.refresh_token == second._config.refresh_token == "R1"

    first.get("/v1/ping")
    second.get("/v1/ping")

    assert api.refresh_posts == [("R1", 200)]
    assert api.pings == [("A2", 200), ("A2", 200)]
    assert yaml.safe_load(_isolate_user_config.read_text())["refresh_token"] == "R2"


# ── a refused refresh and the login on disk ───────────────────────────


def _login_elsewhere(api) -> None:
    """Another process logs in while this refresh is in flight (an unlocked
    writer): the file now holds a different, valid login."""
    from keel.token_store import store_oauth_tokens

    with api.lock:
        api.access, api.refresh = "A_NEW_LOGIN", "R_NEW_LOGIN"
    store_oauth_tokens(
        access_token="A_NEW_LOGIN", refresh_token="R_NEW_LOGIN", expires_in=3600, api_url=api.url
    )


def test_refused_refresh_adopts_a_newer_stored_login(api, _isolate_user_config):
    from keel.config import load_config
    from keel.token_store import attempt_refresh

    _isolate_user_config.write_text(_due_login(api.url))
    config = load_config()
    with api.lock:
        api.refresh = "R_ALREADY_ROTATED"  # R1 will be refused
    api.on_refresh = _login_elsewhere

    updated = attempt_refresh(config)

    assert api.refresh_posts == [("R1", 401)]  # non-vacuity: the refusal happened
    assert (updated.api_key, updated.refresh_token) == ("A_NEW_LOGIN", "R_NEW_LOGIN")
    stored = yaml.safe_load(_isolate_user_config.read_text())
    assert (stored.get("api_key"), stored.get("refresh_token")) == ("A_NEW_LOGIN", "R_NEW_LOGIN")


def test_control_refused_refresh_of_the_stored_token_still_clears(api, _isolate_user_config):
    from keel.config import load_config
    from keel.errors import AuthError
    from keel.token_store import attempt_refresh

    _isolate_user_config.write_text(_due_login(api.url))
    config = load_config()
    with api.lock:
        api.refresh = "R_ALREADY_ROTATED"

    with pytest.raises(AuthError, match="session has expired"):
        attempt_refresh(config)

    assert api.refresh_posts == [("R1", 401)]
    stored = yaml.safe_load(_isolate_user_config.read_text()) or {}
    assert "api_key" not in stored and "refresh_token" not in stored
    assert stored.get("api_url") == api.url
