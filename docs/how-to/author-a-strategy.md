# Author a strategy

`strategy-gpt author` is the platform's root primitive for creating a new strategy crate. It drives an interactive LLM dialog to elicit your intent, then emits a working strategy crate that compiles and passes a smoke backtest.

## When to use this

- You have an idea ("trend-follow SPY with ATR stops, daily bars") and want a working strategy crate on disk in minutes.
- You want to iterate on an existing strategy without hand-editing source.
- You want to feed a strategy into the hypothesize → optimize loop without first hand-writing one.

If you want the longer hand-authored path that exercises the trait surface directly, see [Hand-authoring a strategy](../explanation/hand-authoring-a-strategy.md).

## Prerequisites

- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in the environment.
- The workspace built once (`cd crates && cargo check --workspace`).
- A Python install with the orchestrator (`cd python && pip install -e '.[dev]' && maturin develop -m ../crates/py-bindings/Cargo.toml`).

## Invoke author with a seed

```bash
strategy-gpt author "trend-follow SPY with ATR stops, daily bars"
```

The LLM opens with the seed and asks one clarifying question at a time. Typical questions: instrument scope, holding-period range, stop construction details, smoke fixture window. When the LLM has enough information it commits to an intent by emitting a `# AuthorIntent` YAML block.

## Invoke author with no seed

```bash
strategy-gpt author
```

The first dialog turn asks what you want to author. The rest of the flow is identical.

## What lands on disk

A successful run produces, in `crates/<name>-strategy/`:

```
src/lib.rs       # strategy source implementing the sealed Strategy trait
Cargo.toml       # manifest (deps within the build-pipeline whitelist)
smoke.toml       # the fixture (symbol, resolution, range, provider)
intent.toml      # round-trip-serializable AuthorIntent record
```

When invoked with `--verify=batch`, a `experiment.yaml` is also persisted.

## Edit an existing strategy

Plain `author` is the entry point — there is no `--edit` flag. When the dialog proposes a name that collides with an existing crate, the LLM asks whether you want to edit it or pick a different name. On `edit`, the existing `intent.toml`, `src/lib.rs`, `Cargo.toml`, and `smoke.toml` are loaded into context and subsequent emissions are framed as modifications.

## Verify against the full walk-forward batch

```bash
strategy-gpt author "vol-target SPY" --verify=batch
```

After the smoke run, the engine runs the full batch declared in the emitted `experiment.yaml`. A failed fold pops control back to the dialog; the crate stays on disk for inspection.

## Repair budget

Two budgets govern the emit / build / smoke loop:

```bash
strategy-gpt author "..." --k-repair-emit=2 --k-repair-build=2
```

`k_repair=2` means three total attempts per stage (1 initial + 2 repairs). When a budget is exhausted, control returns to the dialog; you can expand the smoke window, swap mechanism, or accept the failure.

## Follow-up commands

```bash
strategy-gpt hypothesize <name>   # run the hypothesis loop on the authored strategy
strategy-gpt optimize --spec <path-to-experiment.yaml>  # tune params
```

## Limitations

- Author has no falsification, no ledger row, no verdict. Success means "the crate compiles and smoke passes." For stronger bars use `--verify=batch` or follow with `hypothesize`.
- Crates outside the build-pipeline whitelist are rejected hard. If a genuinely-needed crate is missing, an operator adds it to `crates/build-pipeline/whitelist.toml` out-of-band before re-running author.
