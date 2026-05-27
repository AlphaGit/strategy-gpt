!!! note "Progress output"

    Every long-running command (`fetch`, `run --wait`, `optimize`, `hypothesize`, `author`, `tester`, smoke) accepts `--progress {auto,plain,json,off}`. `auto` (the default) picks a rich phase tree on a TTY and falls back to JSONL on a pipe; `json` forces line-delimited JSON for ingestion; `off` silences progress entirely. See [Read progress output](how-to/read-progress-output.md) for the event vocabulary and filter recipes.
