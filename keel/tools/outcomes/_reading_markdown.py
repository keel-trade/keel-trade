"""Reading material as markdown (agent-surface-cleanup spec 02 §2.2).

Under the non-view probe arm (`_channels.NONVIEW_TEXT_ENV`, default OFF) a
non-view tool returns ONE text block and no `structuredContent` (R5). For the
four READING tools that block is markdown rather than a JSON envelope —
markdown key/value outscores JSON for model reading (proposals §3):

* `keel_help` — the document body itself after one line `topic: <slug>`;
  with no topic, the topic list with one line each;
* `keel_components_search` / `keel_components_get_many` /
  `keel_components_get` — one `### Name · category` heading per
  component, then `in → out`, the description and the parameter table.

These tools carry the discovery half of NL → DSL (LANES.md bar #1), so the
rule is LOSSLESS: every field of the envelope reaches the text — the ones
rendered as prose, and any other as a trailing `details:` JSON line. A
renderer never drops a fact the JSON form carried.
"""

from __future__ import annotations

import json
from typing import Any

from ._param_range import RANGE_KEYS, hard_limit, typical_range


__all__ = ["READING_RENDERERS", "render_component", "render_help"]

#: Envelope keys every result carries that are links, not reading material.
_LINK_KEYS = ("share_url", "hero_url", "url_line", "resource_uri", "run_id")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _details(envelope: dict, used: set[str]) -> str | None:
    rest = {
        k: v
        for k, v in envelope.items()
        if k not in used and k not in _LINK_KEYS and v not in (None, [], {}, "")
    }
    return f"details: {json.dumps(rest, default=str)}" if rest else None


def _links(envelope: dict) -> list[str]:
    line = envelope.get("url_line")
    return [str(line)] if isinstance(line, str) and line else []


_COMPONENT_PROSE = {
    "name",
    "category",
    "sub_category",
    "description",
    "input_type",
    "output_type",
    "parameters",
    "usage_hint",
    "param_constraints",
    "examples",
    "pitfalls",
    "clock",
    "version",
    "latest",
    "status",
    "deterministic",
    "range_note",
}

#: Parameter keys rendered as their own columns; everything else lands in
#: the trailing ``constraints`` cell. ``range`` is the labelled form of the
#: constraints' range keys, which the two range columns already show.
_PARAM_COLUMNS = ("name", "type", "default", "required", "description", "range")


def _param_extra(p: dict) -> dict:
    """The trailing-cell blob: every key not shown in a column. The range keys
    of ``constraints`` (Q-2242) move to the ``hard limit`` / ``typical
    (guidance)`` columns; the rest of ``constraints`` (step, options) stays."""
    extra: dict = {}
    for k, v in p.items():
        if k in _PARAM_COLUMNS or v in (None, [], {}, ""):
            continue
        # The internal type tree repeats the `type` column, and a false
        # slot flag says nothing (Q-2273 L7: the raw JSON in this cell was
        # most of each parameter row).
        if k == "type_structure" or (k == "slot_reference" and v is False):
            continue
        if k == "constraints" and isinstance(v, dict):
            v = {ck: cv for ck, cv in v.items() if ck not in RANGE_KEYS}
            if not v:
                continue
        extra[k] = v
    return extra


def render_component(entry: dict, *, detail: bool = True) -> str:
    """One component as markdown — heading, types, description, parameters."""
    if "error" in entry and "name" not in entry:
        return f"- error: {entry.get('error')}" + (
            f" ({entry['suggestion']})" if entry.get("suggestion") else ""
        )
    category = "/".join(str(c) for c in (entry.get("category"), entry.get("sub_category")) if c)
    lines = [f"### {entry.get('name')}" + (f" · {category}" if category else "")]
    types = f"`{entry.get('input_type')}` → `{entry.get('output_type')}`"
    lines.append(types)
    if entry.get("description"):
        lines.append(str(entry["description"]).strip())
    description_words = " ".join(str(entry.get("description") or "").split())
    usage = " ".join(str(entry.get("usage_hint") or "").split())
    # The usage hint is usually the description's own first paragraph, and
    # an example its own "Example:" line — printed twice per component
    # (Q-2273 L7). Each is shown only when the description lacks it.
    if usage and usage not in description_words:
        lines.append(f"Usage: {usage}")
    meta = []
    for key in ("version", "latest", "status", "deterministic"):
        if entry.get(key) is not None:
            meta.append(f"{key} {entry[key]}")
    if meta:
        lines.append(" · ".join(meta))
    params = entry.get("parameters")
    if isinstance(params, list) and params:
        rows = [p for p in params if isinstance(p, dict)]
        lines.append("")
        if any(hard_limit(p.get("constraints")) for p in rows):
            # Q-2242: say once what the two range columns mean, so the typical
            # range is never read as a limit.
            lines.append(
                "hard limit = the only range the validator rejects; typical (guidance) = "
                "where values usually sit, and a value outside it is valid."
            )
            lines.append("")
        lines.append(
            "| parameter | type | default | required | hard limit | typical (guidance) "
            "| description | constraints |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for p in rows:
            constraints = p.get("constraints")
            hard = hard_limit(constraints, inf="∞") or ""
            typical = typical_range(constraints, inf="∞") or ""
            extra = _param_extra(p)
            lines.append(
                f"| `{p.get('name')}` | {_cell(p.get('type'))} | {_cell(p.get('default'))} | "
                f"{'yes' if p.get('required') else 'no'} | {hard} | {typical} | "
                f"{_cell(p.get('description'))} | {_cell(extra) if extra else ''} |"
            )
    for key, title in (("param_constraints", "Constraints"), ("pitfalls", "Pitfalls")):
        items = entry.get(key)
        if isinstance(items, list) and items:
            lines.append("")
            lines.append(f"{title}:")
            lines.extend(f"- {_cell(item)}" for item in items)
    examples = [
        e
        for e in (entry.get("examples") or [])
        if not (isinstance(e, str) and " ".join(e.split()) in description_words)
    ]
    if examples:
        lines.append("")
        lines.append("Examples:")
        for example in examples:
            text = example if isinstance(example, str) else json.dumps(example, default=str)
            lines.append("```python")
            lines.append(text.strip("\n"))
            lines.append("```")
    clock = entry.get("clock")
    if isinstance(clock, dict) and clock:
        lines.append("")
        lines.append("Clock:")
        lines.extend(f"- {k}: {_cell(v)}" for k, v in clock.items())
    if detail:
        rest = _details(entry, _COMPONENT_PROSE | {"error", "error_code", "suggestion"})
        if rest:
            lines.append(rest)
    return "\n".join(lines)


def _render_search(envelope: dict) -> str:
    results = envelope.get("results") or []
    lines = [
        f"{envelope.get('total', len(results))} components"
        + (f" (limit {envelope['limit']})" if envelope.get("limit") is not None else "")
    ]
    for entry in results:
        if isinstance(entry, dict):
            lines.append("")
            lines.append(render_component(entry))
    rest = _details(envelope, {"results", "total", "limit"})
    if rest:
        lines.append(rest)
    lines.extend(_links(envelope))
    return "\n".join(lines) + "\n"


def _render_batch(envelope: dict) -> str:
    components = envelope.get("components") or {}
    omitted = envelope.get("omitted") or 0
    lines = [
        f"{envelope.get('found', 0)} found, {envelope.get('missing', 0)} missing"
        + (f", {omitted} left out for size" if omitted else "")
        + f" of {len(envelope.get('names_requested') or components)} requested"
    ]
    for name, entry in components.items():
        lines.append("")
        if isinstance(entry, dict):
            if "error" in entry and "name" not in entry:
                lines.append(f"### {name}")
            lines.append(render_component(entry))
    rest = _details(envelope, {"components", "found", "missing", "omitted", "names_requested"})
    if rest:
        lines.append(rest)
    lines.extend(_links(envelope))
    return "\n".join(lines) + "\n"


def _render_compose_help(envelope: dict) -> str:
    return render_component(envelope) + "\n" + "\n".join(_links(envelope)) + "\n"


def render_help(envelope: dict) -> str | None:
    """`topic: <slug>` then the document body; with no topic, the index."""
    body = envelope.get("body") or envelope.get("content")
    topic = envelope.get("topic") or envelope.get("name")
    if isinstance(body, str) and body.strip():
        lines = []
        if topic:
            lines.append(f"topic: {topic}")
            lines.append("")
        lines.append(body.rstrip("\n"))
        rest = _details(envelope, {"body", "content", "topic", "name"})
        if rest:
            lines.append("")
            lines.append(rest)
        return "\n".join(lines) + "\n"
    index = envelope.get("topic_index")
    if isinstance(index, list) and index:
        lines = []
        if envelope.get("info"):
            lines.append(str(envelope["info"]))
            lines.append("")
        for row in index:
            if isinstance(row, dict):
                summary = row.get("summary")
                lines.append(f"- `{row.get('topic')}`" + (f" — {summary}" if summary else ""))
        rest = _details(envelope, {"topic_index", "topics", "info"})
        if rest:
            lines.append("")
            lines.append(rest)
        return "\n".join(lines) + "\n"
    return None


READING_RENDERERS: dict[str, Any] = {
    "keel_help": render_help,
    "keel_components_search": _render_search,
    "keel_components_get_many": _render_batch,
    "keel_components_get": _render_compose_help,
}
