"""Shared infrastructure for outcome tools — CLI and MCP both bind here.

Per `projects/agent-v2/03-ideal-experience-spec.md` §4 + §12 + §13:

- One outcome-tool surface across CLI and MCP (the table in §4 is the
  canonical inventory).
- Standard return shape with authenticated `hero_url` by default and
  `share_url=None` until explicit `keel_share_create`.
- Standard error envelope (§13.5 mandatory 5-field shape).
- `KEEL_TOOLSETS` env scopes which tools load at MCP startup.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


Toolset = Literal[
    "always",
    "read-only",
    "backtest",
    "share",
    "live-read",
    "live-write",
    "live",  # deprecated KEEL_TOOLSETS alias, retained for config compatibility
]


@dataclass
class ToolContext:
    """Per-call context passed to every outcome handler.

    Handlers stay decoupled from "am I being called from CLI or MCP" —
    they read `is_tty` to know whether to lean human (CLI in a terminal)
    or structured (everywhere else), but otherwise behave identically.
    """

    api_client: Any | None = None
    workspace: Any | None = None
    is_tty: bool = False
    toolsets: frozenset[str] = field(default_factory=frozenset)
    dry_run: bool = False
    app_url: str = "https://app.usekeel.io"
    share_url_root: str = "https://usekeel.io/share"

    def get_client(self):
        """Lazily construct a KeelClient if one wasn't supplied."""
        if self.api_client is None:
            from keel.client import KeelClient

            self.api_client = KeelClient()
        return self.api_client


@dataclass
class OutcomeResult:
    """Standard return shape across every outcome tool.

    Per spec §5: `hero_url` is the authenticated app link by default;
    `share_url` is `None` until the user explicitly calls
    `keel_share_create`. `resource_uri` points at a `keel://...` resource
    when one is available (lazy fetch, no startup token cost).
    """

    run_id: str | None = None
    hero_url: str | None = None
    share_url: str | None = None
    summary_metrics: dict | None = None
    resource_uri: str | None = None
    extra: dict = field(default_factory=dict)

    def to_envelope(self) -> dict:
        """Serialize to the wire envelope. Drops None fields except
        `share_url`, which stays explicit (`null`) so callers see the
        deliberate "this is private until you publish" signal."""
        envelope: dict = {"share_url": self.share_url}
        if self.run_id is not None:
            envelope["run_id"] = self.run_id
        if self.hero_url is not None:
            envelope["hero_url"] = self.hero_url
        if self.summary_metrics is not None:
            envelope["summary_metrics"] = self.summary_metrics
        if self.resource_uri is not None:
            envelope["resource_uri"] = self.resource_uri
        for k, v in self.extra.items():
            if k in envelope:
                continue  # never let extra clobber a contractual field
            envelope[k] = v
        return envelope


def envelope_error(
    code: str,
    message: str,
    what_was_expected: str,
    example: dict,
    suggested_next_action: dict,
) -> dict:
    """Spec §13.5 mandatory 5-field error envelope.

    Every outcome handler emits errors through this helper so the agent
    surface stays predictable. The legacy `KeelError.to_dict()` shape
    is kept on the existing exception classes for backward compat; new
    handlers go through this envelope.
    """
    return {
        "code": code,
        "message": message,
        "what_was_expected": what_was_expected,
        "example": example,
        "suggested_next_action": suggested_next_action,
    }


def normalize_input_schema(schema: dict) -> dict:
    """Return a top-level strict copy of an outcome input schema.

    Outcome schemas are the contract shared by CLI docs, MCP tools/list,
    and tests. Keep the top-level object closed so agents get a clear
    list of accepted argument names. Nested pass-through objects remain
    intentionally untouched until the individual tool models them.
    """
    normalized = deepcopy(schema)
    if normalized.get("type") == "object":
        normalized.setdefault("properties", {})
        normalized.setdefault("required", [])
        normalized["additionalProperties"] = False
    return normalized


@dataclass(frozen=True)
class OutcomeTool:
    """Declarative definition of one outcome tool.

    The CLI adapter renders this as a Click command; the MCP adapter
    registers it as a FastMCP tool. Same args, same returns, same
    destructive-action gating.

    The ``required_action`` field is the ``platform_auth.actions``
    string the hosted MCP server gates against (e.g. ``"backtest.create"``).
    Declaring it on the outcome means a new tool in a future SDK
    release picks up the gate automatically — the mcp-server doesn't
    need a parallel edit. Spec §7 line 945.
    """

    name: str  # MCP name, e.g. "keel_backtest_run"
    cli_path: tuple[str, ...]  # CLI command path, e.g. ("backtest", "run")
    toolset: Toolset
    description: str  # MCP description — MUST include a "Don't use to..." clause
    input_schema: dict  # JSON Schema; drives both MCP inputSchema and Click options
    annotations: dict  # MCP annotations: readOnlyHint, destructiveHint, ...
    handler: Callable[[dict, ToolContext], OutcomeResult]
    required_action: str = ""  # platform_auth action string — empty → "read" by default
    cli_options_override: list = field(default_factory=list)
    confirm_in_cli: bool = False  # destructive tools: prompt unless --yes
    mcp_only: bool = False  # skip CLI registration (use when CLI surface is hand-rolled elsewhere)

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_schema", normalize_input_schema(self.input_schema))


# Sentinel set of all toolset names (parsing helper).
ALL_TOOLSETS: frozenset[str] = frozenset(
    {"always", "read-only", "backtest", "share", "live-read", "live-write", "live"}
)
