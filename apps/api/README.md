# apps/api — Backend FastAPI

Backend do Sistema de Auditoria de Lançamentos.

## Requisitos

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 16 + Redis 7 (via `docker compose` na raiz)

## Setup

```bash
# A partir da raiz do monorepo
make up-infra       # sobe postgres + redis
cd apps/api
cp .env.example .env
# editar .env com OMIE_ENCRYPTION_KEY e JWT_SECRET reais
uv sync --all-extras
uv run alembic upgrade head
uv run python scripts/seed-dev.py     # (quando S2 estiver concluída)
uv run uvicorn app.main:app --reload
```

Ou usando o Makefile da raiz: `make dev-api`.

## Estrutura

```
app/
├── core/           # config, crypto, security, logging, exceptions
├── db/             # models, session, repositories
├── modules/        # auth, users, clients, reconciliations, anomalies, reports
├── integrations/   # omie, anthropic
├── workers/        # arq tasks
├── cache/          # AsyncCache abstration (memory + redis)
├── utils/          # helpers puros (magic_bytes, decimal, dates)
└── main.py         # FastAPI app factory
```

## Comandos úteis

```bash
uv run ruff check .
uv run ruff format .
uv run mypy app
uv run pytest -v
uv run alembic revision --autogenerate -m "descrição"
```

Veja o [Makefile raiz](../../Makefile) para atalhos.

## Padrões

- **Async em tudo que toca I/O.** `def` síncrono só em funções puras.
- **Type hints em 100 %** do código. Mypy strict no CI.
- **Pydantic v2** para todo DTO. Nunca `Any` em schemas.
- **Nunca logar** credenciais, senhas, JWTs (redactor do structlog mascara).
- **Nunca retornar** hash de senha ou credenciais em respostas.

Veja regras completas em [CLAUDE.md](../../CLAUDE.md) e [PLANO_IMPLEMENTACAO.md §8](../../Docs/PLANO_IMPLEMENTACAO.md#8-padrões-de-código).
