"""Caller input never becomes a filesystem path or a different keel-api route (Q-3352).

Prod Sentry caught a probe of the hosted MCP:
`resources/read keel://knowledge/..%252F..%252Fetc%252Fpasswd`. The knowledge
loader joined the name onto the package directory and opened it FIRST; the
probe failed only because the name stayed percent-encoded. The review behind
Q-3352 found two classes and fixed each in one place:

* **bundled names** (knowledge sections, DSL reference topics, skills) are only
  keys into the directory listing — `keel.data._bundled.read_bundled`;
* **ids in keel-api paths** pass `keel.api_path.seg` at every interpolation and
  `check_request_path` at `KeelClient._request`, the one door every request
  passes. Percent-quoting would not do: uvicorn decodes `%2F` before routing.

Every server arm runs TWICE: as deployed (fastmcp 4's default resource
screening on) and with that screening OFF, which is what a server built on
fastmcp 3.x — still inside `keel-trade`'s `fastmcp>=3.1.1` range — runs. The
guard has to be Keel's, not the library's.

Seeds (2026-10-30, each reverted by reversing the edit):
* `load_section` back to `joinpath("knowledge", f"{name}.md").read_text()` →
  the knowledge traversal arms red (screening OFF reads the skill file);
* `seg()` removed from `components_help._detail_via_api` → the tool sweep red
  (`GET /v1/deployments/dep_x/stop`), and the source guard red;
* `check_request_path` removed from `KeelClient._request` → the client arm red;
* `seg()` removed from the `keel://strategy/{id}/lockfile` resource → the
  resource-id sweep red in both screening modes (`zz1/zz2` reaches a path);
* `seg()` removed from `live_monitor`'s `.replace("{id}", …)` → the source
  guard red;
* `_inside()` dropped from `read_bundled` → the symlink arm red;
* the resource wrapper's generic arm re-raising the raw exception → the
  plain-sentence arm red;
* `override=true` put back into the path text → the trigger-override arm red;
* `keel://context/strategy/{id}` back to `except Exception` → its
  plain-sentence arm red (the exception text came back as a successful read).

With every guard in place the knowledge arm stays green as deployed even with
the loader seeded back (fastmcp 4's screening stops `..`), and goes red only
with that screening off: the prod barrier was the library, not Keel.
"""

from __future__ import annotations

import ast
import asyncio
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import respx
from httpx import Response
from keel.api_path import check_request_path, seg
from keel.client import KeelClient
from keel.config import KeelConfig
from keel.errors import NotFoundError, UsageError


API = "https://api.harden.test"
SDK_ROOT = Path(__file__).resolve().parents[1]
KEEL_PKG = SDK_ROOT / "keel"

#: Every way a traversal name can be spelled in a resource URI. One decode is
#: applied by fastmcp; the double-encoded forms are the Sentry probe's shape.
TRAVERSAL_URI_PARTS = (
    "..%2F..%2Fskills%2Fbacktest-and-analyze%2FSKILL",
    "..%252F..%252Fskills%252Fbacktest-and-analyze%252FSKILL",
    "%2e%2e%2f%2e%2e%2fskills%2fbacktest-and-analyze%2fSKILL",
    "%2E%2E%2F%2E%2E%2Fskills%2Fbacktest-and-analyze%2FSKILL",
    "..%5C..%5Cskills%5Cbacktest-and-analyze%5CSKILL",
    "%2Fetc%2Fhosts",
    "%2Fetc%2F..%2Fetc%2Fhosts",
    "operating_core%00",
    "a" * 400,
    "..",
    ".",
    "%20operating_core",
)

#: Ids that would restructure a keel-api path, spelled as they arrive in a
#: resource URI (one decode by fastmcp).
HOSTILE_URI_IDS = (
    "zz1%3Fzz2",
    "zz1%23zz2",
    "zz1%2Fzz2",
    "zz1%252Fzz2",
    "..",
    "%2E%2E",
    "..%2F..%2Fzz3",
    "zz1%5Czz2",
    "zz1%00zz2",
)

#: The only keel-api paths a hostile resource id may produce: the two
#: resources that send the id as a QUERY value (httpx encodes it).
QUERY_ONLY_PATHS = {"/v1/strategy-work", "/v1/backtests"}

#: Text only the escaped-to file carries (a skill's frontmatter).
ESCAPED_MARKER = "name: backtest-and-analyze"


# ── one rule: seg / check_request_path ───────────────────────────────────


@pytest.mark.parametrize(
    "value",
    ["", ".", "..", "a/b", "../me", "a?b", "a#b", "a%2Fb", "a\\b", "a\x00b", "a\nb", None, 1.5],
)
def test_seg_refuses_anything_that_is_not_one_segment(value):
    with pytest.raises(UsageError) as exc:
        seg(value)
    # One plain sentence; never the value.
    assert "not valid" in str(exc.value)
    if isinstance(value, str) and value not in ("", ".", ".."):
        assert value not in str(exc.value)


@pytest.mark.parametrize("value", ["str_01JABC", "btr_x-1", "HEAD", "v1.2", 7, "RSI", "a b"])
def test_control_seg_accepts_real_ids_names_and_refs(value):
    assert seg(value) == str(value)


@pytest.mark.parametrize(
    "path",
    [
        "/v1/strategies/../me",
        "/v1/strategies/./x",
        "/v1/strategies//x",
        "/v1/strategies/x?y",
        "/v1/strategies/x#y",
        "/v1/strategies/a%2Fb",
        "/v1/strategies/a\\b",
        "v1/strategies/x",
        "/v1/strategies/x\r\nHost: evil",
    ],
)
def test_client_refuses_a_restructured_path_and_sends_nothing(path):
    client = KeelClient(KeelConfig(api_key="tok", api_url=API))
    with respx.mock(base_url=API, assert_all_called=False) as router:
        route = router.route().mock(return_value=Response(200, json={}))
        with pytest.raises(UsageError):
            client.get(path)
        assert route.call_count == 0
    client.close()


def test_control_client_sends_a_clean_path():
    client = KeelClient(KeelConfig(api_key="tok", api_url=API))
    with respx.mock(base_url=API, assert_all_called=False) as router:
        route = router.get("/v1/strategies/str_ok/lock").mock(return_value=Response(200, json={}))
        assert client.get("/v1/strategies/str_ok/lock") == {}
        assert route.call_count == 1
    client.close()


# ── source guard: every /v1 interpolation goes through seg() ─────────────

#: The only interpolations allowed without seg(): a constant action suffix
#: from the module's own `_ACTIONS` table (`"/stop"`, `"/pause"`), never a
#: caller value. `check_request_path` still covers the whole path.
EXEMPT = {
    ("keel/tools/outcomes/live_control.py", "suffix"),
    ("keel/tools/outcomes/paper_control.py", "suffix"),
}
PATH_PREFIXES = ("/v1/", "/internal/v1/")


def _is_seg(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Call) and getattr(expr.func, "id", None) == "seg"


def unguarded_interpolations(source: str) -> list[tuple[int, str]]:
    """``(line, expr)`` for every value put into a keel-api path without
    ``seg()``: an f-string interpolation, or a ``template.replace("{id}", x)``
    placeholder fill (`live_monitor`'s view table)."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("{")
            and node.args[0].value.endswith("}")
            and not node.args[0].value.startswith("{{")
            and not _is_seg(node.args[1])
        ):
            out.append((node.lineno, ast.unparse(node.args[1])))
            continue
        if not (isinstance(node, ast.JoinedStr) and node.values):
            continue
        first = node.values[0]
        if not (isinstance(first, ast.Constant) and str(first.value).startswith(PATH_PREFIXES)):
            continue
        for value in node.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            expr = value.value
            if _is_seg(expr):
                continue
            out.append((expr.lineno, ast.unparse(expr)))
    return out


def test_every_keel_api_path_interpolation_goes_through_seg():
    guarded = 0
    offenders = []
    for py in sorted(KEEL_PKG.rglob("*.py")):
        rel = py.relative_to(SDK_ROOT).as_posix()
        source = py.read_text(encoding="utf-8")
        guarded += source.count("{seg(")
        for line, expr in unguarded_interpolations(source):
            if (rel, expr) not in EXEMPT:
                offenders.append(f"{rel}:{line} {{{expr}}}")
    assert not offenders, "interpolate with seg(): " + ", ".join(offenders)
    # Non-vacuity: the scan saw the real population (82 at Q-3352).
    assert guarded >= 80, guarded


def test_control_the_source_guard_flags_an_unwrapped_interpolation():
    flagged = unguarded_interpolations('p = f"/v1/strategies/{sid}/lock"\n')
    assert flagged == [(1, "sid")]
    assert unguarded_interpolations('p = f"/v1/strategies/{seg(sid)}/lock"\n') == []
    assert unguarded_interpolations('p = f"https://x/{sid}"\n') == []
    assert unguarded_interpolations('p = t.replace("{id}", dep)\n') == [(1, "dep")]
    assert unguarded_interpolations('p = t.replace("{id}", seg(dep))\n') == []
    assert unguarded_interpolations('p = t.replace("{{CSS}}", css)\n') == []


# ── bundled names are keys, never paths ──────────────────────────────────

BUNDLED_HOSTILE = (
    "../../skills/backtest-and-analyze/SKILL",
    "../reference/phases",
    "/etc/hosts",
    str(KEEL_PKG / "data" / "reference" / "phases"),
    "a" * 5000,
    "operating_core\x00",
    "",
    ".",
    None,
)


def _loaders():
    from keel.data.knowledge import load_section
    from keel.data.reference import _load_reference_file
    from keel.skills import _read_skill_file

    return {
        "knowledge": load_section,
        "reference": _load_reference_file,
        "skills": _read_skill_file,
    }


@pytest.mark.parametrize("loader", ["knowledge", "reference", "skills"])
@pytest.mark.parametrize("name", BUNDLED_HOSTILE)
def test_a_bundled_loader_refuses_a_name_that_is_not_listed(loader, name):
    fn = _loaders()[loader]
    if loader == "reference":
        fn.cache_clear()
    with pytest.raises(NotFoundError) as exc:
        fn(name)
    message = str(exc.value)
    assert message.startswith("Unknown ") and "Available: " in message
    assert str(SDK_ROOT) not in message and os.sep + "etc" not in message
    if isinstance(name, str) and len(name) > 2:
        assert name not in message


@pytest.mark.parametrize(
    "loader,name,marker",
    [
        ("knowledge", "pipeline_system", "#"),
        ("knowledge", "platform-operations", "#"),
        ("reference", "phases", "#"),
        ("skills", "backtest-and-analyze", ESCAPED_MARKER),
    ],
)
def test_control_a_listed_name_still_reads(loader, name, marker):
    assert marker in _loaders()[loader](name)


def test_a_listed_file_that_resolves_outside_its_directory_is_refused(tmp_path, monkeypatch):
    """Defence in depth: a symlinked entry cannot point the read elsewhere."""
    from keel.data._bundled import MARKDOWN_FILES, bundled_names, read_bundled

    outside = tmp_path / "secret.md"
    outside.write_text("SECRET")
    pkg = tmp_path / "q3352_fixture_pkg"
    (pkg / "docs").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "docs" / "ok.md").write_text("fine")
    (pkg / "docs" / "evil.md").symlink_to(outside)
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        assert bundled_names("q3352_fixture_pkg", "docs", MARKDOWN_FILES) == ("evil", "ok")
        assert read_bundled("q3352_fixture_pkg", "docs", "ok", layout=MARKDOWN_FILES, kind="doc")
        with pytest.raises(NotFoundError):
            read_bundled("q3352_fixture_pkg", "docs", "evil", layout=MARKDOWN_FILES, kind="doc")
    finally:
        sys.modules.pop("q3352_fixture_pkg", None)


# ── the real server, in-process, hosted ──────────────────────────────────

_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
)


@contextmanager
def _hosted_full():
    from keel.hosting import bind_request_credentials, clear_request_credentials

    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ["KEEL_EXECUTION_MODE"] = "hosted"
    token = bind_request_credentials(token="tok_harden", api_url=API)
    try:
        yield
    finally:
        clear_request_credentials(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _server(screening: bool):
    import fastmcp
    from keel.mcp.server import create_server

    server = create_server()
    if not screening:
        # What a fastmcp 3.x server runs: no resource-parameter screening.
        if int(fastmcp.__version__.split(".")[0]) >= 4:
            assert hasattr(server, "_resource_security"), "fastmcp moved its screening switch"
            server._resource_security = None
        else:
            assert not hasattr(server, "_resource_security")
    return server


def _contents_text(result) -> str:
    return "\n".join(getattr(c, "text", "") or "" for c in result)


async def _read_all(server, uris):
    from fastmcp import Client

    out = {}
    async with Client(server) as client:
        for uri in uris:
            try:
                out[uri] = ("ok", _contents_text(await client.read_resource(uri)))
            except Exception as e:  # noqa: BLE001 — the refusal IS the observation
                out[uri] = ("error", str(e))
    return out


@pytest.mark.parametrize("screening", [True, False], ids=["as-deployed", "no-fastmcp-screening"])
def test_knowledge_and_reference_resources_never_read_outside_their_directory(screening):
    uris = [f"keel://knowledge/{p}" for p in TRAVERSAL_URI_PARTS]
    uris += [f"keel://dsl/reference/{p}" for p in TRAVERSAL_URI_PARTS]
    with _hosted_full(), respx.mock(base_url=API, assert_all_called=False):
        results = asyncio.run(_read_all(_server(screening), uris))
    assert len(results) == len(uris) == 2 * len(TRAVERSAL_URI_PARTS)
    for uri, (state, text) in results.items():
        assert state == "error", (uri, text[:200])
        assert ESCAPED_MARKER not in text
        assert str(SDK_ROOT) not in text and "site-packages" not in text, (uri, text[:300])
        assert "Errno" not in text and "Traceback" not in text, (uri, text[:300])


def test_control_knowledge_and_reference_resources_serve_listed_names():
    uris = ["keel://knowledge/pipeline_system", "keel://dsl/reference/phases"]
    with _hosted_full(), respx.mock(base_url=API, assert_all_called=False):
        results = asyncio.run(_read_all(_server(screening=False), uris))
    assert all(state == "ok" and len(text) > 200 for state, text in results.values()), results


def _id_templates(server) -> list[str]:
    async def run():
        from fastmcp import Client

        async with Client(server) as client:
            # mcp 2.x renamed the field; mcp 1.x (the 3.11 conda env) has only
            # the old spelling.
            return [
                getattr(t, "uri_template", None) or t.uriTemplate
                for t in await client.list_resource_templates()
            ]

    names = ("{section}", "{topic}")
    return [t for t in asyncio.run(run()) if not any(n in t for n in names)]


@pytest.mark.parametrize("screening", [True, False], ids=["as-deployed", "no-fastmcp-screening"])
def test_no_hostile_resource_id_reaches_keel_api(screening):
    with _hosted_full():
        server = _server(screening)
        templates = _id_templates(server)
        # Non-vacuity: every id-taking template is swept (seven at Q-3352).
        assert len(templates) >= 7, templates
        uris = [
            t.replace(t[t.index("{") : t.index("}") + 1], hostile)
            for t in templates
            for hostile in HOSTILE_URI_IDS
        ]
        with respx.mock(base_url=API, assert_all_called=False) as router:
            router.route().mock(return_value=Response(200, json={}))
            asyncio.run(_read_all(server, uris))
            sent = [c.request.url.raw_path.decode().split("?", 1)[0] for c in router.calls]
    # A hostile id is refused before any request, except where it is a query
    # value; no path carries it.
    leaked = [p for p in sent if p not in QUERY_ONLY_PATHS]
    assert not leaked, leaked


def _clean(raw_path: str) -> bool:
    """A recorded upstream request carries no hostile fragment in its path."""
    path = raw_path.split("?", 1)[0]
    try:
        check_request_path(path)
    except UsageError:
        return False
    return not any(marker in path for marker in ("dep_x", "zz_", "zz1", "zz3"))


def test_control_a_real_resource_id_reaches_its_endpoint():
    with _hosted_full():
        server = _server(screening=False)
        templates = _id_templates(server)
        uris = [t.replace(t[t.index("{") : t.index("}") + 1], "str_ok") for t in templates]
        with respx.mock(base_url=API, assert_all_called=False) as router:
            router.route().mock(return_value=Response(200, json={}))
            asyncio.run(_read_all(server, uris))
            sent = [c.request.url.raw_path.decode() for c in router.calls]
    assert "/v1/strategies/str_ok/versions/HEAD/source" in sent
    assert "/v1/strategies/str_ok/lock" in sent
    assert "/v1/components/str_ok" in sent


#: Tool-argument values that would restructure a path (no URI decoding here:
#: a tool argument arrives as-is).
HOSTILE_ARGS = ("../deployments/dep_x/stop?", "zz_a/zz_b", "zz_c%2Fzz_d", "zz_e#zz_f", "..")

#: A valid companion argument so a hostile value reaches the code that would
#: interpolate it, rather than stopping at a missing-input refusal.
BASE_ARGS = {"strategy_id": "str_ok", "deployment_id": "dep_ok", "backtest_id": "btr_ok"}


async def _sweep_tools(server):
    from fastmcp import Client

    cases = 0
    async with Client(server) as client:
        tools = await client.list_tools()
        for tool in tools:
            schema = getattr(tool, "input_schema", None) or tool.inputSchema
            props = (schema or {}).get("properties", {})
            for param, spec in props.items():
                if spec.get("type") not in ("string", None):
                    continue
                for hostile in HOSTILE_ARGS:
                    args = {k: v for k, v in BASE_ARGS.items() if k in props and k != param}
                    args[param] = hostile
                    await asyncio.wait_for(
                        client.call_tool(tool.name, args, raise_on_error=False), timeout=30
                    )
                    cases += 1
    return len(tools), cases


def test_no_tool_argument_restructures_a_keel_api_path(monkeypatch):
    monkeypatch.setattr("keel.client.time.sleep", lambda _s: None)
    with _hosted_full():
        server = _server(screening=True)
        with respx.mock(base_url=API, assert_all_called=False) as router:
            router.route().mock(return_value=Response(200, json={}))
            n_tools, cases = asyncio.run(_sweep_tools(server))
            sent = [c.request.url.raw_path.decode() for c in router.calls]
    # Non-vacuity: the whole hosted surface was swept, and it did talk to
    # keel-api (the clean companion arguments reach it).
    assert n_tools >= 30 and cases >= 200, (n_tools, cases)
    assert sent
    leaked = sorted({p for p in sent if not _clean(p)})
    assert not leaked, leaked


@pytest.mark.parametrize(
    "tool,args,expected",
    [
        ("keel_components_get", {"name": "RSI"}, "/v1/components/RSI"),
        ("keel_strategy_get", {"strategy_id": "str_ok"}, "/v1/strategies/str_ok"),
        ("keel_strategy_history", {"strategy_id": "str_ok"}, "/v1/strategies/str_ok/versions"),
    ],
)
def test_control_a_clean_tool_argument_reaches_its_endpoint(tool, args, expected):
    async def run(server):
        from fastmcp import Client

        async with Client(server) as client:
            await client.call_tool(tool, args, raise_on_error=False)

    with _hosted_full():
        server = _server(screening=True)
        with respx.mock(base_url=API, assert_all_called=False) as router:
            router.route().mock(return_value=Response(200, json={}))
            asyncio.run(run(server))
            sent = [c.request.url.raw_path.decode().split("?", 1)[0] for c in router.calls]
    assert expected in sent, sent


@pytest.mark.parametrize(
    "uri", ["keel://strategy/str_ok/lockfile", "keel://context/strategy/str_ok"]
)
def test_resource_errors_reach_the_client_as_plain_sentences(uri):
    """A resource that fails on something other than a KeelError says one
    sentence; the cause stays in the server log. (`context/strategy` used to
    return the exception's text as a successful read's `error` field.)"""
    with _hosted_full(), respx.mock(base_url=API, assert_all_called=False) as router:
        router.route().mock(side_effect=OSError("[Errno 2] /opt/conda/envs/keel/secret.md"))
        results = asyncio.run(_read_all(_server(screening=True), [uri]))
    state, text = results[uri]
    assert state == "error"
    assert "/opt/conda" not in text and "Errno" not in text, text
    assert "Keel could not read this resource." in text


def test_live_trigger_override_still_reaches_keel_api_as_a_query(monkeypatch):
    """`override=true` moved from text in the path (which the client now
    refuses) to a query parameter; the request keel-api sees is unchanged."""
    from keel.tools.outcomes import live_control
    from keel.tools.outcomes._base import ToolContext

    monkeypatch.setattr("keel.permissions.assert_armed_for_account", lambda _a: None)
    client = KeelClient(KeelConfig(api_key="tok", api_url=API))
    with respx.mock(base_url=API, assert_all_called=False) as router:
        router.route().mock(return_value=Response(200, json={"status": "queued"}))
        live_control._handler(
            {"deployment_id": "dep_ok", "action": "trigger", "override": True},
            ToolContext(api_client=client, is_tty=False),
        )
        sent = [(c.request.method, str(c.request.url)) for c in router.calls]
    client.close()
    assert ("POST", f"{API}/v1/deployments/dep_ok/trigger?override=true") in sent, sent
