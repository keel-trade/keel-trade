"""A plan-cap wall on a save resumes EXACTLY the blocked save (Q-1847).

The Q-1807 shape, in `keel_strategy_compose`: the resume carried only
`strategy_id` and `name` — no source at all — so following it after the
reset raised `missing_input`, and even a patched-in source would have lost
the thesis (`description`), the version label (`message`) and the base
(`parent_version`). The schema check in `test_handoff_resume` could not see
it: compose declares no required args (source vs source_file is decided in
the handler).

Proof it can fail (2026-09-23, reverted by reversing the edit): restore the
old two-key `retry_args` — all three arms red (both saves and the source_file call).

Proof it is not vacuous: the blocked call exercises every declared argument
kind a save carries (asserted), and the resume is replayed through the
REAL handler, which must send the same body as the blocked call did.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from keel.errors import translate_http_error
from keel.tools.outcomes import _bootstrap, get
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._handoff import HandoffRequired

from tests.test_handoff_envelope import SPENT_WITH_PATHS_403


_bootstrap()

SOURCE = """Globals(target_timeframe='1d')
Universe(mode='manual', symbols=['BTC'])
Execution(rebalance='every_bar')
Pipeline([PriceDataLoader(), ROC(period=20), ForecastWeightNormalizer(target_leverage=1.0)], name='x')
"""


def _ctx(client: MagicMock) -> ToolContext:
    return ToolContext(api_client=client, app_url="https://app.usekeel.io", is_tty=False)


def _blocked(client: MagicMock, args: dict) -> dict:
    with pytest.raises(HandoffRequired) as exc:
        get("keel_strategy_compose").handler(dict(args), _ctx(client))
    return exc.value.to_envelope()["resume"]["verify_call"]


@pytest.mark.parametrize("update", [False, True], ids=["create", "update"])
def test_the_resume_is_the_blocked_save(update: bool):
    blocked = {
        "source": SOURCE,
        "name": "BTC beater",
        "description": "Beat holding BTC with a 20-day trend filter.",
        "message": "first cut",
        "present": "receipt",
    }
    if update:
        blocked.update({"strategy_id": "str_1", "parent_version": "3"})
    assert len(blocked) >= 5, "non-vacuity: every semantic arg kind a save carries"
    client = MagicMock()
    client.get.return_value = {}
    wall = translate_http_error(403, SPENT_WITH_PATHS_403)
    (client.patch if update else client.post).side_effect = wall
    verify = _blocked(client, blocked)
    assert verify["tool"] == "keel_strategy_compose"
    assert verify["args"] == blocked

    # Replayed through the real handler, the resume sends the same body.
    sent_blocked = (client.patch if update else client.post).call_args.kwargs["json"]
    replay = MagicMock()
    replay.get.return_value = {}
    (replay.patch if update else replay.post).side_effect = wall
    _blocked(replay, verify["args"])
    sent_resume = (replay.patch if update else replay.post).call_args.kwargs["json"]
    assert sent_resume == sent_blocked


def test_a_source_file_call_resumes_with_the_text_it_read(tmp_path):
    """The resume names `source`, not the path: a hosted resume has no
    filesystem, and the file may have changed since the wall."""
    path = tmp_path / "strategy.py"
    path.write_text(SOURCE)
    client = MagicMock()
    client.post.side_effect = translate_http_error(403, SPENT_WITH_PATHS_403)
    verify = _blocked(client, {"source_file": str(path), "name": "BTC beater"})
    assert verify["args"] == {"source": SOURCE, "name": "BTC beater"}
