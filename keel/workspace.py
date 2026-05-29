"""Local workspace for strategy development.

Provides a checkout/push/pull model for working on platform strategies
locally. Agents and MCP tools use this to iterate on strategies with
full local validation, then sync back to the platform for backtesting
and deployment.

Workspace layout:
    ~/.keel/workspace/
    └── {strategy_id}/
        ├── strategy.py          # Working copy (editable)
        └── .keel-meta.json      # Sync metadata
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path.home() / ".keel" / "workspace"
META_FILE = ".keel-meta.json"
STRATEGY_FILE = "strategy.py"
NOTES_FILE = "notes.md"


@dataclass
class WorkspaceMeta:
    """Sync metadata for a checked-out strategy."""

    strategy_id: str
    name: str
    source_hash: str  # Platform source_hash at checkout/last sync
    checked_out_at: str
    org_id: str | None = None
    current_sequence: int | None = None

    def save(self, workspace_dir: Path) -> None:
        meta_path = workspace_dir / META_FILE
        meta_path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, workspace_dir: Path) -> WorkspaceMeta:
        meta_path = workspace_dir / META_FILE
        data = json.loads(meta_path.read_text())
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class WorkspaceStatus:
    """Comparison of local vs remote state."""

    strategy_id: str
    name: str
    local_hash: str
    remote_hash: str | None
    remote_sequence: int | None
    local_modified: bool
    remote_modified: bool
    conflict: bool
    workspace_dir: str

    @property
    def state(self) -> str:
        if self.conflict:
            return "conflict"
        if self.local_modified and self.remote_modified:
            return "conflict"
        if self.local_modified:
            return "ahead"
        if self.remote_modified:
            return "behind"
        return "current"


def _no_workspace_error_message() -> str:
    """Build the auto-detect failure message with a list of candidates.

    When the user runs `keel strategy push` (or pull/status/discard)
    without a strategy_id from a dir that doesn't auto-resolve, telling
    them "specify --strategy-id" is half the answer. Listing the
    workspaces they DO have checked out is the other half — they can
    pick one directly.
    """
    workspaces = list_workspaces()
    ids = [ws.strategy_id for ws in workspaces]
    if not ids:
        return (
            "No workspace strategy found. Run `keel_strategy_checkout <id>` "
            "first, or pass `strategy_id` explicitly (CLI: positional arg or "
            "`--strategy-id`)."
        )
    if len(ids) == 1:
        return (
            f"Auto-detect failed but exactly one workspace exists: {ids[0]}. "
            "Run again from inside that workspace dir, or pass the id "
            "(CLI: positional arg or `--strategy-id`)."
        )
    preview = ", ".join(ids[:5]) + (", …" if len(ids) > 5 else "")
    return (
        f"Multiple workspaces checked out — auto-detect can't pick one. "
        f"Pass the id (CLI: positional arg or `--strategy-id`). Known: {preview}"
    )


def _compute_hash(source: str) -> str:
    """Compute SHA256 hash matching the platform's compute_source_hash."""
    return hashlib.sha256(source.encode()).hexdigest()


def _normalize_paginated_versions(raw: Any) -> list[dict[str, Any]]:
    """Coerce the /versions endpoint response into a list of commit dicts.

    The endpoint historically returned a bare ``list[VersionResponse]``
    but the canonical platform shape is ``{"data": [...], "pagination":
    {...}}``. Some intermediate proxies have surfaced a third shape
    ``{"versions": [...]}``. Centralize the parsing so we don't repeat
    the if/elif soup at every call site.
    """
    if isinstance(raw, list):
        return [v for v in raw if isinstance(v, dict)]
    if isinstance(raw, dict):
        rows = raw.get("data") or raw.get("versions") or []
        return [v for v in rows if isinstance(v, dict)]
    return []


def _fetch_latest_commit_id(client: Any, strategy_id: str) -> str | None:
    """One-shot lookup for the current HEAD commit_id.

    PATCH /v1/strategies/<id> and POST /versions/restore both return
    StrategyResponse (which carries source_hash + sequence but NOT
    commit_id). Callers that need the commit_id to quote back to the
    user (or pin a backtest) follow up with this single GET.

    Best-effort: any error returns None rather than masking the caller's
    happy path. The mutation already succeeded by the time we get here.
    """
    try:
        raw = client.get(f"/v1/strategies/{strategy_id}/versions", limit=1)
    except Exception:
        return None
    versions = _normalize_paginated_versions(raw)
    if not versions:
        return None
    return versions[0].get("commit_id")


def _append_notes_entry(ws_dir: Path, entry: str) -> None:
    """Append a timestamped line to the workspace `notes.md`.

    Why this lives in the lib (not the outcome wrapper): every push from
    any surface (CLI / MCP / direct SDK) should leave the same trail —
    the agent and the user need a shared narrative of what's happened.
    A single source of truth for "what did I just do" beats reconstructing
    from `git log`-style history alone, since notes can include free-form
    rationale ("tuned ROC to 14 because mid-cycle drawdown was too deep").

    Failures are swallowed: notes are a best-effort agent breadcrumb,
    NOT a correctness primitive — never block a push because notes
    couldn't be written.
    """
    try:
        notes_path = ws_dir / NOTES_FILE
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"- {timestamp} — {entry}\n"
        if notes_path.exists():
            with notes_path.open("a", encoding="utf-8") as f:
                f.write(line)
        else:
            notes_path.write_text(
                f"# Workspace notes\n\nAuto-updated by `keel_strategy_push`. "
                f"Free to edit.\n\n{line}",
                encoding="utf-8",
            )
    except OSError:
        return


# ── Project-local workspace detection ──────────────────────────────────
# A "project" is any directory containing `.keel/workspace.yaml` (written
# by `keel project init`). When working inside one, strategy checkouts
# land in `<project>/strategies/<id>/` so the IDE can see them. Outside
# any project, falls back to the home-dir workspace (hidden but always
# available). The user opts into project mode by running `keel project
# init` in the directory they want to collaborate from.


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default: cwd) looking for `.keel/workspace.yaml`.

    Returns the directory containing `.keel/workspace.yaml`, or None if
    no project is found before hitting filesystem root.
    """
    cwd = (start or Path.cwd()).resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".keel" / "workspace.yaml").exists():
            return candidate
    return None


def project_workspace_root(project_root: Path) -> Path:
    """Per-project workspace directory: `<project>/strategies/`."""
    return project_root / "strategies"


def resolve_workspace_dir(
    strategy_id: str,
    *,
    explicit_dir: str | Path | None = None,
) -> tuple[Path, str]:
    """Pick the workspace directory for a strategy.

    Resolution priority:
      1. `explicit_dir` (caller passed `--dir`) → that exact path.
      2. Current directory is inside a project (cwd has `.keel/
         workspace.yaml` in cwd or any ancestor) → `<project>/
         strategies/<id>/`. The IDE-visible path.
      3. Otherwise → `~/.keel/workspace/<id>/`. Home-dir fallback, not
         visible to editors but always available.

    Returns ``(dir_path, mode)`` where mode is one of
    ``"explicit" | "project" | "home"`` — surface this in the agent
    response so callers can tell the user where the file landed and
    suggest `keel project init` when falling back to home.
    """
    if explicit_dir is not None:
        return Path(explicit_dir).expanduser().resolve() / strategy_id, "explicit"

    project = find_project_root()
    if project is not None:
        return project_workspace_root(project) / strategy_id, "project"

    return WORKSPACE_ROOT / strategy_id, "home"


def get_workspace_dir(strategy_id: str) -> Path:
    """Return the workspace dir for a strategy. Detects project-local +
    falls back to home — see `resolve_workspace_dir`.

    Kept as a thin wrapper for backward compat with callers that don't
    care about the mode.
    """
    path, _mode = resolve_workspace_dir(strategy_id)
    return path


def _scan_workspace_root(root: Path) -> list[WorkspaceMeta]:
    """Scan one workspace root dir for `.keel-meta.json` entries.

    Returns the list of WorkspaceMeta. Skips dirs that can't be read or
    have corrupted metadata.
    """
    if not root.exists():
        return []
    out: list[WorkspaceMeta] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / META_FILE
        if not meta_path.exists():
            continue
        try:
            out.append(WorkspaceMeta.load(d))
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def list_workspaces() -> list[WorkspaceMeta]:
    """List all checked-out strategies across home + current project.

    Scans both the home workspace root (`~/.keel/workspace/`) and the
    current project's workspace dir (`<project>/strategies/`) if cwd
    is inside one. Project-local entries come first in the result
    (more likely to be the user's active work).
    """
    project = find_project_root()
    found: list[WorkspaceMeta] = []
    if project is not None:
        found.extend(_scan_workspace_root(project_workspace_root(project)))
    home_entries = _scan_workspace_root(WORKSPACE_ROOT)
    # Dedupe by strategy_id — project-local wins if both exist.
    seen = {ws.strategy_id for ws in found}
    for ws in home_entries:
        if ws.strategy_id not in seen:
            found.append(ws)
            seen.add(ws.strategy_id)
    return found


def get_workspace(strategy_id: str) -> WorkspaceMeta | None:
    """Load workspace metadata for a strategy, or None if not checked out.

    Searches the current project first (if cwd is inside one), then the
    home workspace root. This lets push/pull/status/discard find an
    existing workspace regardless of which root it was checked out into.
    """
    # 1. Current project, if any
    project = find_project_root()
    if project is not None:
        ws_dir = project_workspace_root(project) / strategy_id
        meta_path = ws_dir / META_FILE
        if meta_path.exists():
            try:
                return WorkspaceMeta.load(ws_dir)
            except (json.JSONDecodeError, KeyError):
                pass

    # 2. Home workspace
    ws_dir = WORKSPACE_ROOT / strategy_id
    meta_path = ws_dir / META_FILE
    if not meta_path.exists():
        return None
    try:
        return WorkspaceMeta.load(ws_dir)
    except (json.JSONDecodeError, KeyError):
        return None


def _find_workspace_dir(strategy_id: str) -> Path | None:
    """Return the on-disk directory for an existing workspace, or None.

    Used by push/pull/status/discard to read/write the file regardless
    of which root it was checked out into.
    """
    project = find_project_root()
    if project is not None:
        candidate = project_workspace_root(project) / strategy_id
        if (candidate / META_FILE).exists():
            return candidate
    candidate = WORKSPACE_ROOT / strategy_id
    if (candidate / META_FILE).exists():
        return candidate
    return None


def find_workspace_strategy() -> WorkspaceMeta | None:
    """Auto-detect workspace strategy from CWD or single checkout.

    Resolution order:
    1. If CWD is inside a workspace dir (project or home) → use it
    2. If exactly one strategy is checked out → use it
    3. Otherwise → None
    """
    cwd = Path.cwd().resolve()

    # Candidate roots: project-local (if cwd is inside a project) + home
    roots: list[Path] = []
    project = find_project_root()
    if project is not None:
        roots.append(project_workspace_root(project))
    roots.append(WORKSPACE_ROOT)

    for root in roots:
        if not root.exists():
            continue
        try:
            cwd.relative_to(root)
        except ValueError:
            continue
        for d in root.iterdir():
            if not d.is_dir():
                continue
            try:
                cwd.relative_to(d)
            except ValueError:
                continue
            meta_path = d / META_FILE
            if meta_path.exists():
                try:
                    return WorkspaceMeta.load(d)
                except (json.JSONDecodeError, KeyError):
                    continue

    # Check for single checkout
    workspaces = list_workspaces()
    if len(workspaces) == 1:
        return workspaces[0]

    return None


def read_local_source(strategy_id: str) -> str:
    """Read the local working copy source.

    Searches both project-local and home workspaces (via
    `_find_workspace_dir`) so push/pull/status work regardless of where
    the workspace was originally checked out.
    """
    ws_dir = _find_workspace_dir(strategy_id) or get_workspace_dir(strategy_id)
    strategy_path = ws_dir / STRATEGY_FILE
    if not strategy_path.exists():
        raise FileNotFoundError(f"No local copy for strategy '{strategy_id}'")
    return strategy_path.read_text()


def write_local_source(strategy_id: str, source: str) -> None:
    """Write to the local working copy.

    Writes to the EXISTING workspace dir (project-local or home, found
    via `_find_workspace_dir`). Does NOT decide where a fresh checkout
    should land — that's `checkout()`'s job.
    """
    ws_dir = _find_workspace_dir(strategy_id) or get_workspace_dir(strategy_id)
    strategy_path = ws_dir / STRATEGY_FILE
    strategy_path.write_text(source)


def checkout(strategy_id: str, *, target_dir: str | Path | None = None) -> dict[str, Any]:
    """Pull a platform strategy into a local workspace.

    Fetches the strategy source from the API and creates a local
    working copy that can be edited, validated, and pushed back.

    Resolution priority for where to write (see
    `resolve_workspace_dir`):
      1. `target_dir` (explicit override) → that path / `<strategy_id>/`
      2. If cwd is inside a project (has `.keel/workspace.yaml`) →
         project-local at `<project>/strategies/<strategy_id>/`
      3. Else home fallback: `~/.keel/workspace/<strategy_id>/`

    Returns:
        Dict with workspace path, strategy metadata, AND a `mode` field
        ("explicit" | "project" | "home") + a `hint` when falling back
        to home so callers can encourage `keel project init` for IDE
        collaboration.
    """
    from keel.client import KeelClient

    client = KeelClient()
    try:
        strategy = client.get(f"/v1/strategies/{strategy_id}")
    finally:
        client.close()

    source = strategy.get("source")
    if not source:
        raise ValueError(f"Strategy '{strategy_id}' has no source code")

    ws_dir, mode = resolve_workspace_dir(strategy_id, explicit_dir=target_dir)
    ws_dir.mkdir(parents=True, exist_ok=True)

    # Write strategy source
    (ws_dir / STRATEGY_FILE).write_text(source)

    # Write sync metadata
    meta = WorkspaceMeta(
        strategy_id=strategy_id,
        name=strategy.get("name", "unnamed"),
        source_hash=strategy.get("source_hash", _compute_hash(source)),
        checked_out_at=datetime.now(timezone.utc).isoformat(),
        org_id=strategy.get("org_id"),
        current_sequence=strategy.get("current_sequence"),
    )
    meta.save(ws_dir)

    result: dict[str, Any] = {
        "strategy_id": strategy_id,
        "name": meta.name,
        "workspace": str(ws_dir),
        "file": str(ws_dir / STRATEGY_FILE),
        "source_hash": meta.source_hash,
        "sequence": meta.current_sequence,
        "status": "checked_out",
        "mode": mode,
    }
    # When falling back to the hidden home dir, surface a hint so the
    # caller can encourage the user to `keel project init` for IDE
    # collaboration. The agent reads this and tells the user; we don't
    # silently leave them with a hidden file they can't see.
    if mode == "home":
        result["hint"] = (
            "Checked out to the hidden home workspace because cwd isn't a Keel "
            "project. To collaborate with an editor/IDE on this strategy, run "
            "`keel project init` in your working directory, then re-checkout — "
            "the file will land where your IDE can see it."
        )
    return result


def push(
    strategy_id: str | None = None,
    message: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Push local changes to the platform.

    Sends the local working copy to the API via PATCH, using
    expected_source_hash for conflict detection unless force=True.

    Args:
        strategy_id: Strategy to push. Auto-detected if None.
        message: Commit message (optional).
        force: Skip conflict detection.

    Returns:
        Dict with push result including new source_hash and sequence.
    """
    if strategy_id is None:
        ws = find_workspace_strategy()
        if ws is None:
            raise ValueError(_no_workspace_error_message())
        strategy_id = ws.strategy_id

    meta = get_workspace(strategy_id)
    if meta is None:
        raise ValueError(f"Strategy '{strategy_id}' is not checked out")

    source = read_local_source(strategy_id)
    local_hash = _compute_hash(source)

    # Check if anything changed
    if local_hash == meta.source_hash:
        return {
            "strategy_id": strategy_id,
            "status": "no_changes",
            "source_hash": local_hash,
        }

    from keel.client import KeelClient

    body: dict[str, Any] = {"source": source}
    if not force:
        body["expected_source_hash"] = meta.source_hash
    if message:
        body["message"] = message

    client = KeelClient()
    try:
        result = client.patch(f"/v1/strategies/{strategy_id}", json=body)
        # PATCH returns StrategyResponse (no commit_id). Follow up so
        # callers can quote the new HEAD commit_id back to the user.
        commit_id = _fetch_latest_commit_id(client, strategy_id)
    finally:
        client.close()

    # Update local metadata — save to the existing workspace dir, not
    # whatever resolve_workspace_dir() picks now (cwd may differ).
    new_hash = result.get("source_hash", local_hash)
    meta.source_hash = new_hash
    meta.current_sequence = result.get("current_sequence", meta.current_sequence)
    meta.name = result.get("name", meta.name)
    ws_dir = _find_workspace_dir(strategy_id) or get_workspace_dir(strategy_id)
    meta.save(ws_dir)

    # Append a breadcrumb so the agent has a free-text history alongside
    # the formal commit log. Tags useful context: sequence, hash prefix,
    # message.
    note_parts = [f"push → sequence={meta.current_sequence}"]
    if commit_id:
        note_parts.append(f"commit_id={commit_id}")
    if new_hash:
        note_parts.append(f"hash={new_hash[:12]}")
    if message:
        note_parts.append(f'msg="{message}"')
    _append_notes_entry(ws_dir, " ".join(note_parts))

    return {
        "strategy_id": strategy_id,
        "name": meta.name,
        "status": "pushed",
        "source_hash": new_hash,
        "sequence": meta.current_sequence,
        "commit_id": commit_id,
        "compilation_error": result.get("compilation_error"),
    }


def pull(strategy_id: str | None = None) -> dict[str, Any]:
    """Pull latest source from the platform into local workspace.

    Args:
        strategy_id: Strategy to pull. Auto-detected if None.

    Returns:
        Dict with pull result.
    """
    if strategy_id is None:
        ws = find_workspace_strategy()
        if ws is None:
            raise ValueError(_no_workspace_error_message())
        strategy_id = ws.strategy_id

    meta = get_workspace(strategy_id)
    if meta is None:
        raise ValueError(f"Strategy '{strategy_id}' is not checked out")

    from keel.client import KeelClient

    client = KeelClient()
    try:
        strategy = client.get(f"/v1/strategies/{strategy_id}")
    finally:
        client.close()

    remote_source = strategy.get("source")
    if not remote_source:
        raise ValueError(f"Strategy '{strategy_id}' has no source on platform")

    remote_hash = strategy.get("source_hash", _compute_hash(remote_source))

    # Check if remote has changed
    if remote_hash == meta.source_hash:
        # Check if local has uncommitted changes
        local_source = read_local_source(strategy_id)
        local_hash = _compute_hash(local_source)
        if local_hash != meta.source_hash:
            return {
                "strategy_id": strategy_id,
                "status": "local_changes",
                "message": "Remote is unchanged but you have local edits. Push when ready.",
                "local_hash": local_hash,
                "remote_hash": remote_hash,
            }
        return {
            "strategy_id": strategy_id,
            "status": "current",
            "source_hash": remote_hash,
        }

    # Check for conflict (local modified AND remote modified)
    local_source = read_local_source(strategy_id)
    local_hash = _compute_hash(local_source)
    local_modified = local_hash != meta.source_hash

    if local_modified:
        return {
            "strategy_id": strategy_id,
            "status": "conflict",
            "message": "Both local and remote have changed. Use 'pull --force' to overwrite local, or 'push --force' to overwrite remote.",
            "local_hash": local_hash,
            "remote_hash": remote_hash,
            "base_hash": meta.source_hash,
        }

    # No local changes — safe to overwrite. Write to the existing
    # workspace dir, not whichever one resolve_workspace_dir picks now.
    ws_dir = _find_workspace_dir(strategy_id) or get_workspace_dir(strategy_id)
    (ws_dir / STRATEGY_FILE).write_text(remote_source)

    meta.source_hash = remote_hash
    meta.current_sequence = strategy.get("current_sequence", meta.current_sequence)
    meta.name = strategy.get("name", meta.name)
    meta.save(ws_dir)

    return {
        "strategy_id": strategy_id,
        "name": meta.name,
        "status": "pulled",
        "source_hash": remote_hash,
        "sequence": meta.current_sequence,
    }


def pull_force(strategy_id: str) -> dict[str, Any]:
    """Force-pull remote source, discarding local changes."""
    meta = get_workspace(strategy_id)
    if meta is None:
        raise ValueError(f"Strategy '{strategy_id}' is not checked out")

    from keel.client import KeelClient

    client = KeelClient()
    try:
        strategy = client.get(f"/v1/strategies/{strategy_id}")
    finally:
        client.close()

    remote_source = strategy.get("source")
    if not remote_source:
        raise ValueError(f"Strategy '{strategy_id}' has no source on platform")

    remote_hash = strategy.get("source_hash", _compute_hash(remote_source))
    ws_dir = _find_workspace_dir(strategy_id) or get_workspace_dir(strategy_id)
    (ws_dir / STRATEGY_FILE).write_text(remote_source)

    meta.source_hash = remote_hash
    meta.current_sequence = strategy.get("current_sequence", meta.current_sequence)
    meta.name = strategy.get("name", meta.name)
    meta.save(ws_dir)

    return {
        "strategy_id": strategy_id,
        "name": meta.name,
        "status": "force_pulled",
        "source_hash": remote_hash,
        "sequence": meta.current_sequence,
    }


def status(
    strategy_id: str | None = None,
    *,
    recent_commits: int = 0,
) -> dict[str, Any]:
    """Compare local workspace state against the platform.

    Args:
        strategy_id: Strategy to check. Auto-detected if None.
        recent_commits: If > 0, also fetch the most recent N commits
            from the server-side commit history. Useful for agents that
            want sync state AND "what did I/someone just do" in one
            call. 0 (default) skips the extra fetch — keeps `status`
            cheap for hot polling.

    Returns:
        Dict with local vs remote state comparison. Includes
        `recent_commits: list[dict]` when ``recent_commits > 0``.
    """
    if strategy_id is None:
        ws = find_workspace_strategy()
        if ws is None:
            raise ValueError(_no_workspace_error_message())
        strategy_id = ws.strategy_id

    meta = get_workspace(strategy_id)
    if meta is None:
        raise ValueError(f"Strategy '{strategy_id}' is not checked out")

    # Local state
    local_source = read_local_source(strategy_id)
    local_hash = _compute_hash(local_source)
    local_modified = local_hash != meta.source_hash

    # Remote state
    remote_hash = None
    remote_sequence = None
    remote_name = meta.name
    fetched_commits: list[dict[str, Any]] | None = None
    try:
        from keel.client import KeelClient

        client = KeelClient()
        try:
            strategy = client.get(f"/v1/strategies/{strategy_id}")
            remote_hash = strategy.get("source_hash")
            remote_sequence = strategy.get("current_sequence")
            remote_name = strategy.get("name", meta.name)
            if recent_commits > 0:
                try:
                    raw = client.get(
                        f"/v1/strategies/{strategy_id}/versions",
                        limit=recent_commits,
                    )
                    fetched_commits = [
                        {
                            "sequence_number": v.get("sequence_number"),
                            "commit_id": v.get("commit_id"),
                            "source_hash": (v.get("source_hash") or "")[:12],
                            "message": v.get("message"),
                            "created_at": v.get("created_at"),
                        }
                        for v in _normalize_paginated_versions(raw)
                    ]
                except Exception:
                    fetched_commits = None
        finally:
            client.close()
    except Exception:
        # Offline or no API key — show local state only
        pass

    remote_modified = remote_hash is not None and remote_hash != meta.source_hash

    actual_ws_dir = _find_workspace_dir(strategy_id) or get_workspace_dir(strategy_id)
    ws_status = WorkspaceStatus(
        strategy_id=strategy_id,
        name=remote_name or meta.name,
        local_hash=local_hash,
        remote_hash=remote_hash,
        remote_sequence=remote_sequence,
        local_modified=local_modified,
        remote_modified=remote_modified,
        conflict=local_modified and remote_modified,
        workspace_dir=str(actual_ws_dir),
    )

    out: dict[str, Any] = {
        "strategy_id": strategy_id,
        "name": ws_status.name,
        "state": ws_status.state,
        "local_hash": local_hash,
        "remote_hash": remote_hash,
        "base_hash": meta.source_hash,
        "local_modified": local_modified,
        "remote_modified": remote_modified,
        "sequence": remote_sequence,
        "workspace": ws_status.workspace_dir,
        "file": str(actual_ws_dir / STRATEGY_FILE),
    }
    if fetched_commits is not None:
        out["recent_commits"] = fetched_commits
    return out


def discard(strategy_id: str | None = None) -> dict[str, Any]:
    """Remove a local workspace (does not affect the platform strategy)."""
    if strategy_id is None:
        ws = find_workspace_strategy()
        if ws is None:
            raise ValueError(_no_workspace_error_message())
        strategy_id = ws.strategy_id

    # Find the EXISTING dir (project or home) — discard should clean up
    # whichever workspace was actually checked out, not whichever cwd
    # would resolve to right now.
    ws_dir = _find_workspace_dir(strategy_id)
    if ws_dir is None:
        raise ValueError(f"Strategy '{strategy_id}' is not checked out")

    import shutil

    shutil.rmtree(ws_dir)

    return {
        "strategy_id": strategy_id,
        "workspace": str(ws_dir),
        "status": "discarded",
    }
