"""Shared constants for the pipeline engine.

Provides sentinel values used across multiple modules to avoid identity
divergence from independently created sentinels.
"""


class MissingType:
    """Type of the :data:`MISSING` sentinel — a copy-stable singleton.

    ``MISSING`` is always tested by identity (``x is MISSING``), so it MUST
    survive ``copy`` / ``deepcopy`` / ``pickle`` as *itself*.  A bare
    ``object()`` does not: ``copy.deepcopy(object())`` mints a **new** object,
    and every ``is MISSING`` test downstream of the copy then silently answers
    "no".  That is exactly how the DSL edit-apply soundness net
    (``dsl/edits.py`` ``spec_equal`` over a ``deepcopy``'d ``StrategyFile``)
    came to reject legitimate edits on any strategy declaring a ``def``
    factory with a non-defaulted parameter: the copied ``FactoryParam.default``
    was no longer ``MISSING``.

    ``__new__`` collapses re-instantiation onto the one instance, and
    ``__reduce__`` makes pickle round-trip to the module global, so there is
    exactly one sentinel however it is reconstructed.
    """

    __slots__ = ()

    _instance: "MissingType | None" = None

    def __new__(cls) -> "MissingType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "MISSING"

    def __copy__(self) -> "MissingType":
        return self

    def __deepcopy__(self, memo: dict) -> "MissingType":
        return self

    def __reduce__(self) -> str:
        return "MISSING"


# Sentinel for "no default provided" / "required parameter".
# Distinct from None so we can tell "required param" from "default is None".
MISSING = MissingType()

# Valid target timeframes for strategy pipelines.
# This is the single source of truth — the runtime TimeframeResampler only
# supports these values.  Update this set when adding new timeframe support.
VALID_TIMEFRAMES = frozenset(
    {
        "15min",
        "30min",
        "1h",
        "2h",
        "3h",
        "4h",
        "6h",
        "8h",
        "12h",
        "1d",
    }
)


__all__ = ["MISSING", "VALID_TIMEFRAMES", "MissingType"]
