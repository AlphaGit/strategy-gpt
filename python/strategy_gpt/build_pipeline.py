"""Python wrapper around the PyO3 ``BuildPipeline`` class.

Surface mirrors `crates/py-bindings/src/build_mod.rs`:

- :meth:`BuildPipeline.lint` — run source + manifest lint only.
- :meth:`BuildPipeline.build` — full pipeline (lint → cache → cargo build);
  errors return as :class:`BuildFailure`, not raw exceptions, so the Tester
  can convert them into a ``rejected: build_failed`` decision with the
  structured diagnostic attached.

The pydantic mirrors here intentionally duplicate the
`build_pipeline::StrategyManifest` shape — the build pipeline's manifest
type lives in trusted Rust and never crosses the FFI boundary directly.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ._native_shim import require_native
from .types import RunnerVersion

BuildProfile = Literal["release", "debug"]


class ManifestDep(BaseModel):
    """One ``[dependencies]`` entry in a strategy crate's Cargo.toml.

    Mirrors `build_pipeline::driver::ManifestDep`. ``req`` is passed through
    to Cargo verbatim; the whitelist linter checks crate names only, not
    version requirement strings.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    req: str


class StrategyManifest(BaseModel):
    """Subset of a strategy crate's Cargo.toml. Mirrors
    `build_pipeline::driver::StrategyManifest`.

    The build pipeline injects ``engine-rt`` as a path dependency; a manifest
    that lists ``engine-rt`` here is de-duplicated transparently by
    SystemCargo.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    dependencies: list[ManifestDep] = Field(default_factory=list)
    dev_dependencies: list[ManifestDep] = Field(default_factory=list)
    build_dependencies: list[ManifestDep] = Field(default_factory=list)


class LintReport(BaseModel):
    """Result of running source + manifest lint without building."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    source_violations: list[str] = Field(default_factory=list)
    manifest_violations: list[str] = Field(default_factory=list)


class BuildOutcomeKind(StrEnum):
    CACHE_HIT = "cache_hit"
    COMPILED = "compiled"


class BuildArtifact(BaseModel):
    """Compiled strategy artifact metadata. Mirrors
    `build_pipeline::artifact_cache::CachedArtifact`.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    library_path: str
    runner_version: RunnerVersion
    source_size_bytes: int


class BuildOutcome(BaseModel):
    """Successful build result."""

    model_config = ConfigDict(frozen=True)

    kind: BuildOutcomeKind
    artifact: BuildArtifact


class BuildErrorKind(StrEnum):
    """Tag for the failure mode of a build invocation. Mirrors the
    `build_pipeline::BuildError` variants.
    """

    SOURCE_LINT = "source_lint"
    MANIFEST_LINT = "manifest_lint"
    WHITELIST = "whitelist"
    IO = "io"
    CARGO = "cargo"
    ARTIFACT_CACHE = "artifact_cache"
    MIGRATION = "migration"


class BuildFailure(Exception):  # noqa: N818 — surface name is the contract; "Failure" reads better than "Error" for a non-fatal pipeline outcome
    """Structured build failure carrying the diagnostic and failure kind.

    The Tester (`tester::compile-and-lint-validation`) catches this and
    records ``rejected: build_failed`` with the message + kind in the ledger.
    """

    def __init__(self, kind: BuildErrorKind, message: str) -> None:
        super().__init__(f"[{kind.value}] {message}")
        self.kind = kind
        self.message = message


class _BuildPipelineLike(Protocol):
    """Protocol used by the Tester to allow stub pipelines in unit tests."""

    def build(self, source: str, manifest: StrategyManifest) -> BuildOutcome: ...

    def lint(self, source: str, manifest: StrategyManifest) -> LintReport: ...


class BuildPipeline:
    """High-level wrapper over `strategy_gpt._native.build.BuildPipeline`."""

    def __init__(
        self,
        cache_root: Path | str,
        work_root: Path | str,
        engine_rt_path: Path | str,
        whitelist_path: Path | str,
        *,
        profile: BuildProfile = "release",
    ) -> None:
        native = require_native()
        self._inner = native.build.BuildPipeline(
            str(cache_root),
            str(work_root),
            str(engine_rt_path),
            str(whitelist_path),
            profile,
        )

    def lint(self, source: str, manifest: StrategyManifest) -> LintReport:
        raw: str = self._inner.lint(source, manifest.model_dump_json())
        return LintReport.model_validate_json(raw)

    def build(self, source: str, manifest: StrategyManifest) -> BuildOutcome:
        raw: str = self._inner.build(source, manifest.model_dump_json())
        envelope = json.loads(raw)
        if "ok" in envelope:
            return BuildOutcome.model_validate(envelope["ok"])
        err = envelope["err"]
        raise BuildFailure(kind=BuildErrorKind(err["kind"]), message=err["message"])


__all__ = [
    "BuildArtifact",
    "BuildErrorKind",
    "BuildFailure",
    "BuildOutcome",
    "BuildOutcomeKind",
    "BuildPipeline",
    "BuildProfile",
    "LintReport",
    "ManifestDep",
    "StrategyManifest",
]
