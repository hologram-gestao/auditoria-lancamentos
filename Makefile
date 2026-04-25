# ============================================================
# Makefile — orquestrador do monorepo
# Uso: make <target>
# Requisitos: docker, docker compose, uv, pnpm, node >= 20
# ============================================================

SHELL := /bin/bash
COMPOSE := docker compose -f docker/docker-compose.yml --env-file .env
API := cd apps/api &&
WEB := cd apps/web &&

.DEFAULT_GOAL := help

# ---------- Ajuda ----------
.PHONY: help
help: ## Exibe os targets disponíveis
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------- Setup inicial ----------
.PHONY: setup
setup: ## Instala todas as dependências (api + web)
	@echo "→ Instalando deps do backend (uv)…"
	$(API) uv sync --all-extras
	@echo "→ Instalando deps do frontend (pnpm)…"
	pnpm install
	@echo "→ Pronto. Crie seus .env copiando de .env.example."

.PHONY: env
env: ## Copia .env.example para .env em todos os lugares
	@test -f .env || cp .env.example .env
	@test -f apps/api/.env || cp apps/api/.env.example apps/api/.env
	@test -f apps/web/.env.local || cp apps/web/.env.example apps/web/.env.local
	@echo "→ Arquivos .env criados. Ajuste valores sensíveis antes de rodar."

# ---------- Docker ----------
.PHONY: up
up: ## Sobe todos os serviços (postgres, redis, api, worker, web)
	$(COMPOSE) up -d

.PHONY: up-infra
up-infra: ## Sobe apenas infra (postgres + redis) — útil para dev local fora do container
	$(COMPOSE) up -d postgres redis

.PHONY: down
down: ## Desliga todos os serviços
	$(COMPOSE) down

.PHONY: logs
logs: ## Logs em tempo real de todos os serviços
	$(COMPOSE) logs -f

.PHONY: ps
ps: ## Lista containers rodando
	$(COMPOSE) ps

.PHONY: rebuild
rebuild: ## Reconstrói as imagens Docker
	$(COMPOSE) build --no-cache

# ---------- Dev (sem Docker) ----------
.PHONY: dev-api
dev-api: ## Roda a API em modo dev (hot reload)
	$(API) uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: dev-worker
dev-worker: ## Roda o worker ARQ
	$(API) uv run arq app.workers.arq_worker.WorkerSettings

.PHONY: dev-web
dev-web: ## Roda o frontend em modo dev
	$(WEB) pnpm dev

# ---------- Banco ----------
.PHONY: db-migrate
db-migrate: ## Aplica migrations pendentes
	$(API) uv run alembic upgrade head

.PHONY: db-revision
db-revision: ## Gera nova migration (autogenerate). Uso: make db-revision m="descrição"
	$(API) uv run alembic revision --autogenerate -m "$(m)"

.PHONY: db-downgrade
db-downgrade: ## Reverte 1 migration
	$(API) uv run alembic downgrade -1

.PHONY: db-seed
db-seed: ## Popula o banco com dados iniciais
	$(API) uv run python -m scripts.seed_dev

.PHONY: db-reset
db-reset: ## DROP + CREATE + migrate + seed (APENAS EM DEV)
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER} -c "DROP DATABASE IF EXISTS $${POSTGRES_DB}; CREATE DATABASE $${POSTGRES_DB};"
	$(MAKE) db-migrate
	$(MAKE) db-seed

# ---------- Lint / Type / Test ----------
.PHONY: lint
lint: lint-api lint-web ## Roda lint em api + web

.PHONY: lint-api
lint-api: ## Lint do backend (ruff)
	$(API) uv run ruff check .
	$(API) uv run ruff format --check .

.PHONY: lint-web
lint-web: ## Lint do frontend (eslint + prettier)
	pnpm lint

.PHONY: fmt
fmt: ## Formata todo o código
	$(API) uv run ruff format .
	$(API) uv run ruff check --fix .
	pnpm format

.PHONY: type-check
type-check: type-check-api type-check-web ## Type-check em api + web

.PHONY: type-check-api
type-check-api: ## Mypy strict no backend
	$(API) uv run mypy app

.PHONY: type-check-web
type-check-web: ## TSC strict no frontend
	pnpm type-check

.PHONY: test
test: test-api test-web ## Roda todos os testes

.PHONY: test-api
test-api: ## Testes do backend
	$(API) uv run pytest -v

.PHONY: test-web
test-web: ## Testes do frontend
	pnpm test

.PHONY: e2e
e2e: ## Testes E2E (Playwright)
	pnpm e2e

# ---------- Segurança ----------
.PHONY: audit
audit: ## pip-audit + npm audit
	$(API) uv run pip-audit
	pnpm audit

# ---------- Utilidades ----------
.PHONY: gen-key
gen-key: ## Gera uma chave AES-256 em hex (use para OMIE_ENCRYPTION_KEY / JWT_SECRET)
	@openssl rand -hex 32

.PHONY: clean
clean: ## Remove artefatos de build e cache
	rm -rf apps/api/.pytest_cache apps/api/.mypy_cache apps/api/.ruff_cache apps/api/htmlcov
	rm -rf apps/web/.next apps/web/coverage apps/web/node_modules/.cache
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".turbo" -exec rm -rf {} + 2>/dev/null || true

.PHONY: nuke
nuke: clean ## Clean + remove node_modules e .venv
	rm -rf node_modules apps/web/node_modules packages/*/node_modules apps/api/.venv
