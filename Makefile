PYTHON ?= python
CONFIG ?= examples/config
RENDER_DIR ?= build/rendered

.PHONY: setup lint test render validate diagrams kind-test security-scan

setup:
	$(PYTHON) -m pip install -c constraints.txt -r requirements-dev.txt -e .

lint:
	ruff format --check .
	ruff check .
	mypy src tests
	yamllint .
	pymarkdown --config .pymarkdown.yml scan README.md docs

test:
	pytest

render:
	airgap-ai-gateway --config $(CONFIG) render --output-dir $(RENDER_DIR)

validate:
	airgap-ai-gateway --config $(CONFIG) discover

diagrams:
	$(PYTHON) scripts/verify_assets.py

kind-test:
	@echo "kind integration tests are not implemented in this scaffold phase; no kubectl command is run."

security-scan:
	scripts/secret-scan.sh --all
