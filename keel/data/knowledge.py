"""System knowledge from bundled markdown files.

The wheel carries only the SHARED layer of the corpus (mcp-conversion 05
§3, R-L2): `scripts/build_data.py` vendors the files `LAYERS.yaml` declares
`reference` or `sectioned`, and never anything under a `chat/` directory.
Keel's in-app chat prompt (its always-on set and its in-app companions) is
assembled by chat-api from the monorepo, not from this package — which is
why there is no `load_system_knowledge()` here any more: the chat's
always-on slots include files this wheel deliberately does not ship.
"""

from __future__ import annotations

from importlib import resources


def load_section(name: str) -> str:
    """Load a single knowledge section by filename (without .md extension)."""
    ref = resources.files("keel.data").joinpath("knowledge", f"{name}.md")
    try:
        return ref.read_text()
    except FileNotFoundError:
        available = [
            f.name.removesuffix(".md")
            for f in resources.files("keel.data").joinpath("knowledge").iterdir()
            if f.name.endswith(".md")
        ]
        raise FileNotFoundError(f"Unknown section '{name}'. Available: {available}")


#: Sections whose FILE is not what an MCP surface serves. `operating_core.md`
#: also carries Keel's chat opinion layer (`register: opinion`), which no MCP
#: surface serves (agent-surface-cleanup spec 01 §2.1.1, R-5): its served form
#: is the corpus builder's base document.
_BASE_DOCUMENT_SECTIONS = frozenset({"operating_core"})


def active_profile() -> str:
    """The corpus builder's profile name for THIS server (`PROFILE_TAGS` key).

    The same three-way split the instructions use (`keel.mcp.server`):
    listed, else hosted, else local. Both inputs raise on an invalid value
    rather than defaulting to the wider surface.
    """
    from keel.hosting import is_hosted
    from keel.tools.outcomes._toolsets import is_listed_profile

    if is_listed_profile():
        return "listed"
    return "full-hosted" if is_hosted() else "full-local"


def served_section(name: str) -> str:
    """A knowledge section as an MCP surface serves it — the ONE owner for
    both `keel_help topic=<name>` and the `keel://knowledge/{section}`
    resource, so the two cannot disagree about what leaves the process.

    Every section is its file verbatim except `operating_core`, which is
    `assemble.base_document()` (base register only), filtered to the
    sections whose `profiles:` admit this server's profile (mcp-conversion
    05 §3.3). The byte-identity pin between the libs source and the vendored
    FILE stays on `load_operating_core()`.
    """
    if name in _BASE_DOCUMENT_SECTIONS:
        from pipeline_engine.reference.system import assemble

        return assemble.base_document(profile=active_profile())
    return load_section(name)


def load_operating_core() -> str:
    """The lean, always-on MCP operating core (bundled copy).

    Reads the bundled ``operating_core.md`` — the byte-identical copy that
    ``scripts/build_data.py`` ships from
    ``libs/pipeline_engine/reference/system/operating_core.md``. This is the
    thin-context distillation the MCP server embeds in its ``instructions=``
    string.
    """
    return load_section("operating_core")
