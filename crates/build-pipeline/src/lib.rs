//! Build pipeline for LLM-emitted strategies.
//!
//! Pipeline stages, in order:
//!
//! 1. [`linter`] — parse Rust source, reject `unsafe` and other banned
//!    constructs; parse the strategy manifest, reject non-whitelisted
//!    dependencies.
//! 2. [`artifact_cache`] — content-addressed cache keyed by
//!    `blake3(source + manifest + runner_version)`; reuse on hit.
//! 3. [`driver`] — lay out a Cargo project on disk and invoke `cargo build`
//!    against the configured toolchain. (See `driver::BuildDriver` for the
//!    API; the actual `cargo` invocation is wired via an injected
//!    [`driver::Cargo`] trait so the rest of the pipeline can be unit-tested
//!    without spawning compilers.)
//! 4. [`migration`] — runner-version migration check. Real migration
//!    (regenerate source through the LLM) lives upstream; this module
//!    decides *whether* migration is needed.

pub mod artifact_cache;
pub mod driver;
pub mod error;
pub mod linter;
pub mod migration;
pub mod whitelist;

pub use artifact_cache::{ArtifactCache, ArtifactKey, CachedArtifact};
pub use driver::{BuildDriver, BuildOutcome, Cargo, StrategyManifest};
pub use error::BuildError;
pub use linter::{lint_manifest, lint_source, LintReport};
pub use migration::{migration_decision, MigrationDecision};
pub use whitelist::Whitelist;
