## 1. Recursive grid searcher

- [ ] 1.1 Add `RecursiveGridSearcher` to `python/strategy_gpt/optimizer.py`: round-wise uniform grid over current box, `top_k` cell selection by score, box-shrink to union of top cells, plateau-stop on per-dim convergence.
- [ ] 1.2 Per-dim resolution override via `space.<param>.resolution`.
- [ ] 1.3 Integer params: round at sampling time, freeze dim when cell width < 1.
- [ ] 1.4 Choice params: treated as full enumeration per round (no narrowing).
- [ ] 1.5 Unit tests on a 2-D synthetic objective with a known max; verify convergence to ε of true optimum within `depth` rounds.

## 2. Per-fold orchestrator

- [ ] 2.1 Add `python/strategy_gpt/optimization_runner.py` orchestrating the per-fold flow: for each fold, run searcher on train slice; collect F fold winners.
- [ ] 2.2 Cross-validate all fold winners across all fold OOS slices in one packed batch.
- [ ] 2.3 Apply objective's OOS aggregate scoring (mean of per-fold OOS metrics; constraint application; soft-secondary scoring).
- [ ] 2.4 Select final by best OOS-aggregate score; tie-break by lower per-fold variance.
- [ ] 2.5 Wire to engine batch-packing (uses `failure_mode: continue` from the prereq change).

## 3. CLI command

- [ ] 3.1 Replace the stub `strategy-gpt optimize` with a real implementation taking `--spec experiment.yaml`.
- [ ] 3.2 `--benchmark` flag (with optional `--sample N`, default 3).
- [ ] 3.3 `--yes` to skip the post-benchmark confirmation prompt.
- [ ] 3.4 `--json` for machine-readable output.
- [ ] 3.5 `strategy-gpt optimize inspect <opt_id> [--trial <trial_id>]` subcommand.
- [ ] 3.6 `strategy-gpt optimize replay <opt_id> --trial <trial_id>` subcommand that reconstructs a single-run BatchSpec and dumps the `BacktestResult`.

## 4. Benchmark predictor

- [ ] 4.1 Add `python/strategy_gpt/benchmark.py`: sample N random candidates, run N × F backtests as one packed batch, compute median + std + spinup.
- [ ] 4.2 Plan-run counter per method: `recursive_grid`, `grid`, `random`, `bayesian`.
- [ ] 4.3 Predict wall time and ledger footprint; print structured report.

## 5. Parallelism auto resolver

- [ ] 5.1 Add a single helper `resolve_parallelism(value: int | "auto") -> int` shared by `run` and `optimize`.
- [ ] 5.2 Honor `sched_getaffinity` on linux; fall back to `os.cpu_count()`; leave one CPU headroom; minimum of 1.
- [ ] 5.3 Record resolved integer into the optimization manifest.

## 6. Persistence

- [ ] 6.1 Create `ledger/optimizations/<opt_id>/` on optimization start with `manifest.json`.
- [ ] 6.2 Stream trial rows to `trials.parquet` in append-friendly chunks (write per round, not per trial).
- [ ] 6.3 Write `best.json` on completion with the winning trial pointer.
- [ ] 6.4 Maintain `ledger/optimizations.sqlite` index: insert on start, update status/finished_at on completion.
- [ ] 6.5 `optimize inspect` reads the manifest and the parquet; pretty-prints trial summary.
- [ ] 6.6 `optimize replay` reconstructs the BatchSpec and submits to the engine; supports `--out path/to/result.json`.

## 7. Reference example migration

- [ ] 7.1 Delete `examples/vxx/optimize.py`.
- [ ] 7.2 Add an `optimize` block + `folds` block to `examples/vxx/experiment.yaml` matching the prior runner.
- [ ] 7.3 Document the new invocation in `docs/cli-cookbook.md` (`optimize`, `optimize --benchmark`, `optimize inspect`, `optimize replay`).

## 8. Tests

- [ ] 8.1 Per-fold orchestrator on synthetic objective: 2 params, 4 folds, known optimum, verify final OOS-aggregate selection.
- [ ] 8.2 Benchmark prediction accuracy: fixed per-run cost via a stub `evaluate`, verify predicted vs actual within ±20%.
- [ ] 8.3 Persistence round-trip: small optimization → `inspect` outputs match in-memory result → `replay` reproduces a single trial's `BacktestResult` byte-identically.
- [ ] 8.4 Parallelism auto on linux mock with affinity set to 4 cores → resolves to 3.
- [ ] 8.5 Plateau-stop test: synthetic surface whose top cell is already < ε wide → optimizer stops at round 1.

## 9. Docs

- [ ] 9.1 Update `docs/cli-cookbook.md` with the four optimize-related recipes.
- [ ] 9.2 Update `CLAUDE.md` Domain vocabulary: drop "walk-forward" mentions; add `opt_id`, `trial`, `fold winner`, `OOS aggregate`.
