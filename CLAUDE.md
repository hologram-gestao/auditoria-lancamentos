# CLAUDE.md — Sistema de Auditoria de Lançamentos (Hologram)

> **Para futuras conversas com Claude:** este arquivo é o _primer_ obrigatório. Leia-o antes de qualquer ação. Ele é atualizado continuamente conforme decisões são tomadas.
>
> **Status do projeto:** 🚀 **S0–S19 + Sprints 0–5 do agents-hub estão na `main` e rodando em dev** no Google Cloud Run (GCP `liberdade-assessoria`, região `southamerica-east1`). Acesso pelas URLs `*.run.app` via **BFF reverse-proxy do Next** — não há custom domain (o BFF resolveu o cookie cross-site, então o DNS na Wix nunca foi necessário). **Não trate mais como greenfield:** o código é a fonte da verdade — leia antes de assumir que algo "ainda precisa ser criado".
>
> ⚠️ **O sistema é MULTI-TENANT desde a Sprint 5.** Usuários do cliente final logam e enxergam **apenas o próprio tenant**. Antes de escrever qualquer query, endpoint ou tela que toque dado escopável, leia **§3.15 (autorização por tenant)**, **§4.8 (modelo de tenancy)** e **§4.9 (matriz de permissões)**. Endpoint novo que esqueça o filtro de tenant é vazamento entre clientes — a Sprint 5 fechou 34/34 endpoints sensíveis e essa cobertura não pode regredir.
>
> **O que cada sprint do agents-hub entregou** (todas na `main`):
>
> | Sprint | Entrega                                                                                                      |
> | ------ | ------------------------------------------------------------------------------------------------------------ |
> | **1**  | Conciliação de **fatura de cartão** + conta aplicação/CDB (`account_type`)                                   |
> | **2**  | Fim da perda silenciosa de dado no parsing (CSV grande, XLSX truncado)                                       |
> | **3**  | **Cripto por cliente** (DEK+KEK), `access_audit`, alerting fail-closed                                       |
> | **4**  | Lista de conciliações, **gaveta** de criação, **multi-arquivo**, notificações in-app, `usage_events`         |
> | **5**  | **Multi-tenancy**: `users.scope`/`client_id`, papéis de cliente, matriz de permissões, isolamento por tenant |
> | **6**  | **Glossário por cliente** (tabela cifrada por tenant, `glossary_version`), veredito do revisor               |
> | **7**  | **Escrita no Omie**: lançamento das compras `sem_omie` da fatura de cartão (`IncluirLancCC`)                 |
>
> ⚠️ **O invariante "Omie read-only" acabou na Sprint 7.** O ADL agora **grava movimento
> financeiro na contabilidade do cliente**. Antes de encostar nesse fluxo, leia **§3.16**:
> ele nasce **desligado** (`OMIE_POSTING_ENABLED=false`), o contrato do `IncluirLancCC`
> segue **NÃO-VERIFICADO** contra a API real, e o critério de rollback é **um único
> lançamento duplicado em produção**.
>
> **Roadmap (PRD 15/06/2026 → FASE 0–5):** plano em [Docs/PLANO_PROXIMOS_PASSOS.md](Docs/PLANO_PROXIMOS_PASSOS.md). **FASE 0 ✅** (Redis/ARQ removido, `BackgroundTasks`). **FASE 1 ✅ na `main`** — tolerância de data fixa (`DATE_DIVERGENCE_RANGE = 3` em `processing/matcher.py`) e cartão. **FASE 2** lançamento de fatura no Omie (Sprint 7). **FASE 3** glossário por cliente (**Sprint 6 — a próxima**). **FASE 4** Open Finance (Pluggy). **FASE 5** rotinas automáticas de auditoria.

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

1. **Nunca** armazene `OMIE_ENCRYPTION_KEY`, `JWT_SECRET`, `ANTHROPIC_API_KEY`, `ALERT_WEBHOOK_URL` — nem o material da **KEK/DEK** (Sprint 3) — em código, banco, log ou resposta. Apenas env vars / GCP Secret Manager / Cloud KMS. O `alerting` loga só o `code` do alerta, nunca a URL do webhook.
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
14. **Landmines do envelope crypto por cliente (Sprint 3) — quebrar = outage/perda de dado:**
    - **Nunca trocar `KEK_KEY_ID`** (manter `k1`). O decrypt sempre usa a KEK
      atual e a rotação só provisiona DEK quando `dek_wrapped IS NULL` — não há
      caminho de re-embrulho no código. Trocar = todos os clientes ficam
      indecifráveis.
    - **Manter `OMIE_ENCRYPTION_KEY` sempre setada** — lê dado bare-legado e
      deriva a KEK local (dev/test). Removê-la = outage.
    - **Nunca ligar/desligar Cloud KMS sobre um DB que já tenha DEKs do outro
      wrapper** (KMS ⇄ local) — sem migração no código = outage.
    - **Alerting é fail-closed em staging/prod:** sem canal entregável
      (`ALERT_WEBHOOK_URL`/`ALERT_EMAIL_TO`) o serviço **não sobe**
      (`verify_alert_config` no lifespan). Em dev degrada com warning.
    - **O canal do sintético não é canal de plantão.** O alerta
      `AlertCode.SYNTHETIC` (gate de deploy, dispara a cada push na `main`) sai
      por `ALERT_WEBHOOK_URL_SYNTHETIC` quando ela existe — e **só** por ela, sem
      e-mail de plantão junto; vazia, cai no canal de plantão como antes. Essa
      URL **nunca** entra em `has_webhook_alert`/`has_alert_channel`
      ([apps/api/app/core/config.py](apps/api/app/core/config.py)): contá-la
      deixaria subir em prod um serviço cujo único canal é o de teste — alerta
      real sem para onde ir. Só o sintético desvia; qualquer alerta novo nasce no
      plantão.
15. **Autorização por tenant (Sprint 5) — a regra mais fácil de furar sem perceber:**
    - **O tenant vem SEMPRE da LINHA do usuário**, nunca de `client_id` recebido
      em URL, query ou body. O JWT carrega `scope`/`client_id`, mas a autoridade
      é a linha já lida por `get_current_user` (a mesma leitura que checa
      `active`) — assim revogação vale no request seguinte, sem esperar o token
      expirar, e sem query nova.
    - **Existe UMA decisão de acesso: `resolve_client_access`** em
      [apps/api/app/core/authz.py](apps/api/app/core/authz.py). Rota e camada de
      dados consultam **ela**. **Proibido** escrever uma segunda implementação da
      regra — se você está prestes a comparar `role`/`client_id` na mão, pare e
      use a função.
    - **Defense-in-depth na camada de dados:** negar na rota é necessário e
      **não** suficiente. Toda query de coleção passa por `scoped_by_tenant(...)`
      e todo detalhe por PK carrega `AND client_id = <tenant do usuário>` no
      próprio `SELECT` — recurso de outro tenant vira **404**, nunca o dado.
      `tenant_filter_client_id(user)` devolve o tenant a forçar no `WHERE`.
    - **Negação não vaza o alvo:** 403 (ou 404 onde a conversão anti-enumeração
      já existe) com corpo **sem nome, razão social ou CNPJ** do tenant alvo, e
      **1** linha em `access_audit`.
    - **Endpoint novo que lê dado escopável entra na lista canônica**
      [apps/api/app/core/sensitive_endpoints.py](apps/api/app/core/sensitive_endpoints.py)
      (**40** hoje) **com teste negativo cross-tenant**. Essa lista é o denominador
      da métrica de isolamento — endpoint fora dela é buraco que ninguém mede.
    - **Identidade de usuário em response é ENXUTA e mascarada por escopo**
      (86e2n39f1): expor QUEM fez algo devolve só `{name, email}` — nunca a
      linha de `users` (§3.2), nem `id` — e passa por **`author_for_viewer`**
      (`reconciliations/service.py`), a decisão ÚNICA: usuário de tenant vendo
      autor `system` recebe **"Equipe Hologram"** sem e-mail. A máscara é do
      SERVIDOR — payload com o nome real e UI escondendo não é barreira (§4.9).
      Vale para qualquer endpoint novo que exponha autoria.
16. **Escrita no Omie (Sprint 7) — a única no sistema, e a mais cara de errar:**
    - **Nasce desligada.** `OMIE_POSTING_ENABLED` tem default **`False`**
      (diferente de `QUALIFICATION_ENABLED`): ligar é decisão explícita **por
      ambiente**, via `--update-env-vars` no Cloud Run, sem deploy. Ligar num
      ambiente exige que ele rode o código do contrato verificado (abaixo) —
      o payload antigo (plano) é recusado pela Omie.
    - **O contrato do `IncluirLancCC` foi VERIFICADO contra a API real em
      21/08/2026** (captura na conta Hologram; fixtures em
      `apps/api/tests/fixtures/omie/`, anonimizadas). O que a evidência diz:
      `param` **ANINHADO** (`cCodIntLanc` no topo + `cabecalho{nCodCC, dDtLanc,
nValorLanc}` + `detalhes{cCodCateg, cTipo, cObs}`); `nValorLanc` é
      **NÚMERO JSON** (string dá `3102`); `cTipo` é obrigatório na prática
      (enviamos `DIN`); **não existe `cNatureza` na escrita** — valor absoluto
      com categoria de despesa aterrissa como **débito** (extrato devolve
      natureza `P` e valor negativo). O formato plano anterior foi recusado com
      `5001` — evidência preservada no histórico da captura. O gate
      `apps/api/tests/unit/test_omie_fixtures.py` agora **roda verde contra as
      fixtures reais** e FALHA se o DTO divergir delas.
    - **Estorno é BLOQUEADO** (`estorno_nao_verificado`, servidor + UI): sem
      campo de sinal no contrato, a representação do **crédito** segue
      não-verificada — lançar estorno no palpite poderia registrá-lo como
      segunda despesa. Só compra (valor negativo) é elegível.
    - **A dedup primária é do ADL — e a do fornecedor agora é FATO:** o
      `IncluirLancCC` é **idempotente sobre `cCodIntLanc`** (2º POST devolve o
      MESMO `nCodLanc`, status 0, sem criar nada — verificado 21/08/2026).
      Ainda assim, antes de qualquer POST o serviço registra a INTENÇÃO em
      `reconciliation_omie_postings` e consulta o **próprio** estado.
      `cCodIntLanc` é derivado da **identidade da linha** (`file_entry_id`),
      **nunca do conteúdo**: duas compras idênticas na mesma fatura têm de
      virar **dois** lançamentos.
    - **Timeout nunca reenvia às cegas.** ⚠️ Verificado 21/08/2026: o
      `ListarExtrato` **NÃO devolve `cCodIntLanc`**, então a reconciliação
      pós-timeout por esse caminho é sempre **inconclusiva ⇒ não reenvia**
      (linha fica travada com "confira no Omie"). A idempotência provada acima
      permitiria reenviar com segurança — **mudar isso é decisão em aberto
      (§10), não implementada**. `faultstring` (a Omie responde **HTTP 200** em
      erro) é falha: **nada** é marcado como lançado.
    - **A mensagem de erro do provedor é persistida e NUNCA logada** — é texto
      livre de terceiro e a Omie ecoa o `cObs`, que carrega a descrição da compra
      (§4.5). No `usage_events` entra só uma **categoria fechada**, nunca o texto.
    - **Só cartão.** Elegibilidade é `session.account_type == 'credit_card'`
      (o `CR` do Omie). ⚠️ O PRD chama a conta de cartão de `CA` e **está errado**:
      `CA` é Conta Aplicação. Filtrar por `CA` lança na conta errada.

---

## 4. Regras Invioláveis de Dados

1. **Campos criptografados (AES-256-GCM, envelope com DEK por cliente — Sprint 3):**
   Cada cliente tem uma **DEK** própria (Data Encryption Key), guardada
   **embrulhada** em `clients.dek_wrapped`; a **KEK** (Key Encryption Key) faz
   wrap/unwrap da DEK — **Cloud KMS** em staging/prod (a KEK nunca sai do KMS,
   recurso em `KEK_KMS_KEY_NAME`), **wrapper local** derivado de
   `OMIE_ENCRYPTION_KEY` via HKDF (domínio separado) em dev/test. O envelope
   (`v<ver>:<key_id>:<hex>`) liga o ciphertext ao cliente via **AAD** —
   ciphertext de um cliente **não** decifra em outro. Dado bare-legado ainda é
   lido com a `OMIE_ENCRYPTION_KEY`. Falha de decrypt **levanta erro** no
   caminho de credencial (nunca retorna texto); na tela de revisão/Excel vira
   `[indecifrável]` + métrica `decrypt_failed` (não célula silenciosamente
   vazia sem sinal). Campos:
   - `clients.omie_app_key_encrypted`, `omie_app_secret_encrypted`
   - `reconciliation_file_entries.description_encrypted`, `user_note_encrypted`
   - `reconciliation_omie_entries.user_note_encrypted`
   - `reconciliation_anomalies.context_encrypted`, `resolution_note_encrypted`
2. **IV novo a cada operação** (12 bytes aleatórios). Nunca reutilize.
3. **Valores monetários em claro** (campos `amount`, `balance`) — são números sem identificação, sem valor isolado.
4. **Datas em claro** (`transaction_date`, `reference_month`) — necessárias para SQL ordering/filtering.
5. **Nenhum dado identificável do cliente final persiste em claro** — CNPJ, razão social, fornecedores, categorias, nomes de contas são **sempre buscados do Omie em tempo real** e mantidos apenas em cache com TTL.
6. **Arquivo original nunca persiste** — processado em memória e descartado.
7. **Trilha de acesso (`access_audit`, LGPD — Sprint 3 + 5):** toda visualização,
   exportação ou negação de acesso a relatório grava 1 linha com **só IDs**
   (`user_id`, `client_id`, `session_id`, `action ∈ {denied, view, export}`,
   `rota`, `timestamp`) — **nunca PII** (CNPJ, razão social, nomes). A Sprint 5
   acrescentou **`user_scope`** e **`actor_client_id`**: numa negação
   cross-tenant dá para saber o escopo e o tenant **do ator**, além do alvo.
   Navegação dentro do próprio tenant **não** gera linha — a trilha não infla
   com uso normal.
8. **Modelo de tenancy (Sprint 5 — `users`):** uma tabela só, sem segundo
   mecanismo de sessão.
   - `scope = 'system'` → equipe Hologram; `client_id` **NULL**; escopo é a
     carteira (`client_assignments`).
   - `scope = 'client'` → usuário DO cliente; `client_id` **obrigatório** e é o
     tenant dele.
   - A integridade é **do banco**, não só da aplicação:
     `ck_users_scope_client_id`. Fonte única do enum e do CHECK:
     [apps/api/app/db/models/user.py](apps/api/app/db/models/user.py).
   - Papéis: `admin` e `manager` (sistema); `client_manager` e `client_operator`
     (cliente). O papel do payload de criação é **whitelist** — `admin`/`manager`
     forjados são rejeitados.
9. **Matriz de permissões (Sprint 5):** declarativa e ÚNICA em
   `PERMISSION_MATRIX` ([apps/api/app/core/authz.py](apps/api/app/core/authz.py)),
   consultada por `has_permission`. No front, o espelho é
   `apps/web/src/lib/authz.ts` — **um** helper, nunca `if (role === ...)` espalhado
   por componente.

   | Ação                         | client_manager | client_operator | admin | manager       |
   | ---------------------------- | -------------- | --------------- | ----- | ------------- |
   | Criar/rodar conciliação      | ✅             | ✅              | ✅    | ✅            |
   | Revisar / exportar           | ✅             | ✅              | ✅    | ✅            |
   | Sincronizar contas do Omie   | ✅             | ✅              | ✅    | ✅            |
   | Gerir usuários do cliente    | ✅             | ❌              | ✅    | ❌            |
   | Editar dados do cliente (§9) | ❌             | ❌              | ✅    | ❌            |
   | Ver outro tenant             | ❌             | ❌              | ✅    | ✅ (carteira) |

   **A UI não é barreira de segurança** — o backend é. Mas **mostrar ação que o
   servidor nega é defeito**: cada ❌ precisa de bloqueio no backend **e** de
   ação oculta na tela.

10. **Uma conciliação = conta + mês (Sprint 4):** unicidade
    `UNIQUE(client_id, omie_conta_id, reference_month)` (parcial, ativas —
    `uq_recon_sessions_account_month`). O hash desceu de nível: cada parte é uma
    linha em `reconciliation_files` com `UNIQUE(session_id, file_hash)`.
    Recriar a mesma conta+mês → **409** pedindo para anexar à existente.

11. **Intenção de lançamento no Omie (Sprint 7):** `reconciliation_omie_postings`
    — tabela própria, **não** colunas em `reconciliation_file_entries`. Ela nasce
    ANTES do POST, sobrevive a timeout, acumula `attempts` e guarda o erro do
    fornecedor; a `file_entry` continua carregando só o **resultado**. Duas
    garantias **no banco** (não na aplicação): `UNIQUE(file_entry_id)` — uma
    intenção por linha, o que torna o registro idempotente sob concorrência via
    `ON CONFLICT DO NOTHING` — e `UNIQUE(client_id, cod_int_lanc)`. `client_id` é
    desnormalizado de propósito: toda query filtra por ele (§3.15) sem depender
    de um JOIN que alguém pode esquecer.

---

## 5. Regras Invioláveis de Domínio (Matching)

1. **Tolerância de valor:** `|a − b| ≤ 0.01 BRL`. Hard-coded, não parametrizável.
2. **Tolerância de data:** **fixa, não parametrizável** (FASE 1) — constante `DATE_DIVERGENCE_RANGE = 3` no matcher. Classificação por `|days_diff|`: `== 0` → `conciliado` (data exata); `1–3` → `conciliado_data_divergente` (+ anomalia `wrong_date`); `> 3` → sem match (linha fica `sem_omie`). Vale para conta corrente **e** cartão. O request não aceita mais `date_tolerance_days` (ignorado se enviado); a coluna homônima é mantida só por histórico e novas sessões gravam 0.
3. **Período Omie expandido:** `[period_start − DATE_DIVERGENCE_RANGE, period_end + DATE_DIVERGENCE_RANGE]` (3 dias fixos) — vale no processamento (`job.py`), na tela de revisão (`/available-omie-entries`) e no export.
4. **Um OmieEntry só matcha uma Movement.** Controle via `set(used_ids)` durante o cruzamento.
5. **Cruzamento em passadas por proximidade de data.** O matcher percorre
   `|days_diff|` de `0` até `DATE_DIVERGENCE_RANGE`: fecha **todos** os pares de
   data exata, depois os de 1 dia, e assim por diante. Dentro de uma passada,
   as linhas do arquivo decidem em ordem `(transaction_date, id)` e o desempate
   entre candidatos é menor `|amount_diff|` → **maior afinidade de fornecedor**
   → `date asc`. **A ordem de leitura do arquivo não afeta mais o resultado.**
   A afinidade (`processing/name_affinity.py`) conta quantos tokens
   significativos do fornecedor Omie aparecem na descrição do extrato — sem
   limiar, sem fuzzy e **sem IA** (§5.9). É **só desempate, nunca exclusão**:
   nome que não bate jamais impede um match, porque descrição de extrato é texto
   sujo e transformá-la em `sem_omie` trocaria um falso positivo por outro pior.
   Só o extrato traz o nome — títulos de `ListarContasPagar/Receber` devolvem
   apenas o código do cliente, então para eles a afinidade é sempre 0. Fornecedor
   e descrição trafegam **em memória** e não são persistidos nem logados (§4.5).
   O motivo é um defeito real: com o laço guloso antigo (uma passada só, cada
   linha pegando seu melhor candidato livre), uma linha cuja contraparte não
   casa por valor — tipicamente porque o pagamento está **dividido** em duas
   parcelas no Omie e o cruzamento é 1-para-1 — levava o lançamento de outra
   linha dentro dos 3 dias. A linha roubada virava `sem_omie` e a qualificação
   acusava incoerência na primeira, por comparar fornecedores diferentes: **um
   pareamento errado gerava duas anomalias falsas.**
6. **Normalização Omie:** `cNatureza='D'` → valor negativo; `cNatureza='C'` → positivo. ⚠️ **Cartão usa OUTRA convenção (fixture real, 21/08/2026):** extrato de conta `CR` devolve natureza `'P'` (pagamento) / `'R'` (recebimento) com `nValorDocumento` **JÁ SINALIZADO** (P negativo, R positivo). `LancamentoExtrato.signed_amount` cobre as duas por construção — só inverte `'D'`; **inverter qualquer outra natureza quebraria o cartão**.
7. **Status Omie considerados no matching** (canônico no DB, camelCase): `Conciliado`, `Atrasado`, `Previsto`. Ignorar cancelados. **Atenção à nomenclatura mista da Omie:** o canônico vem de `ListarExtrato.cSituacao`; já o FILTRO `filtrar_por_status` em `ListarContasPagar/Receber` usa o enum oficial Omie em UPPERCASE (`ATRASADO`, `AVENCER`, etc) — `"PREVISTO"` NÃO é valor válido como filtro, devolve 5xx. Mapping: filtro `AVENCER` → canônico `Previsto`. ⚠️ Lançamento **recém-criado** volta no extrato **sem `cSituacao`** (evidência 21/08/2026) — o campo é opcional no schema e vira `""` no `OmieMovement`: não casa com nenhum canônico e não dispara regra.
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
- **Conteúdo dentro de área de altura fixa** (as telas do shell do cliente, `min-h-0 flex-1` numa seção `flex h-full flex-col`): `min-h-0 flex-1` **autoriza** o container a encolher abaixo da altura do conteúdo — sem `overflow` o conteúdo é pintado FORA da caixa e o que vier depois (barra de paginação, rodapé) cobre o que vazou. Duas receitas, uma por tipo de conteúdo:
  - **card / conteúdo não-tabular** → `<ScrollRegion>` (`components/ui/scroll-region.tsx`);
  - **tabela** → `<TableCard>` + `<Table fill>` (`components/ui/table.tsx`). O wrapper que o `<Table>` já cria rola só no HORIZONTAL (altura de conteúdo); `fill` é o que o torna o scroller vertical e gruda o cabeçalho, e `TableCard` é quem limita a altura. Nunca pôr `overflow` no container de fora: dois containers roláveis aninhados espremem as colunas em 390px em vez de rolar (ADR-007-FE).

  Região rolável **sem** `tabIndex`/`role`/`aria-label` reprova `scrollable-region-focusable` (SERIOUS) no gate `web_a11y` — por isso a decisão mora num componente só, nunca copiada na tela.

- **Acessibilidade:** shadcn/ui entrega; em componentes custom, revisar `aria-*` e suporte a teclado.
- **Validação visual é ABRIR a imagem — gate verde não substitui.** `axe-core` audita
  semântica e contraste e **não mede transbordo de layout**; jsdom (vitest) não tem layout;
  `tsc`/eslint não enxergam render. Toda task de UI termina com screenshot **desktop e
  390px** aberta e conferida contra: ação primária cortada na borda, elemento pintando fora
  do card, gaveta cortada, coluna espremida. Na Sprint 7 os três gates ficaram verdes com o
  botão "Confirmar e lançar" **clipado** no rodapé da gaveta em 390px — o defeito só
  apareceu na leitura do PNG. Quando um corte for corrigido, **trave-o com medida**
  (`boundingBox().x + width <= viewportSize().width`), não com inspeção manual. O primeiro
  desses asserts vive no cenário mobile da gaveta de lançamento em
  `apps/web/e2e/a11y-mocked.spec.ts` — copie o padrão de lá, inclusive o
  `aguardarAnimacao()`: a gaveta do Radix entra **deslizando**, e `boundingBox()` medida no
  meio do trajeto devolve coordenada fora da tela que não é defeito nenhum (mede em 1440px
  também, e reprova os quatro cenários). O mesmo vale para COR: o toast do Sonner entra em
  fade, e o axe medindo no meio do trajeto vê cor MESCLADA — reprovou 4,25:1 num par cujos
  tokens puros dão 4,75:1 (PR #89, flaky sem defeito). Antes de medir toast,
  `aguardarToastEstavel()`. **Visível não é estável.**

- **Dica/tooltip NUNCA é `title` nativo** — não aparece em toque, não alcança teclado e o
  leitor de tela ignora. Use o `<Tooltip>` do design system com `role="img"` +
  `aria-label` carregando a explicação INTEIRA (anunciada mesmo sem abrir a dica) +
  `tabIndex={0}` com anel de foco. Padrão em `qualification-cell.tsx`,
  `situation-badge.tsx` e `author-label.tsx` — copiar de lá, não reinventar.
- **O relatório do gate de a11y é artefato, nunca fonte.** `scripts/a11y-gate.sh` escreve
  `apps/web/a11y-report.json` (o CI escreve o mesmo arquivo e o sobe como _artifact_,
  `ci.yml:300-304`). Ele **não entra em commit**: é reescrito a cada execução e carrega
  caminhos absolutos da máquina de quem rodou. Antes de fechar qualquer task de front,
  `git diff --name-only develop..HEAD` só pode listar código-fonte.
  A lacuna do `.gitignore` foi fechada na validação da Sprint 7 (task `86e2w8xpv`):
  `apps/web/.gitignore` ignora `a11y-report.json` — o arquivo não entra mais em commit por
  acidente. A conferência de `git diff --name-only` antes de fechar task de front continua
  valendo como higiene.

### API

- **Response de sucesso:** `{ "data": {...} }` ou `{ "data": [...], "pagination": {...} }`.
- **Response de erro:** `{ "error": { "code", "message", "userMessage" } }`.
- **Rotas:** `/api/v1/...`.
- **Paginação:** `?page=1&pageSize=20`, max 100. No FastAPI, o parâmetro **precisa** de `alias="pageSize"` (`page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20`). Sem o alias o nome que entra pela URL vira `page_size`, o `pageSize` do front é descartado **sem erro** e o servidor devolve o default — o seletor de itens-por-página fica de enfeite. Foi o que aconteceu nas 3 rotas da revisão até 14/08/2026 (86e2u512z).
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
- **Teste de integração NOVO roda na ordem do CI antes do push** — o arquivo inteiro +
  os vizinhos que semeiam os mesmos dados (a ordem é alfabética por arquivo). Seed de
  tabela compartilhada entre arquivos (ex.: `anomaly_types`) é **get-or-create, nunca
  insert às cegas**: a tabela sobrevive entre arquivos e o insert cego morre com
  `UniqueViolation` só no CI (foi o único vermelho da PR #84; os helpers dos arquivos de
  teste já fazem certo — copiar deles).
- **Teste flaky** (passa local, falha CI): tratar como bug a ser deflakizado, **não** ignorar. Padrão de root cause comum: timestamp/relógio ms-resolution, ordem de fixture, dependência de rede mockada parcialmente. Documentar a causa no commit do deflake (ex: ver `1185e17`).
- **Hooks locais** (husky + lint-staged + commitlint) rodam no `git commit`. **Nunca** usar `--no-verify` pra contornar — se o hook falhar, o CI vai falhar igual. Conserta antes.
- **Quando um job for marcado como `skipped` no CI** (ex: `Web` quando o PR só toca API): isso é esperado pelo `paths-filter`. Mas o status do summary precisa ser verde — se vier vermelho num skip, é bug do workflow, **abrir antes de mergear**.

### Idioma

- **Código:** inglês.
- **Comentários/docstrings:** português quando clarificam domínio de negócio; inglês para tecnologia pura.
- **Mensagens ao usuário final:** **sempre** português.
- **Mensagem de commit:** **inglês (EN-US)**, corpo e rodapé inclusive. O formato Conventional Commits (§7 · Commits / Git) **não muda** — traduza a mensagem, não o padrão.
- **Nome de branch:** **inglês (EN-US)**, mantendo o padrão `fix/S17-redactor-token-counts`, `feat/S3-login-endpoint`.
- **Título e corpo de PR:** **sempre** português. A separação é deliberada: commit e branch são artefatos do repositório, o PR é a conversa do time.

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

### Sprints do agents-hub (numeração própria, todas na `main`)

Rodadas pelo orquestrador multi-agente; o escopo de cada uma vive no **Doc do
ClickUp**, não no repo. `make sprints` lista o estado.

| Sprint | Foco                                       | Deixou no código                                                          |
| ------ | ------------------------------------------ | ------------------------------------------------------------------------- |
| **0**  | Estabilização                              | —                                                                         |
| **1**  | Fatura de cartão + conta aplicação         | `account_type`, `DATE_DIVERGENCE_RANGE`                                   |
| **2**  | Parsing sem perda silenciosa               | CSV grande, XLSX completo                                                 |
| **3**  | Cripto por cliente, auditoria, alerta      | `clients.dek_wrapped`, `access_audit`, `core/kms.py`                      |
| **4**  | Lista, gaveta, multi-arquivo, notificações | `reconciliation_files`, `usage_events`, `notifications`                   |
| **5**  | Multi-tenancy e papéis de cliente          | `users.scope`/`client_id`, `core/authz.py`, `core/sensitive_endpoints.py` |
| **6**  | Glossário e classificação por cliente      | `client_glossary_entries`, `clients.glossary_version`, `review_verdict`   |
| **7**  | Lançamento de faturas no Omie              | `reconciliation_omie_postings`, `omie_posting/`, `OMIE_POSTING_ENABLED`   |

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
- [x] ~~**Quebra do invariante "Omie read-only"**~~ → **implementado na Sprint 7**, com
      **`IncluirLancCC`** (lançamento na própria conta do cartão) e **não**
      `IncluirContaPagar`. ✅ **A captura S-1 foi feita em 21/08/2026** (conta Hologram,
      cartão Inter): contrato aninhado verificado, idempotência de `cCodIntLanc`
      confirmada (2º POST devolve o mesmo `nCodLanc`), fixtures anonimizadas no repo e
      gate rodando verde. Ver **§3.16**. ⚠️ **O que ficou em aberto:**
      (a) **representação de ESTORNO** (crédito) na escrita — hoje bloqueado
      (`estorno_nao_verificado`); precisa de captura própria (categoria de receita?
      valor com sinal?); (b) **reenvio pós-timeout** — o `ListarExtrato` não devolve
      `cCodIntLanc`, então a reconciliação atual é sempre inconclusiva; a idempotência
      provada permitiria reenviar com segurança — decidir com o Pedro; (c) ligar
      `OMIE_POSTING_ENABLED` em dev **só depois** deste código deployado. _FASE 2 / S24_
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

_Versão 1.14 — 21/08/2026. **A captura S-1 aconteceu — o contrato de escrita do Omie deixou de ser suposição.** Rodada contra a conta real da Hologram (cartão Inter, três iterações guiadas por faultstring): o formato PLANO foi recusado (`5001`), o aninhado com `nValorLanc` string e sem `cTipo` caiu em `3102`, e o aninhado com **número JSON + `cTipo='DIN'`** foi ACEITO. **§3.16 reescrita como lei atual:** contrato verificado (aninhado, sem `cNatureza` na escrita, valor absoluto → débito), **idempotência de `cCodIntLanc` confirmada** (2º POST devolve o mesmo `nCodLanc`), **estorno bloqueado** (`estorno_nao_verificado`) até a representação do crédito ser capturada, e o caminho pós-timeout registrado como sempre-inconclusivo (o extrato NÃO devolve `cCodIntLanc`) com o reenvio idempotente como decisão em aberto (§10). **§5.6–5.7** ganharam as duas descobertas de leitura: extrato de CARTÃO usa natureza `P`/`R` com valor JÁ sinalizado (`signed_amount` cobre as duas convenções — só inverte `'D'`), e lançamento recém-criado volta **sem `cSituacao`** (campo agora opcional; exigi-lo derrubava o reprocessamento no dia de uma inclusão). Fixtures reais anonimizadas entraram em `apps/api/tests/fixtures/omie/` e o gate `test_omie_fixtures.py` roda verde contra elas — inclusive as 5 leituras, validadas contra resposta real pela primeira vez._

_Versão 1.13 — 22/08/2026. **O épico "Tela de revisão: confiança no que a tela mostra" (86e2n4tck) fechou 7/7 e deixou três regras novas.** **§3.15** ganhou a regra de identidade em response: autoria exposta é `{name, email}` mascarada por escopo via `author_for_viewer` — usuário de tenant vê "Equipe Hologram" para autor da equipe, e a máscara é do servidor. **§3.16** registra o cross-check da doc do `IncluirLancCC` (19/08): o `param` documentado é ANINHADO e sem `cNatureza` — o DTO plano não foi reescrito de propósito, e a recusa do 1º POST da captura passou a ser desfecho esperado e evidência válida. **§7** ganhou: tooltip nunca é `title` nativo (padrão `role="img"` + `aria-label` + `tabIndex`, três componentes já o usam); "visível não é estável" agora cobre COR (o axe medindo toast em fade reprova tokens que passam — `aguardarToastEstavel`); teste de integração novo roda na ordem do CI com seed get-or-create; e o parágrafo do `a11y-report.json` foi atualizado — a lacuna do `.gitignore` fechou na Sprint 7 (`apps/web/.gitignore`). Somas da aba Resumo, filtros server-side e o rótulo "Conciliadas (data exata)" são entregas do épico registradas nos PRs #79–#93, não regras novas — o que era regra ("fonte única de contadores", §5 intocada) só foi reafirmado._

_Versão 1.12 — 18/08/2026. **O ADL passou a ESCREVER no Omie (Sprint 7) — o invariante "Omie read-only" acabou, e essa é a mudança mais perigosa que este primer já registrou.** Nova regra **§3.16** com o que não pode ser errado: a feature nasce **desligada** (`OMIE_POSTING_ENABLED=false` por default, ao contrário de todo outro flag do projeto); o contrato do `IncluirLancCC` segue **NÃO-VERIFICADO** contra a API real (S-1) e o gate `tests/unit/test_omie_fixtures.py` **SKIPA citando S-1** em vez de passar verde; a **dedup primária é do ADL** (`reconciliation_omie_postings`, §4.11), nunca do fornecedor; `cCodIntLanc` vem da **identidade da linha**, nunca do conteúdo — chave de conteúdo colapsaria duas compras idênticas e deixaria dinheiro **faltando**, que o rollback não vigia; timeout **reconcilia antes de reenviar** e inconclusivo **não reenvia**; `faultstring` nunca é logada. Corrigido o número da lista canônica de endpoints sensíveis (**40**, não 34 — a Sprint 6 e a 7 entraram e o primer não acompanhou). **§8** ganhou o que a Sprint 6 (glossário) e a 7 deixaram no código, e **§10** marca a quebra do invariante como decidida, com a captura da fixture real explicitamente **ainda pendente**._

_Versão 1.11 — 11/08/2026. **O alerta sintético ganhou canal próprio (§3.14).** O gate de deploy dispara `AlertCode.SYNTHETIC` a cada push na `main` — prova de entrega, não incidente — e isso caía no canal de plantão várias vezes por dia. Com `ALERT_WEBHOOK_URL_SYNTHETIC` configurada, o sintético (e só ele) sai por um webhook separado, sem e-mail de plantão junto; sem a setting, nada muda. A parte que não pode ser errada: essa URL **não** conta em `has_webhook_alert`/`has_alert_channel` — canal de teste não substitui canal de plantão, e contá-la faria o fail-closed deixar subir um serviço mudo para alerta real._

_Versão 1.10 — 08/08/2026. **O matcher ganhou uma terceira dimensão: fornecedor (§5.5).** Ele conhecia valor e data, e mais nada — era o que sustentava a frase da Bruna de que o sistema cruzou o extrato de um fornecedor com o lançamento de outro "considerando apenas o valor". O dado existia e era descartado na montagem: `LancamentoExtrato.supplier` nunca chegava ao `OmieMovement`, e a descrição do arquivo nunca chegava ao `FileEntryForMatch`. Agora chegam, e a afinidade entre os dois entra como desempate **depois** do valor (valor é fato, nome é indício) e **nunca** como exclusão. A métrica é contagem de tokens em comum — sem limiar arbitrário para justificar quando um cruzamento sair errado — e continua determinística, sem IA (§5.9). Títulos a pagar/receber ficam de fora por limitação do Omie, que devolve só o código do cliente. `MatchResult` passou a carregar `tie_stats` (contadores puros, sem PII) porque o conjunto de candidatos de um cruzamento **não é persistido** e essa pergunta não tem resposta retroativa no banco._

_Versão 1.9 — 07/08/2026. **O cruzamento deixou de ser guloso na ordem do arquivo (§5.5).** Passa a acontecer em **passadas por `|days_diff|` crescente**: todos os pares de data exata primeiro, depois 1, 2 e 3 dias. Motivo: uma linha cuja contraparte não casa por valor (pagamento **dividido** em duas parcelas no Omie, contra um cruzamento 1-para-1) levava o lançamento de outra linha dentro dos 3 dias; a linha roubada virava `sem_omie` e a qualificação acusava incoerência na primeira — **um pareamento errado, duas anomalias falsas**. Reportado pela Bruna em 04/08/2026 no cliente Romilson Carpintaria. As tolerâncias não mudaram (`AMOUNT_TOLERANCE = 0.01`, `DATE_DIVERGENCE_RANGE = 3`), o cruzamento continua 1-para-1 (§5.4) e continua determinístico, sem heurística e sem IA (§5.9). Efeito colateral desejado: **a ordem de leitura do arquivo não afeta mais o resultado** — as linhas decidem em `(transaction_date, id)`._

_Versão 1.8 — 07/08/2026. **Idioma de commit, branch e PR fixado na §7.** Commit e nome de branch passam a ser escritos em **inglês (EN-US)** — o formato Conventional Commits não muda, só o idioma do texto; título e corpo de **PR continuam em português**. A seção Idioma cobria código, comentários e mensagens ao usuário final, mas era silenciosa sobre os artefatos de git, e o silêncio vinha sendo lido como "tudo em português". Vale a partir desta data, sem reescrever histórico._

_Versão 1.7 — 03/08/2026. **Sprints 4 e 5 aterrissaram na `main`; o primer estava duas sprints atrasado.** Antes desta revisão o arquivo não continha uma única menção a `tenancy`, `scope`, `client_manager` ou `PERMISSION_MATRIX` — um agent lendo o primer partiria da premissa de que só existem os papéis `admin`/`manager` e escreveria query sem filtro de tenant, reabrindo a classe de vazamento que a Sprint 5 fechou em 34/34 endpoints. **Nova regra §3.15** (autorização por tenant: decisão vem da LINHA, `resolve_client_access` é a função ÚNICA, `scoped_by_tenant` na camada de dados, endpoint novo entra em `sensitive_endpoints.py` com teste negativo). **Novas regras §4.8–§4.10**: modelo de tenancy em `users` com o CHECK `ck_users_scope_client_id`; matriz de permissões declarativa (`core/authz.py` no back, `lib/authz.ts` no front); uma conciliação = conta + mês, com o hash em `reconciliation_files`. **§4.7** ganhou `user_scope`/`actor_client_id` na `access_audit`. Nota de status reescrita com o que cada sprint entregou, **§8** ganhou o mapa das sprints do agents-hub. Corrigidas duas afirmações falsas: a Sprint 3 **está** na `main` (não "aguardando review") e a FASE 1 **está** na `main` (`processing/matcher.py`), não numa branch de integração._

_Versão 1.6 — 22/07/2026. **Sprint 3 (Cripto por cliente, auditoria de acesso e alerta) — na `develop`, PR #38 aguardando review p/ `main`:** §3 e §4 atualizados como lei atual. Cripto migrou para **envelope com DEK por cliente**: cada cliente tem uma DEK própria embrulhada em `clients.dek_wrapped`; a KEK faz wrap/unwrap (**Cloud KMS** em staging/prod via `KEK_KMS_KEY_NAME`, **wrapper local** derivado de `OMIE_ENCRYPTION_KEY`/HKDF em dev/test); AAD liga o ciphertext ao cliente. Nova regra §3.14 com os **landmines** (nunca trocar `KEK_KEY_ID`=`k1`, manter `OMIE_ENCRYPTION_KEY`, não alternar KMS⇄local sobre DB com DEKs, alerting fail-closed em prod/staging). Nova regra §4.7: trilha `access_audit` (LGPD — {denied,view,export}, só IDs). Migrations aditivas (`dek_wrapped`, `access_audit`) reversíveis; endpoint `POST /system/alert-test` admin-only. ⚠️ Deploy exige provisionamento GCP prévio (`scripts/setup-gcp.sh <env>`: KEK no KMS + secrets de alerta + IAM) e backfill das DEKs pós-deploy — ver `scripts/environments-runbook.md`._

_Versão 1.5 — 19/06/2026. **FASE 1 / BACK 1.6 (tolerância de data fixa) na branch de integração `feat/fase1-cartao`:** a tolerância deixou de ser parametrizável — `DATE_DIVERGENCE_RANGE = 3` fixo no matcher. Classificação: data exata → `conciliado`; 1–3 dias → `conciliado_data_divergente` (+ `wrong_date`); > 3 → `sem_omie`. Vale para CC **e** cartão (muda comportamento da CC em prod quando a FASE 1 for mergeada). §5.2/§5.3 reescritos como lei atual, nota do topo e ponto em aberto §10 marcados resolvidos. `date_tolerance_days` removido do request (ignorado se enviado); coluna mantida (novas sessões = 0). O range fixo também rege a janela Omie no processamento, na revisão e no export. Gate local verde (ruff/mypy/pytest — matcher 20, + regressão CC e divergência no job). **Ainda não na `main`** (integração)._

_Versão 1.4 — 16/06/2026. **FASE 0 / S20 (BACK 0.1) aterrissou:** Redis/ARQ removido — background jobs agora via `BackgroundTasks` nativo do FastAPI; cache de lançamentos virou **L1-only** (L2 Redis existia só p/ coerência com o worker separado, que não existe mais). §2 (stack/infra), §8 (mapa S10/S11), §9 (comandos) e §10 (job runner) atualizados como lei atual. Teto do processamento via `asyncio.timeout(RECONCILIATION_TIMEOUT_SECONDS=900)` + cron de cleanup como rede de segurança. Gate local verde (ruff/mypy/507 pytest). §5 (tolerância de data) **não** alterado de propósito — muda só na FASE 1. ⚠️ Cloud Run da API exige `--no-cpu-throttling` + `min-instances ≥ 1` (§10)._

_Versão 1.3 — 15/06/2026. Reordenação do roadmap pelo PRD de 15/06 (FASE 0–5). Status (§ topo) e Fontes da Verdade (§1) passam a apontar o plano vigente [Docs/PLANO_PROXIMOS_PASSOS.md](Docs/PLANO_PROXIMOS_PASSOS.md); o [PLANO_S20_AUDITORIA_CONTINUA.md](Docs/PLANO_S20_AUDITORIA_CONTINUA.md) foi marcado **superseded** (absorvido na FASE 5). §10 ganhou os pontos em aberto do PRD. Os 2 bugs da FASE 0 já estavam resolvidos (auth #19, timeout #16)._

_Versão 1.2 — 11/06/2026. Adicionada a obrigação contínua de manter este primer atualizado (§13, parte do Definition of Done). Varredura de stack contra o código: corrigido o formatter (`ruff format`, não black) em §2/§7, completada a lista de regras ruff (`+ S, A, ASYNC, ANN, PT, TID`), FastAPI alinhado para 0.115+ e `hypothesis` incluído no conjunto de testes. Status S0–S19 em dev e eixo S20+ revalidados contra git log e estrutura de `apps/api`._

_Versão 1.1 — 09/06/2026. Atualizado o status (S0–S19 em dev), worker (ARQ, não Celery), deploy (Google Cloud Run/GCP) e o eixo S20+. Alinhado à documentação em `Docs/documentation/`, ao plano em `Docs/PLANO_IMPLEMENTACAO.md` e ao pivot em `Docs/PLANO_S20_AUDITORIA_CONTINUA.md`._
