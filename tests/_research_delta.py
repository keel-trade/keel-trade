"""Research's registry delta (spec 07 §2.2's table): the eight MCP tools and the
CLI-only verbs, written from the spec, never read back from the registry, so a
test that compares them to OUTCOMES can fail (AT-1)."""

from __future__ import annotations


#: The eight research MCP tools (07 §2.2; listed: plan, run, get).
RESEARCH_MCP_ADDITIONS = frozenset(
    {
        "keel_research_plan",
        "keel_research_run",
        "keel_research_get",
        "keel_research_view_set",
        "keel_research_brief",
        "keel_research_note",
        "keel_research_cancel",
        "keel_research_make_signal",
    }
)

#: The CLI-only verbs (07 §2.2's "CLI" rows), one OutcomeTool each. AT-1's
#: thirteen rows count set-aside with restore, archive and delete as one row;
#: they are four commands, so four tools.
RESEARCH_CLI_ONLY = frozenset(
    {
        "keel_research_add",
        "keel_research_save",
        "keel_research_run_draft",
        "keel_research_watch",
        "keel_research_data",
        "keel_research_list",
        "keel_research_resume",
        "keel_research_retry",
        "keel_research_steps",
        "keel_research_copy",
        "keel_research_project",
        "keel_research_set_aside",
        "keel_research_restore",
        "keel_research_archive",
        "keel_research_delete",
        "keel_research_shares",
    }
)

RESEARCH_ADDITIONS = RESEARCH_MCP_ADDITIONS | RESEARCH_CLI_ONLY

#: The research view tools (07 §2.2: view, markdown) and the three that draw
#: the study card (07 §3.3).
RESEARCH_VIEW_TOOLS = frozenset(
    {
        "keel_research_plan",
        "keel_research_run",
        "keel_research_get",
        "keel_research_view_set",
        "keel_research_brief",
    }
)
#: D-328 D1 (WL-AG-11): the plan draws the one-row plan card.
RESEARCH_CARD_TOOLS = frozenset(
    {"keel_research_plan", "keel_research_run", "keel_research_get", "keel_research_view_set"}
)
