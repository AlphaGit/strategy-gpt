//! Curated source list format.
//!
//! Spec `knowledge-base::curated-ingestion-pipeline`: ingestion accepts a
//! human-approved list of resources. The format is TOML with a `[[source]]`
//! array entry per resource. Per-source config carries chunking and metadata
//! knobs but never an "auto-scrape" toggle: human approval is mandatory.

use serde::{Deserialize, Serialize};

use crate::error::KbError;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceKind {
    Book,
    Paper,
    Article,
    Note,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceConfig {
    /// Stable identifier used for provenance. SHOULD be human-meaningful (e.g.
    /// `"hull-options-2018"`), so citations remain readable in the ledger.
    pub id: String,
    pub kind: SourceKind,
    pub title: String,
    #[serde(default)]
    pub author: Option<String>,
    #[serde(default)]
    pub year: Option<i32>,
    /// Filesystem path to the resource text. Plain UTF-8 only at v1; pdf/epub
    /// extraction happens upstream in the `kb/` ingestion glue.
    pub path: String,
    /// Optional default section label applied to all chunks unless overridden.
    #[serde(default)]
    pub section: Option<String>,
    #[serde(default = "default_chunk_size")]
    pub chunk_size: usize,
    #[serde(default = "default_chunk_overlap")]
    pub chunk_overlap: usize,
}

fn default_chunk_size() -> usize {
    600
}

fn default_chunk_overlap() -> usize {
    80
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceList {
    #[serde(default)]
    pub source: Vec<SourceConfig>,
}

impl SourceList {
    pub fn from_toml(text: &str) -> Result<Self, KbError> {
        let parsed: SourceList = toml::from_str(text)?;
        for s in &parsed.source {
            if s.id.is_empty() {
                return Err(KbError::Config("source id must not be empty".to_string()));
            }
            if s.chunk_overlap >= s.chunk_size {
                return Err(KbError::Config(format!(
                    "source {}: chunk_overlap must be < chunk_size",
                    s.id
                )));
            }
        }
        Ok(parsed)
    }

    pub fn from_path(path: &std::path::Path) -> Result<Self, KbError> {
        let text = std::fs::read_to_string(path)?;
        Self::from_toml(&text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_minimal_source_list() {
        let text = r#"
[[source]]
id = "hull-2018"
kind = "book"
title = "Options, Futures and Other Derivatives"
path = "books/hull.txt"
"#;
        let list = SourceList::from_toml(text).unwrap();
        assert_eq!(list.source.len(), 1);
        assert_eq!(list.source[0].id, "hull-2018");
        assert_eq!(list.source[0].chunk_size, 600);
        assert_eq!(list.source[0].chunk_overlap, 80);
    }

    #[test]
    fn rejects_empty_id() {
        let text = r#"
[[source]]
id = ""
kind = "paper"
title = "X"
path = "p.txt"
"#;
        assert!(SourceList::from_toml(text).is_err());
    }

    #[test]
    fn rejects_overlap_ge_size() {
        let text = r#"
[[source]]
id = "x"
kind = "paper"
title = "X"
path = "p.txt"
chunk_size = 100
chunk_overlap = 100
"#;
        assert!(SourceList::from_toml(text).is_err());
    }
}
