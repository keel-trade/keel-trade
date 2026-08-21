"""Schema-0 trace instrumentation shim (dsl-type-system spec 04 §2.2.2).

Observation-only instrumentation for the CURRENT DSL validator: a
:class:`Schema0Sink` collects the schema-0 base trace — bases + slot
states + ordered issues, exactly what today's validator computes — via
four observation points wired into ``dsl/validator.py``:

1. ``on_step(n, path, kind, component, version, out_name)`` — the pass-6
   walk's synthesized-output site, once per visited step.
2. ``on_slot_state(name, type_name)`` — once per slot from the pass-8
   store map after the walk completes (terminal map, not per-write).
3. ``on_issue(code, severity, path)`` — the single catalog ``emit()``
   site every issue already flows through.
4. ``finalize(output_name) -> dict`` — assembles the schema-0 document.

Every hook site in the validator is a one-``if`` no-op when the sink is
``None`` (the default and the only shipped configuration): zero verdict
changes, zero new branches beyond the sink-presence checks (spec 06 §2.3
step 1).

Schema-0 semantics pinned here (deterministic; no timestamps, no
randomness — byte-identical re-emission for the M0 pin):

- ``steps[*].out`` / ``terminal.output`` / ``terminal.slots`` values are
  the canonical ``type_name()`` strings today's walk assigns (04 §2.2.2).
  A ``validate_strategy`` run that short-circuits before pass 6
  (structural errors, unknown components) records ``terminal.output``
  ``"None"`` — the walk's initial fold value ``type(None)``.
- ``issues[*].path`` is the location string today's validator attaches at
  ``emit()`` (or ``None``) — the only issue-position vocabulary the
  current validator has. Messages are excluded (04 §2.2 rule 5).
- ``terminal.issues`` order is 04 §2.2 rule 8's canonical
  ``(ordinal, code, path)``: ordinal is the first step ordinal whose
  ``path`` equals the issue's path, else −1 (non-event issues); −1
  issues sort first, ordered among themselves by ``(code, path)`` with
  ``None`` path sorting as ``""``. Ties keep emission order (stable sort).
- Structural paths follow 04 §2.2.1's grammar over the post-expansion
  walk: ``root`` + ``/<index>`` segments, ``/<branch>/<index>`` inside a
  Parallel branch, ``/!<name>/<index>`` inside a factory/variable
  expansion frame. The factory frame identifier is the post-pass-3
  expanded ``PipelineSpec.name`` (pass 3 always names expansions);
  anonymous nested pipelines chain plain indices (04 §9 open question 1:
  the grammar is the proposal of record).

A step the shim cannot classify into the closed step-kind enum is a loud
emission error, never a silent skip. The sink is single-use: observation
after ``finalize()`` — or a second ``finalize()`` — raises.

Quick Start:
    >>> from pipeline_engine.dsl.trace_shim import Schema0Sink
    >>> from pipeline_engine.dsl.validator import validate_strategy
    >>> sink = Schema0Sink(subject={...}, config={...})
    >>> result = validate_strategy(strategy, trace_sink=sink)
    >>> doc = sink.document  # the schema-0 trace document
"""

from __future__ import annotations

import contextvars
import copy
from contextlib import contextmanager
from typing import Any, Iterator


__all__ = [
    "STEP0_KINDS",
    "TRACE_SCHEMA_VERSION",
    "Schema0Sink",
    "Schema1Sink",
    "TraceShimError",
    "activate_trace_sink",
    "active_trace_sink",
    "project_schema1_to_schema0",
]

#: Schema version of the documents this sink emits (file suffix ``.trace0.json``).
TRACE_SCHEMA_VERSION = 0

#: Closed step-kind enum (04 §2.2 schema-1's enum, shared by schema-0).
STEP0_KINDS = frozenset(
    {
        "component",
        "parallel",
        "slot_store",
        "slot_store_value",
        "slot_load",
        "slot_extract",
    }
)

_ISSUE_SEVERITIES = frozenset({"error", "warning", "info"})


class TraceShimError(RuntimeError):
    """Loud emission error — the shim never silently skips or improvises."""


class Schema0Sink:
    """Collects one schema-0 trace document for one ``validate_strategy`` run.

    ``subject`` and ``config`` are caller-supplied (the emission writer —
    ``write_golden_traces`` — owns sourcing them: subject per 04 §2.2's
    subject block, config per 04 §2.2.6's vector). The sink deep-copies
    both so later caller mutation cannot skew the document.
    """

    def __init__(self, subject: dict[str, Any], config: dict[str, Any]) -> None:
        self._subject = copy.deepcopy(subject)
        self._config = copy.deepcopy(config)
        self._steps: list[dict[str, Any]] = []
        self._slots: dict[str, str] = {}
        self._issues: list[tuple[str, str, str | None]] = []
        self._path_first_ordinal: dict[str, int] = {}
        self._document: dict[str, Any] | None = None

    @property
    def next_n(self) -> int:
        """Ordinal the next observed step must carry (walk order, 0-based)."""
        return len(self._steps)

    def _check_open(self, hook: str) -> None:
        if self._document is not None:
            raise TraceShimError(
                f"Schema0Sink.{hook}() called after finalize() — the sink is "
                f"single-use; construct a fresh sink per validate_strategy run."
            )

    def on_step(
        self,
        n: int,
        path: str,
        kind: str,
        component: str | None,
        version: int | None,
        out_name: str,
    ) -> None:
        """Observation point 1 — the pass-6 walk's synthesized-output site."""
        self._check_open("on_step")
        if kind not in STEP0_KINDS:
            raise TraceShimError(
                f"on_step(): step kind {kind!r} is outside the closed schema-0 "
                f"enum {sorted(STEP0_KINDS)} — extend the schema (a reviewed "
                f"change), never improvise a kind."
            )
        if n != len(self._steps):
            raise TraceShimError(
                f"on_step(): ordinal {n} breaks walk order — expected "
                f"{len(self._steps)} (ordinals are contiguous in visit order)."
            )
        self._steps.append(
            {
                "n": n,
                "path": path,
                "step": {"kind": kind, "component": component, "version": version},
                "out": out_name,
            }
        )
        self._path_first_ordinal.setdefault(path, n)

    def on_slot_state(self, name: str, type_name: str) -> None:
        """Observation point 2 — terminal pass-8 store map, once per slot."""
        self._check_open("on_slot_state")
        if name in self._slots:
            raise TraceShimError(
                f"on_slot_state(): slot {name!r} observed twice — the terminal "
                f"store map carries one entry per slot."
            )
        self._slots[name] = type_name

    def on_issue(self, code: str, severity: str, path: str | None) -> None:
        """Observation point 3 — the single catalog ``emit()`` site."""
        self._check_open("on_issue")
        if severity not in _ISSUE_SEVERITIES:
            raise TraceShimError(
                f"on_issue(): severity {severity!r} is outside {sorted(_ISSUE_SEVERITIES)}."
            )
        self._issues.append((code, severity, path))

    def finalize(self, output_name: str) -> dict[str, Any]:
        """Observation point 4 — assemble and return the schema-0 document."""
        self._check_open("finalize")

        def _issue_key(rec: tuple[str, str, str | None]) -> tuple[int, str, str]:
            code, _severity, path = rec
            ordinal = -1 if path is None else self._path_first_ordinal.get(path, -1)
            return (ordinal, code, "" if path is None else path)

        ordered = sorted(self._issues, key=_issue_key)  # stable: ties keep emission order
        self._document = {
            "trace_schema": TRACE_SCHEMA_VERSION,
            "subject": self._subject,
            "config": self._config,
            "steps": self._steps,
            "terminal": {
                "output": output_name,
                "slots": self._slots,
                "issues": [
                    {"code": code, "severity": severity, "path": path}
                    for code, severity, path in ordered
                ],
            },
        }
        return self._document

    @property
    def document(self) -> dict[str, Any]:
        """The assembled schema-0 document. Raises before ``finalize()`` ran."""
        if self._document is None:
            raise TraceShimError(
                "Schema0Sink.document read before finalize() — the document "
                "exists only after a completed validate_strategy run."
            )
        return self._document


# ═══════════════════════════════════════════════════════════════════════════
# Schema-1 — the full typed trace (spec 04 §2.2), born at the M2c cutover
# ═══════════════════════════════════════════════════════════════════════════

#: Closed SKIP vocabulary (spec 04 §2.2 rule 7).
SKIP_VERDICTS = frozenset({"skip:loader-entry", "skip:none-storevalue"})

#: Closed MARK vocabulary (spec 04 §2.2 rule 7). Only
#: ``waiver:record-tolerant`` is observable at m2-compat: the degrade rows
#: (J-INST/J-LIT) belong to scheme-synthesis-post machinery, and ``widened``
#: needs a ⊔ cap-overflow report channel the closed algebra does not expose.
SCHEMA1_MARKS = frozenset(
    {
        "widened",
        "degrade:unresolvable-binding",
        "degrade:variable-ref",
        "degrade:registry-skew",
        "waiver:record-tolerant",
    }
)


def _ser_domain(d) -> object:
    """DOM canon (spec 04 §2.2 rule 2): sets sorted ascending, ``-0.0`` → 0.0;
    NaN/inf/⊥ abort emission with a loud error."""

    def _num(x) -> float:
        v = float(x)
        if v != v or v in (float("inf"), float("-inf")):
            raise TraceShimError(f"non-finite domain element {x!r} in a schema-1 trace")
        return 0.0 if v == 0 else v

    if d == "top":
        return "top"
    if isinstance(d, dict) and set(d) == {"set"}:
        return {"set": sorted(_num(v) for v in d["set"])}
    if isinstance(d, dict) and set(d) == {"interval"}:
        lo, hi = d["interval"]
        return {"interval": [_num(lo), _num(hi)]}
    raise TraceShimError(f"not a serializable domain lattice element: {d!r}")


def _ser_flow_type(fv) -> object:
    """FlowVal → schema-1 TYPE. The base is the m2-compat ``type_name()``
    rendering (``FlowVal.name`` — today's synthesized base, per spec 03
    §3.4(2) bases never move at M2), so π(TYPE) is exactly the schema-0 NAME."""
    if fv.fields is not None:
        return {"record": {k: _ser_flow_type(sub) for k, sub in fv.fields.items()}}
    if fv.name == "Any":
        return "Any"
    if fv.name == "None":
        return "None"
    if fv.name == "NoneType":
        return {"sentinel": "NoneType"}
    tok = fv.tok
    if hasattr(tok, "variants"):
        raise TraceShimError(
            f"union TYPE in an out/slot position ({fv.name!r}) — schema-1 admits "
            f"unions on the demand side only (spec 04 §2.2)"
        )
    domain = tok.domain if hasattr(tok, "domain") else "top"
    return {"base": fv.name, "domain": _ser_domain(domain)}


def _ser_demand(tok) -> object:
    """Demand token → schema-1 TYPE (``check.expected``), exp-unfolded per the
    relation's own demand resolution; union operands in canonical order."""
    if tok is None:
        return None
    if tok == "Any":
        return "Any"
    from pipeline_engine.dsl import relation as R

    if isinstance(tok, R.Union):
        ordered = R._canonical_union_variants(tok.variants)
        return {"union": [_ser_demand(v) for v in ordered]}
    if isinstance(tok, R.Pair):
        e = R.exp_demand(tok.base)
        dom = e.domain
        if tok.domain != R.TOP and not R.is_refined(tok.base):
            dom = tok.domain
        return {"base": e.base, "domain": _ser_domain(dom)}
    raise TraceShimError(f"not a serializable demand token: {tok!r}")


def _type_to_name(t) -> str:
    """π's TYPE → NAME rule (spec 04 §2.2.3)."""
    if t == "Any":
        return "Any"
    if t == "None":
        return "None"
    if isinstance(t, dict):
        if "record" in t:
            return "dict"
        if "sentinel" in t:
            return t["sentinel"]
        if "base" in t:
            return t["base"]
    raise TraceShimError(f"not a projectable schema-1 TYPE: {t!r}")


class Schema1Sink:
    """Collects one schema-1 typed trace document (spec 04 §2.2) for one
    ``validate_strategy`` run over the judgment-table interpreter walk.

    Rides the same ``trace_sink`` parameter as :class:`Schema0Sink`: it
    implements the schema-0 observation protocol (``on_step`` /
    ``on_slot_state`` / ``on_issue`` / ``finalize`` / ``next_n``) PLUS the
    typed hooks the walk calls when they exist (spec 04 §2.3 — the sink is
    the only trace machinery inside the checker): ``on_check`` / ``on_mark``
    / ``on_slot_write`` / ``on_event`` / ``on_terminal``.

    Pin-defining m2-compat vocabulary notes:

    - Issue ``path`` strings are today's emit-site LOCATION strings (the M0
      schema-0 pin vocabulary, the M0a cross-spec touchpoint). They never
      match event structural paths, so every issue carries ordinal −1 and
      ``terminal.issues`` sorts by ``(code, path)`` — the SAME rule as
      :class:`Schema0Sink`; by rule 8's own attribution definition the
      event-level ``issues`` arrays are empty.
    - ``inst`` is always null: J-INST is scheme-synthesis-post machinery and
      m2-compat pins ``pre``.
    - ``check`` defaults to ``{expected: null, matched: null, verdict:
      "accept"}`` for steps with no input premise (Store, found Load,
      Parallel formation, successful Extract, the R1 unresolved-signature
      recovery — no premise ran).
    - Per-write Σ deltas (``slots``) appear on the writing step's event
      (Store/StoreValue); the Parallel merge re-applies branch deltas
      already evidenced at their branch events, so the parallel event
      carries ``slots: null``.
    """

    def __init__(self, subject: dict[str, Any], config: dict[str, Any]) -> None:
        self._subject = copy.deepcopy(subject)
        self._config = copy.deepcopy(config)
        self._events: list[dict[str, Any]] = []
        self._issues: list[tuple[str, str, str | None]] = []
        self._path_first_ordinal: dict[str, int] = {}
        self._terminal_out: object = "None"
        self._terminal_slots: dict[str, Any] = {}
        self._document: dict[str, Any] | None = None
        self._pending_out_name: str | None = None
        # per-step scratch, reset at each on_event
        self._check: tuple[object, str, int | None] | None = None
        self._marks: list[str] = []
        self._slots_delta: dict[str, Any] | None = None

    @property
    def next_n(self) -> int:
        """Ordinal the next observed step must carry (walk order, 0-based)."""
        return len(self._events)

    def _check_open(self, hook: str) -> None:
        if self._document is not None:
            raise TraceShimError(
                f"Schema1Sink.{hook}() called after finalize() — the sink is "
                f"single-use; construct a fresh sink per validate_strategy run."
            )

    # ── schema-0 observation protocol (shared trace_sink parameter) ─────────

    def on_step(self, n, path, kind, component, version, out_name) -> None:
        self._check_open("on_step")
        if kind not in STEP0_KINDS:
            raise TraceShimError(f"on_step(): step kind {kind!r} outside the closed enum")
        if n != len(self._events):
            raise TraceShimError(
                f"on_step(): ordinal {n} breaks walk order — expected {len(self._events)}"
            )
        self._pending_out_name = out_name

    def on_slot_state(self, name: str, type_name: str) -> None:
        self._check_open("on_slot_state")
        # Cross-check only: terminal slot pairs come from on_terminal; the
        # π-name of each must agree with the schema-0 rendering.
        recorded = self._terminal_slots.get(name)
        if recorded is not None and _type_to_name(_ser_flow_type(recorded)) != type_name:
            raise TraceShimError(
                f"on_slot_state(): slot {name!r} schema-0 name {type_name!r} "
                f"disagrees with the recorded terminal pair"
            )

    def on_issue(self, code: str, severity: str, path: str | None) -> None:
        self._check_open("on_issue")
        if severity not in _ISSUE_SEVERITIES:
            raise TraceShimError(f"on_issue(): severity {severity!r} out of vocabulary")
        self._issues.append((code, severity, path))

    # ── typed hooks (the walk calls these when the sink carries them) ───────

    def on_check(self, expected_tok, verdict: str, matched: int | None = None) -> None:
        self._check_open("on_check")
        if not (verdict == "accept" or verdict in SKIP_VERDICTS or verdict.startswith("issue:")):
            raise TraceShimError(f"on_check(): verdict {verdict!r} out of vocabulary")
        self._check = (expected_tok, verdict, matched)

    def on_mark(self, mark: str) -> None:
        self._check_open("on_mark")
        if mark not in SCHEMA1_MARKS:
            raise TraceShimError(f"on_mark(): {mark!r} outside the closed MARK enum (rule 7)")
        self._marks.append(mark)

    def on_slot_write(self, name: str, fv) -> None:
        self._check_open("on_slot_write")
        if self._slots_delta is None:
            self._slots_delta = {}
        self._slots_delta[name] = fv

    def on_event(self, path: str, kind: str, component, version, out_fv) -> None:
        self._check_open("on_event")
        n = len(self._events)
        expected_tok, verdict, matched = self._check or (None, "accept", None)
        out_type = _ser_flow_type(out_fv)
        if self._pending_out_name is not None and _type_to_name(out_type) != self._pending_out_name:
            raise TraceShimError(
                f"on_event(): π of the typed out ({_type_to_name(out_type)!r}) diverges "
                f"from the schema-0 out name ({self._pending_out_name!r}) at n={n}"
            )
        self._events.append(
            {
                "n": n,
                "path": path,
                "step": {"kind": kind, "component": component, "version": version},
                "check": {
                    "expected": _ser_demand(expected_tok),
                    "matched": matched,
                    "verdict": verdict,
                },
                "inst": None,
                "out": out_type,
                "slots": (
                    {name: _ser_flow_type(fv) for name, fv in self._slots_delta.items()}
                    if self._slots_delta is not None
                    else None
                ),
                "marks": list(self._marks),
                "issues": [],
            }
        )
        self._path_first_ordinal.setdefault(path, n)
        self._check = None
        self._marks = []
        self._slots_delta = None
        self._pending_out_name = None

    def on_terminal(self, final_fv, sigma: dict[str, Any]) -> None:
        self._check_open("on_terminal")
        self._terminal_out = _ser_flow_type(final_fv)
        self._terminal_slots = dict(sigma)

    # ── assembly ────────────────────────────────────────────────────────────

    def finalize(self, output_name: str) -> dict[str, Any]:
        self._check_open("finalize")
        if _type_to_name(self._terminal_out) != output_name:
            raise TraceShimError(
                f"finalize(): π of the typed terminal output "
                f"({_type_to_name(self._terminal_out)!r}) diverges from the walk's "
                f"terminal name ({output_name!r})"
            )

        def _issue_key(rec: tuple[str, str, str | None]) -> tuple[int, str, str]:
            code, _severity, path = rec
            ordinal = -1 if path is None else self._path_first_ordinal.get(path, -1)
            return (ordinal, code, "" if path is None else path)

        ordered = sorted(self._issues, key=_issue_key)  # stable: ties keep emission order
        self._document = {
            "trace_schema": 1,
            "subject": self._subject,
            "config": self._config,
            "events": self._events,
            "terminal": {
                "output": self._terminal_out,
                "slots": {k: _ser_flow_type(v) for k, v in self._terminal_slots.items()},
                "issues": [
                    {"code": code, "severity": severity, "path": path}
                    for code, severity, path in ordered
                ],
            },
        }
        return self._document

    @property
    def document(self) -> dict[str, Any]:
        """The assembled schema-1 document. Raises before ``finalize()`` ran."""
        if self._document is None:
            raise TraceShimError(
                "Schema1Sink.document read before finalize() — the document "
                "exists only after a completed validate_strategy run."
            )
        return self._document


def project_schema1_to_schema0(doc: dict[str, Any]) -> dict[str, Any]:
    """The normative projection π: schema-1 → schema-0 (spec 04 §2.2.3).

    Total on well-formed schema-1 documents, deterministic: events → steps
    1:1 (same ``n``/``path``/``step``); TYPE → NAME (domains dropped, records
    ⇒ ``"dict"``, sentinels ⇒ their name); ``check``/``inst``/``marks``/
    per-event ``slots`` project to nothing; terminal maps by the same
    TYPE → NAME rule with ``issues`` carried verbatim (code/severity/path are
    common to both schemas — rule 8's order, the M0a −1-ordinal rule
    included, is preserved because both sinks sort identically); the config
    vector carries over verbatim.
    """
    if doc.get("trace_schema") != 1:
        raise TraceShimError(f"π expects a schema-1 document, got {doc.get('trace_schema')!r}")
    return {
        "trace_schema": 0,
        "subject": copy.deepcopy(doc["subject"]),
        "config": copy.deepcopy(doc["config"]),
        "steps": [
            {
                "n": e["n"],
                "path": e["path"],
                "step": copy.deepcopy(e["step"]),
                "out": _type_to_name(e["out"]),
            }
            for e in doc["events"]
        ],
        "terminal": {
            "output": _type_to_name(doc["terminal"]["output"]),
            "slots": {k: _type_to_name(v) for k, v in doc["terminal"]["slots"].items()},
            "issues": copy.deepcopy(doc["terminal"]["issues"]),
        },
    }


#: Active sink for the ``emit()`` observation point. ``validate_strategy``
#: activates it for the duration of a traced run; the default (``None``)
#: makes every hook site a one-``if`` no-op.
_ACTIVE_SINK: contextvars.ContextVar[Schema0Sink | None] = contextvars.ContextVar(
    "keel_dsl_schema0_sink", default=None
)


def active_trace_sink() -> Schema0Sink | None:
    """The sink active for the current ``validate_strategy`` run, if any."""
    return _ACTIVE_SINK.get()


@contextmanager
def activate_trace_sink(sink: Schema0Sink) -> Iterator[Schema0Sink]:
    """Activate ``sink`` for the enclosed ``validate_strategy`` run."""
    token = _ACTIVE_SINK.set(sink)
    try:
        yield sink
    finally:
        _ACTIVE_SINK.reset(token)
