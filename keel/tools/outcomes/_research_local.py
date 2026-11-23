"""Whether a LOCAL surface (the CLI, a full-profile ``keel mcp serve``) serves Research (FINAL-LIST §D; 07 §5.3).

The hosted server binds ``research_served`` per request from the caller's ``/v1/me`` (``services/mcp-server``
``auth.research_served_from_me``). A local process has one caller, the credential it acts as, so it reads that
caller's ``/v1/me`` the same way: Research is served exactly when the body carries the ``research`` block, which
keel-api serves only while ``research.enabled`` is on. While it is off the CLI and the local MCP server show no
research verb, tool, topic or card, so a prod user never meets a Research that is not launched.

The read is cached twice, so the CLI's start pays for it at most once per :data:`TTL_S`:

- in the process (the first caller pays; every later one reads the answer);
- on disk at ``<KEEL_CONFIG_DIR>/research-served.json``, keyed by the API URL and a hash of the credential (never
  the credential itself), so a different login or API never reads another's answer.

"Could not tell" is never on: no credential (the read would mint an anonymous org, so it is never made), a
non-2xx, a network failure or a body that is not an object reads the last good answer on disk when there is one,
and false when there is none. One request, :data:`TIMEOUT_S`, no retries.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any


#: How long a read answer stays fresh, seconds (keel-api's own settings cache is 60 s).
TTL_S = 300
#: The one request's budget, seconds: the CLI's start waits at most this long.
TIMEOUT_S = 3.0
CACHE_NAME = "research-served.json"

_PROCESS: dict[str, bool] = {}


def served_from_me(me: object) -> bool:
    """Whether a ``/v1/me`` body serves Research: its ``research`` block is present (an object). The hosted
    server's rule (``mcp-server/src/auth.py`` ``research_served_from_me``), so both surfaces decide alike."""
    return isinstance(me, dict) and isinstance(me.get("research"), dict)


def _cache_path():
    import keel.config as config  # read at call time: the suite patches CONFIG_DIR

    return config.CONFIG_DIR / CACHE_NAME


def _key(api_url: str, api_key: str) -> str:
    return f"{api_url.rstrip('/')}#{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"


def _read_disk(key: str) -> tuple[bool, float] | None:
    try:
        row = json.loads(_cache_path().read_text()).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(row, dict) or not isinstance(row.get("served"), bool):
        return None
    at = row.get("at")
    return row["served"], float(at) if isinstance(at, (int, float)) else 0.0


def _write_disk(key: str, served: bool) -> None:
    path = _cache_path()
    try:
        try:
            rows: dict[str, Any] = json.loads(path.read_text())
            if not isinstance(rows, dict):
                rows = {}
        except (OSError, ValueError):
            rows = {}
        rows[key] = {"served": served, "at": time.time()}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows))
        tmp.replace(path)
    except OSError:
        pass  # a read-only home only costs the next start one read


def _read_me(config: Any) -> bool:
    """One ``GET /v1/me`` as ``config``'s credential; raises when it cannot tell."""
    import httpx

    from keel.client import KeelClient

    client = KeelClient(config)
    try:
        response = client._client.get("/v1/me", timeout=TIMEOUT_S)
    finally:
        client._client.close()
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise httpx.DecodingError("Keel's account check answered in an unexpected form; try again.")
    return served_from_me(body)


def local_research_served() -> bool:
    """Whether this local process serves Research now (see the module doc)."""
    from keel.config import load_config

    try:
        config = load_config()
    except Exception:  # noqa: BLE001 - an unreadable config cannot tell
        return False
    if not config.api_key:
        return False
    key = _key(config.api_url, config.api_key)
    if key in _PROCESS:
        return _PROCESS[key]
    cached = _read_disk(key)
    if cached is not None and time.time() - cached[1] < TTL_S:
        _PROCESS[key] = cached[0]
        return cached[0]
    try:
        served = _read_me(config)
    except Exception:  # noqa: BLE001 - could not tell: the last good answer, else hidden
        served = cached[0] if cached is not None else False
    else:
        _write_disk(key, served)
    _PROCESS[key] = served
    return served


def forget() -> None:
    """Test seam: drop the process cache (a new process)."""
    _PROCESS.clear()
