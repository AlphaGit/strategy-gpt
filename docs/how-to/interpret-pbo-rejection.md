# Interpret a PBO rejection

When an optimization run prints `decision: rejected_pbo`, the selection layer has determined that the in-sample winner of the top-K is systematically *worse* than chance out-of-sample (PBO above the threshold). It is a research signal, not a failure of the system. The candidate is recorded in `best.json` as `would_have_picked` so you can inspect what the ranking would have chosen without the gate.

This page is operator-facing. For the methodology behind PBO, DSR, and the robust score, see [Overfitting & selection](../explanation/overfitting-and-selection.md). For the knob surface, see [Objective spec](../reference/objective-spec.md).

## Triage checklist

1. **Inspect `would_have_picked` and per-trial PBO inputs** in `best.json`. Open the per-fold OOS metric matrix and look for one candidate that dominates most folds:

    ```bash
    strategy-gpt optimize inspect <opt_id>
    ```

    If the OOS metric matrix shows clear within-fold leaders that swap across the partitions, PBO is doing its job — the IS winner really is fold-specific.

2. **Re-rank by robust score** to favor stability over peak:

    ```bash
    strategy-gpt optimize reselect <opt_id> --robust-objective
    ```

    Robust ranking promotes plateaus. This is often what survives in practice.

3. **Override the threshold** when you have positive evidence the per-fold variance is dominated by sampling noise rather than overfit:

    ```bash
    strategy-gpt optimize reselect <opt_id> --pbo-threshold 0.6
    ```

    The override is recorded in `best_<timestamp>.json` alongside the original PBO. Both stay readable.

4. **Add folds or extend the train slice.** Fewer fold OOS samples = noisier rank statistic. Doubling folds halves the per-candidate metric variance under the usual assumptions.

5. **Use `--force` only as a last resort.** It publishes the candidate despite the gate; the override is permanent in the manifest.

    ```bash
    strategy-gpt optimize reselect <opt_id> --force
    ```

    Reach for this only when you have an out-of-band reason (a known data anomaly, a planned A/B). Document the rationale in the decision log.

## Compare two selections

After running `reselect` you can side-by-side two `best.json` outputs from the same `opt_id`:

```bash
strategy-gpt optimize compare <opt_id> best.json best_2026-05-17T14-22-04Z.json
```

The diff prints decision, PBO, DSR, robust score, would-have-picked, and rank delta of each top-K candidate. Use it to argue for or against the override before committing.

## Common patterns

| Pattern in the OOS matrix | Likely cause | Recommended action |
|---|---|---|
| One candidate dominates ~all folds; PBO > 0.8 | Genuine overfit (the rank stat caught it) | Reduce search budget or constrain the space |
| Two candidates split fold halves (each wins its own clusters) | Regime split — the strategy works in one regime, fails in the other | Add regime detection or split the experiment |
| Most candidates tie within noise; PBO ≈ 0.5 ± epsilon | Search is below the threshold of meaningful differentiation | Tighten the search space or accept robust-score ranking |
| `would_have_picked` is the same as a high-robust-score candidate | The robust ranking and the deflated ranking agree | Safe to override threshold; document why |
| `would_have_picked` differs sharply from the robust-best | Peak vs plateau tradeoff | Prefer robust-best unless the peak is mechanistically explainable |

## Don't

- Don't re-run the optimization with a different seed and pick whichever survives — that's exactly the multiple-testing problem PBO exists to flag.
- Don't lower the threshold without documenting why; a permanent override should justify itself in `best.json`'s methodology block.
- Don't compare PBO values across runs with different fold counts; the rank statistic distribution depends on \(S\).
