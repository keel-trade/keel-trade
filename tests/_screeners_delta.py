"""Explicit spec 06 full-profile delta; no listed/paper baseline is re-pinned."""

SCREENER_ADDITIONS = frozenset(
    {
        "keel_blocks_get",
        "keel_blocks_search",
        "keel_screener_action",
        "keel_screener_copy",
        "keel_screener_describe",
        "keel_screener_follow",
        "keel_screener_follow_view_set",
        "keel_screener_list",
        "keel_screener_passing",
        "keel_screener_publish",
        "keel_screener_refresh",
        "keel_screener_run",
        "keel_screener_unpublish",
        "keel_screener_view",
        "keel_screener_view_set",
        "keel_use_detach",
        "keel_use_preview",
        "keel_use_upgrade",
        "keel_view_describe",
    }
)
SCREENER_CARD_TOOLS = frozenset(
    {"keel_screener_run", "keel_screener_view", "keel_screener_view_set"}
)

#: The screen commands that are CLI-only (screeners L13 §2b): registered as
#: CLI commands, absent from every MCP ``tools/list``.
SCREENER_CLI_ONLY = frozenset(
    {
        "keel_screener_passing",
        "keel_screener_describe",
        "keel_screener_follow_view_set",
        "keel_screener_copy",
        "keel_view_describe",
    }
)
#: The screen tools an MCP ``tools/list`` on the full profile carries.
SCREENER_MCP_ADDITIONS = SCREENER_ADDITIONS - SCREENER_CLI_ONLY
