# strategy-gpt — top-level developer commands.
# `make lint` runs the same suite as `pre-commit run --all-files`. CI
# (`.github/workflows/ci.yml`) invokes `make lint` + `make test`; this
# Makefile is the canonical entry point and CI YAML should not duplicate the
# rule selection or tool invocations.

SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help lint fmt lint-rust lint-python fmt-rust fmt-python test test-rust test-python

help: ## List available targets.
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  %-16s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

lint: lint-rust lint-python ## Run all lint gates (Rust + Python).

fmt: fmt-rust fmt-python ## Run all formatters (writes changes).

lint-rust: ## cargo fmt --check + cargo clippy --workspace --all-targets -D warnings
	cd crates && cargo fmt --all -- --check
	cd crates && cargo clippy --workspace --all-targets -- -D warnings

lint-python: ## ruff check + ruff format --check + mypy --strict
	cd python && ruff check --config=pyproject.toml .
	cd python && ruff format --check --config=pyproject.toml .
	cd python && mypy --config-file=pyproject.toml strategy_gpt

fmt-rust: ## cargo fmt
	cd crates && cargo fmt --all

fmt-python: ## ruff format
	cd python && ruff format --config=pyproject.toml .

test: test-rust test-python ## Run all tests.

test-rust: ## cargo test --workspace
	cd crates && cargo test --workspace

test-python: ## pytest
	cd python && pytest
