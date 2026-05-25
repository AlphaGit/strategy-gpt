# Read progress output

Strategy-GPT's long-running commands (`optimize`, `run --wait`, `hypothesize`,
`tester`, `smoke`, `fetch`) emit progress events to **stderr** through a
typed event vocabulary. The `--progress` flag picks the renderer.

## The four modes

| Mode | When to use | Output |
|------|-------------|--------|
| `auto` (default) | Interactive terminal — *or* CI / pipe (auto-degrades) | rich live phase tree on TTY, JSONL on pipe |
| `plain` | `tee` to a file, log-friendly terminal | one human-readable line per `phase_begin`/`phase_end`, heartbeats throttled |
| `json` | Machine consumers, CI assertions | one JSON line per event, every event verbatim |
| `off` | Scripts that must not interleave any progress | no progress sink installed; structlog/tracing flow unchanged |

`stdout` is reserved for command results (JSON payloads, handles). Progress
never touches stdout.

## Example output

### `--progress=auto` on a TTY

```
progress
├─ ▶ optimize 12.4s  best=1.41
│   ├─ ✓ optimize.search ok 8.2s
│   ├─ ▶ optimize.oos [ 47%] 14/30 4.1s  {score=1.38, best=1.41}
```

Refresh rate is capped at 10 frames/second. Phases with a known `total`
render as bars; unbounded phases (CMA-ES etc.) render as spinners with
elapsed seconds.

### `--progress=json`

```jsonl
{"kind":"phase_begin","path":"optimize","emitted_at":0.0,"started_at":0.0,"total":3,"msg":"opt_id=abc123"}
{"kind":"phase_begin","path":"optimize.search","emitted_at":0.0,"started_at":0.0,"total":3}
{"kind":"phase_progress","path":"optimize.fold_0.train_fold_0","emitted_at":0.21,"current":1,"msg":"trial #0","metrics":{"score":1.32,"best":1.32}}
{"kind":"heartbeat","path":"optimize.oos","emitted_at":11.0,"wall_secs":11.0,"since_last_event_secs":5.4}
{"kind":"phase_end","path":"optimize","emitted_at":47.3,"status":"ok","wall_secs":47.3}
```

Useful filters:

```bash
# All events for a specific phase
strategy-gpt optimize --progress=json --spec ... 2> >(grep '"path":"optimize.oos"')

# Heartbeats only (proves the process is alive)
strategy-gpt run --wait --progress=json ... 2> >(grep '"kind":"heartbeat"')

# Path prefix
strategy-gpt hypothesize foo --progress=json 2> >(grep '"path":"hypothesize.')
```

### `--progress=plain`

```
[begin]   optimize (total=3) opt_id=abc123
[begin]   optimize.search (total=3)
[end]     optimize.search status=ok wall=8.20s
[begin]   optimize.oos (total=3)
[hb]      optimize.oos wall=5.1s idle=5.0s
[end]     optimize.oos status=ok wall=11.40s
[end]     optimize status=ok wall=19.85s
```

Heartbeats are throttled to one per 30 s per path. No ANSI escapes.

## Event vocabulary

Four kinds, all keyed by a dotted `path`:

| Kind | Required | Optional |
|------|----------|----------|
| `phase_begin` | `path`, `started_at` | `total`, `unit`, `msg` |
| `phase_progress` | `path`, `current` | `total`, `msg`, `metrics` |
| `phase_end` | `path`, `status` (`ok`/`fail`/`skip`/`cancelled`), `wall_secs` | `msg`, `metrics` |
| `heartbeat` | `path`, `wall_secs`, `since_last_event_secs` | `msg` |

Path conventions:

- `optimize.*` — parameter optimizer (`optimize.search`, `optimize.fold_N.train_fold_N`, `optimize.oos`)
- `hypothesize.*` — hypothesis loop (one per LangGraph node)
- `tester.*` — tester sub-phases (`tester.lint`, `tester.build`, `tester.smoke`, `tester.full_batch`)
- `smoke.*` — smoke run
- `fetch.<provider>.download` — data gateway fetches
- `worker.batch_<n>.run_<m>.*` — Rust worker events bridged from stderr

## Cancellation

`Ctrl-C` (SIGINT) drains the progress bus before tearing down workers:
every still-open phase receives a `phase_end(status="cancelled")` event,
the active sink renders its final state, then the orchestrator signals
its workers. JSONL consumers will see a `phase_end(status="cancelled")`
line for every open phase before the process exits.

## Reproducibility

Progress events are a **UX channel**. They are never written to the
experiment ledger; the byte-identity smoke fixture continues to compare
results on stdout, and identical inputs produce identical ledger output
whether `--progress` is `auto` or `off`.
