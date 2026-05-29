"""`keel_help` — fetch a knowledge doc by topic.

Per spec §13.6: hosts that don't browse MCP resources well can hit
this tool to retrieve the same content that backs registered knowledge
and DSL reference resources.

For 0.3.0 the bundled markdown in `keel/data/reference/` and
`keel/data/knowledge/` is still readable. Phase 2C migrates the
storage to a keel-api endpoint; this handler will fall back to API
when bundled files are gone.
"""

from __future__ import annotations

from importlib import resources

from keel.errors import KeelError

from . import register
from ._base import OutcomeResult, OutcomeTool, ToolContext


_BUNDLED_KNOWLEDGE_DIRS = ("knowledge", "reference", "patterns")


def _try_bundled(topic: str) -> tuple[str, str] | None:
    for subdir in _BUNDLED_KNOWLEDGE_DIRS:
        try:
            ref = resources.files("keel.data").joinpath(subdir).joinpath(f"{topic}.md")
            if ref.is_file():
                return ref.read_text(encoding="utf-8"), subdir
        except (FileNotFoundError, ModuleNotFoundError):
            continue
    return None


def _bundled_resource_uri(topic: str, subdir: str) -> str | None:
    if subdir == "reference":
        return f"keel://dsl/reference/{topic}"
    if subdir == "knowledge":
        return f"keel://knowledge/{topic}"
    return None


def _handler(args: dict, ctx: ToolContext) -> OutcomeResult:
    topic = args.get("topic", "").strip()
    if not topic:
        raise KeelError(
            "Missing required `topic` argument.",
            error_code="missing_topic",
            suggestion="Pass --topic <name>, e.g. --topic phases.",
        )

    # 1. Bundled fallback (works without auth).
    bundled = _try_bundled(topic)
    if bundled is not None:
        body, subdir = bundled
        return OutcomeResult(
            run_id=None,
            hero_url=None,
            share_url=None,
            resource_uri=_bundled_resource_uri(topic, subdir),
            extra={"topic": topic, "body": body, "source": "bundled"},
        )

    # 2. API fallback (when an endpoint exists in Phase 2C+; today this
    #    surfaces a clean "not found" with the available topics).
    try:
        client = ctx.get_client()
        body = client.get(f"/v1/reference/{topic}")
        return OutcomeResult(
            run_id=None,
            hero_url=None,
            share_url=None,
            resource_uri=f"keel://dsl/reference/{topic}",
            extra={"topic": topic, "body": body, "source": "api"},
        )
    except Exception:
        # Surface the list of bundled topics so the agent can self-correct.
        available = sorted(_list_bundled_topics())
        raise KeelError(
            f"Help topic not found: {topic!r}.",
            error_code="not_found",
            exit_code=3,
            suggestion=f"Known topics: {', '.join(available[:20])}",
        )


def _list_bundled_topics() -> list[str]:
    topics: list[str] = []
    for subdir in _BUNDLED_KNOWLEDGE_DIRS:
        try:
            dir_ref = resources.files("keel.data").joinpath(subdir)
            if dir_ref.is_dir():
                for entry in dir_ref.iterdir():
                    name = entry.name
                    if name.endswith(".md"):
                        topics.append(name[:-3])
        except (FileNotFoundError, ModuleNotFoundError):
            continue
    return topics


HELP = register(
    OutcomeTool(
        name="keel_help",
        required_action="audit.read",
        cli_path=("help",),
        toolset="always",
        description=(
            "Fetch a Keel knowledge document by topic name. Used when a host "
            "doesn't browse MCP resources well. Reference topics mirror "
            "`keel://dsl/reference/<topic>`; knowledge topics mirror "
            "`keel://knowledge/<section>`. "
            "Do NOT use to search components — call `keel_components_search`. "
            "Do NOT use to look up a specific component's params — call "
            "`keel_components_compose_help`."
        ),
        input_schema={
            "type": "object",
            "required": ["topic"],
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Topic slug. Examples: `phases`, `types`, `slots`, "
                        "`composition`, `normalization`, `best_practices`."
                    ),
                },
            },
        },
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        handler=_handler,
    )
)
