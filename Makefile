UV ?= uv
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
RUN := $(UV) run --locked --offline

.PHONY: help sync env-check lint format typecheck test ci-fast architecture-check governance-check contract-check package-check mvp-live-arxiv
help:
	@printf '%s\n' 'make sync            Install locked dependencies' 'make env-check       Check the lock and CLI package' 'make ci-fast         Verify the implemented offline scope' 'make test            Run implemented offline tests' 'make mvp-live-arxiv  Run the bounded public arXiv -> fake model -> fake email smoke'
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
architecture-check:
	cd "$(ROOT)" && $(RUN) python -m pytest src/libs/kernel/tests/architecture -q
governance-check:
	cd "$(ROOT)" && $(RUN) python -m pytest src/libs/kernel/tests/governance -q
contract-check:
	cd "$(ROOT)" && $(RUN) python -m pytest src/libs/research_workflow/tests/contract -q
package-check:
	cd "$(ROOT)" && $(RUN) python -m pytest src/apps/cli/tests/integration/test_wheel_install.py -q
ci-fast: env-check lint typecheck test
mvp-live-arxiv:
	cd "$(ROOT)" && PAPER_RADAR_LIVE_ARXIV=1 $(RUN) python -m pytest \
		src/apps/cli/tests/integration/test_worker_arxiv_catalog_projection.py \
		-m live_external -k live_arxiv_attention_paper_reaches_fake_email -q
