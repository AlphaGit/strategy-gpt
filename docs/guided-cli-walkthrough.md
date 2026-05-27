# Guided CLI walkthrough

This page traces the natural arc an operator follows when using `strategy-gpt`: set up the toolchain, explore the command surface, acquire data, author a strategy, run a single deterministic backtest, optimize parameters, propose and test logic hypotheses, iterate, and finally reproduce any recorded run byte-for-byte.

The nine stages mirror real usage order. Commands used in more than one stage (e.g. `optimize inspect` during stage 5 and again during iteration) appear in each relevant stage — context, not deduplication, is what helps the operator.

Each stage stays surface-level: one framing paragraph, fenced command snippets, and one or two sentences of *why*. Depth (full flag tables, methodology, schemas) lives in the linked "See also" pages.

> **Scope.** This walkthrough is for *operators* — people who run prompts, drive optimizations, and read backtest results. Contributor / CI tooling (`make lint`, `make test`, `pre-commit`, `cargo check --workspace`) is intentionally out of scope; see `CONTRIBUTING.md` and `CLAUDE.md` for those.

## Stage 0 — Setup {#stage-0-setup}

One-time toolchain steps that every other stage assumes. You install the orchestrator, build the engine worker plus the reference strategy, set the LLM credentials, and turn on a build cache. After this, every subsequent stage works against a venv-activated shell rooted at the repo.

Print the installed CLI version (sanity check that the entry point is on `PATH`):

```bash
strategy-gpt version
```

Build the engine worker and the reference VXX strategy. The worker is the per-batch subprocess every backtest fans out to (the binary `engine-worker` ships in the `engine` crate); the VXX strategy is the bundled smoke target this walkthrough refers to throughout. Run from the repo root — the Rust workspace `Cargo.toml` lives at `crates/Cargo.toml`, so each command uses `--manifest-path` instead of changing directory:

```bash
cargo build --manifest-path crates/Cargo.toml -p engine --bin engine-worker
cargo build --manifest-path crates/Cargo.toml -p vxx-strategy
```

Build the PyO3 bindings into the active venv. Required after a fresh checkout and after any change to the Rust crates exposed via `crates/py-bindings/`:

```bash
maturin develop -m crates/py-bindings/Cargo.toml
```

Export an LLM key. `author` and `hypothesize` need one; everything else (fetch, run, optimize, replay) does not. Anthropic is the default for the stronger reasoning stages; OpenAI is supported as an alternative:

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # used by author + hypothesize stages
export OPENAI_API_KEY=sk-...           # alternative; pick whichever your --model expects
```

Turn on the shared compiler cache. Rust rebuilds (author emit/build, hypothesize candidate compiles) hit `cargo` repeatedly; `sccache` makes the second and subsequent builds dramatically faster:

```bash
export RUSTC_WRAPPER=sccache
```

> **Contributor tooling is out of scope here.** `make lint`, `make test`, `pre-commit`, and `cargo check --workspace` are CI / contributor concerns. If you're contributing to the platform itself rather than running it, start at [`CLAUDE.md`](https://github.com/AlphaGit/strategy-gpt/blob/main/CLAUDE.md) and [`CONTRIBUTING.md`](https://github.com/AlphaGit/strategy-gpt/blob/main/CONTRIBUTING.md).

### See also

- [`CLAUDE.md` — Build / develop](https://github.com/AlphaGit/strategy-gpt/blob/main/CLAUDE.md#build--develop): the canonical build instructions including Rust toolchain pin.
- [Architecture](explanation/architecture.md): why the orchestrator / Rust core / worker split exists.

## Stage 1 — Explore the command surface {#stage-1-explore}

Before driving any workflow, get a feel for what the CLI exposes. `strategy-gpt` follows the standard `--help` convention: the root command lists subcommands, and each subcommand has its own `--help` with flags, defaults, and a short purpose line. Two no-cost preview flags (`--dry-run` on `hypothesize`, `--benchmark` on `optimize`) let you inspect resolved wiring and predicted cost without spending tokens or running backtests.

List the root surface:

```bash
strategy-gpt --help
```

Drill into one subcommand:

```bash
strategy-gpt hypothesize --help
strategy-gpt optimize --help
strategy-gpt author --help
```

Preview the hypothesize loop's resolved dependencies (baseline source, fold source, per-stage models, engine-worker path, budgets) without invoking the workflow:

```bash
strategy-gpt hypothesize spy_atr --baseline-defaults --dry-run
```

This is the cheapest way to confirm that the wiring resolves end-to-end: no LLM call, no engine call, no token spend.

Predict the cost and ledger footprint of an optimize run before launching:

```bash
strategy-gpt optimize --spec examples/vxx/experiment.yaml --benchmark --sample 3 --yes
```

`--benchmark` runs `--sample N` trials, extrapolates total cost (wall-clock, ledger bytes), prints the estimate, and exits. `--yes` accepts the projection without prompting; drop it to interactively confirm before the full run kicks off.

### See also

- [hypothesize CLI reference](reference/hypothesize-cli.md): every flag the hypothesize subcommand accepts.
- [Experiment spec reference](reference/experiment-spec.md): the YAML/JSON shape `--spec` consumes.

## Stage 2 — Acquire data {#stage-2-data}

A *dataset* in this system is a normalized OHLCV bar stream pinned to a content-addressed manifest. The data gateway (`crates/data-gateway`) produces one when you fetch from a provider; the manifest hash identifies the exact bytes the engine saw, and the ledger stores that hash alongside every run so any backtest can be reproduced byte-for-byte. Year segmentation means a single fetch over `2018–2026` produces nine cache blobs (one per calendar year); a later fetch over `2020–2024` reuses those blobs and only hits the network for the gap.

Download VXX history through the yfinance provider:

```bash
strategy-gpt fetch \
  --provider yfinance --symbol VXX \
  --start 2018-01-01 --end 2026-12-31 \
  --resolution Day --adjustment back_adjusted \
  --mode prefer_cache --root cache
```

Output:

```json
{
  "bar_count": 2087,
  "manifest_hash": "29bdecf5fe758d38d524025321aacfb2825daf2fbcce4a3c2c04377bf635b97b",
  "manifest_blobs": [ "<blob-hash>", ... ],
  "warning_count": 0
}
```

Save the `manifest_hash` — that's how every downstream surface (engine, ledger, optimizer) references this exact dataset.

--8<-- "cli-roots.md"

--8<-- "cli-progress.md"

Use a CSV provider for bring-your-own data:

```bash
strategy-gpt fetch \
  --provider my_csv --symbol VXX \
  --csv-provider-dir ./my-csvs \
  --start 2018-01-01 --end 2024-12-31 \
  --resolution Day --adjustment back_adjusted
```

Expects `./my-csvs/VXX.csv` with header `timestamp,open,high,low,close,volume`. RFC 3339 or `YYYY-MM-DD` timestamps both accepted.

**Cache modes.** Pick `--mode` based on what guarantee you need: `prefer_cache` (default) uses cached blobs and only fetches missing years; `validate` re-fetches and diffs against the cached blob, emitting divergence warnings (currently aliased to `prefer_cache` — the full re-fetch/diff path is a planned follow-up); `force_refresh` bypasses the cache entirely and overwrites every year's blob; `offline` never hits the network and fails if any year is missing locally. Use `offline` for reproducibility-sensitive CI:

```bash
strategy-gpt fetch \
  --provider yfinance --symbol VXX \
  --start 2018-01-01 --end 2026-12-31 \
  --resolution Day --adjustment back_adjusted \
  --mode offline --root cache
```

Inspect the cache:

```bash
strategy-gpt cache-stats --root cache
# {"blob_count": 9, "total_bytes": 481234}
```

**Materialize cached bars to JSON.** `strategy-gpt run` reads bars from a JSON file. Cache blobs are not directly that shape; you have to pull them through the gateway. This is the one place where the operator must drop to Python because no CLI primitive exists:

```python
import json
from datetime import datetime, UTC
from pathlib import Path
from strategy_gpt.gateway import Gateway
from strategy_gpt.types import BarRequest, Resolution, AdjustmentPolicy

gw = Gateway('cache')
gw.register_yfinance_provider('yfinance')
req = BarRequest(
    provider='yfinance', symbol='VXX',
    start=datetime(2018,1,1,tzinfo=UTC), end=datetime(2026,12,31,tzinfo=UTC),
    resolution=Resolution.DAY, adjustment=AdjustmentPolicy.BACK_ADJUSTED,
)
resp = gw.fetch(req, 'prefer_cache')
bars = [b.model_dump(mode='json') for b in resp.bars]
Path('examples/vxx/bars.json').write_text(json.dumps(bars))
print(len(bars), 'bars')
```

A one-time step per dataset window. The output JSON is reused across every subsequent `strategy-gpt run` against the same window.

### See also

- [Experiment spec reference](reference/experiment-spec.md): the `bars` block (`dataset` or `request`) the engine consumes downstream.

## Stage 3 — Author a strategy {#stage-3-author}

`strategy-gpt author` is the LLM-driven creation primitive. It runs an interactive dialog, locks each clarification into `crates/<name>-strategy/.author/decisions.jsonl` (the authoritative state — the LLM's chat history is non-load-bearing), then emits `Cargo.toml` + `src/lib.rs` + `intent.toml` + `smoke.toml`, runs `cargo build`, and runs a smoke backtest. Success means *compiles and smoke passes*. There is no ledger row, no verdict.

!!! warning "Strategy name → on-disk path"

    The intent `name` you choose is the short handle. Everywhere a directory or package id is required, the platform appends `-strategy`:

    - Crate directory: `crates/<name>-strategy/` (e.g. `vxx` → `crates/vxx-strategy/`).
    - Cargo package id (used with `cargo build -p ...`): `<name>-strategy` (e.g. `cargo build -p vxx-strategy`).
    - Compiled library on disk: `lib<name>_strategy.{dylib,so,dll}` — hyphens in the crate id become underscores in the library name (e.g. `vxx` → `libvxx_strategy.dylib`; `multi_signal_weighted` → `libmulti_signal_weighted.dylib`).
    - All `strategy-gpt` subcommands that take a strategy handle (`hypothesize <name>`, `recent-decisions --strategy <name>`, `hypothesis replay --strategy <name>`) take the *short* form (`vxx`), not `vxx-strategy`.

    Pick a name that survives filesystem and Cargo conventions: lowercase, ASCII, snake_case or kebab-case, no spaces, no leading digits, ≤ 40 chars. Anything else will collide with `cargo build` or with one of the path layouts above.

Invoke with a one-line seed:

```bash
strategy-gpt author "trend-follow SPY with ATR stops, daily bars"
```

The LLM opens with the seed and asks one focused clarifying question per turn until it has enough to commit to an `AuthorIntent`. On success it prints a JSON envelope with the crate path and a next-step hint.

--8<-- "cli-env-keys.md"

Invoke with no seed:

```bash
strategy-gpt author
```

The first dialog turn asks what you want to author. Everything else is the same.

**Edit-mode** is triggered by re-running `author` against an existing crate name. The dialog detects the name collision, asks `edit` or rename, and on `edit` loads the existing `intent.toml`, `src/lib.rs`, `Cargo.toml`, and `smoke.toml` into context so subsequent emissions are framed as modifications:

```bash
strategy-gpt author my_existing_strategy   # re-runs against the existing crate; prompts edit/rename
```

There is no `--edit` flag — the collision check is the trigger.

Verify against the full walk-forward batch (not just the smoke window):

```bash
strategy-gpt author "vol-target SPY" --verify=batch
```

After the smoke run, the engine runs the full batch declared in the emitted `experiment.yaml`. A failed fold pops control back to the dialog; the crate stays on disk for inspection.

**Repair budget.** The emit/build/smoke loop is bounded. Two flags raise the ceiling when an emission is one or two tweaks short of compiling:

```bash
strategy-gpt author "vol-target SPY" --k-repair-emit 4 --k-repair-build 3
```

`--k-repair-emit N` (default `2`) is the number of repair attempts the emit/build/smoke stage gets — `k=2` means three total attempts. `--k-repair-build N` (default `2`) is the inner budget the build sub-stage gets within a single emit attempt. Raising both costs roughly linearly in tokens and wall-clock; raise gently.

**Model and verbosity.** Override the reasoning model with `--model` (e.g. `claude-sonnet-4-6`, `o3`). `--quiet` suppresses the locked-in decisions panel and collapses progress lines; `--verbose` streams per-line cargo / rustc output during the build:

```bash
strategy-gpt author "vol-target SPY" --model claude-sonnet-4-6
strategy-gpt author "vol-target SPY" --quiet
strategy-gpt author "vol-target SPY" --verbose
```

**Multi-line input.** Short answers — type and press Enter. For pasted YAML, multi-paragraph explanations, or copied blocks of code: either paste it (the input wrapper probes stdin after each line and slurps any buffered lines that arrive together, so a paste lands as a single dialog turn), or use sentinels — type `<<<` on its own line to enter multi-line mode, then your content (any number of lines, blank lines preserved), then `>>>` on its own line to submit. Both modes apply to dialog turns and to the free-form guidance prompts in the repair-exhaustion menu.

**Repair-exhaustion menu.** When the emit/build/smoke loop burns through its budget, control returns to the operator with four options:

1. **Suggest an alternative approach** — type a natural-language amendment; the LLM revises the intent and the loop restarts with fresh budget.
2. **Retry with an extended budget** — provide new `k_repair_emit` / `k_repair_build` values; the loop restarts with the same intent.
3. **Edit a specific decision** — name a field (`mechanism_summary`, `param_sketch`, `smoke_spec`, `universe`) and the amendment; the LLM revises only that field.
4. **Abort** — exit non-zero. The crate files and `.author/decisions.jsonl` stay on disk for inspection.

**Troubleshooting.**

- **`smoke_failed: no_trades`** — the emitted strategy compiled and ran without panic but did not place any simulated trades over the smoke window. Typical operator action: when control returns, propose loosening an entry filter or extending the smoke window (`smoke_spec.start` earlier).
- **`exhausted repair budget`** — the LLM could not get the crate to compile + smoke-pass within `k_repair_emit + 1` attempts. Pick menu option 1 (alternative approach) or 2 (extend budget); option 2 is the right move when the failures are the LLM nearly succeeding on each retry, option 1 when the failures look structurally similar.
- **`smoke_failed: timeout`** — the smoke backtest exceeded the default 60s budget. The window is probably too large or the strategy is doing per-bar `O(n²)` work; have the LLM emit a tighter window and a more direct implementation.

### See also

- [Tutorial — Author a strategy](tutorials/author-a-strategy.md): end-to-end walkthrough from a natural-language seed.
- [How-to — Author a strategy](how-to/author-a-strategy.md): task-oriented recipe page with deeper coverage of edit-mode and `--verify=batch`.
- [Explanation — Hand-authoring a strategy](explanation/hand-authoring-a-strategy.md): the engineer-targeted deep dive on the sealed `Strategy` trait surface author targets.

## Stage 4 — One-shot backtest {#stage-4-one-shot}

Reach for `strategy-gpt run` when you want a single deterministic backtest of an existing strategy: smoke-checking a parameter tweak, reproducing a specific configuration, or fanning out a small parameter sweep without invoking the optimizer. Each run pins `(artifact_hash, dataset_manifest, params, seed, runner_version)` and writes the result through the orchestrator; identical inputs produce a byte-identical `BacktestResult`.

Submit and block until completion:

```bash
strategy-gpt run \
  --spec examples/vxx/experiment.yaml \
  --worker crates/target/debug/engine-worker \
  --wait
```

`--wait` prints the full `JobStatus` JSON on completion: `{ status, results[], error }`, where each `results[i]` is a `RunResult` discriminated entry — successful runs carry `{ status: "ok", run_index, result: { metrics, trades, signals, equity, regimes, exec_log, meta } }`; failed runs (only emitted under `failure_mode: continue`) carry `{ status: "failed", run_index, error_kind, message }`. See the [BatchSpec reference](reference/batch-spec.md) for the full output schema.

Submit without blocking (async pattern):

```bash
HANDLE=$(strategy-gpt run --spec examples/vxx/experiment.yaml)
echo "submitted: $HANDLE"
```

Drop `--wait` and the CLI returns the job handle immediately. Polling currently lives in Python (`Engine.poll(handle)` on the orchestrator surface); the CLI exposes blocking polls via `--wait` only.

**Tweak parameters and re-run.** Parameter changes never trigger a rebuild — that's what `runs[].params` is for. Each entry under `runs:` needs `params`, `seed` (determinism anchor; defaults to `0`), and `slice` (the half-open `[start, end)` window the run evaluates over) at the same level — only `params` changes between sweeps. Edit `examples/vxx/experiment.yaml`:

```yaml
runs:
  - params:
      vol_lo: 0.35    # change me
      vol_hi: 0.80    # change me
      size:   100.0
      symbol: VXX
    seed: 42
    slice:
      start: 2018-01-01T00:00:00Z
      end:   2026-12-31T00:00:00Z
```

Re-run `strategy-gpt run --spec ... --wait`. The artifact hash is unchanged; the engine reuses the compiled `.dylib`.

**Multi-run sweep.** Add more entries to the spec's `runs:` list. `seed` and `slice` are peers of `params` on each entry, not nested inside it:

```yaml
runs:
  - params: { vol_lo: 0.30, vol_hi: 0.75, size: 100.0, symbol: VXX }
    seed: 42
    slice: { start: 2018-01-01T00:00:00Z, end: 2026-12-31T00:00:00Z }
  - params: { vol_lo: 0.35, vol_hi: 0.80, size: 100.0, symbol: VXX }
    seed: 42
    slice: { start: 2018-01-01T00:00:00Z, end: 2026-12-31T00:00:00Z }
  - params: { vol_lo: 0.40, vol_hi: 0.85, size: 100.0, symbol: VXX }
    seed: 42
    slice: { start: 2018-01-01T00:00:00Z, end: 2026-12-31T00:00:00Z }
```

The engine compiles once and fans out across `parallelism` worker subprocesses (set under `engine:` or at the spec root; defaults to `auto` = `max(1, cpus - 1)`).

### See also

- [Experiment spec reference](reference/experiment-spec.md): every field on the YAML, including `bars.dataset` (cache-only), the `modes` axis, and `caps`.
- [Batch spec reference](reference/batch-spec.md): the internal `BatchSpec` shape and the `BacktestResult` output schema.

## Stage 5 — Optimize {#stage-5-optimize}

`strategy-gpt optimize` drives a per-fold search: for each fold of the experiment's `folds` block the configured method runs against the fold's *train* slice; every fold winner is then cross-validated across every fold's *OOS* slice and the candidate with the highest OOS-aggregate score wins. Trial rows, the run manifest, and `best.json` land under `ledger/optimizations/<opt_id>/`; a SQLite index at `ledger/optimizations.sqlite` lists every run. An overfitting-aware selection layer (PBO, Deflated Sharpe, robust-rank) runs over the trials before `best.json` is published.

Default method (whatever the spec declares, typically `recursive_grid`):

```bash
strategy-gpt optimize --spec examples/vxx/experiment.yaml
```

**Method overrides.** `--method <name>` overrides the spec on the fly. One paragraph and one snippet per method:

- **`sobol`** — Owen-scrambled Sobol quasi-random covers the parameter space more uniformly than `random` at the same budget. Drop-in replacement for `random` and a good seed for evolutionary methods.

  ```bash
  strategy-gpt optimize --spec experiment.yaml --method sobol
  ```

- **`recursive_grid`** — adaptive grid that zooms toward the best region across `depth` levels. Strong default for small-to-medium continuous spaces.

  ```bash
  strategy-gpt optimize --spec experiment.yaml --method recursive_grid
  ```

- **`lhs_polish`** — Latin Hypercube seeds the space, Hooke-Jeeves polishes from the top-K LHS points. Per-iteration cost is `top_k * 2 * D` runs; cheap small-budget baseline.

  ```bash
  strategy-gpt optimize --spec experiment.yaml --method lhs_polish
  ```

- **`successive_halving`** — evaluates many candidates on a small fold subset, halves the bottom by `1/eta`, doubles the fold budget, repeats. Cheaper than flat per-fold search when most candidates are obviously bad.

  ```bash
  strategy-gpt optimize --spec experiment.yaml --method successive_halving
  ```

- **`cma_es`** — Covariance Matrix Adaptation Evolution Strategy (Hansen). Strong on smooth-but-noisy continuous surfaces with parameter interaction; each generation packs as one engine batch.

  ```bash
  strategy-gpt optimize --spec experiment.yaml --method cma_es
  ```

- **`differential_evolution`** — population-based search (Storn & Price). Best on noisy, multi-modal surfaces with mixed-integer dims; each generation packs as one engine batch.

  ```bash
  strategy-gpt optimize --spec experiment.yaml --method differential_evolution
  ```

Predict the cost and ledger footprint of any of the above before launching:

```bash
strategy-gpt optimize --spec experiment.yaml --benchmark --sample 3 --yes
```

`--benchmark` runs `--sample N` trials, extrapolates total cost, prints the estimate, and exits. `--yes` accepts without prompting.

Control fan-out:

```bash
strategy-gpt optimize --spec experiment.yaml --parallelism auto    # default: max(1, cpus - 1)
strategy-gpt optimize --spec experiment.yaml --parallelism 8       # explicit cap
```

The resolved value is recorded in the optimization manifest.

**Inspect a finished run:**

```bash
strategy-gpt optimize inspect <opt_id>                 # run manifest + summary
strategy-gpt optimize inspect <opt_id> --trial 4271    # one trial row
```

**Replay a single trial** byte-identically — same `(artifact_hash, dataset_manifest, params, seed, runner_version)`, so the resulting `BacktestResult` matches the ledger entry bit-for-bit:

```bash
strategy-gpt optimize replay <opt_id> --trial 4271 --out result.json
```

**Re-run the selection layer post-hoc** without re-running any backtests. This writes a new `best_<timestamp>.json` next to the original (the audit trail of selection decisions over the same trial set is always preserved):

```bash
strategy-gpt optimize reselect <opt_id> --pbo-threshold 0.7
strategy-gpt optimize reselect <opt_id> --robust-objective
```

`--pbo-threshold` tightens / loosens the Probability of Backtest Overfitting cutoff. `--robust-objective` ranks the final by parameter-sensitivity score in place of Deflated Sharpe.

**Compare two selection outputs from the same run:**

```bash
strategy-gpt optimize compare <opt_id> best.json best_<timestamp>.json
```

`≠` flags rows that differ between the two rankings; `=` confirms the rows that agree (e.g. PBO, which is a property of the trial set rather than the ranking).

**Force publish despite a PBO rejection.** When you want to publish a `best.json` even though the selection layer flagged `rejected_pbo`, `--force` records the override explicitly in the manifest (audit trail intact):

```bash
strategy-gpt optimize --spec experiment.yaml --force
```

### See also

- [Objective spec reference](reference/objective-spec.md): primary, secondary, and tradeoff knobs.
- [Explanation — Overfitting & selection](explanation/overfitting-and-selection.md): PBO, Deflated Sharpe, and the robust score, with limitations.
- [How-to — Interpret a PBO rejection](how-to/interpret-pbo-rejection.md): operator actions when `decision.status == "rejected_pbo"`.

## Stage 6 — Hypothesize improvements + KB {#stage-6-hypothesize}

The hypothesis loop is a LangGraph workflow (`diagnose → kb_query → generate → critique → rank → select`) over an immutable pydantic state. Each iteration of the inner `generate → critique → rank` cycle produces fresh hypothesis candidates informed by the knowledge base; the loop exits when enough candidates pass critique, the iteration budget is exhausted, or new candidates closely resemble prior rejections. Every accepted *and* rejected decision is persisted to the per-strategy ledger so subsequent runs don't re-propose what's already been rejected.

**Baseline modes** are mutually exclusive; one must be supplied.

The **cheapest path** uses the crate's parameter defaults as the baseline. Wiring builds an `evaluate_fold` over the crate's `smoke.toml` (or `experiment.yaml` if present), invokes it at the parameter defaults declared in `intent.toml.param_schema_sketch`. The comparison space matches candidates' but the baseline is less rigorous than an optimized one:

```bash
strategy-gpt hypothesize spy_atr --baseline-defaults
```

The **rigorous path** lifts the baseline from a prior optimize run. Wiring reads `best.json` (+ per-fold `oos_metrics`) from `ledger/optimizations/<opt-run-id>/`. Use after a real optimize run; per-fold scores come from the OOS folds the optimizer cross-validated:

```bash
strategy-gpt hypothesize spy_atr --baseline-from <opt-run-id>
```

Inspect resolved dependencies (baseline source, fold source, per-stage models, engine-worker path, budgets) without invoking the workflow:

```bash
strategy-gpt hypothesize spy_atr --baseline-defaults --dry-run
```

Iterate fast — single-fold evaluator, small mini-optimize budget:

```bash
strategy-gpt hypothesize spy_atr --baseline-defaults --quick
```

Pick the objective the workflow optimizes against (default `sharpe`):

```bash
strategy-gpt hypothesize spy_atr --baseline-from <opt-id> --objective sortino
```

**Per-stage model overrides.** The hypothesize workflow uses different LLM stages with different reasoning needs; you can route each to a different model. Defaults pick the most capable model the env's API keys allow. Use stronger models on the generative stages and lighter ones on critique/rank when iterating:

```bash
strategy-gpt hypothesize spy_atr \
  --baseline-defaults \
  --model-stage1 claude-opus-4-7 \
  --model-stage2 claude-sonnet-4-6 \
  --model-stage3 claude-sonnet-4-6 \
  --model-critique claude-haiku-4-5-20251001 \
  --model-rank claude-haiku-4-5-20251001
```

**KB store.** `--kb-store <path>` (default `kb/store/`) points at the SQLite-backed knowledge base store. The store lazy-builds from `kb/sources.toml` on first run with a one-time progress banner; subsequent runs hit the warm store. `--rebuild-kb` forces a fresh build:

```bash
strategy-gpt hypothesize spy_atr --baseline-defaults --kb-store kb/store --rebuild-kb
```

**Audit accepted vs rejected vs deferred decisions:**

```bash
strategy-gpt recent-decisions --root ledger --limit 25
```

Outcomes come in three flavors. `accepted` (workflow accepted) and `rejected` (logic failure — `reject_schema`, `reject_smoke`, `reject_noise`, `reject_variance`, `reject_verdict`) both bias future ideation: the critique stage's duplicate-similarity check biases against ideas resembling either. `deferred` (mechanical failure — `reject_build`, `reject_lint`, `reject_format`, `reject_deps`, or `exhausted_repair_budget` on stages 1–3) does *not* bias future ideation: the LLM couldn't compile the code, the hypothesis is preserved, future runs may re-propose it.

**Replay a recorded decision.** Re-runs the workflow nodes against the persisted state and prints a JSON summary (workflow state at acceptance time, candidate diff, per-fold metrics, baseline source label, citations). `--strategy <name>` scopes the lookup when decision IDs collide across strategies:

```bash
strategy-gpt hypothesis replay <decision-id>
strategy-gpt hypothesis replay <decision-id> --strategy spy_atr
```

**Diff a candidate against the baseline.** Prints a unified diff of the candidate's source bundle vs the baseline's. Useful for code-review-style inspection of what the workflow proposed:

```bash
strategy-gpt hypothesis diff <decision-id>
strategy-gpt hypothesis diff <decision-id> --strategy spy_atr
```

### See also

- [How-to — Run the hypothesize loop](how-to/run-hypothesize.md): task-oriented recipe coverage of every flag.
- [How-to — Read progress output](how-to/read-progress-output.md): per-node / per-LLM-attempt / per-trial heartbeat vocabulary on stderr.

## Stage 7 — Iterate {#stage-7-iterate}

One improvement cycle is `optimize → hypothesize → accept → re-optimize`. The "iterate" stage is the second and subsequent passes of that cycle: an accepted hypothesis changes the strategy code or parameter space, so the previously-best parameters are no longer authoritative — you re-optimize, then re-run `hypothesize` with the new optimize-run id as the rigorous baseline.

The minimum end-to-end loop:

1. Re-optimize after acceptance — the accepted hypothesis ships, so the baseline parameter set is stale:

   ```bash
   strategy-gpt optimize --spec examples/vxx/experiment.yaml
   ```

2. Hypothesize again from the *new* baseline. Grab the new `<opt-id>` from the optimize stdout or `ledger/optimizations.sqlite`:

   ```bash
   strategy-gpt hypothesize spy_atr --baseline-from <new-opt-id>
   ```

3. Audit accumulated decisions across iterations. Bump `--limit` to see the long tail:

   ```bash
   strategy-gpt recent-decisions --root ledger --limit 50
   ```

The decision log is the durable cross-iteration artifact. Every `recent-decisions` row carries the rationale, KB citations, baseline source, and timestamps, so the audit trail spans the full research arc without any external bookkeeping.

### See also

- [Stage 5 — Optimize](#stage-5-optimize): the re-optimize step.
- [Stage 6 — Hypothesize improvements + KB](#stage-6-hypothesize): the re-hypothesize step.

## Stage 8 — Reproduce and debug {#stage-8-reproduce}

Every run pins `(artifact_hash, dataset_manifest, params, seed, runner_version)`. Identical inputs produce a byte-identical `BacktestResult`. The ledger stores enough to replay a run from `(ledger record + cache blobs)` without contacting the upstream provider, which is what the reproducibility guarantee is built on.

Replay a recorded run from the ledger:

```bash
strategy-gpt replay --run-id <ledger-run-id> --ledger-root ledger --gateway-root cache
```

Reconstructs the `BatchSpec` + bars from the ledger and the cache. The upstream provider (yfinance, your CSV directory, …) is *never* contacted. Result is byte-identical to the originally-recorded `BacktestResult`.

For a single optimize trial, use Stage 5's [`optimize replay`](#stage-5-optimize) — same guarantee, scoped to one trial row inside an optimize run:

```bash
strategy-gpt optimize replay <opt_id> --trial 4271 --out result.json
```

**CI reproducibility.** Combine the ledger replay with offline cache mode to assert that a build's reproducibility does not depend on network availability:

```bash
strategy-gpt fetch --provider yfinance --symbol VXX \
  --start 2018-01-01 --end 2026-12-31 \
  --resolution Day --adjustment back_adjusted \
  --mode offline --root cache
strategy-gpt replay --run-id <ledger-run-id> --ledger-root ledger --gateway-root cache
```

`--mode offline` fails fast if any year is missing locally, so a green CI build is a positive proof of cache + ledger sufficiency.

For the curious: `ledger/optimizations.sqlite` is the index of every optimize run; you can attach a SQLite client to it for ad-hoc audit queries, though the schema is treated as internal and is not part of the documented surface.

### See also

- [Explanation — Domain vocabulary](explanation/domain-vocabulary.md): every term used in the rest of the docs, defined once, including the reproducibility primitives.
