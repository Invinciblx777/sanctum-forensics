VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install test lint typecheck run docker clean

install:
	python3.11 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install --constraint constraints.txt -e ".[dev]"

lint:
	$(VENV)/bin/ruff check .

typecheck:
	$(VENV)/bin/mypy --strict core/

test:
	$(VENV)/bin/pytest -q

run:
	$(VENV)/bin/uvicorn api.main:create_app --factory --reload

docker:
	docker build -t sanctum-forensics .

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
