"""The portfolio view's HEAD outputs on the full profile and the CLI (S06 §3.5.5).

``M2-sdk-portfolio-real`` makes ``keel_live_monitor``'s portfolio view name its
class (``capital_class=real``) on every surface, so every client Keel ships
asks for what it means before keel-api reads an absent class as ``real``
(R-71). The read changes; the rendered output must not. The listed surface's
HEAD outputs are ``fixtures/listed_outputs_head.json``; this module adds the
other two surfaces, captured from the base commit's SDK (``028a89104``) into
``fixtures/portfolio_outputs_head.json`` against the same faked keel-api (the
world stored in ``listed_outputs_head.json``):

* **full** — a local ``keel mcp serve`` (full profile, local execution,
  ``KEEL_API_KEY``), through the real MCP adapter: structured content, text
  and ``_meta``;
* **cli** — ``keel live monitor`` through the real Click tree: the output and
  the exit code.

Both with no arguments and with ``view=portfolio``. Re-capture (a non-paper
change only): ``python -m tests._portfolio_head capture`` from the SDK root, in the SDK
venv with ``env -u PYTHONPATH``, then ``scripts/ci/prettier-pinned.sh --write``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests._listed_head_fixtures import (
    API,
    API_HOST,
    BASE_COMMIT,
    FakedKeelApi,
    _FrozenDatetime,
    _wire,
    load_world,
)


PORTFOLIO_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "portfolio_outputs_head.json"

_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_LISTED_CLIENT",
    "KEEL_LISTED_PAPER",
    "KEEL_APP_URL",
    "KEEL_API_URL",
    "KEEL_API_KEY",
    "KEEL_SHARE_URL_ROOT",
    "KEEL_AGENT_MODE",
    "KEEL_ALLOW_DIRECT_DEPLOY",
    "KEEL_ANON_AUTO",
)

#: The two portfolio requests a caller can make, by surface.
MCP_CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("no_args", {}),
    ("portfolio", {"view": "portfolio"}),
)
CLI_CALLS: tuple[tuple[str, list[str]], ...] = (
    ("no_args", ["live", "monitor"]),
    ("portfolio", ["live", "monitor", "--view", "portfolio"]),
)


@contextmanager
def _local_env(world: dict[str, Any]) -> Iterator[FakedKeelApi]:
    """A local, full-profile process on an API key, keel-api faked."""
    import itertools

    import keel.client
    import keel.surface
    import keel.tools.outcomes._base as base
    import respx

    saved = {k: os.environ.get(k) for k in _ENV}
    saved_surface = keel.surface._SURFACE
    saved_sleep = keel.client.time.sleep
    saved_clock = (base.datetime, base._ELECTION_SEQ)
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(KEEL_API_KEY="keel_portfolio_head", KEEL_API_URL=API, KEEL_ANON_AUTO="0")
    keel.surface._SURFACE = None
    keel.client.time.sleep = lambda *_a, **_k: None
    base.datetime, base._ELECTION_SEQ = _FrozenDatetime, itertools.count(1)
    api = FakedKeelApi(world)
    with tempfile.TemporaryDirectory(prefix="keel-portfolio-head-") as tmp:
        try:
            with (
                patch("keel.config.CONFIG_FILE", Path(tmp) / "config.yaml"),
                patch("keel.config.CONFIG_DIR", Path(tmp)),
                respx.mock(assert_all_called=False) as router,
            ):
                router.route(host=API_HOST).mock(side_effect=api)
                yield api
        finally:
            keel.surface._SURFACE = saved_surface
            keel.client.time.sleep = saved_sleep
            base.datetime, base._ELECTION_SEQ = saved_clock
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def full_call(args: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
    from keel.mcp.server import create_server

    with _local_env(world) as api:
        result = asyncio.run(create_server().call_tool("keel_live_monitor", args))
    wire = _wire(result.to_mcp_result())
    content = wire.get("content") or []
    return {
        "structured": wire.get("structuredContent"),
        "text": "\n".join(c.get("text", "") for c in content if c.get("type") == "text"),
        "meta": wire.get("_meta"),
        "is_error": bool(wire.get("isError", False)),
        "requests": api.requests,
    }


def cli_call(argv: list[str], world: dict[str, Any]) -> dict[str, Any]:
    from click.testing import CliRunner
    from keel.cli.main import cli

    with _local_env(world) as api:
        result = CliRunner().invoke(cli, argv, catch_exceptions=False)
    return {"output": result.output, "exit_code": result.exit_code, "requests": api.requests}


def drive_portfolio_outputs(world: dict[str, Any]) -> list[dict[str, Any]]:
    from keel.tools.outcomes import _bootstrap

    _bootstrap()
    out = [
        {"name": f"full:{name}", "args": args, **full_call(args, world)} for name, args in MCP_CALLS
    ]
    out += [
        {"name": f"cli:{name}", "argv": argv, **cli_call(argv, world)} for name, argv in CLI_CALLS
    ]
    return out


def capture() -> None:
    outputs = drive_portfolio_outputs(load_world())
    empty = [o["name"] for o in outputs if not (o.get("text") or o.get("output"))]
    if len(outputs) != 4 or empty:
        raise SystemExit(f"expected four non-empty outputs; got {len(outputs)}, empty: {empty}")
    PORTFOLIO_FIXTURE.write_text(
        json.dumps(
            {"base_commit": BASE_COMMIT, "outputs": outputs},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{PORTFOLIO_FIXTURE.name}: {len(outputs)} outputs")


if __name__ == "__main__":  # pragma: no cover - a one-off capture, not a test
    if sys.argv[1:] != ["capture"]:
        raise SystemExit("usage: python -m tests._portfolio_head capture")
    capture()
