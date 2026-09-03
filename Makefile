# MediKiosk. Plain venv + pip, no uv, no poetry.

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
export PYTHONPATH := .

.PHONY: help setup demo api web test lint fmt fixtures check clean supabase-check demo-local-up demo-local-down demo-local-reset

help:
	@echo "make setup        create the venv and install everything"
	@echo "make demo         run the API and the frontend together (./start.sh)"
	@echo "make test         run the whole test suite"
	@echo "make lint         ruff + mypy + tsc"
	@echo "make check        lint + test  (run this before committing)"

setup:
	python3.12 -m venv $(VENV)
	$(PIP) install -q --upgrade pip setuptools wheel
	$(PIP) install -q -r requirements-dev.txt
	cd frontend && npm install --silent
	@echo "Ready. Now run: make demo"

# One entry point, so the ports cannot drift apart between the two. See start.sh's header
# for why this project does not use 5173/8000.
demo:
	./start.sh

# ---------------------------------------------------------------- demo fallback
# Presentation only, and never automatic. See docker-compose.demo.yml for why it exists.
demo-local-up:
	docker compose -f docker-compose.demo.yml up -d --wait
	DEMO_LOCAL_DB=true $(PY) -m alembic upgrade head
	@echo ""
	@echo "  LOCAL DEMO DATABASE is up and migrated on 127.0.0.1:5433."
	@echo "  Start the stack with:  DEMO_LOCAL_DB=true make demo"
	@echo "  Everything will be labelled LOCAL in the log, in /about and in the UI."

demo-local-down:
	docker compose -f docker-compose.demo.yml down

demo-local-reset:
	docker compose -f docker-compose.demo.yml down -v

supabase-check:
	@$(PY) scripts/check_supabase.py

api:
	$(PY) -m uvicorn app.main:app --reload --port $${API_PORT:-10101}

web:
	cd frontend && VITE_PORT=$${WEB_PORT:-10100} npm run dev

test:
	$(PY) -m pytest tests/ -q

lint:
	$(VENV)/bin/ruff check app tests scripts workers
	$(VENV)/bin/mypy app
	$(PY) scripts/check_no_raw_colours.py
	cd frontend && npx tsc --noEmit

fmt:
	$(VENV)/bin/ruff check --fix app tests scripts
	$(VENV)/bin/ruff format app tests scripts

fixtures:
	$(PY) scripts/make_document_fixtures.py

check: lint test
	@echo ""
	@echo "All checks passed."

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache medikiosk.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
