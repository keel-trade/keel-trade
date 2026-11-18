"""Shared renderer move and screen bundle registration are executable proofs."""

import re
import subprocess
from pathlib import Path

from keel.widgets import CARD_KINDS, CARD_TOOLS, LISTED_EXCLUDED_CARDS, build_card_html


ROOT = Path(__file__).resolve().parents[1]
#: The card's served templates (`copy.card`), as the envelope will carry them.
CARD_COPY = {
    "pass_now": "{passing} of {of} coins",
    "tie": "{n} coins tie at {value} at the cutoff",
    "view_rev": "view revision {n}",
    "more_in_keel": "+{k} more in Keel",
    "show": "Show",
    "show_all": "All ({n})",
    "expand": "Open the interactive chart",
    "scrub": "Move through the window",
    "latest_note": "The numbers and the table show the latest values.",
    "sorted_within": "Sorted within the {n} shown.",
    "more_views": "More views in Keel",
}


def test_card_registration_and_sandbox():
    assert "screener" in CARD_KINDS and "screener" in LISTED_EXCLUDED_CARDS
    assert CARD_TOOLS["keel_screener_run"] == CARD_TOOLS["keel_screener_view"] == "screener"
    assert "keel_screener_passing" not in CARD_TOOLS
    html = build_card_html("screener")
    source = (ROOT / "keel/widgets/assets/card-screener.js").read_text()
    assert source and source in html
    for banned in ("fetch(", "XMLHttpRequest", "<iframe", "<img", "new Image"):
        assert banned not in source, banned
    assert "anchor_values" in source and "--sc-" in source


#: Identifiers the card compares against data, never shows as prose: the
#: published kinds (`graphs.kinds` values), the axis letters and a key name.
_DATA_WORDS = {"Lines", "Scatter", "Table", "Enter"}


def _string_literals(source: str) -> list[str]:
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    return re.findall(r'"((?:[^"\\\n]|\\.)*)"', code)


#: Space-separated tokens that are class lists or directives, not prose.
_CODE_TOKENS = {"use", "strict", "noopener", "noreferrer", "chart", "muted", "tall", "ml", "dim"}


def _english(literal: str) -> bool:
    if re.search(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}", literal) and not all(
        t.startswith("sc-") or t in _CODE_TOKENS for t in literal.split()
    ):
        return True
    return bool(re.fullmatch(r"[A-Z][a-z]+", literal)) and literal not in _DATA_WORDS


def test_card_carries_no_english_copy():
    """INV-53: every word on the screen card comes from the envelope. Seed:
    put `"pass now"` back as a literal → red. Not vacuous: the file has
    > 100 string literals and the detector flags its own positive controls."""
    source = (ROOT / "keel/widgets/assets/card-screener.js").read_text()
    literals = _string_literals(source)
    assert len(literals) > 100
    assert _english("of pass now") and _english("Waiting") and not _english("Lines")
    flagged = [lit for lit in literals if _english(lit)]
    assert flagged == [], flagged
    # spec 06 §D3.4: no freshness or ranking literal
    for word in ("LIVE", "not a recommendation", "lagging", "stalled", "expired"):
        assert word not in source, word


def test_drawmulti_move_svg_bytes_and_null_breaks():
    result = subprocess.run(
        ["node", "tests/fixtures/cards/c_drawmulti_move_check.mjs"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert '"unchanged_svg_cases":12' in result.stdout


#: The hero template's declared quadrant readings (Scatter(quadrants=…)), as
#: `copy.card.quadrants` serves them: keyed top_left … bottom_right.
QUADRANTS = {
    "top_left": "Longs paying, price falling",
    "top_right": "Longs paying, price rising",
    "bottom_left": "Shorts paying, price falling",
    "bottom_right": "Shorts paying, price rising",
}


def test_card_states_both_dialects_and_sizes(tmp_path):
    import json

    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes._screener_view import build_result

    source = json.loads((ROOT / "tests/fixtures/cards/screener_fallback.json").read_text())
    states = {
        size: build_result(source, ToolContext(), size=size).to_envelope()
        for size in ("view", "receipt")
    }
    # The served arm: the card's templates (`copy.card`, requested from the
    # envelope's copy bundle) and three published kinds. The unserved arm
    # above must render none of these words.
    served = json.loads(json.dumps(source))
    served["completed"]["copy"]["card"] = CARD_COPY
    served["completed"]["graphs"] = {
        "kinds": ["Lines", "River", "Scatter", "Table"],
        "default": "Lines",
        "top_control": False,
    }
    states["served"] = build_result(served, ToolContext(), size="view").to_envelope()
    # Round 4 (founder: share cards specific to the screen): a Scatter or a
    # Table default draws that graph inline, and only the passing coins
    # (`hero.passing`, the whole set) are coloured and named.
    # Q-3635: the card reads the filter off `passing` (count < total), draws
    # the set's rows in a Table and the set's legend over a Scatter.
    passing = ["ASSET28", "ASSET26"]
    for kind in ("Scatter", "Table"):
        env = json.loads(json.dumps(served))
        env["completed"]["graphs"]["default"] = kind
        env["completed"]["hero"]["passing"] = passing
        env["completed"]["headline"] = {"passing": len(passing), "of": 30}
        env["completed"]["passing"]["counts"]["passing"] = len(passing)
        if kind == "Scatter":
            # L2 ced050e701: the author's readings, served keyed by corner
            env["completed"]["copy"]["card"] = {**CARD_COPY, "quadrants": dict(QUADRANTS)}
        states[kind.lower()] = build_result(env, ToolContext(), size="view").to_envelope()
    # the fallback shape ships no `latest`: Table and Scatter read the 5 of
    # `latest_top` (spec 06 §D3.5)
    assert source["shape"] == "fallback"
    rows = len(source["completed"]["latest_top"]["rows"])
    states["failed"] = {
        "code": "unavailable",
        "message": "This result is unavailable.",
        "suggested_next_action": {},
    }
    path = tmp_path / "input.json"
    # 30 of 30 is no filter (Q-3635): no pass sentence; "+25 more" counts
    # the set (30) against the five legend entries, never the universe.
    words = ["+25 more in Keel"]
    expect = {
        "view": {"lines": 8, "absent": [*words, "pass now", "Show"]},
        "served": {
            "lines": 8,
            "present": words,
            "absent": ["pass now"],
            "tabs": 3,
            "tableRows": rows,
        },
        "scatter": {
            "dots": rows,
            "passing": len(passing),
            "nums": len(passing),
            "present": list(QUADRANTS.values()),
        },
        # a Table view draws the set's rows, no legend, no coloured row wash
        "table": {"rows": len(passing), "passing": 0, "nums": 0},
        "receipt": {},
        "failed": {},
    }
    path.write_text(
        json.dumps({"html": build_card_html("screener"), "states": states, "expect": expect})
    )
    result = subprocess.run(
        ["node", "tests/fixtures/cards/c_screener_states_check.mjs", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cases"] == 24


def test_card_in_an_mcp_apps_host_l13(tmp_path):
    """L13 host-harness defects D-1..D-6, in a sandboxed MCP Apps host.

    # SEED (L13): put `a.target = "_blank";` back on the action link in
    # card-screener.js (and drop the click handler) — the open-link arm reds;
    # restore the uniform `p.y -= over` shift — the label arm reds. Reverse.
    """
    import json

    from keel.tools.outcomes._base import ToolContext
    from keel.tools.outcomes._screener_view import build_result

    source = json.loads((ROOT / "tests/fixtures/cards/screener_card.json").read_text())
    source["completed"]["copy"]["card"] = {**CARD_COPY, "expand": "Open full size"}
    source["completed"]["graphs"] = {
        "kinds": ["Lines", "Scatter", "Table"],
        "default": "Lines",
        "top_control": False,
    }
    source["run"]["status"] = "completed"
    view = build_result(source, ToolContext(), size="view").to_envelope()
    view["actions"] = [
        {"label": "Open in Keel", "url": view["hero_url"]},
        {"label": "Test as a strategy", "url": view["hero_url"] + "&automate=price_level"},
    ]
    inflight_src = json.loads(json.dumps(source))
    inflight_src["completed"] = None
    inflight_src["run"]["status"] = "RUNNING"
    inflight = build_result(inflight_src, ToolContext(), size="view").to_envelope()
    # Staging, 2026-11-03: a 179-coin run passes everything; the stored list is
    # the first 50 by name and holds none of the ranked top, so the card drew
    # no numbers and "+179 more". Seed: drop the count check in passingSet.
    truncated = json.loads(json.dumps(view))
    top = [row[0] for row in truncated["latest_top"]["rows"]]
    truncated.setdefault("hero", {})["passing"] = [f"ZZ{i:02d}" for i in range(50)]
    truncated["headline"] = {"passing": 179, "of": 179}
    assert not set(top) & set(truncated["hero"]["passing"])
    # Seed: drop the `if (!pts.length) return;` guard in scatter().
    empty_scatter = json.loads(json.dumps(view))
    empty_scatter.setdefault("graphs", {})["default"] = "Scatter"
    empty_scatter["graphs"]["kinds"] = ["Scatter", "Lines", "Table"]
    empty_scatter["latest_top"]["rows"] = []
    empty_scatter.pop("latest", None)
    path = tmp_path / "input.json"
    path.write_text(
        json.dumps(
            {
                "html": build_card_html("screener"),
                "envs": {
                    "view": view,
                    "inflight": inflight,
                    "truncated": truncated,
                    "empty_scatter": empty_scatter,
                },
            }
        )
    )
    result = subprocess.run(
        ["node", "tests/fixtures/cards/c_screener_host_check.mjs", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["inline"]["links"] == 1 and out["inflight"] is True
    assert out["fullscreen"]["labels"] >= 2 and out["fullscreen"]["lines"] >= 10
    assert out.get("sort_buttons", 0) >= 1
    assert out["truncated_nums"] >= 1
    assert out["empty_scatter"]["nan"] == 0
