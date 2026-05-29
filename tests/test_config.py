"""Tests for keel.config."""

import os
from unittest.mock import patch

import yaml

from keel.config import KeelConfig, load_config, save_config


# ── Default config ───────────────────────────────────────────────────────────


def test_default_config():
    with patch.dict(os.environ, {}, clear=True):
        with patch("keel.config.CONFIG_FILE") as mock_file:
            mock_file.exists.return_value = False
            config = load_config()
            assert config.api_key is None
            assert config.api_url == "https://api.usekeel.io"


def test_default_api_url():
    config = KeelConfig()
    assert config.api_url == "https://api.usekeel.io"


def test_default_api_key_is_none():
    config = KeelConfig()
    assert config.api_key is None


# ── Env var loading ──────────────────────────────────────────────────────────


def test_env_var_override():
    with patch.dict(os.environ, {"KEEL_API_KEY": "sk_test_123"}, clear=False):
        with patch("keel.config.CONFIG_FILE") as mock_file:
            mock_file.exists.return_value = False
            config = load_config()
            assert config.api_key == "sk_test_123"


def test_env_var_api_url_override():
    with patch.dict(os.environ, {"KEEL_API_URL": "http://localhost:8080"}, clear=False):
        with patch("keel.config.CONFIG_FILE") as mock_file:
            mock_file.exists.return_value = False
            config = load_config()
            assert config.api_url == "http://localhost:8080"


def test_env_var_takes_precedence_over_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    # Write a config file with one key
    config_file.write_text(yaml.dump({"api_key": "from_file"}))

    # Set env var with a different key
    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {"KEEL_API_KEY": "from_env"}, clear=False):
        config = load_config()
        assert config.api_key == "from_env"


def test_env_var_api_url_takes_precedence_over_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    config_file.write_text(yaml.dump({"api_url": "https://file.example.com"}))

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {"KEEL_API_URL": "https://env.example.com"}, clear=False):
        config = load_config()
        assert config.api_url == "https://env.example.com"


# ── Config file loading ─────────────────────────────────────────────────────


def test_load_from_config_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    config_file.write_text(yaml.dump({"api_key": "sk_from_file", "api_url": "https://custom.api.io"}))

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        config = load_config()
        assert config.api_key == "sk_from_file"
        assert config.api_url == "https://custom.api.io"


def test_load_ignores_corrupt_config_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    config_file.write_text("{{invalid yaml: [}")

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        config = load_config()
        assert config.api_key is None
        assert config.api_url == "https://api.usekeel.io"


def test_load_handles_empty_config_file(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    config_file.write_text("")

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        config = load_config()
        assert config.api_key is None
        assert config.api_url == "https://api.usekeel.io"


# ── save_config ──────────────────────────────────────────────────────────────


def test_save_and_load(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        # Save
        save_config(KeelConfig(api_key="sk_saved"))
        assert config_file.exists()

        # Load
        config = load_config()
        assert config.api_key == "sk_saved"


def test_save_creates_directory(tmp_path):
    config_dir = tmp_path / "nested" / ".keel"
    config_file = config_dir / "config.yaml"

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir):
        save_config(KeelConfig(api_key="sk_test"))
        assert config_dir.exists()
        assert config_file.exists()


def test_save_writes_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir):
        save_config(KeelConfig(api_key="sk_yaml_test"))
        content = yaml.safe_load(config_file.read_text())
        assert content["api_key"] == "sk_yaml_test"


def test_save_omits_default_api_url(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir):
        save_config(KeelConfig(api_key="sk_test", api_url="https://api.usekeel.io"))
        content = yaml.safe_load(config_file.read_text())
        assert "api_url" not in content


def test_save_includes_custom_api_url(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir):
        save_config(KeelConfig(api_key="sk_test", api_url="https://custom.api.io"))
        content = yaml.safe_load(config_file.read_text())
        assert content["api_url"] == "https://custom.api.io"


def test_save_omits_none_api_key(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir):
        save_config(KeelConfig(api_key=None))
        content = config_file.read_text()
        # With no api_key and default api_url, file should have empty dict
        data = yaml.safe_load(content)
        assert data is None or "api_key" not in (data or {})


def test_save_roundtrip_with_custom_url(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        original = KeelConfig(api_key="sk_round", api_url="https://staging.usekeel.io")
        save_config(original)
        loaded = load_config()
        assert loaded.api_key == original.api_key
        assert loaded.api_url == original.api_url


# ── OAuth fields (refresh_token, token_expires_at, client_name) ─────────────


def test_oauth_fields_default_to_none():
    config = KeelConfig()
    assert config.refresh_token is None
    assert config.token_expires_at is None
    assert config.client_name is None


def test_save_omits_oauth_fields_when_none(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir):
        save_config(KeelConfig(api_key="sk_test"))
        data = yaml.safe_load(config_file.read_text()) or {}
        assert "refresh_token" not in data
        assert "token_expires_at" not in data
        assert "client_name" not in data


def test_save_persists_oauth_fields(tmp_path):
    from datetime import datetime, timezone

    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path
    expires = datetime(2026, 6, 1, 12, 30, 45, tzinfo=timezone.utc)

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir):
        save_config(KeelConfig(
            api_key="jwt.access.token",
            refresh_token="krt_abc123",
            token_expires_at=expires,
            client_name="Keel CLI/0.4.0",
        ))
        data = yaml.safe_load(config_file.read_text())
        assert data["api_key"] == "jwt.access.token"
        assert data["refresh_token"] == "krt_abc123"
        assert data["token_expires_at"] == "2026-06-01T12:30:45+00:00"
        assert data["client_name"] == "Keel CLI/0.4.0"


def test_oauth_roundtrip(tmp_path):
    from datetime import datetime, timezone

    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path
    expires = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        save_config(KeelConfig(
            api_key="jwt.X",
            refresh_token="krt_xyz",
            token_expires_at=expires,
            client_name="Keel CLI/0.4.0",
        ))
        loaded = load_config()
        assert loaded.api_key == "jwt.X"
        assert loaded.refresh_token == "krt_xyz"
        assert loaded.token_expires_at == expires
        assert loaded.client_name == "Keel CLI/0.4.0"


def test_load_normalizes_naive_token_expires_at_to_utc(tmp_path):
    from datetime import datetime, timezone

    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    # Simulate a config file with a naive datetime string (older format / hand-edit).
    config_file.write_text(yaml.dump({
        "api_key": "jwt.X",
        "token_expires_at": "2026-06-01T12:30:45",  # no tz
    }))

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        loaded = load_config()
        assert loaded.token_expires_at == datetime(2026, 6, 1, 12, 30, 45, tzinfo=timezone.utc)


def test_load_handles_invalid_token_expires_at(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    config_file.write_text(yaml.dump({
        "api_key": "jwt.X",
        "token_expires_at": "not-a-date",
    }))

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        loaded = load_config()
        assert loaded.api_key == "jwt.X"
        assert loaded.token_expires_at is None


def test_load_backwards_compatible_with_v03_config(tmp_path):
    """v0.3.x configs have only api_key/api_url — must still load cleanly."""
    config_file = tmp_path / "config.yaml"
    config_dir = tmp_path

    config_file.write_text(yaml.dump({"api_key": "sk_legacy", "api_url": "https://api.usekeel.io"}))

    with patch("keel.config.CONFIG_FILE", config_file), \
         patch("keel.config.CONFIG_DIR", config_dir), \
         patch.dict(os.environ, {}, clear=True):
        loaded = load_config()
        assert loaded.api_key == "sk_legacy"
        assert loaded.refresh_token is None
        assert loaded.token_expires_at is None
        assert loaded.client_name is None
