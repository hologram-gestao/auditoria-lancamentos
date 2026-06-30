# CLAUDE.md — Sistema de Auditoria de Lançamentos (Hologram)

> **Para futuras conversas com Claude:** este arquivo é o _primer_ obrigatório. Leia-o antes de qualquer ação. Ele é atualizado continuamente conforme decisões são tomadas.
>
> **Status do projeto:** 🚀 Sessões **S0–S19 implementadas e rodando em dev** no Google Cloud Run (GCP `liberdade-assessoria`, região `southamerica-east1`). Acesso pelas URLs `*.run.app` via **BFF reverse-proxy do Next** — não há custom domain configurado (o BFF resolveu o cookie cross-site, então o DNS na Wix nunca foi necessário). A conciliação file-driven funciona ponta a ponta (upload → IA → matching → revisão → Excel). **Não trate mais como greenfield:** o código é a fonte da verdade — leia antes de assumir que algo "ainda precisa ser criado".
>
> **Roadmap atual (PRD 15/06/2026 → FASE 0–5):** o roadmap foi reordenado pelo PRD em [Docs/NextSteps/](Docs/NextSteps/). O plano de execução é [Docs/PLANO_PROXIMOS_PASSOS.md](Docs/PLANO_PROXIMOS_PASSOS.md) (sessões **S20+**): **FASE 0 ✅ CONCLUÍDA (S20)** — Redis/ARQ removido, processamento via `BackgroundTasks` ([BACK 0.1]); os 2 bugs já estavam corrigidos (auth #19, timeout #16). **FASE 1–2** conciliação e lançamento de **fatura de cartão**. **FASE 3** glossário por cliente. **FASE 4** Open Finance (Pluggy). **FASE 5** rotinas automáticas de auditoria (absorve o antigo S20–S27). ✅ **A tolerância de data fixa já está no código** (FASE 1 / BACK 1.6, na branch de integração `feat/fase1-cartao` — ainda não na `main`): o matching usa `DATE_DIVERGENCE_RANGE = 3` fixo — data exata → `conciliado`; 1–3 dias → `conciliado_data_divergente` (+ anomalia `wrong_date`); > 3 → `sem_omie`. Vale **também para conta corrente** → muda comportamento em prod quando a FASE 1 for mergeada na main. Ver §5.2.

---

## 1. Contexto Rápido

**O que é:** SaaS interno da Hologram Gestão para auditoria de lançamentos bancários contra o ERP Omie.

**Fluxo núcleo:**

1. Analista faz upload de extrato/fatura → 2. IA (Claude) extrai movimentações → 3. Humano valida amostra → 4. Sistema busca lançamentos Omie e faz matching determinístico → 5. Humano revisa → 6. Relatório Excel gerado.

**Não é multi-tenant de BPOs** — é uso interno da Hologram. Multi-cliente = múltiplos clientes finais da Hologram.

**Fontes da verdade:**

- **Funcional:** `Docs/documentation/` (arquivos 0 a 18, numerados sequencialmente).
- **Backlog:** `Docs/List _ Auditora de Lançamentos - Backlog _ Hologram (Lista) - TAREFAS.pdf`.
- **PRD vigente (roadmap):** `Docs/NextSteps/PRD - Próximos Passos-20260615173056.md` — FASE 0–5 (estabilização, cartão, glossário, Pluggy, rotinas).
- **Plano de execução vigente:** [Docs/PLANO_PROXIMOS_PASSOS.md](Docs/PLANO_PROXIMOS_PASSOS.md) — sessões **S20+** derivadas do PRD. **É o plano ativo daqui pra frente.**
- **Plano histórico (S0–S19):** [Docs/PLANO_IMPLEMENTACAO.md](Docs/PLANO_IMPLEMENTACAO.md) — conciliação file-driven, já construída.
- **Plano antigo do pivot (SUPERSEDED):** [Docs/PLANO_S20_AUDITORIA_CONTINUA.md](Docs/PLANO_S20_AUDITORIA_CONTINUA.md) — auditoria contínua; **absorvido na FASE 5** do plano vigente. Útil só como material de origem (rastreabilidade dos transcritos + modelo de dados).
- **Fluxograma:** `Docs/flow/Fluxograma Completo - sistema de conciliação.png`.

**Convenção de IDs de tarefa:** quando o usuário citar `[BACK 1.1]` ou `[FRONT 9.12]`, isso vem do PDF do backlog. Mapeie para a sessão correspondente (S3, S12, etc.) consultando o PLANO.

---

## 2. Stack (decisões formalizadas)

**Decisões operacionais (24/04/2026):** FastAPI + uv + pnpm + monorepo simples. _O ARQ/Redis foi removido na FASE 0 (16/06/2026) — background jobs agora rodam via `BackgroundTasks` nativo do FastAPI._

### Backend

- **Python 3.12+** gerenciado via **`uv`** (workspaces habilitados)
- **FastAPI 0.115+**
- **SQLAlchemy 2.0** (async) + **Alembic**
- **PostgreSQL 16** + **psycopg3** async
- **Pydantic v2** (DTOs + settings)
- **httpx** (async HTTP client)
- **`BackgroundTasks` nativo do FastAPI** para o processamento assíncrono da conciliação (Omie + matching + qualificação) — sem broker, sem Redis (FASE 0). Teto via `asyncio.timeout(RECONCILIATION_TIMEOUT_SECONDS)`; rede de segurança no cron `mark_stuck_sessions_as_error`.
- **cryptography** para AES-256-GCM
- **python-jose** para JWT, **bcrypt** direto (cost ≥ 12) — passlib não é usado (incompatível com bcrypt 5.x)
- **openpyxl** para Excel
- **structlog** para logs estruturados
- **pytest + pytest-asyncio + respx + testcontainers + hypothesis** (property-based)
- **ruff (lint + format) + mypy strict**

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
- Orquestração via **scripts pnpm na raiz** (`pnpm dev:api`, `dev:web`, `infra:up`, `db:migrate`, `db:seed`, …). Há um `Makefile`, mas `make` não está disponível no ambiente Windows do dev — **prefira os scripts pnpm** (ver `MEMORY.md`).
- Deploys independentes via **path filters** no GitHub Actions

### Infra

- **Dev local:** Docker Compose (`docker/docker-compose.yml`) sobe **Postgres** (sem Redis desde a FASE 0).
- **Deploy (dev):** **Google Cloud Run** no GCP `liberdade-assessoria`, região `southamerica-east1`. Imagens no **Artifact Registry** (`southamerica-east1-docker.pkg.dev`), build via **Cloud Build**. Serviços: API e web; migration/cleanup rodam como **Cloud Run Jobs**. **Sem worker e sem Redis/Upstash** (FASE 0). ⚠️ A API precisa de **`--no-cpu-throttling` + `min-instances ≥ 1`** porque o processamento roda em `BackgroundTasks` fora do handler HTTP (ver §10).
- **CI/CD:** GitHub Actions — `ci.yml` (qualidade) + `deploy-dev.yml` / `deploy-prod.yml`.
- **Observabilidade:** Sentry + Grafana/Loki.

---

## 3. Regras Invioláveis de Segurança

**Estas regras valem para 100 % do código. Nunca as viole, mesmo que o usuário peça.**

1. **Nunca** armazene `OMIE_ENCRYPTION_KEY`, `JWT_SECRET`, `ANTHROPIC_API_KEY` em código, banco, log ou resposta. Apenas env vars.
2. **Nunca** retorne hash de senha, credenciais descriptografadas ou tokens em respostas de API.
3. **Nunca** logue: senhas, credenciais Omie, JWTs, conteúdo de arquivos. Use `[REDACTED]`. O redactor do structlog (em `apps/api/app/core/logging.py`) mascara automaticamente chaves sensíveis.
4. **Nunca** use `float` para valores monetários. Sempre `Decimal` (Python) ou string/BigInt de centavos (TS). `DECIMAL(14,2)` no DB.
5. **Nunca** use IDs sequenciais em rotas públicas. Sempre UUID v4.
6. **Nunca** acesse `session` / DB global. Sempre via `Depends` do FastAPI.
7. **Nunca** escreva SQL cru. Se inevitável, use `text()` + `bindparams`.
8. **Nunca** confie em validação client-side. Revalide tudo no servidor (extensão, tamanho, magic bytes, hash, RBAC).
9. **Nunca** retorne "senha incorreta" ou "email não existe" separadamente no login — resposta genérica "E-mail ou senha incorretos".
10. **Nunca** faça upload de arquivo para disco. Processar em memória e descartar.
11. **Nunca** permita que manager veja cliente de outro manager. Sempre validar `client_assignments`.
12. **Nunca** confie em token JWT sem revalidar `users.active = true` no DB (middleware) — usuário desativado perde acesso instantaneamente.
13. **Nunca leia, edite ou cite o conteúdo de arquivos `.env`, `.env.local`,
    `.env.production`, `.env.*` ou qualquer outro arquivo que contenha
    segredos reais.** Vale para qualquer ferramenta (Read, Edit, Bash com
    `cat`/`type`/`grep`, etc). Se o usuário pedir explicitamente para
    validar/editar uma variável, **recuse e oriente** a editar fora do
    Claude Code, sugerindo `permissions.deny` em `~/.claude/settings.json`
    como bloqueio técnico complementar. Se o conteúdo entrar no contexto
    por outro caminho (ex.: `<system-reminder>` do IDE quando o usuário
    abre/edita o arquivo), **avise imediatamente** que houve exposição e
    recomende rotação da credencial. Pode trabalhar com `.env.example` à
    vontade — placeholders públicos.

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
2. **Tolerância de data:** **fixa, não parametrizável** (FASE 1) — constante `DATE_DIVERGENCE_RANGE = 3` no matcher. Classificação por `|days_diff|`: `== 0` → `conciliado` (data exata); `1–3` → `conciliado_data_divergente` (+ anomalia `wrong_date`); `> 3` → sem match (linha fica `sem_omie`). Vale para conta corrente **e** cartão. O request não aceita mais `date_tolerance_days` (ignorado se enviado); a coluna homônima é mantida só por histórico e novas sessões gravam 0.
3. **Período Omie expandido:** `[period_start − DATE_DIVERGENCE_RANGE, period_end + DATE_DIVERGENCE_RANGE]` (3 dias fixos) — vale no processamento (`job.py`), na tela de revisão (`/available-omie-entries`) e no export.
4. **Um OmieEntry só matcha uma Movement.** Controle via `set(used_ids)` durante o cruzamento.
5. **Desempate (ordem):** menor `|days_diff|` → menor `|amount_diff|` → primeiro por `date asc`.
6. **Normalização Omie:** `cNatureza='D'` → valor negativo; `cNatureza='C'` → positivo.
7. **Status Omie considerados no matching** (canônico no DB, camelCase): `Conciliado`, `Atrasado`, `Previsto`. Ignorar cancelados. **Atenção à nomenclatura mista da Omie:** o canônico vem de `ListarExtrato.cStatus`; já o FILTRO `filtrar_por_status` em `ListarContasPagar/Receber` usa o enum oficial Omie em UPPERCASE (`ATRASADO`, `AVENCER`, etc) — `"PREVISTO"` NÃO é valor válido como filtro, devolve 5xx. Mapping: filtro `AVENCER` → canônico `Previsto`.
8. **Idempotência:** `UNIQUE(client_id, omie_conta_id, reference_month, file_hash)`. Duplicata = HTTP 409 `DUPLICATE_FILE`.
9. **IA nunca decide match.** IA só extrai do arquivo. Cruzamento é código determinístico.

---

## 6. Regras Invioláveis de Integridade (Anti-Alucinação)

**Estas regras existem para garantir que cada entrega seja confiável. Nunca as viole. Preferir admitir "não sei" a inventar é regra absoluta — fingir competência custa mais caro do que confessar dúvida.**

**Princípio-guia:** nada pode ser feito sem estar muito bem definido antes; nada pode ser entregue sem verificação. "Propriedade" = ter base concreta (leitura de código, output de comando, doc oficial) pra cada afirmação.

_**Definir ANTES de fazer:**_

1. **Antes de qualquer implementação não-trivial, alinhe o escopo com o usuário.** Use `AskUserQuestion`, Plan mode, ou texto explícito pedindo confirmação. "Bem definido antes de fazer" não é opcional.
2. **Spec ambígua → pergunte.** Se a especificação deixa dois caminhos válidos, NÃO decida sozinho. `AskUserQuestion` é a ferramenta certa.
3. **Pesquisa preliminar é parte do trabalho.** Antes de delegar a agente-filho ou começar a codar, investigue o estado atual do código (Read, Grep, Glob, Explore). Repasse achados explícitos — ver [[feedback_prompts_em_fatias]].
4. **Mudança em sistema desconhecido = leia primeiro.** Antes de editar módulo que você não viu nesta conversa, abra o arquivo e leia o contrato. Sem exceção.

_**Verificar ANTES de afirmar:**_

5. **Nunca cite identificador (função, classe, endpoint, env var, biblioteca, comando, flag, arquivo, módulo, hash, ticket) sem ter confirmado que existe.** Read/Grep/Glob/`gh`/`git` provam a existência. Se não pode verificar agora, escreva "(a confirmar)" explícito — não chute.
6. **Nunca invente assinatura de função** (parâmetros, tipos, defaults, retorno). Leia o arquivo onde está declarada antes de chamar/sugerir.
7. **Nunca invente comportamento de biblioteca de terceiros.** Confirme na documentação oficial atualizada — APIs mudam, conhecimento de treinamento envelhece.
8. **Omie API é especialmente perigoso.** Sempre validar contra response real, **nunca** contra `Docs/documentation/6` ou doc interna — já temos histórico de campos divergentes ([[feedback_omie_validate_response_not_internal_doc]]).
9. **Conhecimento de treinamento NÃO é fonte da verdade.** Para qualquer fato técnico (versão de lib, sintaxe de framework, comportamento de SDK), verifique no projeto ou na doc oficial **antes** de afirmar.

_**Verificar ANTES de declarar pronto:**_

10. **Nunca diga "está funcionando" sem ter rodado.** Testes locais (`uv run pytest`, `pnpm test`) ou comando do CI. Cite o output real, não "deve passar".
11. **Nunca invente número de testes, IDs de commit, status de CI, conteúdo de log, ou tamanho de diff.** Se vai citar, mostre o output real (`gh run view`, `git log`, output do pytest, `git diff --stat`).
12. **Nunca afirme que um arquivo foi criado/modificado sem ter executado a tool com sucesso.** Tool falhou, foi negada, ou nem foi chamada = tarefa não feita. Não relate como entregue.
13. **Verifique commit hashes via `git log` antes de citar.** Hashes mudam após rebase/amend — não confie em memória da própria conversa.

_**Honestidade ao reportar:**_

14. **Quando não souber, diga "não sei" e explique o que falta pra responder.** Não improvise. "Acho que" sem base é alucinação disfarçada.
15. **Quando estimar (tempo, custo, performance), explicite que é estimativa e mostre a base do cálculo.** "~5h baseado em S14 que foi 4h30 + sub-task UI" é estimativa válida; "~5h" sozinho é palpite.
16. **Quando um teste falhar de forma estranha, NÃO mude assertion pra fazer passar.** Investigue root cause. Esconder falha é alucinar competência — e o bug aparece em prod.
17. **Quando uma decisão de design comprometer algo (segurança, integridade, performance, UX), avise no momento da decisão.** Não enterre o trade-off em silêncio.
18. **Se o usuário pediu A e você fez B, declare a mudança e o motivo.** Nunca relate B como se fosse A.

_**Quando o ambiente discordar do que você "sabe":**_

19. **Conflito entre treinamento/memória e código atual: confie no código atual.** Código é fonte da verdade; treinamento é desatualizado; memórias caducam — ver disclaimer de memória do próprio agente.
20. **Erro inesperado de tool = pare e investigue.** Não tente "outro jeito" sem entender o que falhou — pode esconder bug real (permissão, path errado, racing).
21. **Escopo crescente durante implementação: pare e pergunte.** Se aparecer refactor adjacente não pedido, NÃO execute em silêncio. Mostre, pergunte, espere confirmação.
22. **Quando o output de uma tool não casar com expectativa, releia o output literalmente.** Não interprete "no output" como "deu certo" — pode ser stderr vazio com exit code != 0.

_**Sanity-check antes de finalizar resposta:**_ antes de apertar enviar numa resposta longa, releia mentalmente — toda função/arquivo/hash/número citado tem base concreta nesta conversa (output de tool, leitura de arquivo, doc oficial)? Estou reportando o que **fiz** (verificável no diff/log) ou o que **pretendia fazer**? Há alguma afirmação que o usuário poderia ler como certeza, mas eu não verifiquei? Qualquer "sim" pra "inventei" = pare, verifique, ou reescreva.

---

## 7. Padrões Obrigatórios

### Backend

- **Type hints em 100 %** do código. Mypy strict no CI.
- **`async def`** para tudo que toca I/O. `def` síncrono apenas em funções puras (matcher, crypto, formatters).
- **Módulos de domínio** seguem padrão `routes.py / service.py / repository.py / schemas.py`.
- **Exceptions custom** (`AppError` → `DuplicateFileError`, `OmieAuthError`, etc.) com `code` e `user_message`. Exception handler global converte para formato §9 do PLANO.
- **Dependency Injection** via `Depends`. Proibido estado global.
- **Lint obrigatório:** ruff (`E, F, I, N, W, UP, B, C4, SIM, RUF, S, A, ASYNC, ANN, PT, TID`) + ruff format (line-length 100), mypy strict.

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

### CI/CD verde (GitHub Actions)

- **`main` precisa terminar com CI verde sempre.** A esteira (`.github/workflows/ci.yml`) é o portão de qualidade — `ruff check` + `ruff format --check` + `mypy` + `pytest` + `pip-audit` no `apps/api`; `lint` + `type-check` + `test` + `npm audit` no `apps/web`. Os 4 do API rodam mesmo em PR/push tocando só web (e vice-versa) — não dá pra esconder regressão atrás de filtro de paths.
- **Antes de cada push para `main`**, rodar localmente o mesmo conjunto que o CI roda:

  ```bash
  cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy app/ && uv run pytest -q --no-cov
  pnpm --filter @auditoria/web lint && pnpm --filter @auditoria/web type-check && pnpm --filter @auditoria/web test
  ```

- **Se o CI falhar:**
  1. `gh run view <run-id> --log-failed` pra ler o erro real (não o "summary" — esse engana).
  2. Reproduzir local com o **mesmo comando do CI** (`uv run pytest -v --cov=app --cov-report=term-missing` no API; coverage muda a quantidade de testes que rodam).
  3. Push do fix **no commit seguinte** — nunca `git push --force` pra "limpar" CI vermelho do histórico.
- **Teste flaky** (passa local, falha CI): tratar como bug a ser deflakizado, **não** ignorar. Padrão de root cause comum: timestamp/relógio ms-resolution, ordem de fixture, dependência de rede mockada parcialmente. Documentar a causa no commit do deflake (ex: ver `1185e17`).
- **Hooks locais** (husky + lint-staged + commitlint) rodam no `git commit`. **Nunca** usar `--no-verify` pra contornar — se o hook falhar, o CI vai falhar igual. Conserta antes.
- **Quando um job for marcado como `skipped` no CI** (ex: `Web` quando o PR só toca API): isso é esperado pelo `paths-filter`. Mas o status do summary precisa ser verde — se vier vermelho num skip, é bug do workflow, **abrir antes de mergear**.

### Idioma

- **Código:** inglês.
- **Comentários/docstrings:** português quando clarificam domínio de negócio; inglês para tecnologia pura.
- **Mensagens ao usuário final:** **sempre** português.

---

## 8. Mapa de Sessões (referência rápida)

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
| **S10** | Processamento async (BG Tasks)     | BACK 8.1–8.6 · FRONT 8.7      |
| **S11** | Revisão — backend + cache L1       | BACK 9.1–9.10                 |
| **S12** | Revisão — estrutura + aba 1        | FRONT 9.11–9.14               |
| **S13** | Revisão — abas 2, 3, 4             | FRONT 9.15–9.17               |
| **S14** | Exportação Excel                   | BACK 10.1                     |
| **S15** | Tipos de anomalia                  | BACK 11.1 · FRONT 11.2        |
| **S16** | Hardening de segurança             | — (transversal)               |
| **S17** | Observabilidade                    | — (transversal)               |
| **S18** | E2E + deploy + docs                | — (finalização)               |
| **S19** | Qualificação (`qualification`)     | BACK 12.1 · FRONT 12.2        |

> **S20+ (pivot — auditoria contínua sobre o Omie):** eixo S20–S27, em planejamento. Não está na tabela acima; ver [Docs/PLANO_S20_AUDITORIA_CONTINUA.md](Docs/PLANO_S20_AUDITORIA_CONTINUA.md).

---

## 9. Comandos Frequentes

Preferir os **scripts pnpm da raiz** (ver `package.json`); `make` não funciona no ambiente.

```bash
# Dev local (scripts pnpm da raiz)
pnpm infra:up        # docker compose: postgres (sem Redis desde a FASE 0)
pnpm dev:api         # uvicorn app.main:app --reload (processa conciliação via BackgroundTasks)
pnpm dev:web         # Next.js dev

# DB
pnpm db:migrate      # alembic upgrade head
pnpm db:seed         # python -m scripts.seed_dev
cd apps/api && uv run alembic revision --autogenerate -m "descrição"

# Lint / type / test (mesmo conjunto do CI — ver §7)
cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy app/ && uv run pytest -q --no-cov
pnpm --filter @auditoria/web lint && pnpm --filter @auditoria/web type-check && pnpm --filter @auditoria/web test
```

---

## 10. Pontos em Aberto (não decidir sozinho)

Quando o usuário não tiver decidido, **pergunte** antes de presumir:

**Decididos:**

- [x] ~~Framework Python~~ → **FastAPI** _(24/04/2026)_
- [x] ~~Job runner~~ → **`BackgroundTasks` do FastAPI** _(FASE 0, 16/06/2026 — ARQ/Redis removido por overengineering; antes era ARQ)_
- [x] ~~PM Python~~ → **uv** | ~~PM Frontend~~ → **pnpm** _(24/04/2026)_
- [x] ~~Monorepo vs polyrepo~~ → **Monorepo simples** _(24/04/2026)_
- [x] ~~Ambiente de staging/deploy~~ → **Google Cloud Run** (GCP `liberdade-assessoria`, `southamerica-east1`); dev no ar.
- [x] ~~Credenciais Omie sandbox~~ → **não existe sandbox no Omie.** Testes contra conta real (Quial), localmente; **nunca** comitar credenciais; rotacionar se vazar.

**Ainda em aberto (aguardando stakeholder / a confirmar):**

- [ ] Chave Anthropic com budget de longo prazo (parsing roda em dev, mas confirmar limite). _S9_
- [ ] Paginação de `ListarExtrato` (doc Omie incompleta — validar com Galhardo). _S5_
- [ ] `ListarContasPagar.filtrar_por_status` aceita múltiplos valores? _S5_
- [ ] Endpoint Omie que expõe saldo em data específica (fallback de `balance_start`). _S10_
- [ ] Política de senhas (rotação, complexidade). _S4_
- [ ] Ambiente de **produção** (o de dev já roda no Cloud Run; falta promover/configurar prod). _S18_

**Novos (do PRD FASE 0–5 — detalhe em [Docs/PLANO_PROXIMOS_PASSOS.md](Docs/PLANO_PROXIMOS_PASSOS.md)):**

- [x] ~~**Tolerância de data zero** também para conta corrente~~ → **SIM, aprovado + implementado** (FASE 1 / BACK 1.6): exato → `conciliado`; 1–3 dias → `conciliado_data_divergente` + `wrong_date`; > 3 → `sem_omie`. Vale p/ CC e cartão (`DATE_DIVERGENCE_RANGE=3` fixo). Na branch de integração; muda o comportamento da CC em prod quando a FASE 1 for mergeada. Ver §5.2.
- [ ] **Quebra do invariante "Omie read-only":** aprovar escrita no Omie (`IncluirContaPagar`) só no fluxo de lançamento de cartão. Endpoint/campos/idempotência a confirmar contra Omie real. _FASE 2 / S24_
- [ ] **Cloud Run `--no-cpu-throttling` + `min-instances ≥ 1`** na API após remover Redis (senão BackgroundTasks congela). Custo aceitável? _FASE 0 / S20_
- [ ] **Pluggy interna vs Cubos** (proposta Arthur Souza, 16/06) + cobertura de Sicredi/BNB/Cora + primeiro endpoint público (webhook). _FASE 4_
- [ ] **Campo de departamento/rateio** na response Omie (bloqueia check `sem_departamento`); Slack (app vs webhook) e provedor de email; persona supervisor (role nova vs reuso). _FASE 5_

---

## 11. Estilo de Trabalho Preferido

- **Sessões focadas:** implementar uma sessão (S0, S1, ...) por vez, não pular.
- **Qualidade > velocidade:** seguir os melhores padrões de mercado, mesmo que demore mais.
- **Reuso obrigatório:** tudo que pode ser abstraído, deve ser. Sem duplicação.
- **Sem código mal feito:** prefira interromper e perguntar a entregar algo frágil.
- **Segurança é inegociável:** nunca corte caminho em segurança.
- **Escalabilidade é considerada:** arquitetura horizontal-ready desde o MVP (ver §6 do PLANO).
- **Nada "temporário":** se é pra ficar, faça direito desde o começo. Se é debug, tire antes do commit.

---

## 12. Comunicação ao Final de Tarefa

Toda vez que Claude termina uma tarefa solicitada pelo usuário, a resposta final
**DEVE** conter duas partes nesta ordem:

1. **Resumo executivo** — bullets curtos de "o que mudou" (arquivos novos /
   modificados, tamanho do diff, hash do commit, status do CI).
2. **Passo a passo de teste** — instruções detalhadas para o usuário validar
   a entrega manualmente:
   - Comandos exatos (assumir **Windows + Git Bash**, `uv` em
     `~/.local/bin`, `pnpm` via corepack — ver `MEMORY.md`).
   - Estado esperado em cada passo: o que deve aparecer na tela, o que deve
     sair no log, o que deve voltar do endpoint.
   - Caminhos felizes **e** pelo menos um caminho de erro relevante
     (validação Zod, RBAC, conflito 409, falha de Omie etc).
   - Se algo não pode ser testado agora (ex: sem credenciais Omie sandbox),
     explicitar a limitação e dizer o que será coberto quando o pré-requisito
     chegar.

Evite "você já sabe" — o usuário pode voltar à entrega depois de dias e não
lembrar dos comandos.

---

## 13. Atualização deste Arquivo

**Manter este arquivo atualizado é obrigação contínua — parte do _Definition of Done_, não um extra.** Sempre que uma tarefa disparar um dos gatilhos de _"Quando atualizar"_ abaixo, atualize o CLAUDE.md **na mesma entrega** (mesmo PR/commit que fez a mudança) e ajuste o rodapé de versão. Não acumule "atualizo depois": primer desatualizado induz erro nas próximas conversas e custa mais caro que um parágrafo a mais. Na dúvida se algo se qualifica, trate como gatilho — ou **pergunte** (§6, §10). Esse passo conversa com a §12: o fim de tarefa é o momento natural de revisar se o primer precisa mudar.

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

_Versão 1.5 — 19/06/2026. **FASE 1 / BACK 1.6 (tolerância de data fixa) na branch de integração `feat/fase1-cartao`:** a tolerância deixou de ser parametrizável — `DATE_DIVERGENCE_RANGE = 3` fixo no matcher. Classificação: data exata → `conciliado`; 1–3 dias → `conciliado_data_divergente` (+ `wrong_date`); > 3 → `sem_omie`. Vale para CC **e** cartão (muda comportamento da CC em prod quando a FASE 1 for mergeada). §5.2/§5.3 reescritos como lei atual, nota do topo e ponto em aberto §10 marcados resolvidos. `date_tolerance_days` removido do request (ignorado se enviado); coluna mantida (novas sessões = 0). O range fixo também rege a janela Omie no processamento, na revisão e no export. Gate local verde (ruff/mypy/pytest — matcher 20, + regressão CC e divergência no job). **Ainda não na `main`** (integração)._

_Versão 1.4 — 16/06/2026. **FASE 0 / S20 (BACK 0.1) aterrissou:** Redis/ARQ removido — background jobs agora via `BackgroundTasks` nativo do FastAPI; cache de lançamentos virou **L1-only** (L2 Redis existia só p/ coerência com o worker separado, que não existe mais). §2 (stack/infra), §8 (mapa S10/S11), §9 (comandos) e §10 (job runner) atualizados como lei atual. Teto do processamento via `asyncio.timeout(RECONCILIATION_TIMEOUT_SECONDS=900)` + cron de cleanup como rede de segurança. Gate local verde (ruff/mypy/507 pytest). §5 (tolerância de data) **não** alterado de propósito — muda só na FASE 1. ⚠️ Cloud Run da API exige `--no-cpu-throttling` + `min-instances ≥ 1` (§10)._

_Versão 1.3 — 15/06/2026. Reordenação do roadmap pelo PRD de 15/06 (FASE 0–5). Status (§ topo) e Fontes da Verdade (§1) passam a apontar o plano vigente [Docs/PLANO_PROXIMOS_PASSOS.md](Docs/PLANO_PROXIMOS_PASSOS.md); o [PLANO_S20_AUDITORIA_CONTINUA.md](Docs/PLANO_S20_AUDITORIA_CONTINUA.md) foi marcado **superseded** (absorvido na FASE 5). §10 ganhou os pontos em aberto do PRD. Os 2 bugs da FASE 0 já estavam resolvidos (auth #19, timeout #16)._

_Versão 1.2 — 11/06/2026. Adicionada a obrigação contínua de manter este primer atualizado (§13, parte do Definition of Done). Varredura de stack contra o código: corrigido o formatter (`ruff format`, não black) em §2/§7, completada a lista de regras ruff (`+ S, A, ASYNC, ANN, PT, TID`), FastAPI alinhado para 0.115+ e `hypothesis` incluído no conjunto de testes. Status S0–S19 em dev e eixo S20+ revalidados contra git log e estrutura de `apps/api`._

_Versão 1.1 — 09/06/2026. Atualizado o status (S0–S19 em dev), worker (ARQ, não Celery), deploy (Google Cloud Run/GCP) e o eixo S20+. Alinhado à documentação em `Docs/documentation/`, ao plano em `Docs/PLANO_IMPLEMENTACAO.md` e ao pivot em `Docs/PLANO_S20_AUDITORIA_CONTINUA.md`._
