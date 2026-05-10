//! Allowed-crate whitelist loaded from a TOML manifest.
//!
//! Versions are intentionally NOT pinned (see proposal): any compatible
//! version of a whitelisted crate is acceptable. The whitelist itself is
//! versioned via `schema_version` so future format changes can be detected.

use serde::Deserialize;
use std::collections::HashSet;
use std::path::Path;

use crate::error::BuildError;

#[derive(Clone, Debug, Deserialize)]
struct WhitelistFile {
    schema_version: u32,
    #[serde(rename = "crate", default)]
    crates: Vec<WhitelistEntry>,
}

#[derive(Clone, Debug, Deserialize)]
struct WhitelistEntry {
    name: String,
    #[serde(default)]
    purpose: String,
}

#[derive(Clone, Debug)]
pub struct Whitelist {
    schema_version: u32,
    allowed: HashSet<String>,
    purposes: Vec<(String, String)>,
}

impl Whitelist {
    pub fn parse_toml(src: &str) -> Result<Self, BuildError> {
        let parsed: WhitelistFile =
            toml::from_str(src).map_err(|e| BuildError::Whitelist(format!("invalid TOML: {e}")))?;
        if parsed.schema_version != 1 {
            return Err(BuildError::Whitelist(format!(
                "unsupported whitelist schema_version {}",
                parsed.schema_version
            )));
        }
        let allowed: HashSet<String> = parsed.crates.iter().map(|c| c.name.clone()).collect();
        let purposes: Vec<(String, String)> = parsed
            .crates
            .iter()
            .map(|c| (c.name.clone(), c.purpose.clone()))
            .collect();
        Ok(Self {
            schema_version: parsed.schema_version,
            allowed,
            purposes,
        })
    }

    pub fn from_file(path: impl AsRef<Path>) -> Result<Self, BuildError> {
        let src = std::fs::read_to_string(path.as_ref())
            .map_err(|e| BuildError::Whitelist(format!("cannot read whitelist: {e}")))?;
        Self::parse_toml(&src)
    }

    pub fn is_allowed(&self, name: &str) -> bool {
        self.allowed.contains(name)
    }

    pub fn schema_version(&self) -> u32 {
        self.schema_version
    }

    pub fn entries(&self) -> impl Iterator<Item = (&str, &str)> {
        self.purposes.iter().map(|(n, p)| (n.as_str(), p.as_str()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_baseline_whitelist() {
        let src = include_str!("../whitelist.toml");
        let wl = Whitelist::parse_toml(src).unwrap();
        assert!(wl.is_allowed("engine-rt"));
        assert!(wl.is_allowed("polars"));
        assert!(!wl.is_allowed("tokio"));
        assert!(!wl.is_allowed("reqwest"));
    }

    #[test]
    fn rejects_unsupported_schema_version() {
        let src = "schema_version = 99\n";
        let err = Whitelist::parse_toml(src).unwrap_err();
        assert!(format!("{err}").contains("schema_version"));
    }

    #[test]
    fn parses_empty_crate_list() {
        let src = "schema_version = 1\n";
        let wl = Whitelist::parse_toml(src).unwrap();
        assert!(!wl.is_allowed("engine-rt"));
    }
}
