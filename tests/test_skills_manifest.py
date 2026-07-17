"""Tests for the published agent-discovery manifests (spec 04 R4).

Covers `.well-known/skills/index.json` (generated from the live skill
registry by ``scripts/build_skills_manifest.py``) and
`.well-known/agent-card.json` (hand-authored metadata, DP7), both served
from keel-site's public directory:

1. **Schema validation** — minimal pydantic schemas both files must
   satisfy (Stripe-style ``skills`` array shape; agent-card
   product/envelope/endpoints/provenance).
2. **Freshness drift gate** — the checked-in skills manifest must be
   byte-identical to a fresh render from ``keel.skills``.
3. **Link checks** — llms.txt and the site AGENTS.md link both files;
   the agent card's endpoint URLs point at paths that actually exist in
   keel-site's public directory.

Monorepo-scope note: the manifest files live in ``services/keel-site``
which is not part of the public keel-trade mirror — these tests SKIP
when that tree is absent (public-repo checkout) and run in monorepo CI.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from keel.skills import BUNDLED_SKILLS
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SDK_ROOT.parents[2]
SITE_PUBLIC = REPO_ROOT / "services" / "keel-site" / "public"
SKILLS_MANIFEST = SITE_PUBLIC / ".well-known" / "skills" / "index.json"
AGENT_CARD = SITE_PUBLIC / ".well-known" / "agent-card.json"

monorepo_only = pytest.mark.skipif(
    not SITE_PUBLIC.exists(),
    reason="keel-site public tree not present (public keel-trade mirror)",
)


def _load_build_script():
    spec = importlib.util.spec_from_file_location(
        "build_skills_manifest", SDK_ROOT / "scripts" / "build_skills_manifest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Minimal schemas ─────────────────────────────────────────────────────


class SkillEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    trigger: str
    install: str
    usage: str
    mcp_prompt: str


class InstallBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: str
    pypi: HttpUrl
    command: str
    mcp_server: str
    mcp_registry: str
    claude_desktop_bundle: HttpUrl
    docs: HttpUrl


class SkillsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str
    website: HttpUrl
    description: str
    install: InstallBlock
    skills: list[SkillEntry]


class AgentCardEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # "for" is a Python keyword — validate it via alias.
    for_: list[str] = Field(alias="for")
    not_for: list[str]


class AgentCardMcp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: str
    install: str
    command: str
    registry: str
    claude_desktop_bundle: HttpUrl


class AgentCardEndpoints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    website: HttpUrl
    docs: HttpUrl
    agent_instructions: HttpUrl
    llms_txt: HttpUrl
    pricing: HttpUrl
    skills: HttpUrl
    mcp: AgentCardMcp


class AgentCardProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: str
    website: HttpUrl
    pypi: HttpUrl
    source: HttpUrl
    license: str
    mcp_registry: str
    contact: HttpUrl


class AgentCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    product: str
    url: HttpUrl
    description: str
    envelope: AgentCardEnvelope
    endpoints: AgentCardEndpoints
    provenance: AgentCardProvenance


# ── Skills manifest ─────────────────────────────────────────────────────


@monorepo_only
def test_skills_manifest_validates_against_schema():
    manifest = SkillsManifest.model_validate(
        json.loads(SKILLS_MANIFEST.read_text(encoding="utf-8"))
    )
    assert len(manifest.skills) == len(BUNDLED_SKILLS)


@monorepo_only
def test_skills_manifest_matches_bundled_skills():
    data = json.loads(SKILLS_MANIFEST.read_text(encoding="utf-8"))
    assert [s["name"] for s in data["skills"]] == list(BUNDLED_SKILLS)
    for entry in data["skills"]:
        assert entry["description"], f"{entry['name']}: empty description"
        assert entry["usage"] == f"keel skills show {entry['name']}"


@monorepo_only
def test_skills_manifest_is_fresh():
    """Checked-in manifest must equal a fresh render from keel.skills."""
    build = _load_build_script()
    checked_in = SKILLS_MANIFEST.read_text(encoding="utf-8")
    assert checked_in == build.render_manifest(), (
        "services/keel-site/public/.well-known/skills/index.json is STALE — "
        "a bundled skill's frontmatter or the builder changed without "
        "regeneration. Regenerate: python packages/keel-trade/keel-sdk/"
        "scripts/build_skills_manifest.py"
    )


# ── Agent card ──────────────────────────────────────────────────────────


@monorepo_only
def test_agent_card_validates_against_schema():
    card = AgentCard.model_validate(json.loads(AGENT_CARD.read_text(encoding="utf-8")))
    assert card.envelope.not_for, "agent card must state what Keel is NOT for"


@monorepo_only
def test_agent_card_endpoints_exist_in_site_public():
    """Card endpoint URLs on usekeel.io must map to real public/ files."""
    data = json.loads(AGENT_CARD.read_text(encoding="utf-8"))
    endpoints = data["endpoints"]
    for key, rel in [
        ("agent_instructions", "AGENTS.md"),
        ("llms_txt", "llms.txt"),
        ("pricing", "pricing.md"),
        ("skills", ".well-known/skills/index.json"),
    ]:
        assert endpoints[key] == f"https://usekeel.io/{rel}"
        assert (SITE_PUBLIC / rel).exists(), f"{key} target missing: public/{rel}"


# ── Link checks (llms.txt / AGENTS.md) ─────────────────────────────────


@monorepo_only
def test_llms_txt_links_both_manifests():
    llms = (SITE_PUBLIC / "llms.txt").read_text(encoding="utf-8")
    assert "https://usekeel.io/.well-known/skills/index.json" in llms
    assert "https://usekeel.io/.well-known/agent-card.json" in llms
    assert "https://usekeel.io/pricing.md" in llms


@monorepo_only
def test_site_agents_md_links_both_manifests():
    agents_md = (SITE_PUBLIC / "AGENTS.md").read_text(encoding="utf-8")
    assert "https://usekeel.io/.well-known/skills/index.json" in agents_md
    assert "https://usekeel.io/.well-known/agent-card.json" in agents_md
