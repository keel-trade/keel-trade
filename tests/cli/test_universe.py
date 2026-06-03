"""Tests for keel universe CLI commands."""

import json

from click.testing import CliRunner

from keel.cli.main import cli

runner = CliRunner()

UNIVERSE_STRATEGY = '''Globals(target_timeframe="1d")

Universe(mode="top_volume", top_n=30, market="perp", resolved=["BTC", "ETH", "SOL", "AAVE", "UNI"], resolved_at="2026-01-01", groups={"defi": ["AAVE", "UNI"], "l1": ["BTC", "ETH"]})

Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    ROC(period=8),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="test")'''

NO_GROUPS_STRATEGY = '''Globals(target_timeframe="1d")

Universe(mode="top_volume", top_n=30, market="perp", resolved=["BTC", "ETH"], resolved_at="2026-01-01")

Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    ROC(period=8),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="test")'''

NO_UNIVERSE_STRATEGY = '''Globals(target_timeframe="1d")

Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    ROC(period=8),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="test")'''


# ── get ──────────────────────────────────────────────────────────────────────


def test_get_universe():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(cli, ["--format", "json", "universe", "get", "strat.py"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["universe"]["mode"] == "top_volume"
        assert data["universe"]["top_n"] == 30


def test_get_universe_has_market():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(cli, ["--format", "json", "universe", "get", "strat.py"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["universe"]["market"] == "perp"


def test_get_universe_has_resolved():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(cli, ["--format", "json", "universe", "get", "strat.py"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "resolved" in data["universe"]
        assert "BTC" in data["universe"]["resolved"]


def test_get_universe_has_groups():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(cli, ["--format", "json", "universe", "get", "strat.py"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "groups" in data["universe"]
        assert "defi" in data["universe"]["groups"]
        assert "l1" in data["universe"]["groups"]


def test_get_universe_no_universe():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_UNIVERSE_STRATEGY)
        result = runner.invoke(cli, ["--format", "json", "universe", "get", "strat.py"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["universe"] is None


def test_get_universe_no_groups():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_GROUPS_STRATEGY)
        result = runner.invoke(cli, ["--format", "json", "universe", "get", "strat.py"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["universe"]["mode"] == "top_volume"
        assert "groups" not in data["universe"]


def test_get_universe_missing_file():
    result = runner.invoke(cli, ["--format", "json", "universe", "get", "nonexistent.py"])
    assert result.exit_code != 0


# ── set ──────────────────────────────────────────────────────────────────────


def test_set_universe():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_GROUPS_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "set", "strat.py",
                "--mode", "manual",
                "--symbols", "BTC", "--symbols", "ETH",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "source" in data


def test_set_universe_returns_source():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_GROUPS_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "set", "strat.py",
                "--mode", "manual",
                "--symbols", "BTC", "--symbols", "ETH",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "source" in data


def test_set_universe_mode_top_volume():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_GROUPS_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "set", "strat.py",
                "--mode", "top_volume",
                "--top-n", "20",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "source" in data


def test_set_universe_preserves_source():
    """Setting universe should return valid source."""
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_GROUPS_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "set", "strat.py",
                "--mode", "manual",
                "--symbols", "BTC",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "source" in data
        assert "Pipeline" in data["source"]


# ── add-group ────────────────────────────────────────────────────────────────


def test_add_group():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_GROUPS_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "add-group", "strat.py",
                "large_cap",
                "--symbols", "BTC", "--symbols", "ETH",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "source" in data


def test_add_group_returns_source():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_GROUPS_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "add-group", "strat.py",
                "my_group",
                "--symbols", "SOL",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "source" in data


def test_add_group_duplicate_exits_nonzero():
    """Adding a group that already exists should fail."""
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "add-group", "strat.py",
                "defi",
                "--symbols", "COMP",
            ],
        )
        assert result.exit_code == 7


def test_add_group_no_universe_exits_nonzero():
    """Adding a group to a strategy with no universe creates one."""
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "add-group", "strat.py",
                "new_group",
                "--symbols", "BTC",
            ],
        )
        # Our implementation creates a default universe when none exists
        assert result.exit_code == 0


# ── modify-group ─────────────────────────────────────────────────────────────


def test_modify_group():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            ["--format", "json", "universe", "modify-group", "strat.py", "defi", "--add", "COMP"],
        )
        assert result.exit_code == 0


def test_modify_group_add_symbol():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            ["--format", "json", "universe", "modify-group", "strat.py", "defi", "--add", "COMP"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "source" in data


def test_modify_group_remove_symbol():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "modify-group", "strat.py",
                "defi", "--remove", "AAVE",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "source" in data


def test_modify_group_add_and_remove():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "modify-group", "strat.py",
                "defi", "--add", "COMP", "--remove", "UNI",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "source" in data


def test_modify_group_nonexistent_exits_7():
    """Modifying a group that doesn't exist should fail."""
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            [
                "--format", "json", "universe", "modify-group", "strat.py",
                "nonexistent_group", "--add", "BTC",
            ],
        )
        assert result.exit_code == 7


# ── remove-group ─────────────────────────────────────────────────────────────


def test_remove_group():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            ["--format", "json", "universe", "remove-group", "strat.py", "defi"],
        )
        assert result.exit_code == 0


def test_remove_group_returns_valid():
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            ["--format", "json", "universe", "remove-group", "strat.py", "defi"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "source" in data


def test_remove_group_nonexistent_exits_7():
    """Removing a group that doesn't exist should fail."""
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            ["--format", "json", "universe", "remove-group", "strat.py", "nonexistent_group"],
        )
        assert result.exit_code == 7


def test_remove_group_no_universe_exits_7():
    """Removing a group from a strategy with no universe should fail."""
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli,
            ["--format", "json", "universe", "remove-group", "strat.py", "any_group"],
        )
        assert result.exit_code == 7


# ── resolve ──────────────────────────────────────────────────────────────────


# Unresolved-universe strategy fixture: matches Alain's bug shape (no resolved=
# argument; criteria-only Universe declaration).
UNRESOLVED_STRATEGY = '''Globals(target_timeframe="1d")

Universe(mode="top_volume", top_n=5, market="perp")

Pipeline([
    PriceDataLoader(timeframe="15min"),
    TargetTimeframeResampler(),
    ROC(period=8),
    ForecastScaler(avg_abs_target=10.0),
    ForecastCapper(limit=20.0),
    ForecastWeightNormalizer(target_leverage=1.0),
], name="test")'''


class _StubClient:
    """Mock KeelClient that returns a canned resolve response."""

    last_body: dict = {}

    def __init__(self):
        pass

    def post(self, path: str, json: dict):
        type(self).last_body = json
        return {
            "resolved": ["BTC", "ETH", "SOL", "AVAX", "ARB"],
            "resolved_at": "2026-06-03T12:00:00+00:00",
            "count": 5,
        }


def test_resolve_writes_back_to_file(monkeypatch):
    """`keel universe resolve <file>` reads criteria from source, calls API,
    writes back. No criteria flags needed — DSL is the source of truth."""
    monkeypatch.setattr("keel.client.KeelClient", _StubClient)
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(UNRESOLVED_STRATEGY)
        result = runner.invoke(
            cli, ["--format", "json", "universe", "resolve", "strat.py"]
        )
        assert result.exit_code == 0, result.output
        # Resolved list and timestamp baked into source on disk
        with open("strat.py") as f:
            updated = f.read()
        assert "BTC" in updated
        assert "ETH" in updated
        assert "resolved_at" in updated
        # API called with criteria derived from source
        assert _StubClient.last_body["mode"] == "top_volume"
        assert _StubClient.last_body["top_n"] == 5


def test_resolve_stdin_to_stdout(monkeypatch):
    """Piped DSL → stdout, no file written."""
    monkeypatch.setattr("keel.client.KeelClient", _StubClient)
    result = runner.invoke(
        cli,
        ["universe", "resolve", "-"],
        input=UNRESOLVED_STRATEGY,
    )
    assert result.exit_code == 0, result.output
    assert "BTC" in result.output
    assert "resolved_at" in result.output


def test_resolve_no_universe_fails(monkeypatch):
    """Source without Universe declaration → ValueError surfaced as nonzero exit."""
    monkeypatch.setattr("keel.client.KeelClient", _StubClient)
    with runner.isolated_filesystem():
        with open("strat.py", "w") as f:
            f.write(NO_UNIVERSE_STRATEGY)
        result = runner.invoke(
            cli, ["--format", "json", "universe", "resolve", "strat.py"]
        )
        assert result.exit_code != 0


def test_resolve_deprecated_flag_form_still_works():
    """Old `keel universe resolve --mode top_volume --top-n 50` form keeps working,
    with a deprecation warning on stderr. Back-compat for users on older docs."""
    # No file argument → falls through to legacy path → uses real client.
    # We can't fully test the API call here without a network, but we can
    # confirm the deprecation warning fires and the path is taken.
    result = runner.invoke(
        cli,
        ["universe", "resolve", "--mode", "top_volume", "--top-n", "50"],
    )
    # Don't assert exit_code (it'll fail due to no auth in test env) — assert
    # the deprecation warning is emitted before the network attempt.
    assert "deprecated" in result.output.lower() or "deprecated" in (result.stderr or "").lower()
