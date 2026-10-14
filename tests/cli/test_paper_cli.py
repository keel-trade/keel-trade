"""`keel paper deploy` — the CLI mirror of keel_paper_deploy (paper-trading-v2 S06 §3.2.6, AC-06-2).

The paper CLI ships with the released paper SDK (``PAPER_SDK_RELEASED``
committed True, PT2-91), so these arms drive the installed ``keel`` tree that
``keel.cli.main`` builds at import, through the one adapter
(``_cli_adapter.register_all``), and assert it carries the ``paper`` group.

The transport is ``KeelClient``'s HTTP verbs, faked and recording: POST
/v1/paper answers as IC-01-9's route; every other request is recorded and
fails.

Seed (AC-06-2): declare ``simulated_capital`` as a free integer (drop its
``enum`` in paper_deploy.py) ⇒ the ``--capital 5000`` invocation reaches the
transport and ``test_capital_outside_the_presets_never_reaches_the_transport``
reds on the recorded-request count. The handler adds no check of its own: the
route is the one validator (§3.2.4), the enum the client-side refusal. Count:
two invocations, one request.
"""

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner


_CREATE_RESPONSE = {
    "deployment_id": "dep_x",
    "strategy_id": "str_x",
    "name": "Hype carry",
    "status": "LIVE",
    "schedule": "0 */4 * * *",
    "venue": {"code": "PAPER", "capital_class": "practice"},
}


@pytest.fixture
def transport(monkeypatch, tmp_path):
    from keel.client import KeelClient

    requests: list[tuple[str, str, object]] = []

    def post(_self, path, json=None, **params):
        requests.append(("POST", path, json))
        if path == "/v1/paper":
            return dict(_CREATE_RESPONSE)
        raise AssertionError(f"unexpected POST {path}")

    def other(method):
        def verb(_self, path, *args, **kwargs):
            requests.append((method, path, None))
            raise AssertionError(f"unexpected {method} {path}")

        return verb

    monkeypatch.setattr(KeelClient, "post", post)
    for method in ("get", "patch", "put", "delete"):
        monkeypatch.setattr(KeelClient, method, other(method.upper()))
    for key in ("KEEL_SERVER_PROFILE", "KEEL_EXECUTION_MODE", "KEEL_TOOLSETS", "KEEL_APP_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KEEL_API_KEY", "keel_paper_cli_test")
    monkeypatch.setenv("KEEL_ANON_AUTO", "0")
    monkeypatch.setattr("keel.config.CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr("keel.config.CONFIG_DIR", tmp_path)
    return requests


@pytest.fixture
def keel_cli(paper_sdk_released) -> click.Group:
    """The installed `keel` group, which carries the paper commands."""
    from keel.cli.main import cli

    assert {"deploy", "control"} <= set(cli.commands["paper"].commands)
    return cli


def test_paper_deploy_maps_to_the_tool_call(keel_cli, transport):
    result = CliRunner().invoke(
        keel_cli, ["--format", "json", "paper", "deploy", "str_x", "--capital", "10000"]
    )

    assert result.exit_code == 0, result.output
    assert transport == [
        ("POST", "/v1/paper", {"strategy_id": "str_x", "simulated_capital": 10000})
    ]
    envelope = json.loads(result.output)
    assert envelope["deployment_id"] == "dep_x"
    assert envelope["venue"]["capital_class"] == "practice"
    assert envelope["url"].endswith("/paper/dep_x")


def test_capital_outside_the_presets_never_reaches_the_transport(keel_cli, transport):
    runner = CliRunner()
    ok = runner.invoke(
        keel_cli, ["--format", "json", "paper", "deploy", "str_x", "--capital", "10000"]
    )
    bad = runner.invoke(
        keel_cli, ["--format", "json", "paper", "deploy", "str_x", "--capital", "5000"]
    )

    # Two invocations, exactly one request: the 5000 never reached keel-api
    # (the AC-06-2 seed reds here first).
    assert len(transport) == 1, transport
    assert transport[0][2] == {"strategy_id": "str_x", "simulated_capital": 10000}
    assert ok.exit_code == 0, ok.output
    assert bad.exit_code != 0
    assert "5000" in bad.output


def test_capital_defaults_to_the_platform_preset(keel_cli, transport):
    result = CliRunner().invoke(keel_cli, ["--format", "json", "paper", "deploy", "str_x"])

    assert result.exit_code == 0, result.output
    assert transport == [
        ("POST", "/v1/paper", {"strategy_id": "str_x", "simulated_capital": 10000})
    ]


def test_the_command_offers_no_live_scope_and_no_confirmation(keel_cli):
    """§3.2.6: no --scope live, no arming, no --yes (confirm_in_cli False)."""
    params = {p.name for p in keel_cli.commands["paper"].commands["deploy"].params}
    assert {"strategy_id", "simulated_capital", "schedule", "config_id"} <= params
    assert not {"scope", "yes"} & params
    help_text = CliRunner().invoke(keel_cli, ["paper", "deploy", "--help"]).output
    assert "--capital [1000|10000|100000]" in help_text
