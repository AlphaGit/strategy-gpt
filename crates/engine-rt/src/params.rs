//! Declared parameter schema for a strategy crate.
//!
//! Every strategy ships a `params_schema.json` at the crate root (see
//! `PROMPT_API.md` §4). The file is the single source of truth for which
//! parameters the strategy reads from `Context::state_get("__params__")`. The
//! build pipeline reads it during layout; the tester validates `param_intent`
//! against it before invoking a mini-optimize pass.
//!
//! The types in this module are the deserialized shape of that JSON file.
//! They are not part of the strategy runtime — strategies read their params
//! as plain serde structs — but they ARE part of the public surface so the
//! build pipeline and the tester can share one definition.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fmt;

/// Primitive kinds permitted in `params_schema.json`.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ParamKind {
    F64,
    I64,
    Bool,
    String,
}

impl fmt::Display for ParamKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParamKind::F64 => write!(f, "f64"),
            ParamKind::I64 => write!(f, "i64"),
            ParamKind::Bool => write!(f, "bool"),
            ParamKind::String => write!(f, "string"),
        }
    }
}

/// One declared parameter row. `min` and `max` are required for numeric
/// kinds and absent for `bool` / `string`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ParamSpec {
    pub name: String,
    pub kind: ParamKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max: Option<f64>,
    pub default: Value,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
}

/// The top-level shape of `params_schema.json`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ParamSchema {
    pub schema_version: u32,
    pub params: Vec<ParamSpec>,
}

impl ParamSchema {
    /// Current accepted schema version. Bump only when the on-disk format
    /// changes incompatibly; the build pipeline rejects unrecognized
    /// versions.
    pub const SCHEMA_VERSION: u32 = 1;

    /// Parse the JSON contents of a `params_schema.json` file.
    pub fn parse_json(src: &str) -> Result<Self, ParamSchemaError> {
        let parsed: Self =
            serde_json::from_str(src).map_err(|e| ParamSchemaError::Json(e.to_string()))?;
        parsed.validate()?;
        Ok(parsed)
    }

    /// Structural validation: schema version, per-row kind/bounds/default
    /// consistency. Pure check — no I/O.
    pub fn validate(&self) -> Result<(), ParamSchemaError> {
        if self.schema_version != Self::SCHEMA_VERSION {
            return Err(ParamSchemaError::UnsupportedVersion(self.schema_version));
        }
        let mut seen = std::collections::HashSet::new();
        for p in &self.params {
            if !seen.insert(p.name.clone()) {
                return Err(ParamSchemaError::DuplicateName(p.name.clone()));
            }
            validate_row(p)?;
        }
        Ok(())
    }

    /// Look up a declared param by name. `None` when absent.
    pub fn get(&self, name: &str) -> Option<&ParamSpec> {
        self.params.iter().find(|p| p.name == name)
    }

    /// All declared parameter names in declaration order. Useful for
    /// `param_intent` schema-validation error messages.
    pub fn names(&self) -> impl Iterator<Item = &str> {
        self.params.iter().map(|p| p.name.as_str())
    }

    /// Empty schema convenience — strategies with no parameters still ship
    /// a `params_schema.json` with `"params": []`. Tests that need a
    /// blank schema use this rather than reading the file.
    pub fn empty() -> Self {
        Self {
            schema_version: Self::SCHEMA_VERSION,
            params: Vec::new(),
        }
    }
}

fn validate_row(p: &ParamSpec) -> Result<(), ParamSchemaError> {
    match p.kind {
        ParamKind::F64 | ParamKind::I64 => {
            let min = p
                .min
                .ok_or_else(|| ParamSchemaError::MissingBound(p.name.clone(), "min"))?;
            let max = p
                .max
                .ok_or_else(|| ParamSchemaError::MissingBound(p.name.clone(), "max"))?;
            if !(min.is_finite() && max.is_finite()) {
                return Err(ParamSchemaError::NonFiniteBound(p.name.clone()));
            }
            if max < min {
                return Err(ParamSchemaError::InvertedBounds(p.name.clone()));
            }
            if !default_in_kind(&p.default, p.kind) {
                return Err(ParamSchemaError::DefaultKindMismatch(
                    p.name.clone(),
                    p.kind,
                ));
            }
            if !default_in_range(&p.default, min, max) {
                return Err(ParamSchemaError::DefaultOutOfRange(p.name.clone()));
            }
        }
        ParamKind::Bool | ParamKind::String => {
            if p.min.is_some() || p.max.is_some() {
                return Err(ParamSchemaError::UnexpectedBound(p.name.clone(), p.kind));
            }
            if !default_in_kind(&p.default, p.kind) {
                return Err(ParamSchemaError::DefaultKindMismatch(
                    p.name.clone(),
                    p.kind,
                ));
            }
        }
    }
    Ok(())
}

fn default_in_kind(v: &Value, kind: ParamKind) -> bool {
    match kind {
        ParamKind::F64 => v.as_f64().is_some(),
        ParamKind::I64 => v.as_i64().is_some(),
        ParamKind::Bool => v.is_boolean(),
        ParamKind::String => v.is_string(),
    }
}

fn default_in_range(v: &Value, min: f64, max: f64) -> bool {
    let Some(x) = v.as_f64() else { return false };
    x >= min && x <= max
}

/// Errors surfaced by [`ParamSchema::parse_json`] and
/// [`ParamSchema::validate`].
#[derive(Debug, Clone, PartialEq)]
pub enum ParamSchemaError {
    Json(String),
    UnsupportedVersion(u32),
    DuplicateName(String),
    MissingBound(String, &'static str),
    NonFiniteBound(String),
    InvertedBounds(String),
    UnexpectedBound(String, ParamKind),
    DefaultKindMismatch(String, ParamKind),
    DefaultOutOfRange(String),
}

impl fmt::Display for ParamSchemaError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Json(e) => write!(f, "params_schema.json is not valid JSON: {e}"),
            Self::UnsupportedVersion(v) => write!(f, "unsupported schema_version {v}"),
            Self::DuplicateName(n) => write!(f, "duplicate parameter name `{n}`"),
            Self::MissingBound(n, which) => {
                write!(f, "param `{n}` missing required `{which}` bound")
            }
            Self::NonFiniteBound(n) => write!(f, "param `{n}` has a non-finite bound"),
            Self::InvertedBounds(n) => write!(f, "param `{n}` has max < min"),
            Self::UnexpectedBound(n, k) => {
                write!(f, "param `{n}` of kind `{k}` must not declare bounds")
            }
            Self::DefaultKindMismatch(n, k) => {
                write!(f, "param `{n}` default value is not a `{k}`")
            }
            Self::DefaultOutOfRange(n) => write!(f, "param `{n}` default value is out of range"),
        }
    }
}

impl std::error::Error for ParamSchemaError {}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_canonical_vxx_schema() {
        let src = r#"{
            "schema_version": 1,
            "params": [
                {"name": "vol_lo", "kind": "f64", "min": 0.001, "max": 0.05, "default": 0.01},
                {"name": "vol_hi", "kind": "f64", "min": 0.01,  "max": 0.20, "default": 0.04},
                {"name": "size",   "kind": "f64", "min": 1.0,   "max": 10000.0, "default": 100.0},
                {"name": "symbol", "kind": "string", "default": "VXX"}
            ]
        }"#;
        let schema = ParamSchema::parse_json(src).unwrap();
        assert_eq!(schema.params.len(), 4);
        assert_eq!(schema.get("vol_lo").unwrap().kind, ParamKind::F64);
        assert_eq!(schema.get("symbol").unwrap().kind, ParamKind::String);
    }

    #[test]
    fn empty_params_list_is_valid() {
        let src = r#"{"schema_version": 1, "params": []}"#;
        let schema = ParamSchema::parse_json(src).unwrap();
        assert!(schema.params.is_empty());
    }

    #[test]
    fn duplicate_name_rejected() {
        let src = r#"{
            "schema_version": 1,
            "params": [
                {"name": "x", "kind": "f64", "min": 0.0, "max": 1.0, "default": 0.5},
                {"name": "x", "kind": "f64", "min": 0.0, "max": 1.0, "default": 0.5}
            ]
        }"#;
        let err = ParamSchema::parse_json(src).unwrap_err();
        assert!(matches!(err, ParamSchemaError::DuplicateName(_)));
    }

    #[test]
    fn missing_bound_on_numeric_rejected() {
        let src = r#"{
            "schema_version": 1,
            "params": [{"name": "x", "kind": "f64", "min": 0.0, "default": 0.5}]
        }"#;
        let err = ParamSchema::parse_json(src).unwrap_err();
        assert!(matches!(err, ParamSchemaError::MissingBound(_, "max")));
    }

    #[test]
    fn inverted_bounds_rejected() {
        let src = r#"{
            "schema_version": 1,
            "params": [{"name": "x", "kind": "f64", "min": 1.0, "max": 0.0, "default": 0.5}]
        }"#;
        let err = ParamSchema::parse_json(src).unwrap_err();
        assert!(matches!(err, ParamSchemaError::InvertedBounds(_)));
    }

    #[test]
    fn default_out_of_range_rejected() {
        let src = r#"{
            "schema_version": 1,
            "params": [{"name": "x", "kind": "f64", "min": 0.0, "max": 1.0, "default": 2.0}]
        }"#;
        let err = ParamSchema::parse_json(src).unwrap_err();
        assert!(matches!(err, ParamSchemaError::DefaultOutOfRange(_)));
    }

    #[test]
    fn bool_must_not_declare_bounds() {
        let src = r#"{
            "schema_version": 1,
            "params": [{"name": "x", "kind": "bool", "min": 0.0, "max": 1.0, "default": true}]
        }"#;
        let err = ParamSchema::parse_json(src).unwrap_err();
        assert!(matches!(err, ParamSchemaError::UnexpectedBound(_, _)));
    }

    #[test]
    fn unsupported_version_rejected() {
        let src = r#"{"schema_version": 99, "params": []}"#;
        let err = ParamSchema::parse_json(src).unwrap_err();
        assert!(matches!(err, ParamSchemaError::UnsupportedVersion(99)));
    }

    #[test]
    fn empty_helper_validates() {
        let s = ParamSchema::empty();
        s.validate().unwrap();
        assert!(s.params.is_empty());
        assert_eq!(s.schema_version, ParamSchema::SCHEMA_VERSION);
    }

    #[test]
    fn round_trip_json() {
        let s = ParamSchema {
            schema_version: 1,
            params: vec![ParamSpec {
                name: "vol_lo".into(),
                kind: ParamKind::F64,
                min: Some(0.001),
                max: Some(0.05),
                default: json!(0.01),
                description: Some("entry threshold".into()),
            }],
        };
        let text = serde_json::to_string(&s).unwrap();
        let back = ParamSchema::parse_json(&text).unwrap();
        assert_eq!(back, s);
    }
}
