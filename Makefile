.PHONY: install lint format types vulture importlint test ci perf

install:
	uv sync

lint:
	uv run ruff check --no-fix src tests
	uv run ruff format --check src tests

format:
	uv run ruff check --fix src tests
	uv run ruff format src tests

types:
	uv run ty check src tests
	uv run basedpyright src tests

vulture:
	uv run vulture

importlint:
	uv run lint-imports

test:
	uv run pytest

# The perf measurements (SP-03 spec §15, SP-04 plan 4c C10): run by hand, never by
# ci. Results go to .perf/sp03.json and .perf/sp04.json.
perf:
	uv run pytest tests/perf -m perf --no-cov -q

# Runs every gate and reports every failure instead of stopping at the first.
ci:
	@fail=0; for target in lint types vulture importlint test; do \
		echo "== make $$target"; $(MAKE) --no-print-directory $$target || fail=1; \
	done; exit $$fail
