"""Error formatting for DSL validation.

Converts ValidationResult into human-readable error reports with
line-numbered context, colored output (optional), and actionable suggestions.

Quick Start:
    >>> from pipeline_engine.dsl.errors import format_validation_errors
    >>> print(format_validation_errors(result))
"""

from __future__ import annotations

from pipeline_engine.validation_shared import ValidationIssue, ValidationResult


def format_validation_errors(
    result: ValidationResult,
    *,
    color: bool = False,
) -> str:
    """Format a ValidationResult into a human-readable report.

    Args:
        result: The validation result to format.
        color: If True, use ANSI color codes.

    Returns:
        Multi-line string with formatted error/warning messages.
    """
    if result.valid and not result.warnings and not result.type_flow:
        return "Status: VALID"

    lines: list[str] = []

    # Pipeline summary (if available)
    if result.pipeline_summary:
        lines.append(f"Pipeline: {result.pipeline_summary}")
        lines.append("")

    # Summary header
    error_count = len(result.errors)
    warning_count = len(result.warnings)

    parts = []
    if error_count:
        parts.append(f"{error_count} error{'s' if error_count != 1 else ''}")
    if warning_count:
        parts.append(f"{warning_count} warning{'s' if warning_count != 1 else ''}")
    summary = ", ".join(parts) if parts else "passed"

    lines.append(f"Validation: {summary}")
    lines.append("")

    # Format errors
    if result.errors:
        lines.append("Errors:")
        for issue in result.errors:
            lines.extend(_format_issue(issue, color, prefix="E"))
        lines.append("")

    # Format warnings
    if result.warnings:
        lines.append("Warnings:")
        for issue in result.warnings:
            lines.extend(_format_issue(issue, color, prefix="W"))
        lines.append("")

    # Type flow summary (if available, excluding slot_op entries)
    flow_entries = [e for e in result.type_flow if e.category != "slot_op"]
    if flow_entries:
        lines.append("Type Flow:")
        for entry in flow_entries:
            lines.append(f"  {entry.step}: {entry.input_type} -> {entry.output_type}")
        lines.append("")

    # Slots section (slot_op entries from type_flow)
    slot_entries = [e for e in result.type_flow if e.category == "slot_op"]
    if slot_entries:
        lines.append("Slots:")
        for entry in slot_entries:
            lines.append(f"  {entry.step} ({entry.output_type})")
        lines.append("")

    # Status line
    if result.valid:
        lines.append("Status: VALID")
    else:
        lines.append(f"Status: INVALID ({error_count} error{'s' if error_count != 1 else ''})")

    return "\n".join(lines)


def _format_issue(
    issue: ValidationIssue,
    color: bool,
    prefix: str = "E",
) -> list[str]:
    """Format a single validation issue."""
    lines: list[str] = []

    # Issue header
    code = issue.code
    msg = issue.message
    loc = issue.location

    if color:
        if prefix == "E":
            header = f"  \033[31m{prefix} [{code}]\033[0m {msg}"
        else:
            header = f"  \033[33m{prefix} [{code}]\033[0m {msg}"
    else:
        header = f"  {prefix} [{code}] {msg}"
    lines.append(header)

    # Location
    if loc:
        lines.append(f"    at: {loc}")

    # Suggestion
    if issue.suggestion:
        if color:
            lines.append(f"    \033[36mHint: {issue.suggestion}\033[0m")
        else:
            lines.append(f"    Hint: {issue.suggestion}")

    return lines


def format_error_summary(result: ValidationResult) -> str:
    """One-line summary of validation errors."""
    if result.valid:
        return "OK"
    error_codes = sorted(set(e.code for e in result.errors))
    return f"FAIL: {', '.join(error_codes)}"


__all__ = [
    "format_error_summary",
    "format_validation_errors",
]
