"""The released paper SDK, asserted for one test (S06 §3.11 item 1; PT2-28, PT2-91).

The paper tools landed dark behind ``_toolsets.PAPER_SDK_RELEASED`` and the
wheel's follow-up (PT2-91) committed it True. Arms that drive a paper tool, a
paper touch-up or the flag-on listed surface still ask for the
``paper_sdk_released`` fixture (``tests/conftest.py``), which wraps
:func:`paper_sdk_release`. It no longer releases anything: it asserts the
committed state the arm depends on, so a revert of the constant reds every
paper arm at its fixture instead of deep inside an assertion:

* ``PAPER_SDK_RELEASED`` is true;
* ``_DEFAULT_TOOLSETS`` and ``_base.ALL_TOOLSETS`` hold ``paper``;
* the registry holds every paper module's tool.

Seed: commit ``PAPER_SDK_RELEASED: bool = False`` ⇒ every arm that asks for
the fixture errors here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


PAPER_TOOLS = ("keel_paper_deploy", "keel_paper_control")


@contextmanager
def paper_sdk_release() -> Iterator[None]:
    import keel.tools.outcomes as outcomes
    import keel.tools.outcomes._base as base
    import keel.tools.outcomes._toolsets as toolsets

    outcomes._bootstrap()
    if toolsets.PAPER_SDK_RELEASED is not True:
        raise AssertionError("the paper SDK is committed released (PT2-91); the constant is not")
    missing_sets = [
        name
        for name, value in (
            ("_DEFAULT_TOOLSETS", toolsets._DEFAULT_TOOLSETS),
            ("ALL_TOOLSETS", base.ALL_TOOLSETS),
        )
        if toolsets.PAPER_TOOLSET not in value
    ]
    if missing_sets:
        raise AssertionError(f"the paper toolset is missing from {missing_sets}")
    missing_tools = [name for name in PAPER_TOOLS if name not in outcomes.OUTCOMES]
    if missing_tools:
        raise AssertionError(f"the paper tools are not registered: {missing_tools}")
    yield
