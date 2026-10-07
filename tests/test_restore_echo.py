"""`keel_strategy_restore` says which version it restored and the label it wrote (Q-2270).

keel-api's restore returns `StrategyResponse`, which carries neither the new
commit's message nor `restored_from`; both are on the commit the restore
wrote. Before Q-2270 the result echoed the raw `ref` alone — a tag or
commit-id restore never said which version came back — and showed no label
unless the caller passed one. Driven over the real FastMCP wire on the hosted
listed server, the Keel API faked at `KeelClient.post` / `.get`.

Seeds (run 2026-10-01, each reverted by reversing the edit):
* make `_head_version` return `{}` — the tag arm reds (no source sequence,
  no applied label); the caller-message arm and the moved-head control stay
  green, because both are answered by what the caller sent.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
from unittest import mock

from keel.tools.outcomes import _bootstrap


_bootstrap()

API = "https://api.test.usekeel.io"
_ENV = (
    "KEEL_SERVER_PROFILE",
    "KEEL_EXECUTION_MODE",
    "KEEL_TOOLSETS",
    "KEEL_API_KEY",
    "KEEL_API_URL",
)


@contextmanager
def _hosted_listed():
    from keel.hosting import bind_request_credentials, clear_request_credentials

    saved = {k: os.environ.get(k) for k in _ENV}
    for k in _ENV:
        os.environ.pop(k, None)
    os.environ.update(KEEL_SERVER_PROFILE="listed", KEEL_EXECUTION_MODE="hosted")
    token = bind_request_credentials(token="tok_test", api_url=API)
    try:
        yield
    finally:
        clear_request_credentials(token)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _restore(args: dict, *, head: dict) -> tuple[list, dict]:
    """POSTs made and the decoded result. keel-api answers the restore with
    the strategy at v5; the head version row is `head`."""
    posts: list = []

    def post(self, path, json=None, **_):
        posts.append((path, json))
        return {"id": "str_a", "name": "p", "status": "DRAFT", "current_sequence": 5}

    def get(self, path, **params):
        if path.endswith("/versions"):
            return [head]
        return {"strategy_id": "str_a", "name": "p", "current_sequence": 5}

    async def run():
        from fastmcp import Client
        from keel.mcp.server import create_server

        async with Client(create_server()) as client:
            return await client.call_tool("keel_strategy_restore", args, raise_on_error=False)

    with (
        _hosted_listed(),
        mock.patch("keel.client.KeelClient.post", post),
        mock.patch("keel.client.KeelClient.get", get),
    ):
        result = asyncio.run(run())
    if isinstance(result.structured_content, dict):
        body = result.structured_content
    else:
        body = json.loads("\n".join(getattr(c, "text", "") for c in result.content))
    if isinstance(body.get("result"), str):
        body = json.loads(body["result"])
    return posts, body


_HEAD = {
    "commit_id": "cmt_5",
    "sequence_number": 5,
    "message": "Restored from v2",
    "meta": {"restored_from": 2},
}


def test_a_tag_restore_names_the_version_it_restored_and_the_applied_label():
    posts, body = _restore({"strategy_id": "str_a", "ref": "champion"}, head=_HEAD)
    assert posts == [("/v1/strategies/str_a/versions/restore", {"ref": "champion"})]
    assert body["restored_from_ref"] == "champion"
    assert body["restored_from_sequence"] == 2
    assert body["message"] == "Restored from v2"  # the label the server wrote
    assert body["new_commit_id"] == "cmt_5"
    assert body["next"].startswith("Restored v2 as v5")


def test_the_callers_message_is_echoed_as_stored():
    head = {**_HEAD, "message": "back to the baseline"}
    posts, body = _restore(
        {"strategy_id": "str_a", "ref": "2", "message": "back to the baseline"}, head=head
    )
    assert posts[0][1] == {"ref": "2", "message": "back to the baseline"}
    assert body["message"] == "back to the baseline"
    assert body["restored_from_sequence"] == 2


def test_a_head_another_write_has_moved_is_not_read_as_this_restore():
    """Control: the head row is v6, not the v5 this restore wrote, so its
    label and source are not claimed; a numeric ref still names itself."""
    moved = {**_HEAD, "sequence_number": 6, "message": "someone else", "meta": {}}
    _, body = _restore({"strategy_id": "str_a", "ref": "3"}, head=moved)
    assert body["restored_from_sequence"] == 3
    assert "message" not in body
    assert body["next"].startswith("Restored v3 as v5")
