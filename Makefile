SHELL := /bin/sh
.DEFAULT_GOAL := help

PYTHON ?= python3
VENV_PYTHON := .venv/bin/python
PORT_ARG := $(if $(PORT),--port "$(PORT)",)

.PHONY: help setup setup-dev start dev check check-python check-frontend test audit

help:
	@printf '%s\n' \
	  'make setup      Install runtime and create private .env if missing' \
	  'make setup-dev  Also install locked verification tools' \
	  'make start      Start frontend + API; local auto-port, HTTPS fixed port' \
	  'make dev        Start frontend + API with Python reload' \
	  'make check      Python + frontend nonvisual source checks' \
	  'make test       Run isolated SQLite and HTTP tests' \
	  'make audit      Check installed dependencies for known advisories' \
	  'Override preferred port: make start PORT=8001. Stop with Ctrl+C.'

setup:
	@if [ ! -e .venv ] && [ ! -L .venv ]; then \
	  "$(PYTHON)" -c 'import sys; assert sys.version_info[:2] >= (3, 12), "Use Python 3.12 or newer"' && \
	  "$(PYTHON)" -m venv .venv; \
	elif ! "$(VENV_PYTHON)" -c 'import sys; assert sys.version_info[:2] >= (3, 12)' >/dev/null 2>&1; then \
	  printf '%s\n' 'Existing .venv is broken or is older than Python 3.12. Move it aside, then rerun make setup.' >&2; \
	  exit 1; \
	fi
	"$(VENV_PYTHON)" -m pip install -r requirements.lock
	"$(VENV_PYTHON)" scripts/setup_config.py

setup-dev: setup
	"$(VENV_PYTHON)" -m pip install -r requirements-dev.lock

start:
	"$(VENV_PYTHON)" scripts/serve.py $(PORT_ARG)

dev:
	"$(VENV_PYTHON)" scripts/serve.py --reload $(PORT_ARG)

check: check-python check-frontend

check-python:
	"$(VENV_PYTHON)" -m pip check
	"$(VENV_PYTHON)" -m ruff check app scripts tests
	"$(VENV_PYTHON)" -m ruff format --check app scripts tests
	"$(VENV_PYTHON)" -m mypy

check-frontend:
	npx --yes --package eslint@10.15.0 eslint static/js
	npx --yes --package stylelint@18.1.0 stylelint static/app.css static/tokens.css
	npx --yes --package prettier@3.10.1 prettier --check static/index.html static/tokens.css static/app.css static/js tests/frontend

test:
	"$(VENV_PYTHON)" -m pytest
	node --test tests/frontend/*.test.mjs

audit:
	"$(VENV_PYTHON)" -m pip_audit
