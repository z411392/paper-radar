UV ?= uv
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
RUN := $(UV) run --locked --offline

.PHONY: help sync env-check lint format typecheck test ci-fast architecture-check governance-check contract-check package-check mvp-live-arxiv
help:
	@printf '%s\n' 'make sync            Install locked dependencies' 'make env-check       Check the lock and CLI package' 'make ci-fast         Verify executable MVP behavior (formatting debt is advisory)' 'make test            Run implemented offline tests' 'make mvp-live-arxiv  Run the bounded public arXiv -> fake model freshness smoke'
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
	cd "$(ROOT)" && $(RUN) python -m pytest \
		src/apps/cli/tests/e2e/test_cli_package.py \
		src/apps/cli/tests/acceptance/test_t01_local_workspace.py \
		src/apps/cli/tests/acceptance/test_mvp_arxiv_profile.py \
		src/libs/research_workflow/tests/contract/test_resumable_harvest.py \
		src/libs/research_workflow/tests/unit/test_project_source_catalog_unit.py \
		src/libs/delivery/tests/integration/test_scheduled_digest_pipeline.py \
		src/libs/delivery/tests/unit/test_digest_preview.py \
		-m 'not live_external' -q
architecture-check:
	cd "$(ROOT)" && $(RUN) python -m pytest src/libs/kernel/tests/architecture -q
governance-check:
	cd "$(ROOT)" && $(RUN) python -m pytest src/libs/kernel/tests/governance -q
contract-check:
	cd "$(ROOT)" && $(RUN) python -m pytest src/libs/research_workflow/tests/contract -q
package-check:
	cd "$(ROOT)" && $(RUN) python -m pytest src/apps/cli/tests/integration/test_wheel_install.py -q
ci-fast: env-check test package-check
mvp-live-arxiv:
	cd "$(ROOT)" && PAPER_RADAR_LIVE_ARXIV=1 $(RUN) python -m pytest \
		src/apps/cli/tests/integration/test_worker_arxiv_catalog_projection.py \
		-m live_external -k live_arxiv_attention_paper_becomes_late_discovery_summary -q
