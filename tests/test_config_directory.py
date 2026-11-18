"""Config isolation without changing the process home or existing login."""

import json
import os
import subprocess
import sys
from pathlib import Path


def probe(env, code):
    return json.loads(subprocess.check_output([sys.executable, "-c", code], env=env, text=True))


def test_explicit_config_directory_round_trips_login_without_home_change(tmp_path):
    target = tmp_path / "isolated" / "config"
    env = {**os.environ, "KEEL_CONFIG_DIR": str(target)}
    result = probe(
        env,
        """
import json
from pathlib import Path
from keel.config import CONFIG_FILE, KeelConfig, load_file_config, save_config
save_config(KeelConfig(api_key='test-only', api_url='https://staging.invalid', anon_org_id='anon_test'))
loaded = load_file_config()
print(json.dumps({'file': str(CONFIG_FILE), 'home': str(Path.home()), 'key': loaded.api_key, 'org': loaded.anon_org_id, 'url': loaded.api_url}))
""",
    )
    assert result == {
        "file": str(target / "config.yaml"),
        "home": str(Path.home()),
        "key": "test-only",
        "org": "anon_test",
        "url": "https://staging.invalid",
    }
    assert (target / "config.yaml").is_file()


def test_unset_or_empty_override_preserves_existing_default():
    for value in (None, ""):
        env = dict(os.environ)
        env.pop("KEEL_CONFIG_DIR", None)
        if value is not None:
            env["KEEL_CONFIG_DIR"] = value
        result = probe(
            env,
            "import json; from keel.config import CONFIG_FILE; print(json.dumps(str(CONFIG_FILE)))",
        )
        assert result == str(Path.home() / ".keel" / "config.yaml")
