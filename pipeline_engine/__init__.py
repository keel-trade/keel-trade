"""Pipeline engine — DSL subset for the Keel SDK.

Contains only the language tooling (parser, validator, emitter, differ)
and registry infrastructure. No runtime execution, no component implementations.
"""

from pipeline_engine.dsl import parse_strategy, validate_strategy
from pipeline_engine.dsl.emitter import spec_to_dsl, spec_to_graph
from pipeline_engine.dsl.differ import diff_strategies

__all__ = [
    "parse_strategy",
    "validate_strategy",
    "spec_to_dsl",
    "spec_to_graph",
    "diff_strategies",
]
