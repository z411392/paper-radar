UV ?= uv
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
RUN := $(UV) run --locked --offline

.PHONY: help sync env-check lint format typecheck test
help:
	@printf '%s\n' 'make sync       Install locked dependencies' 'make env-check  Check the lock and CLI package' 'make test       Run implemented offline tests'
sync:
	cd "$(ROOT)" && $(UV) sync --locked
env-check:
	cd "$(ROOT)" && $(UV) lock --check --offline
	cd "$(ROOT)" && $(RUN) python -I -m apps.cli version
lint:
	cd "$(ROOT)" && $(RUN) ruff check src
	cd "$(ROOT)" && $(RUN) ruff format --check src
format:
	cd "$(ROOT)" && $(RUN) ruff format src
typecheck:
	cd "$(ROOT)" && $(RUN) pyright
test:
	cd "$(ROOT)" && $(RUN) python -m pytest src -m 'not live_external' -q
