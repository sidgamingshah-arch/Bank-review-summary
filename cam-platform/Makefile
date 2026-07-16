.PHONY: install test stack seed e2e frontend frontend-dev compose-up lint-compose

install:            ## venv + backend deps (+ dev extras)
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:               ## full backend test suite
	.venv/bin/pytest

stack:              ## run all 9 services locally (SQLite, mock LLM)
	.venv/bin/python scripts/run_stack.py

seed:               ## seed masters through the APIs (maker-checker)
	.venv/bin/python scripts/seed_demo.py

e2e:                ## full BRD §9 acceptance walkthrough (starts its own stack)
	.venv/bin/python scripts/e2e_demo.py

frontend:           ## install + production build of the SPA
	cd frontend && npm install && npm run build

frontend-dev:       ## Vite dev server on :5173 (proxies /api -> :8080)
	cd frontend && npm run dev

compose-up:         ## containerised stack: PostgreSQL + 9 services
	docker compose up --build

lint-compose:       ## validate compose file
	docker compose config -q
