"""The full profile's instructions and served operating core at the paper base.

paper-trading-v2 S06 §3.9 row 3, §3.11 item 1 (PT2-28): the operating core
gains a ``paper`` section carrying ``when: paper_loaded``, so while the paper
SDK is held the full-profile instructions and the document ``keel_help
topic=operating_core`` serves are HEAD's byte for byte. This module captures
HEAD's: it assembles both from the corpus source and the builder as they
stood at the paper base commit (``BASE_COMMIT``, the same base as
``fixtures/listed_surface_head.json``), read with ``git show`` into a
temporary directory and imported from there, into
``fixtures/full_instructions_head.json``.

Re-capture (only for a non-paper corpus change, with review): from the SDK
root, ``python -m tests._full_instructions_head capture`` in the SDK venv with
``env -u PYTHONPATH``, then ``scripts/ci/prettier-pinned.sh --write`` on the
fixture.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tests._listed_head_fixtures import BASE_COMMIT


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "full_instructions_head.json"
REPO_ROOT = Path(__file__).resolve().parents[4]
_SOURCES = (
    "libs/pipeline_engine/reference/system/assemble.py",
    "libs/pipeline_engine/reference/system/operating_core.md",
)
#: The full profiles and the two live-write states the server assembles.
PROFILES = ("full-local", "full-hosted")


def key(profile: str, live_write_loaded: bool) -> str:
    return f"{profile}:live_write_loaded={str(live_write_loaded).lower()}"


def _base_assemble(tmp: Path) -> Any:
    for rel in _SOURCES:
        text = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{BASE_COMMIT}:{rel}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        (tmp / Path(rel).name).write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("assemble_at_base", tmp / "assemble.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the base commit's assemble.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def capture() -> None:
    with tempfile.TemporaryDirectory(prefix="keel-full-instructions-head-") as tmp:
        assemble = _base_assemble(Path(tmp))
        try:
            if assemble.core_path() != Path(tmp) / "operating_core.md":
                raise SystemExit(f"assembled from {assemble.core_path()}, not the base copy")
            instructions = {
                key(profile, loaded): assemble.instructions(profile, live_write_loaded=loaded)
                for profile in PROFILES
                for loaded in (False, True)
            }
            served = {profile: assemble.base_document(profile=profile) for profile in PROFILES}
        finally:
            sys.modules.pop("assemble_at_base", None)
    if any("keel_paper" in text for text in [*instructions.values(), *served.values()]):
        raise SystemExit("the base corpus names a paper tool; wrong base commit")
    FIXTURE.write_text(
        json.dumps(
            {
                "base_commit": BASE_COMMIT,
                "sources": list(_SOURCES),
                "instructions": instructions,
                "served_operating_core": served,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {FIXTURE} ({len(instructions)} instructions, {len(served)} served documents)")


def load() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    if sys.argv[1:] != ["capture"]:
        raise SystemExit("usage: python -m tests._full_instructions_head capture")
    capture()
