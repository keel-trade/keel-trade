"""The help command resolves the source-owned screener grammar after SDK regen."""

from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext


def test_help_screener_is_bundled_reference():
    _bootstrap()
    result = OUTCOMES["keel_help"].handler({"topic": "screener"}, ToolContext())
    assert result.extra["topic"] == "screener"
    assert "Outputs" in str(result.to_envelope())


def test_the_screen_words_an_agent_asks_by_reach_the_screen_docs():
    """Q-3637: `keel_help("screener_view")` was not_found on claude.ai; the
    screen and its view are reachable by the nouns agents use.

    # SEED (2026-11-05): drop the `<!-- topic: screener_view -->` line from
    # results.views.generate.view_language and regenerate — the first row
    # reds not_found. Reverse the edit and regenerate."""
    _bootstrap()
    rows = {
        "screener_view": "view_language",
        "screen_view": "view_language",
        "screens": "screener",
        "screen": "screener",
        "screen-recipes": "screen_recipes",
    }
    for asked, served in rows.items():
        result = OUTCOMES["keel_help"].handler({"topic": asked}, ToolContext())
        assert result.extra["topic"] == served, asked


def test_the_screener_reference_points_to_the_view_syntax_and_the_recipes():
    """Q-3637: the screener topic never named `view_language`, so an agent
    reading it had no route to the view syntax or the worked screens."""
    _bootstrap()
    body = OUTCOMES["keel_help"].handler({"topic": "screener"}, ToolContext()).extra["body"]
    text = " ".join(body.split())  # the doc wraps its prose
    assert "the `view_language` topic is its complete syntax" in text
    assert "`screen_recipes` topic is worked screens" in text
    # The order the founder's test got wrong: run before reading, and the
    # stored revision, never a guessed one.
    assert "the order is save, run, then read" in text
    assert "`expected_revision` (never a guessed number)" in text
