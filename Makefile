PYTHON ?= python
CONFIG ?= examples/config
RENDER_DIR ?= build/rendered
AIRGAP_LOCK ?= airgap/sources.lock.yaml
AIRGAP_COMPAT ?= baseline-v1.3.1
AIRGAP_DIST ?= dist/airgap-demo
AIRGAP_REGISTRY ?= registry.example.internal:5000

.PHONY: setup lint test render validate diagrams kind-test security-scan airgap-demo

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
	airgap-ai-gateway --config $(CONFIG) verify --lock-file $(AIRGAP_LOCK) --compatibility-set $(AIRGAP_COMPAT) --registry $(AIRGAP_REGISTRY)
	$(PYTHON) scripts/validate_manifests.py

diagrams:
	$(PYTHON) scripts/verify_assets.py

kind-test:
	@echo "kind integration tests are not implemented in this scaffold phase; no kubectl command is run."

security-scan:
	scripts/secret-scan.sh --all

airgap-demo:
	airgap-ai-gateway bundle build --lock-file $(AIRGAP_LOCK) --compatibility-set $(AIRGAP_COMPAT) --registry $(AIRGAP_REGISTRY) --dist-dir $(AIRGAP_DIST) --metadata-hook sbom --metadata-hook signature
	airgap-ai-gateway bundle verify --lock-file $(AIRGAP_LOCK) --compatibility-set $(AIRGAP_COMPAT) --registry $(AIRGAP_REGISTRY) --bundle-dir $(AIRGAP_DIST)/$(AIRGAP_COMPAT)
	airgap-ai-gateway --config $(CONFIG) registry promote --lock-file $(AIRGAP_LOCK) --compatibility-set $(AIRGAP_COMPAT) --registry $(AIRGAP_REGISTRY) --output-file $(AIRGAP_DIST)/promotion-plan.json
	airgap-ai-gateway --config $(CONFIG) verify --lock-file $(AIRGAP_LOCK) --compatibility-set $(AIRGAP_COMPAT) --registry $(AIRGAP_REGISTRY)
