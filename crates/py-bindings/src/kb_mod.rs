//! PyO3 bindings for the knowledge base.
//!
//! Surface (per task 8.7):
//! - `KnowledgeBase(db_path, base_dir)` — opens / creates the SQLite-backed
//!   store; uses the v1 [`HashEmbedder`](kb::HashEmbedder) + a permissive
//!   keyword extractor by default (overrides land with the orchestrator's
//!   ingestion adapter).
//! - `retrieve(query, k) -> str` — JSON [`kb::RetrievalResult`].
//! - `add_source(source_json) -> str` — JSON [`kb::IngestOutcome`].
//! - `add_source_from_text(source_json, text) -> str` — JSON
//!   [`kb::IngestOutcome`]; used by smoke tests where source content is
//!   in-memory rather than on disk.
//! - `reingest(source_list_toml) -> str` — JSON list of
//!   [`kb::IngestOutcome`].

use std::path::PathBuf;

use kb::extract::CoocurrenceRule;
use kb::extract::KeywordRule;
use kb::source::SourceConfig;
use kb::{EdgeKind, HashEmbedder, KeywordExtractor, KnowledgeBase as RustKb, NodeKind, SourceList};
use pyo3::prelude::*;

use crate::{json_err, runtime_err};

/// Default extractor used when the orchestrator opens a KB without supplying
/// its own. Surfaces the strategy-gpt domain vocabulary (volatility regime,
/// VIX/VXX, momentum, mean reversion) so the starter corpus produces a
/// reasonable graph even before a smarter extractor is wired in.
fn default_extractor() -> KeywordExtractor {
    let mut x = KeywordExtractor::new();
    let kw = |k: &str, kind: NodeKind, summary: &str| KeywordRule {
        keyword: k.to_string(),
        node_id: k.to_lowercase(),
        node_kind: kind,
        summary: summary.to_string(),
    };
    x = x
        .rule(kw("vix", NodeKind::Indicator, "CBOE volatility index"))
        .rule(kw("vxx", NodeKind::Indicator, "VIX short-term futures ETN"))
        .rule(kw("rsi", NodeKind::Indicator, "Relative strength index"))
        .rule(kw("ema", NodeKind::Indicator, "Exponential moving average"))
        .rule(kw("atr", NodeKind::Indicator, "Average true range"))
        .rule(kw(
            "backwardation",
            NodeKind::Regime,
            "Inverted futures curve",
        ))
        .rule(kw(
            "contango",
            NodeKind::Regime,
            "Upward-sloping futures curve",
        ))
        .rule(kw(
            "mean reversion",
            NodeKind::Technique,
            "Reversion to a mean",
        ))
        .rule(kw("momentum", NodeKind::Technique, "Trend continuation"))
        .rule(kw(
            "walk-forward",
            NodeKind::Technique,
            "Time-rolling validation",
        ))
        .rule(kw("regime", NodeKind::Concept, "Market regime"))
        .cooccurrence(CoocurrenceRule {
            src_keyword: "vix".to_string(),
            dst_keyword: "backwardation".to_string(),
            kind: EdgeKind::EmpiricalSupport,
            weight: 1.0,
        })
        .cooccurrence(CoocurrenceRule {
            src_keyword: "vxx".to_string(),
            dst_keyword: "backwardation".to_string(),
            kind: EdgeKind::FailsInRegime,
            weight: 1.0,
        })
        .cooccurrence(CoocurrenceRule {
            src_keyword: "mean reversion".to_string(),
            dst_keyword: "rsi".to_string(),
            kind: EdgeKind::Implements,
            weight: 1.0,
        });
    x
}

#[pyclass(name = "KnowledgeBase", unsendable)]
pub struct PyKnowledgeBase {
    inner: RustKb<HashEmbedder, KeywordExtractor>,
}

#[pymethods]
impl PyKnowledgeBase {
    #[new]
    #[pyo3(signature = (db_path, base_dir, embedding_dim = 64))]
    fn new(db_path: &str, base_dir: &str, embedding_dim: usize) -> PyResult<Self> {
        let kb = RustKb::open(
            std::path::Path::new(db_path),
            std::path::Path::new(base_dir),
            HashEmbedder::new(embedding_dim),
            default_extractor(),
        )
        .map_err(runtime_err)?;
        Ok(Self { inner: kb })
    }

    /// JSON [`kb::RetrievalResult`].
    fn retrieve(&self, query: &str, k: usize) -> PyResult<String> {
        let res = self.inner.retrieve(query, k).map_err(runtime_err)?;
        serde_json::to_string(&res).map_err(runtime_err)
    }

    /// `source_json` is a JSON-encoded [`SourceConfig`]. Reads the file at
    /// `cfg.path` (resolved against the KB's `base_dir`) and ingests it.
    fn add_source(&mut self, source_json: &str) -> PyResult<String> {
        let cfg: SourceConfig = serde_json::from_str(source_json).map_err(json_err)?;
        let outcome = self.inner.add_source(&cfg).map_err(runtime_err)?;
        serde_json::to_string(&outcome).map_err(runtime_err)
    }

    fn add_source_from_text(&mut self, source_json: &str, text: &str) -> PyResult<String> {
        let cfg: SourceConfig = serde_json::from_str(source_json).map_err(json_err)?;
        let outcome = self
            .inner
            .add_source_from_text(&cfg, text)
            .map_err(runtime_err)?;
        serde_json::to_string(&outcome).map_err(runtime_err)
    }

    fn reingest(&mut self, source_list_toml: &str) -> PyResult<String> {
        let list = SourceList::from_toml(source_list_toml).map_err(runtime_err)?;
        let outcomes = self.inner.reingest(&list).map_err(runtime_err)?;
        serde_json::to_string(&outcomes).map_err(runtime_err)
    }

    fn base_dir(&self) -> PyResult<String> {
        Ok(self.inner.base_dir.to_string_lossy().to_string())
    }

    fn source_count(&self) -> PyResult<i64> {
        self.inner.store.source_count().map_err(runtime_err)
    }
}

// Required by pyo3 when the type is referenced in error paths; PathBuf is
// captured here so the trait is in scope above without an extra import.
#[allow(dead_code)]
fn _path_check(p: PathBuf) -> PathBuf {
    p
}

#[derive(serde::Serialize)]
struct _DimSerde {
    dim: usize,
}
