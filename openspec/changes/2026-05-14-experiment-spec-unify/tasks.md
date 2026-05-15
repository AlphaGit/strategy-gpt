## 1. Schema

- [ ] 1.1 Define `experiment-spec` v1 fields in a JSON Schema document under `crates/experiment-spec/schema.json` (or equivalent shared location).
- [ ] 1.2 Document the schema in `docs/experiment-spec.md`: every field, every default, the `bars` polymorphism, `parallelism: auto` semantics, and the migration mapping from `batch.json`.

## 2. Python loader

- [ ] 2.1 Add `python/strategy_gpt/experiment_spec.py` with pydantic models: `ExperimentSpec`, `BarsRef` (`DatasetRef | RequestRef`), `EngineConfig`, `RunConfig`, `Caps`.
- [ ] 2.2 Validate that exactly one of `dataset` / `request` is set in `bars`.
- [ ] 2.3 Resolve `parallelism: "auto"` at load time to `max(1, len(os.sched_getaffinity(0)) - 1)` on linux, `max(1, os.cpu_count() - 1)` elsewhere.
- [ ] 2.4 Reject the legacy `batch.json` shape with an explicit error message ("legacy `batch.json` format detected; migrate to experiment-spec.yaml — see docs/experiment-spec.md").
- [ ] 2.5 Unit tests covering: valid spec round-trip, both bars variants, auto-parallelism resolution on each OS path, legacy rejection.

## 3. Gateway integration

- [ ] 3.1 When `bars.request` is provided, call `Gateway.fetch(request, prefer_cache)` before submitting to the engine; record the resulting manifest hash back into the resolved spec for the ledger.
- [ ] 3.2 When `bars.dataset` is provided, look up the manifest in the cache; error with a structured "dataset not cached" message if missing.

## 4. Engine config cleanup

- [ ] 4.1 Remove `slippage_bps` from the `EngineConfig` pydantic model and from the example.
- [ ] 4.2 Confirm the engine's Rust-side schema still accepts the field for backward-compatible deserialization (modes still use it); add a release note.

## 5. CLI migration

- [ ] 5.1 Rewrite `strategy-gpt run` to accept `--spec experiment.yaml` only; remove the per-piece flags.
- [ ] 5.2 Keep `--wait`, `--poll-interval-secs`, and ledger output behavior unchanged.
- [ ] 5.3 Update `python/strategy_gpt/cli.py`'s docstring and `--help` text.

## 6. Reference example

- [ ] 6.1 Delete `examples/vxx/batch.json`.
- [ ] 6.2 Add `examples/vxx/experiment.yaml` with the same single-run definition.
- [ ] 6.3 Update `examples/vxx/optimize.py` (or delete pending optimize-command change) so it stops referencing `batch.json`.

## 7. Docs

- [ ] 7.1 Update `docs/cli-cookbook.md` `run` recipe to the new flag surface.
- [ ] 7.2 Update `CLAUDE.md` Domain vocabulary entry for `BatchSpec` → reference the new experiment-spec as the *user-facing* envelope, BatchSpec as the *internal* engine input.
