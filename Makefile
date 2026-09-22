UV ?= uv
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

.PHONY: help sync env-check
help:
	@printf '%s\n' 'Planning baseline only; product CLI and ci-fast are not implemented.' 'make sync       Synchronize the uv environment' 'make env-check  Check the lock and selected interpreter'
sync:
	cd "$(ROOT)" && $(UV) sync --locked
env-check:
	cd "$(ROOT)" && $(UV) lock --check
	cd "$(ROOT)" && $(UV) run --locked python -c 'import sys, sqlite3; print(sys.version); print("SQLite", sqlite3.sqlite_version)'
