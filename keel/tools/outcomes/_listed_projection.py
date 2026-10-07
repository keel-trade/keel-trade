"""What a LISTED result keeps of keel-api's rows (Q-2268, round 3).

OpenAI's app review asks a directory connector to "remove … telemetry/internal
identifiers (… internal account IDs …)" from what the model receives. keel-api
rows carry storage and tenancy plumbing — the org id, an exchange-account id,
a config id, storage keys, content hashes, the component lock — beside the
facts a user asked for. On the listed profile each tool that passes such a
row through returns an ALLOW-LISTED projection of it instead; the CLI and the
local server keep the whole row (their callers script against it).

Allow-lists rather than deny-lists wherever the row has a schema: a field
keel-api adds tomorrow stays off the listed surface until someone names it
here. The one deny-list (:data:`LISTED_INTERNAL_KEYS`) is for the live views
whose payloads vary by view; it is applied recursively and only on listed.

Every list here was checked against what the cards read
(`keel/widgets/assets/card-*.js`): a field a card draws is kept.
"""

from __future__ import annotations

from typing import Any, Iterable


#: `keel_strategy_get.metadata` on listed — the strategy's own facts, its
#: version, its evidence summary and whether it is running. Not kept: the
#: org id, storage key, source / lock / library hashes, the component lock,
#: the deployment id and schedule, the library import id and the in-app
#: journey projection. `metadata.graph` and `metadata.source` are not kept
#: either — `view` carries the structure and `include_source` the source.
LISTED_STRATEGY_METADATA_FIELDS: tuple[str, ...] = (
    "strategy_id",
    "id",
    "name",
    "strategy_name",
    "description",
    "status",
    "visibility",
    "tags",
    "fork_count",
    "forked_from_name",
    "library_entry_name",
    "library_source_slug",
    "created_at",
    "updated_at",
    "current_sequence",
    "head_commit_id",
    "compilation_error",
    "validation",
    # The pinned components that are deprecated, derived by keel-api from the
    # HEAD lock (position-layer spec 04-R24): component, version, known issue
    # and replacement text — agent context for the upgrade (D-29), no hash.
    "deprecations",
    "deployed_sequence",
    "deployed_version_string",
    "deployed_at",
    "backtest_state",
    "backtest_run_count",
    # The strategy card's evidence fallback (`evidenceFromMeta`).
    "latest_backtest_id",
    "latest_backtest_metrics",
    "latest_backtest_sequence",
    "latest_backtest_start_date",
    "latest_backtest_end_date",
    "latest_backtest_sub_window",
)

#: One version row on listed (`keel_strategy_get include_versions=true`,
#: `keel_strategy_history`): the same facts the history tool returns —
#: never keel-api's `meta` blob, the source hash or the raw client /
#: auth-surface provenance (the rendered `modified_via` sentence carries it).
LISTED_VERSION_FIELDS: tuple[str, ...] = (
    "sequence_number",
    "commit_id",
    "parent_id",
    "message",
    "created_at",
    "tags",
    "modified_via",
)

#: `keel_strategy_get include_source=true` on listed: the text and which
#: version it is — not its source hash or component lock.
LISTED_SOURCE_FIELDS: tuple[str, ...] = ("source", "sequence_number", "commit_id")

#: One note row on listed (`keel_strategy_notes_read`): keel-api's
#: `StrategyMemoryItem` minus the conversation id.
LISTED_NOTE_FIELDS: tuple[str, ...] = (
    "memory_id",
    "id",
    "strategy_id",
    "memory_type",
    "content",
    "note",
    "written_by_role",
    "role",
    "created_at",
)

#: One Library variant on listed (`keel_library_get`): keel-api's
#: `LibraryVariantSummary`.
LISTED_LIBRARY_VARIANT_FIELDS: tuple[str, ...] = (
    "variant_id",
    "label",
    "name",
    "params",
    "metrics",
    "is_default",
    "publishable",
    "forkable",
    "failure_reason",
    "equity_curve",
)

#: One deployment row on listed (`keel_live_monitor` overview, and each row
#: of the portfolio summary): keel-api's `DeploymentResponse` /
#: `DeploymentSummary` minus the org id, exchange-account id, config id,
#: storage key, source hash and the raw tranching block. Every field the
#: live card draws is here (`card-live.js`: name, status, version, schedule,
#: execution style, pause provenance, P&L, position count, warnings).
LISTED_DEPLOYMENT_FIELDS: tuple[str, ...] = (
    "deployment_id",
    "id",
    "strategy_id",
    "name",
    "strategy_name",
    "status",
    "schedule",
    "deployed_at",
    "stopped_at",
    "created_at",
    "updated_at",
    "deployed_commit_id",
    "deployed_version_string",
    "total_pnl",
    "position_count",
    "universe_warnings",
    "account_warnings",
    "execution_style",
    "paused_reason",
    "paused_at",
    "money_freshness",
)

#: Keys no listed live result carries at any depth: tenancy, exchange-account
#: and storage identifiers, content hashes, request telemetry, contact and
#: credential material. The live views' payloads differ per view (orders,
#: trades, funding, executions …), so these are removed recursively.
LISTED_INTERNAL_KEYS: frozenset[str] = frozenset(
    {
        "org_id",
        "owner_id",
        "created_by",
        "principal_id",
        "user_id",
        "account_id",
        "config_id",
        "source_hash",
        "head_source_hash",
        "lock_hash",
        "component_lock",
        "s3_key",
        "artifact_s3_key",
        "storage_key",
        "session_id",
        "request_id",
        "trace_id",
        "internal_url",
        "email",
        "posthog_distinct_id",
        "wallet_address",
        "agent_address",
        "api_key",
        "source_conversation_id",
        "conversation_id",
        "client_name",
        "auth_surface",
        "upgrade_url",
        "manage_url",
        "tranching",
    }
)


def pick(row: Any, fields: Iterable[str]) -> Any:
    """`row` reduced to `fields`, in their order; a non-dict passes through."""
    if not isinstance(row, dict):
        return row
    return {k: row[k] for k in fields if k in row}


def scrub(value: Any, keys: frozenset[str] = LISTED_INTERNAL_KEYS) -> Any:
    """`value` with every dict key in `keys` removed, at every depth."""
    if isinstance(value, dict):
        return {k: scrub(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [scrub(v, keys) for v in value]
    return value


def listed_strategy_metadata(meta: Any) -> Any:
    """`keel_strategy_get.metadata` on listed — plus `is_live`, the one fact
    the dropped `deployment_id` carried for the card (its Live chip)."""
    if not isinstance(meta, dict):
        return meta
    out = pick(meta, LISTED_STRATEGY_METADATA_FIELDS)
    out["is_live"] = bool(meta.get("deployment_id"))
    return out


def listed_live_data(view: str, data: Any) -> Any:
    """One `keel_live_monitor` payload on listed: a deployment row is
    allow-listed (the overview, and each portfolio row); everything else is
    scrubbed of :data:`LISTED_INTERNAL_KEYS`."""
    if view == "overview" and isinstance(data, dict):
        return pick(data, LISTED_DEPLOYMENT_FIELDS)
    out = scrub(data)
    if view == "portfolio" and isinstance(out, dict):
        rows = out.get("deployments")
        if isinstance(rows, list):
            out["deployments"] = [pick(r, LISTED_DEPLOYMENT_FIELDS) for r in rows]
    return out


__all__ = [
    "LISTED_DEPLOYMENT_FIELDS",
    "LISTED_INTERNAL_KEYS",
    "LISTED_LIBRARY_VARIANT_FIELDS",
    "LISTED_NOTE_FIELDS",
    "LISTED_SOURCE_FIELDS",
    "LISTED_STRATEGY_METADATA_FIELDS",
    "LISTED_VERSION_FIELDS",
    "listed_live_data",
    "listed_strategy_metadata",
    "pick",
    "scrub",
]
