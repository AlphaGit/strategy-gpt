//! Reference strategy cdylib used as a fixture for the plugin-loading path.
//!
//! Implements the simplest viable strategy (does nothing, just records the
//! lifecycle methods it sees) and invokes [`engine_rt::strategy_entry!`] to
//! emit the C-ABI registration symbols the engine worker resolves via
//! `libloading`.
//!
//! The build pipeline will lay out LLM-emitted strategies in a similar
//! shape; this crate exists so the workspace `cargo check` validates the
//! macro expansion and an integration test in `engine` can load the
//! compiled artifact end-to-end.

use engine_rt::{strategy_entry, Bar, Context, Fill, Result, Sealed, Strategy, StrategyMeta};

/// Minimal no-op strategy. Records `on_init` / `on_end` count to prove the
/// engine drove the lifecycle without doing anything user-facing.
#[derive(Default)]
pub struct NoopStrategy {
    init_calls: u32,
    end_calls: u32,
    bar_calls: u32,
}

impl Sealed for NoopStrategy {}

impl Strategy for NoopStrategy {
    fn metadata(&self) -> StrategyMeta {
        StrategyMeta::new(
            "example_noop",
            "0.1.0",
            "engine-rt fixture",
            "Reference no-op strategy that records lifecycle counts.",
        )
    }

    fn on_init(&mut self, _ctx: &mut dyn Context) -> Result<()> {
        self.init_calls += 1;
        Ok(())
    }

    fn on_bar(&mut self, _bar: &Bar, _ctx: &mut dyn Context) -> Result<()> {
        self.bar_calls += 1;
        Ok(())
    }

    fn on_fill(&mut self, _fill: &Fill, _ctx: &mut dyn Context) -> Result<()> {
        Ok(())
    }

    fn on_end(&mut self, _ctx: &mut dyn Context) -> Result<()> {
        self.end_calls += 1;
        Ok(())
    }
}

fn factory() -> Box<dyn Strategy> {
    Box::<NoopStrategy>::default()
}

strategy_entry!(factory);
