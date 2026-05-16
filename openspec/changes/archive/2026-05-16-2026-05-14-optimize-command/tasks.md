## 1. Recursive grid searcher

- [x] 1.1 Add `RecursiveGridSearcher` to `python/strategy_gpt/optimizer.py`: round-wise uniform grid over current box, `top_k` cell selection by score, box-shrink to union of top cells, plateau-stop on per-dim convergence.
- [x] 1.2 Per-dim resolution override via `space.<param>.resolution`.
- [x] 1.3 Integer params: round at sampling time, freeze dim when cell width < 1.
- [x] 1.4 Choice params: treated as full enumeration per round (no narrowing).
- [x] 1.5 Unit tests on a 2-D synthetic objective with a known max; verify convergence to ε of true optimum within `depth` rounds.

## 2. Per-fold orchestrator

- [x] 2.1 Add `python/strategy_gpt/optimization_runner.py` orchestrating the per-fold flow: for each fold, run searcher on train slice; collect F fold winners.
- [x] 2.2 Cross-validate all fold winners across all fold OOS slices in one packed batch.
- [x] 2.3 Apply objective's OOS aggregate scoring (mean of per-fold OOS metrics; constraint application; soft-secondary scoring).
- [x] 2.4 Select final by best OOS-aggregate score; tie-break by lower per-fold variance.
- [x] 2.5 Wire to engine batch-packing (uses `failure_mode: continue` from the prereq change).

## 3. CLI command

- [x] 3.1 Replace the stub `strategy-gpt optimize` with a real implementation taking `--spec experiment.yaml`.
- [x] 3.2 `--benchmark` flag (with optional `--sample N`, default 3).
- [x] 3.3 `--yes` to skip the post-benchmark confirmation prompt.
- [x] 3.4 `--json` for machine-readable output.
- [x] 3.5 `strategy-gpt optimize inspect <opt_id> [--trial <trial_id>]` subcommand.
- [x] 3.6 `strategy-gpt optimize replay <opt_id> --trial <trial_id>` subcommand that reconstructs a single-run BatchSpec and dumps the `BacktestResult`.

## 4. Benchmark predictor

- [x] 4.1 Add `python/strategy_gpt/benchmark.py`: sample N random candidates, run N × F backtests as one packed batch, compute median + std + spinup.
- [x] 4.2 Plan-run counter per method: `recursive_grid`, `grid`, `random`, `bayesian`.
- [x] 4.3 Predict wall time and ledger footprint; print structured report.

## 5. Parallelism auto resolver

- [x] 5.1 Add a single helper `resolve_parallelism(value: int | "auto") -> int` shared by `run` and `optimize`.
- [x] 5.2 Honor `sched_getaffinity` on linux; fall back to `os.cpu_count()`; leave one CPU headroom; minimum of 1.
- [x] 5.3 Record resolved integer into the optimization manifest.

## 6. Persistence

- [x] 6.1 Create `ledger/optimizations/<opt_id>/` on optimization start with `manifest.json`.
- [x] 6.2 Stream trial rows to `trials.parquet` in append-friendly chunks (write per round, not per trial).
- [x] 6.3 Write `best.json` on completion with the winning trial pointer.
- [x] 6.4 Maintain `ledger/optimizations.sqlite` index: insert on start, update status/finished_at on completion.
- [x] 6.5 `optimize inspect` reads the manifest and the parquet; pretty-prints trial summary.
- [x] 6.6 `optimize replay` reconstructs the BatchSpec and submits to the engine; supports `--out path/to/result.json`.

## 7. Reference example migration

- [x] 7.1 Delete `examples/vxx/optimize.py`.
- [x] 7.2 Add an `optimize` block + `folds` block to `examples/vxx/experiment.yaml` matching the prior runner.
- [x] 7.3 Document the new invocation in `docs/cli-cookbook.md` (`optimize`, `optimize --benchmark`, `optimize inspect`, `optimize replay`).

## 8. Tests

- [x] 8.1 Per-fold orchestrator on synthetic objective: 2 params, 4 folds, known optimum, verify final OOS-aggregate selection.
- [x] 8.2 Benchmark prediction accuracy: fixed per-run cost via a stub `evaluate`, verify predicted vs actual within ±20%.
- [x] 8.3 Persistence round-trip: small optimization → `inspect` outputs match in-memory result → `replay` reproduces a single trial's `BacktestResult` byte-identically.
- [x] 8.4 Parallelism auto on linux mock with affinity set to 4 cores → resolves to 3.
- [x] 8.5 Plateau-stop test: synthetic surface whose top cell is already < ε wide → optimizer stops at round 1.

## 9. Docs

- [x] 9.1 Update `docs/cli-cookbook.md` with the four optimize-related recipes.
- [x] 9.2 Update `CLAUDE.md` Domain vocabulary: drop "walk-forward" mentions; add `opt_id`, `trial`, `fold winner`, `OOS aggregate`.
