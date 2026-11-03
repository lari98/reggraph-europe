# Developer shortcuts. `make check` is what CI runs.
PY ?= python

.PHONY: install lint format types test security audit manifest check run

install:            ## install the package with dev tools
	$(PY) -m pip install -e ".[dev]"

lint:               ## style + bug-pattern + security-pattern rules (ruff)
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format:             ## apply the formatter
	$(PY) -m ruff format .

types:              ## strict static typing (mypy)
	$(PY) -m mypy

test:               ## unit + integration + security tests with coverage gate
	$(PY) -m pytest

security:           ## static security scan (bandit)
	$(PY) -m bandit -q -r src -c pyproject.toml

audit:              ## known-vulnerability scan of dependencies
	$(PY) -m pip_audit

manifest:           ## every module version matches its files
	$(PY) tools/module_manifest.py check

check: lint types test security manifest   ## everything except the network-bound audit

run:                ## start the API locally (needs REGGRAPH_TOKEN_KEYS)
	$(PY) -m reggraph.cli serve
