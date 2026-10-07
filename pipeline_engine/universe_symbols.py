"""The ONE reading of "which symbols does this Universe trade" (Q-1147).

Every runtime and every submit-time guard used to read ``Universe.resolved``
and nothing else, while the write-time validators admitted a manual basket
with ``symbols`` and no ``resolved`` on the strength of a claim — "the
resolver derives resolved from symbols" — that described no code. This
module IS that code, held in one place so the resolver
(``dsl/resolver.py``), the backtest and eval workers, and keel-api's
``assert_universe_resolved`` cannot disagree again about what a declaration
means.

Rules, in order:

1. ``resolved`` present (non-None) → it is the answer, verbatim — including
   an explicit ``[]``, which means "resolved to zero assets" and stays a
   refusal (``EMPTY_UNIVERSE``) for every caller. Never widened here.
2. ``mode == "manual"`` with ``symbols`` and ``resolved`` absent → the typed
   basket: ``symbols`` minus ``exclusions`` plus ``inclusions``, de-duplicated
   in declaration order. No venue call — a manual basket names the traded set
   exactly, so there is nothing to resolve.
3. Anything else → ``None`` ("not resolved"): a criteria mode that has not
   been resolved yet, or a manual declaration with no symbols.

Accepts the DSL ``UniverseSpec`` and the compiled-blob ``universe`` dict
(``dsl/resolver.py`` persists the same keys), so one helper serves both the
parse-time and the blob-time readers.
"""

from __future__ import annotations

from typing import Any


__all__ = ["effective_universe_symbols", "manual_basket"]


def _field(universe: Any, name: str) -> Any:
    if isinstance(universe, dict):
        return universe.get(name)
    return getattr(universe, name, None)


def _symbol_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"Universe symbol field must be a list, got {type(raw).__name__}")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Universe symbol lists must contain non-blank strings")
        out.append(item.strip())
    return out


def manual_basket(
    symbols: list[str] | tuple[str, ...] | None,
    exclusions: list[str] | tuple[str, ...] | None = None,
    inclusions: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """``symbols`` minus ``exclusions`` plus ``inclusions``, order preserved,
    de-duplicated. The manual-mode basket rule, shared by the resolver's
    runtime fallback and keel-api's save-time bake so the stored
    ``resolved`` and the runtime reading are the same list."""
    excluded = set(_symbol_list(exclusions))
    out: list[str] = []
    seen: set[str] = set()
    for symbol in _symbol_list(symbols):
        if symbol in excluded or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    for symbol in _symbol_list(inclusions):
        if symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


def effective_universe_symbols(universe: Any) -> list[str] | None:
    """The symbols a Universe declaration trades, or ``None`` when it has no
    answer yet (see the module docstring for the three rules).

    ``universe`` is a ``UniverseSpec`` or the compiled-blob dict; ``None``
    (no declaration) returns ``None``.
    """
    if universe is None:
        return None
    resolved = _field(universe, "resolved")
    if resolved is not None:
        return _symbol_list(resolved)
    mode = _field(universe, "mode") or "manual"
    symbols = _field(universe, "symbols")
    if mode == "manual" and symbols:
        return manual_basket(
            symbols, _field(universe, "exclusions"), _field(universe, "inclusions")
        )
    return None
