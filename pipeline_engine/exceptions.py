"""Custom exceptions for the pipeline engine.

Provides rich error context for debugging and clear failure reporting.

Quick Start:
    >>> from pipeline_engine.exceptions import FrameworkError, SlotNotFoundError
    >>> try:
    ...     raise SlotNotFoundError("slot 'ohlcv_1d' not set")
    ... except FrameworkError:
    ...     pass  # SlotNotFoundError is NOT a FrameworkError (it's a KeyError)
"""

from __future__ import annotations

from typing import Any


class FrameworkError(Exception):
    """Base exception for all framework errors."""

    pass


# ─── Structured errors (core-engine-audit spec 01 T3, amendment 10) ─────────


class StructuredError(Exception):
    """Shared base for structured, user-surfaced enforcement errors.

    One error shape across specs 01 and 03 (core-engine-audit): every
    pin/serialize/verify enforcement error carries

    - ``code``: stable machine-readable identifier (UPPER_SNAKE),
    - ``remediation``: the user-facing fix. ``__str__`` renders it FIRST so
      worker surfaces that persist a truncated string — backtest
      ``backtest_runs.error_message`` and live ``signal_runs.error_summary``,
      both capped at 1000 chars — can never lose the remediation (spec 01
      §2.2, remediation-first packing),
    - ``fields``: the machine-readable payload for the backtest, editor,
      and API surfaces (never string-parsed),
    - ``to_dict()``: the wire shape.

    Spec 03's error minting (schema/opaque/fingerprint codes in T4/T5,
    verify-boundary codes in T9) subclasses this base instead of a plain
    ``CompileError``-with-message.
    """

    def __init__(self, *, code: str, remediation: str, detail: str = "", **fields: Any):
        self.code = code
        self.remediation = remediation
        self.detail = detail
        # None-valued fields carry no information — drop them so to_dict()
        # is exactly the meaningful payload.
        self.fields: dict[str, Any] = {k: v for k, v in fields.items() if v is not None}
        super().__init__(self._render_message())

    def _render_message(self) -> str:
        """Remediation first, then the machine code + diagnostic detail."""
        tail = f"[{self.code}] {self.detail}".rstrip()
        return f"{self.remediation} {tail}"

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable payload (spec 01 §2.1 — never string-parse errors)."""
        out: dict[str, Any] = {"code": self.code, "remediation": self.remediation}
        if self.detail:
            out["detail"] = self.detail
        out.update(self.fields)
        return out


class CompileError(Exception):
    """Raised when a pipeline cannot be compiled to canonical JSON.

    Canonical import path is still ``pipeline_engine.pipeline.compile``
    (which re-exports this class). The definition moved here (spec 01 T3)
    so :class:`ComponentVersionError` can subclass both ``CompileError``
    and ``LockError`` without an import cycle — this module imports
    nothing from pipeline_engine.
    """


class LockError(Exception):
    """Raised when lock generation or update fails.

    Canonical import path is still ``pipeline_engine.base.lock`` (which
    re-exports this class). Moved here for the same reason as
    :class:`CompileError` above.
    """


class SerializationContractError(StructuredError, CompileError):
    """A component parameter violates the serialize contract at COMPILE time.

    Spec 03 (round-trip law) T4/T5 — raised while serializing a live
    pipeline, before anything reaches storage. Codes:

    - ``code="PARAM_NOT_SERIALIZABLE"`` (RT-6): a parameter's value has no
      JSON representation and the parameter is not declared in the
      component's ``env_injected_params``. The silent ``{"__type__":
      "opaque"}`` tag this replaces is written by nothing anymore; the v1
      reader still decodes it for legacy blobs only.
    - ``code="PARAM_NOT_READABLE"`` (RT-8): an ``__init__`` parameter has no
      readable attribute on the instance (tried ``k`` and ``_k``), so
      serialization would silently drop it — the hole that swallowed
      ``signals_list`` and ``chunk_config`` (B2).

    Carries ``component``, ``param`` and (for RT-6) ``value_type``.
    """

    def __init__(
        self,
        *,
        code: str,
        remediation: str,
        detail: str = "",
        component: str | None = None,
        param: str | None = None,
        value_type: str | None = None,
    ):
        self.component = component
        self.param = param
        self.value_type = value_type
        super().__init__(
            code=code,
            remediation=remediation,
            detail=detail,
            component=component,
            param=param,
            value_type=value_type,
        )


class BlobSchemaError(StructuredError, CompileError):
    """A compiled blob violates its schema-version read rules at LOAD time.

    Spec 03 §5.1 — the version-gated reader. Codes:

    - ``code="BLOB_SCHEMA_UNKNOWN_VERSION"``: ``schema_version`` outside the
      enumerated supported set. Never "try and see".
    - ``code="BLOB_OPAQUE_PARAM"``: a schema-v2 blob carries an opaque type
      tag (v2 writers can never produce one — the blob is corrupt or
      hand-edited). v1 blobs keep the legacy deserialize-to-None behavior.
    - ``code="BLOB_FINGERPRINT_MISMATCH"``: the round-trip self-check failed
      for a schema-v2 blob (v1 mismatches are WARN + metric — recompiling
      v1 blobs under v2 serialize rules legitimately shifts fingerprints).

    Carries ``schema_version`` and code-specific fields.
    """

    def __init__(
        self,
        *,
        code: str,
        remediation: str,
        detail: str = "",
        schema_version: int | None = None,
        **fields: Any,
    ):
        self.schema_version = schema_version
        super().__init__(
            code=code,
            remediation=remediation,
            detail=detail,
            schema_version=schema_version,
            **fields,
        )


class BlobVerificationError(StructuredError, CompileError):
    """A compiled blob failed the structural ``verify_blob`` walk.

    Spec 03 §4.2/T5 — ``code="BLOB_STRUCTURE_INVALID"``, carrying the full
    ``violations`` list (every structural invariant that failed, never just
    the first). Raised at the write path before ``store_blob`` and by the
    golden-blob CI gate.
    """

    def __init__(
        self,
        *,
        remediation: str,
        violations: list[str],
        detail: str = "",
        schema_version: int | None = None,
    ):
        self.violations = list(violations)
        self.schema_version = schema_version
        super().__init__(
            code="BLOB_STRUCTURE_INVALID",
            remediation=remediation,
            detail=detail or "; ".join(violations),
            violations=self.violations,
            schema_version=schema_version,
        )


class SpecVerificationError(StructuredError, ValueError):
    """A parsed ``StrategyFile`` failed the structural ``verify_spec`` check.

    Spec 03 §4.2/T9 — ``code="SPEC_STRUCTURALLY_INVALID"``, carrying the
    ``stage`` that emitted the invalid spec (``parse``, ``graph_to_spec``,
    ``eval-worker``) and the ``violations`` list. Subclasses ``ValueError``
    so existing boundary ``except (…, ValueError)`` sites catch it while
    the payload stays structured.
    """

    def __init__(
        self,
        *,
        stage: str,
        violations: list[str],
        remediation: str,
        detail: str = "",
    ):
        self.stage = stage
        self.violations = list(violations)
        super().__init__(
            code="SPEC_STRUCTURALLY_INVALID",
            remediation=remediation,
            detail=detail or f"stage={stage}: " + "; ".join(violations),
            stage=stage,
            violations=self.violations,
        )


class SlotTypeResolutionError(StructuredError, CompileError):
    """A Slot's serialized ``value_type`` name cannot be resolved.

    Both sides of the slot value_type round-trip (core-engine-audit T10
    slot-op fix; structured codes per C4 item 4). Codes:

    - ``code="SLOT_TYPE_UNRESOLVABLE"`` (write time): a slot is typed with
      something outside the enumerated platform slot-value-type registry, so
      the compiled blob could never restore it faithfully. Refused at
      COMPILE — never record a name the reader cannot resolve back (no
      store-now-fail-at-load blobs).
    - ``code="SLOT_TYPE_UNKNOWN"`` (read time): a stored blob carries a
      ``value_type`` name that no longer resolves — the writer proved it
      resolvable at compile time, so a platform type was removed/renamed
      after the blob was written (engine drift). Hard error, never a
      silent Any substitution.

    Carries ``slot`` and ``value_type``.
    """

    def __init__(
        self,
        *,
        code: str,
        remediation: str,
        detail: str = "",
        slot: str | None = None,
        value_type: str | None = None,
    ):
        self.slot = slot
        self.value_type = value_type
        super().__init__(
            code=code,
            remediation=remediation,
            detail=detail,
            slot=slot,
            value_type=value_type,
        )


class ComponentVersionError(StructuredError, CompileError, LockError):
    """A stored artifact references a component (version) that cannot resolve.

    Spec 01 §2.1 — the pins-assert enforcement error. Raised with one of:

    - ``code="COMPONENT_VERSION_PHASED_OUT"``: the artifact pins a version
      that is gone from the registry (phased out per spec 01 §1.4). Carries
      ``component``, ``pinned_version``, ``latest_version``,
      ``available_versions``, ``changelog`` and the upgrade remediation.
    - ``code="COMPONENT_UNREGISTERED"``: the class is not in the registry at
      all (deleted/renamed without the §1.4 process, or a pre-registry
      artifact). Carries ``component``, ``module`` and the remediation.

    Subclasses BOTH ``CompileError`` (the blob/reconstruct path contract —
    ``from_compiled`` raises CompileError) and ``LockError`` (the source
    path contract — ``_build_effective_registry`` raises LockError), so
    every existing ``except CompileError`` / ``except LockError`` site
    catches it while the payload stays structured and identical on both
    paths (spec 01 §2.1 unification).
    """

    def __init__(
        self,
        *,
        code: str,
        component: str,
        remediation: str,
        detail: str = "",
        pinned_version: int | None = None,
        latest_version: int | None = None,
        available_versions: list[int] | None = None,
        changelog: dict[int, str] | None = None,
        module: str | None = None,
    ):
        self.component = component
        self.pinned_version = pinned_version
        self.latest_version = latest_version
        self.available_versions = list(available_versions) if available_versions else []
        self.changelog = dict(changelog) if changelog else {}
        self.module = module
        super().__init__(
            code=code,
            remediation=remediation,
            detail=detail,
            component=component,
            pinned_version=pinned_version,
            latest_version=latest_version,
            available_versions=self.available_versions or None,
            changelog=self.changelog or None,
            module=module,
        )


class PipelineError(FrameworkError):
    """Error occurring within pipeline execution."""

    pass


class ComponentExecutionError(FrameworkError):
    """
    Rich error with full execution context.

    This exception wraps component failures with complete context about
    where the failure occurred in the pipeline.
    """

    def __init__(
        self,
        component: Any,
        branch: str | None,
        index: int,
        original_error: Exception,
        signal_type: str | None = None,
    ):
        """
        Initialize execution error with full context.

        Args:
            component: The component that failed
            branch: Branch name if in parallel execution, None for main pipeline
            index: Index of component in the sequence
            original_error: The original exception that was raised
            signal_type: Type of input signal (for debugging type issues)
        """
        self.component = component
        self.branch = branch
        self.index = index
        self.original_error = original_error
        self.signal_type = signal_type

        # Build location string
        if branch:
            location = f"Branch '{branch}' > Index {index}"
        else:
            location = f"Main pipeline > Index {index}"

        # Get component name
        component_name = getattr(component, "name", None) or component.__class__.__name__

        # Build detailed message
        message_parts = [
            f"Pipeline error at {location}",
            f"  Component: {component_name} ({component.__class__.__name__})",
        ]

        if signal_type:
            message_parts.append(f"  Input type: {signal_type}")

        message_parts.append(f"  {original_error.__class__.__name__}: {original_error}")

        super().__init__("\n".join(message_parts))

        # Preserve the original traceback
        self.__cause__ = original_error


class CacheError(FrameworkError):
    """Error during cache operations (read, write, extend)."""

    pass


class PipelineValidationError(FrameworkError):
    """Error during pipeline construction/validation."""

    pass


# ─── Slot & Context Exceptions ────────────────────────────────────────────


class SlotNotFoundError(KeyError):
    """Raised when reading from an unset slot."""

    pass


class SlotTypeError(TypeError):
    """Raised when setting wrong type to slot (in DEBUG mode)."""

    pass


class UndeclaredReadError(RuntimeError):
    """Raised when a step reads a slot not declared in its reads property."""

    pass


class ImmutabilityError(Exception):
    """Raised when attempting to modify a frozen context."""

    pass


class BoundsViolationError(ValueError):
    """Raised when value violates Bounds/Ge/Le constraints (DEBUG mode)."""

    pass


class SlotOverwriteError(FrameworkError):
    """Raised when multiple Parallel branches write the same new slot."""

    pass


class LookaheadError(FrameworkError):
    """Raised when runtime verification detects future data in step output."""

    pass


class ValidationError(FrameworkError):
    """Raised when pipeline validation fails before execution."""

    pass


class InvariantViolationError(FrameworkError):
    """Raised when a pipeline invariant is violated (NaN, Inf, empty, bounds)."""

    pass


class PipelineExecutionError(PipelineError):
    """Rich execution error with step context and context snapshot.

    Wraps exceptions that occur during Pipeline.run() with information
    about which step failed, the context state at failure, and the
    current value flowing through the pipeline.
    """

    def __init__(
        self,
        *,
        step: Any,
        step_index: int,
        original_error: Exception,
        context_snapshot: Any | None = None,
        current_value: Any | None = None,
        branch_name: str | None = None,
    ):
        self.step = step
        self.step_index = step_index
        self.original_error = original_error
        self.context_snapshot = context_snapshot
        self.current_value = current_value
        self.branch_name = branch_name

        step_name = getattr(step, "name", type(step).__name__)

        parts = [f"Pipeline execution failed at step {step_index} ({step_name})"]
        if branch_name is not None:
            parts[0] += f" in branch '{branch_name}'"
        parts.append(f"  {type(original_error).__name__}: {original_error}")

        if context_snapshot is not None:
            slots = getattr(context_snapshot, "list_slots", lambda: [])()
            if slots:
                parts.append(f"  Context slots: {[s.name for s in slots]}")

        if current_value is not None:
            parts.append(f"  Value type: {type(current_value).__name__}")

        super().__init__("\n".join(parts))
        self.__cause__ = original_error


__all__ = [
    "FrameworkError",
    "StructuredError",
    "CompileError",
    "LockError",
    "ComponentVersionError",
    "SlotTypeResolutionError",
    "SerializationContractError",
    "BlobSchemaError",
    "BlobVerificationError",
    "SpecVerificationError",
    "PipelineError",
    "CacheError",
    "ComponentExecutionError",
    "PipelineValidationError",
    "SlotNotFoundError",
    "SlotTypeError",
    "UndeclaredReadError",
    "ImmutabilityError",
    "BoundsViolationError",
    "LookaheadError",
    "ValidationError",
    "InvariantViolationError",
    "PipelineExecutionError",
    "SlotOverwriteError",
]
