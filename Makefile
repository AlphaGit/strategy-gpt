# strategy-gpt — top-level developer commands.
# `make lint` runs the same suite as `pre-commit run --all-files`. CI
# (`.github/workflows/ci.yml`) invokes `make lint` + `make test`; this
# Makefile is the canonical entry point and CI YAML should not duplicate the
# rule selection or tool invocations.

SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help lint fmt lint-rust lint-python lint-docs fmt-rust fmt-python test test-rust test-python docs docs-serve docs-build

help: ## List available targets.
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  %-16s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

lint: lint-rust lint-python lint-docs ## Run all lint gates (Rust + Python + docs).

fmt: fmt-rust fmt-python ## Run all formatters (writes changes).

lint-rust: ## cargo fmt --check + cargo clippy --workspace --all-targets -D warnings
	cd crates && cargo fmt --all -- --check
	cd crates && cargo clippy --workspace --all-targets -- -D warnings

lint-python: ## ruff check + ruff format --check + mypy --strict
	cd python && ruff check --config=pyproject.toml .
	cd python && ruff format --check --config=pyproject.toml .
	cd python && mypy --config-file=pyproject.toml strategy_gpt

lint-docs: ## mkdocs build --strict (skipped if mkdocs is not installed)
	@if command -v mkdocs >/dev/null 2>&1; then \
		mkdocs build --strict; \
	else \
		echo "skipping lint-docs: mkdocs not installed (pip install -r requirements-docs.txt)"; \
	fi

docs: docs-serve ## Alias for docs-serve.

docs-serve: ## Serve the docs site locally at http://127.0.0.1:8000
	mkdocs serve

docs-build: ## mkdocs build --strict
	mkdocs build --strict

fmt-rust: ## cargo fmt
	cd crates && cargo fmt --all

fmt-python: ## ruff format
	cd python && ruff format --config=pyproject.toml .

test: test-rust test-python ## Run all tests.

test-rust: ## cargo test --workspace
	cd crates && cargo test --workspace

test-python: ## pytest
	cd python && pytest
