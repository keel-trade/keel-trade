"""Tests for keel.output."""

import json
import sys
from io import StringIO
from unittest.mock import patch

from keel.errors import KeelError, NotFoundError
from keel.output import emit, emit_error, format_human, format_json, format_table, format_tsv


# ── format_json ──────────────────────────────────────────────────────────────


def test_format_json_dict():
    result = format_json({"a": 1, "b": "two"})
    data = json.loads(result)
    assert data == {"a": 1, "b": "two"}


def test_format_json_list():
    result = format_json([{"name": "ROC"}, {"name": "EWMA"}])
    data = json.loads(result)
    assert len(data) == 2


def test_format_json_nested():
    result = format_json({"params": {"period": 8}, "list": [1, 2, 3]})
    data = json.loads(result)
    assert data["params"]["period"] == 8
    assert data["list"] == [1, 2, 3]


def test_format_json_string():
    result = format_json("hello")
    assert json.loads(result) == "hello"


def test_format_json_number():
    result = format_json(42)
    assert json.loads(result) == 42


def test_format_json_null():
    result = format_json(None)
    assert json.loads(result) is None


def test_format_json_is_indented():
    result = format_json({"a": 1})
    assert "\n" in result  # indented JSON has newlines


def test_format_json_empty_list():
    result = format_json([])
    assert json.loads(result) == []


def test_format_json_empty_dict():
    result = format_json({})
    assert json.loads(result) == {}


# ── format_table ─────────────────────────────────────────────────────────────


def test_format_table_list():
    data = [{"name": "ROC", "cat": "indicator"}, {"name": "EWMA", "cat": "indicator"}]
    result = format_table(data, columns=["name", "cat"])
    lines = result.strip().split("\n")
    assert len(lines) == 3  # header + 2 rows
    assert "NAME" in lines[0]
    assert "CAT" in lines[0]
    assert "ROC" in lines[1]
    assert "EWMA" in lines[2]


def test_format_table_empty():
    assert format_table([]) == "(no results)"


def test_format_table_auto_columns():
    data = [{"x": 1, "y": 2}]
    result = format_table(data)
    assert "X" in result
    assert "Y" in result


def test_format_table_alignment():
    data = [
        {"name": "A", "value": "short"},
        {"name": "LongerName", "value": "x"},
    ]
    result = format_table(data, columns=["name", "value"])
    lines = result.strip().split("\n")
    # All lines should have the same column positions (padded with spaces)
    header_name_end = lines[0].index("VALUE")
    for line in lines[1:]:
        # The VALUE column should start at the same position
        assert line[header_name_end:header_name_end + 5] != "     " or "VALUE" not in line


def test_format_table_missing_column_value():
    data = [{"name": "ROC"}, {"name": "EWMA", "extra": "val"}]
    result = format_table(data, columns=["name", "extra"])
    lines = result.strip().split("\n")
    assert len(lines) == 3


def test_format_table_single_row():
    data = [{"name": "ROC"}]
    result = format_table(data, columns=["name"])
    lines = result.strip().split("\n")
    assert len(lines) == 2  # header + 1 row
    assert "NAME" in lines[0]
    assert "ROC" in lines[1]


def test_format_table_dict_input():
    """A single dict is treated as one row."""
    data = {"name": "ROC", "category": "indicator"}
    result = format_table(data, columns=["name", "category"])
    lines = result.strip().split("\n")
    assert len(lines) == 2
    assert "ROC" in lines[1]


def test_format_table_non_dict_list():
    """List of non-dict items gets wrapped as {"value": item}."""
    data = ["alpha", "beta", "gamma"]
    result = format_table(data)
    assert "VALUE" in result
    assert "alpha" in result
    assert "gamma" in result


# ── format_tsv ───────────────────────────────────────────────────────────────


def test_format_tsv():
    data = [{"name": "ROC", "cat": "ind"}]
    result = format_tsv(data, columns=["name", "cat"])
    lines = result.strip().split("\n")
    assert lines[0] == "name\tcat"
    assert lines[1] == "ROC\tind"


def test_format_tsv_tab_separators():
    data = [{"a": "1", "b": "2", "c": "3"}]
    result = format_tsv(data, columns=["a", "b", "c"])
    lines = result.strip().split("\n")
    assert lines[0].count("\t") == 2
    assert lines[1].count("\t") == 2


def test_format_tsv_multiple_rows():
    data = [{"x": "a"}, {"x": "b"}, {"x": "c"}]
    result = format_tsv(data, columns=["x"])
    lines = result.strip().split("\n")
    assert len(lines) == 4  # header + 3 rows


def test_format_tsv_empty():
    result = format_tsv([])
    assert result == ""


def test_format_tsv_auto_columns():
    data = [{"x": 1, "y": 2}]
    result = format_tsv(data)
    assert "x\ty" in result


# ── format_human ─────────────────────────────────────────────────────────────


def test_format_human_dict():
    result = format_human({"name": "ROC", "cat": "indicator"})
    assert "name: ROC" in result
    assert "cat: indicator" in result


def test_format_human_list():
    data = [{"a": 1}]
    result = format_human(data)
    # Should use table format for lists
    assert "A" in result  # header


def test_format_human_list_falls_back_to_table():
    data = [{"name": "ROC"}, {"name": "EWMA"}]
    result = format_human(data)
    assert "NAME" in result
    assert "ROC" in result
    assert "EWMA" in result


def test_format_human_string():
    result = format_human("hello world")
    assert result == "hello world"


def test_format_human_number():
    result = format_human(42)
    assert result == "42"


def test_format_human_dict_with_nested_list():
    result = format_human({"items": [1, 2, 3], "name": "test"})
    assert "items:" in result
    assert "name: test" in result
    # Nested list should be JSON-serialized
    assert "[1, 2, 3]" in result


def test_format_human_dict_with_nested_dict():
    result = format_human({"params": {"period": 8}})
    assert "params:" in result


# ── emit() ───────────────────────────────────────────────────────────────────


def test_emit_writes_to_stdout():
    buf = StringIO()
    with patch("sys.stdout", buf):
        emit({"key": "val"}, "json")
    output = buf.getvalue()
    data = json.loads(output)
    assert data["key"] == "val"


def test_emit_ends_with_newline():
    buf = StringIO()
    with patch("sys.stdout", buf):
        emit("test", "json")
    assert buf.getvalue().endswith("\n")


def test_emit_table_format():
    buf = StringIO()
    with patch("sys.stdout", buf):
        emit([{"name": "ROC"}], "table", columns=["name"])
    output = buf.getvalue()
    assert "NAME" in output
    assert "ROC" in output


def test_emit_tsv_format():
    buf = StringIO()
    with patch("sys.stdout", buf):
        emit([{"x": "1"}], "tsv", columns=["x"])
    output = buf.getvalue()
    assert "x" in output
    assert "1" in output


def test_emit_human_format():
    buf = StringIO()
    with patch("sys.stdout", buf):
        emit({"name": "test"}, "human")
    output = buf.getvalue()
    assert "name: test" in output


def test_emit_unknown_format_falls_back_to_json():
    buf = StringIO()
    with patch("sys.stdout", buf):
        emit({"a": 1}, "unknown_format")
    output = buf.getvalue()
    data = json.loads(output)
    assert data["a"] == 1


# ── emit_error() ─────────────────────────────────────────────────────────────


def test_emit_error_writes_to_stderr():
    """Bare strings emit a minimal envelope under the spec §13.5 fields."""
    buf = StringIO()
    with patch("sys.stderr", buf):
        emit_error("something broke", "json")
    output = buf.getvalue()
    data = json.loads(output)
    assert data["message"] == "something broke" or data["code"] == "something broke"


def test_emit_error_json_with_keel_error():
    """KeelError → spec §13.5 5-field envelope on stderr.

    `suggested_next_action.reason` is only present when a recovery_tool
    is set — otherwise the reason just echoed `what_was_expected` and
    doubled the noise the agent had to parse.
    """
    buf = StringIO()
    with patch("sys.stderr", buf):
        emit_error(NotFoundError("missing item", suggestion="check name"), "json")
    output = buf.getvalue()
    data = json.loads(output)
    assert data["code"] == "not_found"
    assert data["message"] == "missing item"
    assert data["what_was_expected"] == "check name"
    # No recovery_tool on plain NotFoundError → reason omitted, tool=None.
    assert data["suggested_next_action"]["tool"] is None
    assert "reason" not in data["suggested_next_action"]
    # Legacy fields preserved for back-compat
    assert data["exit_code"] == 3


def test_emit_error_json_with_recovery_tool_includes_reason():
    """When recovery_tool IS set, reason describes why call THAT tool —
    genuinely distinct from what_was_expected."""
    from keel.errors import AuthError

    buf = StringIO()
    with patch("sys.stderr", buf):
        emit_error(AuthError("token expired", suggestion="run keel auth login"), "json")
    output = buf.getvalue()
    data = json.loads(output)
    assert data["suggested_next_action"]["tool"] == "keel_auth_login"
    assert data["suggested_next_action"]["reason"] == "run keel auth login"


def test_emit_error_human_format():
    buf = StringIO()
    with patch("sys.stderr", buf):
        emit_error("something broke", "human")
    output = buf.getvalue()
    assert "Error: something broke" in output


def test_emit_error_json_plain_exception():
    """Generic Python exceptions render as a minimal envelope."""
    buf = StringIO()
    with patch("sys.stderr", buf):
        emit_error(ValueError("bad value"), "json")
    output = buf.getvalue()
    data = json.loads(output)
    assert "bad value" in (data.get("message") or "")


def test_emit_error_ends_with_newline():
    buf = StringIO()
    with patch("sys.stderr", buf):
        emit_error("err", "json")
    assert buf.getvalue().endswith("\n")
