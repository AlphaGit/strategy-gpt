!!! note "Cross-cutting reminders"

    **Progress output.** Every long-running command (`fetch`, `run --wait`, `optimize`, `hypothesize`, `author`, `tester`, smoke) accepts `--progress {auto,plain,json,off}`. `auto` (the default) picks a rich phase tree on a TTY and falls back to JSONL on a pipe. See [Read progress output](how-to/read-progress-output.md) for the event vocabulary and filter recipes.

    **Env vars.** LLM-driven commands (`author`, `hypothesize`) need `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (whichever the chosen model needs). `RUSTC_WRAPPER=sccache` is recommended for fast Rust rebuilds across `author` and `hypothesize`'s build pipeline.

    **Root flags.** `--cache-root` (gateway cache, default `cache`), `--ledger-root` (run history, default `ledger`), `--gateway-root` (gateway cache alias used by some subcommands, default `cache`), `--work-root` (build scratch, default `cache/build-work`). All four are independent and persist nothing implicitly; an isolated workspace is just a fresh set of roots.
