"""Pinned Use syntax and engine-only stored expansion contracts."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pipeline_engine.dsl.spec import SourceLocation, StrategyFile
    from pipeline_engine.validation_shared import OutputDescriptor

USE_REF_PATTERN = re.compile(r"^str_[0-9a-z]{26}@[1-9][0-9]*$")
USE_MAX_DEPTH = 8


class UseParseError(ValueError):
    def __init__(self, message: str, *, location: SourceLocation | None = None):
        self.code = "USE_REF_MALFORMED"
        self.message = message
        self.location = location
        super().__init__(message)


@dataclass
class UseSpec:
    ref: str
    output: str
    as_: str | None
    location: SourceLocation


@dataclass(frozen=True)
class StoredOutputExpansion:
    descriptor: OutputDescriptor
    slice_text: str
    slice_hash: str
    lookback_bars: int | None = None
    loaders: tuple[str, ...] = ()
    universe_sensitive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor": self.descriptor.to_dict(),
            **self.stored_metadata(),
        }

    def stored_metadata(self) -> dict[str, Any]:
        """Commit column projection; static descriptor is owned by the blob."""
        return {
            "slice_text": self.slice_text,
            "slice_hash": self.slice_hash,
            "lookback_bars": self.lookback_bars,
            "loaders": list(self.loaders),
            "universe_sensitive": self.universe_sensitive,
        }


@dataclass(frozen=True)
class OutputExpansion:
    ref: str
    outputs: dict[str, StoredOutputExpansion]
    source_lock: dict[str, int] = field(default_factory=dict)
    source_timeframe: str | None = None
    depth: int = 1
    source_shown: bool = True
    source_shown_at: str | None = None
    source_bar_offset: str | None = None
    # Database lineage only: never expose hidden authors in validation/preview DTOs.
    source_principal_id: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "outputs": {name: value.to_dict() for name, value in self.outputs.items()},
            "source_lock": self.source_lock,
            "source_timeframe": self.source_timeframe,
            "depth": self.depth,
            "source_shown": self.source_shown,
            "source_shown_at": self.source_shown_at,
            "source_bar_offset": self.source_bar_offset,
        }


def expansion_from_dict(value: dict[str, Any]) -> OutputExpansion:
    from pipeline_engine.validation_shared import OutputDescriptor

    outputs = {}
    for name, output in value["outputs"].items():
        outputs[name] = StoredOutputExpansion(
            descriptor=OutputDescriptor.from_dict(output["descriptor"]),
            slice_text=output["slice_text"],
            slice_hash=output["slice_hash"],
            lookback_bars=output.get("lookback_bars"),
            loaders=tuple(output.get("loaders", [])),
            universe_sensitive=output.get("universe_sensitive", False),
        )
    return OutputExpansion(
        ref=value["ref"],
        outputs=outputs,
        source_lock=value.get("source_lock", {}),
        source_timeframe=value.get("source_timeframe"),
        depth=value.get("depth", 1),
        source_shown=value.get("source_shown", True),
        source_shown_at=value.get("source_shown_at"),
        source_bar_offset=value.get("source_bar_offset"),
    )


def expansion_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_use_ref(ref: str) -> tuple[str, int]:
    if not isinstance(ref, str) or not USE_REF_PATTERN.fullmatch(ref):
        raise UseParseError(
            f"Use reference {ref!r} is malformed; expected str_<26 lowercase letters or digits>@<positive version>."
        )
    block, version = ref.rsplit("@", 1)
    return block, int(version)


def parse_use_call(node: ast.Call, *, location: SourceLocation) -> UseSpec:
    try:
        if len(node.args) != 1:
            raise UseParseError("Use takes one pinned reference and output=, with optional as_=.")
        ref = ast.literal_eval(node.args[0])
        parse_use_ref(ref)
        params = {}
        for keyword in node.keywords:
            if keyword.arg not in {"output", "as_"} or keyword.arg in params:
                raise UseParseError(
                    "Use accepts only output= and optional as_=; no parameters in v1."
                )
            params[keyword.arg] = ast.literal_eval(keyword.value)
        if not isinstance(params.get("output"), str) or not params["output"]:
            raise UseParseError("Use requires a nonempty output= string.")
        if "as_" in params and (not isinstance(params["as_"], str) or not params["as_"]):
            raise UseParseError("Use as_= must be a nonempty role string when supplied.")
        return UseSpec(ref, params["output"], params.get("as_"), location)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise UseParseError(str(exc), location=location) from exc


def format_use_call(step: UseSpec) -> str:
    role = f", as_={step.as_!r}" if step.as_ is not None else ""
    return f"Use({step.ref!r}, output={step.output!r}{role})"


def iter_uses(strategy: StrategyFile):
    """Source-order traversal including variables and factory bodies."""
    from pipeline_engine.dsl.spec import ParallelSpec, PipelineSpec

    def walk(steps, path):
        for index, step in enumerate(steps):
            at = f"{path}.step[{index}]"
            if isinstance(step, UseSpec):
                yield at, step
            elif isinstance(step, PipelineSpec):
                yield from walk(step.steps, at)
            elif isinstance(step, ParallelSpec):
                for name, branch in step.branches.items():
                    yield from walk(branch, f"{at}.branch[{name}]")

    for factory in strategy.factories:
        yield from walk(factory.body.steps, f"factory[{factory.name}]")
    for variable in strategy.variables:
        if isinstance(variable.value, PipelineSpec):
            yield from walk(variable.value.steps, f"var[{variable.name}]")
    yield from walk(strategy.pipeline.steps, "pipeline")


def collect_use_refs(strategy: StrategyFile) -> list[str]:
    return list(dict.fromkeys(step.ref for _, step in iter_uses(strategy)))
