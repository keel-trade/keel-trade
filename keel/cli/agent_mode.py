"""Auto-detect when running inside an AI agent context.

Checks for known environment variables set by major AI coding tools.
Falls back to TTY detection (non-interactive = agent mode).
"""

from __future__ import annotations

import os
import sys

# Tool-specific env vars (verified against docs/source)
_TOOL_ENV_VARS = {
    # ── Confirmed (docs or source code) ──────────────────────────────────
    "CLAUDECODE",        # Claude Code (Anthropic) — set to "1" in all subprocesses
    "GEMINI_CLI",        # Gemini CLI (Google) — set to "1" in shell commands
    "CLINE_ACTIVE",      # Cline (formerly Claude Dev) — set to "true" in terminals
    "ROO_ACTIVE",        # Roo Code (Roo Cline) — set to "true" in terminals
    "CODEX_SANDBOX",     # Codex CLI (OpenAI) — set to "seatbelt" in sandbox
    "GOOSE_TERMINAL",    # Goose (Block) — set to "1" in terminal sessions
    "AUGMENT_AGENT",     # Augment Code — set to "1"
    "OPENCODE_CLIENT",   # OpenCode — set to "1"
    "TRAE_AI_SHELL_ID",  # TRAE AI (ByteDance) — set to session ID
    "COPILOT_CLI",       # GitHub Copilot CLI — set to "1" in subprocesses
    "REPL_ID",           # Replit — always set in Replit environment
    # ── Likely (forum references, not officially documented) ─────────────
    "CURSOR_AGENT",      # Cursor — reported in forum discussions
}

# Emerging cross-tool standards
_STANDARD_ENV_VARS = {
    "AI_AGENT",          # Vercel @vercel/detect-agent standard
    "AGENT",             # agents.md proposal (adopted by Goose, Amp)
}


def is_agent_mode() -> bool:
    """Detect if running inside an agent context.

    Priority:
    1. Explicit KEEL_AGENT_MODE=true/false override
    2. Cross-tool standards (AI_AGENT, AGENT)
    3. Tool-specific env vars
    4. Non-TTY stdout (piped output)
    """
    # 1. Explicit override
    explicit = os.environ.get("KEEL_AGENT_MODE")
    if explicit == "false":
        return False
    if explicit == "true":
        return True

    # 2. Cross-tool standards
    if any(os.environ.get(var) for var in _STANDARD_ENV_VARS):
        return True

    # 3. Tool-specific detection
    if any(os.environ.get(var) for var in _TOOL_ENV_VARS):
        return True

    # 4. Non-TTY fallback (pipes, redirects, subprocess calls)
    if not sys.stdout.isatty():
        return True

    return False


def detected_agent() -> str | None:
    """Return the name of the detected agent, or None.

    Useful for telemetry and adjusting behavior per-agent.
    """
    # Standards first
    ai_agent = os.environ.get("AI_AGENT")
    if ai_agent:
        return ai_agent
    agent = os.environ.get("AGENT")
    if agent:
        return agent

    # Tool-specific
    _AGENT_NAMES = {
        "CLAUDECODE": "claude-code",
        "GEMINI_CLI": "gemini-cli",
        "CLINE_ACTIVE": "cline",
        "ROO_ACTIVE": "roo-code",
        "CODEX_SANDBOX": "codex",
        "GOOSE_TERMINAL": "goose",
        "AUGMENT_AGENT": "augment",
        "OPENCODE_CLIENT": "opencode",
        "TRAE_AI_SHELL_ID": "trae-ai",
        "COPILOT_CLI": "copilot-cli",
        "CURSOR_AGENT": "cursor",
        "REPL_ID": "replit",
    }
    for var, name in _AGENT_NAMES.items():
        if os.environ.get(var):
            return name

    return None


def default_format() -> str:
    """Return default output format based on context."""
    return "json" if is_agent_mode() else "human"
