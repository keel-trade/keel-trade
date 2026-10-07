"""CI drift gate for the canonical surface-routing table (spec 07 R7).

The table's single source of truth is ``shared/surface-routing.json``;
every rendered copy (site pages, AGENTS.md files, SDK README) must
match. See scripts/check_surface_routing.py for the full contract.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest


CHECKER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_surface_routing.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_checker():
    return _load_module("check_surface_routing", CHECKER_PATH)


def test_surface_routing_renders_match_canonical():
    checker = _load_checker()
    assert checker.check_surface_routing() == []


# ── Agent-surface drift gate (spec 02 R3) ───────────────────────────────


def test_count_gate_reads_every_consumer():
    """Non-vacuity: every consumer carries ≥1 hosted-count statement the
    patterns can see, and they all agree with the registry."""
    checker = _load_checker()
    expected = len(checker.listed_tools())
    for rel in checker.COUNT_CONSUMERS:
        counts = checker.count_statements((checker.REPO_ROOT / rel).read_text())
        assert counts, f"{rel}: the count gate reads nothing here"
        assert set(counts) == {expected}, f"{rel}: {counts} != {expected}"


def test_count_gate_reds_on_a_stale_number_in_every_phrasing():
    """Proof it can fail — one seed per phrasing the repo uses, plus the
    control: the correct number in the same phrasing stays green."""
    checker = _load_checker()
    phrasings = [
        "a {n}-tool research/backtest/read surface",
        "the single {n}-tool listed research/backtest/read surface",
        "research/backtest/read surface ({n} tools)",
        "{n} research/backtest/read tools, nothing to install",
        "Hosted tools ({n}):",
        '"hosted_tool_count": {n},',
        "export const TOOL_COUNT = {n};",
        "({n} tools, fail-closed)",
        # the Q-1451 cases: a wrap and markdown emphasis around the number
        "a {n}-tool research/backtest/read\nsurface",
        "A **{n}-tool** research/backtest/read surface",
    ]
    for text in phrasings:
        assert checker.count_errors("x", text.format(n=24), 26), text
        assert checker.count_errors("x", text.format(n=26), 26) == [], text
    # a consumer the patterns cannot see is an error, never a silent pass
    assert checker.count_errors("x", "no number here at all", 26)


def test_omission_gate_reds_when_a_hand_list_loses_a_hosted_tool():
    """Q-0997's defect: the generated block names every tool, so the arm
    must look OUTSIDE it — a control with the tool present stays green."""
    checker = _load_checker()
    names = ["keel_account_status", "keel_strategy_diff"]
    block = (
        checker.AS_BEGIN_MARKER
        + "\n- `keel_account_status` — x.\n- `keel_strategy_diff` — y.\n"
        + checker.AS_END_MARKER
    )
    hand_complete = "- `keel_account_status`\n- `keel_strategy_diff`\n"
    hand_lossy = "- `keel_account_status`\n"
    assert checker.omission_errors("x", block + "\n" + hand_complete, names) == []
    errs = checker.omission_errors("x", block + "\n" + hand_lossy, names)
    assert errs and "keel_strategy_diff" in errs[0] and "keel_account_status" not in errs[0]


def test_copy_law_is_one_law_and_bites():
    """The gate lifts FORBIDDEN_TEXT_RE from scan_listing_copy.py; it must be
    the same rule test_policy_scan.py applies to the listed tools, and it
    must red on a seeded banned noun as well as a verb."""
    policy_scan = _load_module(
        "test_policy_scan", Path(__file__).resolve().parent / "test_policy_scan.py"
    )

    checker = _load_checker()
    forbidden = checker.load_forbidden_text_re()
    assert forbidden.pattern == policy_scan.FORBIDDEN_TEXT_RE.pattern
    assert checker.copy_errors("x", [("f", "Backtest, then read the result")], forbidden) == []
    # the package NAME is an identifier, not prose — but the word still bites
    assert checker.copy_errors("x", [("f", "pipx install keel-trade")], forbidden) == []
    assert checker.copy_errors("x", [("f", "pipx install keel-trade, then trade")], forbidden)
    assert checker.copy_errors("x", [("f", "Deploy it and go live")], forbidden)
    assert checker.copy_errors("x", [("f", "net of fees and funding")], forbidden)


def test_copy_law_json_is_the_scanner_regex_and_reds_when_stale(tmp_path):
    """agent-surface 2.6: the two TS rendered-text scans read the copy law
    from shared/copy-law.json, so that file must be exactly the regex lifted
    from scan_listing_copy.py. Seeded arms: a stale pattern in the file and a
    Python-only construct in the rule both red; control: the checked-in file
    is byte-equal to the render, and the exported pattern compiles as the
    same rule in Python."""
    checker = _load_checker()
    forbidden = checker.load_forbidden_text_re()
    rendered = checker.render_copy_law_json(forbidden)
    doc = json.loads(rendered)
    assert doc["pattern"] == forbidden.pattern
    assert doc["js_flags"] == "i" and doc["python_flags"] == ["IGNORECASE"]
    assert doc["identifier_tokens"] == list(checker.COPY_IDENTIFIER_TOKENS)
    # the exported pattern IS the rule: same hits on the strings Q-1444 named
    exported = re.compile(doc["pattern"], re.IGNORECASE)
    for text in ("Build. Backtest. Deploy.", "Go live on Hyperliquid", "net of funding"):
        assert [m.group(0) for m in exported.finditer(text)] == [
            m.group(0) for m in forbidden.finditer(text)
        ]
    assert not exported.search("Backtests on real Hyperliquid market history")

    # control: the checked-in consumer is current
    checked_in = checker.REPO_ROOT / checker.COPY_LAW_JSON_TARGET
    assert checked_in.read_text() == rendered
    assert checker.COPY_LAW_JSON_TARGET in checker.gated_paths()

    # seeded: a stale file reds, and --write repairs it
    stale = tmp_path / "copy-law.json"
    stale.write_text(rendered.replace("|trading|", "|"))
    # (REPO_ROOT / <absolute path> is the absolute path, so tmp_path works here)
    errs = checker._check_whole_file_target(
        str(stale),
        rendered,
        False,
        source="scan_listing_copy.py FORBIDDEN_TEXT_RE",
    )
    assert errs and "drifted" in errs[0]

    # seeded: a rule that only Python can read is refused, never exported
    with pytest.raises(ValueError, match="Python-only"):
        checker.render_copy_law_json(re.compile(r"(?P<w>\btrade\b)", re.IGNORECASE))
    with pytest.raises(ValueError, match="IGNORECASE"):
        checker.render_copy_law_json(re.compile(r"\btrade\b"))


def test_copy_law_covers_the_routing_table():
    """Q-1463: the spec-07 rows render into the same files as the agent
    surface, so the copy-law arm reads them too. Seeded: the pre-Q-1463 row
    ("Going live" / "go live") reds, naming the row field AND the rendered
    block; control: the real table is clean, and a row with the same shape
    and no banned word stays green."""
    checker = _load_checker()
    forbidden = checker.load_forbidden_text_re()
    real = checker.load_canonical()
    assert checker.routing_table_copy_errors(real, forbidden) == []

    seeded = {**real, "rows": [dict(r) for r in real["rows"]]}
    seeded["rows"][2] = {
        "audience": "Going live with a strategy",
        "default_path": "Keel web app (connect account, review sizing, go live)",
        "also_works": "reads on every surface",
    }
    errs = checker.routing_table_copy_errors(seeded, forbidden)
    assert any("rows[2].audience" in e and "going live" in e for e in errs), errs
    assert any("rows[2].default_path" in e and "go live" in e for e in errs), errs
    assert any("block:routing" in e for e in errs), errs

    control = {**real, "rows": [dict(r) for r in real["rows"]]}
    control["rows"][2] = {
        "audience": "Reading a result on your phone",
        "default_path": "Keel web app",
        "also_works": "reads on every surface",
    }
    assert checker.routing_table_copy_errors(control, forbidden) == []
    # `$`-comment keys are not published strings, so they are not scanned
    commented = {**real, "$note": "go live"}
    assert checker.routing_table_copy_errors(commented, forbidden) == []


def test_keel_app_routing_table_is_held_to_the_canonical_rows():
    """keel-app hand-copies the spec-07 rows (no @shared alias). Seeded: the
    pre-Q-1463 row text reds; control: the file on disk is green; a file the
    regex cannot parse is an error, not a pass."""
    checker = _load_checker()
    routing = checker.load_canonical()
    text = (checker.REPO_ROOT / checker.KEEL_APP_ROUTING_TABLE).read_text()
    assert checker.keel_app_routing_table_errors(text, routing) == []
    seeded = text.replace(
        'audience: "Running a strategy from the Keel app"',
        'audience: "Going live with a strategy"',
        1,
    )
    assert seeded != text
    errs = checker.keel_app_routing_table_errors(seeded, routing)
    assert errs and "drifted" in errs[0]
    assert checker.keel_app_routing_table_errors("export const X = 1;", routing)


def test_docs_setup_page_is_a_whole_file_consumer(tmp_path, monkeypatch):
    """Task 2.3: content/docs/agents/setup.mdx is rendered WHOLE from the
    source + registry. Seeded: a hand edit to the page (or a missing page)
    reds the checker; control: the render itself passes."""
    checker = _load_checker()
    data = checker.load_agent_surface()
    tools = checker.listed_tools()
    page = checker.render_docs_setup_page(data, tools)
    # what the page must carry (spec 02 R2 #5 + the 2.3 brief)
    assert data["endpoint"]["hosted"] in page
    assert data["handoff"] in page and page.count(data["handoff"]) == 1  # AS-14: once
    for client in data["clients"]:
        assert f"### {client['name']}" in page
    for tool in tools:
        assert f"`{tool['name']}`" in page
    assert f"a {len(tools)}-tool research/backtest/read surface" in page
    assert data["other_ways"]["cli"]["install"] in page
    # MDX safety: no bare JSX-looking token survives outside a code fence
    assert "share/<id>" not in page and "share/&lt;id&gt;" in page

    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    rel = checker.DOCS_SETUP_PAGE_TARGET
    assert checker._check_whole_file_target(rel, page, False, source="x")  # missing → error
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(page)
    assert checker._check_whole_file_target(rel, page, False, source="x") == []  # control
    (tmp_path / rel).write_text(page.replace("Customize → Connectors", "Settings → Connectors", 1))
    errs = checker._check_whole_file_target(rel, page, False, source="x")
    assert errs and "drifted" in errs[0]


def test_docs_setup_page_answers_did_it_work_for_every_client():
    """Every client block carries a verify step and says what version the
    steps were checked against (Q-1570, Q-1574).

    This is a CONTENT guard, deliberately separate from the drift gate above.
    The drift gate only proves the checked-in file matches the renderer — so
    if the verify step were removed from the renderer AND regenerated, the two
    would agree and the gate would pass while the page silently lost the half
    that a real user needed. On 2026-09-18 a user connected Codex
    successfully, had no way to confirm it, and re-ran the sign-in five times.
    """
    checker = _load_checker()
    data = checker.load_agent_surface()
    page = checker.render_docs_setup_page(data, checker.listed_tools())

    # Non-vacuity: there really are clients to check, and the registry really
    # carries the two states this asserts on (one stamped, one not) — neither
    # quantity is moved by the behaviour under test.
    assert len(data["clients"]) >= 6
    assert any(c.get("verified") for c in data["clients"]), (
        "no stamped client to prove the verified branch"
    )
    assert any(not c.get("verified") for c in data["clients"]), (
        "no unstamped client to prove the other branch"
    )

    blocks = page.split("### ")
    for client in data["clients"]:
        block = next((b for b in blocks if b.startswith(client["name"])), None)
        assert block, f"no block for {client['name']}"

        # 1. a verify step, and the agent check every client can run
        assert checker.VERIFY_TITLE in block, f"{client['name']} has no verify step"
        assert checker.VERIFY_PROMPT in block, f"{client['name']} has no agent check"

        # 2. the version answered BOTH ways — an unstamped client must read as
        #    unverified rather than silently looking checked
        v = client.get("verified")
        if v:
            assert "Verified against" in block and v["version"] in block
        else:
            assert "Not verified yet" in block

        # 3. the CLI check only where it is DERIVABLE from the snippet's
        #    shape; never invented
        parts = (client.get("snippet") or "").strip().split()
        if client["kind"] == "command" and len(parts) >= 2 and parts[1] == "mcp":
            assert f"{parts[0]} mcp list" in block
        elif not client.get("ui_path"):
            assert "does not report the connection separately" in block


def test_mdx_text_escapes_jsx_but_not_code_spans():
    checker = _load_checker()
    assert checker._mdx_text("a public usekeel.io/share/<id> URL") == (
        "a public usekeel.io/share/&lt;id&gt; URL"
    )
    assert checker._mdx_text("pass `include_source=true` and {x}") == (
        "pass `include_source=true` and &#123;x&#125;"
    )
    assert checker._mdx_text("`<kept>` inside code") == "`<kept>` inside code"


# ── 2.8: the spec 02 R1/R2 accepted deltas ──────────────────────────────


def test_numbers_arm_reds_a_typed_integer_and_passes_computed_ones():
    """Q-1468: any integer before tool/skill/component/topic must be a
    computed NUMBERS value. Seeds: the audit's stale 182 components, a 23-tool
    surface, 9 skills, 12 topics — all red. Controls: every computed number in
    the same phrasings stays green, and prose with no number is silent."""
    checker = _load_checker()
    n = checker.compute_numbers()
    assert n["tool_count"] == len(checker.listed_tools())
    assert n["tools_registry"] > n["tools_local_default"] > n["tool_count"]
    assert n["skills_bundled"] == n["skills_hosted"] + 1  # deploy-and-monitor excluded
    assert n["component_count"] > 100 and n["knowledge_topics"] > 0
    assert set(n["library"]) == set(checker.LIBRARY_NUMBER_SLUGS)
    for text in (
        "a typed 182-component DSL",
        "182 components, validated at compose time",
        "a 23-tool research/backtest/read surface",
        "9 bundled agent skills",
        "12 knowledge topics",
        "**23-tool** research/backtest/read\nsurface",  # emphasis + wrap
    ):
        assert checker.number_errors("x", text, n), text
    for text in (
        f"a typed {n['component_count']}-component DSL",
        f"a {n['tool_count']}-tool research/backtest/read surface",
        f"{n['tools_registry']} outcome tools",
        f"{n['tools_live_write']} live-write tools",
        f"{n['skills_bundled']} bundled agent skills",
        f"{n['skills_hosted']} skills served",
        f"{n['knowledge_topics']} knowledge topics",
        "no number before a noun here; tools and skills in prose",
    ):
        assert checker.number_errors("x", text, n) == [], text


def test_numbers_arm_reads_every_number_consumer():
    """Non-vacuity: every numbers consumer exists on disk and the arm is clean
    over the real files; the TSX pages that used to type 182/26 are in the set."""
    checker = _load_checker()
    n = checker.compute_numbers()
    consumers = checker.number_consumers()
    assert "services/keel-site/src/app/mcp-hyperliquid/page.tsx" in consumers
    assert "services/keel-site/src/app/agents/page.tsx" in consumers
    assert checker.SKILLS_INDEX_TARGET in consumers
    for rel in consumers:
        path = checker.REPO_ROOT / rel
        assert path.exists(), rel
        assert checker.number_errors(rel, path.read_text(), n) == []


def test_every_hosted_tool_has_a_group_title_and_read_only_flag():
    """Seed: an unmapped tool name raises (a new listed tool must be placed);
    control: every listed tool resolves to a group in TOOL_GROUP_ORDER."""
    import pytest

    checker = _load_checker()
    tools = checker.listed_tools()
    assert {t["group"] for t in tools} <= set(checker.TOOL_GROUP_ORDER)
    assert all(t["title"] for t in tools)
    assert any(t["read_only"] for t in tools) and any(not t["read_only"] for t in tools)
    assert checker.tool_group("keel_account_status") == "start"
    with pytest.raises(RuntimeError, match="TOOL_GROUPS"):
        checker.tool_group("keel_not_a_tool")
    # every group that renders has at least one tool, so a briefing section is never empty
    assert {t["group"] for t in tools} == set(checker.TOOL_GROUP_ORDER)


def test_vendor_doc_is_required_and_ages_into_a_warning():
    """Seeds: a client without vendor_doc → error; a checked date 91 days old
    → warning naming the URL; controls: the real source has no errors, and a
    date exactly 90 days old is not yet a warning."""
    from datetime import date, timedelta

    checker = _load_checker()
    data = checker.load_agent_surface()
    assert checker.vendor_doc_errors(data) == []
    checked = date.fromisoformat(data["clients"][0]["vendor_doc"]["checked"])
    assert checker.vendor_doc_warnings(data, today=checked + timedelta(days=90)) == []
    warnings = checker.vendor_doc_warnings(data, today=checked + timedelta(days=91))
    assert warnings and all("vendor_doc.checked" in w for w in warnings)
    assert any(data["clients"][0]["vendor_doc"]["url"] in w for w in warnings)

    seeded = {**data, "clients": [dict(c) for c in data["clients"]]}
    seeded["clients"][1]["vendor_doc"] = None
    errs = checker.vendor_doc_errors(seeded)
    assert errs and "clients[chatgpt] has no vendor_doc" in errs[0]
    seeded["clients"][1]["vendor_doc"] = {"url": "https://x", "checked": "yesterday"}
    assert any("not YYYY-MM-DD" in e for e in checker.vendor_doc_errors(seeded))


def test_deep_links_render_the_vendor_formats():
    """Cursor: base64 of the compact config JSON; VS Code: URL-encoded, with the
    vscode.dev web twin. A client without a deep link renders None."""
    import base64
    import urllib.parse

    checker = _load_checker()
    data = checker.load_agent_surface()
    endpoint = data["endpoint"]["hosted"]
    by_id = {c["id"]: c for c in data["clients"]}
    cursor = checker.render_deep_link(by_id["cursor"], endpoint)
    expected_b64 = base64.b64encode(f'{{"url":"{endpoint}"}}'.encode()).decode()
    assert cursor == {
        "url": f"cursor://anysphere.cursor-deeplink/mcp/install?name=keel&config={expected_b64}",
        "web": None,
    }
    vscode = checker.render_deep_link(by_id["vscode"], endpoint)
    enc = urllib.parse.quote(f'{{"name":"keel","type":"http","url":"{endpoint}"}}', safe="")
    assert vscode == {
        "url": f"vscode:mcp/install?{enc}",
        "web": f"https://vscode.dev/redirect/mcp/install?{enc}",
    }
    assert checker.render_deep_link(by_id["claude"], endpoint) is None
    # the generated module pins the same bytes (the TS deepLink() must agree —
    # services/keel-site/src/lib/__tests__/agent-surface-deeplink.unit.test.ts)
    module = (checker.REPO_ROOT / "shared/agent-surface.generated.ts").read_text()
    assert cursor["url"] in module and vscode["url"] in module


def test_optional_clients_and_briefing_block():
    """VS Code is the one optional client (AS-20 #13) and sits before `other`;
    the briefing keys on Accept first and never lists Claude-User/ChatGPT-User."""
    checker = _load_checker()
    data = checker.load_agent_surface()
    ids = [c["id"] for c in data["clients"]]
    assert ids == list(checker.CLIENT_ORDER)
    assert [c["id"] for c in data["clients"] if c["optional"]] == ["vscode"]
    assert ids.index("vscode") == ids.index("other") - 1
    for c in data["clients"]:
        assert c["login"] and c["first_prompt"] and len(c["starter_prompts"]) == 3
        assert c["troubleshooting"][-1]["solution"] == "{{handoff}}"
    b = data["briefing"]
    assert b["accept"] == ["text/markdown"]
    assert b["path"] == "/agents.md" and b["routes"][0] == "/"
    assert not {"Claude-User", "ChatGPT-User"} & set(b["user_agents"])
    assert "GPTBot" in b["user_agents"]
    # seed: the optional flag on a non-optional client is an error in the gate
    seeded = {**data, "clients": [dict(c) for c in data["clients"]]}
    seeded["clients"][0]["optional"] = True
    for client in seeded["clients"]:
        if bool(client.get("optional")) != (client["id"] in checker.OPTIONAL_CLIENT_IDS):
            break
    else:
        raise AssertionError("seed did not trip the optional check")


def test_skills_index_and_tool_reference_are_whole_file_consumers(tmp_path, monkeypatch):
    """Both renders match disk (control) and a hand edit reds (seed)."""
    checker = _load_checker()
    skills = checker.render_skills_index()
    doc = __import__("json").loads(skills)
    assert doc["hosted"]["excluded"] == ["deploy-and-monitor"]
    assert doc["install"]["hosted_endpoint"] == checker.load_agent_surface()["endpoint"]["hosted"]
    assert list(doc["install"])[:2] == ["hosted_endpoint", "hosted_setup"]  # hosted-first
    ref = checker.render_tool_reference(mdx=True)
    assert "| Hosted |" in ref
    assert ref.count("| ✓ |") == len(checker.listed_tools())
    assert (checker.REPO_ROOT / checker.SKILLS_INDEX_TARGET).read_text() == skills
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    rel = checker.SKILLS_INDEX_TARGET
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(skills.replace('"served"', '"servd"', 1))
    errs = checker._check_whole_file_target(rel, skills, False, source="x")
    assert errs and "drifted" in errs[0]


def test_briefing_is_a_generated_whole_file_consumer(tmp_path, monkeypatch):
    """Spec 02 R4 (amended): the briefing is the generator's render of the same
    source — second person, Railway's order, every client, tools by group,
    the handoff once. Seed: a hand edit reds; control: the render on disk."""
    checker = _load_checker()
    data = checker.load_agent_surface()
    tools = checker.listed_tools()
    briefing = checker.render_briefing(data, tools)
    rel = checker.briefing_target(data)
    assert rel == "services/keel-site/public/agent-briefing.md"
    assert (checker.REPO_ROOT / rel).read_text() == briefing
    assert briefing.startswith("# Keel for agents")
    assert "You are an AI agent reading usekeel.io." in briefing
    order = [briefing.index(h) for h in ("## How to connect", "## What you can do", "## Docs")]
    assert order == sorted(order)
    for client in data["clients"]:
        assert f"**{client['name']}**" in briefing  # optional clients included
    for tool in tools:
        assert f"`{tool['name']}`" in briefing
    for group in checker.TOOL_GROUP_ORDER:
        assert f"### {group.capitalize()}" in briefing
    assert briefing.count(data["handoff"]) == 1
    assert f"a {len(tools)}-tool research/backtest/read surface" in briefing
    assert data["setup_page_url"] in briefing and data["agents_page_url"] in briefing
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(briefing.replace("## Docs", "## Documentation", 1))
    errs = checker._check_whole_file_target(rel, briefing, False, source="x")
    assert errs and "drifted" in errs[0]
    (tmp_path / rel).write_text(briefing)
    assert checker._check_whole_file_target(rel, briefing, False, source="x") == []


def test_sdk_lane_runs_on_every_gated_path():
    """Q-1457: both agent-surface gates run ONLY in sdk-test.yml, so its path
    filter must cover every path either checker reads — including the
    keel-site directories check_agent_surface_docs.py scans, which the
    original filter (packages/keel-trade + libs/pipeline_engine) never named.
    Seeded arm: drop one pattern → uncovered; control: an unrelated service
    path is NOT matched, so the filter is not simply "everything"."""
    import yaml

    checker = _load_checker()
    docs_checker = _load_module(
        "check_agent_surface_docs",
        Path(__file__).resolve().parent.parent / "scripts" / "check_agent_surface_docs.py",
    )
    workflow = yaml.safe_load((checker.REPO_ROOT / ".github/workflows/sdk-test.yml").read_text())
    triggers = workflow[True] if True in workflow else workflow["on"]  # PyYAML reads `on:` as True
    push_paths = triggers["push"]["paths"]
    pr_paths = triggers["pull_request"]["paths"]
    assert push_paths == pr_paths, "push and pull_request filters must be the same list"

    # every directory the docs checker scans → a representative file must match
    scanned = [
        f"{rel}/deep/file.md" if not rel.endswith((".md", ".py")) else rel
        for rel in docs_checker.SCAN_PATHS
    ]
    wanted = scanned + checker.gated_paths()
    assert len(wanted) > 20
    assert checker.paths_uncovered(push_paths, wanted) == []

    # control: the filter is selective
    assert checker.paths_uncovered(push_paths, ["services/keel-api/src/main.py"]) == [
        "services/keel-api/src/main.py"
    ]
    # proof it can fail: remove the keel-site content glob and the blog dir is uncovered
    narrowed = [p for p in push_paths if p != "services/keel-site/content/**"]
    assert "services/keel-site/content/blog/deep/file.md" in checker.paths_uncovered(
        narrowed, wanted
    )
    # glob semantics themselves
    assert checker.workflow_glob_matches(
        "services/keel-site/public/**", "services/keel-site/public/.well-known/agents.md"
    )
    assert checker.workflow_glob_matches(
        "services/keel-app/src/lib/agent-surface*.ts",
        "services/keel-app/src/lib/agent-surface.generated.ts",
    )
    assert not checker.workflow_glob_matches(
        "services/keel-app/src/lib/agent-surface*.ts",
        "services/keel-app/src/lib/x/agent-surface.ts",
    )


def test_helm_values_follow_the_source():
    """The two mcp-server values files carry MCP_DOCS_URL and MCP_RESOURCE_URL;
    both must be the source's facts, and the parser must see both keys
    (a values file it cannot read is an error, never a pass). Seeded: a
    wrong docs URL and a wrong host each red; the control (the real
    files) stays green."""
    checker = _load_checker()
    data = checker.load_agent_surface()
    for rel in checker.HELM_VALUES:
        text = (checker.REPO_ROOT / rel).read_text()
        env = checker.helm_values_env(text)
        assert set(env) == {"MCP_DOCS_URL", "MCP_RESOURCE_URL"}, rel
        assert checker.helm_errors(rel, text, data) == [], rel
        seeded = text.replace(env["MCP_DOCS_URL"], "https://usekeel.io/docs/elsewhere", 1)
        assert any("MCP_DOCS_URL" in e for e in checker.helm_errors(rel, seeded, data)), rel
        seeded = text.replace(env["MCP_RESOURCE_URL"], "https://mcp.example.io", 1)
        assert any("MCP_RESOURCE_URL" in e for e in checker.helm_errors(rel, seeded, data)), rel
        assert checker.helm_errors(rel, "toolsets: x\n", data), "a file with no env must error"


def test_gate_is_clean_on_main():
    checker = _load_checker()
    assert checker.check_agent_surface_gate() == []


def test_canonical_table_matches_spec_07_rows():
    """The canonical table must stay the spec-07 R7 table verbatim.

    Guard against 'helpful' edits to the canonical source silently
    changing the reviewed routing story on every surface at once.
    """
    checker = _load_checker()
    data = checker.load_canonical()
    assert data["columns"] == ["You are…", "Default path (shown first)", "Also works"]
    rows = [(r["audience"], r["default_path"], r["also_works"]) for r in data["rows"]]
    # ONE hosted endpoint model (D1 2026-07-19): mcp.usekeel.io serves one
    # 23-tool surface, added via paste-URL now (directory one-click coming);
    # running a strategy is a Keel web-app handoff on every surface, reads
    # stay everywhere. There is no separate "full"/"listed" split any more.
    # The rows are held to the listing copy law (Q-1463, AS-14): the handoff
    # row says what the person is doing, not "go live". The coding-agent row
    # defaults to the hosted endpoint (AS-1, Q-1465) — the local install is
    # the also-works path, never the default.
    assert rows == [
        (
            "Using Claude/ChatGPT on web or phone",
            "Hosted endpoint — paste the URL (directory one-click coming)",
            "CLI + local MCP",
        ),
        (
            "Working in Claude Code / Cursor / terminal",
            "Hosted endpoint — one command, or paste the URL",
            "CLI + local MCP",
        ),
        (
            "Running a strategy from the Keel app",
            "Keel web app (connect an account, review sizing, start it there)",
            "reads on every surface",
        ),
        ("Building your own agent/scripts", "SDK + API key", "CLI"),
        ("Just browsing/running strategies", "Web app + library", "hosted endpoint"),
    ]


# ── Full-registry tool inventory (Q-1703) ───────────────────────────────


def test_tool_inventory_names_every_registered_tool():
    """Non-vacuity: the inventory's bullet set IS the registry's key set.

    The quantity read here is the registry itself, which no drift in the
    PUBLISHED file can move — so this arm stays honest under the seeds
    below. The hand-maintained section this replaced was missing five
    tools (`keel_accounts_safety`, `keel_deployments_list`,
    `keel_live_quality`, `keel_live_receipt`, `keel_live_update`) and the
    omission arm never looked at them: it only asks after HOSTED names.
    """
    import sys

    checker = _load_checker()
    sys.path.insert(0, str(CHECKER_PATH.parent.parent))
    from keel.tools.outcomes import OUTCOMES, _bootstrap
    from keel.tools.outcomes._toolsets import LISTED_PROFILE_TOOLS

    _bootstrap()
    assert len(OUTCOMES) > 40, "registry unexpectedly small — this arm would prove nothing"

    block = checker.render_tool_inventory_block()
    bullets = re.findall(r"^- `(keel_[a-z0-9_]+)`(.*)$", block, flags=re.M)
    assert {n for n, _ in bullets} == set(OUTCOMES)
    assert len(bullets) == len(OUTCOMES), "a tool is listed twice or dropped"

    marks = dict(bullets)
    for name, tool in OUTCOMES.items():
        if tool.local_only:
            assert marks[name] == " (local only)", name
        elif name in LISTED_PROFILE_TOOLS:
            assert marks[name] == " (hosted)", name
        else:
            assert marks[name] == "", name

    # Every hosted tool is marked hosted, and nothing else is.
    assert {n for n, m in bullets if m == " (hosted)"} == set(LISTED_PROFILE_TOOLS)


def test_tool_inventory_is_published_and_reds_on_drift(tmp_path, monkeypatch):
    """Proof it can fail, on the two drifts the hand-maintained list hid.

    Control: the block as published is green. Seed 1 — a tool registered
    but not regenerated into the file. Seed 2 — a tool deleted from the
    published block by hand. Both must name `tool-inventory`.
    """
    checker = _load_checker()
    published = checker.render_tool_inventory_block()

    # Control: the real repo file carries exactly this block.
    for rel in checker.TOOL_INVENTORY_TARGETS:
        assert (
            checker._check_markdown_target(
                rel,
                published,
                False,
                block_re=checker._TOOL_INVENTORY_BLOCK_RE,
                markers=(
                    checker.TOOL_INVENTORY_BEGIN_MARKER,
                    checker.TOOL_INVENTORY_END_MARKER,
                ),
                label="tool-inventory",
                source="x",
            )
            == []
        ), rel

    rel = checker.TOOL_INVENTORY_TARGETS[0]
    monkeypatch.setattr(checker, "REPO_ROOT", tmp_path)
    (tmp_path / rel).parent.mkdir(parents=True)

    def _errs(file_text: str, expected_block: str) -> list[str]:
        (tmp_path / rel).write_text(file_text)
        return checker._check_markdown_target(
            rel,
            expected_block,
            False,
            block_re=checker._TOOL_INVENTORY_BLOCK_RE,
            markers=(
                checker.TOOL_INVENTORY_BEGIN_MARKER,
                checker.TOOL_INVENTORY_END_MARKER,
            ),
            label="tool-inventory",
            source="the outcome registry",
        )

    assert _errs(f"# x\n\n{published}\n", published) == []  # control, in a tmp tree

    # Seed 1: a new tool joins the registry, nobody regenerates the file.
    tools = checker.registry_tools()
    widened = checker.render_tool_inventory_block(
        tools
        + [{"name": "keel_new_thing", "toolset": "read-only", "local_only": False, "hosted": False}]
    )
    errs = _errs(f"# x\n\n{published}\n", widened)
    assert errs and "tool-inventory" in errs[0] and "drifted" in errs[0]

    # Seed 2: a tool is hand-deleted from the published block.
    lopped = published.replace("- `keel_live_update`\n", "", 1)
    assert lopped != published
    errs = _errs(f"# x\n\n{lopped}\n", published)
    assert errs and "tool-inventory" in errs[0]

    # Seed 3: the markers are removed entirely (the section retyped by hand).
    errs = _errs("# x\n\n- `keel_account_status`\n", published)
    assert errs and "expected exactly one tool-inventory marker block" in errs[0]


def test_tool_inventory_refuses_an_unplaced_toolset(monkeypatch):
    """A new toolset must be PLACED, never silently dropped — the failure
    mode that let five live tools go unpublished for months."""
    checker = _load_checker()
    tools = checker.registry_tools()
    with pytest.raises(RuntimeError, match="a toolset was dropped"):
        checker.render_tool_inventory_block(
            tools
            + [{"name": "keel_x", "toolset": "not-a-toolset", "local_only": False, "hosted": False}]
        )
