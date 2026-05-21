# Reference

Schemas, knobs, and CLI surface. Exhaustive; consult when you need the exact shape of something.

- [Experiment spec](experiment-spec.md) — user-facing experiment envelope (`experiment-spec.yaml` / `.json`).
- [Batch spec](batch-spec.md) — internal engine input shape across the PyO3 boundary.
- [Objective spec](objective-spec.md) — primary/secondary metrics, selection knobs (PBO/DSR/robust), thresholds.
- [`hypothesize` CLI](hypothesize-cli.md) — flags, exit codes, and JSON output shape for `strategy-gpt hypothesize` and the `hypothesis replay` / `hypothesis diff` subcommands.
