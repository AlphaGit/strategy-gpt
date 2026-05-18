# Overfitting & selection

## Intuition

Once you run many backtests over a parameter grid, the *best* result is no longer trustworthy on its own: with enough trials, some configuration will score well by chance even if no real edge exists. Strategy-gpt's optimizer therefore separates two phases. First a *search* explores the parameter space and produces every backtest the optimizer commissioned (`trials.parquet`). Then a *selection layer* — overfitting-aware, deterministic, methodology-grounded — operates over those trials to decide which (if any) candidate may be published as `best.json`.

The selection layer answers three questions:

1. **Is the in-sample winner systematically worse than chance out-of-sample?** If yes, reject the whole run. This is the Probability of Backtest Overfitting (PBO) test.
2. **Of the survivors, which would still look statistically meaningful once we deflate for the fact we tested many configurations?** That is the Deflated Sharpe Ratio (DSR) ranking.
3. **Is the candidate's score a fragile spike, or is the neighborhood around it consistently good?** That is the parameter-sensitivity (robust) score.

The layer is a pure function of the trial set plus the selection knobs. Running it twice on the same artifacts yields byte-identical output, which is what makes `strategy-gpt optimize reselect` reproducible.

## Formalism

### Notation

| Symbol | Meaning |
|--------|--------|
| \(N\) | Number of candidate configurations evaluated in the train phase. |
| \(S\) | Number of folds. |
| \(M_{i,j}\) | Out-of-sample metric (e.g. Sharpe) of candidate \(i\) on fold \(j\). |
| \(\text{SR}^\star\) | Maximum observed Sharpe across the survivor set. |
| \(\widehat{\mathrm{Var}}(\text{SR})\) | Sample variance of the survivor Sharpes. |
| \(\lambda\) | Penalty applied to the standard deviation of neighborhood scores. |
| \(k\) | Number of nearest neighbors used in the robust score. |

### PBO via CSCV

For an even \(S\), enumerate every partition of folds into two equal halves \((A, B)\). For each partition compute the in-sample-best candidate on \(A\) and observe its rank on \(B\). Let

\[
r_{(A,B)} = \frac{\text{rank}_{B}(\arg\max_i \overline{M_{i,A}})}{N}.
\]

PBO is the fraction of partitions where the IS-best lands in the *bottom half* of the OOS ranking:

\[
\text{PBO} = \frac{1}{|\mathcal{P}|} \sum_{(A,B) \in \mathcal{P}} \mathbb{1}\!\left[r_{(A,B)} > \tfrac{1}{2}\right].
\]

For \(S \le 16\) every \(\binom{S}{S/2}\) partition is enumerated. For \(S > 16\) the layer Monte-Carlo samples `max_splits` partitions; the seed is recorded in the manifest. If \(\text{PBO} > \text{pbo\_threshold}\) (default \(0.5\)) the run is `rejected_pbo` and no `best` is published without `--force`. See [Bailey, Borwein, López de Prado, Zhu (2017)](bibliography.md#bailey-borwein-lopez-de-prado-zhu-2017).

### DSR

The max-Sharpe across \(N\) trials is upward biased. The Deflated Sharpe Ratio adjusts for the expected maximum under the null and reports

\[
\text{DSR} = \Phi\!\left(\frac{(\text{SR}^\star - E[\max_N \text{SR}])\sqrt{T-1}}{\sqrt{1 - \widehat{\gamma_3}\,\text{SR}^\star + \tfrac{\widehat{\gamma_4} - 1}{4}\text{SR}^{\star 2}}}\right),
\]

with \(E[\max_N \text{SR}]\) approximated using the Bailey/López de Prado expression with Euler-Mascheroni mixing, and the skew \(\widehat{\gamma_3}\) / kurtosis \(\widehat{\gamma_4}\) defaulting to normal-returns moments because the engine's aggregate metrics dict does not carry per-trade statistics. Effective \(N\) is the number of *distinct parameter sets* by default (`effective_n: distinct_params`) or the raw `trial_count`.

By default the layer ranks survivors by DSR descending; ties break by raw primary score, then by lower per-fold OOS variance. See [Bailey & López de Prado (2014)](bibliography.md#bailey-lopez-de-prado-2014).

### Robust (parameter-sensitivity) score

For each top-\(K\) candidate, compute

\[
\text{robust}_i = \overline{s}_{N_k(i)} \;-\; \lambda \cdot \sigma_{N_k(i)},
\]

where \(N_k(i)\) is the \(k\) nearest already-evaluated trials in min-max-normalized parameter space (Euclidean over numeric dims, \(\{0, 1\}\) distance per categorical mismatch). The neighborhood draws from the *full* trial history, not just the top-\(K\), so the search's own exploration is leveraged. The candidate's own score participates in the neighborhood mean.

The robust score is *always reported* in `best.json`. It is used for final ranking only when `optimize.robust_objective: true` (or `--robust-objective`). Computation is at selection time only; the per-fold search always sees the raw objective so its convergence dynamics are not muddled.

See [López de Prado (2018)](bibliography.md#lopez-de-prado-2018) and [Pardo (2008)](bibliography.md#pardo-2008).

### Decision rule

```text
if PBO > pbo_threshold and not force:
    decision = rejected_pbo
elif robust_objective:
    rank top-K by robust_score desc
else:
    rank top-K by DSR desc (tie: raw_score, then lower per-fold OOS variance)
final = top-1 of the ranked list (if any survive constraints)
```

`best.json` always records the full top-\(K\) scores plus the decision. The candidate the configured ranking would have picked in the absence of the gate is recorded as `would_have_picked` for transparency.

## Worked example (synthetic toy data)

Set \(N = 4\) candidates over \(S = 4\) folds. The per-fold OOS metric matrix \(M\) (rows = candidates, cols = folds):

| Candidate | Fold 1 | Fold 2 | Fold 3 | Fold 4 |
|-----------|--------|--------|--------|--------|
| A         | 1.20   | 1.10   | 0.20   | 0.10   |
| B         | 0.10   | 0.20   | 1.10   | 1.20   |
| C         | 0.70   | 0.75   | 0.72   | 0.68   |
| D         | 0.50   | 0.55   | 0.45   | 0.60   |

### PBO

Enumerate the \(\binom{4}{2} = 6\) equal-half partitions. For each, the IS-best is the candidate with the highest mean on the IS half:

| IS folds | OOS folds | IS-best | OOS rank of IS-best | Bottom half? |
|----------|-----------|---------|---------------------|--------------|
| {1,2}    | {3,4}     | A (1.15) | 4 | yes |
| {1,3}    | {2,4}     | A (0.70) | 4 | yes |
| {1,4}    | {2,3}     | A (0.65) | 4 | yes |
| {2,3}    | {1,4}     | B (0.65) | 4 | yes |
| {2,4}    | {1,3}     | B (0.70) | 4 | yes |
| {3,4}    | {1,2}     | B (1.15) | 4 | yes |

\(\text{PBO} = 6/6 = 1.0\). With the default threshold \(0.5\) the run is `rejected_pbo`. Intuitively: A and B each win in halves where their own clustered folds dominate, but each is *worst* on the other half. C is the steady performer, but never the in-sample winner in this contrived setup.

### DSR

Suppose the survivor Sharpes after the gate were \(\{1.20, 1.10, 0.75, 0.55\}\) over \(T = 252\) trading days each, with \(N_{\mathrm{eff}} = 4\) distinct params. The expected max under the null is approximately

\[
E[\max_4 \text{SR}] \approx (1 - \gamma)\,\Phi^{-1}\!\left(1 - \tfrac{1}{4}\right) + \gamma\,\Phi^{-1}\!\left(1 - \tfrac{1}{4 \cdot e}\right) \approx 0.85,
\]

where \(\gamma \approx 0.5772\) (Euler-Mascheroni). With normal-returns moments DSR for candidate A is

\[
\text{DSR}_A = \Phi\!\left(\frac{(1.20 - 0.85)\sqrt{251}}{\sqrt{1}}\right) \approx \Phi(5.55) \approx 1.0.
\]

The deflation here is mild because \(T\) is large; with \(T = 30\) the term shrinks to roughly \(\Phi(1.92) \approx 0.97\). The toy demonstrates the *shape*: bigger \(N\) and noisier returns drag DSR away from 1.

### Robust score

Treat the same survivors and use \(k = 2\), \(\lambda = 1.0\) in normalized parameter space. If A and B sit at opposite corners (mean of two nearest = 0.65, sd = 0.55) their robust scores are

\[
\text{robust}_A = 0.65 - 1.0 \cdot 0.55 = 0.10.
\]

A candidate sitting in the dense steady-performer cluster (mean 0.70, sd 0.04) instead gets

\[
\text{robust} = 0.70 - 1.0 \cdot 0.04 = 0.66.
\]

The robust score promotes plateaus over peaks, exactly the opposite of DSR's bias.

## Assumptions

- **PBO**: per-fold OOS metrics are exchangeable across folds. The CSCV construction assumes folds are *not* sequentially dependent in a way that makes the partition non-symmetric.
- **PBO with \(S > 16\)**: `max_splits` random partitions form an unbiased estimator only when the sampling is uniform without replacement. The implementation uses `numpy.random.Generator` with the manifest seed.
- **DSR**: returns within a fold are i.i.d. with finite skew and kurtosis. When per-trade moments are unavailable (current default), DSR uses normal-returns moments — a conservative simplification that ignores fat tails.
- **Robust score**: nearest-neighbor distances are meaningful in the rescaled parameter space. Mixing numeric and categorical dims uses Euclidean + 0/1 distance, which is a heuristic rather than a metric in the strict sense.
- **Decision logic**: thresholds are applied to point estimates; the layer does not currently surface confidence intervals around PBO or DSR.

## Limitations

- **PBO degrades on highly correlated folds.** If fold definitions overlap (e.g. small `gap`, rolling slices with substantial overlap), the equal-half partitions are not informative. Pre-validate that fold OOS slices are disjoint in time.
- **PBO failure modes**: with very few candidates (\(N < 4\)) the rank statistic collapses; the layer still computes PBO but the value carries little information. Default to a fixed threshold here at your peril.
- **DSR with non-Gaussian returns**: under heavy tails the normal-moments default *understates* deflation. Strategies with heavy left tails (volatility-short, mean-reversion) should treat DSR conservatively.
- **DSR sensitivity to \(N_{\mathrm{eff}}\)**: `effective_n: trial_count` inflates deflation when many trials evaluate identical parameters (common under recursive_grid restarts); `distinct_params` is preferred unless the search method intentionally re-evaluates configurations.
- **Robust score depends on local density.** Sparse regions get noisy neighborhood means; the score is least reliable where the search explored least.
- **Robust score with categorical dominance**: when most dims are categorical the 0/1 distance collapses many points to identical distance, and the k-NN neighborhood degenerates to "any point with a matching category". Avoid robust ranking in those regimes.
- **All three computations assume the trial set is the population.** Running the layer over a heavily filtered subset (e.g. constraint-failed candidates excluded) changes the meaning of "max Sharpe across trials" — `manifest.json` records the filter, but downstream comparisons must respect it.

## References

- [Bailey, Borwein, López de Prado, Zhu (2017) — *The Probability of Backtest Overfitting*](bibliography.md#bailey-borwein-lopez-de-prado-zhu-2017)
- [Bailey & López de Prado (2014) — *The Deflated Sharpe Ratio*](bibliography.md#bailey-lopez-de-prado-2014)
- [López de Prado (2018) — *Advances in Financial Machine Learning*, ch. 11-12](bibliography.md#lopez-de-prado-2018)
- [Pardo (2008) — *The Evaluation and Optimization of Trading Strategies*, ch. 9](bibliography.md#pardo-2008)

## Related material

- Operator actions when a run is `rejected_pbo`: [Interpret PBO rejection](../how-to/interpret-pbo-rejection.md).
- Configuration surface: [Objective spec](../reference/objective-spec.md).
