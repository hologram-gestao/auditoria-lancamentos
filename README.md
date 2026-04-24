# Sistema de Auditoria de Lançamentos — Hologram

SaaS interno da Hologram Gestão para automatizar a conciliação bancária de clientes BPO, cruzando extratos/faturas com o ERP Omie via IA (Claude) + lógica determinística.

## Documentação

| Documento | O que é |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Primer operacional — regras invioláveis, stack, padrões. **Leia primeiro.** |
| [Docs/PLANO_IMPLEMENTACAO.md](Docs/PLANO_IMPLEMENTACAO.md) | Plano de implementação em 18 sessões (S0 – S18). |
| [Docs/documentation/](Docs/documentation/) | Especificação funcional (19 arquivos, fonte da verdade). |

## Stack

- **Backend:** Python 3.12 + FastAPI + SQLAlchemy async + Alembic + ARQ + Redis + PostgreSQL 16
- **Frontend:** Next.js 14 + TypeScript strict + TailwindCSS + shadcn/ui + TanStack Query
- **Gerenciamento:** monorepo com `uv` (Python) e `pnpm` (Node)
- **Infra:** Docker Compose (dev), GitHub Actions (CI/CD)

## Pré-requisitos

- **Docker** + **Docker Compose** v2
- **Node 20 LTS** (use `nvm use`)
- **pnpm 9+** (`npm install -g pnpm`)
- **uv** (`curl -LsSf https://astral.sh/uv/install.sh | sh` ou via PowerShell)
- **Python 3.12+** (uv instala se faltar)
- **make** (Git Bash/WSL no Windows)

## Setup em 5 comandos

```bash
git clone <repo-url> auditoria-lancamentos
cd auditoria-lancamentos
make env            # cria .env a partir dos .env.example
make setup          # instala deps (uv + pnpm)
make up             # sobe postgres + redis + api + worker + web via Docker
```

Acessos:
- **Web:** http://localhost:3000
- **API:** http://localhost:8000 (docs em `/docs`)
- **Postgres:** localhost:5432
- **Redis:** localhost:6379

Para dev local **sem** containerizar o código (só a infra):

```bash
make up-infra       # só postgres + redis
make dev-api        # em um terminal
make dev-worker     # em outro
make dev-web        # em outro
```

## Comandos úteis

```bash
make help           # lista tudo que o Makefile entende
make db-migrate     # aplica migrations
make db-seed        # popula dados iniciais
make lint           # ruff + eslint
make type-check     # mypy + tsc
make test           # pytest + vitest
make e2e            # playwright
make audit          # pip-audit + npm audit
make gen-key        # gera chave AES-256 em hex (use para .env)
```

## Estrutura

```
.
├── apps/
│   ├── api/            # Backend FastAPI (uv workspace)
│   └── web/            # Frontend Next.js (pnpm workspace)
├── packages/
│   └── shared-types/   # Tipos TS gerados do OpenAPI
├── docker/             # docker-compose.yml + Dockerfiles
├── scripts/            # seeds, key rotation, utilitários
├── Docs/               # plano + especificação funcional
├── .github/workflows/  # CI (lint + type + test com path filters)
└── CLAUDE.md           # primer para agentes de IA
```

## Princípios de desenvolvimento

- **Segurança é inegociável.** Nenhum atalho em criptografia, auth ou RBAC. Ver [CLAUDE.md §3-5](CLAUDE.md).
- **IA só extrai, nunca decide match.** Matching é 100 % determinístico.
- **Type hints e strict mode em 100 %** do código.
- **Async puro** no backend — nada de bloquear event loop.
- **Reuso obrigatório.** Qualquer código duplicado vira função/módulo compartilhado.

## Como contribuir

1. Branch a partir de `main`: `feat/S<n>-descrição-curta`.
2. Commits no padrão Conventional (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).
3. PR com review obrigatório.
4. CI verde (lint + type + test + audit).
5. Atualize [CLAUDE.md](CLAUDE.md) se a mudança afeta padrões/decisões globais.

## Licença

Projeto privado da Hologram Gestão. Todos os direitos reservados.
