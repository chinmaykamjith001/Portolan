.PHONY: e2e setup dev backend frontend test lint typecheck record eval

ARGS ?=

setup:
	cd backend && uv sync
	cd frontend && pnpm install

dev:
	$(MAKE) -j2 backend frontend

backend:
	cd backend && uv run portolan serve --reload

frontend:
	cd frontend && pnpm dev

test:
	cd backend && uv run pytest
	cd frontend && pnpm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && pnpm lint

typecheck:
	cd backend && uv run pyright
	cd frontend && pnpm typecheck

# Real LLM calls. Run by the owner only, with their key in .env. Example:
#   make record ARGS="--paper 1706.03762 --steps master"
record:
	cd backend && uv run portolan record $(ARGS)

eval:
	cd backend && uv run portolan eval $(ARGS)

e2e:  ## Playwright end-to-end tests (mock provider, seeded demo paper)
	cd frontend && pnpm e2e
