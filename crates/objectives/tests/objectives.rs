//! Integration tests for objective spec parsing, validation, evaluation.

use engine::BacktestMetrics;
use objectives::{
    evaluate, validate, Comparison, ComparisonOp, EvaluationOutcome, FoldScheme, Folds,
    ObjectiveSpec, PrimaryMetric, SecondaryMetric, SecondaryMode, Tradeoff,
};

fn metrics(sharpe: f64, max_dd: f64, pf: f64, win: f64) -> BacktestMetrics {
    BacktestMetrics {
        sharpe,
        sortino: sharpe,
        profit_factor: pf,
        win_ratio: win,
        max_drawdown: max_dd,
        annualized_return: 0.10,
        n_trades: 10,
        avg_trade_length_bars: 5.0,
    }
}

fn baseline_spec() -> ObjectiveSpec {
    ObjectiveSpec {
        primary: PrimaryMetric {
            metric: "sharpe".into(),
            target: Some(Comparison {
                op: ComparisonOp::Ge,
                value: 1.0,
            }),
            weight: 1.0,
        },
        secondary: vec![
            SecondaryMetric {
                metric: "max_drawdown".into(),
                target: Comparison {
                    op: ComparisonOp::Le,
                    value: 0.20,
                },
                weight: 1.0,
                mode: SecondaryMode::Constraint,
            },
            SecondaryMetric {
                metric: "profit_factor".into(),
                target: Comparison {
                    op: ComparisonOp::Ge,
                    value: 1.3,
                },
                weight: 0.5,
                mode: SecondaryMode::Soft,
            },
        ],
        tradeoff: Tradeoff::Lexicographic,
        folds: Folds {
            count: 5,
            scheme: FoldScheme::Rolling,
            gap: Some(1),
            warmup_bars: None,
            oos_min_score: Some(0.5),
        },
    }
}

#[test]
fn parses_yaml_with_string_comparisons() {
    let yaml = r#"
primary:
  metric: sharpe
  target: ">= 1.5"
  weight: 1.0
secondary:
  - metric: max_drawdown
    target: "<= 0.20"
    weight: 0.5
    mode: constraint
  - metric: profit_factor
    target: ">= 1.3"
    weight: 0.3
    mode: soft
tradeoff: lexicographic
folds:
  count: 5
  scheme: rolling
  gap: 1
  oos_min_score: 0.5
"#;
    let spec = ObjectiveSpec::from_yaml(yaml).unwrap();
    assert_eq!(spec.primary.metric, "sharpe");
    assert_eq!(spec.primary.target.unwrap().op, ComparisonOp::Ge);
    assert_eq!(spec.secondary.len(), 2);
    assert!(matches!(spec.tradeoff, Tradeoff::Lexicographic));
    assert_eq!(spec.folds.count, 5);
    assert!(matches!(spec.folds.scheme, FoldScheme::Rolling));
}

#[test]
fn rejects_legacy_walk_forward_key_with_migration_error() {
    let yaml = r#"
primary:
  metric: sharpe
  target: ">= 1.0"
tradeoff: lexicographic
walk_forward:
  folds: 5
"#;
    let err = ObjectiveSpec::from_yaml(yaml).unwrap_err();
    let msg = format!("{err}");
    assert!(
        msg.contains("walk_forward") && msg.contains("folds"),
        "migration error must name old + new keys; got: {msg}"
    );
}

#[test]
fn rejects_unknown_metric_name() {
    let mut spec = baseline_spec();
    spec.primary.metric = "alpha_omega".into();
    let err = validate(&spec).unwrap_err();
    assert!(format!("{err}").contains("unknown metric"));
}

#[test]
fn rejects_duplicate_metric_across_primary_and_secondary() {
    let mut spec = baseline_spec();
    spec.secondary[0].metric = "sharpe".into();
    let err = validate(&spec).unwrap_err();
    assert!(format!("{err}").contains("declared more than once"));
}

#[test]
fn rejects_negative_weight() {
    let mut spec = baseline_spec();
    spec.primary.weight = -1.0;
    let err = validate(&spec).unwrap_err();
    assert!(format!("{err}").contains("negative"));
}

#[test]
fn pareto_requires_two_or_more_metrics() {
    let mut spec = ObjectiveSpec {
        primary: PrimaryMetric {
            metric: "sharpe".into(),
            target: None,
            weight: 1.0,
        },
        secondary: vec![],
        tradeoff: Tradeoff::Pareto,
        folds: Folds {
            count: 1,
            scheme: FoldScheme::Rolling,
            gap: None,
            warmup_bars: None,
            oos_min_score: None,
        },
    };
    let err = validate(&spec).unwrap_err();
    assert!(format!("{err}").contains("at least two"));

    // Pass with a secondary added.
    spec.secondary.push(SecondaryMetric {
        metric: "max_drawdown".into(),
        target: Comparison {
            op: ComparisonOp::Le,
            value: 0.2,
        },
        weight: 1.0,
        mode: SecondaryMode::Soft,
    });
    validate(&spec).unwrap();
}

#[test]
fn rejects_zero_folds() {
    let mut spec = baseline_spec();
    spec.folds.count = 0;
    let err = validate(&spec).unwrap_err();
    assert!(format!("{err}").contains("folds"));
}

#[test]
fn baseline_spec_is_valid() {
    validate(&baseline_spec()).unwrap();
}

#[test]
fn evaluator_accepts_when_no_constraint_is_violated() {
    let spec = baseline_spec();
    let o: EvaluationOutcome = evaluate(&metrics(1.8, 0.10, 1.5, 0.6), &spec);
    assert!(o.accepted);
    assert_eq!(o.violations.len(), 0);
    // Lexicographic: score == primary metric value.
    assert!((o.score - 1.8).abs() < 1e-9);
}

#[test]
fn evaluator_rejects_on_constraint_violation_regardless_of_primary() {
    let spec = baseline_spec();
    // sharpe=5 (great), but max_drawdown 0.30 > 0.20 constraint → reject.
    let o = evaluate(&metrics(5.0, 0.30, 2.0, 0.8), &spec);
    assert!(!o.accepted);
    assert!(o.violations.contains(&"max_drawdown".to_string()));
    assert_eq!(o.score, f64::NEG_INFINITY);
}

#[test]
fn soft_miss_is_not_a_rejection_but_recorded() {
    let spec = baseline_spec();
    // profit_factor 1.0 < 1.3 soft target. Primary sharpe 1.5 satisfies its target.
    let o = evaluate(&metrics(1.5, 0.10, 1.0, 0.6), &spec);
    assert!(o.accepted);
    assert!(o.soft_misses.contains(&"profit_factor".to_string()));
}

#[test]
fn weighted_sum_combines_primary_and_soft_secondaries() {
    let mut spec = baseline_spec();
    spec.tradeoff = Tradeoff::WeightedSum;
    // Both metrics within constraint; sharpe 2 * 1.0 + profit_factor 1.5 * 0.5
    // − max_drawdown 0.10 * 1.0 (constraint metric, not soft, so it does NOT
    // contribute to weighted-sum aggregation).
    let o = evaluate(&metrics(2.0, 0.10, 1.5, 0.6), &spec);
    assert!(o.accepted);
    let expected = 2.0 * 1.0 + 1.5 * 0.5;
    assert!((o.score - expected).abs() < 1e-9);
}

#[test]
fn weighted_sum_negates_lower_is_better_targets() {
    // Add a soft secondary with a `<=` target to verify sign handling.
    let mut spec = ObjectiveSpec {
        primary: PrimaryMetric {
            metric: "sharpe".into(),
            target: Some(Comparison {
                op: ComparisonOp::Ge,
                value: 1.0,
            }),
            weight: 1.0,
        },
        secondary: vec![SecondaryMetric {
            metric: "max_drawdown".into(),
            target: Comparison {
                op: ComparisonOp::Le,
                value: 0.20,
            },
            weight: 2.0,
            mode: SecondaryMode::Soft,
        }],
        tradeoff: Tradeoff::WeightedSum,
        folds: Folds {
            count: 1,
            scheme: FoldScheme::Rolling,
            gap: None,
            warmup_bars: None,
            oos_min_score: None,
        },
    };
    validate(&spec).unwrap();
    let m = metrics(2.0, 0.10, 1.0, 0.5);
    let o = evaluate(&m, &spec);
    // sharpe * 1.0 + (-1) * max_drawdown * 2.0 = 2.0 - 0.20 = 1.80
    assert!((o.score - 1.80).abs() < 1e-9);
    let _ = &mut spec; // silence move-warning on `mut`
}

#[test]
fn comparison_parse_round_trip_handles_whitespace_and_ops() {
    assert_eq!(Comparison::parse(">= 1.5").unwrap().op, ComparisonOp::Ge);
    assert_eq!(Comparison::parse("<=0.20").unwrap().op, ComparisonOp::Le);
    assert_eq!(Comparison::parse(">  0.0").unwrap().op, ComparisonOp::Gt);
    assert_eq!(Comparison::parse("< 100").unwrap().op, ComparisonOp::Lt);
    assert_eq!(Comparison::parse("== 1.0").unwrap().op, ComparisonOp::Eq);
    assert!(Comparison::parse("unknown 1.0").is_err());
    assert!(Comparison::parse(">= notanumber").is_err());
}
