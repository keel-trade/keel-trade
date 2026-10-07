"""Shared constants for the pipeline engine.

Provides sentinel values used across multiple modules to avoid identity
divergence from independently created sentinels.
"""

from timeframes import VALID_TIMEFRAMES  # noqa: F401 — re-export; see below


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

# Valid target timeframes for strategy pipelines — the KEY SET of the clock
# alphabet, re-exported from ``libs/timeframes`` rather than mirrored.
#
# It used to be a hand-written frozenset, and the comment here explained why:
# ``dsl/validator.py``'s Globals gate wants a membership test, and importing
# the minutes table from ``validation_shared`` would have been circular. That
# reason is gone. The alphabet now lives in ``libs/timeframes``, a stdlib-only
# leaf package that imports nothing from ``pipeline_engine``, so the membership
# set can come straight from the source with no cycle to route around.
#
# The mirror had already cost us once: ``3h`` sat in this set while
# ``TIMEFRAME_MINUTES`` lacked it, so a strategy could declare a timeframe the
# scheduler could not convert to minutes (see
# ``services/keel-api/tests/test_schedule.py``). A parity test made that drift
# a red; deriving makes it unrepresentable, which is strictly better than a
# test that catches it afterwards. The import sits at the top of the file.


__all__ = ["MISSING", "VALID_TIMEFRAMES", "MissingType"]
