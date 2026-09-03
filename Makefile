# Sanctum Forensics task runner.
#
# The deployment target is Linux, but development happens on Windows too, and a
# virtualenv puts its executables in different directories on each: Scripts/ on
# Windows, bin/ everywhere else. Probe for the interpreter rather than assuming
# a layout, so `make lint typecheck test` is one command on both. Probing the
# file beats branching on $(OS) because a POSIX-layout venv on a Windows host
# (WSL, Git Bash) still resolves correctly.
#
# Every target runs its tool as `$(PY) -m <tool>`. Invoking the console scripts
# directly would reintroduce the same bin/ vs Scripts/ split this file exists to
# remove, and `-m` guarantees the tool comes from the same interpreter that
# resolves the project's imports.

VENV ?= .venv
PY := $(if $(wildcard $(VENV)/Scripts/python.exe),$(VENV)/Scripts/python.exe,$(VENV)/bin/python)

# Interpreter used only to create the venv, before $(PY) exists. The Windows
# launcher takes `py -3.11`; POSIX installs expose `python3.11` on PATH.
ifeq ($(OS),Windows_NT)
BOOTSTRAP_PY ?= py -3.11
else
BOOTSTRAP_PY ?= python3.11
endif

# Cache removal runs on whatever interpreter is on PATH, not $(PY): `clean`
# deletes the venv, and on Windows a running python.exe cannot delete itself.
HOST_PY ?= python

.PHONY: install test lint typecheck check run docker clean

install:
	$(BOOTSTRAP_PY) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install --constraint constraints.txt -e ".[dev]"

lint:
	$(PY) -m ruff check .

typecheck:
	$(PY) -m mypy --strict core/

# No -q here: pyproject's addopts already sets it, and a second -q suppresses
# the summary line, hiding the pass and skip counts.
test:
	$(PY) -m pytest

# The full gate, in the order a failure is cheapest to read.
check: lint typecheck test

run:
	$(PY) -m uvicorn api.main:create_app --factory --reload

docker:
	docker build -t sanctum-forensics .

clean:
	$(HOST_PY) -c "import pathlib, shutil; \
	[shutil.rmtree(p, ignore_errors=True) for p in ('$(VENV)', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'build', 'dist')]; \
	[shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]; \
	[shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').glob('*.egg-info')]"
