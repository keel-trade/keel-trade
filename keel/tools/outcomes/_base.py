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

import itertools
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
        deliberate "this is private until you publish" signal.

        `url_line` (spec 06 R2/R4): every result that carries a
        canonical URL also carries it as one plain text line, so every
        surface — widget or not — can show a clickable link without
        parsing the envelope. Handlers with no hero/share URL may set
        their own `url_line` via `extra` (e.g. a preview whose only
        link is a handoff URL)."""
        envelope: dict = {"share_url": self.share_url}
        if self.run_id is not None:
            envelope["run_id"] = self.run_id
        if self.hero_url is not None:
            envelope["hero_url"] = self.hero_url
        _canonical_url = self.hero_url or self.share_url
        if _canonical_url is not None:
            envelope["url_line"] = f"View in Keel: {_canonical_url}"
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


#: The parameters a listed registration OMITS from its input schema (R-8,
#: agent-surface-cleanup specs 01 §2.5 and 03 §2.8) — dotted paths, a
#: nested one reached through `properties`. The hosted listed server is
#: file-free and carries no live-write tools, so these are parameters a
#: listed caller can never use (`source_file`, write-through) or that
#: read as a scare number with no effect on sizing (`config.leverage`, the
#: account margin cap; `config.initial_capital`, the deprecated alias of
#: `init_cash`). Everything else is copy-only: names, types, enums,
#: defaults and `required` match the shared schema, save the one declared
#: exception below (`LISTED_REQUIRED_ADDITIONS`)
#: (tests/test_server_profiles.py reads THIS table).
#: What a strategy name is (Q-1897, R4): an agent titled a strategy
#: "Zach $5k — Momentum + Funding", putting the user's first name into a
#: name shown on every list and share. One sentence, appended to the `name`
#: parameter of every tool that names a strategy (compose, fork, library
#: fork), so the three never drift.
STRATEGY_NAME_FACT = (
    "It names the strategy — its market, thesis or method (e.g. `HYPE MACD "
    "trend`) — and is shown wherever the strategy is listed; the user's own "
    "name or account belongs to the account, not to a strategy name."
)


#: 2026-10-01 (Q-2080): `commit_id` leaves keel_backtest_run's listed schema
#: (`version` is the one selector there and takes a commit id too);
#: `target_workspace_id` leaves keel_strategy_fork's (the share-link route
#: never reads it, and the hosted server has no workspace); `role` leaves
#: keel_strategy_notes_add's (a connector's note is the agent's);
#: `permission` leaves keel_share_create's (derived from `include_source`).
LISTED_SCHEMA_OMISSIONS: dict[str, frozenset[str]] = {
    "keel_strategy_compose": frozenset({"source_file"}),
    "keel_backtest_run": frozenset(
        {"auto_push", "push_message", "commit_id", "config.leverage", "config.initial_capital"}
    ),
    "keel_strategy_fork": frozenset({"target_workspace_id"}),
    "keel_strategy_notes_add": frozenset({"role"}),
}


#: Parameters the listed schema REQUIRES although the shared schema does not
#: (Q-2268, round 3) — the one narrow exception to "`required` matches the
#: shared schema". It exists for exactly one shape: the shared schema leaves
#: a parameter optional because an ALTERNATIVE can stand in for it, and the
#: listed schema omits that alternative (`LISTED_SCHEMA_OMISSIONS`), so on
#: the listed surface the parameter is required in fact — the handler
#: refuses a call without it (`strategy_compose._read_source`,
#: `missing_input`). `parameter → the omitted alternative`; the profile
#: test holds every row to that shape (the alternative is a declared
#: omission of the same tool, the parameter is a property of both schemas).
LISTED_REQUIRED_ADDITIONS: dict[str, dict[str, str]] = {
    "keel_strategy_compose": {"source": "source_file"},
}


#: Enum VALUES the listed schema spells differently (Q-2080, 2026-10-01):
#: `(tool, parameter) → {shared value: listed value}`. OpenAI's scan reads an
#: enum value as copy, and `orders` / `trades` / `funding` read as actions on
#: a tool that only reads them. The handler accepts both spellings
#: (`live_monitor.canonical_view`); the shared schema (CLI `--view`) keeps the
#: short names. `listed_schema` applies the map; tests/test_server_profiles.py
#: holds the two enums equal under it.
LISTED_ENUM_RENAMES: dict[tuple[str, str], dict[str, str]] = {
    ("keel_live_monitor", "view"): {
        "orders": "order_history",
        "trades": "trade_history",
        "funding": "funding_payments",
    },
}


def listed_enum(tool_name: str, param: str, values: list) -> list:
    """``values`` spelled as the listed schema spells them."""
    renames = LISTED_ENUM_RENAMES.get((tool_name, param), {})
    return [renames.get(v, v) for v in values]


def listed_ignored_arguments(tool_name: str) -> frozenset[str]:
    """Top-level arguments the listed schema OMITS but a listed call ACCEPTS
    and silently drops (review 2, lane A).

    ChatGPT freezes a connector's catalog at connect time, so a connector
    made before the omissions shipped still sends `auto_push` /
    `push_message` / `source_file`. Refusing them as unexpected would break
    every such connector; honouring them is impossible on a file-free
    server. Dropping them keeps the call working and the published schema
    clean. Nested omissions (`config.leverage`, `config.initial_capital`)
    are not here: `config` is one argument and the backtest handler reads
    those keys as it always has.
    """
    return frozenset(p for p in LISTED_SCHEMA_OMISSIONS.get(tool_name, ()) if "." not in p)


def listed_schema(
    tool_name: str,
    schema: dict,
    *,
    descriptions: dict[str, str] | None = None,
) -> dict:
    """The listed profile's input schema for one tool — ONE helper (R-8).

    ``schema`` minus the tool's :data:`LISTED_SCHEMA_OMISSIONS`, with any
    parameter description in ``descriptions`` (dotted path → text) replaced
    by its listed copy. Built at import time beside the shared schema; there
    is no profile-aware schema builder anywhere else.
    """
    out = deepcopy(schema)
    for path in sorted(LISTED_SCHEMA_OMISSIONS.get(tool_name, ())):
        node = out
        parts = path.split(".")
        for part in parts[:-1]:
            node = node["properties"][part]
        node["properties"].pop(parts[-1], None)
        if isinstance(node.get("required"), list) and parts[-1] in node["required"]:
            node["required"] = [r for r in node["required"] if r != parts[-1]]
    for param in LISTED_REQUIRED_ADDITIONS.get(tool_name, {}):
        required = list(out.get("required") or [])
        if param not in required:
            out["required"] = [*required, param]
    for path, text in (descriptions or {}).items():
        node = out
        for part in path.split("."):
            node = node["properties"][part]
        node["description"] = text
    for (name, param), _renames in LISTED_ENUM_RENAMES.items():
        if name != tool_name or param not in out.get("properties", {}):
            continue
        prop = out["properties"][param]
        if isinstance(prop.get("enum"), list):
            prop["enum"] = listed_enum(tool_name, param, prop["enum"])
    return out


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
    # MCP description — names the neighbour to use instead. Listed tools state
    # it as a fact ("Forking is `keel_strategy_fork`."), never an imperative
    # "Do NOT use… — call" (Q-1804; tests/test_policy_scan.py IMPERATIVE_RE).
    description: str
    input_schema: dict  # JSON Schema; drives both MCP inputSchema and Click options
    annotations: dict  # MCP annotations: readOnlyHint, destructiveHint, ...
    handler: Callable[[dict, ToolContext], OutcomeResult]
    required_action: str = ""  # platform_auth action string — empty → "read" by default
    cli_options_override: list = field(default_factory=list)
    confirm_in_cli: bool = False  # destructive tools: prompt unless --yes
    mcp_only: bool = False  # skip CLI registration (use when CLI surface is hand-rolled elsewhere)
    # Tool only makes sense on the user's own machine (local workspace
    # checkouts under ~/.keel, browser-based login, config-file writes).
    # Hosted servers (KEEL_EXECUTION_MODE=hosted) never register these —
    # enforced by `_toolsets.is_tool_loaded`. Spec 01 R2.
    local_only: bool = False
    # ── Listed-profile copy overrides (spec 01 R3, research/08) ──────
    # The directory-listed registration carries policy-vetted copy: no
    # deploy/fund/trade verbs anywhere in titles, descriptions, or
    # parameter descriptions, and no routing to tools absent from the
    # listed surface. When set, these replace the shared fields ONLY
    # under KEEL_SERVER_PROFILE=listed (tests/test_policy_scan.py is
    # the gate). Tool behavior, arg names, and required fields must
    # stay identical across profiles — overrides are copy, not contract.
    listed_description: str | None = None
    listed_input_schema: dict | None = None
    listed_title: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_schema", normalize_input_schema(self.input_schema))
        if self.listed_input_schema is not None:
            object.__setattr__(
                self,
                "listed_input_schema",
                normalize_input_schema(self.listed_input_schema),
            )
        # The declared bounds are enforced wherever the handler is invoked
        # (Q-2270): CLI, MCP and a direct call all refuse an argument outside
        # a `minimum` / `maximum` / `maxLength` / `maxItems` the schema
        # declares, instead of a handler clamping it or keel-api 422ing it.
        from ._declared_bounds import wrap_handler

        object.__setattr__(self, "handler", wrap_handler(self, self.handler))


# Sentinel set of all toolset names (parsing helper).
ALL_TOOLSETS: frozenset[str] = frozenset(
    {"always", "read-only", "backtest", "share", "live-read", "live-write", "live"}
)


# ── `present` — how much of the result to show now (BUILD §2.4) ────────
#
# One shared declaration for the four tools that take it
# (`keel_strategy_compose`, `keel_strategy_fork`, `keel_backtest_run`,
# `keel_backtest_watch`), so the enum and the copy cannot drift apart.
# The argument is `present`, not `size`: `size` is a forbidden listed
# parameter token (tests/test_policy_scan.py FORBIDDEN_PARAM_TOKENS),
# which is also why the VALUES are words rather than dimensions.

PRESENT_VALUES: tuple[str, ...] = ("receipt", "view")

#: Q-1745: the user's own words map onto the two values ("show me it",
#: "at full size" → `view`; "one line", "just the short version" →
#: `receipt`). Without that mapping the value was only ever the default on
#: claude.ai — the one host that drops server instructions, so this
#: parameter description is the only place the mapping can live. It says
#: "in full or enlarged", not "full size": `size` is a forbidden listed
#: parameter token (the policy scan), in descriptions as in names.
#:
#: Q-1748: it describes what each VALUE renders and states THIS tool's own
#: default — nothing else. The earlier shared copy ended "dry runs and single
#: runs are receipts; a create, a fork or a save is a view": a sentence that
#: sorts calls of OTHER tools into categories, which ChatGPT's approval gate
#: read as "tool documentation prescribes how the classifier should treat
#: strategy forking and related tool usage" and flagged keel_strategy_fork
#: as a Suspicious Instruction. A parameter description says what the
#: parameter does on the tool that carries it.
PRESENT_DESCRIPTION = (
    "How this result displays in the chat — presentation only, no effect on "
    "what the tool does. `view`: the full card, for showing the result in full or "
    "enlarged. `receipt`: a compact one-line summary row, for the one-line or short "
    "version."
)


def present_param_schema(default: str) -> dict[str, Any]:
    """The `present` JSON-Schema property for one tool, naming ITS default.

    ``default`` is the plain-language default of the tool that carries it
    (e.g. "`receipt`", or "`receipt` for a dry run, `view` for a save").
    """
    return {
        "type": "string",
        "enum": list(PRESENT_VALUES),
        "description": f"{PRESENT_DESCRIPTION} Omitted: {default}.",
    }


def present_choice(args: dict) -> str | None:
    """The caller's `present`, or None for "take the default".

    Anything that is not one of the two enum values is None: the adapter
    validates the enum, and a handler called directly (CLI, a test, the
    SDK) must not be able to smuggle a third size in.
    """
    value = args.get("present")
    return value if isinstance(value, str) and value in PRESENT_VALUES else None


# ── The supersession election key (BUILD §2.9, Q-1689) ────────────────
#
# Every `view` carries `object` / `at` / `seq`. The widgets announce the
# triple on the `keel-cards` BroadcastChannel and a card that hears a
# YOUNGER sibling for the same `object` re-renders as its receipt — the
# documented Claude mechanism (`connectors/building/mcp-apps/
# instance-supersession`), which is the only thing that can collapse the
# five cards an agent that saves five times in one turn leaves behind.
#
# Server-minted, always: the doc is explicit that a client `Date.now()`
# mis-orders lazily mounted cells on reopen, so a widget MUST NOT stamp
# its own instant. `seq` breaks ties inside one process at one
# millisecond; `at` orders across processes.

_ELECTION_SEQ = itertools.count(1)


def election_key(object_id: str | None) -> dict[str, Any]:
    """`{object?, at, seq}` for one freshly built view.

    `object` is the thing the card is ABOUT — the strategy id, the
    backtest id, or `compare:<strategy_id>` — and is omitted when there
    is none (an anonymous dry run has nothing to supersede and nothing
    can supersede it).
    """
    key: dict[str, Any] = {}
    if isinstance(object_id, str) and object_id:
        key["object"] = object_id
    key["at"] = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    key["seq"] = next(_ELECTION_SEQ)
    return key
