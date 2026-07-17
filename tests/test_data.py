"""Tests for the data layer — registry, reference, patterns, examples, templates."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _load_build_data_module():
    sdk_root = Path(__file__).resolve().parents[1]
    build_script = sdk_root / "scripts" / "build_data.py"
    spec = importlib.util.spec_from_file_location("keel_sdk_build_data", build_script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sdk_types_generation_replaces_only_top_level_pandas_import():
    sdk_root = Path(__file__).resolve().parents[1]
    repo_root = sdk_root.parents[2]
    upstream = (repo_root / "libs" / "pipeline_engine" / "types.py").read_text()

    rendered = _load_build_data_module()._render_sdk_types_module(upstream)

    assert ast.get_docstring(ast.parse(rendered), clean=False) == ast.get_docstring(
        ast.parse(upstream), clean=False
    )
    assert rendered.count("SDK-bundled stub") == 1
    assert "\nimport pandas as pd\n" not in rendered
    assert rendered == (sdk_root / "pipeline_engine" / "types.py").read_text()


@pytest.mark.parametrize(
    "source",
    ["value = 1\n", "import pandas as pd\nimport pandas as pd\n"],
)
def test_sdk_types_generation_requires_exactly_one_top_level_pandas_import(source):
    with pytest.raises(RuntimeError, match="Expected exactly one top-level pandas import"):
        _load_build_data_module()._render_sdk_types_module(source)


@pytest.mark.parametrize(
    "relative_path",
    ["validation_shared.py", "dsl/validator.py"],
)
def test_sdk_bundled_validation_modules_match_upstream(relative_path):
    sdk_root = Path(__file__).resolve().parents[1]
    repo_root = sdk_root.parents[2]

    bundled = (sdk_root / "pipeline_engine" / relative_path).read_text()
    upstream = (repo_root / "libs" / "pipeline_engine" / relative_path).read_text()

    assert bundled == upstream


class TestRegistry:
    def test_load_registry(self):
        from keel.data.registry import load_registry

        data = load_registry()
        assert "components" in data
        assert "type_transitions" in data
        assert "phase_index" in data
        assert len(data["components"]) > 0

    def test_search_components_keyword(self):
        from keel.data.registry import search_components

        results = search_components(keyword="ROC")
        assert isinstance(results, list)
        assert len(results) > 0
        assert all("name" in r for r in results)

    def test_search_components_category(self):
        from keel.data.registry import search_components

        results = search_components(category="indicator")
        assert len(results) > 0
        assert all(r["category"] == "indicator" for r in results)

    def test_search_components_query(self):
        from keel.data.registry import search_components

        results = search_components(query="momentum crossover")
        assert isinstance(results, list)

    def test_load_registry_survives_pipeline_engine_import_error(self, monkeypatch):
        """Regression — v0.4.x prod-readiness smoke caught the SDK
        exploding with `ModuleNotFoundError: No module named 'pandas'`
        when run with `PYTHONPATH=libs` set (direnv default in the
        Keel monorepo). Root cause: `pipeline_engine.base.registry`
        triggers `pipeline_engine.__init__` which imports
        `pipeline_engine.context` which imports pandas. The fix
        catches the ImportError and returns bundled JSON data
        regardless — read-only queries (search/detail/after/before/
        dump) don't need the live registry hydration.
        """
        import builtins

        from keel.data import registry as _registry

        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name.startswith("pipeline_engine"):
                raise ImportError(f"Simulated missing pipeline_engine: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _blocked_import)
        # Even with pipeline_engine unavailable, load_registry must
        # return the bundled data dict so search/detail/etc. work.
        data = _registry.load_registry()
        assert "components" in data
        assert len(data["components"]) > 0

        # And the search path must still produce results.
        results = _registry.search_components(query="momentum")
        assert isinstance(results, list)

    def test_get_component_detail(self):
        from keel.data.registry import get_component_detail

        detail = get_component_detail("ROC")
        assert detail["name"] == "ROC"
        assert "parameters" in detail
        assert "input_type" in detail

    def test_get_component_detail_not_found(self):
        from keel.data.registry import get_component_detail

        with pytest.raises(KeyError):
            get_component_detail("NonexistentComponent")

    def test_get_components_after(self):
        from keel.data.registry import get_components_after

        results = get_components_after("ROC")
        assert isinstance(results, list)

    def test_get_components_before(self):
        from keel.data.registry import get_components_before

        results = get_components_before("ForecastScaler")
        assert isinstance(results, list)

    def test_get_components_dump(self):
        from keel.data.registry import get_components_dump

        results = get_components_dump()
        assert len(results) > 0
        assert all("name" in r for r in results)


class TestReference:
    def test_load_reference_toc(self):
        from keel.data.reference import load_reference

        result = load_reference()
        assert "topics" in result
        assert len(result["topics"]) > 0

    def test_load_reference_topic(self):
        from keel.data.reference import load_reference

        result = load_reference("phases")
        assert "content" in result
        assert len(result["content"]) > 0

    def test_load_reference_invalid(self):
        from keel.data.reference import load_reference

        with pytest.raises(ValueError):
            load_reference("nonexistent_topic")


class TestPatterns:
    def test_search_patterns(self):
        from keel.data.patterns import search_patterns

        results = search_patterns("momentum")
        assert isinstance(results, list)

    def test_list_patterns(self):
        from keel.data.patterns import list_patterns

        results = list_patterns()
        assert len(results) > 0
        assert all("name" in p for p in results)


class TestExamples:
    def test_strategy_examples(self):
        from keel.data.examples import strategy_examples

        results = strategy_examples()
        # examples.json may wrap in a dict with "examples" key
        assert isinstance(results, (list, dict))
        if isinstance(results, dict):
            assert "examples" in results


class TestTemplates:
    def test_list_templates(self):
        from keel.data.templates import list_templates

        templates = list_templates()
        assert "basic" in templates
        assert "momentum" in templates

    def test_get_template(self):
        from keel.data.templates import get_template

        tmpl = get_template("basic")
        assert "content" in tmpl
        assert "name" in tmpl

    def test_get_template_not_found(self):
        from keel.data.templates import get_template

        with pytest.raises(KeyError):
            get_template("nonexistent")


def test_bundled_tool_schemas_match_build_data_owner():
    sdk_root = Path(__file__).resolve().parents[1]
    repo_root = sdk_root.parents[2]
    build_script = sdk_root / "scripts" / "build_data.py"
    bundled = json.loads((sdk_root / "keel" / "data" / "tool_schemas.json").read_text())
    command = (
        "import importlib.util,json;"
        f"spec=importlib.util.spec_from_file_location('build_data',{str(build_script)!r});"
        "module=importlib.util.module_from_spec(spec);"
        "spec.loader.exec_module(module);"
        "module.ensure_imports();"
        "print(json.dumps(module.build_tool_schemas()))"
    )
    env = {**os.environ, "PYTHONPATH": str(repo_root / "libs")}

    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert bundled == json.loads(result.stdout)
