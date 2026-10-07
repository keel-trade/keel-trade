"""The card fixtures must carry what the SERVER sends (Q-1719 / Q-1720 / Q-1721).

Every card capture a reviewer has ever looked at, and every card rule with a
guard, is driven by `tests/fixtures/cards/*.envelope.json`. Those files were
hand-written from the BUILD spec. On 2026-09-22 a lane rendered the same cards
from the envelopes staging actually produced and diffed the two, and three of
the differences were the fixture being WRONG rather than merely different:

* **Q-1720** — `view.categories` chipped `ForecastScaler` and `ForecastCapper`
  as `regime_detector`; the live component registry, which the server reads at
  compose time, says `forecast_mapper`. Every fixture-driven review of the
  strategy card had been judging two category chips the platform does not use.
* **Q-1721** — `view.change.summary_text` read `1 changed · ROC period 10 → 20`
  where the wire sends `1 changed`, in four separate saves. Neither producer
  can emit the richer line: the `·` phrase grammar belongs to
  `_change_summary`'s DECLARATION half, and those fixtures move no declaration.
* **Q-1719** — the window. See the docstring on its test below; the wire half
  is Q-1709, fixed in `4068c5ede`.

The guards below therefore pin each field to the code that PRODUCES it — the
live registry, the live `_change_summary`, the live `window_block` — rather
than to a literal typed here, so a fixture cannot drift from the server and a
server change that should invalidate a fixture reds instead of shipping.

Proof each can fail: every test carries a `SEED:` comment naming the one-line
fixture edit that reds it (they are the ORIGINAL values, so each seed restores
the defect this module was written for). Run on 2026-09-22 and reversed.
Proof they are not vacuous: every test asserts how many subjects it walked and
that the producer it compares against is live — a registry that failed to load,
or a `_change_summary` that returned nothing, would otherwise make each test
pass over an empty population.
"""

from __future__ import annotations

import json
import pathlib
import re


HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "cards"

#: The differ's own block tally — `1 changed`, `1 added | 1 changed`
#: (`_strategy_view._change_summary`'s docstring names both forms).
_TALLY = re.compile(r"^\d+ (?:added|removed|changed|reordered)$")


def _envelopes() -> dict[str, dict]:
    out = {}
    for path in sorted(FIXTURES.glob("*.envelope.json")):
        env = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(env, dict):
            out[path.name] = env
    return out


def _windows(env: object, trail: tuple[str, ...] = ()) -> list[tuple[str, dict]]:
    """Every `window` block anywhere in an envelope, with its dotted path."""
    found: list[tuple[str, dict]] = []
    if isinstance(env, dict):
        for key, value in env.items():
            if key == "window" and isinstance(value, dict) and "start" in value:
                found.append((".".join((*trail, key)), value))
            found.extend(_windows(value, (*trail, key)))
    elif isinstance(env, list):
        for i, value in enumerate(env):
            found.extend(_windows(value, (*trail, str(i))))
    return found


# ── Q-1720 · the category chips ─────────────────────────────────────────────


def test_every_fixture_category_is_the_registry_s() -> None:
    """The chips are the card's only statement of what each block IS.

    `_strategy_view.categories_for` builds `view.categories` from the bundled
    registry (`keel/data/registry.json`) — the same generated projection every
    other SDK surface reads — so a fixture value the registry disagrees with is
    a taxonomy the platform does not use, shown to every reviewer.

    SEED: put `"ForecastScaler": "regime_detector"` back into
    `strategy_receipt.envelope.json` — this reds naming the file, the
    component, the fixture's value and the registry's.
    """
    from keel.data.registry import _load_json

    components = _load_json("registry.json").get("components")
    assert isinstance(components, list) and components, "the bundled registry is empty"
    registry = {
        str(c["name"]): c.get("category")
        for c in components
        if isinstance(c, dict) and c.get("name")
    }
    # Not vacuous: the registry really does know the components the fixtures
    # name, so a mismatch below is a wrong CATEGORY rather than an unknown
    # component quietly skipped.
    checked = 0
    carriers = 0
    for name, env in _envelopes().items():
        view = env.get("view")
        categories = view.get("categories") if isinstance(view, dict) else None
        if not isinstance(categories, dict) or not categories:
            continue
        carriers += 1
        for component, declared in sorted(categories.items()):
            assert component in registry, f"{name}: {component} is not in the registry"
            assert declared == registry[component], (
                f"{name}: {component} is chipped {declared!r}; "
                f"the registry says {registry[component]!r}"
            )
            checked += 1
    assert carriers >= 8, f"only {carriers} fixtures carry categories"
    assert checked >= 100, f"only {checked} component chips checked"


# ── Q-1721 · the change summary ─────────────────────────────────────────────


def test_no_fixture_summary_is_richer_than_the_server_can_write() -> None:
    """`view.change.summary_text` has exactly two producers, and both are read
    here rather than described.

    The line is `_strategy_view._change_summary(diff, declarations,
    blocks_touched)`: the differ's own BLOCK TALLY (`1 changed`,
    `1 added | 1 changed`), then one `Section · label a → b` phrase per moved
    declaration, joined with ` | `. So a `·` phrase can only come from a
    declaration — and `strategy_receipt` carried `1 changed · ROC period 10 →
    20` with no declarations at all, a line the server cannot produce.

    That matters beyond tidiness: Q-1700 is an open finding about this card
    saying the same thing twice, and whoever resolves it will look at a
    capture to decide whether the duplication is gone. A fixture-driven
    capture showing a summary the server never sends makes that decision about
    a card that does not exist.

    SEED: put `"1 changed · ROC period 10 → 20"` back into
    `strategy_receipt.envelope.json` — this reds naming the segment and the
    empty declaration set.
    """
    from keel.tools.outcomes._strategy_view import _change_summary

    # Not vacuous, and it pins the comparison to the live producer: the
    # declaration half really does emit a `·` phrase.
    probe = _change_summary({}, {"execution": {"buffer_threshold": {"a": 1, "b": 2}}}, 0)
    assert probe and "·" in probe and "→" in probe, probe

    carriers = 0
    for name, env in _envelopes().items():
        view = env.get("view")
        change = view.get("change") if isinstance(view, dict) else None
        if not isinstance(change, dict):
            continue
        summary = change.get("summary_text")
        if summary is None:
            continue
        carriers += 1
        declarations = change.get("declarations")
        declarations = declarations if isinstance(declarations, dict) else {}
        expected_phrases = _change_summary({}, declarations, 0)
        phrases = expected_phrases.split(" | ") if expected_phrases else []

        segments = summary.split(" | ")
        tail = segments[len(segments) - len(phrases) :] if phrases else []
        assert tail == phrases, (
            f"{name}: declaration phrases are {tail}; _change_summary builds {phrases}"
        )
        for segment in segments[: len(segments) - len(phrases)]:
            assert _TALLY.match(segment), (
                f"{name}: {segment!r} is not a block tally, and "
                f"view.change.declarations is {declarations or 'empty'} — "
                "the server cannot write this line"
            )
    assert carriers >= 4, f"only {carriers} fixtures carry a change summary"


# ── Q-1719 · the window ─────────────────────────────────────────────────────


def test_every_fixture_window_is_what_window_block_would_emit() -> None:
    """`view.window` is a DATE pair, and the fixtures are a fixed point of the
    one owner that builds it.

    The entry was filed while the wire sent
    `"2024-07-27 00:00:00+00:00"` and every fixture sent `"2024-08-15"` — the
    most frequent single shape difference in the corpus (10 paths across 5
    pairs). Between the filing and this fix the WIRE half landed (Q-1709,
    `4068c5ede`): `window_block(start, end)` in `_backtest_view` is now the
    one owner, all three writers read it, and a value that is not a date
    becomes `None` rather than being passed through.

    That changes what the corpus half has to be. The fixtures are no longer
    the odd ones out — they are now the shape the contract states — so the
    useful guard is not "add a datetime fixture" (the wire can no longer send
    one, and a fixture carrying a shape the contract forbids would teach the
    next reader the wrong thing). It is this: every window in the corpus is
    EXACTLY what the live producer would emit for the same inputs. A fixture
    cannot drift back to the wire's old shape, and a change to `window_block`
    that should invalidate the corpus reds here instead of shipping.

    The shape Q-1709 newly made reachable — a `None` member, which no fixture
    carried — is covered on the DOM side by
    `test_widgets_cadence.py::test_a_window_the_server_could_not_date_draws_no_stamp`.

    SEED: put `"start": "2024-07-27 00:00:00+00:00"` back into
    `c_bt_receipt_completed.envelope.json` — this reds naming the file, the
    path, the fixture's value and the producer's.
    """
    from keel.tools.outcomes._backtest_view import window_block

    # Not vacuous, and nothing in a fixture can move it: the producer really
    # does normalise, so "fixture == producer" below is a claim about the
    # SHAPE rather than an identity that would hold for any input.
    assert window_block("2024-07-27 00:00:00+00:00", "2026-09-22 00:00:00+00:00") == {
        "start": "2024-07-27",
        "end": "2026-09-22",
        # Additive (spec 03 §2.5): the last covered day and the day count.
        "last_bar": "2026-09-21",
        "days": 787,
    }

    checked = 0
    for name, env in _envelopes().items():
        for path, window in _windows(env):
            produced = window_block(window.get("start"), window.get("end"))
            assert produced == window, f"{name}: {path} is {window}; window_block emits {produced}"
            checked += 1
    assert checked >= 12, f"only {checked} window blocks walked"
