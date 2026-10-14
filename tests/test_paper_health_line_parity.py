"""The practice health line: the SDK's phrase map and keel-app's cannot drift
(paper-trading-v2 S05 IC-05-6, S06 §3.5.3; finding F29).

keel-app's ``HEALTH_LINE_PHRASES`` (``services/keel-app/src/lib/status-badge.ts``)
is the single edit point of the paper status vocabulary. The SDK words a
practice row's ``health_detail.reason`` from ``_paper_strings.HEALTH_LINE_ROWS``.

Arms:

* parity — this file reads status-badge.ts, normalises each phrase to a
  Python template, and requires the SDK's full forms to equal them, total over
  keel-app's ``HEALTH_REASONS`` (an added, removed or reworded phrase on either
  side reds). keel-app's ``n === 1`` plural is the SDK's two plural rows.
* listed — every listed form equals its full form except the declared
  rewording (``funding_stalled``: "funding" is a banned listed token).
* render — ``keel_live_monitor``'s overview of a practice row carries
  ``health_line`` (and ``health_url`` for a stalled run); a null health, a
  null reason and a reason the map lacks render no line; control: a ``real``
  row's response has neither key.

Seed (run 2026-10-12, reversed by exact edit): reword the SDK's
``latest_eval_failed`` full form to "last evaluation did not finish" -> the
parity arm reds naming ``latest_eval_failed``; the render and control arms
stay green.
"""

from __future__ import annotations

import re
from unittest import mock

import pytest
from keel.tools.outcomes import _paper_strings as ps

from tests import _surface_fixtures as sf


STATUS_BADGE_TS = sf.REPO_ROOT / "services" / "keel-app" / "src" / "lib" / "status-badge.ts"

pytestmark_monorepo = pytest.mark.skipif(
    not STATUS_BADGE_TS.is_file(), reason="reads keel-app's status-badge.ts (monorepo only)"
)

#: Listed forms that may differ from their full form, and why.
LISTED_REWORDINGS = {"funding_stalled": "'funding' is a banned listed token"}


# ── reading keel-app's map ──────────────────────────────────────────────────


def _ts_block(text: str, start: str) -> str:
    begin = text.index(start)
    end = text.index("\n};", begin)
    return text[begin:end]


def _ts_reasons(text: str) -> list[str]:
    block = text[text.index("export const HEALTH_REASONS = [") :]
    block = block[: block.index("] as const;")]
    return re.findall(r'"([a-z_]+)"', block)


_PLURAL_RE = re.compile(r'\$\{n === 1 \? "([^"]+)" : "([^"]+)"\}')
_PENDING_RE = re.compile(r"\$\{(\w+) \?\? PENDING\}")


def _normalise(template: str) -> list[str]:
    """A TS phrase → its Python template(s): ``${x ?? PENDING}`` and ``${n}``
    become ``{x}`` / ``{n}``; the ``n === 1`` ternary yields one template per
    branch (singular first). Any other interpolation is refused, so a new TS
    form cannot pass by being skipped."""
    plural = _PLURAL_RE.search(template)
    variants = (
        [_PLURAL_RE.sub(plural.group(1), template), _PLURAL_RE.sub(plural.group(2), template)]
        if plural
        else [template]
    )
    out = []
    for variant in variants:
        variant = _PENDING_RE.sub(r"{\1}", variant).replace("${n}", "{n}")
        assert "${" not in variant, f"unrecognised interpolation in {template!r}"
        out.append(variant)
    return out


def _ts_phrases(text: str) -> dict[str, list[str]]:
    """keel-app's key → its phrase template(s), read off the source."""
    block = _ts_block(text, "export const HEALTH_LINE_PHRASES")
    block = block[block.index("> = {") + len("> = {") :]
    entries = re.split(r"\n  (?=[a-z_]+: )", "\n" + block)
    phrases: dict[str, list[str]] = {}
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        key = entry.split(":", 1)[0]
        literals = re.findall(r"`([^`]*)`|\"([^\"]*)\"", entry.split("=>", 1)[1])
        # The phrase is the entry's last string literal (a block body's
        # `return`); plural words inside the ternary are template interiors.
        backticks = [b for b, _ in literals if b]
        phrase = backticks[-1] if backticks else [d for _, d in literals if d][-1]
        phrases[key] = _normalise(phrase)
    return phrases


def _sdk_phrases() -> dict[str, list[str]]:
    rows = ps.HEALTH_LINE_ROWS
    out: dict[str, list[str]] = {}
    for key, row in rows.items():
        base = key.split(":", 1)[0]
        out.setdefault(base, []).append(row.full)
    return out


@pytestmark_monorepo
def test_the_sdk_phrase_map_equals_keel_apps():
    text = STATUS_BADGE_TS.read_text(encoding="utf-8")
    reasons = _ts_reasons(text)
    ts = _ts_phrases(text)
    # Non-vacuity: keel-app's map is total over its twelve reasons plus null.
    assert len(reasons) == 12 and "schedule_unreadable" in reasons
    assert set(ts) == {"null", *reasons}
    sdk = _sdk_phrases()
    assert set(sdk) == set(ts), (sorted(set(sdk) ^ set(ts)),)
    drift = {key: (sdk[key], ts[key]) for key in ts if sdk[key] != ts[key]}
    assert not drift, f"SDK health phrases differ from status-badge.ts: {drift}"


def test_listed_forms_equal_full_forms_except_the_declared_rewording():
    rows = ps.HEALTH_LINE_ROWS
    assert len(rows) == 14
    differing = {key.split(":", 1)[0] for key, row in rows.items() if row.listed != row.full}
    assert differing == set(LISTED_REWORDINGS)
    for row in rows.values():
        assert isinstance(row.full, str) and isinstance(row.listed, str)
        assert row in ps.ALL_PAPER_STRINGS
        assert not re.search(r"\bLive\b", row.full + row.listed)


# ── the monitor renders it ─────────────────────────────────────────────────

_PRACTICE = {
    "deployment_id": "dep_paper",
    "strategy_id": "str_x",
    "name": "Hype carry",
    "status": "LIVE",
    "schedule": "0 */4 * * *",
    "venue": {"code": "PAPER", "capital_class": "practice"},
    "health": "degraded",
    "health_detail": {
        "reason": "consecutive_missing",
        "consecutive_missing": 2,
        "last_complete_at": "2026-10-11T08:00:00Z",
        "since": "2026-10-11T12:00:00Z",
    },
}
_REAL = {
    "deployment_id": "dep_live",
    "strategy_id": "str_y",
    "name": "Momentum",
    "status": "LIVE",
    "venue": {"code": "HYPERLIQUID", "capital_class": "real"},
    "health": None,
    "health_detail": None,
}


def _overview(row: dict) -> dict:
    from keel.tools.outcomes import _bootstrap
    from keel.tools.outcomes import get as get_tool
    from keel.tools.outcomes._base import ToolContext

    _bootstrap()

    client = mock.MagicMock()
    client.get.side_effect = lambda path, **kw: (
        dict(row) if path == f"/v1/deployments/{row['deployment_id']}" else {"points": []}
    )
    return (
        get_tool("keel_live_monitor")
        .handler(
            {"deployment_id": row["deployment_id"]},
            ToolContext(api_client=client, app_url="https://app.usekeel.io"),
        )
        .to_envelope()
    )


def _detail(**detail) -> dict:
    return {**_PRACTICE, "health_detail": {**_PRACTICE["health_detail"], **detail}}


@pytest.fixture
def full_profile(monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "full")


def test_a_degraded_practice_row_carries_its_phrase(full_profile):
    env = _overview(_PRACTICE)
    assert env["health_line"] == "2 missed evaluations since 2026-10-11T12:00:00Z"
    assert "health_url" not in env  # degraded, not stalled
    # The row itself is relayed unchanged.
    assert env["data"]["health_detail"] == _PRACTICE["health_detail"]
    one = _overview(_detail(consecutive_missing=1))
    assert one["health_line"] == "1 missed evaluation since 2026-10-11T12:00:00Z"


def test_a_stalled_run_points_at_its_paper_page(full_profile):
    env = _overview({**_detail(reason="schedule_unreadable"), "health": "stalled"})
    assert env["health_line"] == "schedule unreadable — evaluations cannot run"
    assert env["health_url"] == "https://app.usekeel.io/paper/dep_paper"


@pytest.mark.parametrize(
    "row",
    [
        {**_PRACTICE, "health": None, "health_detail": None},
        {**_detail(reason=None), "health": "running"},
        _detail(reason="a_reason_shipped_before_the_sdk"),
    ],
    ids=["null-health", "null-reason", "unknown-reason"],
)
def test_no_line_where_no_phrase_is_known(full_profile, row):
    env = _overview(row)
    assert "health_line" not in env
    assert env["label"] == "Paper run (simulated)"  # non-vacuity: a practice render


def test_control_a_real_row_has_no_health_keys(full_profile):
    env = _overview(_REAL)
    assert "health_line" not in env and "health_url" not in env
    assert "label" not in env


def test_listed_with_the_flag_on_uses_the_listed_form(paper_sdk_released, monkeypatch):
    monkeypatch.setenv("KEEL_SERVER_PROFILE", "listed")
    monkeypatch.setenv("KEEL_LISTED_PAPER", "1")
    env = _overview(_detail(reason="funding_stalled"))
    assert env["health_line"] == "rate data delayed — accrual paused since 2026-10-11T12:00:00Z"
