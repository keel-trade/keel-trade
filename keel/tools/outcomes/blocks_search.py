"""Find reusable output blocks by role, terms, family or type. Inspect one with blocks get."""

# grounded-in: reference/screener.md; reference/view_language.md;
# system/tool_usage.md — stored evidence, exact preview pins, separate views.

import json

from ._screener_commands import S, lines_result, register_command, route


REQUIRED_ACTION = "strategy.read"
_SEARCH = route("get", "/v1/blocks")


def _handler(args, ctx):
    """07 §4.10 (A3 4.10, WL-AG-17): the search's text names its population ("Showing 3 of 12 outputs") and each
    output by name, role and the call a study names it by, ``Output("str_…@n", output="…")``; the route's JSON
    stays the structured answer."""
    extra = _SEARCH(args, ctx).extra
    items = [i for i in extra.get("items") or [] if isinstance(i, dict)]
    total = int(extra.get("total") or len(items))
    lines = [f"Showing {len(items)} of {total} output{'s' if total != 1 else ''}"]
    for item in items:
        call = f"Output({json.dumps(item.get('ref'))}, output={json.dumps(item.get('output'))})"
        role = f" · {item['role']}" if item.get("role") else ""
        lines.append(f"- {item.get('name')} · {item.get('output')}{role} · {call}")
    return lines_result(extra, lines)


TOOL = register_command(
    "blocks_search",
    ("blocks", "search"),
    "Find reusable output blocks by role, terms, family or type. Inspect one with blocks get.",
    {
        "q": {**S, "x-cli-positional": True},
        "role": S,
        "families": {"type": "array", "items": S},
        "output_type": S,
        "scope": {"type": "string", "enum": ["library", "mine", "public"]},
    },
    [],
    REQUIRED_ACTION,
    _handler,
    read=True,
    idempotent=True,
    title="Search Output Blocks",
)
