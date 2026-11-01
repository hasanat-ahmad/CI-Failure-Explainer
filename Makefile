PYTHON ?= python3
SCRIPTS := skills/ci-failure-explainer/scripts

.PHONY: help test eval lint validate fixtures check package clean

help:            ## list targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

test:            ## run the unit and integration tests
	$(PYTHON) -m unittest discover tests

eval:            ## run the fixture eval (fails below 9/10)
	$(PYTHON) tests/run_eval.py

validate:        ## lint the known-pattern library
	$(PYTHON) $(SCRIPTS)/match_patterns.py --validate

lint:            ## ruff lint (dev-only tool; the skill itself has no dependencies)
	ruff check .

fixtures:        ## regenerate synthetic fixtures and refresh tests/EVAL.md
	$(PYTHON) tests/fixtures/generate_fixtures.py
	$(PYTHON) tests/run_eval.py --write

check: validate test eval   ## everything CI runs (except lint)

package:         ## build dist/ci-failure-explainer.zip (for claude.ai upload / releases)
	@mkdir -p dist
	@rm -f dist/ci-failure-explainer.zip
	cd skills && zip -qr ../dist/ci-failure-explainer.zip ci-failure-explainer -x '*/__pycache__/*'
	@echo "dist/ci-failure-explainer.zip"

clean:           ## remove caches and build output
	rm -rf dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
