//! Entity / relation extractor abstraction.
//!
//! Production extractor calls a reasoning model with a structured-output
//! prompt; v1 ships [`KeywordExtractor`], a deterministic rule-based stub that
//! lets the rest of the ingestion pipeline + retrieval tests run without an
//! API key. The orchestrator can swap implementations through the
//! [`Extractor`] trait.

use std::collections::HashMap;

use crate::error::KbError;
use crate::schema::{EdgeKind, NodeKind, NodeRecord, RelationRecord};

/// One chunk's worth of extracted entities and relations, all carrying back
/// their source chunk for provenance.
#[derive(Debug, Default, Clone)]
pub struct ExtractedFacts {
    pub nodes: Vec<NodeRecord>,
    pub relations: Vec<RelationRecord>,
}

pub trait Extractor: Send + Sync {
    fn extract(
        &self,
        source_id: &str,
        chunk_id: &str,
        text: &str,
    ) -> Result<ExtractedFacts, KbError>;
}

/// Rule: a phrase mapping to a node kind + canonical id.
#[derive(Debug, Clone)]
pub struct KeywordRule {
    pub keyword: String,
    pub node_id: String,
    pub node_kind: NodeKind,
    pub summary: String,
}

/// Co-occurrence relation rule: when two keywords appear in the same chunk,
/// emit an edge between their canonical nodes.
#[derive(Debug, Clone)]
pub struct CoocurrenceRule {
    pub src_keyword: String,
    pub dst_keyword: String,
    pub kind: EdgeKind,
    pub weight: f32,
}

#[derive(Debug, Clone, Default)]
pub struct KeywordExtractor {
    pub rules: Vec<KeywordRule>,
    pub cooccurrences: Vec<CoocurrenceRule>,
}

impl KeywordExtractor {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn rule(mut self, rule: KeywordRule) -> Self {
        self.rules.push(rule);
        self
    }

    pub fn cooccurrence(mut self, rule: CoocurrenceRule) -> Self {
        self.cooccurrences.push(rule);
        self
    }
}

impl Extractor for KeywordExtractor {
    fn extract(
        &self,
        source_id: &str,
        chunk_id: &str,
        text: &str,
    ) -> Result<ExtractedFacts, KbError> {
        let lower = text.to_ascii_lowercase();
        let mut by_keyword: HashMap<&str, NodeRecord> = HashMap::new();
        for rule in &self.rules {
            if lower.contains(&rule.keyword.to_ascii_lowercase()) {
                by_keyword
                    .entry(rule.keyword.as_str())
                    .or_insert_with(|| NodeRecord {
                        id: rule.node_id.clone(),
                        kind: rule.node_kind,
                        name: rule.node_id.clone(),
                        summary: rule.summary.clone(),
                        source_id: Some(source_id.to_string()),
                        data: serde_json::json!({}),
                    });
            }
        }
        let mut relations = Vec::new();
        for cooc in &self.cooccurrences {
            let src = by_keyword.get(cooc.src_keyword.as_str());
            let dst = by_keyword.get(cooc.dst_keyword.as_str());
            if let (Some(s), Some(d)) = (src, dst) {
                relations.push(RelationRecord {
                    src_id: s.id.clone(),
                    dst_id: d.id.clone(),
                    kind: cooc.kind,
                    weight: cooc.weight,
                    evidence_chunk_id: Some(chunk_id.to_string()),
                });
            }
        }
        Ok(ExtractedFacts {
            nodes: by_keyword.into_values().collect(),
            relations,
        })
    }
}
