"""Pipeline DSL — parse, validate, resolve strategy files.

Quick Start:
    >>> strategy = parse_strategy(source_code)
    >>> result = validate_strategy(strategy)
"""

from pipeline_engine.dsl.emitter import graph_to_spec, spec_to_dsl, spec_to_graph
from pipeline_engine.dsl.errors import format_error_summary, format_validation_errors
from pipeline_engine.dsl.parser import DSLParseError, parse_strategy
from pipeline_engine.dsl.validator import validate_strategy


__all__ = [
    "DSLParseError",
    "ResolveError",
    "format_error_summary",
    "format_validation_errors",
    "graph_to_spec",
    "parse_strategy",
    "spec_to_dsl",
    "spec_to_graph",
    "validate_strategy",
]
