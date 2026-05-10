//! Bulk per-run array storage.
//!
//! v1 writes JSON sidecars under `<ledger_root>/sidecars/<run_id>/`. Parquet
//! upgrade is task 6.3 follow-up; the [`SidecarKind`] enum and [`SidecarStore`]
//! API stay shape-stable so the swap is internal.

use std::path::{Path, PathBuf};

use engine::result::{EquityPoint, Trade};
use engine_rt::{DecisionEvent, SignalEvent};
use serde::Serialize;

use crate::error::LedgerError;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SidecarKind {
    Trades,
    Signals,
    Equity,
    ExecLog,
}

impl SidecarKind {
    fn filename(self) -> &'static str {
        match self {
            SidecarKind::Trades => "trades.json",
            SidecarKind::Signals => "signals.json",
            SidecarKind::Equity => "equity.json",
            SidecarKind::ExecLog => "exec_log.json",
        }
    }
}

#[derive(Clone, Debug)]
pub struct SidecarStore {
    root: PathBuf,
}

impl SidecarStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn run_dir(&self, run_id: &str) -> PathBuf {
        self.root.join(run_id)
    }

    pub fn write_trades(&self, run_id: &str, trades: &[Trade]) -> Result<PathBuf, LedgerError> {
        self.write(run_id, SidecarKind::Trades, trades)
    }

    pub fn write_signals(
        &self,
        run_id: &str,
        signals: &[SignalEvent],
    ) -> Result<PathBuf, LedgerError> {
        self.write(run_id, SidecarKind::Signals, signals)
    }

    pub fn write_equity(
        &self,
        run_id: &str,
        equity: &[EquityPoint],
    ) -> Result<PathBuf, LedgerError> {
        self.write(run_id, SidecarKind::Equity, equity)
    }

    pub fn write_exec_log(
        &self,
        run_id: &str,
        log: &[DecisionEvent],
    ) -> Result<PathBuf, LedgerError> {
        self.write(run_id, SidecarKind::ExecLog, log)
    }

    pub fn read_trades(&self, run_id: &str) -> Result<Vec<Trade>, LedgerError> {
        self.read(run_id, SidecarKind::Trades)
    }

    pub fn read_signals(&self, run_id: &str) -> Result<Vec<SignalEvent>, LedgerError> {
        self.read(run_id, SidecarKind::Signals)
    }

    pub fn read_equity(&self, run_id: &str) -> Result<Vec<EquityPoint>, LedgerError> {
        self.read(run_id, SidecarKind::Equity)
    }

    pub fn read_exec_log(&self, run_id: &str) -> Result<Vec<DecisionEvent>, LedgerError> {
        self.read(run_id, SidecarKind::ExecLog)
    }

    fn write<T: Serialize + ?Sized>(
        &self,
        run_id: &str,
        kind: SidecarKind,
        value: &T,
    ) -> Result<PathBuf, LedgerError> {
        let dir = self.run_dir(run_id);
        std::fs::create_dir_all(&dir)?;
        let path = dir.join(kind.filename());
        let bytes = serde_json::to_vec(value)?;
        std::fs::write(&path, bytes)?;
        Ok(path)
    }

    fn read<T: for<'de> serde::Deserialize<'de>>(
        &self,
        run_id: &str,
        kind: SidecarKind,
    ) -> Result<T, LedgerError> {
        let path = self.run_dir(run_id).join(kind.filename());
        if !path.exists() {
            return Err(LedgerError::NotFound(format!(
                "sidecar {} for run {run_id}",
                kind.filename()
            )));
        }
        let bytes = std::fs::read(path)?;
        let value: T = serde_json::from_slice(&bytes)?;
        Ok(value)
    }
}
