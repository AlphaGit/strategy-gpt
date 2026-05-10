use crate::bar::Bar;
use crate::context::Context;
use crate::error::Result;
use crate::order::Fill;
use crate::sealed::Sealed;
use crate::version::{RunnerVersion, RUNNER_VERSION};
use serde::{Deserialize, Serialize};

/// Identifying metadata recorded into the artifact at build time.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StrategyMeta {
    pub name: String,
    pub version: String,
    pub author: String,
    pub description: String,
    /// Runner ABI the strategy was built against. Set automatically via the
    /// build pipeline; strategies must not override.
    pub runner_version: RunnerVersion,
}

impl StrategyMeta {
    /// Convenience used by generated strategies. The build pipeline injects
    /// [`RUNNER_VERSION`] when the artifact is laid out.
    pub fn new(
        name: impl Into<String>,
        version: impl Into<String>,
        author: impl Into<String>,
        description: impl Into<String>,
    ) -> Self {
        Self {
            name: name.into(),
            version: version.into(),
            author: author.into(),
            description: description.into(),
            runner_version: RUNNER_VERSION,
        }
    }
}

/// Sealed trait every strategy implements.
///
/// The seal prevents implementations outside the runtime; new strategy crates
/// inherit the seal automatically through this crate's `Sealed` impl on every
/// concrete strategy type the build pipeline generates.
pub trait Strategy: Sealed {
    fn metadata(&self) -> StrategyMeta;

    fn on_init(&mut self, _ctx: &mut dyn Context) -> Result<()> {
        Ok(())
    }

    fn on_bar(&mut self, bar: &Bar, ctx: &mut dyn Context) -> Result<()>;

    fn on_fill(&mut self, _fill: &Fill, _ctx: &mut dyn Context) -> Result<()> {
        Ok(())
    }

    fn on_end(&mut self, _ctx: &mut dyn Context) -> Result<()> {
        Ok(())
    }
}
