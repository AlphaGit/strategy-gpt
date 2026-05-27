!!! note "Root flags"

    Four root flags pin where the CLI reads and writes filesystem state: `--cache-root` (gateway cache, default `cache`), `--gateway-root` (alias used by `run` / `optimize` / `replay` to resolve bars, default `cache`), `--ledger-root` (run history + per-strategy decisions, default `ledger`), `--work-root` (build-pipeline scratch, default `cache/build-work`). The four roots are independent; pointing them at a fresh directory tree gives you an isolated workspace with no implicit shared state.
