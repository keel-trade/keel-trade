"""Component registry — SDK shim re-exporting from registry_types.py."""

from pipeline_engine.base.registry_types import *  # noqa: F401,F403

# Explicit re-exports: `import *` does not bind underscore names, but SDK
# consumers (dsl/validator.py, scripts/build_data.py) import these from
# this shim. noqa'd per-name 2026-07-10 (M1.3) — removing them breaks the
# SDK validator at runtime.
from pipeline_engine.base.registry_types import (
    _build_effective_registry,  # noqa: F401
    _resolve_param_type,  # noqa: F401
    _resolve_type_name,  # noqa: F401
)
