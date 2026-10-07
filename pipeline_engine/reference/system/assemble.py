"""The corpus builder — one source, three assemblies (guidance spec §4).

`operating_core.md` beside this module is the SOURCE. It is a sequence of
front-mattered sections; this module parses them and assembles:

* the MCP server instructions, per profile (`instructions()`);
* the 512-character head every surface starts with (`head()`), and its
  chat rendering through one vocabulary map (`chat_head()`);
* the two REGISTERS (agent-surface-cleanup spec 01 §2.1): the served base
  document, every `register: base` section in full (`base_document()` —
  what `keel_help topic=operating_core` returns), and the chat's opinion
  layer, every `register: opinion` section (`chat_layer()` — Keel's own
  chat prompt, nowhere else);
* the declared always-on / pull split for Keel's own chat
  (`declared_always_on()`, `declared_pull()`);
* the rule-id owner map and the sole-carrier anchor table, plus the pure
  verdict functions the guards run (`rule_owner_violations()`,
  `missing_anchors()`, `staleness_violations()`, `directive_violations()`,
  `description_size_report()`, `missing_index_entries()`).

**Stdlib only, by contract.** `scripts/build_data.py` vendors this file
into the `keel-trade` wheel exactly as it vendors `dsl/catalog.py`, and
`assert_vendored_bundle_self_contained()` imports the vendored tree with
`libs/` off `sys.path`. A third-party import here ships a wheel that dies
at first use. chat-api imports it from `libs/`; the SDK imports the
vendored copy — one builder, two callers (spec §3 "Own chat", P5).

Section format
--------------

A section is a front-matter block followed by a body. Blocks are
delimited by lines that are exactly ``---`` (the shape prettier
round-trips: a blank line before the closing fence, so the key lines are
never read as a setext heading)::

    ---

    id: quota
    layer: body
    surfaces: [mcp, chat, docs]
    profiles: [listed, full]      # optional; absent = every profile
    when: live_write_absent       # optional emission condition
    register: base                # REQUIRED: base | opinion
    rules: [R-QUOTA]

    ---

    Runs and compute are metered ... (terse, emitted into the instructions)

    Metered units: ... (long form, served by keel_help topic=operating_core)

One rule, stated once and mechanically checked by the head/size guards:
**`layer: body` emits its FIRST PARAGRAPH into the instructions.** Every
paragraph of every section is available to the other assemblies. `layer:
head` is the head; `layer: skill` and `layer: reference` are never
emitted into instructions at all (they are pull-tier text, which is why a
sentence that lives in one of them still has a sole-carrier twin).

A second rule, enforced at load: **`register` is required and has no
default.** `base` is a capability, a contract or a fact — true and useful
printed in a reference manual with no reader in mind; it is served to
every MCP profile, to the base document and to the chat prompt's head.
`opinion` is a method, a gate, a posture or a tone; it is served ONLY to
Keel's chat prompt through `chat_layer()`. A missing or unknown register
raises, and so does an always-on MCP section (`layer: head|body` with
`mcp` in `surfaces`) in the opinion register — opinion has no always-on
MCP slot, so the assembler cannot emit it by accident.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence


_SYSTEM_DIR = Path(__file__).parent

#: Where `operating_core.md` lives, in the two trees this module runs in.
#: Both entries are the SAME BYTES — `build_data.py` copies the libs file
#: into the wheel and `tests/test_operating_core.py` proves byte identity —
#: so this is a location lookup, not a semantic fallback. Neither present
#: is a loud error, never a synthesised default.
_CORE_CANDIDATES = (
    # monorepo: beside this file
    _SYSTEM_DIR / "operating_core.md",
    # vendored wheel: <sdk_root>/pipeline_engine/reference/system/assemble.py
    # → <sdk_root>/keel/data/knowledge/operating_core.md
    _SYSTEM_DIR.parents[2] / "keel" / "data" / "knowledge" / "operating_core.md",
)


def core_path() -> Path:
    """The corpus source file, or a loud error naming the rebuild."""
    for candidate in _CORE_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "operating_core.md not found beside assemble.py or in the bundled "
        f"knowledge directory (tried {[str(c) for c in _CORE_CANDIDATES]}). "
        "Re-run: PYTHONPATH=libs python "
        "packages/keel-trade/keel-sdk/scripts/build_data.py"
    )


# ── The layer manifest (mcp-conversion 05 §3, R-L1 / L5) ────────────────
#
# Every `.md` under `libs/pipeline_engine/reference/` declares its LAYER in
# ONE manifest, `reference/LAYERS.yaml` — never in front matter, which would
# leak into the chat prompt and into the served bodies. There is no default:
# a file the manifest does not name is a build failure, the way spec 01 made
# a missing `register` a `load_sections` error.
#
# * `reference` — shared: vendored into the SDK wheel, served over MCP, and
#   loaded by Keel's chat.
# * `opinion` — in-app only: lives under a `chat/` directory, loaded by
#   Keel's chat right after the shared file whose name it shares, and never
#   vendored or served.
# * `sectioned` — `system/operating_core.md` alone: its register is
#   declared per SECTION (the spec 01 mechanism below).

#: The three layer values, in the manifest's vocabulary.
LAYER_VALUES = frozenset({"reference", "opinion", "sectioned"})

#: The layers the SDK wheel vendors and MCP serves (R-L2).
SERVED_LAYERS = frozenset({"reference", "sectioned"})

#: The one `sectioned` file (05 §3.3).
SECTIONED_PATH = "system/operating_core.md"

#: The directory name that marks an in-app-only file, at any depth.
CHAT_DIR = "chat"

_LAYERS_FILE = "LAYERS.yaml"
_LAYERS_VERSION = "1"
_MANIFEST_KEY_RE = re.compile(r"^([A-Za-z0-9_./-]+\.md):\s*([a-z]+)\s*$")


def reference_root() -> Path:
    """`libs/pipeline_engine/reference/` — the tree the manifest describes."""
    return _SYSTEM_DIR.parent


def layers_path() -> Path:
    """The manifest, or a loud error.

    The manifest is a MONOREPO file: the wheel ships only the files it
    names as served, so a vendored `assemble.py` has no manifest to read and
    no caller there needs one. Absent is never a synthesised default.
    """
    path = reference_root() / _LAYERS_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"{_LAYERS_FILE} not found at {path}. The layer manifest lives in the "
            "monorepo (libs/pipeline_engine/reference/); the SDK wheel does not "
            "carry it and does not need it."
        )
    return path


def parse_layers(text: str) -> dict[str, str]:
    """Parse the manifest's one flat shape, strictly.

    ::

        version: 1
        files:
          system/composition_mechanics.md: reference

    Blank lines and whole-line ``#`` comments are allowed anywhere. Every
    other line must be exactly one of those three shapes (entries indented
    by two spaces), keys must be unique ``.md`` paths, and values must be a
    `LAYER_VALUES` member. Anything else raises: a leniently-skipped line is
    a file whose layer silently changed.
    """
    lines = [
        (n, ln.rstrip("\n"))
        for n, ln in enumerate(text.splitlines(), start=1)
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if len(lines) < 2:
        raise ValueError(f"{_LAYERS_FILE}: expected 'version: 1' and 'files:'")
    (n0, first), (n1, second) = lines[0], lines[1]
    if first != f"version: {_LAYERS_VERSION}":
        raise ValueError(
            f"{_LAYERS_FILE}:{n0}: expected 'version: {_LAYERS_VERSION}', got {first!r}"
        )
    if second != "files:":
        raise ValueError(f"{_LAYERS_FILE}:{n1}: expected 'files:', got {second!r}")
    out: dict[str, str] = {}
    for n, ln in lines[2:]:
        if not ln.startswith("  ") or ln.startswith("   "):
            raise ValueError(
                f"{_LAYERS_FILE}:{n}: an entry is indented by exactly two spaces: {ln!r}"
            )
        m = _MANIFEST_KEY_RE.match(ln[2:])
        if not m:
            raise ValueError(f"{_LAYERS_FILE}:{n}: not a '<path>.md: <layer>' entry: {ln!r}")
        path, value = m.group(1), m.group(2)
        if value not in LAYER_VALUES:
            raise ValueError(
                f"{_LAYERS_FILE}:{n}: {path} has layer {value!r}; valid: {sorted(LAYER_VALUES)} "
                "(there is no default)"
            )
        if path in out:
            raise ValueError(f"{_LAYERS_FILE}:{n}: {path} is listed twice")
        out[path] = value
    if not out:
        raise ValueError(f"{_LAYERS_FILE}: 'files:' lists nothing")
    return out


@lru_cache(maxsize=1)
def load_layers() -> Mapping[str, str]:
    """The manifest, parsed: `{path relative to reference/: layer}`."""
    return parse_layers(layers_path().read_text())


def is_chat_path(path: str) -> bool:
    """Whether a manifest path sits under a `chat/` directory (any depth)."""
    return CHAT_DIR in Path(path).parts[:-1]


def expected_layer(path: str) -> str:
    """The one layer a path may declare (05 §3 invariants)."""
    if path == SECTIONED_PATH:
        return "sectioned"
    return "opinion" if is_chat_path(path) else "reference"


def layer_violations(
    root: Path | None = None,
    layers: Mapping[str, str] | None = None,
) -> list[str]:
    """Every breach of the manifest's invariants (guard G1), or ``[]``.

    * every `.md` under `root` is listed, and every listed path exists;
    * every value is a `LAYER_VALUES` member;
    * a path under a `chat/` directory is `opinion`, any other path is
      `reference`, and `system/operating_core.md` alone is `sectioned`.
    """
    base = root if root is not None else reference_root()
    declared = dict(layers) if layers is not None else dict(load_layers())
    on_disk = {p.relative_to(base).as_posix() for p in base.rglob("*.md")}
    out: list[str] = []
    for path in sorted(on_disk - set(declared)):
        out.append(f"{path}: not listed in {_LAYERS_FILE} (every corpus file declares its layer)")
    for path in sorted(set(declared) - on_disk):
        out.append(f"{path}: listed in {_LAYERS_FILE} but does not exist")
    for path, value in sorted(declared.items()):
        if value not in LAYER_VALUES:
            out.append(f"{path}: unknown layer {value!r}")
            continue
        want = expected_layer(path)
        if value != want:
            out.append(f"{path}: declared {value!r}, but a path here must be {want!r}")
    return out


def served_paths(layers: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The manifest paths the wheel vendors and MCP serves (R-L2), sorted."""
    declared = layers if layers is not None else load_layers()
    return tuple(sorted(p for p, v in declared.items() if v in SERVED_LAYERS))


def chat_only_paths(layers: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The manifest paths only Keel's chat loads (never vendored), sorted."""
    declared = layers if layers is not None else load_layers()
    return tuple(sorted(p for p, v in declared.items() if v == "opinion"))


__all__ = [
    "AIM_DESCRIPTION_CHARS",
    "AIM_LISTED_TOTAL_CHARS",
    "CHAT_VOCABULARY",
    "DESCRIPTION_MAX_BYTES",
    "DIRECTIVE_PATTERNS",
    "DESCRIPTION_CLASSES",
    "HEAD_FACTS",
    "HEAD_MAX_CHARS",
    "IMPERATIVE_RE",
    "INSTRUCTIONS_MAX_BYTES",
    "LISTED_INSTRUCTIONS_MAX_CHARS",
    "LISTED_TOTAL_CEILING_CHARS",
    "MIN_DESCRIPTION_CHARS",
    "PROFILE_TAGS",
    "REGISTERS",
    "REGISTER_WORD_RE",
    "RULE_OWNERS",
    "SOLE_CARRIER",
    "STALENESS_ACKNOWLEDGED",
    "UNVERIFIED",
    "Anchor",
    "CHAT_DIR",
    "HeadFact",
    "LAYER_VALUES",
    "RuleOwner",
    "SECTIONED_PATH",
    "SERVED_LAYERS",
    "Section",
    "base_document",
    "base_sections",
    "chat_head",
    "chat_layer",
    "chat_only_paths",
    "core_twin_violations",
    "declared_always_on",
    "declared_pull",
    "description_shape_violations",
    "description_size_report",
    "directive_violations",
    "earliest_data_violations",
    "expected_layer",
    "forward_reference_violations",
    "head",
    "head_fact_positions",
    "instructions",
    "is_chat_path",
    "layer_violations",
    "layers_path",
    "load_layers",
    "load_sections",
    "parse_layers",
    "reference_root",
    "served_paths",
    "missing_anchors",
    "missing_index_entries",
    "register_violations",
    "rule_owner_violations",
    "section",
    "staleness_violations",
    "unknown_tool_violations",
]


# ── Host limits (HARD) and aims (REPORTED) ──────────────────────────────
#
# Founder ruling 2026-09-22 (spec §2, decision #38): only a cited host
# limit is a guard. Everything else is an aim — measured and printed,
# never asserted.

#: Claude Code truncates a tool description at 2 KB. "2 KB" is BYTES.
DESCRIPTION_MAX_BYTES = 2048
#: Claude Code truncates server instructions at the same 2 KB.
INSTRUCTIONS_MAX_BYTES = 2048
#: OpenAI: ChatGPT and Codex read the first 512 CHARACTERS first, so the
#: head must be self-contained inside them.
HEAD_MAX_CHARS = 512

#: AIM — a description with no skeleton or table of contents to carry.
AIM_DESCRIPTION_CHARS = 1200
#: AIM — the listed profile's total description budget (spec §2 L1).
AIM_LISTED_TOTAL_CHARS = 22_000
#: AIM — Agent Skills' "< 5,000 tokens on activation".
AIM_SKILL_TOKENS = 5000

# ── Project ceilings (ASSERTED, cited) ──────────────────────────────────
#
# Not host limits: founder-ratified ceilings of the agent-surface-cleanup
# project (GOAL.md "Done when", 2026-09-23). Asserted by the guards that
# cite them, and named here so the number has one home.

#: The listed instructions string, in CHARACTERS (spec 01 §2.2).
LISTED_INSTRUCTIONS_MAX_CHARS = 1000
#: The listed profile's description TOTAL, in characters (spec 01 §2.5).
LISTED_TOTAL_CEILING_CHARS = 13_000
#: A description shorter than this is not describing the tool (spec 01 §4 #1).
MIN_DESCRIPTION_CHARS = 200


# ── Section parsing ─────────────────────────────────────────────────────

_FENCE = "---"
_KEY_RE = re.compile(r"^([a-z_]+):\s*(.*)$")
_LIST_RE = re.compile(r"^\[(.*)\]$")

PROFILE_TAGS: Mapping[str, frozenset[str]] = {
    # profile name → the tags a section's `profiles:` may name to be in it
    "listed": frozenset({"listed"}),
    "full-hosted": frozenset({"full", "hosted"}),
    "full-local": frozenset({"full", "local"}),
}


@dataclass(frozen=True)
class Section:
    """One front-mattered section of `operating_core.md`."""

    id: str
    layer: str
    surfaces: tuple[str, ...]
    body: str
    profiles: tuple[str, ...] = ()
    when: str | None = None
    rules: tuple[str, ...] = ()
    #: `base` or `opinion` (module docstring). The default exists only so a
    #: test can build a synthetic Section; the PARSER requires the key.
    register: str = "base"

    @property
    def paragraphs(self) -> tuple[str, ...]:
        return tuple(p.strip() for p in self.body.split("\n\n") if p.strip())

    @property
    def terse(self) -> str:
        """The first paragraph — what `layer: body` emits into instructions."""
        paras = self.paragraphs
        if not paras:
            raise ValueError(f"section '{self.id}' has an empty body")
        return paras[0]


_VALID_LAYERS = frozenset({"head", "body", "skill", "reference"})

#: The two registers (spec 01 §2.1). No default: a silently-defaulted
#: register is a sentence emitted on the wrong surface.
REGISTERS = frozenset({"base", "opinion"})


def _parse_front_matter(chunk: str) -> dict[str, object] | None:
    """Return the parsed keys, or None when `chunk` is not front matter.

    Front matter is a chunk whose every non-blank line is ``key: value``
    and whose first key is ``id``. Anything else is a body. There is no
    lenient path: a chunk that begins with ``id:`` and then carries a
    non-key line raises, because a silently-dropped key is a section
    quietly emitted on the wrong profile.
    """
    lines = [ln for ln in chunk.splitlines() if ln.strip()]
    if not lines:
        return None
    first = _KEY_RE.match(lines[0])
    if not first or first.group(1) != "id":
        return None
    out: dict[str, object] = {}
    for ln in lines:
        m = _KEY_RE.match(ln)
        if not m:
            raise ValueError(
                f"operating_core.md front matter for '{out.get('id')}' has a non-key line: {ln!r}"
            )
        key, raw = m.group(1), m.group(2).strip()
        lst = _LIST_RE.match(raw)
        if lst:
            out[key] = tuple(v.strip() for v in lst.group(1).split(",") if v.strip())
        else:
            out[key] = raw
    return out


@lru_cache(maxsize=1)
def load_sections(path: str | None = None) -> tuple[Section, ...]:
    """Parse `operating_core.md` into its ordered sections."""
    src = Path(path) if path else core_path()
    text = src.read_text()
    # Everything before the first fence is the file's own preamble (an HTML
    # comment naming this module as the only reader). Dropping it here is
    # what keeps the file from opening with a YAML front-matter block —
    # prettier reformats THOSE, and a reflowed `rules:` list would change
    # what the builder parses.
    parts = re.split(r"(?m)^---\s*$", text, maxsplit=1)
    text = parts[1] if len(parts) == 2 else text
    chunks = re.split(r"(?m)^---\s*$", text)
    sections: list[Section] = []
    i = 0
    while i < len(chunks):
        meta = _parse_front_matter(chunks[i])
        if meta is None:
            if chunks[i].strip():
                raise ValueError(
                    f"operating_core.md: text outside a section at chunk {i}: "
                    f"{chunks[i].strip()[:80]!r}"
                )
            i += 1
            continue
        if i + 1 >= len(chunks):
            raise ValueError(f"operating_core.md: section '{meta['id']}' has no body")
        body = chunks[i + 1].strip("\n")
        layer = str(meta.get("layer", ""))
        if layer not in _VALID_LAYERS:
            raise ValueError(
                f"operating_core.md: section '{meta['id']}' has layer {layer!r}; "
                f"valid: {sorted(_VALID_LAYERS)}"
            )
        register = str(meta.get("register", ""))
        if register not in REGISTERS:
            raise ValueError(
                f"operating_core.md: section '{meta['id']}' has register {register!r}; "
                f"valid: {sorted(REGISTERS)} (there is no default)"
            )
        surfaces = tuple(meta.get("surfaces") or ())  # type: ignore[arg-type]
        if register == "opinion" and layer in ("head", "body") and "mcp" in surfaces:
            raise ValueError(
                f"operating_core.md: section '{meta['id']}' is `layer: {layer}` on the "
                "mcp surface in the opinion register — opinion has no always-on MCP "
                "slot; make it `layer: skill` with `surfaces: [chat]`, or state the fact "
                "in the base register"
            )
        sections.append(
            Section(
                id=str(meta["id"]),
                layer=layer,
                surfaces=surfaces,
                profiles=tuple(meta.get("profiles") or ()),  # type: ignore[arg-type]
                when=str(meta["when"]) if meta.get("when") else None,
                rules=tuple(meta.get("rules") or ()),  # type: ignore[arg-type]
                body=body,
                register=register,
            )
        )
        i += 2
    if not sections:
        raise ValueError("operating_core.md parsed to zero sections")
    return tuple(sections)


def section(section_id: str) -> Section:
    for s in load_sections():
        if s.id == section_id:
            return s
    raise KeyError(f"no section '{section_id}' in operating_core.md")


# ── The head ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HeadFact:
    """One of the five facts the head must carry inside 512 characters."""

    id: str
    #: A phrase that appears VERBATIM in the rendered head.
    anchor: str


#: agent-surface-cleanup spec 01 §2.2 — the facts, in the order they
#: appear. Fact 3 is scoped to what a result actually names (review 06
#: M-5 #4: the `config:` line is the strategy's declarations — no result
#: names the cost model), and fact 4 is reply conduct in the base register,
#: ratified as proposals §7 row 12 — recorded so nobody "fixes" it.
#: The sixth fact, `Method: keel_help(topic="skill:strategy-creation")`,
#: left the head (mcp-conversion 05 §3.2, R-L3: nothing we serve tells the
#: model to fetch guidance); the chat keeps its rendering of that sentence
#: as the opinion section `chat-route-method`.
HEAD_FACTS: tuple[HeadFact, ...] = (
    HeadFact("category", "use it for crypto strategy, backtest or portfolio tasks"),
    HeadFact("status-first", "keel_account_status reports identity, quota and capabilities"),
    HeadFact("defaults-stated", "a run's result names its window and declarations"),
    HeadFact("explain-not-repeat", "explain it rather than repeat it"),
    HeadFact(
        "summarize-or-compare",
        "One run: keel_backtest_summarize; several: keel_backtest_compare once",
    ),
)

#: Template parity (decision #29): the chat head is the SAME sentences
#: with the chat's own tool vocabulary. Substitutions are applied
#: longest-key-first so no key is a prefix of another's match. The last
#: four entries do not occur in the head; they stay because other
#: renderings use them, and a dead entry is harmless to the parity guard
#: (its `applied` set is computed from the head). `chat_layer()` is NOT
#: rendered through this map — it is written in the chat's vocabulary.
CHAT_VOCABULARY: Mapping[str, str] = {
    "keel_account_status reports identity, quota and capabilities when a task needs them.": (
        "The per-turn context carries identity, plan and quota."
    ),
    # Fact 4 has no chat twin: no chat tool result carries a `view`, and the
    # editor renders the strategy and the run beside the conversation. The
    # head is a TEMPLATE, so the chat states its own mechanism (decision #29).
    "When a result carries `view`, the card the host draws already shows names, ids and links — "
    "explain it rather than repeat it.": (
        "The editor renders the strategy and the run beside this chat; "
        "explain what they show in your own words."
    ),
    "One run: keel_backtest_summarize; several: keel_backtest_compare once.": (
        "One run: read its results; several: compare them once."
    ),
    "keel_components_get_many": "strategy_component_detail_batch",
    "keel_components_search": "strategy_components_search",
    "keel_strategy_compose": "update_strategy",
    "keel_backtest_run": "run_backtest",
}


def head() -> str:
    """The MCP head — `layer: head`, one paragraph, ≤ 512 characters."""
    return section("head").terse


def chat_head() -> str:
    """The head rendered in the chat's vocabulary (template parity)."""
    return render_vocabulary(head())


def render_vocabulary(text: str) -> str:
    for key in sorted(CHAT_VOCABULARY, key=len, reverse=True):
        text = text.replace(key, CHAT_VOCABULARY[key])
    return text


def head_fact_positions(text: str | None = None) -> dict[str, int]:
    """Map each head fact id → the index where its anchor begins (-1 if absent)."""
    rendered = head() if text is None else text
    return {f.id: rendered.find(f.anchor) for f in HEAD_FACTS}


# ── Instructions assembly ───────────────────────────────────────────────


def _included(sec: Section, profile: str, *, live_write_loaded: bool) -> bool:
    if "mcp" not in sec.surfaces:
        return False
    if sec.layer not in ("head", "body"):
        return False
    # Belt and braces: `load_sections` already refuses an always-on MCP
    # section in the opinion register; this keeps a synthetic one out too.
    if sec.register != "base":
        return False
    tags = PROFILE_TAGS[profile]
    if sec.profiles and not (set(sec.profiles) & tags):
        return False
    if sec.when == "live_write_absent" and live_write_loaded:
        return False
    return True


def instructions(
    profile: str = "listed",
    *,
    live_write_loaded: bool = False,
    include_ids: bool = False,
) -> str:
    """Assemble the server instructions for one profile.

    `profile` is a key of `PROFILE_TAGS` (`listed`, `full-hosted`,
    `full-local`). Each included section contributes its FIRST paragraph;
    blocks join with a blank line.
    """
    if profile not in PROFILE_TAGS:
        raise ValueError(f"unknown profile {profile!r}; valid: {sorted(PROFILE_TAGS)}")
    blocks = [
        s.terse
        for s in load_sections()
        if _included(s, profile, live_write_loaded=live_write_loaded)
    ]
    if include_ids:  # pragma: no cover - debugging aid
        return "\n\n".join(
            f"[{s.id}] {s.terse}"
            for s in load_sections()
            if _included(s, profile, live_write_loaded=live_write_loaded)
        )
    return "\n\n".join(blocks)


def instruction_section_ids(profile: str, *, live_write_loaded: bool = False) -> tuple[str, ...]:
    return tuple(
        s.id for s in load_sections() if _included(s, profile, live_write_loaded=live_write_loaded)
    )


# ── The two registers (spec 01 §2.1, §2.4) ──────────────────────────────


def base_sections(sections: Sequence[Section] | None = None) -> tuple[Section, ...]:
    """Every `register: base` section, in file order."""
    secs = tuple(sections) if sections is not None else load_sections()
    return tuple(s for s in secs if s.register == "base")


def _in_profile(sec: Section, profile: str) -> bool:
    """Whether a section's `profiles:` admit `profile` (absent = every one)."""
    return not sec.profiles or bool(set(sec.profiles) & PROFILE_TAGS[profile])


def base_document(sections: Sequence[Section] | None = None, *, profile: str | None = None) -> str:
    """The SERVED form of the corpus (spec 01 §2.1.1, R-5).

    Every `register: base` section, in file order, front matter stripped,
    the FULL body (every paragraph), joined by one blank line. This is what
    `keel_help topic=operating_core` returns — never the raw file, which
    also carries the chat's opinion layer.

    `profile` (a `PROFILE_TAGS` key) filters by each section's `profiles:`
    exactly as `instructions()` does (mcp-conversion 05 §3.3), so the listed
    profile is never served the full-only AUTH, write-through STATE, LIVE
    or `KEEL_TOOLSETS` sections. ``None`` is the whole base register, for a
    caller that is not serving one profile; an unknown profile raises.
    """
    if profile is not None and profile not in PROFILE_TAGS:
        raise ValueError(f"unknown profile {profile!r}; valid: {sorted(PROFILE_TAGS)}")
    return "\n\n".join(
        s.body.strip()
        for s in base_sections(sections)
        if profile is None or _in_profile(s, profile)
    )


def chat_layer(sections: Sequence[Section] | None = None) -> str:
    """Keel's own chat's opinion layer (spec 01 §2.4).

    Every `register: opinion` section whose `surfaces` names `chat`, in
    file order, joined by one blank line. It is written in the chat's own
    tool vocabulary and is emitted nowhere else — not into any MCP
    instructions string, not into the base document.
    """
    secs = tuple(sections) if sections is not None else load_sections()
    return "\n\n".join(
        s.body.strip() for s in secs if s.register == "opinion" and "chat" in s.surfaces
    )


# ── The chat's always-on / pull split (W2 §6; the guard reads THIS) ─────

#: Eleven knowledge files Keel's own chat keeps always-on (86,779 B).
_ALWAYS_ON: tuple[str, ...] = (
    "pipeline_system",
    "strategy_paths",
    "strategy_patterns",
    "collaboration",
    "mistakes",
    "trading_domain",
    "composition_mechanics",
    "universe_selection",
    "reasoning_principles",
    "tool_usage",
    "dsl_syntax",
)

#: Six that move behind the chat's `dsl_reference` / knowledge tool and
#: the route modules (43,734 B — 33.5 % of the always-on knowledge).
_PULL: tuple[str, ...] = (
    "capability_boundaries",
    "strategy_phases",
    "platform-operations",
    "costs_and_fees",
    "editor_ui",
    "component_versioning",
)


def declared_always_on() -> tuple[str, ...]:
    """The knowledge sections the chat's static prompt carries always-on.

    The chat's parity guard reads ITS file set from here rather than from
    any live list, so a file added to one side and not the other reds
    (spec §5 `chat parity`).
    """
    return _ALWAYS_ON


def declared_pull() -> tuple[str, ...]:
    """The sections that move to route-, state- or tool-pull."""
    return _PULL


# ── Rule ids: one owner each ────────────────────────────────────────────


@dataclass(frozen=True)
class RuleOwner:
    """A rule id, its one owner, and the canonical sentence."""

    rule: str
    #: `core:<section-id>`, `tool:<name>`, `param:<tool>.<name>` or
    #: `rule:<CODE>` (a validator code — valid only while ARMED, R-30).
    owner: str
    canonical: str


#: agent-surface-cleanup spec 01 §2.6, as amended by review 06. One owner
#: per rule. `core:` owners are checked verbatim against their section
#: body; `tool:`/`param:` owners against the live description strings the
#: caller supplies; `rule:` owners against the validator catalog the caller
#: supplies — a `rule:` owner resolves ONLY while its code is at WARNING or
#: ERROR on the tree being built (R-30): a dormant rule is no carrier, so
#: its prose keeps the fact. For a `rule:` owner the canonical is the code.
RULE_OWNERS: tuple[RuleOwner, ...] = (
    RuleOwner("R-CATEGORY", "core:head", "Keel builds crypto strategies on Hyperliquid"),
    RuleOwner(
        "R-STATUS-FIRST",
        "core:head",
        "keel_account_status reports identity, quota and capabilities",
    ),
    RuleOwner(
        "R-DEFAULTS-STATED",
        "core:head",
        "Omitted arguments take platform defaults; a run's result names its window and "
        "declarations.",
    ),
    RuleOwner("R-EXPLAIN-NOT-REPEAT", "core:head", "explain it rather than repeat it"),
    RuleOwner(
        "R-SUMMARIZE-OR-COMPARE",
        "core:head",
        "One run: keel_backtest_summarize; several: keel_backtest_compare once.",
    ),
    RuleOwner(
        "R-ROUTE",
        "core:sequence",
        "keel_components_search → keel_components_get_many → "
        "keel_strategy_compose(dry_run=true) until clean → save → keel_backtest_run",
    ),
    # The chat's opinion layer owns the method rules (spec 01 §2.4).
    RuleOwner(
        "R-TWO-STEP",
        "core:chat-method",
        "strategy_component_detail_batch the whole set before update_strategy",
    ),
    RuleOwner("R-NO-HANDROLL", "core:chat-method", "search for it rather than hand-rolling it"),
    RuleOwner("R-ITERATE", "core:chat-method", "Edits are the smallest change, one at a time"),
    RuleOwner(
        "R-DEFAULTS-NOT-ASK", "core:chat-method", "Use sensible platform defaults and keep moving"
    ),
    RuleOwner(
        "R-ONE-QUESTION",
        "core:chat-method",
        "ask one question only on real architecture ambiguity",
    ),
    RuleOwner("R-MECHANISM", "core:chat-method", "the mechanism, not the outcome"),
    # The composition facts moved to the validator (Q-1947, 2026-09-25):
    # ChatGPT's approval gate read compose's "required composition rules"
    # sentences as the tool prescribing classifier behaviour. Each rule is
    # armed (`TERMINAL_NOT_WEIGHTS` WARNING, `MISSING_UNIVERSE` and
    # `DICT_NOT_CONSUMED` ERROR) and has an accept fixture of its suggested
    # shape in the conformance corpus (review 06 §3.2 #3), so each owns its
    # fact (R-30); the example skeleton still shows all three.
    RuleOwner("R-WEIGHTSERIES", "rule:TERMINAL_NOT_WEIGHTS", "TERMINAL_NOT_WEIGHTS"),
    RuleOwner("R-UNIVERSE-REQUIRED", "rule:MISSING_UNIVERSE", "MISSING_UNIVERSE"),
    RuleOwner("R-PARALLEL-CONSUMED", "rule:DICT_NOT_CONSUMED", "DICT_NOT_CONSUMED"),
    # Review 06 M-1: the old "normalize after WeightConcatenator, not per
    # branch" was BACKWARDS — WeightConcatenator accepts only WeightSeries
    # branches. NORMALIZER_BEFORE_CONCAT is at WARNING on this tree and its
    # fix validates (NORMALIZER_BEFORE_CONCAT/accept_normalized_branches_*),
    # so the validator owns the fact and compose's sentence left (R-1/R-30).
    # The reference corpus keeps it as method (mistakes.md M-29).
    RuleOwner(
        "R-CONCAT-BRANCHES-SIZED",
        "rule:NORMALIZER_BEFORE_CONCAT",
        "NORMALIZER_BEFORE_CONCAT",
    ),
    # Armed on this tree (G-B, `bdfa79ddd`): the validator owns these now,
    # with a coded message and a fix, so compose's sentences left.
    RuleOwner(
        "R-FORECAST-NORMALIZER",
        "rule:FORECAST_MAGNITUDE_DISCARDED",
        "FORECAST_MAGNITUDE_DISCARDED",
    ),
    RuleOwner("R-FIXEDWEIGHT-LEVERAGECAP", "rule:FIXED_WEIGHT_UNCAPPED", "FIXED_WEIGHT_UNCAPPED"),
    RuleOwner("R-MASK-DIRECTION", "rule:MASK_ON_DIRECTIONAL_SIGNAL", "MASK_ON_DIRECTIONAL_SIGNAL"),
    RuleOwner(
        "R-POLARITY",
        "tool:keel_strategy_compose",
        "polarity is trend-following by default",
    ),
    RuleOwner(
        "R-DEFAULTS-UNIVERSE",
        "tool:keel_strategy_compose",
        "Universe(mode='top_volume', top_n=30, market='perp')",
    ),
    RuleOwner("R-HELP-TOC", "tool:keel_help", "With no `topic` it lists"),
    RuleOwner("R-SKILLS-VIA-HELP", "tool:keel_help", '`topic="skills"` lists the agent skills'),
    RuleOwner("R-QUOTA", "core:quota", "Runs and compute are metered per period"),
    RuleOwner(
        "R-OPEN-IN-APP",
        "core:surface-listed",
        "strategies that are running are managed in the Keel web app (keel_app_link)",
    ),
    # The listed line no longer carries the state sentence (04 §5.3, 05
    # §3.3: research and backtests only; no orders, funds or wallets). The
    # fact stays owned by the tool whose result states it: a read of a
    # non-HEAD version names the server's HEAD apart.
    RuleOwner("R-STATE-ON-SERVER", "tool:keel_strategy_get", "names HEAD apart"),
    RuleOwner("R-ERROR-TWICE", "tool:keel_connection_check", "failing twice on one root cause"),
    RuleOwner("R-AUTH-FLOW", "core:auth", "means call keel_auth_login"),
    RuleOwner("R-SERVER-HEAD", "core:state-model", "server HEAD is the truth"),
    RuleOwner("R-FRESHNESS", "core:live", "read its freshness first"),
    RuleOwner(
        "R-LIVE-HANDOFF", "core:live", "hand off to the web app (handoff_required, action_url)"
    ),
    RuleOwner("R-TOOLSETS", "core:live-write-notice", "KEEL_TOOLSETS="),
    RuleOwner(
        "R-DEFAULT-WINDOW",
        "tool:keel_backtest_run",
        "the window is the platform's default for the strategy's timeframe",
    ),
    RuleOwner(
        "R-DESCRIPTION-PARAM",
        "param:keel_strategy_compose.description",
        "Shown on the strategy page",
    ),
    RuleOwner("R-WATCH-NO-POLL", "tool:keel_backtest_watch", "polls to a terminal status"),
    RuleOwner("R-MONITOR-BARE", "tool:keel_live_monitor", "With no arguments"),
    RuleOwner("R-DEPLOY-CEREMONY", "param:keel_live_deploy.direct", "KEEL_ALLOW_DIRECT_DEPLOY"),
)

#: The prefix of a finding the caller did not supply the evidence to
#: settle. "I could not tell" is never "there is nothing wrong"
#: (`.claude/rules`), so an unverifiable owner is REPORTED, marked as such.
UNVERIFIED = "unverified"

#: A validator severity that makes a code a carrier (R-30).
_ARMED_SEVERITIES = frozenset({"warning", "error"})


def rule_owner_violations(
    sections: Sequence[Section] | None = None,
    *,
    rule_severities: Mapping[str, str] | None = None,
    carriers: Mapping[str, str] | None = None,
) -> list[str]:
    """Every rule id has exactly ONE owner, and the owner says it.

    Failures reported:

    * a rule id named by two or more sections whose `layer` is `head` or
      `body` — two always-on owners is the 25-places problem regrowing;
    * a `core:<id>` owner whose section does not list the rule, or whose
      canonical sentence is not in its body;
    * a `tool:`/`param:` owner whose canonical is not in the live string
      `carriers` maps it to (keys `tool:<name>` / `param:<tool>.<name>`);
    * a `rule:<CODE>` owner whose code is absent from `rule_severities`
      (code → effective severity; `""` for a DORMANT code) or is not at
      WARNING or ERROR — a dormant rule is no carrier (R-30).

    `carriers` and `rule_severities` are the caller's evidence: this module
    is stdlib-only and imports neither the tool registry nor the catalog.
    When one is not supplied, every owner it would settle is reported with
    the `UNVERIFIED` prefix rather than passed.
    """
    secs = tuple(sections) if sections is not None else load_sections()
    by_rule: dict[str, list[str]] = {}
    for s in secs:
        for rule in s.rules:
            by_rule.setdefault(rule, []).append(f"{s.id}({s.layer})")
    out: list[str] = []
    for rule, owners in sorted(by_rule.items()):
        emitted = [o for o in owners if o.endswith("(head)") or o.endswith("(body)")]
        if len(emitted) > 1:
            out.append(f"{rule}: {len(emitted)} always-on owners {sorted(emitted)}")
    declared = {s.id: s for s in secs}
    for owner in RULE_OWNERS:
        kind, _, target = owner.owner.partition(":")
        if kind == "core":
            sec = declared.get(target)
            if sec is None:
                out.append(f"{owner.rule}: owner section '{target}' does not exist")
                continue
            if owner.rule not in sec.rules:
                out.append(f"{owner.rule}: owner section '{target}' does not declare it")
            if owner.canonical not in sec.body:
                out.append(f"{owner.rule}: canonical sentence not found verbatim in '{target}'")
        elif kind in ("tool", "param"):
            if carriers is None:
                out.append(f"{UNVERIFIED} {owner.rule}: no carriers supplied for {owner.owner}")
                continue
            live = carriers.get(owner.owner)
            if live is None:
                out.append(f"{owner.rule}: owner {owner.owner} is not a registered carrier")
            elif owner.canonical not in live:
                out.append(f"{owner.rule}: canonical not found verbatim in {owner.owner}")
        elif kind == "rule":
            if rule_severities is None:
                out.append(f"{UNVERIFIED} {owner.rule}: no catalog supplied for {owner.owner}")
                continue
            if target not in rule_severities:
                out.append(f"{owner.rule}: {owner.owner} names no catalog code")
            elif rule_severities[target] not in _ARMED_SEVERITIES:
                state = rule_severities[target] or "dormant"
                out.append(
                    f"{owner.rule}: {owner.owner} is {state}, not armed — a dormant rule "
                    "is no carrier; its prose keeps the fact (R-30)"
                )
        else:
            out.append(f"{owner.rule}: unknown owner kind {owner.owner!r}")
    # Every rule a section names must have an entry in the owner map.
    mapped = {o.rule for o in RULE_OWNERS}
    for rule in sorted(by_rule):
        if rule not in mapped:
            out.append(f"{rule}: named by a section but absent from RULE_OWNERS")
    return out


# ── Sole-carrier: every instructions sentence has a live twin ───────────


@dataclass(frozen=True)
class Anchor:
    """One instructions sentence and the live strings that twin it.

    `phrases` are ANCHOR PHRASES: each must be found VERBATIM in the
    string its locator names. A paraphrase is not a twin (spec §5).
    Locators are `(kind, name[, sub])` tuples the guard resolves:

    * `("core", "<section-id>")` — a `register: base` section, served in
      full by `keel_help topic=operating_core` (`base_document()`); an
      opinion section is not served on MCP and does not resolve;
    * `("knowledge", "<stem>")` — a bundled knowledge section;
    * `("skill", "<name>")` — a bundled skill's composed text;
    * `("tool", "<keel_name>")` — that tool's description;
    * `("param", "<keel_name>", "<param>")` — a parameter description.
    """

    id: str
    sentence: str
    rules: tuple[str, ...]
    twins: tuple[tuple[tuple[str, ...], str], ...] = field(default=())


def _a(id_: str, sentence: str, rules: str, *twins: tuple[tuple[str, ...], str]) -> Anchor:
    return Anchor(id_, sentence, tuple(r.strip() for r in rules.split()), tuple(twins))


#: spec 01 §2.6, as anchors: the sentences of the LISTED instructions
#: (H*, S*, Q*, L*; H6 and L3 left with mcp-conversion 05 §3.3), then the full profiles' own sentences
#: (A*, T*, V*, N*, SH*, SL*), which claude.ai never sees but Codex and
#: Claude Code truncate at 2 KB. At least ONE twin per sentence must
#: resolve verbatim; the guard reports which, so a sentence down to its
#: last twin is visible before it becomes a sole carrier.
SOLE_CARRIER: tuple[Anchor, ...] = (
    _a(
        "H1",
        "Keel builds crypto strategies on Hyperliquid; use it for … tasks",
        "R-CATEGORY",
        (("core", "identity"), "quantitative crypto research platform"),
    ),
    _a(
        "H2",
        "keel_account_status reports identity, quota and capabilities when a task needs them",
        "R-STATUS-FIRST",
        # The description names what the call returns, not when to make it
        # (Q-2080, 2026-10-01): "the first of a session" read to OpenAI's
        # scan as a trigger beyond the user's request; the head owns the
        # sequencing fact, the description twins its substance.
        (("tool", "keel_account_status"), "who is signed in"),
    ),
    _a(
        "H3",
        "omitted arguments take defaults; a run's result names what ran",
        "R-DEFAULTS-STATED",
        (("tool", "keel_backtest_run"), "the result names the window"),
    ),
    _a(
        "H4",
        "when a result carries `view`, a drawn card is visible — explain it rather than repeat it",
        "R-EXPLAIN-NOT-REPEAT",
        (("skill", "strategy-creation"), "explain it in your own words"),
    ),
    _a(
        "H5",
        "one run: summarize; several: compare once",
        "R-SUMMARIZE-OR-COMPARE",
        (("tool", "keel_backtest_summarize"), "several runs together are `keel_backtest_compare`"),
    ),
    _a(
        "S1",
        "New strategy: search → detail_batch → compose(dry_run) until clean → save → run",
        "R-ROUTE",
        (("tool", "keel_strategy_compose"), "keel_components_get_many"),
        (("knowledge", "tool_usage"), "keel_components_get_many"),
    ),
    _a(
        "S2",
        "an edit to it saves its next version, not a fork",
        "R-ROUTE",
        # 2026-09-25: out of the compose/fork descriptions — ChatGPT's
        # per-call review flagged request-sorting copy there as a
        # "Suspicious Instruction". The skill (tool output) carries it; the
        # descriptions carry the FACT instead (Q-1961: compose keeps every
        # earlier version, a fork starts its own history).
        (("skill", "strategy-fork-and-iterate"), "are VERSIONS of that strategy"),
        (("tool", "keel_strategy_compose"), "earlier versions and their backtests stay"),
    ),
    _a(
        "Q1",
        "Runs and compute are metered per period",
        "R-QUOTA",
        (("tool", "keel_account_status"), "reset instant"),
        (("tool", "keel_plan_usage"), "reset instant"),
        (("core", "quota"), "Metered units"),
    ),
    _a(
        "Q2",
        "keel_plan_usage spends nothing",
        "R-QUOTA",
        (("tool", "keel_plan_usage"), "spends no quota"),
        (("core", "quota"), "so the remaining allowance is known"),
    ),
    _a(
        "Q3",
        "a plan-limit refusal is not retryable and carries its details and the resume",
        "R-QUOTA",
        (("skill", "recover-from-error"), "limit_details"),
        (("core", "quota"), "no re-login clears it"),
    ),
    _a(
        "L1",
        "This connector is for research and backtests; no orders, funds or wallets",
        "R-OPEN-IN-APP",
        (("tool", "keel_account_status"), "what this connector can do"),
    ),
    _a(
        "L2",
        "strategies that are running are managed in the Keel web app (keel_app_link)",
        "R-OPEN-IN-APP",
        (("tool", "keel_app_link"), "web app"),
    ),
    # ── the full profiles' own sentences ──
    _a(
        "A1",
        "authenticated:false / envelope → keel_auth_login",
        "R-AUTH-FLOW",
        (("tool", "keel_auth_login"), "authenticated: false"),
    ),
    _a(
        "A2",
        "scope='live' adds live consent",
        "R-AUTH-FLOW",
        (("param", "keel_auth_login", "scope"), "live-trading consent"),
    ),
    _a(
        "T1",
        "server HEAD is the truth",
        "R-SERVER-HEAD",
        (("tool", "keel_strategy_history"), "server HEAD is the source of truth"),
        (("tool", "keel_strategy_push"), "Server HEAD is the source of truth"),
    ),
    _a(
        "T2",
        "backtest_run pushes unpushed edits; auto_push=false → local_ahead",
        "R-SERVER-HEAD",
        (("param", "keel_backtest_run", "auto_push"), "local_ahead"),
    ),
    _a(
        "T3",
        "compose writes back into a same-machine checkout",
        "R-SERVER-HEAD",
        (("tool", "keel_strategy_compose"), "workspace_sync"),
    ),
    _a(
        "T4",
        "sync_conflict stops; nothing merges automatically",
        "R-SERVER-HEAD",
        (("tool", "keel_strategy_push"), "Conflict-safe"),
    ),
    _a(
        "V1",
        "keel_live_monitor reads; read freshness first",
        "R-FRESHNESS",
        (("tool", "keel_live_monitor"), "freshness"),
    ),
    _a(
        "V2",
        "deploy/control need live-write + explicit request",
        "R-LIVE-HANDOFF",
        (("tool", "keel_live_control"), "already live"),
    ),
    _a(
        "V3",
        "hand off to the web app (handoff_required, action_url)",
        "R-LIVE-HANDOFF",
        (("tool", "keel_live_deploy"), "handoff_required"),
    ),
    _a(
        "N1",
        "live-write notice / KEEL_TOOLSETS",
        "R-TOOLSETS",
        (("tool", "keel_account_status"), "KEEL_TOOLSETS"),
    ),
    _a(
        "SH1",
        "hosted: no workspace tools or keel_auth_login",
        "R-OPEN-IN-APP",
        (("tool", "keel_account_status"), "toolsets"),
        (("skill", "recover-from-error"), "hosted endpoint"),
    ),
    _a(
        "SH2",
        "hosted: files need the CLI",
        "R-OPEN-IN-APP",
        (("tool", "keel_connection_check"), "Keel CLI"),
    ),
    _a(
        "SH3",
        "hosted: keel_app_link opens charts and actions",
        "R-OPEN-IN-APP",
        (("tool", "keel_app_link"), "web app"),
    ),
    _a(
        "SL1",
        "local: charts and visual review",
        "R-OPEN-IN-APP",
        (("tool", "keel_app_link"), "tearsheet and charts"),
    ),
    _a(
        "SL2",
        "local: live management without an install",
        "R-LIVE-HANDOFF",
        (("skill", "recover-from-error"), "hosted endpoint"),
    ),
    _a(
        "SL3",
        "hosted/local: erroring twice → keel_connection_check, then keel_feedback",
        "R-ERROR-TWICE",
        (("tool", "keel_connection_check"), "failing twice on one root cause"),
        (("tool", "keel_feedback"), "when the user asks to send feedback"),
    ),
)


def core_twin_violations() -> list[str]:
    """A `core:` twin must be text the INSTRUCTIONS do not carry.

    The sole-carrier rule is "nothing lives only in the instructions", so
    a twin that is itself an instructions sentence proves nothing. Every
    `("core", <id>)` anchor phrase must therefore be absent from all
    three assembled strings — which is true of a `layer: skill` /
    `reference` section, and of the LONG-FORM paragraphs of a `layer:
    body` section (the builder emits only the first).
    """
    assemblies = {p: instructions(p, live_write_loaded=False) for p in PROFILE_TAGS}
    served = {s.id for s in base_sections()}
    out: list[str] = []
    for anchor in SOLE_CARRIER:
        for locator, phrase in anchor.twins:
            if locator[0] != "core":
                continue
            if locator[1] not in served:
                out.append(
                    f"{anchor.id}: core twin {locator[1]!r} is not a served base section "
                    "(an opinion section never reaches an MCP host)"
                )
            for profile, text in assemblies.items():
                if phrase in text:
                    out.append(
                        f"{anchor.id}: core twin {locator[1]!r} phrase {phrase!r} is "
                        f"itself in the {profile} instructions — not a twin"
                    )
    return out


def missing_anchors(resolved: Mapping[tuple[str, ...], str]) -> list[str]:
    """Sentences with no twin found verbatim in any live string.

    `resolved` maps each locator tuple to the live string it names. A
    locator absent from `resolved` is reported as unresolvable — "I could
    not tell" is never "there is nothing wrong" (`.claude/rules`).
    """
    out: list[str] = []
    for anchor in SOLE_CARRIER:
        found = False
        for locator, phrase in anchor.twins:
            live = resolved.get(locator)
            if live is None:
                out.append(f"{anchor.id}: locator {locator} did not resolve to a live string")
                continue
            if phrase in live:
                found = True
        if not found:
            twins = ", ".join(f"{loc}:{ph!r}" for loc, ph in anchor.twins)
            out.append(f"{anchor.id} ({anchor.sentence}): no twin found — tried {twins}")
    return out


def anchor_locators() -> tuple[tuple[str, ...], ...]:
    """Every distinct locator the sole-carrier table names."""
    seen: list[tuple[str, ...]] = []
    for anchor in SOLE_CARRIER:
        for locator, _ in anchor.twins:
            if locator not in seen:
                seen.append(locator)
    return tuple(seen)


# ── The description register (directives audit §4) ──────────────────────

#: Behavioural-directive patterns. A description states what the tool
#: does and when to invoke it; conduct belongs in the instructions and
#: the skills (directory review criteria, audit §1).
#: The list is the audit's, verbatim (§4): `BE PROACTIVE`, `ALWAYS`,
#: `NEVER`, `Do NOT ask`, `Don't ask`, `you're`, `you should`, `you must`,
#: `Load the`, `FIRST-TIME`, `Start here`, `Reach for it`, `just run it`,
#: `without asking`, `don't rewrite`, `rearchitect`, `not optional`,
#: `composing blind`, `sweep`. Nothing is added to it here — a wider net
#: would flag mechanics ("just call it again" on `keel_backtest_watch`)
#: that state what the tool DOES.
DIRECTIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("be-proactive", r"\bBE PROACTIVE\b"),
    ("always-shout", r"\bALWAYS\b"),
    ("never-shout", r"\bNEVER\b"),
    ("do-not-ask", r"\b(?:Do NOT ask|Don't ask|do not ask|don't ask)\b"),
    ("second-person-state", r"\byou're\b|\byou should\b|\byou must\b"),
    ("load-first", r"\bLoad the\b|\bFIRST-TIME\b"),
    ("start-here", r"\bStart here\b"),
    ("reach-for-it", r"\bReach for it\b"),
    ("just-run-it", r"\bjust run it\b"),
    ("without-asking", r"\bwithout asking\b"),
    ("dont-rewrite", r"\bdon't rewrite\b|\brearchitect\b"),
    ("not-optional", r"\bnot optional\b|\bcomposing blind\b"),
    ("sweep", r"\bsweep\b"),
)

#: Class-A routing sentences are EXEMPT: "when to invoke it" is required
#: by the same rule, and intra-server sequencing is what every surveyed
#: server does (spec §0). A sentence matching this and naming one of our
#: own tools is not a directive.
ROUTING_EXEMPTION_RE = re.compile(
    r"(?:Do NOT use|Don't use|Use)\b[^.]{0,120}?\bkeel_[a-z0-9_]+"
    r"|\bkeel_[a-z0-9_]+\b[^.]{0,120}?\b(?:instead|enumerat|renders|covers|finds)"
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text or "") if s.strip()]


def directive_violations(descriptions: Mapping[str, str]) -> tuple[list[str], int, int]:
    """(violations, sentences scanned, routing sentences exempted).

    The two counts are the guard's non-vacuity arm: a scanner that
    exempted everything would report zero violations AND zero
    exemptions, and a scanner reading nothing would report zero
    sentences.
    """
    violations: list[str] = []
    scanned = 0
    exempted = 0
    compiled = [(name, re.compile(pat)) for name, pat in DIRECTIVE_PATTERNS]
    for owner, text in sorted(descriptions.items()):
        for sentence in _sentences(text):
            scanned += 1
            if ROUTING_EXEMPTION_RE.search(sentence):
                exempted += 1
                continue
            for name, rx in compiled:
                if rx.search(sentence):
                    violations.append(f"{owner}: [{name}] {sentence[:120]}")
                    break
    return violations, scanned, exempted


# ── The base register lint (spec 01 §4 #7) ──────────────────────────────

#: The descriptive register's one regex (Q-1804), shared by the base
#: register lint here and the listed policy scan
#: (`keel-sdk/tests/test_policy_scan.py::IMPERATIVE_RE`, which pins its
#: pattern to this one). Case-insensitive on purpose: "never auto-merged"
#: is an imperative to a model whatever its case.
IMPERATIVE_RE = re.compile(
    r"\bDo NOT\b|\bNEVER\b|\bALWAYS\b|\bMUST\b|\byou should\b|\brelay"
    r"|\bdo not use\b|\binstead call\b",
    re.IGNORECASE,
)

#: The opinion register's own words — method and posture that no
#: IMPERATIVE_RE match catches because they read as description. Head
#: fact 4 ("explain it rather than repeat it") is ratified base-register
#: reply conduct (proposals §7 row 12) and is deliberately NOT listed.
REGISTER_WORD_RE = re.compile(
    r"\b(keep moving|be proactive|proactively|smallest change|rearchitect|hand-roll(?:ing)?)\b",
    re.IGNORECASE,
)


def register_violations(emissions: Mapping[str, str]) -> tuple[list[str], int]:
    """(violations, emissions scanned) for text served in the BASE register.

    Every base emission — the instructions on each profile, the base
    document — must match none of `DIRECTIVE_PATTERNS` (no routing
    exemption: the instructions are not a description), `IMPERATIVE_RE`
    or `REGISTER_WORD_RE`. The count is the lint's non-vacuity arm.
    """
    compiled = [(name, re.compile(pat)) for name, pat in DIRECTIVE_PATTERNS]
    compiled += [("imperative", IMPERATIVE_RE), ("opinion-word", REGISTER_WORD_RE)]
    out: list[str] = []
    for owner, text in sorted(emissions.items()):
        for name, rx in compiled:
            for m in rx.finditer(text or ""):
                out.append(f"{owner}: [{name}] {m.group(0)!r}")
    return out, len(emissions)


# ── The description shape rule (spec 01 §2.5, R-4) ──────────────────────

#: Forbidden sentence classes, `(name, pattern, scope)`. `scope` is `all`
#: (every profile's description) or `listed` (a LISTED description only —
#: the hosted server is file-free, so a write-through mechanism is not a
#: fact about the tool that reader holds). Case-insensitive.
DESCRIPTION_CLASSES: tuple[tuple[str, str, str], ...] = (
    (
        "rendering-cadence",
        r"renders as|one-line receipt|the rendering for|at full size|one-line form"
        r"|renders what changed|renders several|renders one run",
        "all",
    ),
    ("method", r"\bMethod —|after discovery:", "all"),
    ("reply-style", r"in a reply|never needs repeating|restat|in your reply|\bpaste", "all"),
    (
        "off-surface",
        r"auto_push|local_ahead|checkout|source_file|workspace_sync|push_message|unpushed",
        "listed",
    ),
)

#: One `keel_help(topic="skill:…")` pointer per description is a fact
#: about where the method is; a second one is the method creeping back.
_SKILL_POINTER_RE = re.compile(r'topic="skill:')
_BACKTICKED_TOOL_RE = re.compile(r"`(keel_[a-z0-9_]+)`")


def _first_line(text: str) -> str:
    parts = _sentences(text)
    return parts[0] if parts else ""


def description_shape_violations(
    descriptions: Mapping[str, str],
    *,
    listed: bool,
) -> tuple[list[str], int]:
    """(violations, sentences scanned) under the description shape rule.

    `descriptions` maps an owner label to its text; `listed` says whether
    they are LISTED strings (the off-surface class applies to those only).
    Classes checked: the `DESCRIPTION_CLASSES` table, more than one
    `skill:` pointer, `IMPERATIVE_RE`, `DIRECTIVE_PATTERNS` (with the
    routing exemption `directive_violations` applies), and duplicate
    routing — a later sentence of the form "… is `keel_x`" naming a tool
    the first line already names.
    """
    classes = [
        (name, re.compile(pat, re.IGNORECASE))
        for name, pat, scope in DESCRIPTION_CLASSES
        if scope == "all" or listed
    ]
    out: list[str] = []
    scanned = 0
    for owner, text in sorted(descriptions.items()):
        sentences = _sentences(text)
        scanned += len(sentences)
        for sentence in sentences:
            for name, rx in classes:
                m = rx.search(sentence)
                if m:
                    out.append(f"{owner}: [{name}] {m.group(0)!r} in {sentence[:100]!r}")
            m = IMPERATIVE_RE.search(sentence)
            if m:
                out.append(f"{owner}: [imperative] {m.group(0)!r} in {sentence[:100]!r}")
        pointers = len(_SKILL_POINTER_RE.findall(text or ""))
        if pointers > 1:
            out.append(
                f"{owner}: [method] {pointers} skill pointers (one is a fact; more is method)"
            )
        first = _first_line(text or "")
        for tool in set(_BACKTICKED_TOOL_RE.findall(first)):
            again = re.compile(rf"\b(?:is|are)\s+`{re.escape(tool)}`")
            for sentence in sentences[1:]:
                if again.search(sentence):
                    out.append(
                        f"{owner}: [duplicate-routing] {tool} is named by the first line "
                        f"and routed again in {sentence[:100]!r}"
                    )
    directives, _, _ = directive_violations(descriptions)
    out += [f"[directive] {v}" for v in directives]
    return out, scanned


# ── Description size (HARD bytes; chars REPORTED) ───────────────────────


def description_size_report(descriptions: Mapping[str, str]) -> dict[str, object]:
    """Measure every description. Only `over_hard_bytes` is a failure."""
    sizes = {
        name: {"chars": len(text or ""), "bytes": len((text or "").encode("utf-8"))}
        for name, text in descriptions.items()
    }
    return {
        "count": len(sizes),
        "sizes": sizes,
        "over_hard_bytes": sorted(
            f"{name}: {v['bytes']} bytes (> {DESCRIPTION_MAX_BYTES})"
            for name, v in sizes.items()
            if v["bytes"] > DESCRIPTION_MAX_BYTES
        ),
        "over_aim_chars": sorted(
            f"{name}: {v['chars']} chars (aim {AIM_DESCRIPTION_CHARS})"
            for name, v in sizes.items()
            if v["chars"] > AIM_DESCRIPTION_CHARS
        ),
        "total_chars": sum(v["chars"] for v in sizes.values()),
        "total_bytes": sum(v["bytes"] for v in sizes.values()),
    }


# ── Skill-index conservation (size REPORTED, conservation ASSERTED) ─────


def missing_index_entries(declared: Sequence[str], composed: str) -> list[str]:
    """Sections a skill declares that its composed text never names.

    The guard asserts CONSERVATION, not a size: a skill may move a
    section out of its body, but its reference index must still name it
    (spec §5 `skill size`).
    """
    return [name for name in declared if name not in composed]


# ── Staleness lint (spec §4) ────────────────────────────────────────────

#: Forward references: prose that promises a future the product either
#: already has or never shipped.
FORWARD_REFERENCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("yet", r"\(yet\)"),
    ("future-resource", r"\bfuture\s+`?keel://|\bfuture\s+\w+\s+resource\b"),
    ("phase-2c", r"\bPhase 2C\b"),
    ("ships-in-phase", r"\bships? in Phase\b"),
)

#: Claims that a specific date is the EARLIEST data Keel has. Exactly one
#: such date may exist, and it must be the owner's
#: (`backtest_run._DEFAULT_START_DATE`) — decision D-g.
_EARLIEST_CLAIM_RE = re.compile(
    r"(?:earliest|oldest|history begins|data begins|start of history)"
    r"[^.\n]{0,80}?(20\d\d-\d\d-\d\d)"
    r"|(20\d\d-\d\d-\d\d)[^.\n]{0,40}?(?:earliest|oldest)",
    re.IGNORECASE,
)

_TOOL_NAME_RE = re.compile(r"\bkeel_[a-z0-9_]+\b")

#: Pending staleness items, each owned by another lane of this build and
#: named with the W2 §5 item it is. The guard REQUIRES each to still be
#: present: an acknowledgement that no longer matches anything is itself
#: a failure, so this list cannot rot into a permanent exemption.
STALENESS_ACKNOWLEDGED: Mapping[str, str] = {
    # Empty, and it should stay that way: W2 §5's S1 ("(yet)" in
    # backtest-and-analyze) and S2 (the "future `keel://…` resource" in
    # strategy_log) were both fixed by lane G2/G3 before this lint
    # landed, so nothing needs acknowledging. An entry here is a
    # DATED exemption another lane owns, and
    # `acknowledged_but_absent()` fails once its item is gone, so the map
    # cannot rot into a permanent hole.
}


def forward_reference_violations(
    corpus: Mapping[str, str],
    acknowledged: Mapping[str, str] | None = None,
) -> list[str]:
    acks = STALENESS_ACKNOWLEDGED if acknowledged is None else acknowledged
    out: list[str] = []
    for owner, text in sorted(corpus.items()):
        for name, pat in FORWARD_REFERENCE_PATTERNS:
            if re.search(pat, text or "", re.IGNORECASE):
                if acks.get(owner) == name:
                    continue
                out.append(f"{owner}: forward reference [{name}]")
    return out


def acknowledged_but_absent(
    corpus: Mapping[str, str],
    acknowledged: Mapping[str, str] | None = None,
) -> list[str]:
    """Acknowledgements whose item is gone — remove the acknowledgement."""
    acks = STALENESS_ACKNOWLEDGED if acknowledged is None else acknowledged
    out: list[str] = []
    patterns = dict(FORWARD_REFERENCE_PATTERNS)
    for owner, name in sorted(acks.items()):
        text = corpus.get(owner)
        if text is None:
            out.append(f"{owner}: acknowledged but the owner is not in the corpus")
            continue
        if not re.search(patterns[name], text, re.IGNORECASE):
            out.append(f"{owner}: acknowledged [{name}] no longer present — drop the entry")
    return out


def unknown_tool_violations(
    corpus: Mapping[str, str], known: frozenset[str] | set[str]
) -> list[str]:
    """`keel_*` names in prose that the registry does not know (S11)."""
    out: list[str] = []
    for owner, text in sorted(corpus.items()):
        for ref in sorted(set(_TOOL_NAME_RE.findall(text or ""))):
            if ref not in known:
                out.append(f"{owner}: unknown tool name {ref}")
    return out


def earliest_data_violations(corpus: Mapping[str, str], owner_date: str) -> list[str]:
    """One earliest-data owner: every such claim renders the same date."""
    found: dict[str, list[str]] = {}
    for owner, text in sorted(corpus.items()):
        for match in _EARLIEST_CLAIM_RE.finditer(text or ""):
            date = match.group(1) or match.group(2)
            found.setdefault(date, []).append(owner)
    out = [
        f"{date}: claimed as earliest data in {sorted(set(owners))} (owner is {owner_date})"
        for date, owners in sorted(found.items())
        if date != owner_date
    ]
    return out


def earliest_data_claims(corpus: Mapping[str, str]) -> dict[str, list[str]]:
    """Every earliest-data claim by date — the lint's non-vacuity arm."""
    found: dict[str, list[str]] = {}
    for owner, text in sorted(corpus.items()):
        for match in _EARLIEST_CLAIM_RE.finditer(text or ""):
            date = match.group(1) or match.group(2)
            found.setdefault(date, []).append(owner)
    return found


def staleness_violations(
    corpus: Mapping[str, str],
    known_tools: frozenset[str] | set[str],
    owner_date: str,
) -> list[str]:
    """Every staleness rule, in one call."""
    return (
        forward_reference_violations(corpus)
        + acknowledged_but_absent(corpus)
        + unknown_tool_violations(corpus, known_tools)
        + earliest_data_violations(corpus, owner_date)
    )
