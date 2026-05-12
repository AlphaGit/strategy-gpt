//! Graph schema for the knowledge base.
//!
//! Matches the spec `knowledge-base::graph-schema-for-financial-knowledge`:
//! node kinds Concept/Indicator/Regime/Model/Technique/Source, relation kinds
//! IMPLEMENTS/CONTRADICTS/REQUIRES/GENERALIZES/CITES/EMPIRICAL_SUPPORT/
//! FAILS_IN_REGIME.

use serde::{Deserialize, Serialize};

use crate::error::KbError;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub enum NodeKind {
    Concept,
    Indicator,
    Regime,
    Model,
    Technique,
    Source,
}

impl NodeKind {
    pub const ALL: &'static [NodeKind] = &[
        NodeKind::Concept,
        NodeKind::Indicator,
        NodeKind::Regime,
        NodeKind::Model,
        NodeKind::Technique,
        NodeKind::Source,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            NodeKind::Concept => "Concept",
            NodeKind::Indicator => "Indicator",
            NodeKind::Regime => "Regime",
            NodeKind::Model => "Model",
            NodeKind::Technique => "Technique",
            NodeKind::Source => "Source",
        }
    }

    pub fn parse(s: &str) -> Result<Self, KbError> {
        match s {
            "Concept" => Ok(NodeKind::Concept),
            "Indicator" => Ok(NodeKind::Indicator),
            "Regime" => Ok(NodeKind::Regime),
            "Model" => Ok(NodeKind::Model),
            "Technique" => Ok(NodeKind::Technique),
            "Source" => Ok(NodeKind::Source),
            other => Err(KbError::UnknownNodeKind(other.to_string())),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum EdgeKind {
    Implements,
    Contradicts,
    Requires,
    Generalizes,
    Cites,
    EmpiricalSupport,
    FailsInRegime,
}

impl EdgeKind {
    pub const ALL: &'static [EdgeKind] = &[
        EdgeKind::Implements,
        EdgeKind::Contradicts,
        EdgeKind::Requires,
        EdgeKind::Generalizes,
        EdgeKind::Cites,
        EdgeKind::EmpiricalSupport,
        EdgeKind::FailsInRegime,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            EdgeKind::Implements => "IMPLEMENTS",
            EdgeKind::Contradicts => "CONTRADICTS",
            EdgeKind::Requires => "REQUIRES",
            EdgeKind::Generalizes => "GENERALIZES",
            EdgeKind::Cites => "CITES",
            EdgeKind::EmpiricalSupport => "EMPIRICAL_SUPPORT",
            EdgeKind::FailsInRegime => "FAILS_IN_REGIME",
        }
    }

    pub fn parse(s: &str) -> Result<Self, KbError> {
        match s {
            "IMPLEMENTS" => Ok(EdgeKind::Implements),
            "CONTRADICTS" => Ok(EdgeKind::Contradicts),
            "REQUIRES" => Ok(EdgeKind::Requires),
            "GENERALIZES" => Ok(EdgeKind::Generalizes),
            "CITES" => Ok(EdgeKind::Cites),
            "EMPIRICAL_SUPPORT" => Ok(EdgeKind::EmpiricalSupport),
            "FAILS_IN_REGIME" => Ok(EdgeKind::FailsInRegime),
            other => Err(KbError::UnknownEdgeKind(other.to_string())),
        }
    }
}

/// A node in the graph. `source_id` is required for every non-Source node
/// (provenance invariant per spec scenario `node-has-provenance`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeRecord {
    pub id: String,
    pub kind: NodeKind,
    pub name: String,
    pub summary: String,
    /// Required for every non-Source node; None for Source nodes themselves.
    pub source_id: Option<String>,
    /// Free-form extra fields, serialized as JSON in storage.
    #[serde(default)]
    pub data: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelationRecord {
    pub src_id: String,
    pub dst_id: String,
    pub kind: EdgeKind,
    pub weight: f32,
    /// Optional evidence chunk id this relation was extracted from.
    pub evidence_chunk_id: Option<String>,
}
