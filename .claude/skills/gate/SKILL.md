---
name: gate
description: >
  Rode o portão de qualidade local IGUAL ao CI antes de afirmar que uma mudança
  funciona. Gatilhos literais: "antes de commitar", "roda o gate", "roda os testes",
  "o CI vai passar?", "está funcionando?", fechar uma task de código — e SEMPRE que
  for declarar uma entrega pronta (CLAUDE.md §6.10 proíbe "deve passar": cite output).
---

# /gate — o portão de qualidade local, igual ao CI

Espelha o `.github/workflows/ci.yml` (jobs `api`, `web` e `web_a11y`). Existe para
matar duas coisas: o "deve passar" sem evidência, e o CI vermelho por algo que dava
para ver localmente.

**Regra de ouro: os DOIS lados rodam sempre.** O `paths-filter` do job `changes`
decide o que executa no CI, mas o CLAUDE.md §7 é explícito: não se esconde regressão
atrás de filtro de path. Mudou só backend? Roda web também. E vice-versa.

## 0. Pré-voo: Docker (ANTES de tudo, não quando falhar)

```bash
docker ps
```

- **Funcionou** → gate completo possível (testcontainers sobe o Postgres da integração).
- **Falhou** ("could not be found in this WSL 2 distro", socket negado, daemon parado)
  → peça para ligar o Docker Desktop (integração WSL) **agora, no início**. Se seguir
  sem ele, os testes de integração NÃO rodam e o veredito final é obrigatoriamente
  **"gate PARCIAL — integração não coberta"**, nunca "verde".
- **Sandbox que bloqueia o socket do Docker** (agente): suba o Postgres por fora e
  aponte `TEST_DATABASE_URL` — o `apps/api/tests/conftest.py` usa a URL e não sobe
  container nenhum:

```bash
docker run -d --rm --name gate-pg -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test \
  -e POSTGRES_DB=test -p 15432:5432 postgres:16-alpine
export TEST_DATABASE_URL='postgresql+psycopg://test:test@localhost:15432/test'
```

## 1. Backend — job `api`, em `apps/api`

As env vars são as MESMAS chaves fake do `ci.yml` (não são segredo). **Nunca leia o
`apps/api/.env` real** (CLAUDE.md §3.13) — exporte estas:

```bash
cd apps/api
export DATABASE_URL='postgresql+psycopg://auditoria:ci_password@localhost:5432/auditoria_test'
export ENVIRONMENT=development
export LOG_LEVEL=warning
export OMIE_ENCRYPTION_KEY='0000000000000000000000000000000000000000000000000000000000000000'
export JWT_SECRET='1111111111111111111111111111111111111111111111111111111111111111'
export SEARCH_BLIND_INDEX_KEY='2222222222222222222222222222222222222222222222222222222222222222'
export ANTHROPIC_API_KEY='sk-ant-ci-fake'
```

Na ordem do CI (o CI faz `uv sync --all-extras`; localmente o `--extra dev` supre as
dependências de teste):

```bash
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev mypy app
uv run --extra dev pytest -v --cov=app --cov-report=term-missing
```

(`pip-audit` roda no CI com `continue-on-error` — não bloqueia; não precisa local.)

### Interpretação do pytest

- **`--cov` muda o que roda.** O comando rápido do §9 (`pytest -q --no-cov`) NÃO é o
  do CI. Para reproduzir falha de CI, use o comando com `--cov` acima — coverage
  altera a coleta e já escondeu diferença de resultado.
- **3 falhas ambientais conhecidas ≠ regressão.** Estes 3 quebram SOMENTE quando o
  `apps/api/.env` local define canal de alerta (leem o `Settings` real; no CI não há
  `.env` e passam):
  - `tests/unit/test_alerting.py::TestVerifyAlertConfig::test_prod_without_channel_raises`
  - `tests/unit/test_alerting.py::TestVerifyAlertConfig::test_email_needs_smtp_host_to_count`
  - `tests/unit/test_alerting.py::TestSendAlert::test_no_channel_returns_none`

  Exatamente estes 3 e nada além: qualquer OUTRA falha em `test_alerting.py` é
  regressão real — a lista não é desculpa para ignorar o arquivo.
- **Sem Docker e sem `TEST_DATABASE_URL`**, a suíte de integração falha na subida do
  container — isso não é regressão de código, é o gate parcial do passo 0. Rode ao
  menos `uv run --extra dev pytest tests/unit -q --no-cov` e reporte como PARCIAL.

## 2. Frontend — job `web`, na RAIZ do repo

```bash
pnpm lint:web
pnpm type-check:web
pnpm test:web
```

(`pnpm audit --audit-level=high` roda no CI com `continue-on-error` — não bloqueia.)

## 3. Mexeu em UI? Existe um TERCEIRO job

O `web_a11y` roda o axe-core em browser real, **nos 3 temas** (light, dark,
hologram). Equivalente local:

```bash
scripts/a11y-gate.sh
```

Gate verde de a11y NÃO valida layout (transbordo/corte só aparece abrindo o
screenshot) — ver CLAUDE.md §7. Detalhes de UI são da skill `front-gate` (quando
existir); esta skill só lembra: se tocou em `apps/web/src`, o a11y conta como parte
do portão.

## 4. Onde o CI roda (e onde NÃO roda)

- `ci.yml` dispara SÓ em `main` (push e pull_request). **PR para `develop` não roda
  CI nenhum** — `gh pr checks` devolve "no checks reported" e é o esperado. Até o PR
  `develop → main`, a ÚNICA evidência de qualidade é este gate local.
- Falha no CI de verdade? Leia o log real, nunca o summary (ele engana):

```bash
gh run view <run-id> --log-failed
```

## 5. Como reportar o resultado

- Cite o output REAL: contagem de testes, arquivos com erro, nomes das falhas.
  "Rodou ok" não é evidência (CLAUDE.md §6.10–6.11).
- Docker desligado → o veredito é "gate PARCIAL (integração não coberta)", dito
  explicitamente, mesmo com tudo o mais verde.
- As 3 falhas ambientais, se aparecerem, são nomeadas como tais — separadas de
  qualquer falha nova.
