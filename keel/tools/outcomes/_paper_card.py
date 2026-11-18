"""The paper receipt's card-only facts (Q-7603; paper-trading-v2 S06 §3.5.6).

``keel_paper_deploy`` and ``keel_paper_control`` draw the ``live`` card (no new
card kind). Their envelope (IC-06-1, IC-06-2) carries what the model needs; the
card also needs what a reader looks for first on a run that has no fills yet:
the strategy's name and version, the clock it runs on, when it runs next and
its starting balance. keel-api's own answer carries those (``name``,
``deployed_version_string``, ``evaluation_grid``, ``next_run_at``), and this
module words them once, here, so the card never prints a raw stamp or an id.

The block rides as ``render.paper``. ``render`` is a ``card`` row of the live
channel spec (``_channels._LIVE``), so under the card-meta arm (prod) it moves
to ``_meta["keel/card"]`` and the text block the model reads is unchanged. With
the arm off (the local default) it stays in the envelope, as the monitor's own
``render`` block always has.
"""

from __future__ import annotations

from typing import Any


def clock_words(grid: Any) -> str | None:
    """``{"period_minutes": 60}`` → ``"1h"``: the timeframe alphabet's spelling
    (``15min``, ``1h``, ``4h``, ``1d``). ``None`` for a grid keel-api could not
    invert (an irregular schedule), never a guess from the cron."""
    minutes = grid.get("period_minutes") if isinstance(grid, dict) else None
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        return None
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}min"


def paper_card_block(
    row: Any, *, state: str | None, simulated_capital: Any = None
) -> dict[str, Any]:
    """``{"paper": {...}}`` for one paper write's ``render`` block.

    ``row`` is keel-api's deployment row as the route answered it (``None`` for
    a stop, which answers 204). ``state`` is keel-app's word for where the run
    now stands (``paper_status_word``). Every key is present only when its fact
    is: the card draws what is known and nothing for what is not.
    """
    from .live_monitor import _utc_words

    row = row if isinstance(row, dict) else {}
    facts: dict[str, Any] = {}
    name = row.get("name") or row.get("strategy_name")
    if isinstance(name, str) and name.strip():
        facts["name"] = name.strip()
    version = row.get("deployed_version_string")
    if isinstance(version, str) and version.strip():
        facts["version"] = version.strip()
    if state:
        facts["state"] = state
    clock = clock_words(row.get("evaluation_grid"))
    if clock:
        facts["clock"] = clock
    next_run = _utc_words(row.get("next_run_at"))
    if next_run:
        facts["next_run"] = next_run
    capital = row.get("simulated_capital", simulated_capital)
    if isinstance(capital, str):
        # FastAPI serves a Decimal as a string.
        try:
            capital = float(capital)
        except ValueError:
            capital = None
    if isinstance(capital, (int, float)) and not isinstance(capital, bool):
        facts["capital"] = capital
    return {"paper": facts}
