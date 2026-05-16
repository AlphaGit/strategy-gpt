//! Objective spec types. Parsed from YAML or JSON via serde.

use serde::de::Error as _;
use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ComparisonOp {
    /// `>=`
    Ge,
    /// `<=`
    Le,
    /// `>`
    Gt,
    /// `<`
    Lt,
    /// `==` (numeric equality with epsilon)
    Eq,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct Comparison {
    pub op: ComparisonOp,
    pub value: f64,
}

impl Comparison {
    pub fn satisfied_by(&self, observed: f64) -> bool {
        match self.op {
            ComparisonOp::Ge => observed >= self.value,
            ComparisonOp::Le => observed <= self.value,
            ComparisonOp::Gt => observed > self.value,
            ComparisonOp::Lt => observed < self.value,
            ComparisonOp::Eq => (observed - self.value).abs() < 1e-9,
        }
    }
}

impl Comparison {
    /// Parse a string like `">= 1.5"` into a [`Comparison`]. Whitespace is
    /// optional. Recognized ops: `>=`, `<=`, `>`, `<`, `==`.
    pub fn parse(s: &str) -> Result<Self, SpecParseError> {
        let trimmed = s.trim();
        for (token, op) in [
            (">=", ComparisonOp::Ge),
            ("<=", ComparisonOp::Le),
            ("==", ComparisonOp::Eq),
            (">", ComparisonOp::Gt),
            ("<", ComparisonOp::Lt),
        ] {
            if let Some(rest) = trimmed.strip_prefix(token) {
                let value: f64 = rest
                    .trim()
                    .parse()
                    .map_err(|e| SpecParseError(format!("invalid number in `{s}`: {e}")))?;
                return Ok(Comparison { op, value });
            }
        }
        Err(SpecParseError(format!(
            "comparison `{s}` must start with one of >=, <=, >, <, =="
        )))
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SecondaryMode {
    /// Hard fail: violation rejects the candidate regardless of primary metric.
    Constraint,
    /// Soft: contributes to the aggregated score per the tradeoff mode.
    Soft,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Tradeoff {
    /// Optimize primary metric; secondary breaks ties.
    Lexicographic,
    /// Scalarize across all soft metrics.
    WeightedSum,
    /// Return non-dominated frontier rather than a single best.
    Pareto,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PrimaryMetric {
    pub metric: String,
    #[serde(default, deserialize_with = "deserialize_comparison_opt")]
    pub target: Option<Comparison>,
    #[serde(default = "default_weight")]
    pub weight: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SecondaryMetric {
    pub metric: String,
    #[serde(deserialize_with = "deserialize_comparison")]
    pub target: Comparison,
    #[serde(default = "default_weight")]
    pub weight: f64,
    pub mode: SecondaryMode,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FoldScheme {
    #[default]
    Rolling,
    Anchored,
}

/// Fold configuration shared by the objective evaluator and the optimizer.
///
/// Structural fields (`count`, `scheme`, `gap`, `warmup_bars`) match the
/// experiment-spec `folds` block; `oos_min_score` is objective-specific.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct Folds {
    pub count: u32,
    #[serde(default)]
    pub scheme: FoldScheme,
    #[serde(default)]
    pub gap: Option<u32>,
    #[serde(default)]
    pub warmup_bars: Option<u32>,
    #[serde(default)]
    pub oos_min_score: Option<f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ObjectiveSpec {
    pub primary: PrimaryMetric,
    #[serde(default)]
    pub secondary: Vec<SecondaryMetric>,
    pub tradeoff: Tradeoff,
    pub folds: Folds,
}

// Custom Deserialize so we can reject the legacy `walk_forward` key with a
// structured migration error instead of a generic missing-field error.
impl<'de> Deserialize<'de> for ObjectiveSpec {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        #[derive(Deserialize)]
        struct Raw {
            primary: PrimaryMetric,
            #[serde(default)]
            secondary: Vec<SecondaryMetric>,
            tradeoff: Tradeoff,
            #[serde(default)]
            folds: Option<Folds>,
            #[serde(default)]
            walk_forward: Option<serde_yaml::Value>,
        }

        let raw = Raw::deserialize(deserializer)?;
        if raw.walk_forward.is_some() {
            return Err(D::Error::custom(
                "objective spec: legacy `walk_forward` key is no longer accepted; \
                 rename the top-level `walk_forward:` block to `folds:` (fields \
                 `count`, `scheme`, `gap`, `warmup_bars`, `oos_min_score` carry \
                 over unchanged; `count` replaces the prior `folds:` numeric field)",
            ));
        }
        let folds = raw.folds.ok_or_else(|| D::Error::missing_field("folds"))?;
        Ok(ObjectiveSpec {
            primary: raw.primary,
            secondary: raw.secondary,
            tradeoff: raw.tradeoff,
            folds,
        })
    }
}

impl ObjectiveSpec {
    pub fn from_yaml(src: &str) -> Result<Self, SpecParseError> {
        serde_yaml::from_str(src).map_err(|e| SpecParseError(format!("yaml: {e}")))
    }

    pub fn from_json(src: &str) -> Result<Self, SpecParseError> {
        serde_json::from_str(src).map_err(|e| SpecParseError(format!("json: {e}")))
    }
}

#[derive(Debug, Error)]
#[error("{0}")]
pub struct SpecParseError(pub String);

fn default_weight() -> f64 {
    1.0
}

fn deserialize_comparison<'de, D>(d: D) -> Result<Comparison, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let s = String::deserialize(d)?;
    Comparison::parse(&s).map_err(serde::de::Error::custom)
}

fn deserialize_comparison_opt<'de, D>(d: D) -> Result<Option<Comparison>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let opt = Option::<String>::deserialize(d)?;
    match opt {
        Some(s) => Comparison::parse(&s)
            .map(Some)
            .map_err(serde::de::Error::custom),
        None => Ok(None),
    }
}
