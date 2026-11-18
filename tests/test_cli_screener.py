"""CLI/MCP registry routes and actual handler bodies share one contract."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from keel.cli.main import cli
from keel.errors import KeelError
from keel.tools.outcomes import OUTCOMES, _bootstrap
from keel.tools.outcomes._base import ToolContext
from keel.tools.outcomes._screener_view import build_result


_bootstrap()
SID = "str_" + "a" * 26
RID = "res_" + "a" * 26
FIXTURE = Path(__file__).parent / "fixtures/cards/screener_fallback.json"
NAMES = (
    "screener_run screener_view screener_passing screener_view_set screener_refresh screener_publish screener_describe screener_unpublish screener_follow screener_follow_view_set screener_copy screener_list screener_action blocks_search blocks_get use_preview use_upgrade use_detach view_describe"
).split()


SHARE = {
    "share_id": "shr_one",
    "title": "OI build-up",
    "commit_id": "cmt_pinned",
    "url": "https://usekeel.io/share/shr_one?ref=ABC",
    "referral_line": "Your link carries your referral code: you earn a share of Keel's fees when people you bring trade.",
    "disclosure_summary": [
        "The full computed history of every output is public.",
        "Others can make a copy and see the source in Keel.",
    ],
    "refresh": {"registered": False, "cadence_minutes": None, "floor_minutes": 60},
}


VIEW_WITH_ABOUT = (
    'Columns(oi_chg7=Column("7-day OI change", unit="percent"))\n'
    'Page(title="OI build-up", about="Coins whose open interest grew over the last week.")\n'
    'Lines("Build-up", plot=Raw("oi_chg7"))'
)


class Client:
    def __init__(self, view_source=VIEW_WITH_ABOUT):
        self.calls = []
        self.writes = 0
        self.view_source = view_source

    def get(self, path, **params):
        self.calls.append(("get", path, params))
        if path.endswith("/views"):
            return {"revision": 3, "source": self.view_source}
        if path.startswith("/v1/screener-shares/") and path.count("/") == 3:
            return {**SHARE, "presentation_revision": 2}
        if path.endswith("/preview"):
            return {
                "passing": {
                    "now": {"bar_ms": 1, "assets": ["A", "B"]},
                    "previous": {"bar_ms": 0, "assets": []},
                    "entered": ["A", "B"],
                    "left": [],
                    "counts": {"passing": 2, "entered": 2, "left": 0},
                }
            }
        if path == "/v1/screener-shares":
            return {"items": [{"commit_id": "published"}]}
        if "/strategies/" in path:
            return {"head_commit_id": "head", "active_share": {"commit_id": "published"}}
        return json.loads(FIXTURE.read_text())

    get_public = get

    def post(self, path, json=None, **params):
        self.calls.append(("post", path, json))
        if not (json or {}).get("dry_run"):
            self.writes += 1
        if path.endswith("/runs") or path == "/v1/result-runs":
            return {"run_id": RID, "status": "COMPLETED"}
        if path == "/v1/screener-shares":
            return {**SHARE, "note": (json or {}).get("note")}
        return {"ok": True}

    def patch(self, path, json=None, **params):
        self.calls.append(("patch", path, json))
        self.writes += 1
        return {**SHARE, "note": (json or {}).get("note")}

    put = post

    def delete(self, path, **params):
        self.calls.append(("delete", path, params))
        return {"ok": True}


@pytest.mark.parametrize("name", NAMES)
def test_every_command_has_cli_help(name):
    result = CliRunner().invoke(cli, [*OUTCOMES["keel_" + name].cli_path, "--help"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "selector,path",
    [
        ({}, "/v1/result-runs/latest/envelope"),
        ({"strategy_id": SID}, f"/v1/screeners/{SID}/envelope"),
        ({"run_id": RID}, f"/v1/result-runs/{RID}/envelope"),
        ({"share_id": "shared"}, "/v1/screener-shares/shared/envelope"),
    ],
)
def test_view_routes_are_reads(selector, path):
    client = Client()
    result = OUTCOMES["keel_screener_view"].handler(selector, ToolContext(api_client=client))
    assert client.calls[0][0:2] == ("get", path)
    assert client.calls[0][2]["shape"] == "fallback"
    assert result.extra["view"]["markdown"].splitlines()[-1].startswith("View in Keel: ")
    assert client.writes == 0


@pytest.mark.parametrize(
    "args", [{}, {"source": "x", "strategy_id": SID}, {"source": "x", "version": 2}]
)
def test_run_invalid_subject_writes_nothing(args):
    client = Client()
    with pytest.raises(KeelError):
        OUTCOMES["keel_screener_run"].handler(args, ToolContext(api_client=client))
    assert client.calls == []


@pytest.mark.parametrize("version", [1, 42])
def test_saved_run_serializes_version_for_api_string_contract(version):
    client = Client()
    result = OUTCOMES["keel_screener_run"].handler(
        {"strategy_id": SID, "version": version, "wait": False},
        ToolContext(api_client=client),
    )
    # SubmitRun.version is str | None; the integer CLI/MCP argument must be
    # serialized as a JSON string or the API rejects the request before intake.
    assert client.calls[0] == ("post", f"/v1/screeners/{SID}/runs", {"version": str(version)})
    assert result.run_id == RID


def test_run_reads_envelope_after_one_submission():
    client = Client()
    result = OUTCOMES["keel_screener_run"].handler(
        {"source": 'Outputs(x="x")', "start_date": "2026-01-01", "wait": False},
        ToolContext(api_client=client),
    )
    assert client.calls[0] == (
        "post",
        "/v1/result-runs",
        {"source": 'Outputs(x="x")', "start": "2026-01-01"},
    )
    assert client.writes == 1 and result.extra["view"]["size"] == "receipt"


@pytest.mark.parametrize(
    "name,args,route",
    [
        (
            "screener_view_set",
            {"strategy_id": SID, "view": 'Lines("x",plot=Raw("x"))', "expected_revision": 2},
            f"/v1/screeners/{SID}/views",
        ),
        (
            "use_upgrade",
            {"strategy_id": SID, "at": "pipeline.0", "to_version": 2},
            f"/v1/strategies/{SID}/uses/upgrade",
        ),
        (
            "use_detach",
            {"strategy_id": SID, "at": "pipeline.0"},
            f"/v1/strategies/{SID}/uses/detach",
        ),
        (
            "screener_action",
            {"strategy_id": SID, "action": "test_as_strategy", "output": "x"},
            f"/v1/strategies/{SID}/automate",
        ),
    ],
)
def test_dry_run_is_forwarded_and_apply_is_distinct(name, args, route):
    client = Client()
    ctx = ToolContext(api_client=client)
    OUTCOMES["keel_" + name].handler({**args, "dry_run": True}, ctx)
    # The last WRITE call: a view write reads the screen back after it (Q-3634).
    sent = [c for c in client.calls if c[0] != "get"][-1]
    assert sent[1] == route and sent[2]["dry_run"] is True and client.writes == 0
    OUTCOMES["keel_" + name].handler({**args, "dry_run": False}, ctx)
    assert client.writes == 1


@pytest.mark.parametrize(
    "command",
    [
        ["use", "detach", SID, "--at", "pipeline.step[2]"],
        ["use", "upgrade", SID, "--at", "pipeline.step[2]", "--to-version", "2"],
        [
            "screener",
            "view-set",
            SID,
            "--view",
            'Lines("x",plot=Raw("x"))',
            "--expected-revision",
            "2",
        ],
        ["screener", "action", SID, "--action", "test_as_strategy", "--output", "x"],
    ],
)
@pytest.mark.parametrize("global_flag", [False, True])
def test_cli_preview_routes_never_apply(monkeypatch, command, global_flag):
    client = Client()
    monkeypatch.setattr(ToolContext, "get_client", lambda self: client)
    args = ["--dry-run", *command] if global_flag else [*command, "--dry-run"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    # The last WRITE call: a view write reads the screen back after it (Q-3634).
    assert [c for c in client.calls if c[0] != "get"][-1][2]["dry_run"] is True
    assert client.writes == 0

    result = CliRunner().invoke(cli, [*command, "--no-dry-run"])
    assert result.exit_code == 0, result.output
    assert [c for c in client.calls if c[0] != "get"][-1][2]["dry_run"] is False
    assert client.writes == 1


def test_refresh_uses_published_commit_and_off_never_reads_head():
    client = Client()
    ctx = ToolContext(api_client=client)
    OUTCOMES["keel_screener_refresh"].handler({"strategy_id": SID, "on": True}, ctx)
    assert client.calls[-1][2] == {"commit_id": "published"}
    client.calls = []
    OUTCOMES["keel_screener_refresh"].handler({"strategy_id": SID, "off": True}, ctx)
    assert client.calls == [("delete", f"/v1/screeners/{SID}/refresh", {})]


def test_human_cli_does_not_dump_series(monkeypatch):
    result = build_result(json.loads(FIXTURE.read_text()), ToolContext())
    monkeypatch.setattr(ToolContext, "get_client", lambda self: Client())
    output = CliRunner().invoke(cli, ["screener", "view", SID, "--format", "human"])
    assert output.exit_code == 0, output.output
    assert output.output.rstrip().endswith(result.hero_url)
    assert "anchor_values" not in output.output


def test_publish_carries_explicit_snapshot_and_disclosure():
    args = {
        "strategy_id": SID,
        "commit_id": "cmt_pinned",
        "screener_run_id": RID,
        "view_document_revision": 3,
        "disclosure_acknowledged_at": "2026-11-02T12:00:00Z",
        "disclosure": {
            "visibility": "public",
            "include_source": True,
            "show_author": False,
            "data_depth": "history",
            "discoverability": "unlisted",
        },
    }
    client = Client()
    OUTCOMES["keel_screener_publish"].handler(args, ToolContext(api_client=client))
    note = "Coins whose open interest grew over the last week."
    assert client.calls == [
        ("get", f"/v1/screeners/{SID}/views", {}),
        ("post", "/v1/screener-shares", {**args, "note": note}),
    ]
    # A caller's own note wins and the view is not read.
    client = Client()
    OUTCOMES["keel_screener_publish"].handler(
        {**args, "note": "Mine."}, ToolContext(api_client=client)
    )
    assert client.calls == [("post", "/v1/screener-shares", {**args, "note": "Mine."})]


def test_generated_screen_link_routes_match_api_owner():
    from importlib.resources import files

    sdk = files("keel.data").joinpath("screener_link_routes.json").read_bytes()
    canonical = Path(__file__).resolve().parents[4] / "libs/screeners/link_routes.json"
    assert sdk == canonical.read_bytes()


def test_action_confirmation_preserves_reviewed_target_commit():
    client = Client()
    tool = OUTCOMES["keel_screener_action"]
    assert "expected_target_commit_id" in tool.input_schema["properties"]
    args = {
        "strategy_id": SID,
        "action": "use_as_filter",
        "output": "x",
        "target_strategy_id": "target",
        "expected_target_commit_id": "reviewed_commit",
        "dry_run": False,
    }
    tool.handler(args, ToolContext(api_client=client))
    body = client.calls[-1][2]
    assert body["target_strategy_id"] == "target"
    assert body["expected_target_commit_id"] == "reviewed_commit"
    assert body["dry_run"] is False


@pytest.mark.parametrize("shared", [False, True])
def test_passing_uses_subject_capability_and_pins_shared_run(shared):
    class PassingClient(Client):
        def get(self, path, **params):
            if path.endswith("/passing"):
                self.calls.append(("get", path, params))
                return {
                    "run_id": params["run_id"],
                    "passing": {"now": {"assets": ["CROSS_ORG_ASSET"]}, "entered": [], "left": []},
                }
            if shared and path.startswith("/v1/result-runs/"):
                raise AssertionError("A share grants no owner-run read capability")
            return super().get(path, **params)

    client = PassingClient()
    args = {"share_id": "shared"} if shared else {"run_id": RID}
    result = OUTCOMES["keel_screener_passing"].handler(args, ToolContext(api_client=client))
    envelope_run = json.loads(FIXTURE.read_text())["run"]["run_id"]
    if shared:
        assert client.calls[-1] == (
            "get",
            "/v1/screener-shares/shared/passing",
            {"run_id": envelope_run},
        )
        assert "CROSS_ORG_ASSET" in result.extra["view"]["markdown"]
    else:
        assert client.calls[-1][1] == f"/v1/result-runs/{envelope_run}/preview"
    assert client.writes == 0


@pytest.mark.parametrize("count", [1, 5, 179])
def test_shared_snapshot_text_preserves_title_and_stored_universe_count(count):
    envelope = json.loads(FIXTURE.read_text())
    envelope["screener"]["name"] = "Published screen title"
    envelope["completed"]["manifest"]["universe"] = {"count": count}
    # One coin passes the snapshot's filter; with a single coin that is all
    # of them, which reads as a ranking (Q-3634), never "1 of 1 pass".
    envelope["completed"]["passing"]["counts"]["passing"] = 1
    client = Client()
    client.get = lambda path, **params: envelope
    result = OUTCOMES["keel_screener_view"].handler(
        {"share_id": "shared"}, ToolContext(api_client=client)
    )
    label = f"{count} asset" + ("" if count == 1 else "s")
    markdown = result.extra["view"]["markdown"]
    assert "**Published screen title**" in markdown
    if count == 1:
        assert "1 coin ranked by" in markdown and "pass" not in markdown
    else:
        assert f"1 of {count} coins pass now" in markdown
    # The retired "universe N assets ·" line (Q-3634) never comes back.
    assert "universe" not in markdown
    assert result.extra["manifest"]["universe"]["label"] == label
    assert client.writes == 0


# ---------- §D1.7 human lines and refusals (INV-24) ----------

PUBLISH_ARGS = {
    "strategy_id": SID,
    "commit_id": "cmt_pinned",
    "screener_run_id": RID,
    "view_document_revision": 3,
    "disclosure_acknowledged_at": "2026-11-02T12:00:00Z",
    "disclosure": {
        "visibility": "unlisted",
        "include_source": True,
        "show_author": False,
        "data_depth": "history",
        "discoverability": "unlisted",
    },
}
PUBLISH_GOLDEN = [
    "Published: OI build-up · https://usekeel.io/share/shr_one?ref=ABC",
    "Description: Coins whose open interest grew over the last week.",
    SHARE["referral_line"],
    "What is public:",
    "- The full computed history of every output is public.",
    "- Others can make a copy and see the source in Keel.",
    "next: keel_screener_refresh(on=true)",
]


def _cli_args(args):
    return [
        "screener",
        "publish",
        args["strategy_id"],
        "--commit-id",
        args["commit_id"],
        "--screener-run-id",
        args["screener_run_id"],
        "--view-document-revision",
        "3",
        "--disclosure-acknowledged-at",
        args["disclosure_acknowledged_at"],
        "--disclosure",
        json.dumps(args["disclosure"]),
        "--yes",
        "--format",
        "human",
    ]


def test_publish_prints_the_golden_lines(monkeypatch):
    client = Client()
    monkeypatch.setattr(ToolContext, "get_client", lambda self: client)
    result = CliRunner().invoke(cli, _cli_args(PUBLISH_ARGS))
    assert result.exit_code == 0, result.output
    assert result.output.rstrip("\n").splitlines() == PUBLISH_GOLDEN
    # The route's response stays the structured result.
    envelope = (
        OUTCOMES["keel_screener_publish"]
        .handler(PUBLISH_ARGS, ToolContext(api_client=Client()))
        .to_envelope()
    )
    assert envelope["referral_line"] == SHARE["referral_line"]
    assert envelope["share_url"] == SHARE["url"]


def test_publish_lines_drop_next_once_auto_refresh_is_on():
    from keel.tools.outcomes.screener_publish import publish_lines

    on = {
        **SHARE,
        "note": "Coins whose open interest grew over the last week.",
        "refresh": {"registered": True, "cadence_minutes": 60, "floor_minutes": 60},
    }
    assert publish_lines(on) == PUBLISH_GOLDEN[:-1]


@pytest.mark.parametrize(
    "source",
    [
        'Columns(oi_chg7=Column("7-day OI change", unit="percent"))\nLines("a", plot=Raw("oi_chg7"))',
        'Columns(oi_chg7=Column("7-day OI change"))\nPage(about="'
        + "x" * 281
        + '")\nLines("a", plot=Raw("oi_chg7"))',
    ],
    ids=["no-about", "about-too-long"],
)
def test_publish_without_a_description_still_publishes_and_says_how_to_add_one(monkeypatch, source):
    client = Client(view_source=source)
    monkeypatch.setattr(ToolContext, "get_client", lambda self: client)
    result = CliRunner().invoke(cli, _cli_args(PUBLISH_ARGS))
    assert result.exit_code == 0, result.output
    posted = [c for c in client.calls if c[0] == "post"]
    assert len(posted) == 1 and "note" not in posted[0][2]  # published, never truncated
    lines = result.output.rstrip("\n").splitlines()
    command = (
        "keel screener describe shr_one --note 'Ranks the screen'\\''s coins by 7-day OI change.'"
    )
    assert lines[-1] == f"next: {command}"
    assert lines[-2] == f"This screen has no description. Add a description: {command}"
    assert not any(line.startswith("Description:") for line in lines)


def test_describe_sets_the_note_against_the_current_presentation_revision():
    client = Client()
    result = OUTCOMES["keel_screener_describe"].handler(
        {"share_id": "shr_one", "note": "Coins with rising OI."}, ToolContext(api_client=client)
    )
    assert client.calls == [
        ("get", "/v1/screener-shares/shr_one", {}),
        (
            "patch",
            "/v1/screener-shares/shr_one",
            {"expected_revision": 2, "note": "Coins with rising OI."},
        ),
    ]
    assert (
        result.extra["view"]["markdown"].splitlines()[0] == "Description set: Coins with rising OI."
    )
    with pytest.raises(Exception, match="280"):
        OUTCOMES["keel_screener_describe"].handler(
            {"share_id": "shr_one", "note": "x" * 281}, ToolContext(api_client=Client())
        )


def test_publish_description_is_documented_for_agents():
    tool = OUTCOMES["keel_screener_publish"]
    assert "Pass note" in tool.description and "Page(about=" in tool.description
    assert tool.input_schema["properties"]["note"]["maxLength"] == 280


def _refused(status, body):
    from keel.errors import translate_http_error

    class Refusing(Client):
        def post(self, path, json=None, **params):
            raise translate_http_error(status, body)

    return Refusing()


@pytest.mark.parametrize(
    "status,code,tool,args",
    [
        (409, "SCREENER_SHARE_RUN_NOT_PUBLISHABLE", "keel_screener_run", {"strategy_id": SID}),
        (422, "SCREENER_SHARE_NOT_A_SCREENER", "keel_share_create", {"strategy_id": SID}),
        (
            422,
            "SCREENER_PRESENTATION_INADMISSIBLE",
            "keel_screener_view_set",
            {"strategy_id": SID, "dry_run": True},
        ),
        (409, "SCREENER_VIEW_DOCUMENT_REVISION_CONFLICT", "keel_screener_publish", None),
        (422, "SCREENER_DISCOVERY_DISABLED", "keel_screener_publish", None),
        (409, "SCREENER_ALREADY_PUBLISHED", None, None),
    ],
)
def test_publish_refusals_name_their_next_action(status, code, tool, args):
    body = json.dumps({"detail": "The plain sentence keel-api serves.", "code": code, "current": 7})
    with pytest.raises(KeelError) as raised:
        OUTCOMES["keel_screener_publish"].handler(
            PUBLISH_ARGS, ToolContext(api_client=_refused(status, body))
        )
    envelope = raised.value.to_envelope()
    assert envelope["message"] == "The plain sentence keel-api serves."
    assert envelope["suggested_next_action"]["tool"] == tool
    if code == "SCREENER_VIEW_DOCUMENT_REVISION_CONFLICT":
        assert envelope["suggested_next_action"]["args"]["view_document_revision"] == 7
    elif code == "SCREENER_DISCOVERY_DISABLED":
        sent = envelope["suggested_next_action"]["args"]["disclosure"]
        assert sent["discoverability"] == "unlisted"
    elif args is not None:
        assert envelope["suggested_next_action"]["args"] == args
    assert envelope["what_was_expected"]


def test_publish_disabled_is_its_own_sdk_code():
    body = json.dumps(
        {
            "detail": "Publishing screens is not open on your account yet.",
            "code": "SCREENER_PUBLISH_DISABLED",
        }
    )
    with pytest.raises(KeelError) as raised:
        OUTCOMES["keel_screener_publish"].handler(
            PUBLISH_ARGS, ToolContext(api_client=_refused(403, body))
        )
    assert raised.value.error_code == "screener_publish_disabled"
    assert raised.value.exit_code == 6 and raised.value.retryable is False
    assert "live" not in raised.value.to_envelope()["what_was_expected"]


def test_every_spec_refusal_row_is_mapped():
    from keel.tools.outcomes._screener_commands import REFUSALS

    rows = (
        "SCREENER_ALREADY_PUBLISHED SCREENER_PUBLISH_DISABLED SCREENER_SHARE_NOT_A_SCREENER "
        "SCREENER_SHARE_RUN_NOT_PUBLISHABLE SCREENER_VIEW_DOCUMENT_REVISION_CONFLICT "
        "SCREENER_PRESENTATION_INADMISSIBLE SCREENER_DISCLOSURE_VIEW_CONFLICT "
        "SCREENER_DISCOVERY_DISABLED SCREENER_SNAPSHOT_COPY_FAILED "
        "SCREENER_REFRESH_NOT_LIVE_VERSION SCREENER_AUTO_REFRESH_DISABLED SCREEN_FOLLOW_OWN "
        "SCREEN_COMMUNITY_COPY_NONE SCREEN_FOLLOW_VIEW_FIXED SCREEN_FOLLOW_VIEW_NOT_A_CONTROL "
        "SCREEN_FOLLOW_VIEW_OUT_OF_RANGE SCREENER_SHARE_DEMOTION_PENDING"
    ).split()
    assert set(rows) <= set(REFUSALS)


def test_anon_publish_says_the_work_carries_over(monkeypatch):
    body = json.dumps(
        {
            "detail": "Your plan does not include public screens.",
            "code": "plan_feature_unavailable",
            "quota": {
                "unit": "screeners_published_max",
                "label": "public screens",
                "kind": "no_cap_grants",
                "limit": 0,
                "used": 0,
                "plan": "anon",
            },
        }
    )
    client = _refused(403, body)
    monkeypatch.setattr(ToolContext, "get_client", lambda self: client)
    result = CliRunner().invoke(cli, _cli_args(PUBLISH_ARGS))
    assert result.exit_code != 0
    assert "your work carries over automatically" in result.output
    assert "{" not in result.output.splitlines()[0]


def test_follow_list_and_view_lines():
    class Following(Client):
        def get(self, path, **params):
            self.calls.append(("get", path, params))
            if path == "/v1/screen-follows":
                return {
                    "items": [
                        {
                            "share_id": "shr_one",
                            "title": "OI build-up",
                            "url": "https://usekeel.io/share/shr_one",
                            "status": "updated",
                            "status_at": "2026-11-02T12:00:00Z",
                            "notice": "Updated on 2026-11-02",
                            "community_share_id": None,
                        }
                    ]
                }
            if path == "/v1/strategies":
                return {
                    "data": [{"strategy_id": SID, "name": "OI build-up", "current_sequence": 3}]
                }
            if path == "/v1/screener-shares":
                return {"items": [{**SHARE, "disclosure": {"visibility": "unlisted"}}]}
            if path.endswith("/refresh"):
                return {"enabled": True, "schedule": {"cadence_minutes": 60}}
            return super().get(path, **params)

        def post(self, path, json=None, **params):
            self.calls.append(("post", path, json))
            return {"share_id": "shr_one", "created": True}

    ctx = ToolContext(api_client=Following())
    follow = OUTCOMES["keel_screener_follow"].handler({"share_id": "shr_one"}, ctx)
    assert follow.extra["view"]["markdown"].splitlines() == [
        "Following: OI build-up · updated",
        "View in Keel: https://usekeel.io/share/shr_one",
    ]
    listed = OUTCOMES["keel_screener_list"].handler({}, ctx).extra["view"]["markdown"]
    assert listed.splitlines() == [
        "Your screens:",
        # L13: each row ends in the id keel_screener_view takes.
        "OI build-up v3 · Unlisted · auto-refresh hourly · "
        + SHARE["url"]
        + " · str_aaaaaaaaaaaaaaaaaaaaaaaaaa",
        "Following:",
        "OI build-up · updated · 2026-11-02 · Updated on 2026-11-02 · "
        "https://usekeel.io/share/shr_one · shr_one",
    ]
    reset = OUTCOMES["keel_screener_follow_view_set"].handler(
        {"share_id": "shr_one", "reset": True}, ctx
    )
    assert reset.extra["view"]["markdown"] == "Your view: the author's view."


def test_owner_table_uses_labels_and_screen_formatting():
    from keel.tools.outcomes._screener_view import screen_value

    assert screen_value(0.2187, "rate_per_year") == "+21.9%"
    assert screen_value(-21.49, "percent") == "−21.5%"
    assert screen_value(0.0004, "percent") == "0.0%"
    assert screen_value(1271.4, "price") == "1,271"
    assert screen_value(0.0049, "price") == "0.0049"
    envelope = json.loads(FIXTURE.read_text())
    completed = envelope["completed"]
    names = [o["name"] for o in completed["manifest"]["outputs"]]
    completed["labels"] = [{"label": f"Label {n}", "unit": "rate_per_year"} for n in names]
    markdown = build_result(envelope, ToolContext()).extra["view"]["markdown"]
    header = next(line for line in markdown.splitlines() if line.startswith("| # |"))
    assert "(rate_per_year)" not in header and "(% a year)" in header
    assert "Label " in header
    first_row = next(line for line in markdown.splitlines() if line.startswith("| 1 |"))
    cells = [c.strip() for c in first_row.strip("|").split("|")][2:]
    assert cells and all(c == "—" or c.endswith("%") for c in cells), first_row


@pytest.mark.parametrize(
    "api,app",
    [
        ("https://staging-api.tailf4d598.ts.net", "https://staging-app.tailf4d598.ts.net"),
        ("https://api.usekeel.io", "https://app.usekeel.io"),
        ("http://localhost:8080", "https://app.usekeel.io"),
    ],
)
def test_owner_link_follows_the_configured_api(monkeypatch, api, app):
    from types import SimpleNamespace

    from keel.tools.outcomes.open_in_app import app_url_for

    monkeypatch.delenv("KEEL_APP_URL", raising=False)
    ctx = ToolContext(api_client=SimpleNamespace(_config=SimpleNamespace(api_url=api)))
    assert app_url_for("screener", SID, ctx).startswith(app + "/strategies/")
    monkeypatch.setenv("KEEL_APP_URL", "https://example.test")
    assert app_url_for("screener", SID, ctx).startswith("https://example.test/")


@pytest.mark.parametrize(
    "api,app",
    [
        ("https://staging-api.tailf4d598.ts.net", "https://staging-app.tailf4d598.ts.net"),
        ("https://api.usekeel.io", "https://app.usekeel.io"),
    ],
)
def test_strategy_and_backtest_links_follow_the_configured_api(monkeypatch, api, app):
    """L13: compose's strategy link (and a backtest's) linked prod from a
    staging SDK. They follow the API now; `KEEL_APP_URL` still wins, and live
    and paper links keep ctx.app_url (the frozen listed outputs pin them).

    # SEED (L13): build the strategy link from `ctx.app_url` again in
    # open_in_app.app_url_for — the staging arm reds. Reverse the edit."""
    from types import SimpleNamespace

    from keel.tools.outcomes.open_in_app import app_url_for

    monkeypatch.delenv("KEEL_APP_URL", raising=False)
    ctx = ToolContext(api_client=SimpleNamespace(_config=SimpleNamespace(api_url=api)))
    assert app_url_for("strategy", SID, ctx) == f"{app}/strategies/{SID}/edit"
    assert app_url_for("backtest", "btr_x", ctx) == f"{app}/backtests/btr_x?tab=tearsheet"
    assert app_url_for("live", "dep_x", ctx) == f"{ctx.app_url}/live/dep_x"
    monkeypatch.setenv("KEEL_APP_URL", "https://example.test")
    assert app_url_for("strategy", SID, ctx).startswith("https://example.test/")
    # A tool that sends no request (keel_app_link) reads the same config.
    monkeypatch.delenv("KEEL_APP_URL")
    monkeypatch.setattr(
        ToolContext,
        "get_client",
        lambda self: SimpleNamespace(_config=SimpleNamespace(api_url=api)),
    )
    assert app_url_for("strategy", SID, ToolContext()) == f"{app}/strategies/{SID}/edit"


def test_anon_carry_over_matches_keel_api():
    """O-23: keel-api owns the sentence; the SDK says it verbatim, URL-free (D-12)."""
    import ast

    from keel.tools.outcomes._screener_commands import ANON_CARRY_OVER

    api = Path(__file__).resolve().parents[4] / "services/keel-api/src/errors.py"
    tree = ast.parse(api.read_text())
    source = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "ANON_CARRY_OVER" for t in node.targets)
    )
    assert ANON_CARRY_OVER == source
    assert "http" not in ANON_CARRY_OVER and "usekeel" not in ANON_CARRY_OVER


def test_view_returns_the_saved_view_and_its_revisions_on_request():
    """L13 §2a.3: the read `keel_screener_view_set` starts from.

    # SEED (L13): drop the `/views/revisions` read in screener_view.py's
    # handler — the revisions arm reds. Reverse the edit."""

    class WithHistory(Client):
        def get(self, path, **params):
            if path.endswith("/views/revisions"):
                self.calls.append(("get", path, params))
                return [
                    {
                        "revision": 3,
                        "updated_at": "2026-11-03T10:00:00Z",
                        "label": "Restored revision 1",
                        "origin": "restore",
                    },
                    {"revision": 2, "updated_at": "2026-11-02T10:00:00Z", "label": None},
                ]
            return super().get(path, **params)

    sid = "str_" + "a" * 26
    client = WithHistory()
    plain = OUTCOMES["keel_screener_view"].handler(
        {"strategy_id": sid}, ToolContext(api_client=client)
    )
    assert "view_source" not in plain.extra
    assert not any(p.endswith("/views") for _, p, _ in client.calls)
    result = OUTCOMES["keel_screener_view"].handler(
        {"strategy_id": sid, "include_view_source": True}, ToolContext(api_client=client)
    )
    view = result.extra["view_source"]
    assert view["source"] == VIEW_WITH_ABOUT and view["revision"] == 3
    # Number, date, label and origin; who saved a revision is not the agent's to read.
    assert view["revisions"] == [
        {
            "revision": 3,
            "saved_at": "2026-11-03T10:00:00Z",
            "label": "Restored revision 1",
            "origin": "restore",
        },
        {"revision": 2, "saved_at": "2026-11-02T10:00:00Z", "label": None, "origin": None},
    ]


def test_view_set_takes_exactly_one_of_view_or_revert_to():
    sid = "str_" + "a" * 26
    handler = OUTCOMES["keel_screener_view_set"].handler
    for args in ({}, {"view": "x", "revert_to": 2}):
        with pytest.raises(KeelError, match="either view"):
            handler(
                {"strategy_id": sid, "expected_revision": 3, **args},
                ToolContext(api_client=Client()),
            )

    class Recorder(Client):
        def put(self, path, json=None, **params):
            self.put_body = (path, json)
            return {"revision": 4, "check": {"valid": True}}

    client = Recorder()
    handler(
        {"strategy_id": sid, "expected_revision": 3, "revert_to": 2}, ToolContext(api_client=client)
    )
    assert client.put_body == (
        f"/v1/screeners/{sid}/views",
        {"revert_to": 2, "expected_revision": 3, "dry_run": False, "origin": "agent"},
    )
    # An agent's save names itself in the view's history, with its label.
    handler(
        {"strategy_id": sid, "expected_revision": 4, "view": "x", "label": "Only below -10%"},
        ToolContext(api_client=client),
    )
    assert client.put_body[1] == {
        "source": "x",
        "label": "Only below -10%",
        "expected_revision": 4,
        "dry_run": False,
        "origin": "agent",
    }
