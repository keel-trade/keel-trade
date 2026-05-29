"""Output formatters for CLI results — json, table, tsv, human."""

from __future__ import annotations

import json
import sys
from typing import Any


def emit(data: Any, fmt: str = "json", columns: list[str] | None = None) -> None:
    """Write formatted data to stdout."""
    text = _format(data, fmt, columns)
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def emit_error(error: Any, fmt: str = "json") -> None:
    """Write error to stderr.

    Structured modes (json / table / tsv) emit the spec §13.5 5-field
    envelope when the error is a `KeelError` (or any object exposing
    `to_envelope()`). Human mode prints the message + the next-action
    reason on two lines.
    """
    if hasattr(error, "to_envelope"):
        envelope = error.to_envelope()
    elif hasattr(error, "to_dict"):
        envelope = error.to_dict()
    else:
        envelope = {"code": "error", "message": str(error)}

    if fmt in {"json", "table", "tsv"}:
        text = json.dumps(envelope, indent=2)
    else:
        # human mode: one-line message + next-action reason if present
        lines = [f"Error: {envelope.get('message') or envelope.get('code') or error}"]
        sna = envelope.get("suggested_next_action")
        if isinstance(sna, dict) and sna.get("reason"):
            lines.append(f"  → {sna['reason']}")
        text = "\n".join(lines)
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _format(data: Any, fmt: str, columns: list[str] | None) -> str:
    if fmt == "json":
        return format_json(data)
    elif fmt == "table":
        return format_table(data, columns)
    elif fmt == "tsv":
        return format_tsv(data, columns)
    elif fmt == "human":
        return format_human(data, columns)
    return format_json(data)


def format_json(data: Any) -> str:
    """Format as indented JSON."""
    return json.dumps(data, indent=2, default=str)


def format_table(data: Any, columns: list[str] | None = None) -> str:
    """Format as aligned text table."""
    rows = _to_rows(data)
    if not rows:
        return "(no results)"
    cols = columns or list(rows[0].keys())
    # Compute column widths
    widths = {c: len(c) for c in cols}
    for row in rows:
        for c in cols:
            val = str(row.get(c, ""))
            widths[c] = max(widths[c], len(val))
    # Header
    header = "  ".join(c.upper().ljust(widths[c]) for c in cols)
    lines = [header]
    for row in rows:
        line = "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in cols)
        lines.append(line)
    return "\n".join(lines)


def format_tsv(data: Any, columns: list[str] | None = None) -> str:
    """Format as tab-separated values (half the tokens of JSON)."""
    rows = _to_rows(data)
    if not rows:
        return ""
    cols = columns or list(rows[0].keys())
    lines = ["\t".join(cols)]
    for row in rows:
        lines.append("\t".join(str(row.get(c, "")) for c in cols))
    return "\n".join(lines)


def format_human(data: Any, columns: list[str] | None = None) -> str:
    """Format for human reading — table for lists, key-value for dicts."""
    if isinstance(data, list):
        return format_table(data, columns)
    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            if isinstance(v, (list, dict)):
                v = json.dumps(v, default=str)
            lines.append(f"{k}: {v}")
        return "\n".join(lines)
    return str(data)


def _to_rows(data: Any) -> list[dict]:
    """Normalize data to list of dicts for tabular formatting."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return data
        return [{"value": item} for item in data]
    if isinstance(data, dict):
        return [data]
    return []
