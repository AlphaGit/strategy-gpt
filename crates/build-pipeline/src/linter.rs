//! Source and manifest linter for LLM-emitted strategies.
//!
//! Two phases:
//! - [`lint_source`] parses the strategy's Rust source via `syn` and rejects
//!   `unsafe` blocks, `unsafe fn`, and any `extern crate` other than the
//!   strategy's own dependency tree.
//! - [`lint_manifest`] inspects the strategy's `StrategyManifest` (a parsed
//!   subset of Cargo.toml) and rejects any dependency that is not in the
//!   whitelist.
//!
//! These checks are the operative guardrail: they run BEFORE cargo touches
//! the source. There is no sandbox; rejecting bad input here is the
//! enforcement.

use std::collections::HashSet;

use syn::visit::Visit;
use syn::{Item, ItemFn, ItemForeignMod};

use crate::driver::StrategyManifest;
use crate::whitelist::Whitelist;

#[derive(Debug, Default, PartialEq, Eq)]
pub struct LintReport {
    pub source_violations: Vec<String>,
    pub manifest_violations: Vec<String>,
}

impl LintReport {
    pub fn is_clean(&self) -> bool {
        self.source_violations.is_empty() && self.manifest_violations.is_empty()
    }

    pub fn merged_message(&self) -> String {
        let mut out = Vec::new();
        for v in &self.source_violations {
            out.push(format!("source: {v}"));
        }
        for v in &self.manifest_violations {
            out.push(format!("manifest: {v}"));
        }
        out.join("; ")
    }
}

pub fn lint_source(src: &str) -> Vec<String> {
    let file = match syn::parse_file(src) {
        Ok(f) => f,
        Err(e) => return vec![format!("could not parse Rust source: {e}")],
    };
    let mut visitor = SourceLinter::default();
    visitor.visit_file(&file);
    visitor.violations
}

#[derive(Default)]
struct SourceLinter {
    violations: Vec<String>,
}

impl<'ast> Visit<'ast> for SourceLinter {
    fn visit_expr_unsafe(&mut self, _i: &'ast syn::ExprUnsafe) {
        self.violations
            .push("`unsafe` block is not permitted in strategy source".into());
    }

    fn visit_item_fn(&mut self, f: &'ast ItemFn) {
        if f.sig.unsafety.is_some() {
            self.violations.push(format!(
                "`unsafe fn {}` is not permitted in strategy source",
                f.sig.ident
            ));
        }
        syn::visit::visit_item_fn(self, f);
    }

    fn visit_item_foreign_mod(&mut self, m: &'ast ItemForeignMod) {
        self.violations
            .push("`extern \"C\"` blocks are not permitted in strategy source".into());
        syn::visit::visit_item_foreign_mod(self, m);
    }

    fn visit_item(&mut self, i: &'ast Item) {
        if let Item::ExternCrate(ec) = i {
            self.violations.push(format!(
                "`extern crate {}` is not permitted; declare deps in Cargo.toml",
                ec.ident
            ));
        }
        syn::visit::visit_item(self, i);
    }
}

pub fn lint_manifest(manifest: &StrategyManifest, whitelist: &Whitelist) -> Vec<String> {
    let mut violations = Vec::new();
    let mut seen: HashSet<&str> = HashSet::new();
    for dep in manifest
        .dependencies
        .iter()
        .chain(manifest.dev_dependencies.iter())
        .chain(manifest.build_dependencies.iter())
    {
        if !seen.insert(dep.name.as_str()) {
            violations.push(format!("dependency `{}` declared more than once", dep.name));
            continue;
        }
        if !whitelist.is_allowed(&dep.name) {
            violations.push(format!(
                "dependency `{}` is not in the allowed-crate whitelist",
                dep.name
            ));
        }
    }
    violations
}

pub fn run_full_lint(src: &str, manifest: &StrategyManifest, whitelist: &Whitelist) -> LintReport {
    LintReport {
        source_violations: lint_source(src),
        manifest_violations: lint_manifest(manifest, whitelist),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::driver::{ManifestDep, StrategyManifest};

    fn wl() -> Whitelist {
        Whitelist::parse_toml(include_str!("../whitelist.toml")).unwrap()
    }

    fn dep(name: &str) -> ManifestDep {
        ManifestDep {
            name: name.into(),
            req: "*".into(),
        }
    }

    #[test]
    fn clean_source_passes() {
        let src = r#"
            use engine_rt::{Bar, Context, Strategy};
            pub fn helper(x: i32) -> i32 { x + 1 }
        "#;
        assert!(lint_source(src).is_empty());
    }

    #[test]
    fn unsafe_block_rejected() {
        let src = r#"
            pub fn dangerous() {
                unsafe { let _ = 1 + 1; }
            }
        "#;
        let v = lint_source(src);
        assert_eq!(v.len(), 1);
        assert!(v[0].contains("`unsafe` block"));
    }

    #[test]
    fn unsafe_fn_rejected() {
        let src = "pub unsafe fn dangerous() {}";
        let v = lint_source(src);
        assert_eq!(v.len(), 1);
        assert!(v[0].contains("unsafe fn dangerous"));
    }

    #[test]
    fn extern_block_rejected() {
        let src = r#"
            extern "C" { pub fn malloc(n: usize) -> *mut u8; }
        "#;
        let v = lint_source(src);
        assert!(v.iter().any(|m| m.contains("extern")));
    }

    #[test]
    fn extern_crate_rejected() {
        let src = "extern crate libc;";
        let v = lint_source(src);
        assert!(v.iter().any(|m| m.contains("extern crate")));
    }

    #[test]
    fn parse_failure_reported() {
        let src = "pub fn missing_paren -> { }";
        let v = lint_source(src);
        assert_eq!(v.len(), 1);
        assert!(v[0].contains("could not parse"));
    }

    #[test]
    fn manifest_with_only_whitelisted_deps_passes() {
        let m = StrategyManifest {
            name: "vxx_rangetrade".into(),
            version: "0.1.0".into(),
            dependencies: vec![dep("engine-rt"), dep("chrono"), dep("serde")],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        };
        assert!(lint_manifest(&m, &wl()).is_empty());
    }

    #[test]
    fn manifest_with_non_whitelisted_dep_rejected() {
        let m = StrategyManifest {
            name: "evil_strategy".into(),
            version: "0.1.0".into(),
            dependencies: vec![dep("engine-rt"), dep("tokio"), dep("reqwest")],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        };
        let v = lint_manifest(&m, &wl());
        assert_eq!(v.len(), 2);
        assert!(v.iter().any(|m| m.contains("tokio")));
        assert!(v.iter().any(|m| m.contains("reqwest")));
    }

    #[test]
    fn duplicate_dependency_rejected() {
        let m = StrategyManifest {
            name: "dup".into(),
            version: "0.1.0".into(),
            dependencies: vec![dep("engine-rt"), dep("engine-rt")],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        };
        let v = lint_manifest(&m, &wl());
        assert!(v.iter().any(|m| m.contains("declared more than once")));
    }

    #[test]
    fn full_lint_aggregates_both_phases() {
        let src = "pub unsafe fn bad() {}";
        let m = StrategyManifest {
            name: "bad".into(),
            version: "0.1.0".into(),
            dependencies: vec![dep("tokio")],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        };
        let report = run_full_lint(src, &m, &wl());
        assert!(!report.is_clean());
        assert_eq!(report.source_violations.len(), 1);
        assert_eq!(report.manifest_violations.len(), 1);
    }
}
