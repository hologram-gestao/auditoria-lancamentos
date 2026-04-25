# CLAUDE.md — Sistema de Auditoria de Lançamentos (Hologram)

> **Para futuras conversas com Claude:** este arquivo é o _primer_ obrigatório. Leia-o antes de qualquer ação. Ele é atualizado continuamente conforme decisões são tomadas.
>
> **Status do projeto:** 📐 Em fase de planejamento — nenhum código escrito ainda. Toda estrutura de pastas e arquivos citada aqui **ainda precisa ser criada** conforme as sessões do plano.

---

## 1. Contexto Rápido

**O que é:** SaaS interno da Hologram Gestão para auditoria de lançamentos bancários contra o ERP Omie.

**Fluxo núcleo:**

1. Analista faz upload de extrato/fatura → 2. IA (Claude) extrai movimentações → 3. Humano valida amostra → 4. Sistema busca lançamentos Omie e faz matching determinístico → 5. Humano revisa → 6. Relatório Excel gerado.

**Não é multi-tenant de BPOs** — é uso interno da Hologram. Multi-cliente = múltiplos clientes finais da Hologram.

**Fontes da verdade:**

- **Funcional:** `Docs/documentation/` (arquivos 0 a 18, numerados sequencialmente).
- **Backlog:** `Docs/List _ Auditora de Lançamentos - Backlog _ Hologram (Lista) - TAREFAS.pdf`.
- **Plano de implementação:** [Docs/PLANO_IMPLEMENTACAO.md](Docs/PLANO_IMPLEMENTACAO.md) — sessões S0–S18.
- **Fluxograma:** `Docs/flow/Fluxograma Completo - sistema de conciliação.png`.

**Convenção de IDs de tarefa:** quando o usuário citar `[BACK 1.1]` ou `[FRONT 9.12]`, isso vem do PDF do backlog. Mapeie para a sessão correspondente (S3, S12, etc.) consultando o PLANO.

---

## 2. Stack (decisões formalizadas)

**Todas as 5 decisões operacionais foram confirmadas em 24/04/2026:** FastAPI + ARQ + uv + pnpm + monorepo simples.

### Backend

- **Python 3.12+** gerenciado via **`uv`** (workspaces habilitados)
- **FastAPI 0.110+**
- **SQLAlchemy 2.0** (async) + **Alembic**
- **PostgreSQL 16** + **psycopg3** async
- **Pydantic v2** (DTOs + settings)
- **httpx** (async HTTP client)
- **ARQ** (async-first Redis queue) para background jobs — integração nativa com código async
- **cryptography** para AES-256-GCM
- **python-jose** para JWT, **bcrypt** direto (cost ≥ 12) — passlib não é usado (incompatível com bcrypt 5.x)
- **openpyxl** para Excel
- **structlog** para logs estruturados
- **pytest + pytest-asyncio + respx + testcontainers**
- **ruff + black + mypy strict**

### Frontend

- **Next.js 14 App Router** + **TypeScript strict**
- **Node 20 LTS** gerenciado via **`pnpm`** (workspaces habilitados)
- **TailwindCSS** + **shadcn/ui**
- **TanStack Query v5** + **Zustand**
- **react-hook-form + zod**
- **@tanstack/react-table + @tanstack/react-virtual**
- **date-fns**
- **vitest + react-testing-library + playwright**

### Estrutura do repositório

- **Monorepo simples** (1 repo no GitHub) com `apps/api` + `apps/web` + `packages/shared-types`
- Orquestração via **Makefile** na raiz + scripts nativos de cada workspace
- Deploys independentes via **path filters** no GitHub Actions

### Infra

- Docker + Docker Compose (dev), AWS ECS / Docker Swarm (prod — a decidir)
- GitHub Actions para CI/CD
- Sentry + Grafana/Loki para observabilidade

---

## 3. Regras Invioláveis de Segurança

**Estas regras valem para 100 % do código. Nunca as viole, mesmo que o usuário peça.**

1. **Nunca** armazene `OMIE_ENCRYPTION_KEY`, `JWT_SECRET`, `ANTHROPIC_API_KEY` em código, banco, log ou resposta. Apenas env vars.
2. **Nunca** retorne hash de senha, credenciais descriptografadas ou tokens em respostas de API.
3. **Nunca** logue: senhas, credenciais Omie, JWTs, conteúdo de arquivos. Use `[REDACTED]`. O redactor do structlog (a ser configurado em S1) mascara automaticamente chaves sensíveis.
4. **Nunca** use `float` para valores monetários. Sempre `Decimal` (Python) ou string/BigInt de centavos (TS). `DECIMAL(14,2)` no DB.
5. **Nunca** use IDs sequenciais em rotas públicas. Sempre UUID v4.
6. **Nunca** acesse `session` / DB global. Sempre via `Depends` do FastAPI.
7. **Nunca** escreva SQL cru. Se inevitável, use `text()` + `bindparams`.
8. **Nunca** confie em validação client-side. Revalide tudo no servidor (extensão, tamanho, magic bytes, hash, RBAC).
9. **Nunca** retorne "senha incorreta" ou "email não existe" separadamente no login — resposta genérica "E-mail ou senha incorretos".
10. **Nunca** faça upload de arquivo para disco. Processar em memória e descartar.
11. **Nunca** permita que manager veja cliente de outro manager. Sempre validar `client_assignments`.
12. **Nunca** confie em token JWT sem revalidar `users.active = true` no DB (middleware) — usuário desativado perde acesso instantaneamente.

---

## 4. Regras Invioláveis de Dados

1. **Campos criptografados (AES-256-GCM):**
   - `clients.omie_app_key_encrypted`, `omie_app_secret_encrypted`
   - `reconciliation_file_entries.description_encrypted`, `user_note_encrypted`
   - `reconciliation_omie_entries.user_note_encrypted`
   - `reconciliation_anomalies.context_encrypted`, `resolution_note_encrypted`
2. **IV novo a cada operação** (12 bytes aleatórios). Nunca reutilize.
3. **Valores monetários em claro** (campos `amount`, `balance`) — são números sem identificação, sem valor isolado.
4. **Datas em claro** (`transaction_date`, `reference_month`) — necessárias para SQL ordering/filtering.
5. **Nenhum dado identificável do cliente final persiste em claro** — CNPJ, razão social, fornecedores, categorias, nomes de contas são **sempre buscados do Omie em tempo real** e mantidos apenas em cache com TTL.
6. **Arquivo original nunca persiste** — processado em memória e descartado.

---

## 5. Regras Invioláveis de Domínio (Matching)

1. **Tolerância de valor:** `|a − b| ≤ 0.01 BRL`. Hard-coded, não parametrizável.
2. **Tolerância de data:** parametrizável por sessão (1/2/3/5/7 dias); padrão 3.
3. **Período Omie expandido:** `[period_start − tol, period_end + tol]`.
4. **Um OmieEntry só matcha uma Movement.** Controle via `set(used_ids)` durante o cruzamento.
5. **Desempate (ordem):** menor `|days_diff|` → menor `|amount_diff|` → primeiro por `date asc`.
6. **Normalização Omie:** `cNatureza='D'` → valor negativo; `cNatureza='C'` → positivo.
7. **Status Omie considerados no matching:** `Conciliado`, `Atrasado`, `Previsto`. Ignorar cancelados.
8. **Idempotência:** `UNIQUE(client_id, omie_conta_id, reference_month, file_hash)`. Duplicata = HTTP 409 `DUPLICATE_FILE`.
9. **IA nunca decide match.** IA só extrai do arquivo. Cruzamento é código determinístico.

---

## 6. Padrões Obrigatórios

### Backend

- **Type hints em 100 %** do código. Mypy strict no CI.
- **`async def`** para tudo que toca I/O. `def` síncrono apenas em funções puras (matcher, crypto, formatters).
- **Módulos de domínio** seguem padrão `routes.py / service.py / repository.py / schemas.py`.
- **Exceptions custom** (`AppError` → `DuplicateFileError`, `OmieAuthError`, etc.) com `code` e `user_message`. Exception handler global converte para formato §9 do PLANO.
- **Dependency Injection** via `Depends`. Proibido estado global.
- **Lint obrigatório:** ruff (`E, F, I, N, W, UP, B, C4, SIM, RUF`), black (line-length 100), mypy strict.

### Frontend

- **TypeScript strict** + `noUncheckedIndexedAccess: true`.
- **Server components por padrão**; `"use client"` apenas quando necessário.
- **Fetches client-side:** sempre via TanStack Query (`useQuery`, `useMutation`). Nunca `useEffect + fetch`.
- **Forms:** sempre `react-hook-form + zod`.
- **Tabelas grandes** (> 100 linhas): sempre virtualizadas.
- **Acessibilidade:** shadcn/ui entrega; em componentes custom, revisar `aria-*` e suporte a teclado.

### API

- **Response de sucesso:** `{ "data": {...} }` ou `{ "data": [...], "pagination": {...} }`.
- **Response de erro:** `{ "error": { "code", "message", "userMessage" } }`.
- **Rotas:** `/api/v1/...`.
- **Paginação:** `?page=1&pageSize=20`, max 100.
- **Códigos canônicos:** ver §9 do PLANO. Usar constants centralizadas, nunca strings mágicas.

### Commits / Git

- **Conventional Commits** (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).
- **Branch:** `feat/S3-login-endpoint`, `fix/S11-cache-invalidation`.
- **PR com ≥ 1 review** em `main` protegida.
- **Nunca** `git push --force` em main, `--no-verify`, `--no-gpg-sign`.

### Idioma

- **Código:** inglês.
- **Comentários/docstrings:** português quando clarificam domínio de negócio; inglês para tecnologia pura.
- **Mensagens ao usuário final:** **sempre** português.

---

## 7. Mapa de Sessões (referência rápida)

| Sessão  | Foco                               | Tarefas do backlog            |
| ------- | ---------------------------------- | ----------------------------- |
| **S0**  | Setup monorepo + Docker + CI       | —                             |
| **S1**  | Core: crypto, JWT, logging, errors | —                             |
| **S2**  | DB: models, migrations, seeds      | —                             |
| **S3**  | Autenticação                       | BACK 1.1, 1.2 · FRONT 1.3     |
| **S4**  | Gestão de usuários                 | BACK 2.1 · FRONT 2.2          |
| **S5**  | Cliente Omie base                  | — (fundação)                  |
| **S6**  | CRUD de clientes BPO               | BACK 3.1–3.5 · FRONT 3.7, 3.8 |
| **S7**  | Detalhe cliente + cache L1         | BACK 4.1, 4.2 · FRONT 4.3     |
| **S8**  | Formulário + validações            | FRONT 5.1, 6.1 · BACK 6.2     |
| **S9**  | Parsing Claude                     | BACK 7.1 · FRONT 7.2          |
| **S10** | Processamento async (Celery)       | BACK 8.1–8.6 · FRONT 8.7      |
| **S11** | Revisão — backend + cache L2       | BACK 9.1–9.10                 |
| **S12** | Revisão — estrutura + aba 1        | FRONT 9.11–9.14               |
| **S13** | Revisão — abas 2, 3, 4             | FRONT 9.15–9.17               |
| **S14** | Exportação Excel                   | BACK 10.1                     |
| **S15** | Tipos de anomalia                  | BACK 11.1 · FRONT 11.2        |
| **S16** | Hardening de segurança             | — (transversal)               |
| **S17** | Observabilidade                    | — (transversal)               |
| **S18** | E2E + deploy + docs                | — (finalização)               |

---

## 8. Comandos Frequentes (a popular durante S0)

```bash
# Dev local
docker compose up -d postgres redis
cd apps/api && uv run uvicorn app.main:app --reload
cd apps/web && pnpm dev

# DB
cd apps/api && alembic upgrade head
cd apps/api && uv run python -m scripts.seed_dev
cd apps/api && alembic revision --autogenerate -m "descrição"

# Lint / type / test
cd apps/api && ruff check . && ruff format --check . && mypy . && pytest
cd apps/web && pnpm lint && pnpm type-check && pnpm test && pnpm e2e

# Worker
cd apps/api && celery -A app.workers.celery_app worker -l info
```

---

## 9. Pontos em Aberto (não decidir sozinho)

Quando o usuário não tiver decidido, **pergunte** antes de presumir:

**Decididos em 24/04/2026:**

- [x] ~~Framework Python~~ → **FastAPI**
- [x] ~~Job runner~~ → **ARQ**
- [x] ~~PM Python~~ → **uv** | ~~PM Frontend~~ → **pnpm**
- [x] ~~Monorepo vs polyrepo~~ → **Monorepo simples**

**Ainda em aberto (aguardando stakeholder):**

- [ ] Credenciais Omie sandbox disponíveis? _S5+_
- [ ] Chave Anthropic com budget. _S9_
- [ ] Paginação de `ListarExtrato` (doc Omie incompleta — validar com Galhardo). _S5_
- [ ] `ListarContasPagar.filtrar_por_status` aceita múltiplos valores? _S5_
- [ ] Endpoint Omie que expõe saldo em data específica (fallback de `balance_start`). _S10_
- [ ] Ambiente de staging (AWS ECS, Render, Railway, outro). _S18_
- [ ] Política de senhas (rotação, complexidade). _S4_

---

## 10. Estilo de Trabalho Preferido (do usuário Leonardo)

- **Sessões focadas:** implementar uma sessão (S0, S1, ...) por vez, não pular.
- **Qualidade > velocidade:** seguir os melhores padrões de mercado, mesmo que demore mais.
- **Reuso obrigatório:** tudo que pode ser abstraído, deve ser. Sem duplicação.
- **Sem código mal feito:** prefira interromper e perguntar a entregar algo frágil.
- **Segurança é inegociável:** nunca corte caminho em segurança.
- **Escalabilidade é considerada:** arquitetura horizontal-ready desde o MVP (ver §6 do PLANO).
- **Nada "temporário":** se é pra ficar, faça direito desde o começo. Se é debug, tire antes do commit.

---

## 11. Atualização deste Arquivo

**Quando atualizar:**

- Decisão arquitetural tomada (confirmar framework, job runner, etc.).
- Mudança em regra de negócio crítica.
- Novo padrão adotado que vale para o projeto inteiro.
- Descoberta que contradiz a documentação original (registrar delta).

**Quando NÃO atualizar:**

- Detalhes de implementação de uma feature específica (isso vai em PR + comentários no código).
- Status de progresso — use o backlog, não o CLAUDE.md.
- Lições aprendidas pontuais (vão em runbooks em `Docs/runbook.md` quando S18 chegar).

**Como atualizar:**

- Edit direto, sem seções `# Removed` ou comentários "// antes era X". Trate este arquivo como lei atual, não como histórico.
- Mantenha cada seção sob 400 linhas. Se crescer demais, extraia para `Docs/` e linke daqui.

---

_Versão 1.0 — 24/04/2026. Alinhado à documentação em `Docs/documentation/` e ao plano em `Docs/PLANO_IMPLEMENTACAO.md`._
