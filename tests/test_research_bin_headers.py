"""AUDIT-NUMBERS S2-k: a Distribution's rows read, in the agent's and the CLI's table, with its bin ends named by the
binned measure ("Rank IC from", "Rank IC to") and every cell in keel-api's served words, never "bin_hi" or a float.

SEED: the ``binned`` block in ``data_text`` removed: the header arm reds ("bin_hi"); the worded-cell control stays
green (its words are keel-api's, ``?words=true``)."""

from __future__ import annotations

from keel.tools.outcomes._research_view import data_text


def test_bin_ends_read_as_the_binned_measures_range() -> None:
    page = {
        "data": [
            {
                "bin_lo": -0.024359925788497216,
                "bin_hi": -0.0205,
                "count": 3,
                "share": 0.0031,
                "words": {"bin_lo": "−0.024", "bin_hi": "−0.021", "count": "3", "share": "0.3%"},
            }
        ],
        "facts": {
            "rows_total": 1,
            "axes": [{"name": "bin_lo", "label": "Bin"}],
            "measures": [{"name": "count", "label": "Count"}, {"name": "share", "label": "Share of all"}],
            "bins": {"label": "Rank IC", "unit": "score"},
        },
    }
    text = data_text(page, title="Against chance", version=1, call_args={})
    assert "| Rank IC from | Rank IC to | Count | Share of all |" in text, text
    assert "bin_hi" not in text.split("Keys for")[0]
    # control: the cells are the served words
    assert "| −0.024 | −0.021 | 3 | 0.3% |" in text
    assert "0.024359925788497216" not in text
