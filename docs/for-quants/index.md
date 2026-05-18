# For quants

Reading path for strategy authors and researchers. Read top-to-bottom on your first pass through the docs.

## Orient

1. [Domain vocabulary](../explanation/domain-vocabulary.md) — every term used in the rest of the docs, defined once.
2. [Architecture (skim)](../explanation/architecture.md) — what runs where; you do not need the trust-boundary detail for day-to-day work.

## Operate

3. [CLI cookbook](../how-to/cli-cookbook.md) — recipe-style commands for the workflows you'll run most often.
4. [Experiment spec reference](../reference/experiment-spec.md) — the YAML/JSON you'll author per experiment.
5. [Objective spec & selection knobs](../reference/objective-spec.md) — your strategy's `objective.yaml` and the optimizer's knobs.

## Reason about results

6. [Overfitting & selection](../explanation/overfitting-and-selection.md) — PBO, DSR, and the robust score, with limitations.
7. [Interpret a PBO rejection](../how-to/interpret-pbo-rejection.md) — what to do when the selection layer rejects a run.

## Verify the methodology

8. [Bibliography](../explanation/bibliography.md) — every citation, every anchor, with DOI / arXiv links.
