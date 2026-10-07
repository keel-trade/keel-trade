"""The receipt-row fit guards (Q-1771, Q-1772).

The one-row receipt (BUILD §4.1) switches on at 480 px and, until Q-1771, had
no overflow strategy: only the name could shrink, so between ~480 and ~640 px
everything after it ran off the card and was clipped — the real-host sweep
(claude.ai, 2026-09-22) read "Show result" as "Sho", "Show pipeline" as "S",
a diff chip as "0.", and at 560 px a compose receipt's "Open" as "Op".

The rule under test (BUILD §4 / runbook B11): the actions (Open ↗, Show …)
are never clipped, the state chip is never dropped, and lower-priority items
go WHOLE — superseded chip, then change chip, then provenance / window /
count chip, then the three numbers — before the name is squeezed past an
ellipsis. A change chip sheds its block path before anything goes (Q-1795,
Q-1796), and provenance goes before the name is cut at all (Q-1797).

``tests/fixtures/cards/c_receipt_fit_check.mjs`` renders every receipt fixture
(and the same fixture under a real-length name) in real Chromium at the widths
the sweep measured and reads the row's geometry. Skips (never silently passes)
when node or Playwright is absent.

Proof it can fail: ``# SEED:`` below, run 2026-09-22 (recorded in the commit).
Proof it is not vacuous: the scan covers every fixture × width, at least one
481 px arm really had to drop something (so the fit ran against a real
overflow), and at 720 px nothing was dropped for the short names (so the
fit does not simply drop everything).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest
from keel.widgets import CARD_KINDS, build_card_html


HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "cards"
CHECK = FIXTURES / "c_receipt_fit_check.mjs"
PLAYWRIGHT = HERE.parents[3] / "services" / "keel-app" / "node_modules" / "playwright"

#: The width at and above which the receipt is ONE row.
ONE_ROW_PX = 481
#: How much of the name must survive before a droppable item may stay.
NAME_ROOM_PX = 120
#: One monospace glyph at the receipt's name size.
GLYPH_PX = 9
SUBJECTS = ("btCompleted", "btFailed", "btRunning", "stSave", "stDecl", "stPreview")


@pytest.fixture(scope="module")
def fit(tmp_path_factory) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the receipt fit is a layout rule")
    if not PLAYWRIGHT.exists():
        pytest.skip(f"Playwright is not installed at {PLAYWRIGHT}")
    cards = tmp_path_factory.mktemp("fit-cards")
    for kind in CARD_KINDS:
        (cards / f"{kind}.html").write_text(build_card_html(kind), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(CHECK), "--cards", str(cards), "--fixtures", str(FIXTURES)],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=HERE.parents[4],
    )
    if not proc.stdout.strip():
        pytest.fail(f"the receipt fit check produced no measurements.\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def _arms(fit: dict, *, min_width: int = 0, max_width: int = 10**6):
    for key, m in sorted(fit["arms"].items()):
        subject, width = key.split("@")
        if min_width <= int(width) <= max_width:
            yield key, subject.removesuffix("Long"), int(width), m


def _never_dropped(m: dict) -> list[str]:
    """What a row may never lose: everything that is not droppable."""
    keep = []
    for p in m["parts"]:
        cls = p["cls"]
        if "rr-act" in cls or "brand" in cls or "rr-name" in cls or "rr-err" in cls:
            keep.append(p["text"])
        elif "chip" in cls and "muted" not in cls:
            keep.append(p["text"])  # version, state, Preview, validity
    return keep


def _founder(fit: dict) -> list[str]:
    """The founder-shaped arms (Q-1797): a real name, real edits."""
    return [k for k in fit["arms"] if k.startswith("mr")]


def test_the_fit_scan_was_not_vacuous(fit: dict) -> None:
    founder = _founder(fit)
    superseded = [k for k in fit["arms"] if "Sup@" in k and k not in founder]
    copy = [k for k in fit["arms"] if k.startswith("copy")]
    assert len(copy) == 2
    assert len(fit["arms"]) == (
        len(SUBJECTS) * len(fit["widths"]) * 2 + len(superseded) + len(copy) + len(founder)
    )
    assert len(superseded) == 6
    assert len(founder) == 15
    # The superseded arms really are superseded: the chip is in the DOM.
    # A dry run's draft check (Q-1848) has no other droppable item, so its
    # one droppable IS the superseded chip; every other receipt carries one
    # of its own beside it.
    for k in superseded:
        floor = 1 if k.startswith("stPreview") else 2
        assert fit["arms"][k]["droppable"] >= floor, k
    assert all(m.get("row") for m in fit["arms"].values()), "a receipt did not render"
    at_481 = [m for k, _, w, m in _arms(fit) if w == ONE_ROW_PX]
    assert any(m["dropped"] for m in at_481), "no 481 px arm overflowed — the fit never ran"
    for subject in SUBJECTS:
        wide = fit["arms"][f"{subject}@720"]
        assert wide["dropped"] == [], (subject, wide["dropped"])


def test_actions_are_never_clipped(fit: dict) -> None:
    """Every action sits wholly inside the card and shows its whole label."""
    # SEED: make `fitRow` return on its first line — at 481/560 px the
    # actions run past the card's right edge and this reds.
    for key, subject, _, m in _arms(fit, min_width=ONE_ROW_PX):
        wide = fit["arms"][f"{key.split('@')[0]}@720"]
        assert [a["text"] for a in m["acts"]] == [a["text"] for a in wide["acts"]], key
        for a in m["acts"]:
            assert a["box"], (key, a["text"], "hidden")
            assert a["box"]["r"] <= m["inner"]["r"] + 0.5, (key, a["text"], a["box"], m["inner"])
            assert a["box"]["sw"] <= a["box"]["w"] + 1, (key, a["text"], "label clipped")
        assert m["row"]["sw"] <= m["row"]["w"] + 1, (key, "row overflows")
        assert m["overflowX"] == 0, key


def test_the_state_and_identity_are_never_dropped(fit: dict) -> None:
    """Name, version, state chip, error and actions survive at every width."""
    for key, _, _, m in _arms(fit, min_width=ONE_ROW_PX):
        wide = fit["arms"][f"{key.split('@')[0]}@720"]
        texts = [p["text"] for p in m["parts"]]
        for must in _never_dropped(wide):
            assert must in texts, (key, must)


def test_droppable_items_go_before_the_name_is_squeezed(fit: dict) -> None:
    """While anything droppable is still shown, the name keeps its room."""
    for key, _, _, m in _arms(fit, min_width=ONE_ROW_PX):
        name = m["name"]
        # Cut by CSS (a trailing ellipsis) or in the middle (Q-1780).
        truncated = name["sw"] > name["w"] + 1 or m["nameText"] != m["nameFull"]
        # A middle cut shrinks the element to its (shorter) text, which is
        # up to one glyph narrower than the room it was given.
        if truncated and name["w"] < NAME_ROOM_PX - GLYPH_PX:
            assert len(m["dropped"]) == m["droppable"], (key, name, m["dropped"])


# ── Below 480 px: two deliberate lines (Q-1772) ─────────────────────────────


def _centre(p: dict) -> float:
    return (p["box"]["t"] + p["box"]["b"]) / 2


def test_the_narrow_receipt_is_two_deliberate_lines(fit: dict) -> None:
    """Line 1 is who it is (name · version · state); line 2 is what it says,
    with the actions pinned to the card's right edge. The old wrap of one
    flat row put the link pair on a third line, floating mid-card."""
    # SEED: in card.css delete the narrow `.rr .rr-l1, .rr .rr-l2 {
    # display: flex … }` rule — the groups stay `contents`, the row wraps
    # as before and the line assertions red.
    checked = 0
    for key, subject, _, m in _arms(fit, max_width=ONE_ROW_PX - 2):
        line1 = [p for p in m["parts"] if p["grp"] == 1]
        line2 = [p for p in m["parts"] if p["grp"] == 2]
        assert line1 and line2, (key, "the row has no line groups")
        for line in (line1, line2):
            centres = [_centre(p) for p in line]
            assert max(centres) - min(centres) <= 4, (key, [p["text"] for p in line])
        assert max(p["box"]["b"] for p in line1) <= min(p["box"]["t"] for p in line2) + 1, key
        assert "rr-name" in line1[1 if "brand" in line1[0]["cls"] else 0]["cls"], key
        acts = [p for p in line2 if "rr-act" in p["cls"]]
        if subject.startswith("stPreview"):
            # A dry run's draft check has no actions at all (Q-1848).
            assert not acts, (key, [a["text"] for a in acts])
        else:
            assert acts, key
            assert abs(acts[-1]["box"]["r"] - m["inner"]["r"]) <= 1, (
                key,
                "actions not at the edge",
            )
        for a in m["acts"]:
            assert a["box"]["sw"] <= a["box"]["w"] + 1, (key, a["text"], "label clipped")
        assert m["overflowX"] == 0, key
        checked += 1
    assert checked >= len(SUBJECTS) * 2 * 3


def test_chip_order_is_the_same_on_every_card(fit: dict) -> None:
    """name · version · state · (superseded) · (change): the superseded chip
    follows the state group and precedes any change or numbers — a preview
    used to read `superseded by v5 · Preview`."""
    # SEED: in card-strategy.js push the superseded text into `spec.chips`
    # ahead of the preview chips (the pre-Q-1772 order) — reds on stPreviewSup.
    seen = 0
    for key, m in fit["arms"].items():
        texts = [p["text"] for p in m["parts"]]
        sup = [i for i, t in enumerate(texts) if t.startswith("superseded")]
        if not sup:
            continue
        seen += 1
        i = sup[0]
        state_like = [
            j
            for j, p in enumerate(m["parts"])
            if "chip" in p["cls"] and "muted" not in p["cls"] and not p["text"].startswith("v")
        ]
        change = [
            j for j, p in enumerate(m["parts"]) if "rr-sc" in p["cls"] or "rr-nums" in p["cls"]
        ]
        assert all(j < i for j in state_like), (key, texts)
        assert all(j > i for j in change), (key, texts)
    assert seen >= 1, "no superseded chip was ever on screen"


# ── Provenance yields to the name (Q-1797) ──────────────────────────────────


def test_provenance_goes_before_the_name_is_cut(fit: dict) -> None:
    """While the provenance line ("Draft · updated Sep 23") is on screen, the
    one-line name is whole: provenance drops before the name shrinks below
    its natural width, not merely before it reaches the 120 px floor."""
    # SEED: delete the soft pass (`if (!rowFits(row) || nameSqueezed(row))
    # { … data-soft … }`) from fitRow — mrRead@560/640 keep the provenance
    # beside a cut name and this reds.
    carrying = [k for k, m in fit["arms"].items() if m["softMade"]]
    # Non-vacuity: the provenance item is MADE on every read arm, and at
    # 1000 px it has room and is shown (the rule does not simply hide it).
    assert len(carrying) >= 7, carrying
    assert fit["arms"]["mrRead@1000"]["softShown"] == 1
    for key in carrying:
        m = fit["arms"][key]
        if m["softShown"] and m["oneLine"]:
            assert not m["nameCut"], (key, m["nameText"])


def test_a_superseded_read_keeps_its_name_and_its_supersession(fit: dict) -> None:
    """The founder's v1: at a claude.ai width the row says who it is and
    that v3 replaced it; the provenance line is what goes."""
    m = fit["arms"]["mrReadSup@720"]
    shown = [p["shown"] for p in m["parts"]]
    assert "superseded by v5" in shown, shown
    assert m["softMade"] == 1 and m["softShown"] == 0, m["said"]
    assert m["name"]["w"] >= NAME_ROOM_PX, m["name"]


# ── A block-param change stays on the row (Q-1795) ──────────────────────────

#: The widths a claude.ai / ChatGPT card is actually drawn at.
HOST_WIDTHS = (560, 640, 720)


def _change_chip(m: dict) -> str | None:
    chips = [p["shown"] for p in m["parts"] if "rr-sc" in p["cls"]]
    return chips[0] if chips else None


def test_a_block_param_change_keeps_its_chip_at_host_widths(fit: dict) -> None:
    """v2 of the founder's strategy changed `TimeSeriesMeanReversionForecast`
    `k 20 → 10`; its receipt read only "v2 valid". At a host width the chip
    stays, shedding the long block path before it would go whole."""
    # SEED: delete the `if (p === 2 && compactable.length) { … }` stage in
    # fitRow — the chip goes whole at 560/640 and this reds.
    for w in HOST_WIDTHS:
        m = fit["arms"][f"mrSave@{w}"]
        chip = _change_chip(m)
        assert chip is not None, (w, [p["shown"] for p in m["parts"]])
        key = chip.split(" · ")[-1]
        assert key.startswith("k") and "20" in key and "10" in key, (w, chip)
    # The whole path is still there when the row has room: compaction is a
    # fit step, not the chip's only shape.
    wide = fit["arms"]["mrSave@1000"]
    assert wide["compact"] == 0, wide["parts"]
    assert "TimeSeries" in (_change_chip(wide) or ""), wide["parts"]


def test_the_block_param_arms_carry_one_change_chip(fit: dict) -> None:
    """Non-vacuity: every mrSave arm MADE the chip (it is in the DOM, shown
    or dropped) — what the seed moves is only whether it is on screen."""
    for key, m in fit["arms"].items():
        if key.startswith("mrSave@"):
            assert m["droppable"] >= 1, key
            assert "rr-sc" in " ".join(m["dropped"]) or _change_chip(m), key


# ── A superseded save says what it was AND that it was replaced (Q-1796) ──


def test_a_superseded_save_keeps_both_its_change_and_its_supersession(fit: dict) -> None:
    """The founder's v2, once v3 was saved: the row read "v2 valid" — the
    superseded chip (priority 1) went first and the change chip after it.
    At a claude.ai width both stay; the change chip sheds its block path
    before anything is dropped."""
    # SEED: move fitRow's compaction back inside the drop loop at p === 2 —
    # the superseded chip is dropped at 720 and this reds.
    m = fit["arms"]["mrSaveSup@720"]
    shown = [p["shown"] for p in m["parts"]]
    assert "superseded by v4" in shown, shown
    chip = _change_chip(m)
    assert chip is not None and chip.split(" · ")[-1].startswith("k"), shown
    assert m["name"]["w"] >= NAME_ROOM_PX - GLYPH_PX, m["name"]
    # Non-vacuity: the superseded chip is MADE on every mrSaveSup arm (the
    # election reached the receipt), and at 1000 px nothing is dropped.
    for w in (640, 720, 1000):
        assert any(t.startswith("superseded") for t in fit["arms"][f"mrSaveSup@{w}"]["said"]), w
    assert fit["arms"]["mrSaveSup@1000"]["dropped"] == []


# ── What the row says (Q-1775) ──────────────────────────────────────────────


def test_a_read_does_not_say_saved(fit: dict) -> None:
    """A `keel_strategy_get` receipt has no change to show and saved
    nothing; it says its provenance line, never "Saved."."""
    # SEED: restore `else spec.note = "Saved.";` in card-strategy.js's
    # renderReceipt — reds here.
    m = fit["arms"]["copyStGet@720"]
    assert "Saved." not in m["said"], m["said"]
    assert m["statusText"] and m["statusText"] in m["said"], (m["statusText"], m["said"])


def test_the_same_version_below_is_not_a_supersession(fit: dict) -> None:
    """A run's receipt followed by its own summary (same object, same
    version) says the run is shown below — not "superseded by v3" on v3.
    CONTROL: a younger sibling at a newer version still says superseded."""
    # SEED: make `supersededLabel` skip the same-version branch — reds here.
    same = fit["arms"]["copyBtSame@720"]["said"]
    assert "shown below" in same, same
    assert not any(t.startswith("superseded") for t in same), same
    newer = fit["arms"]["btCompletedSup@720"]["said"]
    assert any(t.startswith("superseded by v") for t in newer), newer
