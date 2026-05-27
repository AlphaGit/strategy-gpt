!!! note "LLM credentials"

    LLM-driven commands (`author` and `hypothesize`) need either `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` exported in the environment; the rest of the CLI surface (`fetch`, `run`, `optimize`, `replay`, `cache-stats`, `recent-decisions`) does not. The chosen `--model` decides which key is consumed; if neither is set, the command fails fast on stderr with `exit_code=2`.
