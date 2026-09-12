---
name: migration
description: >
  Roteiro OBRIGATÓRIO ao tocar apps/api/app/db/models/ ou apps/api/alembic/, e para
  qualquer correção de dado em massa. Gatilhos literais: "nova coluna", "alterar
  tabela", "alembic", "migration", "backfill", "seed", "índice", "constraint",
  "downgrade", "dropar coluna". Migration é a mudança que só dá errado em produção:
  o autogenerate NÃO enxerga índice parcial nem CHECK, downgrade tem de ser real,
  backfill tem de ser idempotente, e o job de deploy roda a migration ANTES da API
  nova — sobre uma imagem cujo digest fica pinado.
---

# /migration — migration reversível e backfill idempotente

O projeto tem 24 migrations em `apps/api/alembic/versions/` (Alembic 1.18.4, `env.py`
async que lê a `DATABASE_URL` das `Settings`, `compare_type` e `compare_server_default`
ligados — `alembic/env.py:36-40`, `:53-54`). O QA cobra "migrations reversíveis;
correção de dados via backfill idempotente"; esta skill diz como fazer e como provar.
Números de linha conferidos em 11/09/2026 — se um grep não bater, o código andou:
releia o arquivo. Caminhos relativos a `apps/api/`.

## Passo 1 — Modelo primeiro, migration gerada depois, e UMA head

O modelo em `app/db/models/` é a fonte; a migration é derivada. Modelo novo precisa
estar importado em `app/db/models/__init__.py` — o `env.py` registra o `Base.metadata`
por esse import (`:28`), e modelo fora dele é invisível ao autogenerate.

```bash
cd apps/api
uv run alembic revision --autogenerate -m "s<N>_<tema_curto>"   # gera alembic/versions/<rev>_s<N>_<tema>.py
uv run alembic heads                                              # esperado: UMA linha; duas = precisa de `alembic merge`
```

Estilo da casa (copie de `alembic/versions/c9e4a7b2d5f8_s6_client_closed_at.py`):
docstring que diz o que muda, por que, se há backfill e o que o downgrade faz; nada de
`app.*` importado no topo — importar código da app constrói o `Settings` inteiro no
import do módulo, e o job de migração sobe sem as secrets do serviço
(`d5c81a4e9b27_s5_user_tenancy.py:32-33`). Constante que o modelo também usa (string
de CHECK, predicado de índice) é COPIADA na migration, com um teste comparando as duas
fontes (`:47-49`; `deduped_session_index_predicate`, `tests/unit/test_usage_event_schemas.py:284`).

## Passo 2 — Revisar o arquivo gerado À MÃO: onde o autogenerate erra

Verificado no código-fonte do Alembic 1.18.4 instalado (`.venv/.../alembic/`):

- **Índice parcial (`postgresql_where`) — o autogenerate NÃO vê o predicado.**
  `compare_indexes` (`ddl/postgresql.py:337-395`) compara unicidade, expressões e
  `_dialect_options` (`:328-335`), que só considera `postgresql_nulls_not_distinct`.
  Trocar o `WHERE` do índice = "sem mudanças" no autogenerate. Precedentes:
  `uq_recon_sessions_account_month` (`WHERE deleted_at IS NULL` — modelo
  `app/db/models/reconciliation_session.py:98-105`, migration
  `b8e2d4a71f36_s4_reconciliation_files.py:161-167`) e a troca de predicado do dedup de
  `usage_events`, escrita à mão (`a1d7f36c9b52_s6_usage_events_dedup_allowlist.py:84-92`).
- **CHECK constraint — não existe comparador.** `check_constraints` entra nas chaves de
  REFLEXÃO (`autogenerate/compare/util.py:30,38`), mas nenhum comparador as usa:
  `compare/constraints.py` só diffa unique, FK e índice, e CHECK aparece apenas no
  `render.py`, dentro de `create_table`. Adicionar ou mudar um CHECK = "sem mudanças".
  `ck_users_scope_client_id` foi criada à mão com `op.create_check_constraint`
  (`d5c81a4e9b27:95`). ⚠️ Passe o **rótulo** (`"scope_client_id"`), não o nome final: a
  `NAMING_CONVENTION` do `Base` (`app/db/base.py:22-37`) prefixa `ck_<tabela>_` — passar
  `"ck_users_scope_client_id"` gera `ck_users_ck_users_scope_client_id` (`:51-55`).
- **`server_default`** é comparado (`env.py:54`), mas o valor precisa ser o literal SQL:
  `server_default="system"` (`d5c81a4e9b27:67`), `sa.text("now()")`. `default=` do
  Python NÃO é server default — coluna `NOT NULL` sem `server_default` numa tabela com
  linhas FALHA no `ALTER`.
- **Enum de domínio é `String`, não `ENUM` do Postgres** (`users.role`/`scope` são
  `VARCHAR(20)`, `d5c81a4e9b27:67`; fonte única `UserRole`/`UserScope` em
  `app/db/models/user.py:38,56`). Valor novo no enum não gera DDL — só precisa de
  migration se a largura ou um CHECK mudar. Tipos da casa: `sa.UUID`, `Numeric(14, 2)`,
  `DateTime(timezone=True)`, `String(n)`.
- **Renomear coluna sai como DROP + ADD** (perda de dado). Escreva `op.alter_column(...,
new_column_name=...)` à mão.

Depois de editar, prove que modelo e migrations estão em sincronia:

```bash
uv run alembic check   # contra um banco JÁ migrado (passo 3); exit 0 = em sincronia
```

⚠️ **Hoje o `alembic check` FALHA com um drift conhecido** (medido em 11/09/2026,
exit 255), e a mensagem é sempre esta:

```
FAILED: New upgrade operations detected: [('remove_index', Index('ix_recon_sessions_deleted_at', ...)),
                                          ('add_index', Index('ix_reconciliation_sessions_deleted_at', ...))]
```

É **só o NOME** do índice: a migration criou `ix_recon_sessions_deleted_at` à mão,
abreviado (`d1e8a4b9f2c5_soft_delete_reconciliation_sessions.py:45-49`), enquanto o
modelo declara `index=True` (`app/db/models/reconciliation_session.py:169-173`) e a
`NAMING_CONVENTION` gera `ix_reconciliation_sessions_deleted_at`. A definição no banco
é idêntica (`CREATE INDEX … USING btree (deleted_at)`), então não há efeito em query —
mas o `check` fica vermelho para sempre até alguém renomear. **Leia a saída, não o exit
code:** se aparecerem SÓ essas duas operações, a sua migration está em sincronia; qualquer
operação além dessas é drift seu. Corrigir o nome é entrega separada (migration de
rename), não carona numa task alheia.

## Passo 3 — Downgrade de verdade, e o ciclo local ANTES do commit

`downgrade()` com `pass` é proibido (hoje: 0 de 24). Quando o dado NÃO cabe na forma
antiga, o downgrade ABORTA com a consulta que resolve, em vez de apagar:
`_ABORT_IF_DUPLICATES` (`a1d7f36c9b52:60-80`, `RAISE EXCEPTION 'Downgrade bloqueado: …'`),
travado por `test_downgrade_aborta_com_mensagem_acionavel_se_houver_duplicata`
(`tests/integration/test_migrations.py:307`). Encolher largura de coluna deixa o aviso
no próprio downgrade (`f4d1a7c93e20_fase1_situation_divergente_wrong_date.py:73-75`).

O ciclo roda num Postgres DESCARTÁVEL — nunca no banco de dev: `downgrade` derruba
coluna e dado. O `env.py` lê a URL das `Settings`, então a injeção é por env var, com
as mesmas chaves fake do CI que a skill `gate` lista (nenhuma é segredo):

```bash
docker ps   # Docker desligado aparece como "could not be found in this WSL 2 distro": é ligar o Desktop
docker run -d --rm --name mig-pg -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=test -p 15433:5432 postgres:16-alpine
until docker exec mig-pg pg_isready -U test -d test >/dev/null 2>&1; do sleep 1; done
cd apps/api
export DATABASE_URL='postgresql+psycopg://test:test@localhost:15433/test' ENVIRONMENT=development LOG_LEVEL=warning \
  OMIE_ENCRYPTION_KEY='0000000000000000000000000000000000000000000000000000000000000000' \
  JWT_SECRET='1111111111111111111111111111111111111111111111111111111111111111' \
  SEARCH_BLIND_INDEX_KEY='2222222222222222222222222222222222222222222222222222222222222222' ANTHROPIC_API_KEY='sk-ant-ci-fake'
uv run alembic upgrade head && uv run alembic current     # do zero: todas as migrations sobem
uv run alembic downgrade -1 && uv run alembic current     # a sua desce
uv run alembic upgrade head && uv run alembic current     # sobe de novo, de volta à head
uv run alembic check                                      # separado: hoje sai 255 (ver aviso abaixo)
docker stop mig-pg
```

**Medido em 11/09/2026** por esse caminho, com o container `postgres:16-alpine`:
`upgrade head` do zero aplicou **24 migrations** (exit 0) e parou em `c9e4a7b2d5f8 (head)`;
`downgrade -1` desceu a `c9e4a7b2d5f8 → a1c5e7f9b2d4` e a coluna `clients.closed_at`
sumiu (`count = 0` em `information_schema.columns`); `upgrade head` recolocou
(`count = 1`) e voltou à head. Ciclo inteiro em ~9 s. O índice parcial e o CHECK
chegaram ao banco como esperado:

```
CREATE UNIQUE INDEX uq_recon_sessions_account_month ON public.reconciliation_sessions
  USING btree (client_id, omie_conta_id, reference_month) WHERE (deleted_at IS NULL)
ck_users_scope_client_id
```

⚠️ Rode `alembic current` DEPOIS de cada passo: `upgrade`/`downgrade` que falham no meio
deixam o banco numa revisão intermediária, e o erro sozinho não diz onde parou. E cuidado
com pipe: `uv run alembic check | grep …` devolve o exit do `grep`, não do Alembic —
por isso o comando acima não usa pipe.

## Passo 4 — Aditiva por padrão

- Coluna nova entra `nullable=True` (`c9e4a7b2d5f8:30-33`), ou `NOT NULL` com
  `server_default` e backfill (`d5c81a4e9b27:65-83`).
- **Drop de coluna é entrega SEPARADA**, depois de o código parar de usá-la. Precedentes
  de coluna mantida como legado: `date_tolerance_days` (`reconciliation_session.py:138-141`,
  novas sessões gravam 0) e `reconciliation_sessions.file_hash`, que virou nullable em
  vez de sumir (`b8e2d4a71f36:150-156`).
- Alargar coluna: `op.alter_column(type_=sa.String(30), existing_type=..., existing_nullable=...)`
  (`f4d1a7c93e20:39-45`).
- Por que aditiva importa no deploy: a migration roda ANTES da revisão nova da API
  (passo 8) — durante a janela, o código VELHO ainda serve sobre o schema NOVO.

```bash
grep -n "drop_column\|drop_table" apps/api/alembic/versions/<sua_migration>.py   # esperado no upgrade: nada (só no downgrade)
```

## Passo 5 — Backfill idempotente, e onde ele mora

Idempotente = **convergente**, não incremental: rodar duas vezes dá o mesmo estado.

- `UPDATE … WHERE <ainda não está no estado final>` (`d5c81a4e9b27:75-83`);
- `INSERT … ON CONFLICT (code) DO NOTHING` para seed (`f4d1a7c93e20:50-68`);
- `UPDATE … WHERE e.file_id IS NULL` para vincular linhas (`b8e2d4a71f36:141-148`);
- pré-check que aborta antes de criar UNIQUE sobre dado duplicado (`b8e2d4a71f36:159`).

Travado por `test_backfill_e_idempotente` (`test_migrations.py:188-206`): desce e sobe
DUAS vezes e conta as linhas. Seed de tabela compartilhada entre testes (`anomaly_types`)
é get-or-create, nunca insert cego (CLAUDE.md §7).

**Onde mora:** um `UPDATE`/`INSERT` em SQL → dentro da migration (`op.execute(sa.text(...))`).
Backfill grande, por lotes, ou que precisa de código da app (cripto, KMS) → script em
`scripts/`, rodado como Cloud Run Job com as secrets do serviço: precedentes
`scripts/rotate_encryption_key.py` (por lotes, retomável, 2º run converte 0) e
`scripts/mark_stuck_sessions_as_error.py`. Uso: `uv run python -m scripts.<nome>`;
no job, `--command=python --args=-m,scripts.<nome>`.

```bash
grep -n "op.execute" apps/api/alembic/versions/<sua_migration>.py   # cada um: convergente? tem WHERE/ON CONFLICT?
```

## Passo 6 — Integridade mora no BANCO, não só na aplicação

- CHECK: `ck_users_scope_client_id` (`user.py:144`; teste
  `test_check_constraint_existe_apos_upgrade`, `test_migrations.py:157`).
- UNIQUE parcial: `uq_recon_sessions_account_month` (só sessões ativas),
  `uq_usage_events_event_session` (allow-list de eventos).
- UNIQUE simples: `uq_recon_omie_postings_file_entry` e `uq_recon_omie_postings_client_cod_int`
  (teste `test_uniqueness_lives_in_the_database`, `:559`) — a dedup do lançamento no Omie
  não depende de a aplicação lembrar de checar.
- FK sempre com `ondelete` explícito: `RESTRICT` onde a linha filha é histórico
  (`users.client_id`, `d5c81a4e9b27:87-94`), `CASCADE` onde é parte
  (`reconciliation_files`, `b8e2d4a71f36:100`).
- Nomes seguem a `NAMING_CONVENTION` (`base.py:22-37`): `pk_`, `fk_`, `ix_`, `uq_`, `ck_`.
- **Coluna que guarda dado do cliente nasce cifrada** (`_encrypted` + `_iv`, envelope
  com DEK por cliente e AAD) e entra na lista do CLAUDE.md §4.1 — é a skill `crypto-field`
  (subtask 8). Nome, CNPJ, descrição e observação em claro é violação da §4.5.

```bash
grep -n "CheckConstraint\|unique=True\|ondelete=" apps/api/app/db/models/<seu_modelo>.py
```

## Passo 7 — Provar: teste de round-trip + gate

`tests/integration/test_migrations.py` é o padrão: fixture `migrations_db_url` (`:38`)
cria e derruba o banco `alembic_roundtrip`; `alembic_cfg` (`:54`) injeta a URL por env var
e limpa o `lru_cache` das `Settings`; testes SÍNCRONOS (o `env.py` chama `asyncio.run`);
revisões referenciadas por ID, nunca `-1` relativo (`:90-95`). Uma classe por sprint
(`:124`, `:253`, `:366`, `:488`, `:537`). Migration nova ganha a sua: `upgrade head` →
insere linha "legada" → `downgrade <rev anterior>` → colunas somem, linhas ficam →
`upgrade head` → backfill aplicado; e o backfill roda 2× (`:199-203`).

```bash
cd apps/api && uv run --extra dev pytest tests/integration/test_migrations.py -q --no-cov   # exige Docker (testcontainers) ou TEST_DATABASE_URL — ver skill `gate`
```

## Passo 8 — No deploy: a migration é um Cloud Run Job, e o job pina o digest

`.github/workflows/deploy-dev.yml`: `build-api → migrate → deploy-api` (`:8`); o job
`auditoria-api-migrate-dev` (`:64`) guarda o digest da imagem no manifest, então o step
"Re-resolve :dev digest" faz `gcloud run jobs update --image …:dev` (`:164-178`) ANTES
de `gcloud run jobs execute --wait` (`:192-197`) — sem o update, o job roda a imagem
VELHA e "migra" nada. `deploy-api` só sobe se `migrate` deu certo (`:199-202`). O job
recebe `KEK_KMS_KEY_NAME` e as secrets de alerta (`:168-176`) porque scripts de backfill
rodam nele. Push na `main` migra sempre; `workflow_dispatch` tem `run_migrations`
(`:43-44`). Consequência prática: entre `migrate` e `deploy-api`, a API antiga serve
sobre o schema novo — por isso o passo 4.

```bash
grep -n "MIGRATE_JOB\|jobs update\|jobs execute" .github/workflows/deploy-dev.yml
```

## Fechamento

Rode a skill `gate` (o round-trip exige Docker) e feche com a skill `entrega`. Coluna
cifrada nova, constraint de integridade nova ou regra de dado nova muda o CLAUDE.md §4 na
MESMA entrega (§13).
